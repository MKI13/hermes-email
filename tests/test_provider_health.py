import asyncio
from dataclasses import asdict

import pytest

from hermes_email.config import EmailPluginConfig
from hermes_email.models import EmailDraft, EmailMessage, EmailMessagePage
from hermes_email.plugin import (
    EmailPlugin,
    EmailProviderUnavailableError,
    EmailReadDisabledError,
    EmailRuntimeState,
)
from hermes_email.providers import (
    EmailProvider,
    ProviderAuthenticationError,
    ProviderCapabilities,
    ProviderConnectionError,
    ProviderMailboxError,
    ProviderProtocolError,
    ProviderTimeoutError,
    ProviderTlsError,
)


class HealthProvider(EmailProvider):
    capabilities = ProviderCapabilities(fetch=True, get=True)

    def __init__(self, health_error: Exception | None = None) -> None:
        self.health_error = health_error
        self.health_calls = 0
        self.fetch_error: Exception | None = None
        self.fetch_calls = 0
        self.closed = False

    @property
    def name(self) -> str:
        return "imap"

    async def check_health(self) -> None:
        self.health_calls += 1
        if self.health_error is not None:
            raise self.health_error

    async def fetch_messages(
        self, *, limit: int = 50, cursor: str | None = None
    ) -> EmailMessagePage:
        del limit, cursor
        self.fetch_calls += 1
        if self.fetch_error is not None:
            raise self.fetch_error
        return EmailMessagePage(messages=())

    async def get_message(self, message_id: str) -> EmailMessage | None:
        del message_id
        return None

    async def create_draft(self, draft: EmailDraft) -> EmailDraft:
        return draft

    async def send_message(self, draft_id: str) -> None:
        del draft_id

    def close(self) -> None:
        self.closed = True


def readonly_config(*, enabled: bool = True) -> EmailPluginConfig:
    return EmailPluginConfig.from_mapping(
        {
            "email": {
                "provider": "imap",
                "read_mode": "readonly" if enabled else "disabled",
            },
            "imap": {
                "host": "mail.example.invalid",
                "username_ref": "HERMES_EMAIL_IMAP_USERNAME",
                "password_ref": "HERMES_EMAIL_IMAP_PASSWORD",
            },
        }
    )


def test_real_provider_starts_configured_without_claiming_health() -> None:
    provider = HealthProvider()
    plugin = EmailPlugin(readonly_config(), provider=provider)

    status = plugin.get_runtime_status()

    assert status.state is EmailRuntimeState.PROVIDER_CONFIGURED
    assert status.provider == "imap"
    assert status.read_enabled is False
    assert status.draft_enabled is False
    assert status.send_enabled is False
    assert provider.health_calls == 0


def test_explicit_health_success_marks_provider_ready() -> None:
    provider = HealthProvider()
    plugin = EmailPlugin(readonly_config(), provider=provider)

    status = asyncio.run(plugin.check_provider_health())

    assert provider.health_calls == 1
    assert status.state is EmailRuntimeState.PROVIDER_READY
    assert status.read_enabled is True
    assert status.diagnostic is None


@pytest.mark.parametrize(
    ("error", "state", "diagnostic"),
    [
        (
            ProviderAuthenticationError("SYNTHETIC PRIVATE DETAIL"),
            EmailRuntimeState.AUTHENTICATION_ERROR,
            "authentication-failed",
        ),
        (
            ProviderTlsError("SYNTHETIC PRIVATE DETAIL"),
            EmailRuntimeState.PROVIDER_UNREACHABLE,
            "tls-failed",
        ),
        (
            ProviderTimeoutError("SYNTHETIC PRIVATE DETAIL"),
            EmailRuntimeState.PROVIDER_UNREACHABLE,
            "provider-timeout",
        ),
        (
            ProviderConnectionError("SYNTHETIC PRIVATE DETAIL"),
            EmailRuntimeState.PROVIDER_UNREACHABLE,
            "connection-failed",
        ),
        (
            ProviderMailboxError("SYNTHETIC PRIVATE DETAIL"),
            EmailRuntimeState.PROVIDER_UNREACHABLE,
            "mailbox-unavailable",
        ),
        (
            ProviderProtocolError("SYNTHETIC PRIVATE DETAIL"),
            EmailRuntimeState.PROVIDER_UNREACHABLE,
            "protocol-error",
        ),
    ],
)
def test_health_failures_become_fixed_redacted_status(
    error: Exception,
    state: EmailRuntimeState,
    diagnostic: str,
) -> None:
    provider = HealthProvider(error)
    plugin = EmailPlugin(readonly_config(), provider=provider)

    status = asyncio.run(plugin.check_provider_health())
    serialized = repr(asdict(status))

    assert status.state is state
    assert status.diagnostic == diagnostic
    assert status.read_enabled is False
    assert "SYNTHETIC PRIVATE DETAIL" not in serialized


def test_disabled_provider_refuses_health_without_provider_call() -> None:
    provider = HealthProvider()
    plugin = EmailPlugin(readonly_config(enabled=False), provider=provider)

    with pytest.raises(EmailReadDisabledError):
        asyncio.run(plugin.check_provider_health())

    assert provider.health_calls == 0


def test_successful_read_updates_provider_health_state() -> None:
    provider = HealthProvider()
    plugin = EmailPlugin(readonly_config(), provider=provider)

    asyncio.run(plugin.fetch_messages(limit=1))

    assert provider.fetch_calls == 1
    assert plugin.get_runtime_status().state is EmailRuntimeState.PROVIDER_READY


def test_health_completion_after_close_cannot_restore_ready_state() -> None:
    class BlockingHealthProvider(HealthProvider):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def check_health(self) -> None:
            self.health_calls += 1
            self.started.set()
            await self.release.wait()

    async def exercise() -> None:
        provider = BlockingHealthProvider()
        plugin = EmailPlugin(readonly_config(), provider=provider)
        task = asyncio.create_task(plugin.check_provider_health())
        await provider.started.wait()
        plugin.close()
        provider.release.set()
        await task

        status = plugin.get_runtime_status()
        assert provider.closed is True
        assert plugin.provider is None
        assert status.state is EmailRuntimeState.DISABLED
        assert status.read_enabled is False

    asyncio.run(exercise())


def test_read_completion_after_close_does_not_return_mail() -> None:
    class BlockingReadProvider(HealthProvider):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def fetch_messages(
            self, *, limit: int = 50, cursor: str | None = None
        ) -> EmailMessagePage:
            del limit, cursor
            self.started.set()
            await self.release.wait()
            return EmailMessagePage(messages=())

    async def exercise() -> None:
        provider = BlockingReadProvider()
        plugin = EmailPlugin(readonly_config(), provider=provider)
        task = asyncio.create_task(plugin.fetch_messages(limit=1))
        await provider.started.wait()
        plugin.close()
        provider.release.set()
        with pytest.raises(EmailProviderUnavailableError, match="closed"):
            await task
        assert plugin.get_runtime_status().state is EmailRuntimeState.DISABLED

    asyncio.run(exercise())


def test_failed_read_updates_status_and_propagates_error() -> None:
    provider = HealthProvider()
    provider.fetch_error = ProviderTimeoutError("SYNTHETIC PRIVATE DETAIL")
    plugin = EmailPlugin(readonly_config(), provider=provider)

    with pytest.raises(ProviderTimeoutError, match="SYNTHETIC PRIVATE DETAIL"):
        asyncio.run(plugin.fetch_messages(limit=1))

    status = plugin.get_runtime_status()
    assert status.state is EmailRuntimeState.PROVIDER_UNREACHABLE
    assert status.diagnostic == "provider-timeout"
    assert "SYNTHETIC PRIVATE DETAIL" not in repr(asdict(status))
