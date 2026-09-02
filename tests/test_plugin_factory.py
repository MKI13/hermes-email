import socket

import pytest

from hermes_email.config import EmailPluginConfig
from hermes_email.plugin import EmailPlugin
from hermes_email.providers import (
    MockEmailProvider,
    ProviderNotConfiguredError,
    UnsupportedEmailProviderError,
)


def config_with_provider(provider: str | None) -> EmailPluginConfig:
    return EmailPluginConfig.from_mapping({"email": {"provider": provider}})


def test_from_config_exists() -> None:
    assert callable(EmailPlugin.from_config)


def test_from_config_creates_plugin_with_mock_provider() -> None:
    config = config_with_provider("mock")

    plugin = EmailPlugin.from_config(config)

    assert isinstance(plugin, EmailPlugin)
    assert isinstance(plugin.provider, MockEmailProvider)
    assert plugin.provider.capabilities.send is False


def test_from_config_uses_provider_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    import hermes_email.plugin as plugin_module

    config = config_with_provider("mock")
    resolved_provider = MockEmailProvider()
    received_configs: list[EmailPluginConfig] = []

    def fake_resolver(received: EmailPluginConfig) -> MockEmailProvider:
        received_configs.append(received)
        return resolved_provider

    monkeypatch.setattr(plugin_module, "resolve_email_provider", fake_resolver)

    plugin = EmailPlugin.from_config(config)

    assert received_configs == [config]
    assert plugin.provider is resolved_provider


def test_from_config_propagates_missing_provider_error() -> None:
    with pytest.raises(ProviderNotConfiguredError, match="no email provider configured"):
        EmailPlugin.from_config(config_with_provider(None))


def test_from_config_propagates_unsupported_provider_error() -> None:
    with pytest.raises(UnsupportedEmailProviderError, match="unsupported email provider"):
        EmailPlugin.from_config(config_with_provider("imap"))


def test_from_config_has_no_silent_mock_fallback() -> None:
    with pytest.raises(ProviderNotConfiguredError):
        EmailPlugin.from_config(EmailPluginConfig())


def test_from_config_preserves_safety_configuration() -> None:
    config = EmailPluginConfig.from_mapping(
        {
            "email": {"provider": "mock"},
            "safety": {
                "allow_send": False,
                "allow_delete": False,
                "allow_move": False,
            },
        }
    )

    plugin = EmailPlugin.from_config(config)

    assert plugin.config is config
    assert plugin.config.safety.allow_send is False
    assert plugin.config.safety.allow_delete is False
    assert plugin.config.safety.allow_move is False


def test_from_config_does_not_access_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def block_socket(*args, **kwargs):
        raise AssertionError(f"unexpected network access: {args!r} {kwargs!r}")

    monkeypatch.setattr(socket, "socket", block_socket)

    plugin = EmailPlugin.from_config(config_with_provider("mock"))
    assert isinstance(plugin.provider, MockEmailProvider)
