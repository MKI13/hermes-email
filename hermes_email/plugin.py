"""Hermes registration and provider-neutral plugin facade."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, Self

from . import __version__
from .config import ConfigError, EmailPluginConfig
from .context import ActiveProfileContextSource, HermesContext, HermesContextSource
from .models import EmailDraft, EmailMessage, EmailMessagePage
from .providers import (
    EmailProvider,
    EmailProviderResolutionError,
    ProviderNotConfiguredError,
    resolve_email_provider,
)


MAX_FETCH_LIMIT: Final = 100
SEARCH_FETCH_LIMIT: Final = 50
SEARCH_QUERY_MAX_LENGTH: Final = 256
_RUNTIME_CONFIG_SECTIONS: Final = (
    "email",
    "hermes",
    "credentials",
    "behavior",
    "safety",
)
_RUNTIME_CONFIG_MISSING: Final = object()


class EmailRuntimeState(StrEnum):
    """Safe operational states reported by the plugin runtime."""

    DISABLED = "disabled"
    MOCK_READY = "mock-ready"
    CONFIGURATION_ERROR = "configuration-error"


@dataclass(frozen=True, slots=True)
class EmailRuntimeStatus:
    """Non-sensitive snapshot of the email plugin's runtime readiness."""

    version: str
    state: EmailRuntimeState
    provider: str | None
    profile: str | None
    read_enabled: bool
    draft_enabled: bool
    send_enabled: bool
    diagnostic: str | None = None


class EmailReadDisabledError(PermissionError):
    """Raised when plugin configuration does not explicitly allow reading."""


class EmailProviderUnavailableError(RuntimeError):
    """Raised when no provider is attached to the plugin facade."""


class EmailFetchUnsupportedError(RuntimeError):
    """Raised when the attached provider does not declare fetch capability."""


class EmailGetUnsupportedError(RuntimeError):
    """Raised when the attached provider does not declare lookup capability."""


class EmailFetchLimitError(ValueError):
    """Raised when a fetch limit is invalid or exceeds the fixed page maximum."""


class EmailFetchCursorError(ValueError):
    """Raised when a fetch cursor is not None or a non-empty opaque string."""


class EmailMessageIdError(ValueError):
    """Raised when a message lookup lacks a non-empty string identifier."""


class EmailSearchQueryError(ValueError):
    """Raised when a local search query is empty, invalid, or too long."""


class SendingUnavailableError(PermissionError):
    """Raised because this version cannot send email."""


class EmailPlugin:
    """Minimal orchestrator for future provider and Hermes adapters."""

    def __init__(
        self,
        config: EmailPluginConfig | None = None,
        *,
        context_source: HermesContextSource | None = None,
        provider: EmailProvider | None = None,
        runtime_state: EmailRuntimeState | None = None,
        runtime_diagnostic: str | None = None,
    ) -> None:
        self.config = config or EmailPluginConfig()
        self.context_source = context_source
        self.provider = provider
        self._runtime_state = runtime_state or (
            EmailRuntimeState.MOCK_READY if provider is not None else EmailRuntimeState.DISABLED
        )
        self._runtime_diagnostic = runtime_diagnostic

    @classmethod
    def from_config(
        cls,
        config: EmailPluginConfig,
        *,
        context_source: HermesContextSource | None = None,
    ) -> Self:
        """Create a plugin using only the configured provider resolver."""
        provider = resolve_email_provider(config)
        return cls(config, context_source=context_source, provider=provider)

    def get_hermes_context(self) -> HermesContext:
        """Return inherited context or an intentionally empty snapshot."""
        if self.context_source is None:
            return HermesContext()
        return self.context_source.get_context()

    def get_runtime_status(self) -> EmailRuntimeStatus:
        """Return non-sensitive readiness data without mailbox activity."""
        context = self.get_hermes_context()
        ready = self._runtime_state is EmailRuntimeState.MOCK_READY
        provider_name = self.provider.name if self.provider is not None else None
        return EmailRuntimeStatus(
            version=__version__,
            state=self._runtime_state,
            provider=provider_name,
            profile=context.profile_name,
            read_enabled=(
                ready
                and self.config.email.read_mode == "mock"
                and self.provider is not None
                and self.provider.capabilities.fetch
            ),
            draft_enabled=(
                ready
                and self.config.email.draft_mode == "mock"
                and self.provider is not None
                and self.provider.capabilities.drafts
            ),
            send_enabled=False,
            diagnostic=self._runtime_diagnostic,
        )

    def _read_provider(self) -> EmailProvider:
        """Return the provider after shared read-policy gates pass."""
        if self.config.email.read_mode != "mock":
            raise EmailReadDisabledError("email reading is disabled; read_mode must be mock")
        if self.provider is None:
            raise EmailProviderUnavailableError("no email provider is configured on the plugin")
        return self.provider

    async def fetch_messages(
        self, *, limit: int = 50, cursor: str | None = None
    ) -> EmailMessagePage:
        """Fetch at most one finite provider page after all read gates pass."""
        provider = self._read_provider()
        if not provider.capabilities.fetch:
            raise EmailFetchUnsupportedError(
                f"email provider {provider.name!r} does not support message fetching"
            )
        self._validate_fetch_limit(limit)
        self._validate_fetch_cursor(cursor)
        return await provider.fetch_messages(limit=limit, cursor=cursor)

    @staticmethod
    def _validate_fetch_limit(limit: int) -> None:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise EmailFetchLimitError("limit must be a positive integer")
        if limit > MAX_FETCH_LIMIT:
            raise EmailFetchLimitError(
                f"limit must not exceed the maximum page size of {MAX_FETCH_LIMIT}"
            )

    @staticmethod
    def _validate_fetch_cursor(cursor: str | None) -> None:
        if cursor is not None and (
            not isinstance(cursor, str) or not cursor.strip()
        ):
            raise EmailFetchCursorError("cursor must be None or a non-empty string")

    async def get_message(self, message_id: str) -> EmailMessage | None:
        """Return one message by an opaque, non-empty provider identifier."""
        provider = self._read_provider()
        if not provider.capabilities.get:
            raise EmailGetUnsupportedError(
                f"email provider {provider.name!r} does not support message lookup"
            )
        if not isinstance(message_id, str) or not message_id.strip():
            raise EmailMessageIdError("message_id must be a non-empty string")
        return await provider.get_message(message_id)

    async def search_messages(
        self,
        query: str,
        *,
        limit: int = SEARCH_FETCH_LIMIT,
        cursor: str | None = None,
    ) -> EmailMessagePage:
        """Search one bounded local message page using plain text matching."""
        if not isinstance(query, str) or not query.strip():
            raise EmailSearchQueryError("query must be a non-empty string")

        normalized_query = query.strip()
        if len(normalized_query) > SEARCH_QUERY_MAX_LENGTH:
            raise EmailSearchQueryError(
                f"query must not exceed {SEARCH_QUERY_MAX_LENGTH} characters"
            )

        provider = self._read_provider()
        if not provider.capabilities.fetch:
            raise EmailFetchUnsupportedError(
                f"email provider {provider.name!r} does not support message fetching"
            )
        self._validate_fetch_limit(limit)
        self._validate_fetch_cursor(cursor)

        page = await provider.fetch_messages(limit=limit, cursor=cursor)
        needle = normalized_query.casefold()
        return EmailMessagePage(
            messages=tuple(
                message
                for message in page.messages
                if any(
                    needle in value.casefold()
                    for value in (
                        message.subject,
                        message.sender.address,
                        message.sender.display_name or "",
                        message.body_text or "",
                    )
                )
            ),
            next_cursor=page.next_cursor,
        )

    def prepare_draft(self, draft: EmailDraft) -> EmailDraft:
        """Return a local draft value without provider or network effects."""
        if self.config.email.draft_mode == "disabled":
            raise PermissionError("draft creation is disabled")
        return draft

    async def send_message(self, draft_id: str) -> None:
        """Refuse sending unconditionally in version 0.13.0."""
        del draft_id
        raise SendingUnavailableError("email sending is not implemented in version 0.13.0")


def format_runtime_status(status: EmailRuntimeStatus) -> str:
    """Format only the fixed, non-sensitive runtime health fields."""
    lines = [
        "Hermes Email",
        f"Version: {status.version}",
        f"Status: {status.state.value}",
        f"Provider: {status.provider or 'none'}",
        f"Profile: {getattr(status, 'profile') or 'none'}",
    ]
    if status.diagnostic is not None:
        lines.append(f"Diagnostic: {status.diagnostic}")
    lines.extend(
        (
            f"Read: {'enabled' if status.read_enabled else 'disabled'}",
            f"Draft: {'enabled' if status.draft_enabled else 'disabled'}",
            f"Send: {'enabled' if status.send_enabled else 'disabled'}",
        )
    )
    return "\n".join(lines)


def _handle_email_status(runtime: EmailPlugin, raw_args: str) -> str:
    del raw_args
    return format_runtime_status(runtime.get_runtime_status())


def _load_runtime_config(ctx: Any) -> EmailPluginConfig:
    raw_config: dict[str, Any] = {}
    for section in _RUNTIME_CONFIG_SECTIONS:
        value = ctx.get_config(section, default=_RUNTIME_CONFIG_MISSING)
        if value is not _RUNTIME_CONFIG_MISSING:
            raw_config[section] = value
    return EmailPluginConfig.from_mapping(raw_config)


def _create_runtime_plugin(ctx: Any) -> EmailPlugin:
    context_source = ActiveProfileContextSource(ctx)
    try:
        config = _load_runtime_config(ctx)
    except ConfigError:
        return EmailPlugin(
            context_source=context_source,
            runtime_state=EmailRuntimeState.CONFIGURATION_ERROR,
            runtime_diagnostic="invalid-plugin-settings",
        )

    provider_name = config.email.provider
    if (
        (provider_name is None or not provider_name.strip())
        and config.email.read_mode == "disabled"
    ):
        return EmailPlugin(config, context_source=context_source)

    try:
        return EmailPlugin.from_config(config, context_source=context_source)
    except EmailProviderResolutionError as exc:
        diagnostic = (
            "provider-not-configured"
            if isinstance(exc, ProviderNotConfiguredError)
            else "unsupported-provider"
        )
        return EmailPlugin(
            config,
            context_source=context_source,
            runtime_state=EmailRuntimeState.CONFIGURATION_ERROR,
            runtime_diagnostic=diagnostic,
        )


def register(ctx: Any) -> EmailPlugin:
    """Load safe runtime settings, bind Hermes context, and register the skill.

    Version 0.13.0 deliberately registers no tools, model hooks, pollers,
    background tasks, or account connections.
    """
    runtime = _create_runtime_plugin(ctx)

    def release_runtime_context() -> None:
        runtime.context_source = None

    ctx.on_unload(release_runtime_context)
    ctx.register_command(
        "email-status",
        handler=lambda raw_args: _handle_email_status(runtime, raw_args),
        description="Show safe Hermes Email runtime status.",
    )

    skill_path = Path(__file__).resolve().parent.parent / "skill" / "SKILL.md"
    ctx.register_skill(
        "email",
        skill_path,
        description="Handle email using the active Hermes profile safely.",
    )
    return runtime
