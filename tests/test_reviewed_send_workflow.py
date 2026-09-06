import json
import os
import smtplib
import socketserver
import sqlite3
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from hermes_email.approval import ApprovalAuthority, ApprovalError, ApprovalScope, review_snapshot
from hermes_email.config import ConfigError, HermesSettings, SendWorkflowSettings
from hermes_email.draft_storage import DraftConflictError
from hermes_email.send_workflow import ReviewedSendWorkflow
from hermes_email.smtp import SmtpSubmissionResult
from test_sending import config, store, draft
from test_send_orchestration import FakeTransport


def configured(tmp_path, **kw):
    base=config()
    cfg=replace(base, hermes=HermesSettings(profile="email"),
                smtp=replace(base.smtp,host="127.0.0.1"),
                send_workflow=SendWorkflowSettings(mode="local-test"), **kw)
    drafts=store(tmp_path,cfg); identifier=drafts.create_draft(draft(),"reviewed-create-0001").draft_id
    scope=ApprovalScope("email",str(drafts.path.parent.resolve()),"uid:test","session:test")
    return cfg,drafts,identifier,scope


def approve(display,challenge,timeout):
    data=json.loads(display)
    assert data["action"]=="SEND THIS EMAIL ONCE"
    assert data["bcc"][0]["address"]=="private@example.invalid"
    return "APPROVE "+challenge


def service(cfg,drafts,scope,transport,**kw):
    return ReviewedSendWorkflow(cfg,drafts,drafts.path.parent,lambda:scope,transport_factory=lambda:transport,**kw)


def test_complete_approved_workflow_and_idempotent_restart(tmp_path):
    cfg,drafts,identifier,scope=configured(tmp_path);transport=FakeTransport()
    first=service(cfg,drafts,scope,transport).run(identifier,approve)
    assert first["state"]=="accepted" and first["smtp_accepted"] and not first["delivery_confirmed"]
    assert first["sent_copy"]=="saved-local" and transport.calls==1
    copies=list((drafts.path.parent/"sent-copies").glob("*.eml"));assert len(copies)==1
    assert b"Bcc:" not in copies[0].read_bytes() and copies[0].stat().st_mode & 0o777==0o600
    drafts.close();drafts=store(tmp_path,cfg);second_transport=FakeTransport()
    def no_prompt(*args):raise AssertionError("a replay must not prompt or send")
    replay=service(cfg,drafts,scope,second_transport).run(identifier,no_prompt)
    assert replay["replayed"] and replay["state"]=="accepted" and second_transport.calls==0


@pytest.mark.parametrize("answer", ["yes", "confirmed=true", "", None])
def test_denial_creates_no_attempt_or_transport(tmp_path,answer):
    cfg,drafts,identifier,scope=configured(tmp_path);transport=FakeTransport()
    with pytest.raises(ApprovalError):
        service(cfg,drafts,scope,transport).run(identifier,lambda *args:answer)
    assert transport.calls==0
    with sqlite3.connect(drafts.path.parent/"email-send-intents.sqlite3") as c:
        assert c.execute("SELECT COUNT(*) FROM send_intents").fetchone()==(0,)


def test_draft_changed_during_review_denies_before_smtp(tmp_path):
    cfg,drafts,identifier,scope=configured(tmp_path);transport=FakeTransport()
    def change(display,challenge,timeout):
        drafts.update_draft(identifier,1,replace(draft(),body_text="Changed after display"),"reviewed-update-0001")
        return "APPROVE "+challenge
    with pytest.raises(DraftConflictError):
        service(cfg,drafts,scope,transport).run(identifier,change)
    assert transport.calls==0


def test_user_session_changes_after_prompt_denies(tmp_path):
    cfg,drafts,identifier,scope=configured(tmp_path);current=[scope];transport=FakeTransport()
    svc=ReviewedSendWorkflow(cfg,drafts,drafts.path.parent,lambda:current[0],transport_factory=lambda:transport)
    def change(display,challenge,timeout):
        current[0]=replace(scope,principal="other-user")
        return "APPROVE "+challenge
    with pytest.raises(ApprovalError):svc.run(identifier,change)
    assert transport.calls==0


def test_expiry_rechecked_at_actual_transport_boundary(tmp_path):
    cfg,drafts,identifier,scope=configured(tmp_path);now=[100.0];transport=FakeTransport()
    def factory():
        now[0]=401.0
        return transport
    svc=ReviewedSendWorkflow(cfg,drafts,drafts.path.parent,lambda:scope,transport_factory=factory,clock=lambda:now[0])
    result=svc.run(identifier,approve)
    assert result["state"]=="definite-failure" and transport.calls==0


@pytest.mark.parametrize("outcome,state", [("unknown","delivery-unknown"),("failure","definite-failure"),("crash","outcome-unknown")])
def test_failed_or_uncertain_smtp_never_automatically_retries(tmp_path,outcome,state):
    cfg,drafts,identifier,scope=configured(tmp_path);transport=FakeTransport(outcome)
    first=service(cfg,drafts,scope,transport).run(identifier,approve)
    assert first["state"]==state and first["automatic_retry_forbidden"]
    next_transport=FakeTransport();second=service(cfg,drafts,scope,next_transport).run(identifier,approve)
    assert second["state"] in {"delivery-unknown","definite-failure"}
    assert next_transport.calls==0 and transport.calls==1


def test_copy_failure_does_not_hide_acceptance_or_resend(tmp_path):
    cfg,drafts,identifier,scope=configured(tmp_path)
    (drafts.path.parent/"sent-copies").symlink_to(tmp_path)
    transport=FakeTransport();first=service(cfg,drafts,scope,transport).run(identifier,approve)
    assert first["state"]=="accepted" and first["sent_copy"]=="local-copy-failed"
    replay=service(cfg,drafts,scope,transport).run(identifier,approve)
    assert replay["replayed"] and transport.calls==1


def test_pending_record_retained_after_storage_error_after_smtp(tmp_path):
    cfg,drafts,identifier,scope=configured(tmp_path)
    db=drafts.path.parent/"email-send-intents.sqlite3"
    class Damaging(FakeTransport):
        def submit_once(self,submission):
            self.calls+=1;db.chmod(0o400);return SmtpSubmissionResult()
    transport=Damaging()
    try:
        first=service(cfg,drafts,scope,transport).run(identifier,approve)
        assert first["state"]=="outcome-unknown"
    finally:db.chmod(0o600)
    replay=service(cfg,drafts,scope,transport).run(identifier,approve)
    assert replay["state"]=="delivery-unknown" and transport.calls==1


def test_disabled_or_wrong_scope_does_not_open_send_database(tmp_path):
    cfg,drafts,identifier,scope=configured(tmp_path);transport=FakeTransport()
    for test_cfg,test_scope in [(replace(cfg,send_workflow=SendWorkflowSettings()),scope),
                               (cfg,replace(scope,profile="other")),(cfg,replace(scope,profile_home=str(tmp_path/"other")))]:
        with pytest.raises(ApprovalError):service(test_cfg,drafts,test_scope,transport).run(identifier,approve)
    assert not (drafts.path.parent/"email-send-intents.sqlite3").exists() and transport.calls==0


def test_local_test_rejects_public_host_and_real_recipients(tmp_path):
    cfg,drafts,identifier,scope=configured(tmp_path)
    with pytest.raises(ConfigError):replace(cfg,smtp=replace(cfg.smtp,host="smtp.example.com"))
    changed=replace(draft(),recipients=(__import__('hermes_email.models',fromlist=['EmailAddress']).EmailAddress("real@example.com"),))
    drafts.update_draft(identifier,1,changed,"real-recipient-0001")
    transport=FakeTransport()
    with pytest.raises(ApprovalError):service(cfg,drafts,scope,transport).run(identifier,approve)
    assert transport.calls==0


def test_review_only_grant_cannot_authorize_sending(tmp_path):
    cfg,drafts,identifier,scope=configured(tmp_path);d=drafts.get_draft(identifier)
    authority=ApprovalAuthority(scope)
    grant=authority.request(review_snapshot(cfg,d),lambda display,c,t:"APPROVE "+c)
    with pytest.raises(ApprovalError):authority.consume(grant,review_snapshot(cfg,d,purpose="send"),scope)


def test_pinned_revision_blocks_parallel_mutation_through_transport(tmp_path):
    cfg,drafts,identifier,scope=configured(tmp_path)
    entered=threading.Event();finished=threading.Event();errors=[];threads=[]
    def edit():
        entered.set()
        try:store(tmp_path,cfg).update_draft(identifier,1,replace(draft(),body_text="after"),"race-update-0001")
        except Exception as exc:errors.append(exc)
        finally:finished.set()
    class Observing(FakeTransport):
        def submit_once(self,submission):
            t=threading.Thread(target=edit);threads.append(t);t.start();assert entered.wait(1)
            assert not finished.wait(0.05)
            self.calls+=1;return SmtpSubmissionResult()
    transport=Observing();result=service(cfg,drafts,scope,transport).run(identifier,approve)
    for t in threads:t.join(5)
    assert result["state"]=="accepted" and finished.is_set() and not errors
    assert drafts.get_draft(identifier).revision==2


def test_full_workflow_uses_actual_loopback_smtp_sink_once(tmp_path):
    cfg,drafts,identifier,scope=configured(tmp_path);received=[]
    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.connection.settimeout(5);self.wfile.write(b"220 sink\r\n")
            while True:
                line=self.rfile.readline(10000)
                if not line:return
                verb=line.split()[0].upper()
                if verb==b"DATA":
                    self.wfile.write(b"354 end\r\n");lines=[]
                    while True:
                        item=self.rfile.readline(10000)
                        if item==b".\r\n":break
                        if not item:return
                        lines.append(item[1:] if item.startswith(b"..") else item)
                    received.append(b"".join(lines));self.wfile.write(b"250 accepted\r\n")
                elif verb==b"QUIT":self.wfile.write(b"221 bye\r\n");return
                else:self.wfile.write(b"250 OK\r\n")
    class Server(socketserver.ThreadingTCPServer):daemon_threads=True
    with Server(("127.0.0.1",0),Handler) as server:
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        class Loopback(FakeTransport):
            def submit_once(self,submission):
                self.calls+=1
                with smtplib.SMTP("127.0.0.1",server.server_address[1],timeout=5) as client:
                    assert not client.sendmail(submission.envelope_sender,submission.envelope_recipients,submission.message_bytes)
                return SmtpSubmissionResult()
        try:
            transport=Loopback();svc=service(cfg,drafts,scope,transport)
            assert svc.run(identifier,approve)["state"]=="accepted"
            assert svc.run(identifier,approve)["replayed"]
            assert transport.calls==1 and len(received)==1
            assert received[0]==next((drafts.path.parent/"sent-copies").glob("*.eml")).read_bytes()
        finally:server.shutdown();worker.join(5)


def test_configuration_changed_after_prompt_revokes_consent(tmp_path):
    cfg,drafts,identifier,scope=configured(tmp_path);current=[cfg];transport=FakeTransport()
    def presenter(display,challenge,timeout):
        current[0]=replace(cfg,smtp=replace(cfg.smtp,port=9999))
        return "APPROVE "+challenge
    svc=ReviewedSendWorkflow(cfg,drafts,drafts.path.parent,lambda:scope,
                            config_provider=lambda:current[0],transport_factory=lambda:transport)
    with pytest.raises(ApprovalError):svc.run(identifier,presenter)
    assert transport.calls==0


def test_real_hermes_cli_handler_has_no_model_send_surface(tmp_path,monkeypatch,capsys):
    from types import SimpleNamespace
    from dataclasses import asdict
    from hermes_email import human_cli, send_workflow
    from hermes_email.profile_guard import register
    from tools.registry import registry
    plugins=pytest.importorskip("hermes_cli.plugins")
    monkeypatch.setattr(plugins,"get_hermes_home",lambda:tmp_path)
    manager=plugins.PluginManager(scope_key=str(tmp_path))
    manifest=plugins.PluginManifest(name="reviewed-send-test",key="reviewed-send-test")
    ctx=plugins.PluginContext(manifest,manager)
    base=config()
    cfg=replace(base,hermes=HermesSettings(profile=ctx.profile_name),smtp=replace(base.smtp,host="127.0.0.1"),
                send_workflow=SendWorkflowSettings(mode="local-test"))
    raw=asdict(cfg)
    ctx.get_config=lambda name,default=None:raw.get(name,default)
    runtime=register(ctx);transport=FakeTransport()
    class SyntheticTerminal:
        def __init__(self,profile,home):self.scope=ApprovalScope(profile,str(home.resolve()),"test-user","test-session")
        def current_scope(self):return self.scope
        def present(self,display,challenge,timeout):return approve(display,challenge,timeout)
        def __enter__(self):return self
        def __exit__(self,*args):pass
    monkeypatch.setattr(human_cli,"LocalTerminal",SyntheticTerminal)
    monkeypatch.setattr(send_workflow,"SmtplibTransport",lambda *args,**kwargs:transport)
    try:
        identifier=runtime.draft_store.create_draft(draft(),"cli-draft-create-0001").draft_id
        assert "email-send" in manager._cli_commands
        assert "email-send" not in manager._plugin_commands
        assert registry.get_entry("email_send",scope=str(tmp_path)) is None
        assert registry.get_entry("email-send",scope=str(tmp_path)) is None
        command=manager._cli_commands["email-send"]["handler_fn"]
        assert command(SimpleNamespace(draft_id=identifier))==0
        first=json.loads(capsys.readouterr().out)
        assert first["state"]=="accepted" and first["smtp_accepted"]
        assert command(SimpleNamespace(draft_id=identifier))==0
        assert json.loads(capsys.readouterr().out)["replayed"]
        assert transport.calls==1
    finally:manager.unload(manifest.key)
    assert "email-send" not in manager._cli_commands


def test_after_smtp_draft_file_change_does_not_mask_durable_receipt(tmp_path):
    cfg,drafts,identifier,scope=configured(tmp_path)
    class FileChange(FakeTransport):
        def submit_once(self,submission):
            self.calls+=1;drafts.path.chmod(0o400);return SmtpSubmissionResult()
    transport=FileChange()
    try:
        result=service(cfg,drafts,scope,transport).run(identifier,approve)
        assert result["state"]=="accepted" and result["smtp_accepted"]
        assert transport.calls==1
    finally:drafts.path.chmod(0o600)
