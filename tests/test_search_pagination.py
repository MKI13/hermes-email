import asyncio
import inspect

import pytest

from hermes_email.config import EmailPluginConfig
from hermes_email.context import HermesContext
from hermes_email.models import EmailAddress, EmailDraft, EmailMessage, EmailMessagePage
from hermes_email.plugin import (
    MAX_FETCH_LIMIT,
    SEARCH_QUERY_MAX_LENGTH,
    EmailFetchCursorError,
    EmailFetchLimitError,
    EmailFetchUnsupportedError,
    EmailPlugin,
    EmailProviderUnavailableError,
    EmailReadDisabledError,
    EmailSearchQueryError,
)
from hermes_email.providers import (
    EmailProvider,
    MockCursorError,
    MockEmailProvider,
    ProviderCapabilities,
)


class RecordingSearchPageProvider(EmailProvider):
    capabilities = ProviderCapabilities(fetch=True)

    def __init__(self, page: EmailMessagePage) -> None:
        self.page = page
        self.fetch_calls: list[tuple[int, str | None]] = []
        self.write_calls: list[str] = []

    @property
    def name(self) -> str:
        return "recording-search-page"

    async def fetch_messages(
        self, *, limit: int = 50, cursor: str | None = None
    ) -> EmailMessagePage:
        self.fetch_calls.append((limit, cursor))
        return self.page

    async def get_message(self, message_id: str) -> EmailMessage | None:
        return None

    async def create_draft(self, draft: EmailDraft) -> EmailDraft:
        self.write_calls.append("draft")
        return draft

    async def send_message(self, draft_id: str) -> None:
        self.write_calls.append("send")


class SearchProviderError(RuntimeError):
    pass


class FailingSearchPageProvider(RecordingSearchPageProvider):
    async def fetch_messages(
        self, *, limit: int = 50, cursor: str | None = None
    ) -> EmailMessagePage:
        self.fetch_calls.append((limit, cursor))
        raise SearchProviderError("search page failed")


class NoFetchSearchProvider(RecordingSearchPageProvider):
    capabilities = ProviderCapabilities(fetch=False)


class FixedContextSource:
    def __init__(self, context: HermesContext) -> None:
        self.context = context

    def get_context(self) -> HermesContext:
        return self.context


def plugin_config(*, read_mode: str = "mock") -> EmailPluginConfig:
    return EmailPluginConfig.from_mapping(
        {"email": {"provider": "mock", "read_mode": read_mode}}
    )


def message(message_id: str, *, subject: str, body: str = "") -> EmailMessage:
    return EmailMessage(
        message_id=message_id,
        subject=subject,
        sender=EmailAddress("sender@example.invalid"),
        recipients=(EmailAddress("recipient@example.invalid"),),
        body_text=body,
    )


def test_search_filters_one_provider_page_and_preserves_its_cursor() -> None:
    async def exercise() -> None:
        provider = RecordingSearchPageProvider(
            EmailMessagePage(
                messages=(
                    message("match", subject="Needle update"),
                    message("miss", subject="Other"),
                ),
                next_cursor="provider-next-page",
            )
        )
        plugin = EmailPlugin(plugin_config(), provider=provider)

        cursor = "".join(("  opaque", " cursor  "))

        page = await plugin.search_messages("needle", limit=1, cursor=cursor)

        assert isinstance(page, EmailMessagePage)
        assert [item.message_id for item in page.messages] == ["match"]
        assert page.next_cursor == "provider-next-page"
        assert provider.fetch_calls == [(1, cursor)]
        assert provider.fetch_calls[0][1] is cursor
        assert provider.write_calls == []

    asyncio.run(exercise())


def test_search_signature_has_bounded_pagination_defaults() -> None:
    signature = inspect.signature(EmailPlugin.search_messages)

    assert signature.parameters["limit"].default == 50
    assert signature.parameters["cursor"].default is None
    assert signature.return_annotation == "EmailMessagePage"


def test_search_without_cursor_fetches_one_default_page() -> None:
    async def exercise() -> None:
        provider = RecordingSearchPageProvider(EmailMessagePage(messages=()))
        plugin = EmailPlugin(plugin_config(), provider=provider)

        page = await plugin.search_messages("needle")

        assert page == EmailMessagePage(messages=(), next_cursor=None)
        assert provider.fetch_calls == [(50, None)]
        assert provider.write_calls == []

    asyncio.run(exercise())


@pytest.mark.parametrize("limit", [1, MAX_FETCH_LIMIT])
def test_search_forwards_valid_limit_boundaries_unchanged(limit: int) -> None:
    async def exercise() -> None:
        provider = RecordingSearchPageProvider(EmailMessagePage(messages=()))
        plugin = EmailPlugin(plugin_config(), provider=provider)

        await plugin.search_messages("needle", limit=limit)

        assert provider.fetch_calls == [(limit, None)]

    asyncio.run(exercise())


@pytest.mark.parametrize("limit", [101, None, True, 0, -1, 1.5, "1", [], {}])
def test_search_rejects_invalid_limits_before_provider_fetch(limit: object) -> None:
    async def exercise() -> None:
        provider = RecordingSearchPageProvider(EmailMessagePage(messages=()))
        plugin = EmailPlugin(plugin_config(), provider=provider)

        with pytest.raises(EmailFetchLimitError):
            await plugin.search_messages("needle", limit=limit)  # type: ignore[arg-type]

        assert provider.fetch_calls == []
        assert provider.write_calls == []

    asyncio.run(exercise())


def test_search_allows_none_cursor() -> None:
    async def exercise() -> None:
        provider = RecordingSearchPageProvider(EmailMessagePage(messages=()))
        plugin = EmailPlugin(plugin_config(), provider=provider)

        await plugin.search_messages("needle", limit=1, cursor=None)

        assert provider.fetch_calls == [(1, None)]

    asyncio.run(exercise())


@pytest.mark.parametrize("cursor", ["", "   ", True, 1, 1.5, [], {}])
def test_search_rejects_invalid_cursors_before_provider_fetch(cursor: object) -> None:
    async def exercise() -> None:
        provider = RecordingSearchPageProvider(EmailMessagePage(messages=()))
        plugin = EmailPlugin(plugin_config(), provider=provider)

        with pytest.raises(EmailFetchCursorError):
            await plugin.search_messages("needle", cursor=cursor)  # type: ignore[arg-type]

        assert provider.fetch_calls == []
        assert provider.write_calls == []

    asyncio.run(exercise())


@pytest.mark.parametrize("query", [None, True, 1, 1.5, [], {}, "", "   "])
def test_search_query_gate_runs_before_read_and_provider_gates(query: object) -> None:
    async def exercise() -> None:
        provider = RecordingSearchPageProvider(EmailMessagePage(messages=()))
        plugin = EmailPlugin(plugin_config(read_mode="disabled"), provider=provider)

        with pytest.raises(EmailSearchQueryError):
            await plugin.search_messages(query)  # type: ignore[arg-type]

        assert provider.fetch_calls == []
        assert provider.write_calls == []

    asyncio.run(exercise())


def test_search_rejects_oversized_query_before_provider_fetch() -> None:
    async def exercise() -> None:
        provider = RecordingSearchPageProvider(EmailMessagePage(messages=()))
        plugin = EmailPlugin(plugin_config(), provider=provider)

        with pytest.raises(EmailSearchQueryError, match="must not exceed"):
            await plugin.search_messages("x" * (SEARCH_QUERY_MAX_LENGTH + 1))

        assert provider.fetch_calls == []

    asyncio.run(exercise())


def test_search_preserves_next_cursor_when_current_page_has_no_matches() -> None:
    async def exercise() -> None:
        provider = RecordingSearchPageProvider(
            EmailMessagePage(
                messages=(message("miss", subject="Other"),),
                next_cursor="provider-has-another-message-page",
            )
        )
        plugin = EmailPlugin(plugin_config(), provider=provider)

        page = await plugin.search_messages("needle", limit=1)

        assert page.messages == ()
        assert page.next_cursor == "provider-has-another-message-page"
        assert provider.fetch_calls == [(1, None)]

    asyncio.run(exercise())


def test_search_last_provider_page_has_no_next_cursor() -> None:
    async def exercise() -> None:
        provider = RecordingSearchPageProvider(
            EmailMessagePage(
                messages=(message("match", subject="Needle"),), next_cursor=None
            )
        )
        plugin = EmailPlugin(plugin_config(), provider=provider)

        page = await plugin.search_messages("needle", limit=1, cursor="last-page")

        assert [item.message_id for item in page.messages] == ["match"]
        assert page.next_cursor is None
        assert provider.fetch_calls == [(1, "last-page")]

    asyncio.run(exercise())


def test_search_never_follows_a_non_empty_next_cursor() -> None:
    async def exercise() -> None:
        provider = RecordingSearchPageProvider(
            EmailMessagePage(messages=(), next_cursor="do-not-follow")
        )
        plugin = EmailPlugin(plugin_config(), provider=provider)

        page = await plugin.search_messages("needle", limit=1)

        assert page.next_cursor == "do-not-follow"
        assert provider.fetch_calls == [(1, None)]

    asyncio.run(exercise())


def test_search_propagates_unknown_mock_cursor_without_retry() -> None:
    async def exercise() -> None:
        plugin = EmailPlugin(plugin_config(), provider=MockEmailProvider())

        with pytest.raises(MockCursorError, match="unknown mock"):
            await plugin.search_messages("needle", limit=1, cursor="unknown")

    asyncio.run(exercise())


def test_search_propagates_provider_error_after_exactly_one_fetch() -> None:
    async def exercise() -> None:
        provider = FailingSearchPageProvider(EmailMessagePage(messages=()))
        plugin = EmailPlugin(plugin_config(), provider=provider)

        with pytest.raises(SearchProviderError, match="search page failed"):
            await plugin.search_messages("needle", limit=2, cursor="opaque")

        assert provider.fetch_calls == [(2, "opaque")]
        assert provider.write_calls == []

    asyncio.run(exercise())


def test_search_policy_and_capability_gates_never_fetch() -> None:
    async def exercise() -> None:
        disabled_provider = RecordingSearchPageProvider(EmailMessagePage(messages=()))
        disabled = EmailPlugin(
            plugin_config(read_mode="disabled"), provider=disabled_provider
        )
        with pytest.raises(EmailReadDisabledError):
            await disabled.search_messages("needle")
        assert disabled_provider.fetch_calls == []

        unavailable = EmailPlugin(plugin_config(), provider=None)
        with pytest.raises(EmailProviderUnavailableError):
            await unavailable.search_messages("needle")

        unsupported_provider = NoFetchSearchProvider(EmailMessagePage(messages=()))
        unsupported = EmailPlugin(plugin_config(), provider=unsupported_provider)
        with pytest.raises(EmailFetchUnsupportedError):
            await unsupported.search_messages("needle")
        assert unsupported_provider.fetch_calls == []

    asyncio.run(exercise())


def test_search_limit_gate_precedes_cursor_gate() -> None:
    async def exercise() -> None:
        provider = RecordingSearchPageProvider(EmailMessagePage(messages=()))
        plugin = EmailPlugin(plugin_config(), provider=provider)

        with pytest.raises(EmailFetchLimitError):
            await plugin.search_messages("needle", limit=0, cursor="")

        assert provider.fetch_calls == []
        assert provider.write_calls == []

    asyncio.run(exercise())


def test_paginated_search_does_not_change_runtime_context() -> None:
    async def exercise() -> None:
        context = HermesContext(profile_name="active-profile")
        provider = RecordingSearchPageProvider(EmailMessagePage(messages=()))
        plugin = EmailPlugin(
            plugin_config(),
            context_source=FixedContextSource(context),
            provider=provider,
        )

        await plugin.search_messages("needle", limit=1, cursor=None)

        assert plugin.get_hermes_context() is context
        assert plugin.get_hermes_context().persona is None
        assert provider.fetch_calls == [(1, None)]
        assert provider.write_calls == []

    asyncio.run(exercise())
