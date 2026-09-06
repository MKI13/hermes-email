import asyncio
import json
import sqlite3
from dataclasses import replace
from email import policy
from email.parser import BytesParser

import pytest

from hermes_email.approval import review_snapshot
from hermes_email.config import EmailSettings, ReplyPolicySettings
from hermes_email.draft_storage import DraftConflictError, DraftStorageSchemaError, DraftValidationError
from hermes_email.draft_tools import CREATE_REPLY_ALL_DRAFT_TOOL, register_draft_tools
from hermes_email.models import EmailAddress, EmailMessage
from hermes_email.plugin import EmailPlugin
from hermes_email.providers import MockEmailProvider
from hermes_email.reply_headers import source_reply_headers, validate_references
from hermes_email.replying import derive_reply_route
from hermes_email.sending import prepare_send_candidate, SendGateMessageError
from test_imap_provider import FakeImapClient, fetch_record, provider_with_client
from test_sending import config, store, draft, confirmation
from test_draft_tools import Context, invoke


def incoming(extra=b""):
    raw=(b"From: Author <author@example.invalid>\r\n"
         b"To: Us <sender@example.invalid>, Friend <friend@example.invalid>\r\n"
         b"Cc: Copy <copy@example.invalid>\r\n"
         b"Bcc: Never <never@example.invalid>\r\n"
         b"Subject: Test\r\nMessage-ID: <parent@example.invalid>\r\n"
         b"References: <root@example.invalid> <earlier@example.invalid>\r\n"
         +extra+b"\r\nUntrusted body")
    client=FakeImapClient();client.fetch_responses["9:9"]=[fetch_record(9,raw)]
    provider,_,_=provider_with_client(client)
    return asyncio.run(provider.get_message("imap-v1:77:9"))


@pytest.mark.parametrize("header", [b"Reply-To: \r\n", b"Reply-To: garbage\r\n",
    b"Reply-To: bad@\r\n", b"Reply-To: <one@example.invalid\r\n",
    b"Reply-To: good@example.invalid\r\nReply-To: other@example.invalid\r\n",
    b"Reply-To: " + b"x"*16400+b"\r\n"])
def test_present_but_invalid_reply_to_never_falls_back(header):
    message=incoming(header)
    assert message.reply_to_present and message.reply_to_invalid
    route=derive_reply_route(message)
    assert route.source=="reply-to" and route.selected is None and not route.valid


def test_absent_reply_to_uses_from_and_preserves_to_cc_without_bcc():
    message=incoming()
    assert not message.reply_to_present and not message.reply_to_invalid
    assert derive_reply_route(message).selected.address=="author@example.invalid"
    assert [a.address for a in message.recipients]==["sender@example.invalid","friend@example.invalid"]
    assert [a.address for a in message.cc]==["copy@example.invalid"]
    assert "never@example.invalid" not in repr(message)


def test_entire_reply_all_chain_reaches_smtp_bytes_after_restart(tmp_path):
    source=incoming(b"Reply-To: desk@example.invalid\r\n")
    cfg=replace(config(), email=EmailSettings(provider="mock",read_mode="mock"),
                reply_policy=ReplyPolicySettings(own_addresses=("sender@example.invalid",)))
    drafts=store(tmp_path,cfg)
    plugin=EmailPlugin(cfg,provider=MockEmailProvider((source,)),draft_store=drafts)
    ctx=Context();register_draft_tools(ctx,plugin)
    tool=next(t for t in ctx.tools if t["name"]==CREATE_REPLY_ALL_DRAFT_TOOL)
    result=invoke(tool,{"message_id":source.message_id,"body_text":"Reviewed reply.","operation_id":"headers-roundtrip-0001"})
    assert result["ok"]
    identifier=result["mutation"]["draft_id"]
    drafts.close(); drafts=store(tmp_path,cfg)
    stored=drafts.get_draft(identifier)
    assert stored.in_reply_to=="<parent@example.invalid>"
    assert stored.references==("<root@example.invalid>","<earlier@example.invalid>","<parent@example.invalid>")
    assert {a.address for a in stored.cc}=={"friend@example.invalid","copy@example.invalid"}
    assert stored.bcc==()
    candidate=prepare_send_candidate(cfg,drafts,draft_id=identifier,expected_revision=1,
                                    confirmation=confirmation(identifier),send_operation_id="headers-send-op-0001")
    message=BytesParser(policy=policy.default).parsebytes(candidate.message_bytes)
    assert str(message["In-Reply-To"]).strip()==stored.in_reply_to
    assert str(message["References"]).split()==list(stored.references)
    assert message["To"].addresses[0].addr_spec=="desk@example.invalid"
    assert {a.addr_spec for a in message["Cc"].addresses}=={"friend@example.invalid","copy@example.invalid"}
    assert message["Bcc"] is None and b"never@example.invalid" not in candidate.message_bytes
    assert message.get_content().strip()=="Reviewed reply."


def test_legacy_migration_preserves_draft_and_operation_receipt(tmp_path):
    cfg=config();drafts=store(tmp_path,cfg)
    receipt=drafts.create_draft(draft(),"migration-create-0001");original=drafts.get_draft(receipt.draft_id);drafts.close()
    with sqlite3.connect(drafts.path) as c:
        c.execute("DROP TABLE draft_reply_references");c.execute("PRAGMA user_version=1")
    migrated=store(tmp_path,cfg)
    assert migrated.get_draft(receipt.draft_id)==original
    replay=migrated.create_draft(draft(),"migration-create-0001")
    assert replay.replayed and replay.draft_id==receipt.draft_id
    with sqlite3.connect(drafts.path) as c:
        assert c.execute("PRAGMA user_version").fetchone()==(2,)
        assert c.execute("SELECT COUNT(*) FROM draft_operations").fetchone()==(1,)


def test_bad_legacy_schema_not_migrated(tmp_path):
    cfg=config();drafts=store(tmp_path,cfg);drafts.create_draft(draft(),"migration-create-0002");drafts.close()
    with sqlite3.connect(drafts.path) as c:
        c.execute("DROP TABLE draft_reply_references");c.execute("PRAGMA user_version=1")
        c.execute("CREATE TABLE unexpected (x TEXT)")
    before=drafts.path.read_bytes()
    with pytest.raises(DraftStorageSchemaError):
        store(tmp_path,cfg).list_drafts()
    assert drafts.path.read_bytes()==before


@pytest.mark.parametrize("refs", [["bad"],["<ok@example.invalid>\r\nBcc: x@y"],
    ["<same@example.invalid>"]*2,[f"<x{i}@example.invalid>" for i in range(51)],"<x@y>"])
def test_reference_limits_fail_before_storage(tmp_path,refs):
    drafts=store(tmp_path)
    with pytest.raises(DraftValidationError):
        drafts.create_draft(replace(draft(),references=refs),"invalid-refs-0001")
    assert not drafts.path.exists()


def test_reference_changes_change_revision_digest_and_approval_snapshot(tmp_path):
    cfg=config();drafts=store(tmp_path,cfg);content=replace(draft(),references=("<one@example.invalid>",))
    receipt=drafts.create_draft(content,"revision-refs-0001")
    original=review_snapshot(cfg,drafts.get_draft(receipt.draft_id))
    changed=replace(content,references=("<two@example.invalid>",))
    with pytest.raises(DraftConflictError):
        drafts.create_draft(changed,"revision-refs-0001")
    update=drafts.update_draft(receipt.draft_id,1,changed,"revision-refs-0002")
    current=drafts.get_draft(receipt.draft_id)
    assert update.revision==2 and current.references==changed.references
    assert review_snapshot(cfg,current).digest!=original.digest


def test_opaque_legacy_parent_never_becomes_smtp_header(tmp_path):
    cfg=config();drafts=store(tmp_path,cfg)
    identifier=drafts.create_draft(replace(draft(),in_reply_to="provider-id-123"),"opaque-parent-0001").draft_id
    with pytest.raises(SendGateMessageError):
        prepare_send_candidate(cfg,drafts,draft_id=identifier,expected_revision=1,
                               confirmation=confirmation(identifier),send_operation_id="opaque-send-op-0001")


def test_ambiguous_parent_and_reference_bounds():
    source=EmailMessage("id","subject",EmailAddress("a@b"),(),metadata={"rfc_message_id":"<a@b> <c@d>"})
    assert source_reply_headers(source)==(None,())
    source=replace(source,metadata={"rfc_message_id":"<parent@b>","references":" ".join(f"<id{i}@b>" for i in range(100))})
    parent,refs=source_reply_headers(source)
    assert parent=="<parent@b>" and len(refs)==50 and refs[0]=="<id0@b>" and refs[-1]==parent
