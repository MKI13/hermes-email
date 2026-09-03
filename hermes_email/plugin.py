"""Hermes registration and provider-neutral plugin facade."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, Self

from . import __version__
from .config import ConfigError, EmailPluginConfig
from .context import ActiveProfileContextSource, HermesContext, HermesContextSource
from .draft_storage import (
    DraftStorageError,
    DraftMutation,
    SqliteDraftStore,
)
from .models import EmailDraft, EmailDraftPage, EmailMessage, EmailMessagePage
from .providers import (
    EmailProvider,
    EmailProviderError,
    EmailProviderResolutionError,
    ProviderAuthenticationError,
    ProviderConnectionError,
    ProviderMailboxError,
    ProviderNotConfiguredError,
    ProviderProtocolError,
    ProviderTimeoutError,
    ProviderTlsError,
    resolve_email_provider,
)
from .secrets import SecretResolver
from .storage import (
    EmailStorageError,
    SqliteObservationStore,
    observation_namespace,
)


MAX_FETCH_LIMIT: Final = 100
SEARCH_FETCH_LIMIT: Final = 50
SEARCH_QUERY_MAX_LENGTH: Final = 256
_RUNTIME_CONFIG_SECTIONS: Final = (
    "email",
    "hermes",
    "credentials",
    "imap",
    "storage",
    "drafts",
    "behavior",
    "safety",
)
_RUNTIME_CONFIG_MISSING: Final = object()


class EmailRuntimeState(StrEnum):
    """Safe operational states reported by the plugin runtime."""

    DISABLED = "disabled"
    MOCK_READY = "mock-ready"
    PROVIDER_CONFIGURED = "provider-configured"
    PROVIDER_READY = "provider-ready"
    AUTHENTICATION_ERROR = "authentication-error"
    PROVIDER_UNREACHABLE = "provider-unreachable"
    STORAGE_ERROR = "storage-error"
    CONFIGURATION_ERROR = "configuration-error"


@dataclass(frozen=True, slots=True)
class EmailRuntimeStatus:
    """Non-sensitive snapshot of the email plugin's runtime readiness."""

    version: str
    state: EmailRuntimeState
    provider: str | None
    profile: str | None
    read_enabled: bool
    storage_enabled: bool
    draft_enabled: bool
    send_enabled: bool
    diagnostic: str | None = None
    draft_diagnostic: str | None = None


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


class DraftingDisabledError(PermissionError):
    """Raised when local draft storage is not explicitly enabled."""


class EmailPlugin:
    """Provider-neutral orchestrator for mail access and Hermes integration."""

    def __init__(
        self,
        config: EmailPluginConfig | None = None,
        *,
        context_source: HermesContextSource | None = None,
        provider: EmailProvider | None = None,
        observation_store: SqliteObservationStore | None = None,
        draft_store: SqliteDraftStore | None = None,
        runtime_state: EmailRuntimeState | None = None,
        runtime_diagnostic: str | None = None,
        draft_diagnostic: str | None = None,
    ) -> None:
        self.config = config or EmailPluginConfig()
        self.context_source = context_source
        self.provider = provider
        self.observation_store = observation_store
        self.draft_store = draft_store
        self._closed = False
        self._runtime_state = runtime_state or self._initial_runtime_state(provider)
        self._runtime_diagnostic = runtime_diagnostic
        self._draft_diagnostic = draft_diagnostic

    @staticmethod
    def _initial_runtime_state(provider: EmailProvider | None) -> EmailRuntimeState:
        if provider is None:
            return EmailRuntimeState.DISABLED
        if provider.name == "mock":
            return EmailRuntimeState.MOCK_READY
        return EmailRuntimeState.PROVIDER_CONFIGURED

    @classmethod
    def from_config(
        cls,
        config: EmailPluginConfig,
        *,
        context_source: HermesContextSource | None = None,
        secret_resolver: SecretResolver | None = None,
        observation_store: SqliteObservationStore | None = None,
        draft_store: SqliteDraftStore | None = None,
    ) -> Self:
        """Create a disconnected plugin using only the configured provider resolver."""
        provider = resolve_email_provider(config, secret_resolver=secret_resolver)
        return cls(
            config,
            context_source=context_source,
            provider=provider,
            observation_store=observation_store,
            draft_store=draft_store,
        )

    def get_hermes_context(self) -> HermesContext:
        """Return inherited context or an intentionally empty snapshot."""
        if self.context_source is None:
            return HermesContext()
        return self.context_source.get_context()

    def get_runtime_status(self) -> EmailRuntimeStatus:
        """Return non-sensitive readiness data without mailbox activity."""
        context = self.get_hermes_context()
        ready = self._runtime_state in {
            EmailRuntimeState.MOCK_READY,
            EmailRuntimeState.PROVIDER_READY,
        }
        provider_name = self.provider.name if self.provider is not None else None
        return EmailRuntimeStatus(
            version=__version__,
            state=self._runtime_state,
            provider=provider_name,
            profile=context.profile_name,
            read_enabled=(
                ready
                and self.config.email.read_mode in {"mock", "readonly"}
                and self.provider is not None
                and self.provider.capabilities.fetch
            ),
            storage_enabled=self.observation_store is not None and not self._closed,
            draft_enabled=self.draft_store is not None and not self._closed,
            send_enabled=False,
            diagnostic=self._runtime_diagnostic,
            draft_diagnostic=self._draft_diagnostic,
        )

    def _read_provider(self) -> EmailProvider:
        """Return the provider after shared read-policy gates pass."""
        if self.config.email.read_mode not in {"mock", "readonly"}:
            raise EmailReadDisabledError(
                "email reading is disabled; read_mode must explicitly allow reading"
            )
        if self.provider is None:
            raise EmailProviderUnavailableError("no email provider is configured on the plugin")
        return self.provider

    def close(self) -> None:
        """Disable the runtime and release provider and Hermes references."""
        self._closed = True
        provider = self.provider
        observation_store = self.observation_store
        draft_store = self.draft_store
        self.provider = None
        self.observation_store = None
        self.draft_store = None
        self.context_source = None
        self._runtime_state = EmailRuntimeState.DISABLED
        self._runtime_diagnostic = None
        self._draft_diagnostic = None
        if draft_store is not None:
            draft_store.close()
        if observation_store is not None:
            observation_store.close()
        if provider is not None:
            provider.close()

    async def check_provider_health(self) -> EmailRuntimeStatus:
        """Run one explicit provider health probe and retain only a safe result code."""
        if self.config.email.read_mode == "disabled":
            raise EmailReadDisabledError("email reading is disabled")
        provider = self.provider
        if provider is None:
            raise EmailProviderUnavailableError("no email provider is configured on the plugin")
        try:
            await provider.check_health()
        except EmailProviderError as exc:
            self._record_provider_failure(exc)
        else:
            if (
                not self._closed
                and self._runtime_state is not EmailRuntimeState.STORAGE_ERROR
            ):
                self._runtime_state = (
                    EmailRuntimeState.MOCK_READY
                    if provider.name == "mock"
                    else EmailRuntimeState.PROVIDER_READY
                )
                self._runtime_diagnostic = None
        return self.get_runtime_status()

    def _ensure_open_after_operation(self) -> None:
        if self._closed:
            raise EmailProviderUnavailableError("email plugin is closed")

    def _record_provider_failure(self, error: EmailProviderError) -> None:
        if self._closed:
            return
        if isinstance(error, ProviderAuthenticationError):
            self._runtime_state = EmailRuntimeState.AUTHENTICATION_ERROR
            self._runtime_diagnostic = "authentication-failed"
        elif isinstance(error, ProviderTlsError):
            self._runtime_state = EmailRuntimeState.PROVIDER_UNREACHABLE
            self._runtime_diagnostic = "tls-failed"
        elif isinstance(error, ProviderTimeoutError):
            self._runtime_state = EmailRuntimeState.PROVIDER_UNREACHABLE
            self._runtime_diagnostic = "provider-timeout"
        elif isinstance(error, ProviderConnectionError):
            self._runtime_state = EmailRuntimeState.PROVIDER_UNREACHABLE
            self._runtime_diagnostic = "connection-failed"
        elif isinstance(error, ProviderMailboxError):
            self._runtime_state = EmailRuntimeState.PROVIDER_UNREACHABLE
            self._runtime_diagnostic = "mailbox-unavailable"
        elif isinstance(error, ProviderProtocolError):
            self._runtime_state = EmailRuntimeState.PROVIDER_UNREACHABLE
            self._runtime_diagnostic = "protocol-error"
        else:
            self._runtime_state = EmailRuntimeState.PROVIDER_UNREACHABLE
            self._runtime_diagnostic = "provider-error"

    def _record_provider_success(self) -> None:
        if not self._closed and self.provider is not None:
            self._runtime_state = (
                EmailRuntimeState.MOCK_READY
                if self.provider.name == "mock"
                else EmailRuntimeState.PROVIDER_READY
            )
            self._runtime_diagnostic = None

    def _record_storage_failure(self) -> None:
        if not self._closed:
            self._runtime_state = EmailRuntimeState.STORAGE_ERROR
            self._runtime_diagnostic = "storage-error"

    async def _observe_messages(self, messages: tuple[EmailMessage, ...]) -> None:
        observation_store = self.observation_store
        if observation_store is None:
            return
        task = asyncio.create_task(
            asyncio.to_thread(observation_store.observe_messages, messages)
        )
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            try:
                await task
            except EmailStorageError:
                self._record_storage_failure()
            raise
        except EmailStorageError:
            self._record_storage_failure()
            raise

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
        try:
            page = await provider.fetch_messages(limit=limit, cursor=cursor)
        except EmailProviderError as exc:
            self._record_provider_failure(exc)
            raise
        self._ensure_open_after_operation()
        if len(page.messages) > limit:
            error = ProviderProtocolError("provider returned too many messages")
            self._record_provider_failure(error)
            raise error
        await self._observe_messages(page.messages)
        self._ensure_open_after_operation()
        self._record_provider_success()
        return page

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
        try:
            message = await provider.get_message(message_id)
        except EmailProviderError as exc:
            self._record_provider_failure(exc)
            raise
        self._ensure_open_after_operation()
        await self._observe_messages((message,) if message is not None else ())
        self._ensure_open_after_operation()
        self._record_provider_success()
        return message

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

        page = await self.fetch_messages(limit=limit, cursor=cursor)
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

    def _local_draft_store(self) -> SqliteDraftStore:
        if self._closed:
            raise DraftingDisabledError("local drafting runtime is closed")
        if self.draft_store is None or self.config.drafts.mode != "sqlite":
            raise DraftingDisabledError("local drafting is disabled")
        return self.draft_store

    async def _run_draft_operation(self, method: Any, *arguments: Any) -> Any:
        task = asyncio.create_task(asyncio.to_thread(method, *arguments))
        try:
            result = await asyncio.shield(task)
        except asyncio.CancelledError:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
            outcome_error = task.exception()
            if isinstance(outcome_error, DraftStorageError) and not self._closed:
                self._draft_diagnostic = "draft-storage-error"
            raise
        except DraftStorageError:
            if not self._closed:
                self._draft_diagnostic = "draft-storage-error"
            raise
        else:
            if not self._closed:
                self._draft_diagnostic = None
            return result

    async def create_draft(
        self, draft: EmailDraft, operation_id: str
    ) -> DraftMutation:
        """Persist one explicit local draft without provider activity."""
        store = self._local_draft_store()
        return await self._run_draft_operation(
            store.create_draft, draft, operation_id
        )

    async def get_draft(self, draft_id: str) -> EmailDraft | None:
        """Return one active local draft without provider activity."""
        store = self._local_draft_store()
        return await self._run_draft_operation(store.get_draft, draft_id)

    async def list_drafts(
        self, *, state: str = "active", limit: int = 10, cursor: str | None = None
    ) -> EmailDraftPage:
        """Return one caller-selected local draft summary page."""
        store = self._local_draft_store()
        method = lambda: store.list_drafts(state=state, limit=limit, cursor=cursor)
        return await self._run_draft_operation(method)

    async def update_draft(
        self,
        draft_id: str,
        expected_revision: int,
        draft: EmailDraft,
        operation_id: str,
    ) -> DraftMutation:
        """Replace one exact active local draft revision."""
        store = self._local_draft_store()
        return await self._run_draft_operation(
            store.update_draft,
            draft_id,
            expected_revision,
            draft,
            operation_id,
        )

    async def trash_draft(
        self, draft_id: str, expected_revision: int, operation_id: str
    ) -> DraftMutation:
        """Move one exact local draft revision to reversible trash."""
        store = self._local_draft_store()
        return await self._run_draft_operation(
            store.trash_draft, draft_id, expected_revision, operation_id
        )

    async def restore_draft(
        self, draft_id: str, expected_revision: int, operation_id: str
    ) -> DraftMutation:
        """Restore one exact trashed local draft revision."""
        store = self._local_draft_store()
        return await self._run_draft_operation(
            store.restore_draft, draft_id, expected_revision, operation_id
        )


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
    if status.draft_diagnostic is not None:
        lines.append(f"Draft diagnostic: {status.draft_diagnostic}")
    lines.extend(
        (
            f"Read: {'enabled' if status.read_enabled else 'disabled'}",
            f"Storage: {'enabled' if status.storage_enabled else 'disabled'}",
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


def _create_observation_store(
    ctx: Any, config: EmailPluginConfig
) -> SqliteObservationStore | None:
    if config.storage.mode != "sqlite":
        return None
    data_dir = ctx.state.data_dir
    if not isinstance(data_dir, Path):
        raise RuntimeError("Hermes plugin state directory is unavailable")
    return SqliteObservationStore(
        data_dir / "email-observations.sqlite3",
        observation_namespace(config),
        config.storage,
    )


def _create_draft_store(ctx: Any, config: EmailPluginConfig) -> SqliteDraftStore | None:
    if config.drafts.mode != "sqlite":
        return None
    data_dir = ctx.state.data_dir
    if not isinstance(data_dir, Path):
        raise RuntimeError("Hermes plugin state directory is unavailable")
    return SqliteDraftStore(data_dir / "email-drafts.sqlite3", config.drafts)


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

    observation_store = _create_observation_store(ctx, config)
    draft_store = _create_draft_store(ctx, config)
    provider_name = config.email.provider
    if (
        (provider_name is None or not provider_name.strip())
        and config.email.read_mode == "disabled"
    ):
        return EmailPlugin(
            config, context_source=context_source, draft_store=draft_store
        )

    try:
        return EmailPlugin.from_config(
            config,
            context_source=context_source,
            observation_store=observation_store,
            draft_store=draft_store,
        )
    except EmailProviderResolutionError as exc:
        diagnostic = (
            "provider-not-configured"
            if isinstance(exc, ProviderNotConfiguredError)
            else "unsupported-provider"
        )
        return EmailPlugin(
            config,
            context_source=context_source,
            draft_store=draft_store,
            runtime_state=EmailRuntimeState.CONFIGURATION_ERROR,
            runtime_diagnostic=diagnostic,
        )


def register(ctx: Any) -> EmailPlugin:
    """Load safe settings and register read tools, status, skill, and cleanup.

    Version 0.17.0 registers no model hooks, pollers, background tasks, or
    account connections during registration.
    """
    runtime = _create_runtime_plugin(ctx)

    def release_runtime_context() -> None:
        runtime.close()

    from .draft_tools import register_draft_tools
    from .tools import register_read_tools

    unload_handle: Any | None = None
    command_handle: Any | None = None
    draft_handles: tuple[Any, ...] = ()
    read_handles: tuple[Any, ...] = ()
    try:
        unload_handle = ctx.on_unload(release_runtime_context)
        command_handle = ctx.register_command(
            "email-status",
            handler=lambda raw_args: _handle_email_status(runtime, raw_args),
            description="Show safe Hermes Email runtime status.",
        )
        draft_handles = register_draft_tools(ctx, runtime)
        read_handles = register_read_tools(ctx, runtime)
        skill_path = Path(__file__).resolve().parent.parent / "skill" / "SKILL.md"
        ctx.register_skill(
            "email",
            skill_path,
            description="Handle email using the active Hermes profile safely.",
        )
    except Exception:
        for handle in reversed(read_handles + draft_handles):
            handle.dispose()
        if command_handle is not None:
            command_handle.dispose()
        if unload_handle is not None:
            unload_handle.dispose()
        runtime.close()
        raise
    return runtime
