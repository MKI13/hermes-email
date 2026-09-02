"""Hermes registration and provider-neutral plugin facade."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Self, Sequence

from .config import EmailPluginConfig
from .context import ActiveProfileContextSource, HermesContext, HermesContextSource
from .models import EmailDraft, EmailMessage
from .providers import EmailProvider, resolve_email_provider


class EmailReadDisabledError(PermissionError):
    """Raised when plugin configuration does not explicitly allow reading."""


class EmailProviderUnavailableError(RuntimeError):
    """Raised when no provider is attached to the plugin facade."""


class EmailFetchUnsupportedError(RuntimeError):
    """Raised when the attached provider does not declare fetch capability."""


class EmailFetchLimitError(ValueError):
    """Raised when a fetch would not have a finite positive message limit."""


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
    ) -> None:
        self.config = config or EmailPluginConfig()
        self.context_source = context_source
        self.provider = provider

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

    async def fetch_messages(self, *, limit: int = 50) -> Sequence[EmailMessage]:
        """Fetch a finite message page after independent policy and capability gates."""
        if self.config.email.read_mode != "mock":
            raise EmailReadDisabledError("email reading is disabled; read_mode must be mock")
        if self.provider is None:
            raise EmailProviderUnavailableError("no email provider is configured on the plugin")
        if not self.provider.capabilities.fetch:
            raise EmailFetchUnsupportedError(
                f"email provider {self.provider.name!r} does not support message fetching"
            )
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise EmailFetchLimitError("limit must be a positive integer")
        return await self.provider.fetch_messages(limit=limit)

    def prepare_draft(self, draft: EmailDraft) -> EmailDraft:
        """Return a local draft value without provider or network effects."""
        if self.config.email.draft_mode == "disabled":
            raise PermissionError("draft creation is disabled")
        return draft

    async def send_message(self, draft_id: str) -> None:
        """Refuse sending unconditionally in version 0.6.0."""
        del draft_id
        raise SendingUnavailableError("email sending is not implemented in version 0.6.0")


def register(ctx: Any) -> EmailPlugin:
    """Bind the public Hermes profile context and register the email skill.

    Version 0.6.0 deliberately registers no tools, model hooks, providers,
    pollers, background tasks, or account connections.
    """
    runtime = EmailPlugin(context_source=ActiveProfileContextSource(ctx))

    def release_runtime_context() -> None:
        runtime.context_source = None

    ctx.on_unload(release_runtime_context)

    skill_path = Path(__file__).resolve().parent.parent / "skill" / "SKILL.md"
    ctx.register_skill(
        "email",
        skill_path,
        description="Handle email using the active Hermes profile safely.",
    )
    return runtime
