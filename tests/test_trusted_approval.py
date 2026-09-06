import json
import os
import re
import select
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

from hermes_email.approval import (ApprovalAuthority, ApprovalError, ApprovalGrant,
                                   ApprovalScope, review_snapshot)
from hermes_email.human_cli import LocalTerminal, register_human_commands
from test_sending import config, store, create


def fixture(tmp_path):
    cfg = config(); drafts = store(tmp_path, cfg); identifier = create(drafts)
    snap = review_snapshot(cfg, drafts.get_draft(identifier))
    scope = ApprovalScope("email", str(tmp_path), "uid:1000", "session-1")
    return cfg, drafts, snap, scope


def approve(display, challenge, timeout):
    return "APPROVE " + challenge


def test_exact_snapshot_once_only(tmp_path):
    cfg, drafts, snap, scope = fixture(tmp_path)
    authority = ApprovalAuthority(scope)
    grant = authority.request(snap, approve)
    result = authority.consume(grant, snap, scope)
    assert result.draft_id == snap.draft_id and result.revision == snap.revision
    with pytest.raises(ApprovalError):
        authority.consume(grant, snap, scope)


@pytest.mark.parametrize("field", ["subject", "body_text", "bcc", "revision", "in_reply_to"])
def test_any_reviewed_content_change_denies(tmp_path, field):
    from hermes_email.models import EmailAddress
    cfg, drafts, snap, scope = fixture(tmp_path)
    authority = ApprovalAuthority(scope); grant = authority.request(snap, approve)
    changes = {"subject": "changed", "body_text": "changed", "bcc": (EmailAddress("new@example.invalid"),),
               "revision": 2, "in_reply_to": "<other@example.invalid>"}
    draft = drafts.get_draft(snap.draft_id)
    new_snapshot = review_snapshot(cfg, replace(draft, **{field: changes[field]}))
    with pytest.raises(ApprovalError):
        authority.consume(grant, new_snapshot, scope)
    with pytest.raises(ApprovalError):
        authority.consume(grant, snap, scope)


@pytest.mark.parametrize("field", ["profile", "profile_home", "principal", "session"])
def test_identity_binding(tmp_path, field):
    cfg, drafts, snap, scope = fixture(tmp_path)
    authority = ApprovalAuthority(scope); grant = authority.request(snap, approve)
    with pytest.raises(ApprovalError):
        authority.consume(grant, snap, replace(scope, **{field: "other"}))


def test_policy_sender_or_endpoint_change_requires_new_consent(tmp_path):
    cfg, drafts, snap, scope = fixture(tmp_path)
    for smtp in [replace(cfg.smtp, sender_address="other@example.invalid"),
                 replace(cfg.smtp, host="other.example.invalid")]:
        authority = ApprovalAuthority(scope); grant = authority.request(snap, approve)
        changed = review_snapshot(replace(cfg, smtp=smtp), drafts.get_draft(snap.draft_id))
        with pytest.raises(ApprovalError):
            authority.consume(grant, changed, scope)


@pytest.mark.parametrize("answer", ["yes", "true", "approved", "APPROVE", {"confirmed": True}, None])
def test_model_like_values_cannot_authorize(tmp_path, answer):
    _, _, snap, scope = fixture(tmp_path)
    with pytest.raises(ApprovalError):
        ApprovalAuthority(scope).request(snap, lambda *args: answer)


def test_expiry_and_backward_clock_and_restart(tmp_path):
    _, _, snap, scope = fixture(tmp_path)
    now = [100.0]
    for end in [99.0, 400.0, 401.0]:
        now[0] = 100.0
        authority = ApprovalAuthority(scope, clock=lambda: now[0]); grant = authority.request(snap, approve)
        now[0] = end
        with pytest.raises(ApprovalError):
            authority.consume(grant, snap, scope)
    authority = ApprovalAuthority(scope); grant = authority.request(snap, approve)
    with pytest.raises(ApprovalError):
        ApprovalAuthority(scope).consume(grant, snap, scope)
    authority.close()
    with pytest.raises(ApprovalError):
        authority.consume(grant, snap, scope)


def test_timeout_during_presentation_and_exception(tmp_path):
    _, _, snap, scope = fixture(tmp_path); now=[0.0]
    def late(display, challenge, timeout):
        now[0]=301.0
        return "APPROVE " + challenge
    with pytest.raises(ApprovalError):
        ApprovalAuthority(scope, clock=lambda: now[0]).request(snap, late)
    def fail(*args):
        raise RuntimeError("SYNTHETIC PRIVATE DETAIL")
    with pytest.raises(ApprovalError) as error:
        ApprovalAuthority(scope).request(snap, fail)
    assert "PRIVATE" not in str(error.value)


def test_forged_receipt_and_swapped_grants(tmp_path):
    _, _, snap, scope = fixture(tmp_path)
    authority = ApprovalAuthority(scope); grant = authority.request(snap, approve)
    with pytest.raises(ApprovalError):
        authority.consume(ApprovalGrant(grant.nonce, "0"*64), snap, scope)
    with pytest.raises(ApprovalError):
        authority.consume({"confirmed": True}, snap, scope)


def test_review_is_full_and_json_escaped(tmp_path):
    cfg, drafts, snap, scope = fixture(tmp_path)
    data = json.loads(snap.display)
    assert data["body_text"] == drafts.get_draft(snap.draft_id).body_text
    assert data["bcc"][0]["address"] == "private@example.invalid"
    assert "\x1b" not in snap.display
    assert cfg.smtp.password_ref not in snap.display


@pytest.mark.skipif(os.name != "posix", reason="local terminal surface is POSIX-only")
@pytest.mark.parametrize("accept", [True, False])
def test_real_controlling_terminal_challenge(tmp_path, accept):
    import pty
    pid, fd = pty.fork()
    if pid == 0:
        try:
            with LocalTerminal("email", tmp_path) as terminal:
                answer = terminal.present('{"body_text":"complete","bcc":["hidden@example.invalid"]}', "a"*24, 5)
            os._exit(0 if answer == "APPROVE " + "a"*24 else 3)
        except BaseException:
            os._exit(4)
    output=b""; status=None
    try:
        deadline=time.monotonic()+7
        while b"> " not in output:
            assert time.monotonic()<deadline
            if select.select([fd],[],[],0.1)[0]:
                output += os.read(fd, 4096)
        assert b'hidden@example.invalid' in output and b'complete' in output
        os.write(fd, ("APPROVE " + "a"*24 + "\n" if accept else "yes\n").encode())
        _, status=os.waitpid(pid,0)
        assert os.waitstatus_to_exitcode(status)==(0 if accept else 3)
    finally:
        os.close(fd)
        if status is None:
            os.kill(pid,9);os.waitpid(pid,0)


def test_noninteractive_subprocess_cannot_approve(tmp_path):
    import subprocess
    result=subprocess.run([sys.executable,"-c",
        "from pathlib import Path; from hermes_email.human_cli import LocalTerminal; LocalTerminal('email',Path('.'))"],
        cwd=Path(__file__).resolve().parents[1], start_new_session=True,
        capture_output=True, text=True, timeout=10)
    assert result.returncode != 0 and "foreground terminal" in result.stderr


def test_fork_cannot_consume_parent_grant(tmp_path):
    if not hasattr(os,"fork"):
        pytest.skip("fork is POSIX-only")
    _, _, snap, scope=fixture(tmp_path); authority=ApprovalAuthority(scope); grant=authority.request(snap,approve)
    pid=os.fork()
    if pid==0:
        try:
            authority.consume(grant,snap,scope)
        except ApprovalError:
            os._exit(0)
        os._exit(1)
    assert os.waitstatus_to_exitcode(os.waitpid(pid,0)[1])==0
    assert authority.consume(grant,snap,scope)


def test_real_hermes_registration_is_cli_only(tmp_path, monkeypatch):
    plugins = pytest.importorskip("hermes_cli.plugins")
    from hermes_email.profile_guard import register
    from tools.registry import registry
    monkeypatch.setattr(plugins, "get_hermes_home", lambda: tmp_path)
    manager = plugins.PluginManager(scope_key=str(tmp_path))
    manifest = plugins.PluginManifest(name="approval-test", key="approval-test")
    ctx = plugins.PluginContext(manifest, manager)
    raw={"hermes":{"profile":ctx.profile_name}, "drafts":{"mode":"sqlite","account_namespace":"a"}}
    ctx.get_config=lambda name,default=None: raw.get(name,default)
    runtime=register(ctx)
    try:
        assert "email-approve" in manager._cli_commands
        assert "email-approve" not in manager._plugin_commands
        assert registry.get_entry("email_approve",scope=str(tmp_path)) is None
        assert registry.get_entry("email_send",scope=str(tmp_path)) is None
        assert not list(tmp_path.rglob("*.sqlite3"))
    finally:
        manager.unload(manifest.key)
    assert "email-approve" not in manager._cli_commands
