import asyncio
import inspect
from collections.abc import Sequence

import pytest

from hermes_email.config import EmailPluginConfig
from hermes_email.context import HermesContext
from hermes_email.models import EmailAddress, EmailDraft, EmailMessage, EmailMessagePage
from hermes_email.plugin import (
    MAX_FETCH_LIMIT,
    EmailFetchCursorError,
    EmailFetchLimitError,
    EmailPlugin,
)
from hermes_email.providers import (
    EmailProvider,
    MockCursorError,
    MockEmailProvider,
    ProviderCapabilities,
)


def test_message_page_is_an_immutable_sequence_with_an_opaque_cursor() -> None:
    page = EmailMessagePage(messages=(), next_cursor="  provider cursor  ")

    assert isinstance(page, Sequence)
    assert page.messages == ()
    assert page.next_cursor == "  provider cursor  "
    assert len(page) == 0
    assert list(page) == []

    with pytest.raises((AttributeError, TypeError)):
        page.next_cursor = None  # type: ignore[misc]


def test_message_page_copies_a_mutable_message_container_to_a_tuple() -> None:
    source: list[EmailMessage] = []

    page = EmailMessagePage(messages=source)  # type: ignore[arg-type]
    source.append(
        EmailMessage(
            message_id="added-later",
            subject="Not part of the page",
            sender=EmailAddress("sender@example.invalid"),
            recipients=(),
        )
    )

    assert page.messages == ()
    assert len(page) == 0


@pytest.mark.parametrize("next_cursor", ["", "   ", True, 1, 1.5, [], {}])
def test_message_page_rejects_invalid_next_cursor(next_cursor: object) -> None:
    with pytest.raises(ValueError, match="next_cursor"):
        EmailMessagePage(messages=(), next_cursor=next_cursor)  # type: ignore[arg-type]


def test_provider_fetch_contract_accepts_an_opaque_cursor_and_returns_a_page() -> None:
    signature = inspect.signature(EmailProvider.fetch_messages)

    assert signature.parameters["cursor"].default is None
    assert signature.return_annotation == "EmailMessagePage"


def test_mock_provider_paginates_one_deterministic_page_per_call() -> None:
    async def exercise() -> None:
        provider = MockEmailProvider()

        first = await provider.fetch_messages(limit=1)
        assert [message.message_id for message in first.messages] == [
            "mock-message-customer-001"
        ]
        assert first.next_cursor is not None

        repeated = await provider.fetch_messages(limit=1, cursor=first.next_cursor)
        repeated_again = await provider.fetch_messages(limit=1, cursor=first.next_cursor)
        assert repeated == repeated_again
        assert [message.message_id for message in repeated.messages] == [
            "mock-message-empty-002"
        ]
        assert repeated.next_cursor is not None

        last = await provider.fetch_messages(limit=1, cursor=repeated.next_cursor)
        assert [message.message_id for message in last.messages] == [
            "mock-message-html-003"
        ]
        assert last.next_cursor is None

    asyncio.run(exercise())


class RecordingPageProvider(EmailProvider):
    capabilities = ProviderCapabilities(fetch=True)

    def __init__(self, result: EmailMessagePage | None = None) -> None:
        self.result = (
            result if result is not None else EmailMessagePage(messages=(), next_cursor=None)
        )
        self.fetch_calls: list[tuple[int, str | None]] = []
        self.write_calls: list[str] = []

    @property
    def name(self) -> str:
        return "recording-page"

    async def fetch_messages(
        self, *, limit: int = 50, cursor: str | None = None
    ) -> EmailMessagePage:
        self.fetch_calls.append((limit, cursor))
        return self.result

    async def get_message(self, message_id: str) -> EmailMessage | None:
        return None

    async def create_draft(self, draft: EmailDraft) -> EmailDraft:
        self.write_calls.append("draft")
        return draft

    async def send_message(self, draft_id: str) -> None:
        self.write_calls.append("send")


class ProviderPageError(RuntimeError):
    pass


class FailingPageProvider(RecordingPageProvider):
    async def fetch_messages(
        self, *, limit: int = 50, cursor: str | None = None
    ) -> EmailMessagePage:
        self.fetch_calls.append((limit, cursor))
        raise ProviderPageError("provider page failed")


class FixedContextSource:
    def __init__(self, context: HermesContext) -> None:
        self.context = context

    def get_context(self) -> HermesContext:
        return self.context


def plugin_config(*, read_mode: str = "mock") -> EmailPluginConfig:
    return EmailPluginConfig.from_mapping(
        {"email": {"provider": "mock", "read_mode": read_mode}}
    )


def test_plugin_forwards_cursor_unchanged_and_returns_the_provider_page() -> None:
    async def exercise() -> None:
        provider_page = EmailMessagePage(messages=(), next_cursor="next-provider-page")
        provider = RecordingPageProvider(provider_page)
        plugin = EmailPlugin(plugin_config(), provider=provider)

        page = await plugin.fetch_messages(limit=1, cursor="  opaque cursor  ")

        assert page is provider_page
        assert provider.fetch_calls == [(1, "  opaque cursor  ")]
        assert provider.write_calls == []

    asyncio.run(exercise())


def test_plugin_default_is_one_bounded_page_with_none_cursor() -> None:
    async def exercise() -> None:
        provider = RecordingPageProvider(
            EmailMessagePage(messages=(), next_cursor="another-page")
        )
        plugin = EmailPlugin(plugin_config(), provider=provider)

        await plugin.fetch_messages()

        assert provider.fetch_calls == [(50, None)]

    asyncio.run(exercise())


@pytest.mark.parametrize("limit", [1, 100])
def test_plugin_accepts_fetch_limit_boundaries(limit: int) -> None:
    async def exercise() -> None:
        provider = RecordingPageProvider()
        plugin = EmailPlugin(plugin_config(), provider=provider)

        await plugin.fetch_messages(limit=limit)

        assert provider.fetch_calls == [(limit, None)]

    asyncio.run(exercise())


def test_max_fetch_limit_is_fixed_at_100() -> None:
    assert MAX_FETCH_LIMIT == 100


@pytest.mark.parametrize("limit", [101, 0, -1, True, None, 1.5, "1", [], {}])
def test_plugin_rejects_invalid_or_oversized_limits_before_provider_call(
    limit: object,
) -> None:
    async def exercise() -> None:
        provider = RecordingPageProvider()
        plugin = EmailPlugin(plugin_config(), provider=provider)

        with pytest.raises(EmailFetchLimitError):
            await plugin.fetch_messages(limit=limit)  # type: ignore[arg-type]

        assert provider.fetch_calls == []
        assert provider.write_calls == []

    asyncio.run(exercise())


def test_plugin_allows_none_cursor() -> None:
    async def exercise() -> None:
        provider = RecordingPageProvider()
        plugin = EmailPlugin(plugin_config(), provider=provider)

        await plugin.fetch_messages(limit=1, cursor=None)

        assert provider.fetch_calls == [(1, None)]

    asyncio.run(exercise())


@pytest.mark.parametrize("cursor", ["", "   ", True, 1, 1.5, [], {}])
def test_plugin_rejects_invalid_cursors_before_provider_call(cursor: object) -> None:
    async def exercise() -> None:
        provider = RecordingPageProvider()
        plugin = EmailPlugin(plugin_config(), provider=provider)

        with pytest.raises(EmailFetchCursorError, match="cursor"):
            await plugin.fetch_messages(limit=1, cursor=cursor)  # type: ignore[arg-type]

        assert provider.fetch_calls == []
        assert provider.write_calls == []

    asyncio.run(exercise())


def test_unknown_mock_cursor_is_propagated_without_retry() -> None:
    async def exercise() -> None:
        provider = MockEmailProvider()
        plugin = EmailPlugin(plugin_config(), provider=provider)

        with pytest.raises(MockCursorError, match="unknown mock"):
            await plugin.fetch_messages(limit=1, cursor="not-a-mock-cursor")

    asyncio.run(exercise())


def test_provider_error_is_propagated_after_exactly_one_fetch() -> None:
    async def exercise() -> None:
        provider = FailingPageProvider()
        plugin = EmailPlugin(plugin_config(), provider=provider)

        with pytest.raises(ProviderPageError, match="provider page failed"):
            await plugin.fetch_messages(limit=2, cursor="opaque")

        assert provider.fetch_calls == [(2, "opaque")]
        assert provider.write_calls == []

    asyncio.run(exercise())


def test_paginated_fetch_does_not_change_runtime_context() -> None:
    async def exercise() -> None:
        context = HermesContext(profile_name="active-profile")
        provider = RecordingPageProvider()
        plugin = EmailPlugin(
            plugin_config(),
            context_source=FixedContextSource(context),
            provider=provider,
        )

        await plugin.fetch_messages(limit=1, cursor=None)

        assert plugin.get_hermes_context() is context
        assert plugin.get_hermes_context().persona is None
        assert provider.write_calls == []

    asyncio.run(exercise())
