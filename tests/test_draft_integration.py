import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from hermes_email.draft_tools import CREATE_DRAFT_TOOL, GET_DRAFT_TOOL
from hermes_email.plugin import register
from hermes_email.tools import LIST_TOOL


class Handle:
    def __init__(self) -> None:
        self.disposed = False

    def dispose(self) -> None:
        self.disposed = True


class RuntimeContext:
    profile_name = "draft-profile"

    def __init__(
        self,
        tmp_path: Path,
        config: dict[str, Any],
        *,
        reject: str | None = None,
        reject_skill: bool = False,
    ) -> None:
        self._config = config
        self.state = SimpleNamespace(data_dir=tmp_path / "plugin-data")
        self.reject = reject
        self.reject_skill = reject_skill
        self.tools = []
        self.handles = []
        self.commands = []
        self.command_handles = []
        self.skills = []
        self.unloads = []
        self.unload_handles = []

    def get_config(self, key: str, default=None):
        return self._config.get(key, default)

    def register_tool(self, **kwargs):
        self.tools.append(kwargs)
        if kwargs["name"] == self.reject:
            return None
        handle = Handle()
        self.handles.append(handle)
        return handle

    def register_command(self, name, handler, description=""):
        self.commands.append((name, handler, description))
        handle = Handle()
        self.command_handles.append(handle)
        return handle

    def register_skill(self, name, path, *, description) -> None:
        if self.reject_skill:
            raise RuntimeError("synthetic skill collision")
        self.skills.append((name, path, description))

    def on_unload(self, callback):
        self.unloads.append(callback)
        handle = Handle()
        self.unload_handles.append(handle)
        return handle


def enabled_config(*, observations: bool = False) -> dict[str, Any]:
    value: dict[str, Any] = {
        "drafts": {"mode": "sqlite", "account_namespace": "draft-account"}
    }
    if observations:
        value["email"] = {"provider": "mock", "read_mode": "mock"}
        value["storage"] = {
            "mode": "sqlite",
            "account_namespace": "read-account",
        }
    return value


def create_args() -> dict[str, Any]:
    return {
        "to": [{"address": "person@example.invalid"}],
        "cc": [],
        "bcc": [],
        "subject": "Local draft",
        "body_text": "Review only.",
        "operation_id": "create-operation-0001",
    }


def test_enabled_registration_uses_public_state_path_but_remains_lazy(
    tmp_path: Path,
) -> None:
    context = RuntimeContext(tmp_path, enabled_config())
    runtime = register(context)
    database = context.state.data_dir / "email-drafts.sqlite3"

    assert runtime.draft_store is not None
    assert runtime.draft_store.path == database
    assert runtime.get_runtime_status().draft_enabled is True
    assert database.exists() is False
    assert all(tool["check_fn"]() is True for tool in context.tools[:6])
    assert database.exists() is False
    status = context.commands[0][1]("")
    assert "Draft: enabled" in status
    assert "Send: unavailable" in status
    assert database.exists() is False

    create = next(tool for tool in context.tools if tool["name"] == CREATE_DRAFT_TOOL)
    result = json.loads(asyncio.run(create["handler"](create_args())))
    assert result["ok"] is True
    assert database.is_file()

    context.unloads[0]()
    assert runtime.get_runtime_status().draft_enabled is False


def test_disabled_registration_never_accesses_state(tmp_path: Path) -> None:
    class NoStateContext(RuntimeContext):
        @property
        def state(self):
            raise AssertionError("disabled plugin accessed state directory")

        @state.setter
        def state(self, value):
            del value

    context = NoStateContext(tmp_path, {})
    runtime = register(context)

    assert runtime.draft_store is None
    assert runtime.get_runtime_status().draft_enabled is False
    assert not (tmp_path / "plugin-data").exists()


def test_read_and_draft_databases_remain_independent(tmp_path: Path) -> None:
    context = RuntimeContext(tmp_path, enabled_config(observations=True))
    runtime = register(context)
    create = next(tool for tool in context.tools if tool["name"] == CREATE_DRAFT_TOOL)
    list_mail = next(tool for tool in context.tools if tool["name"] == LIST_TOOL)

    created = json.loads(asyncio.run(create["handler"](create_args())))
    draft_database = context.state.data_dir / "email-drafts.sqlite3"
    observation_database = context.state.data_dir / "email-observations.sqlite3"
    draft_digest = hashlib.sha256(draft_database.read_bytes()).digest()
    assert observation_database.exists() is False

    mail = json.loads(asyncio.run(list_mail["handler"]({"limit": 1})))

    assert created["ok"] is True and mail["ok"] is True
    assert observation_database.is_file()
    assert hashlib.sha256(draft_database.read_bytes()).digest() == draft_digest
    assert runtime.draft_store.count_drafts() == 1
    assert runtime.observation_store.count_observations() == 1


def test_read_tool_collision_rolls_back_all_prior_draft_tools(tmp_path: Path) -> None:
    context = RuntimeContext(tmp_path, enabled_config(), reject=LIST_TOOL)

    with pytest.raises(Exception):
        register(context)

    assert len(context.handles) == 6
    assert all(handle.disposed for handle in context.handles)


def test_skill_failure_rolls_back_all_nine_tools_and_closes_runtime(
    tmp_path: Path,
) -> None:
    context = RuntimeContext(tmp_path, enabled_config(), reject_skill=True)

    with pytest.raises(RuntimeError, match="skill collision"):
        register(context)

    assert len(context.handles) == 9
    assert all(handle.disposed for handle in context.handles)
    assert context.command_handles[0].disposed is True
    assert context.unload_handles[0].disposed is True
    assert "Draft: disabled" in context.commands[0][1]("")


def test_bad_read_provider_does_not_disable_independent_local_drafts(
    tmp_path: Path,
) -> None:
    configured = enabled_config()
    configured["email"] = {"provider": "gmail", "read_mode": "disabled"}
    context = RuntimeContext(tmp_path, configured)

    runtime = register(context)

    assert runtime.get_runtime_status().draft_enabled is True
    assert runtime.get_runtime_status().diagnostic == "unsupported-provider"
    create = next(tool for tool in context.tools if tool["name"] == CREATE_DRAFT_TOOL)
    assert json.loads(asyncio.run(create["handler"](create_args())))["ok"] is True


def test_pinned_hermes_registry_dispatches_installed_style_draft_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugins = pytest.importorskip("hermes_cli.plugins")
    registry_module = pytest.importorskip("tools.registry")
    monkeypatch.setattr(plugins, "get_hermes_home", lambda: tmp_path)
    scope = str(tmp_path)
    manager = plugins.PluginManager(scope_key=scope)
    manifest = plugins.PluginManifest(
        name="hermes-email-drafts-test", key="hermes-email-drafts-test"
    )
    context = plugins.PluginContext(manifest, manager)
    raw_config = enabled_config()
    context.get_config = lambda key, default=None: raw_config.get(key, default)

    runtime = register(context)
    try:
        created = json.loads(
            registry_module.registry.dispatch(CREATE_DRAFT_TOOL, create_args(), scope=scope)
        )
        fetched = json.loads(
            registry_module.registry.dispatch(
                GET_DRAFT_TOOL,
                {"draft_id": created["mutation"]["draft_id"]},
                scope=scope,
            )
        )
        assert created["ok"] is True
        assert fetched["found"] is True
        assert fetched["draft"]["sent"] is False
        assert runtime.draft_store.path.parent == context.state.data_dir
    finally:
        manager.unload(manifest.key)

    assert registry_module.registry.get_entry(CREATE_DRAFT_TOOL, scope=scope) is None
