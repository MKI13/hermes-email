import asyncio
from typing import Sequence

import pytest

from hermes_email.config import EmailPluginConfig
from hermes_email.context import HermesContext
from hermes_email.models import EmailDraft, EmailMessage
from hermes_email.plugin import (
    EmailFetchLimitError,
    EmailFetchUnsupportedError,
    EmailPlugin,
    EmailProviderUnavailableError,
    EmailReadDisabledError,
)
from hermes_email.providers import EmailProvider, ProviderCapabilities


class RecordingProvider(EmailProvider):
    capabilities = ProviderCapabilities(fetch=True)

    def __init__(self, result: Sequence[EmailMessage] = ()) -> None:
        self.result = result
        self.fetch_limits: list[int] = []
        self.other_calls: list[str] = []

    @property
    def name(self) -> str:
        return "recording"

    async def fetch_messages(self, *, limit: int = 50) -> Sequence[EmailMessage]:
        self.fetch_limits.append(limit)
        return self.result

    async def get_message(self, message_id: str) -> EmailMessage | None:
        self.other_calls.append(f"get:{message_id}")
        return None

    async def create_draft(self, draft: EmailDraft) -> EmailDraft:
        self.other_calls.append("draft")
        return draft

    async def send_message(self, draft_id: str) -> None:
        self.other_calls.append(f"send:{draft_id}")


class NoFetchProvider(RecordingProvider):
    capabilities = ProviderCapabilities(fetch=False)


class ProviderFetchError(RuntimeError):
    pass


class FailingProvider(RecordingProvider):
    async def fetch_messages(self, *, limit: int = 50) -> Sequence[EmailMessage]:
        self.fetch_limits.append(limit)
        raise ProviderFetchError("provider fetch failed")


class FixedContextSource:
    def __init__(self, context: HermesContext) -> None:
        self.context = context

    def get_context(self) -> HermesContext:
        return self.context


def plugin_config(*, read_mode: str) -> EmailPluginConfig:
    return EmailPluginConfig.from_mapping(
        {"email": {"provider": "mock", "read_mode": read_mode}}
    )


def test_fetch_messages_exists() -> None:
    assert callable(EmailPlugin.fetch_messages)


def test_mock_read_mode_delegates_and_preserves_provider_result() -> None:
    async def exercise() -> None:
        provider_result: tuple[EmailMessage, ...] = ()
        provider = RecordingProvider(provider_result)
        plugin = EmailPlugin(plugin_config(read_mode="mock"), provider=provider)

        result = await plugin.fetch_messages(limit=10)

        assert result is provider_result
        assert provider.fetch_limits == [10]
        assert provider.other_calls == []

    asyncio.run(exercise())


def test_from_config_fetches_only_the_requested_mock_page() -> None:
    async def exercise() -> None:
        plugin = EmailPlugin.from_config(plugin_config(read_mode="mock"))

        messages = await plugin.fetch_messages(limit=2)

        assert len(messages) == 2
        assert [message.message_id for message in messages] == [
            "mock-message-customer-001",
            "mock-message-empty-002",
        ]

    asyncio.run(exercise())


def test_default_fetch_limit_is_finite_and_forwarded() -> None:
    async def exercise() -> None:
        provider = RecordingProvider()
        plugin = EmailPlugin(plugin_config(read_mode="mock"), provider=provider)

        await plugin.fetch_messages()

        assert provider.fetch_limits == [50]
        assert provider.other_calls == []

    asyncio.run(exercise())


def test_disabled_read_mode_blocks_before_provider_access() -> None:
    async def exercise() -> None:
        provider = RecordingProvider()
        plugin = EmailPlugin(plugin_config(read_mode="disabled"), provider=provider)

        with pytest.raises(EmailReadDisabledError, match="disabled"):
            await plugin.fetch_messages(limit=10)

        assert provider.fetch_limits == []
        assert provider.other_calls == []

    asyncio.run(exercise())


def test_missing_provider_is_blocked() -> None:
    async def exercise() -> None:
        plugin = EmailPlugin(plugin_config(read_mode="mock"), provider=None)

        with pytest.raises(EmailProviderUnavailableError, match="no email provider"):
            await plugin.fetch_messages(limit=10)

    asyncio.run(exercise())


def test_provider_without_fetch_capability_is_blocked() -> None:
    async def exercise() -> None:
        provider = NoFetchProvider()
        plugin = EmailPlugin(plugin_config(read_mode="mock"), provider=provider)

        with pytest.raises(EmailFetchUnsupportedError, match="does not support"):
            await plugin.fetch_messages(limit=10)

        assert provider.fetch_limits == []
        assert provider.other_calls == []

    asyncio.run(exercise())


def test_provider_fetch_error_is_propagated_unchanged() -> None:
    async def exercise() -> None:
        provider = FailingProvider()
        plugin = EmailPlugin(plugin_config(read_mode="mock"), provider=provider)

        with pytest.raises(ProviderFetchError, match="provider fetch failed"):
            await plugin.fetch_messages(limit=7)

        assert provider.fetch_limits == [7]
        assert provider.other_calls == []

    asyncio.run(exercise())


def test_fetch_does_not_change_runtime_context() -> None:
    async def exercise() -> None:
        inherited_context = HermesContext(profile_name="active-profile")
        provider = RecordingProvider()
        plugin = EmailPlugin(
            plugin_config(read_mode="mock"),
            context_source=FixedContextSource(inherited_context),
            provider=provider,
        )

        await plugin.fetch_messages(limit=1)

        assert plugin.get_hermes_context() is inherited_context
        assert plugin.get_hermes_context().persona is None
        assert provider.other_calls == []

    asyncio.run(exercise())


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, None])
def test_invalid_fetch_limit_is_blocked_without_provider_access(limit: object) -> None:
    async def exercise() -> None:
        provider = RecordingProvider()
        plugin = EmailPlugin(plugin_config(read_mode="mock"), provider=provider)

        with pytest.raises(EmailFetchLimitError, match="positive integer"):
            await plugin.fetch_messages(limit=limit)  # type: ignore[arg-type]

        assert provider.fetch_limits == []
        assert provider.other_calls == []

    asyncio.run(exercise())
