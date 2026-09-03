import asyncio
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from hermes_email.config import EmailPluginConfig, StorageSettings
from hermes_email.plugin import EmailPlugin, EmailRuntimeState, register
from hermes_email.providers import MockEmailProvider, ProviderProtocolError
from hermes_email.storage import (
    EmailStorageResourceError,
    EmailStorageSchemaError,
    EmailStorageSecurityError,
    EmailStorageUnavailableError,
    EmailStorageValidationError,
    SqliteObservationStore,
    observation_namespace,
)
from hermes_email.tools import LIST_TOOL, register_read_tools


def persistent_config() -> EmailPluginConfig:
    return EmailPluginConfig.from_mapping(
        {
            "email": {
                "provider": "mock",
                "read_mode": "mock",
                "draft_mode": "disabled",
            },
            "storage": {
                "mode": "sqlite",
                "account_namespace": "integration-inbox",
            },
        }
    )


def persistent_plugin(tmp_path: Path) -> tuple[EmailPlugin, SqliteObservationStore]:
    config = persistent_config()
    store = SqliteObservationStore(
        tmp_path / "data" / "email-observations.sqlite3",
        observation_namespace(config),
        config.storage,
    )
    plugin = EmailPlugin(config, provider=MockEmailProvider(), observation_store=store)
    return plugin, store


def test_explicit_fetch_persists_identity_without_suppressing_repeated_reads(
    tmp_path: Path,
) -> None:
    plugin, store = persistent_plugin(tmp_path)

    first = asyncio.run(plugin.fetch_messages(limit=2))
    second = asyncio.run(plugin.fetch_messages(limit=2))

    assert first == second
    assert len(first.messages) == 2
    assert store.count_observations() == 2
    assert store.observation_count(first.messages[0].message_id) == 2
    assert plugin.get_runtime_status().storage_enabled is True


def test_lookup_and_search_observe_only_explicit_provider_results(tmp_path: Path) -> None:
    plugin, store = persistent_plugin(tmp_path)

    found = asyncio.run(plugin.get_message("mock-message-customer-001"))
    missing = asyncio.run(plugin.get_message("missing"))
    searched = asyncio.run(plugin.search_messages("sample", limit=2))

    assert found is not None
    assert missing is None
    assert searched.next_cursor == "mock-page-offset-0002"
    assert store.count_observations() == 2
    assert store.observation_count("mock-message-customer-001") == 2
    assert store.observation_count("mock-message-empty-002") == 1


def test_provider_over_return_is_rejected_before_any_observation(tmp_path: Path) -> None:
    class OverReturningProvider(MockEmailProvider):
        async def fetch_messages(self, *, limit=50, cursor=None):
            del limit
            return await super().fetch_messages(limit=2, cursor=cursor)

    config = persistent_config()
    store = SqliteObservationStore(
        tmp_path / "data" / "email-observations.sqlite3",
        observation_namespace(config),
        config.storage,
    )
    plugin = EmailPlugin(
        config,
        provider=OverReturningProvider(),
        observation_store=store,
    )

    with pytest.raises(ProviderProtocolError):
        asyncio.run(plugin.fetch_messages(limit=1))

    assert store.path.exists() is False


def test_storage_failure_fails_read_closed_and_sets_fixed_runtime_state(
    tmp_path: Path,
) -> None:
    class FailingStore:
        def observe_messages(self, messages) -> None:
            del messages
            raise EmailStorageUnavailableError("SYNTHETIC PRIVATE PATH")

        def close(self) -> None:
            pass

    config = persistent_config()
    plugin = EmailPlugin(
        config,
        provider=MockEmailProvider(),
        observation_store=FailingStore(),  # type: ignore[arg-type]
    )

    with pytest.raises(EmailStorageUnavailableError):
        asyncio.run(plugin.fetch_messages(limit=1))

    status = plugin.get_runtime_status()
    assert status.state is EmailRuntimeState.STORAGE_ERROR
    assert status.diagnostic == "storage-error"
    assert status.read_enabled is False
    assert "SYNTHETIC" not in repr(status)
    health_after_failure = asyncio.run(plugin.check_provider_health())
    assert health_after_failure.state is EmailRuntimeState.STORAGE_ERROR
    assert health_after_failure.read_enabled is False
    with pytest.raises(EmailStorageUnavailableError):
        asyncio.run(plugin.get_message("missing"))
    assert plugin.get_runtime_status().state is EmailRuntimeState.STORAGE_ERROR


@pytest.mark.parametrize(
    ("error_type", "code"),
    [
        (EmailStorageUnavailableError, "storage-unavailable"),
        (EmailStorageSecurityError, "storage-insecure"),
        (EmailStorageSchemaError, "storage-incompatible"),
        (EmailStorageResourceError, "storage-full"),
        (EmailStorageValidationError, "storage-invalid"),
    ],
)
def test_storage_failure_is_a_fixed_redacted_tool_error(error_type, code: str) -> None:
    class FailingStore:
        def observe_messages(self, messages) -> None:
            del messages
            raise error_type("SYNTHETIC PRIVATE PATH")

        def close(self) -> None:
            pass

    class Context:
        def __init__(self) -> None:
            self.tools: list[dict[str, Any]] = []

        def register_tool(self, **kwargs):
            self.tools.append(kwargs)
            return SimpleNamespace(dispose=lambda: None)

    plugin = EmailPlugin(
        persistent_config(),
        provider=MockEmailProvider(),
        observation_store=FailingStore(),  # type: ignore[arg-type]
    )
    context = Context()
    register_read_tools(context, plugin)
    list_tool = next(tool for tool in context.tools if tool["name"] == LIST_TOOL)

    result = json.loads(asyncio.run(list_tool["handler"]({"limit": 1})))

    assert result == {
        "ok": False,
        "operation": "list",
        "error": {"code": code},
    }
    assert "SYNTHETIC" not in json.dumps(result)


def test_cancellation_waits_for_observation_outcome_without_returning_mail() -> None:
    started = threading.Event()
    release = threading.Event()
    completed = threading.Event()

    class BlockingStore:
        def observe_messages(self, messages) -> None:
            assert messages
            started.set()
            assert release.wait(timeout=5)
            completed.set()

        def close(self) -> None:
            release.set()

    plugin = EmailPlugin(
        persistent_config(),
        provider=MockEmailProvider(),
        observation_store=BlockingStore(),  # type: ignore[arg-type]
    )

    async def scenario() -> None:
        operation = asyncio.create_task(plugin.fetch_messages(limit=1))
        assert await asyncio.to_thread(started.wait, 5)
        operation.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await operation

    asyncio.run(scenario())
    assert completed.is_set()


def test_plugin_close_closes_store_and_disables_storage_status(tmp_path: Path) -> None:
    plugin, store = persistent_plugin(tmp_path)
    asyncio.run(plugin.fetch_messages(limit=1))

    plugin.close()
    plugin.close()

    assert plugin.get_runtime_status().storage_enabled is False
    with pytest.raises(EmailStorageUnavailableError):
        store.count_observations()


class _Registration:
    def dispose(self) -> None:
        pass


class RuntimeContext:
    profile_name = "test-profile"

    def __init__(self, tmp_path: Path, config: dict[str, Any]) -> None:
        self._config = config
        self.state = SimpleNamespace(data_dir=tmp_path / "plugin-data")
        self.commands = []
        self.tools = []
        self.skills = []
        self.unload_callbacks = []

    def get_config(self, key: str, default=None):
        return self._config.get(key, default)

    def on_unload(self, callback) -> None:
        self.unload_callbacks.append(callback)

    def register_command(self, name, handler, description="") -> None:
        self.commands.append((name, handler, description))

    def register_tool(self, **kwargs):
        self.tools.append(kwargs)
        return _Registration()

    def register_skill(self, name, path, *, description) -> None:
        self.skills.append((name, path, description))


def test_registration_uses_public_state_data_dir_but_opens_storage_lazily(
    tmp_path: Path,
) -> None:
    context = RuntimeContext(
        tmp_path,
        {
            "email": {
                "provider": "mock",
                "read_mode": "mock",
                "draft_mode": "disabled",
            },
            "storage": {
                "mode": "sqlite",
                "account_namespace": "runtime-inbox",
            },
        },
    )

    plugin = register(context)
    database = context.state.data_dir / "email-observations.sqlite3"

    assert plugin.observation_store is not None
    assert database.exists() is False
    list_tool = next(tool for tool in context.tools if tool["name"] == LIST_TOOL)
    result = json.loads(asyncio.run(list_tool["handler"]({"limit": 1})))
    assert result["ok"] is True
    assert database.is_file()


def test_pinned_hermes_context_supplies_profile_scoped_storage_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugins = pytest.importorskip("hermes_cli.plugins")
    registry_module = pytest.importorskip("tools.registry")
    monkeypatch.setattr(plugins, "get_hermes_home", lambda: tmp_path)
    scope = str(tmp_path)
    manager = plugins.PluginManager(scope_key=scope)
    manifest = plugins.PluginManifest(
        name="hermes-email-storage-test",
        key="hermes-email-storage-test",
    )
    context = plugins.PluginContext(manifest, manager)
    raw_config = {
        "email": {
            "provider": "mock",
            "read_mode": "mock",
            "draft_mode": "disabled",
        },
        "storage": {
            "mode": "sqlite",
            "account_namespace": "pinned-context-inbox",
        },
    }
    context.get_config = lambda key, default=None: raw_config.get(key, default)

    runtime = register(context)
    try:
        assert runtime.observation_store is not None
        assert runtime.observation_store.path == (
            context.state.data_dir / "email-observations.sqlite3"
        )
        assert runtime.observation_store.path.is_relative_to(tmp_path / "plugin-data")
        assert runtime.observation_store.path.exists() is False
        result = json.loads(
            registry_module.registry.dispatch(LIST_TOOL, {"limit": 1}, scope=scope)
        )
        assert result["ok"] is True
        assert runtime.observation_store.path.is_file()
    finally:
        manager.unload(manifest.key)


def test_disabled_registration_never_accesses_state_directory(tmp_path: Path) -> None:
    class NoStateContext(RuntimeContext):
        @property
        def state(self):
            raise AssertionError("disabled registration accessed persistent state")

        @state.setter
        def state(self, value):
            del value

    context = NoStateContext(tmp_path, {})

    plugin = register(context)

    assert plugin.get_runtime_status().storage_enabled is False
    assert not (tmp_path / "plugin-data").exists()
