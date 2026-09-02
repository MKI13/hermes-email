"""Abstract interface for future email provider adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Sequence

from hermes_email.models import EmailDraft, EmailMessage


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    """Operations a provider explicitly declares as supported."""

    fetch: bool = False
    drafts: bool = False
    send: bool = False
    delete: bool = False
    move: bool = False


class EmailProvider(ABC):
    """Provider-neutral asynchronous email interface.

    Version 0.1.0 ships no concrete providers. Implementations must not treat
    provider capability declarations as user authorization; the plugin safety
    configuration remains an independent gate.
    """

    capabilities = ProviderCapabilities()

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the stable provider identifier."""

    @abstractmethod
    async def fetch_messages(self, *, limit: int = 50) -> Sequence[EmailMessage]:
        """Return normalized messages without changing mailbox state."""

    @abstractmethod
    async def get_message(self, message_id: str) -> EmailMessage | None:
        """Return one normalized message by provider-stable identifier."""

    @abstractmethod
    async def create_draft(self, draft: EmailDraft) -> EmailDraft:
        """Store a draft without sending it."""

    @abstractmethod
    async def send_message(self, draft_id: str) -> None:
        """Send a stored draft only after independent safety authorization.

        No version 0.1.0 implementation may perform this operation.
        """
