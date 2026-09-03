import builtins
import socket

import pytest

from hermes_email.config import EmailPluginConfig
from hermes_email.providers import (
    EmailProvider,
    ImapReadOnlyProvider,
    MockEmailProvider,
    ProviderNotConfiguredError,
    UnsupportedEmailProviderError,
    resolve_email_provider,
)


def config_with_provider(provider: str | None) -> EmailPluginConfig:
    return EmailPluginConfig.from_mapping({"email": {"provider": provider}})


def test_mock_resolves_to_email_provider() -> None:
    provider = resolve_email_provider(config_with_provider("mock"))

    assert isinstance(provider, MockEmailProvider)
    assert isinstance(provider, EmailProvider)
    assert provider.name == "mock"


@pytest.mark.parametrize("configured_name", ["mock", "MOCK", "Mock", "  mOcK  "])
def test_mock_name_is_normalized(configured_name: str) -> None:
    assert isinstance(
        resolve_email_provider(config_with_provider(configured_name)),
        MockEmailProvider,
    )


def test_imap_resolves_without_secret_or_network_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hermes_email.secrets import EnvironmentSecretResolver

    def forbidden(*args, **kwargs):
        raise AssertionError(f"unexpected external access: {args!r} {kwargs!r}")

    monkeypatch.setattr(EnvironmentSecretResolver, "get_secret", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    config = EmailPluginConfig.from_mapping(
        {
            "email": {"provider": "imap", "read_mode": "readonly"},
            "imap": {
                "host": "mail.example.invalid",
                "username_ref": "HERMES_EMAIL_IMAP_USERNAME",
                "password_ref": "HERMES_EMAIL_IMAP_PASSWORD",
            },
        }
    )

    provider = resolve_email_provider(config)

    assert isinstance(provider, ImapReadOnlyProvider)
    assert provider.name == "imap"


@pytest.mark.parametrize(
    "configured_name",
    ["gmail", "proton", "outlook", "unknown"],
)
def test_unknown_provider_is_blocked(configured_name: str) -> None:
    with pytest.raises(
        UnsupportedEmailProviderError,
        match=f"unsupported email provider: {configured_name!r}",
    ):
        resolve_email_provider(config_with_provider(configured_name))


@pytest.mark.parametrize("configured_name", [None, "", " ", "\t\n"])
def test_missing_provider_is_not_replaced_with_mock(configured_name: str | None) -> None:
    with pytest.raises(ProviderNotConfiguredError, match="no email provider configured"):
        resolve_email_provider(config_with_provider(configured_name))


@pytest.mark.parametrize("configured_name", ["../synthetic-provider", "module.Class"])
def test_suspicious_names_are_not_imported(
    configured_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import_attempted = False

    def block_import(*args, **kwargs):
        nonlocal import_attempted
        import_attempted = True
        raise AssertionError(f"unexpected dynamic import: {args!r} {kwargs!r}")

    monkeypatch.setattr(builtins, "__import__", block_import)

    with pytest.raises(UnsupportedEmailProviderError):
        resolve_email_provider(config_with_provider(configured_name))

    assert import_attempted is False


def test_resolver_has_no_network_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    def block_socket(*args, **kwargs):
        raise AssertionError(f"unexpected network access: {args!r} {kwargs!r}")

    monkeypatch.setattr(socket, "socket", block_socket)

    assert isinstance(
        resolve_email_provider(config_with_provider("mock")),
        MockEmailProvider,
    )
