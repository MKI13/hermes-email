"""Independent read-only multi-folder proof on disposable synthetic accounts.

Only the test fixture creates/appends folders and mails. The plugin clients
perform EXAMINE and BODY.PEEK reads, no mailbox mutations or SMTP submission.
"""
from __future__ import annotations

import asyncio
import hashlib
import imaplib
import json
import ssl
from dataclasses import replace
from types import SimpleNamespace

from greenmail_e2e import greenmail, RecordingResolver, IMAGE
from hermes_email.config import EmailPluginConfig
from hermes_email.plugin import EmailPlugin
from hermes_email.providers.multi_mailbox import MultiMailboxImapProvider
from hermes_email.providers.imap import ImapMessageIdError
from hermes_email.providers.resolver import resolve_email_provider
from hermes_email.tools import register_read_tools, LIST_TOOL, SEARCH_TOOL, THREAD_TOOL


def main() -> None:
    with greenmail() as lab:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        # Trust this just-created fixture's exact certificate, never a public account.
        with imaplib.IMAP4_SSL('127.0.0.1',lab['imaps'],ssl_context=context,timeout=5) as seed:
            assert hashlib.sha256(seed.sock.getpeercert(binary_form=True)).hexdigest()==lab['pin']
            assert seed.login('"sender"',lab['password'])[0]=='OK'
            folders=('INBOX','Sent Items','Archive')
            for i,folder in enumerate(folders):
                quoted='"'+folder+'"'
                if folder!='INBOX':assert seed.create(quoted)[0]=='OK'
                ref='References: <multi-0@example.invalid>\r\n' if i==1 else ''
                raw=(f'From: customer@example.invalid\r\nTo: sender@example.invalid\r\n'
                     f'Subject: Same project\r\nMessage-ID: <multi-{i}@example.invalid>\r\n'
                     f'{ref}Date: Sun, 06 Sep 2026 10:0{i}:00 +0000\r\n\r\nSynthetic body {i}.\r\n').encode()
                assert seed.append(quoted,None,None,raw)[0]=='OK'
            cfg=EmailPluginConfig.from_mapping({
                'hermes':{'profile':'hermesemailtest'},'email':{'provider':'imap','read_mode':'readonly'},
                'imap':{'host':'127.0.0.1','port':lab['imaps'],'security':'tls-pinned',
                        'tls_sha256_fingerprint':lab['pin'],'username_ref':'HERMES_EMAIL_IMAP_MULTI_USER',
                        'password_ref':'HERMES_EMAIL_IMAP_MULTI_PASSWORD','account_namespace':'synthetic-multi-folder',
                        'mailboxes':list(folders),'timeout_seconds':5}})
            values={'HERMES_EMAIL_IMAP_MULTI_USER':'sender','HERMES_EMAIL_IMAP_MULTI_PASSWORD':lab['password']}
            resolver=RecordingResolver(values)
            provider=resolve_email_provider(cfg,secret_resolver=resolver)
            assert isinstance(provider,MultiMailboxImapProvider)
            runtime=EmailPlugin(cfg,provider=provider)
            tools={}
            def register_tool(**kw):tools[kw['name']]=kw;return SimpleNamespace(dispose=lambda:None)
            register_read_tools(SimpleNamespace(register_tool=register_tool),runtime)
            def invoke(name,args):return json.loads(asyncio.run(tools[name]['handler'](args)))
            try:
                first=invoke(LIST_TOOL,{'limit':1})
                assert first['ok'] and first['count']==1 and not first['folder_scan']['scan_complete']
                second=invoke(LIST_TOOL,{'limit':2,'cursor':first['next_cursor']})
                assert second['ok'] and second['count']==2 and second['folder_scan']['scan_complete'],second
                all_messages=first['messages']+second['messages']
                assert len({m['message_id'] for m in all_messages})==3
                assert all(m['body_loaded'] is False for m in all_messages)
                assert {m['mailbox'] for m in all_messages}==set(folders)
                found=invoke(SEARCH_TOOL,{'query':'Same project','limit':9})
                assert found['ok'] and found['count']==3 and found['folder_scan']['scan_complete'],found
                no_body=invoke(SEARCH_TOOL,{'query':'Synthetic body','limit':9})
                assert no_body['count']==0 and not no_body['folder_scan']['body_search_performed']
                thread=invoke(THREAD_TOOL,{'message_id':first['messages'][0]['message_id'],'scan_limit':9})
                assert thread['ok'] and thread['count']==2 and thread['scan_complete'],thread
                assert [m['mailbox'] for m in thread['messages']]==['INBOX','Sent Items']
                assert [m['body_text'] for m in thread['messages']]==['Synthetic body 0.','Synthetic body 1.']
                other_resolver=RecordingResolver(values)
                other=MultiMailboxImapProvider(replace(cfg.imap,account_namespace='another-account'),other_resolver)
                try:
                    try:asyncio.run(other.get_message(first['messages'][0]['message_id']))
                    except ImapMessageIdError:pass
                    else:raise AssertionError('foreign scoped ID accepted')
                    assert not other_resolver.calls
                finally:other.close()
                missing=MultiMailboxImapProvider(replace(cfg.imap,mailboxes=folders+('Missing',)),resolver)
                try:
                    page=asyncio.run(missing.fetch_messages(limit=12))
                    assert page.next_cursor is None and not page.scan.scan_complete
                    assert ('Missing','unavailable') in page.scan.folders
                finally:missing.close()
                # Direct fixture-only check: none of the plugin reads marked mail Seen.
                for folder in folders:
                    assert seed.select('"'+folder+'"',readonly=True)[0]=='OK'
                    status,flags=seed.uid('FETCH','1:*','(FLAGS)')
                    assert status=='OK' and b'\\Seen' not in b' '.join(item for item in flags if isinstance(item,bytes))
            finally:runtime.close()
    print(json.dumps({'independent_server':'GreenMail 2.1.13','image':IMAGE,
        'multi_folder_pagination':'PASS','header_search':'PASS','scoped_ids':'PASS',
        'inbox_sent_thread':'PASS','missing_folder_reporting':'PASS','unseen_flags_preserved':'PASS',
        'model_calls':0,'external_mail_sent':False,'proton_authenticated_account':'NOT_TESTED'},indent=2))


if __name__=='__main__':
    main()
