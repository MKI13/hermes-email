"""Multi-folder scope, budgets, incompleteness, header-only IO and Hermes tools."""
import asyncio
import json
import re
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_email.config import ConfigError, EmailPluginConfig, ImapSettings
from hermes_email.models import EmailMessage, EmailMessagePage, EmailAddress
from hermes_email.plugin import EmailPlugin
from hermes_email.providers.imap import ImapReadOnlyProvider, ImapCursorError, ImapMessageIdError, ImapLimitError, _mailbox_argument
from hermes_email.providers.multi_mailbox import MultiMailboxImapProvider
from hermes_email.providers.errors import ProviderMailboxError, ProviderTlsError, ProviderProtocolError, ProviderConnectionError
from hermes_email.providers.resolver import resolve_email_provider
from hermes_email.tools import register_read_tools, LIST_TOOL, SEARCH_TOOL, GET_TOOL, THREAD_TOOL
from test_imap_provider import FakeImapClient, RecordingSecretResolver, imap_settings, fetch_record, PLAIN_MESSAGE


def config(**overrides):
    return imap_settings(account_namespace="sample-account", mailboxes=("INBOX", "Sent Items", "Archive"), **overrides)


def raw_message(identifier, refs="", subject="Project", body="PRIVATE BODY", date="01"):
    return (f"From: customer@example.invalid\r\nTo: office@example.invalid\r\nSubject: {subject}\r\n"
            f"Message-ID: <{identifier}@example.invalid>\r\nReferences: {refs}\r\n"
            f"Date: Sun, 06 Sep 2026 10:{date}:00 +0000\r\n\r\n{body}\r\n").encode()


class MailboxClient(FakeImapClient):
    def __init__(self, mailbox, data, calls, *, missing=False, validity=77, uid_next=None):
        super().__init__()
        self.mailbox, self.data, self.calls = mailbox, data, calls
        self.uid_validity = validity
        self.uid_next = uid_next or (max(data, default=0) + 1)
        self.message_count = len(data)
        if missing:
            self.select_result = "NO"

    def select(self, mailbox, readonly=False):
        self.calls.append((self.mailbox, "select", mailbox, readonly))
        return super().select(mailbox, readonly)

    def uid(self, command, uid_set, query):
        self.calls.append((self.mailbox, command, uid_set, query))
        lo, hi = map(int, uid_set.split(":"))
        bound = int(re.search(r"<0\.(\d+)>", query)[1])
        values = []
        for uid, raw in self.data.items():
            if lo <= uid <= hi:
                part = raw.split(b"\r\n\r\n", 1)[0] + b"\r\n\r\n" if "[HEADER]" in query else raw
                metadata, payload = fetch_record(uid, part[:bound], remote_size=len(raw))
                if "[HEADER]" in query:
                    metadata = metadata.replace(b"BODY[]", b"BODY[HEADER]")
                values.append((metadata, payload))
        return "OK", values


def make(settings=None, data=None, *, missing=(), validity=None, uid_next=None):
    settings = settings or config()
    data = data if data is not None else {
        "INBOX": {1: raw_message("root", date="01")},
        "Sent Items": {1: raw_message("reply", "<root@example.invalid>", date="02")},
        "Archive": {1: raw_message("other", subject="Same subject not same thread", date="03")},
    }
    calls = []
    resolver = RecordingSecretResolver()
    children = []
    def factory(cfg, secrets):
        client = MailboxClient(cfg.mailbox, data.get(cfg.mailbox, {}), calls,
            missing=cfg.mailbox in missing, validity=(validity or {}).get(cfg.mailbox, 77),
            uid_next=(uid_next or {}).get(cfg.mailbox))
        child = ImapReadOnlyProvider(cfg, secrets, client_factory=lambda *a, **kw: client)
        children.append((child, client))
        return child
    provider = MultiMailboxImapProvider(settings, resolver, provider_factory=factory)
    return provider, resolver, calls, children


def invoke(awaitable):
    return asyncio.run(awaitable)


@pytest.mark.parametrize("mailboxes", [("INBOX", "inbox"), ("Sent",), tuple(["INBOX"] + [f"F{i}" for i in range(8)]), ("INBOX", "bad\r\nSELECT"), ("INBOX", "ümlaut"), ("INBOX", ""), ("INBOX", "x"*256)])
def test_config_rejects_invalid_folders(mailboxes):
    with pytest.raises(ConfigError):
        imap_settings(account_namespace="sample", mailboxes=mailboxes)


def test_explicit_namespace_and_primary_are_required():
    with pytest.raises(ConfigError):
        imap_settings(mailboxes=("INBOX", "Sent"))
    cfg=config()
    assert cfg.mailboxes == ("INBOX", "Sent Items", "Archive")
    assert replace(cfg, mailboxes=("inbox",)).mailboxes == ("INBOX",)


def test_resolver_selects_opt_in_without_connecting():
    resolver=RecordingSecretResolver()
    cfg=EmailPluginConfig.from_mapping({"email":{"provider":"imap","read_mode":"readonly"},
        "imap":{"host":"mail.example.invalid","username_ref":"HERMES_EMAIL_IMAP_USERNAME",
                "password_ref":"HERMES_EMAIL_IMAP_PASSWORD","account_namespace":"primary",
                "mailboxes":["INBOX","Sent Items"]}})
    provider=resolve_email_provider(cfg,secret_resolver=resolver)
    assert isinstance(provider,MultiMailboxImapProvider) and not resolver.calls
    provider.close()


def test_header_scan_binds_same_uid_in_different_folders_and_never_fetches_body():
    p,r,c,children=make()
    page=invoke(p.fetch_messages(limit=9))
    assert len(page)==3 and len({m.message_id for m in page})==3
    assert all(m.body_text is None and m.attachments==() for m in page)
    assert page.next_cursor is None and page.scan.scan_complete
    assert {m.metadata['mailbox'] for m in page}=={'INBOX','Sent Items','Archive'}
    assert all('[HEADER]' in x[3] for x in c if x[1]=='FETCH')
    assert all(x[-1] is True for x in c if x[1]=='select')
    assert [x[2] for x in c if x[1]=='select']==['INBOX','"Sent Items"','Archive']
    for m in page:
        detail=invoke(p.get_message(m.message_id))
        assert detail.message_id==m.message_id and detail.body_text=='PRIVATE BODY'
    p.close()


@pytest.mark.parametrize("change", [{'account_namespace':'other'}, {'host':'another.example.invalid'},
    {'port':999}, {'username_ref':'HERMES_EMAIL_OTHER_USER'}])
def test_foreign_account_ids_are_rejected_before_any_secret(change):
    p,_,_,_=make(); identifier=invoke(p.fetch_messages(limit=3))[0].message_id
    other,resolver,calls,_=make(replace(config(),**change))
    with pytest.raises(ImapMessageIdError):invoke(other.get_message(identifier))
    assert not resolver.calls and not calls


def test_removed_mailbox_and_legacy_identifier_are_denied():
    p,_,_,_=make(); identifier=invoke(p.fetch_messages(limit=3))[1].message_id
    other,r,c,_=make(replace(config(),mailboxes=('INBOX',)))
    for value in (identifier,'imap-v1:77:1','imap-m1:'+('a'*200),False):
        with pytest.raises(ImapMessageIdError):invoke(other.get_message(value))
    assert not r.calls and not c


def test_stable_message_id_survives_new_runtime_but_uidvalidity_change_fails():
    p,_,_,_=make();identifier=invoke(p.fetch_messages(limit=3))[0].message_id
    restarted,_,_,_=make();assert invoke(restarted.get_message(identifier)).message_id==identifier
    changed,_,calls,_=make(validity={'INBOX':78})
    with pytest.raises(ImapMessageIdError):invoke(changed.get_message(identifier))
    assert not [c for c in calls if c[1]=='FETCH']


def test_small_pages_rotate_without_duplicates_or_starvation():
    p,_,calls,_=make();cursor=None;seen=[]
    for i in range(3):
        page=invoke(p.fetch_messages(limit=1,cursor=cursor));seen.extend(page.messages);cursor=page.next_cursor
        assert len(page)<=1
        assert page.scan.scan_complete == (i==2)
    assert cursor is None and len({m.message_id for m in seen})==3
    assert [c[0] for c in calls if c[1]=='FETCH']==['INBOX','Sent Items','Archive']


def test_sparse_uid_range_empty_page_still_has_bounded_continuation():
    p,_,calls,_=make(replace(config(),mailboxes=('INBOX',)),data={'INBOX':{1:raw_message('root')}},uid_next={'INBOX':1001})
    page=invoke(p.fetch_messages(limit=2))
    assert not page.messages and page.next_cursor and not page.scan.scan_complete
    next_page=invoke(p.fetch_messages(limit=2,cursor=page.next_cursor))
    assert next_page.next_cursor != page.next_cursor
    assert len([c for c in calls if c[1]=='FETCH'])==2


def test_missing_mailbox_remains_visible_even_when_cursor_is_exhausted():
    p,_,_,_=make(missing=('Archive',))
    page=invoke(p.fetch_messages(limit=9))
    assert len(page)==2 and page.next_cursor is None and not page.scan.scan_complete
    assert ('Archive','unavailable') in page.scan.folders


def test_transport_security_errors_are_not_masked_as_partial_results():
    p,_,_,children=make()
    async def fail(**kwargs):raise ProviderTlsError('fixed error')
    children[1][0].fetch_headers=fail
    with pytest.raises(ProviderTlsError):invoke(p.fetch_messages(limit=9))


def test_partial_headers_remain_marked_across_pagination():
    data={'INBOX':{1:raw_message('root',subject='x'*2500)},'Sent Items':{},'Archive':{}}
    p,_,_,_=make(data=data)
    page=invoke(p.fetch_messages(limit=1))
    assert not page.scan.headers_complete
    assert page[0].metadata['headers_truncated']=='true'
    last=invoke(p.fetch_messages(limit=9,cursor=page.next_cursor))
    assert last.next_cursor is None and not last.scan.scan_complete and not last.scan.headers_complete


def test_cursor_signature_query_and_runtime_binding_before_io():
    p,r,c,_=make();page=invoke(p.search_headers('Project',limit=1));original=page.next_cursor
    assert original and len(original)<=512
    before=(len(r.calls),len(c))
    for value in (original[:-1]+('f' if original[-1]!='f' else 'e'),'../invalid-cursor','x'*513):
        with pytest.raises(ImapCursorError):invoke(p.search_headers('Project',limit=1,cursor=value))
    with pytest.raises(ImapCursorError):invoke(p.search_headers('different',cursor=original))
    with pytest.raises(ImapCursorError):invoke(p.fetch_messages(cursor=original))
    assert before==(len(r.calls),len(c))
    restarted,r2,c2,_=make()
    with pytest.raises(ImapCursorError):invoke(restarted.search_headers('Project',cursor=original))
    assert not r2.calls and not c2


def test_maximum_valid_cursor_is_bounded():
    cfg=replace(config(),mailboxes=('INBOX',)+tuple(f'F{i}' for i in range(7)))
    p,_,_,_=make(cfg)
    cursor=p._encode_cursor('q'+'a'*16,7,1,[[4294967295,4294967295]]*8)
    assert len(cursor)<=512
    assert p._decode_cursor(cursor,'q'+'a'*16)==(7,1,[[4294967295,4294967295]]*8)


def test_search_matches_headers_not_hidden_body_and_binds_plain_query():
    p,_,calls,_=make()
    assert len(invoke(p.search_headers('PROJECT',limit=9)))==2
    body_only=invoke(p.search_headers('PRIVATE BODY',limit=9))
    assert not body_only and body_only.scan.scan_complete
    assert all('[HEADER]' in c[3] for c in calls if c[1]=='FETCH')


@pytest.mark.parametrize('value',[True,0,-1,101,'3',None])
def test_limits_fail_before_io(value):
    p,r,c,_=make()
    with pytest.raises(ImapLimitError):invoke(p.fetch_messages(limit=value))
    assert not r.calls and not c


@pytest.mark.parametrize('mailbox,expected', [('Sent Items','"Sent Items"'),('a"b','"a\\"b"'),('a\\b','"a\\\\b"'),('x) (UID 1','"x) (UID 1"')])
def test_mailbox_is_one_quoted_argument(mailbox,expected):
    assert _mailbox_argument(mailbox)==expected


def tool_runtime(p):
    cfg=EmailPluginConfig.from_mapping({'email':{'provider':'imap','read_mode':'readonly'},
                                      'imap':{k:getattr(p._settings,k) for k in p._settings.__dataclass_fields__}})
    plugin=EmailPlugin(cfg,provider=p)
    ctx=SimpleNamespace(tools={})
    def register_tool(**kw):ctx.tools[kw['name']]=kw;return SimpleNamespace(dispose=lambda:None)
    ctx.register_tool=register_tool;register_read_tools(ctx,plugin)
    return plugin,ctx.tools


def test_thread_only_hydrates_linked_bodies_from_inbox_and_sent():
    p,_,calls,_=make();seed=invoke(p.fetch_messages(limit=3))[0].message_id
    calls.clear();plugin,tools=tool_runtime(p)
    result=json.loads(invoke(tools[THREAD_TOOL]['handler']({'message_id':seed,'scan_limit':9})))
    assert result['ok'] and result['content_is_untrusted'] and result['scan_complete']
    assert [m['mailbox'] for m in result['messages']]==['INBOX','Sent Items']
    assert result['folder_scan']['authorization']=='none'
    assert result['folder_scan']['body_search_performed'] is False
    full=[c[0] for c in calls if c[1]=='FETCH' and 'BODY.PEEK[]' in c[3]]
    assert full==['INBOX','Sent Items']
    assert all(m['body_loaded'] for m in result['messages'])
    plugin.close()


def test_thread_missing_detail_is_not_reported_complete():
    p,_,_,children=make();seed=invoke(p.fetch_messages(limit=3))[0].message_id
    async def vanished(*args):return None
    children[1][0].get_message_limited=vanished
    plugin,_=tool_runtime(p);thread=invoke(plugin.get_thread_context(seed,scan_limit=9))
    assert len(thread.messages)==1 and not thread.scan_complete and thread.scan.missing_messages==1


def test_tools_expose_coverage_even_for_zero_search_matches():
    p,_,_,_=make();plugin,tools=tool_runtime(p)
    value=json.loads(invoke(tools[SEARCH_TOOL]['handler']({'query':'does-not-exist','limit':3})))
    assert value['ok'] and value['count']==0 and value['folder_scan']['scanned_messages']==3
    assert value['folder_scan']['cross_folder_atomic_snapshot'] is False
    assert value['folder_scan']['search_scope']=='subject-from-to-cc-reply-to'
    plugin.close()


def test_closed_provider_does_not_touch_secrets():
    p,r,c,_=make();p.close()
    with pytest.raises(ProviderConnectionError):invoke(p.fetch_messages())
    assert not r.calls and not c


@pytest.mark.parametrize('payload', [b'From: a@example.invalid\r\n\r\nBODY', b'From: a@example.invalid\n\nBODY'])
def test_header_response_cannot_smuggle_a_body(payload):
    client=FakeImapClient()
    metadata,body=fetch_record(1,payload)
    client.uid_next=2
    client.fetch_responses['1:1']=[(metadata.replace(b'BODY[]',b'BODY[HEADER]'),body)]
    provider=ImapReadOnlyProvider(imap_settings(),RecordingSecretResolver(),client_factory=lambda *a,**k:client)
    with pytest.raises(ProviderProtocolError):invoke(provider.fetch_headers(limit=1))


def test_wrong_fetch_section_is_rejected():
    client=FakeImapClient();client.uid_next=2
    client.fetch_responses['1:1']=[fetch_record(1,PLAIN_MESSAGE)]
    provider=ImapReadOnlyProvider(imap_settings(),RecordingSecretResolver(),client_factory=lambda *a,**k:client)
    with pytest.raises(ProviderProtocolError):invoke(provider.fetch_headers(limit=1))


@pytest.mark.parametrize('states', [[True,0,0],[[0,1],0,0],[[1,4294967296],0,0],[[1],0,0],['bad',0,0],[0,0]])
def test_signed_malformed_cursor_state_still_fails_validation(states):
    p,r,c,_=make()
    token=p._encode_cursor('list',0,0,states)
    with pytest.raises(ImapCursorError):invoke(p.fetch_messages(cursor=token))
    assert not r.calls and not c


def test_header_budget_is_global_not_per_folder():
    p,_,calls,_=make(replace(config(),max_page_bytes=409_600),
        data={name:{100:raw_message(name)} for name in config().mailboxes},
        uid_next={name:101 for name in config().mailboxes})
    page=invoke(p.fetch_messages(limit=100))
    fetched=[c for c in calls if c[1]=='FETCH']
    bytes_bound=0;slots=0
    for _,_,uids,query in fetched:
        lo,hi=map(int,uids.split(':'));count=hi-lo+1;slots+=count
        bytes_bound+=count*int(re.search(r'<0\.(\d+)>',query)[1])
    assert slots<=100 and bytes_bound<=409_600 and page.scan.uid_slot_budget==100


def test_explicit_same_rfc_id_does_not_collapse_distinct_folder_locations():
    data={name:{1:raw_message('root',body=name)} for name in config().mailboxes}
    p,_,_,_=make(data=data);plugin,_=tool_runtime(p)
    seed=invoke(p.fetch_messages(limit=3))[0].message_id
    thread=invoke(plugin.get_thread_context(seed,scan_limit=9))
    assert len(thread.messages)==3 and len({m.message_id for m in thread.messages})==3
    assert {m.metadata['mailbox'] for m in thread.messages}==set(config().mailboxes)


def test_real_hermes_registry_multifolder_read_search_thread_and_unload(tmp_path,monkeypatch):
    plugins=pytest.importorskip('hermes_cli.plugins')
    registry=pytest.importorskip('tools.registry').registry
    from dataclasses import asdict
    from hermes_email.profile_guard import register
    from hermes_email import plugin as facade
    monkeypatch.setattr(plugins,'get_hermes_home',lambda:tmp_path)
    manager=plugins.PluginManager(scope_key=str(tmp_path))
    manifest=plugins.PluginManifest(name='multi-folder-test',key='multi-folder-test')
    ctx=plugins.PluginContext(manifest,manager)
    p,_,_,_=make()
    settings={'email':{'provider':'imap','read_mode':'readonly'},'hermes':{'profile':ctx.profile_name},
              'imap':asdict(p._settings),'audit':{'mode':'sqlite'}}
    ctx.get_config=lambda key,default=None:settings.get(key,default)
    monkeypatch.setattr(facade,'resolve_email_provider',lambda *a,**k:p)
    runtime=register(ctx)
    try:
        first=json.loads(registry.dispatch(LIST_TOOL,{'limit':1},scope=str(tmp_path)))
        second=json.loads(registry.dispatch(LIST_TOOL,{'limit':2,'cursor':first['next_cursor']},scope=str(tmp_path)))
        assert first['audit']['recorded'] and second['folder_scan']['scan_complete']
        found=json.loads(registry.dispatch(SEARCH_TOOL,{'query':'Project','limit':9},scope=str(tmp_path)))
        assert found['count']==2 and found['folder_scan']['body_search_performed'] is False
        thread=json.loads(registry.dispatch(THREAD_TOOL,{'message_id':first['messages'][0]['message_id'],'scan_limit':9},scope=str(tmp_path)))
        assert thread['count']==2 and thread['scan_complete'] and thread['audit']['recorded']
        assert {m['mailbox'] for m in thread['messages']}=={'INBOX','Sent Items'}
        assert registry.get_entry('email_send',scope=str(tmp_path)) is None
    finally:
        manager.unload(manifest.key)
    assert registry.get_entry(LIST_TOOL,scope=str(tmp_path)) is None
    assert p._closed


def test_short_header_section_without_blank_separator_is_not_truncation():
    from hermes_email.providers.imap import _complete_header_window
    raw = b"From: a@example.invalid\r\nSubject: test\r\n"
    assert _complete_header_window(raw, 16384)
    assert not _complete_header_window(raw, len(raw))
    assert _complete_header_window(raw + b"\r\n", len(raw) + 2)


def test_universal_multifolder_example_stays_readonly_and_scoped():
    from hermes_email.config import load_config
    cfg=load_config(Path(__file__).parents[1]/'examples/config.multi-folder.example.yaml')
    assert cfg.imap.mailboxes == ('INBOX','Sent Items','Archive')
    assert cfg.imap.account_namespace and cfg.email.read_mode == 'readonly'
    assert not cfg.safety.allow_send and cfg.send_workflow.mode == 'disabled'


def test_close_during_last_failed_folder_read_never_returns_a_success_page():
    p,_,_,children=make(replace(config(),mailboxes=('INBOX',)))
    async def close_then_fail(**kwargs):
        p.close()
        raise ProviderMailboxError('unavailable')
    children[0][0].fetch_headers=close_then_fail
    with pytest.raises(ProviderConnectionError):invoke(p.fetch_messages(limit=1))


@pytest.mark.parametrize('header,count',[('To',51),('Cc',51),('Reply-To',11),('From',2)])
def test_discarded_address_header_cannot_report_complete_search(header,count):
    values=','.join(f'u{i}@example.invalid' for i in range(count))
    raw=raw_message('root')
    line=(header+': '+values+'\r\n').encode()
    if header=='To':raw=raw.replace(b'To: office@example.invalid\r\n',line)
    elif header=='From':raw=raw.replace(b'From: customer@example.invalid\r\n',line)
    else:raw=raw.replace(b'Subject:',line+b'Subject:')
    # Each header fits within byte and per-field text limits: address count is
    # what causes normalization to discard it.
    assert len(line)<2000
    p,_,_,_=make(data={'INBOX':{1:raw},'Sent Items':{},'Archive':{}})
    first=invoke(p.search_headers('u0@example.invalid',limit=1))
    assert not first.messages
    assert not first.scan.headers_complete and not first.scan.scan_complete
    last=invoke(p.search_headers('u0@example.invalid',limit=9,cursor=first.next_cursor))
    assert not last.messages and last.next_cursor is None
    assert not last.scan.headers_complete and not last.scan.scan_complete


def test_scan_metadata_describes_only_the_actual_operation():
    p,_,_,_=make();plugin,tools=tool_runtime(p)
    first=json.loads(invoke(tools[LIST_TOOL]['handler']({'limit':3})))
    assert 'search_scope' not in first['folder_scan']
    assert first['folder_scan']['operation']=='list'
    search=json.loads(invoke(tools[SEARCH_TOOL]['handler']({'query':'Project','limit':9})))
    assert search['folder_scan']['operation']=='search'
    assert search['folder_scan']['search_scope']=='subject-from-to-cc-reply-to'
    thread=json.loads(invoke(tools[THREAD_TOOL]['handler']({'message_id':first['messages'][0]['message_id'],'scan_limit':9})))
    assert 'search_scope' not in thread['folder_scan']
    assert thread['folder_scan']['operation']=='thread'
    assert thread['folder_scan']['linkage_scope']=='message-id-in-reply-to-references'
    plugin.close()
