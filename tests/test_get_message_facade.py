import asyncio
from typing import Sequence

import pytest

from hermes_email.config import EmailPluginConfig
from hermes_email.context import HermesContext
from hermes_email.models import EmailAddress, EmailDraft, EmailMessage
from hermes_email.plugin import (
    EmailGetUnsupportedError,
    EmailMessageIdError,
    EmailPlugin,
    EmailProviderUnavailableError,
    EmailReadDisabledError,
)
from hermes_email.providers import EmailProvider, ProviderCapabilities


class RecordingProvider(EmailProvider):
    capabilities = ProviderCapabilities(fetch=False, get=True)

    def __init__(self, result: EmailMessage | None = None) -> None:
        self.result = result
        self.get_calls: list[str] = []
        self.other_calls: list[str] = []

    @property
    def name(self) -> str:
        return "recording"

    async def fetch_messages(self, *, limit: int = 50) -> Sequence[EmailMessage]:
        self.other_calls.append(f"fetch:{limit}")
        return ()

    async def get_message(self, message_id: str) -> EmailMessage | None:
        self.get_calls.append(message_id)
        return self.result

    async def create_draft(self, draft: EmailDraft) -> EmailDraft:
        self.other_calls.append("draft")
        return draft

    async def send_message(self, draft_id: str) -> None:
        self.other_calls.append(f"send:{draft_id}")


class NoGetProvider(RecordingProvider):
    capabilities = ProviderCapabilities(fetch=True, get=False)


class ProviderLookupError(RuntimeError):
    pass


class FailingProvider(RecordingProvider):
    async def get_message(self, message_id: str) -> EmailMessage | None:
        self.get_calls.append(message_id)
        raise ProviderLookupError("provider lookup failed")


class FixedContextSource:
    def __init__(self, context: HermesContext) -> None:
        self.context = context

    def get_context(self) -> HermesContext:
        return self.context


def plugin_config(*, read_mode: str) -> EmailPluginConfig:
    return EmailPluginConfig.from_mapping(
        {"email": {"provider": "mock", "read_mode": read_mode}}
    )


def sample_message() -> EmailMessage:
    return EmailMessage(
        message_id="provider-message-001",
        subject="Read-only sample",
        sender=EmailAddress("sender@example.invalid"),
        recipients=(EmailAddress("recipient@example.invalid"),),
        body_text="Local message body",
    )


def test_get_message_exists() -> None:
    assert callable(EmailPlugin.get_message)


def test_known_mock_id_returns_message() -> None:
    async def exercise() -> None:
        plugin = EmailPlugin.from_config(plugin_config(read_mode="mock"))

        message = await plugin.get_message("mock-message-customer-001")

        assert message is not None
        assert message.message_id == "mock-message-customer-001"

    asyncio.run(exercise())


def test_unknown_mock_id_returns_none() -> None:
    async def exercise() -> None:
        plugin = EmailPlugin.from_config(plugin_config(read_mode="mock"))

        assert await plugin.get_message("mock-message-unknown") is None

    asyncio.run(exercise())


def test_get_message_delegates_once_and_returns_result_unchanged() -> None:
    async def exercise() -> None:
        provider_result = sample_message()
        provider = RecordingProvider(provider_result)
        plugin = EmailPlugin(plugin_config(read_mode="mock"), provider=provider)

        result = await plugin.get_message("  provider-message-001  ")

        assert result is provider_result
        assert provider.get_calls == ["  provider-message-001  "]
        assert provider.other_calls == []

    asyncio.run(exercise())


def test_disabled_read_mode_blocks_before_provider_call() -> None:
    async def exercise() -> None:
        provider = RecordingProvider()
        plugin = EmailPlugin(plugin_config(read_mode="disabled"), provider=provider)

        with pytest.raises(EmailReadDisabledError, match="disabled"):
            await plugin.get_message("provider-message-001")

        assert provider.get_calls == []
        assert provider.other_calls == []

    asyncio.run(exercise())


def test_missing_provider_is_blocked() -> None:
    async def exercise() -> None:
        plugin = EmailPlugin(plugin_config(read_mode="mock"), provider=None)

        with pytest.raises(EmailProviderUnavailableError, match="no email provider"):
            await plugin.get_message("provider-message-001")

    asyncio.run(exercise())


def test_provider_without_get_capability_is_blocked() -> None:
    async def exercise() -> None:
        provider = NoGetProvider()
        plugin = EmailPlugin(plugin_config(read_mode="mock"), provider=provider)

        with pytest.raises(EmailGetUnsupportedError, match="does not support"):
            await plugin.get_message("provider-message-001")

        assert provider.get_calls == []
        assert provider.other_calls == []

    asyncio.run(exercise())


@pytest.mark.parametrize("message_id", ["", "   "])
def test_empty_message_id_is_rejected_without_provider_call(message_id: str) -> None:
    async def exercise() -> None:
        provider = RecordingProvider()
        plugin = EmailPlugin(plugin_config(read_mode="mock"), provider=provider)

        with pytest.raises(EmailMessageIdError, match="non-empty string"):
            await plugin.get_message(message_id)

        assert provider.get_calls == []
        assert provider.other_calls == []

    asyncio.run(exercise())


@pytest.mark.parametrize("message_id", [None, 123, 1.5, True, [], {}])
def test_non_string_message_id_is_rejected_without_provider_call(message_id: object) -> None:
    async def exercise() -> None:
        provider = RecordingProvider()
        plugin = EmailPlugin(plugin_config(read_mode="mock"), provider=provider)

        with pytest.raises(EmailMessageIdError, match="non-empty string"):
            await plugin.get_message(message_id)  # type: ignore[arg-type]

        assert provider.get_calls == []
        assert provider.other_calls == []

    asyncio.run(exercise())


def test_provider_lookup_error_is_propagated_unchanged() -> None:
    async def exercise() -> None:
        provider = FailingProvider()
        plugin = EmailPlugin(plugin_config(read_mode="mock"), provider=provider)

        with pytest.raises(ProviderLookupError, match="provider lookup failed"):
            await plugin.get_message("provider-message-001")

        assert provider.get_calls == ["provider-message-001"]
        assert provider.other_calls == []

    asyncio.run(exercise())


def test_get_message_does_not_change_runtime_context() -> None:
    async def exercise() -> None:
        inherited_context = HermesContext(profile_name="active-profile")
        provider = RecordingProvider()
        plugin = EmailPlugin(
            plugin_config(read_mode="mock"),
            context_source=FixedContextSource(inherited_context),
            provider=provider,
        )

        await plugin.get_message("provider-message-001")

        assert plugin.get_hermes_context() is inherited_context
        assert plugin.get_hermes_context().persona is None
        assert provider.get_calls == ["provider-message-001"]
        assert provider.other_calls == []

    asyncio.run(exercise())
