import socket
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

import hermes_email
import hermes_email.plugin as plugin_module
from hermes_email.config import EmailPluginConfig
from hermes_email.plugin import EmailRuntimeState, register
from hermes_email.providers import MockEmailProvider


class FakeHermesContext:
    def __init__(
        self,
        settings: dict[str, Any] | None = None,
        *,
        profile_name: str = "ef-sinn-mail",
    ) -> None:
        self.settings = settings or {}
        self.profile_name = profile_name
        self.config_reads: list[str] = []
        self.skills: list[tuple[str, Path, str]] = []
        self.unload_callbacks = []

    def get_config(self, key: str, default=None):
        self.config_reads.append(key)
        return self.settings.get(key, default)

    def on_unload(self, callback) -> None:
        self.unload_callbacks.append(callback)

    def register_skill(self, name: str, path: Path, *, description: str) -> None:
        self.skills.append((name, path, description))


def mock_settings() -> dict[str, Any]:
    return {
        "email": {
            "provider": "mock",
            "read_mode": "mock",
            "draft_mode": "mock",
        },
        "hermes": {"profile": "auto"},
        "safety": {
            "allow_send": False,
            "allow_delete": False,
            "allow_move": False,
        },
    }


def test_register_without_configuration_is_disabled() -> None:
    context = FakeHermesContext()

    plugin = register(context)
    status = plugin.get_runtime_status()

    assert status.version == hermes_email.__version__
    assert status.state is EmailRuntimeState.DISABLED
    assert status.provider is None
    assert status.profile == "ef-sinn-mail"
    assert status.read_enabled is False
    assert status.draft_enabled is False
    assert status.send_enabled is False
    assert status.diagnostic is None
    assert plugin.provider is None
    assert len(context.skills) == 1


def test_register_reads_only_official_plugin_setting_sections() -> None:
    context = FakeHermesContext()

    register(context)

    assert context.config_reads == ["email", "hermes", "behavior", "safety"]


def test_valid_mock_configuration_is_ready() -> None:
    plugin = register(FakeHermesContext(mock_settings()))

    status = plugin.get_runtime_status()

    assert status.state is EmailRuntimeState.MOCK_READY
    assert status.provider == "mock"
    assert status.profile == "ef-sinn-mail"
    assert status.read_enabled is True
    assert status.draft_enabled is True
    assert status.send_enabled is False
    assert status.diagnostic is None
    assert isinstance(plugin.provider, MockEmailProvider)
    assert plugin.config.safety.allow_send is False
    assert plugin.config.safety.allow_delete is False
    assert plugin.config.safety.allow_move is False


def test_runtime_mock_provider_uses_existing_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    resolved_provider = MockEmailProvider()
    received_configs: list[EmailPluginConfig] = []

    def fake_resolver(config: EmailPluginConfig) -> MockEmailProvider:
        received_configs.append(config)
        return resolved_provider

    monkeypatch.setattr(plugin_module, "resolve_email_provider", fake_resolver)

    plugin = register(FakeHermesContext(mock_settings()))

    assert received_configs == [plugin.config]
    assert plugin.provider is resolved_provider
    assert plugin.get_runtime_status().state is EmailRuntimeState.MOCK_READY


def test_unsupported_provider_becomes_configuration_error() -> None:
    context = FakeHermesContext({"email": {"provider": "gmail"}})

    plugin = register(context)
    status = plugin.get_runtime_status()

    assert status.state is EmailRuntimeState.CONFIGURATION_ERROR
    assert status.provider is None
    assert status.diagnostic == "unsupported-provider"
    assert status.read_enabled is False
    assert status.draft_enabled is False
    assert status.send_enabled is False
    assert len(context.skills) == 1


def test_active_read_mode_without_provider_is_configuration_error() -> None:
    plugin = register(FakeHermesContext({"email": {"read_mode": "mock"}}))

    status = plugin.get_runtime_status()

    assert status.state is EmailRuntimeState.CONFIGURATION_ERROR
    assert status.provider is None
    assert status.diagnostic == "provider-not-configured"


def test_invalid_settings_become_configuration_error() -> None:
    context = FakeHermesContext({"email": {"read_mode": "network"}})

    plugin = register(context)
    status = plugin.get_runtime_status()

    assert status.state is EmailRuntimeState.CONFIGURATION_ERROR
    assert status.provider is None
    assert status.diagnostic == "invalid-plugin-settings"
    assert len(context.skills) == 1


def test_unexpected_resolver_failure_is_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken_resolver(config: EmailPluginConfig):
        del config
        raise RuntimeError("programming failure")

    monkeypatch.setattr(plugin_module, "resolve_email_provider", broken_resolver)

    with pytest.raises(RuntimeError, match="programming failure"):
        register(FakeHermesContext(mock_settings()))


def test_status_contains_no_secrets_or_message_content() -> None:
    secret = "super-secret-token-value"
    context = FakeHermesContext(
        {"email": {"provider": "gmail", "credential": secret}},
        profile_name="safe-profile",
    )

    status = register(context).get_runtime_status()
    serialized_status = repr(asdict(status))

    assert status.state is EmailRuntimeState.CONFIGURATION_ERROR
    assert secret not in serialized_status
    assert "credential" not in serialized_status
    assert "body_text" not in serialized_status
    assert "subject" not in serialized_status
    assert set(asdict(status)) == {
        "version",
        "state",
        "provider",
        "profile",
        "read_enabled",
        "draft_enabled",
        "send_enabled",
        "diagnostic",
    }


def test_runtime_initialization_and_status_use_no_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def block_socket(*args, **kwargs):
        raise AssertionError(f"unexpected network access: {args!r} {kwargs!r}")

    monkeypatch.setattr(socket, "socket", block_socket)

    plugin = register(FakeHermesContext(mock_settings()))
    assert plugin.get_runtime_status().state is EmailRuntimeState.MOCK_READY


def test_runtime_lifecycle_cleanup_still_releases_context() -> None:
    context = FakeHermesContext(mock_settings())
    plugin = register(context)

    assert len(context.unload_callbacks) == 1
    context.unload_callbacks[0]()

    assert plugin.context_source is None
    assert plugin.get_runtime_status().profile is None
