import asyncio
from typing import Sequence

import pytest

from hermes_email.config import EmailPluginConfig
from hermes_email.models import EmailAddress, EmailDraft, EmailMessage, EmailMessagePage
from hermes_email.plugin import (
    SEARCH_FETCH_LIMIT,
    SEARCH_QUERY_MAX_LENGTH,
    EmailFetchUnsupportedError,
    EmailPlugin,
    EmailProviderUnavailableError,
    EmailReadDisabledError,
    EmailSearchQueryError,
)
from hermes_email.providers import EmailProvider, ProviderCapabilities


class RecordingProvider(EmailProvider):
    capabilities = ProviderCapabilities(fetch=True)

    def __init__(self, messages: Sequence[EmailMessage] = ()) -> None:
        self.messages = messages
        self.fetch_calls: list[tuple[int, str | None]] = []
        self.other_calls: list[str] = []

    @property
    def name(self) -> str:
        return "recording"

    async def fetch_messages(
        self, *, limit: int = 50, cursor: str | None = None
    ) -> EmailMessagePage:
        self.fetch_calls.append((limit, cursor))
        return EmailMessagePage(messages=tuple(self.messages[:limit]))

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


def plugin_config(*, read_mode: str) -> EmailPluginConfig:
    return EmailPluginConfig.from_mapping(
        {"email": {"provider": "mock", "read_mode": read_mode}}
    )


def message(message_id: str, *, subject: str, sender: str, body: str | None) -> EmailMessage:
    return EmailMessage(
        message_id=message_id,
        subject=subject,
        sender=EmailAddress(sender),
        recipients=(EmailAddress("recipient@example.invalid"),),
        body_text=body,
    )


def test_search_messages_exists() -> None:
    assert callable(EmailPlugin.search_messages)


def test_search_matches_mock_subject() -> None:
    async def exercise() -> None:
        plugin = EmailPlugin.from_config(plugin_config(read_mode="mock"))

        results = await plugin.search_messages("sample service")

        assert [item.message_id for item in results] == ["mock-message-customer-001"]

    asyncio.run(exercise())


def test_search_matches_mock_sender() -> None:
    async def exercise() -> None:
        plugin = EmailPlugin.from_config(plugin_config(read_mode="mock"))

        results = await plugin.search_messages("customer@example.invalid")

        assert [item.message_id for item in results] == ["mock-message-customer-001"]

    asyncio.run(exercise())


def test_search_matches_mock_body() -> None:
    async def exercise() -> None:
        plugin = EmailPlugin.from_config(plugin_config(read_mode="mock"))

        results = await plugin.search_messages("team access")

        assert [item.message_id for item in results] == ["mock-message-customer-001"]

    asyncio.run(exercise())


def test_search_is_case_insensitive_and_trims_query() -> None:
    async def exercise() -> None:
        plugin = EmailPlugin.from_config(plugin_config(read_mode="mock"))

        results = await plugin.search_messages("  QUESTION ABOUT  ")

        assert [item.message_id for item in results] == ["mock-message-customer-001"]

    asyncio.run(exercise())


def test_multiple_matches_preserve_provider_order() -> None:
    async def exercise() -> None:
        messages = (
            message("first", subject="Status update", sender="one@example.invalid", body="Alpha"),
            message("second", subject="Other", sender="two@example.invalid", body="status pending"),
            message("third", subject="STATUS complete", sender="three@example.invalid", body=None),
        )
        provider = RecordingProvider(messages)
        plugin = EmailPlugin(plugin_config(read_mode="mock"), provider=provider)

        results = await plugin.search_messages("status")

        assert [item.message_id for item in results] == ["first", "second", "third"]
        assert provider.fetch_calls == [(SEARCH_FETCH_LIMIT, None)]
        assert provider.other_calls == []

    asyncio.run(exercise())


def test_no_matches_returns_empty_sequence() -> None:
    async def exercise() -> None:
        plugin = EmailPlugin.from_config(plugin_config(read_mode="mock"))

        page = await plugin.search_messages("definitely absent text")

        assert page == EmailMessagePage(messages=(), next_cursor=None)

    asyncio.run(exercise())


@pytest.mark.parametrize("query", ["", "   "])
def test_empty_query_is_rejected_before_fetch(query: str) -> None:
    async def exercise() -> None:
        provider = RecordingProvider()
        plugin = EmailPlugin(plugin_config(read_mode="mock"), provider=provider)

        with pytest.raises(EmailSearchQueryError, match="non-empty string"):
            await plugin.search_messages(query)

        assert provider.fetch_calls == []
        assert provider.other_calls == []

    asyncio.run(exercise())


@pytest.mark.parametrize("query", [None, 123, True, [], {}])
def test_non_string_query_is_rejected_before_fetch(query: object) -> None:
    async def exercise() -> None:
        provider = RecordingProvider()
        plugin = EmailPlugin(plugin_config(read_mode="mock"), provider=provider)

        with pytest.raises(EmailSearchQueryError, match="non-empty string"):
            await plugin.search_messages(query)  # type: ignore[arg-type]

        assert provider.fetch_calls == []
        assert provider.other_calls == []

    asyncio.run(exercise())


def test_oversized_query_is_rejected_before_fetch() -> None:
    async def exercise() -> None:
        provider = RecordingProvider()
        plugin = EmailPlugin(plugin_config(read_mode="mock"), provider=provider)

        with pytest.raises(EmailSearchQueryError, match="must not exceed"):
            await plugin.search_messages("x" * (SEARCH_QUERY_MAX_LENGTH + 1))

        assert provider.fetch_calls == []
        assert provider.other_calls == []

    asyncio.run(exercise())


def test_disabled_read_mode_blocks_before_fetch() -> None:
    async def exercise() -> None:
        provider = RecordingProvider()
        plugin = EmailPlugin(plugin_config(read_mode="disabled"), provider=provider)

        with pytest.raises(EmailReadDisabledError, match="disabled"):
            await plugin.search_messages("status")

        assert provider.fetch_calls == []
        assert provider.other_calls == []

    asyncio.run(exercise())


def test_missing_provider_is_blocked() -> None:
    async def exercise() -> None:
        plugin = EmailPlugin(plugin_config(read_mode="mock"), provider=None)

        with pytest.raises(EmailProviderUnavailableError, match="no email provider"):
            await plugin.search_messages("status")

    asyncio.run(exercise())


def test_provider_without_fetch_capability_is_blocked() -> None:
    async def exercise() -> None:
        provider = NoFetchProvider()
        plugin = EmailPlugin(plugin_config(read_mode="mock"), provider=provider)

        with pytest.raises(EmailFetchUnsupportedError, match="does not support"):
            await plugin.search_messages("status")

        assert provider.fetch_calls == []
        assert provider.other_calls == []

    asyncio.run(exercise())
