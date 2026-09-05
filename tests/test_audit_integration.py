"""Exercise every registered tool with audit enabled, including failure paths."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_email.audit import AuditError, _ALLOWED_OPERATIONS
from hermes_email.models import EmailAddress, EmailMessage
from hermes_email.profile_guard import register
from hermes_email.providers import MockEmailProvider, ProviderTimeoutError


class Context:
    profile_name = "audit-test"

    def __init__(self, root: Path):
        self.state = SimpleNamespace(data_dir=root / "private")
        self.tools = {}
        self.commands = {}
        self.settings = {
            "hermes": {"profile": self.profile_name},
            "email": {"provider": "mock", "read_mode": "mock"},
            "drafts": {"mode": "sqlite", "account_namespace": "audit-test"},
            "audit": {"mode": "sqlite"},
            "reply_policy": {"own_addresses": ["support@example.invalid"]},
        }

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def register_tool(self, **tool):
        self.tools[tool["name"]] = tool
        return SimpleNamespace(dispose=lambda: None)

    def register_command(self, name, handler, **kwargs):
        self.commands[name] = handler
        return SimpleNamespace(dispose=lambda: None)

    def register_skill(self, *args, **kwargs):
        return None

    def on_unload(self, callback):
        return SimpleNamespace(dispose=lambda: None)


def invoke(context, name, args):
    return json.loads(asyncio.run(context.tools[name]["handler"](args)))


def content():
    return {
        "to": [{"address": "person@example.invalid"}], "cc": [], "bcc": [],
        "subject": "PRIVATE SUBJECT SENTINEL", "body_text": "PRIVATE BODY SENTINEL",
    }


def test_every_tool_with_audit_enabled(tmp_path):
    context = Context(tmp_path)
    runtime = register(context)
    assert len(context.tools) == 14
    assert all(tool["check_fn"]() for tool in context.tools.values())
    invoked = set()

    def call(name, args):
        result = invoke(context, name, args)
        assert result["ok"] is True, result
        assert result["audit"] == {"recorded": True, "diagnostic": None, "gap_detected": False}
        invoked.add(name)
        return result

    call("email_list_messages", {"limit": 2})
    call("email_get_message", {"message_id": "mock-message-html-003"})
    call("email_search_messages", {"query": "sample"})
    call("email_get_thread", {"message_id": "mock-message-customer-001"})
    call("email_provider_health", {})
    created = call("email_create_draft", {**content(), "operation_id": "audit-create-0001"})
    did = created["mutation"]["draft_id"]
    for name, oid in (("email_create_reply_draft", "audit-reply-00001"),
                      ("email_create_reply_all_draft", "audit-reply-all-1")):
        call(name, {"message_id": "mock-message-customer-001",
                    "body_text": "PRIVATE REPLY SENTINEL", "operation_id": oid})
    call("email_list_drafts", {})
    call("email_get_draft", {"draft_id": did})
    call("email_review_draft_send", {"draft_id": did})
    call("email_update_draft", {**content(), "draft_id": did, "expected_revision": 1,
                                 "operation_id": "audit-update-001"})
    call("email_trash_draft", {"draft_id": did, "expected_revision": 2,
                                "operation_id": "audit-trash-0001"})
    call("email_restore_draft", {"draft_id": did, "expected_revision": 3,
                                  "operation_id": "audit-restore-01"})
    assert invoked == set(context.tools)
    rows = runtime.audit_store.recent(limit=100)
    assert len(rows) == 14
    assert {row["operation"] for row in rows} == _ALLOWED_OPERATIONS
    database = runtime.audit_store.path.read_bytes()
    for forbidden in (b"SENTINEL", b"example.invalid", b"mock-message", did.encode()):
        assert forbidden not in database
    runtime.close()


@pytest.mark.parametrize("tool_name", [
    "email_list_messages", "email_get_message", "email_search_messages",
    "email_get_thread", "email_provider_health", "email_create_draft",
    "email_create_reply_draft", "email_create_reply_all_draft", "email_list_drafts",
    "email_get_draft", "email_review_draft_send", "email_update_draft",
    "email_trash_draft", "email_restore_draft",
])
def test_each_tool_records_argument_errors_without_throwing(tmp_path, tool_name):
    context = Context(tmp_path)
    runtime = register(context)
    result = invoke(context, tool_name, {"unexpected": "PRIVATE VALUE"})
    assert result["ok"] is False
    assert result["audit"]["recorded"] is True
    assert runtime.audit_store.recent()[0]["outcome"] == "invalid-arguments"
    assert "PRIVATE VALUE" not in json.dumps(result)
    runtime.close()


def test_audit_failure_preserves_mutation_receipt_and_idempotency(tmp_path, monkeypatch):
    context = Context(tmp_path)
    runtime = register(context)
    original = runtime.audit_store.record
    calls = []

    def fail(*args):
        calls.append(args)
        raise AuditError("PRIVATE DATABASE PATH OR DETAIL")

    monkeypatch.setattr(runtime.audit_store, "record", fail)
    args = {**content(), "operation_id": "audit-create-0001"}
    result = invoke(context, "email_create_draft", args)
    assert result["ok"] is True
    assert result["mutation"]["replayed"] is False
    assert result["audit"] == {"recorded": False, "diagnostic": "audit-write-failed", "gap_detected": True}
    assert len(calls) == 1  # no error-handler recursion or second audit attempt
    assert "PRIVATE DATABASE" not in json.dumps(result)
    assert "Audit diagnostic: audit-write-failed" in context.commands["email-status"]("")
    replay = invoke(context, "email_create_draft", args)
    assert replay["ok"] is True
    assert replay["mutation"]["replayed"] is True
    assert replay["mutation"]["draft_id"] == result["mutation"]["draft_id"]
    assert len(asyncio.run(runtime.list_drafts()).drafts) == 1
    monkeypatch.setattr(runtime.audit_store, "record", original)
    recovered = invoke(context, "email_list_drafts", {})
    assert recovered["audit"] == {"recorded": True, "diagnostic": None, "gap_detected": True}
    assert runtime.get_runtime_status().audit_diagnostic == "audit-write-failed"
    assert runtime.get_runtime_status().send_enabled is False
    runtime.close()


def test_audit_failure_preserves_original_provider_error(tmp_path, monkeypatch):
    context = Context(tmp_path)
    runtime = register(context)

    async def timeout(*args, **kwargs):
        raise ProviderTimeoutError("PRIVATE PROVIDER DETAIL")

    def audit_error(*args):
        raise OSError("PRIVATE AUDIT DETAIL")

    monkeypatch.setattr(runtime.provider, "fetch_messages", timeout)
    monkeypatch.setattr(runtime.audit_store, "record", audit_error)
    result = invoke(context, "email_list_messages", {})
    assert result["error"]["code"] == "provider-timeout"
    assert result["audit"]["recorded"] is False
    assert "PRIVATE" not in json.dumps(result)
    runtime.close()


def test_failed_health_probe_is_not_recorded_as_healthy(tmp_path, monkeypatch):
    context = Context(tmp_path)
    runtime = register(context)

    async def timeout():
        raise ProviderTimeoutError("PRIVATE HEALTH DETAIL")

    monkeypatch.setattr(runtime.provider, "check_health", timeout)
    result = invoke(context, "email_provider_health", {})
    assert result["ok"] is True  # probe completed; provider is NOT ready
    assert result["read_ready"] is False
    assert result["diagnostic"] == "provider-timeout"
    assert runtime.audit_store.recent()[0]["outcome"] == "provider-timeout"
    assert runtime.audit_store.recent()[0]["item_count"] == 0
    runtime.close()


def test_missing_draft_audit_count_is_zero(tmp_path):
    context = Context(tmp_path)
    runtime = register(context)
    missing = "draft_" + "A" * 32
    result = invoke(context, "email_get_draft", {"draft_id": missing})
    assert result["ok"] is True and result["found"] is False
    assert runtime.audit_store.recent()[0]["item_count"] == 0
    runtime.close()


def test_profile_denial_does_not_create_audit_or_register_tools(tmp_path):
    context = Context(tmp_path)
    context.profile_name = "other-profile"
    runtime = register(context)
    assert context.tools == {}
    assert not context.state.data_dir.exists()
    assert "profile-blocked" in context.commands["email-status"]("")
    runtime.close()


@pytest.mark.parametrize("audit_fails", [False, True])
def test_real_hermes_registry_preserves_reply_all_receipt(tmp_path, monkeypatch, audit_fails):
    plugins = pytest.importorskip("hermes_cli.plugins")
    registry_module = pytest.importorskip("tools.registry")
    monkeypatch.setattr(plugins, "get_hermes_home", lambda: tmp_path)
    manager = plugins.PluginManager(scope_key=str(tmp_path))
    manifest = plugins.PluginManifest(name="audit-registry-test", key="audit-registry-test")
    context = plugins.PluginContext(manifest, manager)
    settings = Context(tmp_path).settings
    settings["hermes"]["profile"] = context.profile_name
    context.get_config = lambda key, default=None: settings.get(key, default)
    runtime = register(context)
    args = {"message_id": "mock-message-customer-001", "body_text": "Review locally.",
            "operation_id": "registry-reply-all-0001"}
    try:
        if audit_fails:
            def fail(*args):
                raise AuditError("SYNTHETIC PRIVATE DETAIL")
            monkeypatch.setattr(runtime.audit_store, "record", fail)
        results = [json.loads(registry_module.registry.dispatch(
            "email_create_reply_all_draft", args, scope=str(tmp_path)
        )) for _ in range(2)]
        assert all(r["ok"] is True and r["sent"] is False for r in results)
        assert results[0]["audit"]["recorded"] is (not audit_fails)
        assert results[1]["mutation"]["replayed"] is True
        assert results[0]["mutation"]["draft_id"] == results[1]["mutation"]["draft_id"]
        assert "SYNTHETIC PRIVATE" not in json.dumps(results)
    finally:
        manager.unload(manifest.key)
    assert registry_module.registry.get_entry("email_create_reply_all_draft", scope=str(tmp_path)) is None
