"""Hermes registration and provider-neutral plugin facade."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Self

from .config import EmailPluginConfig
from .context import ActiveProfileContextSource, HermesContext, HermesContextSource
from .models import EmailDraft
from .providers import EmailProvider, resolve_email_provider


class SendingUnavailableError(PermissionError):
    """Raised because version 0.5.0 cannot send email."""


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

    def prepare_draft(self, draft: EmailDraft) -> EmailDraft:
        """Return a local draft value without provider or network effects."""
        if self.config.email.draft_mode == "disabled":
            raise PermissionError("draft creation is disabled")
        return draft

    async def send_message(self, draft_id: str) -> None:
        """Refuse sending unconditionally in version 0.5.0."""
        del draft_id
        raise SendingUnavailableError("email sending is not implemented in version 0.5.0")


def register(ctx: Any) -> EmailPlugin:
    """Bind the public Hermes profile context and register the email skill.

    Version 0.5.0 deliberately registers no tools, model hooks, providers,
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
