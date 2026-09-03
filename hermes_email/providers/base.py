"""Abstract interface for future email provider adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..models import EmailMessage, EmailMessagePage


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    """Operations a provider explicitly declares as supported."""

    fetch: bool = False
    get: bool = False


class EmailProvider(ABC):
    """Provider-neutral asynchronous email interface.

    Implementations must not treat provider capability declarations as user
    authorization; the plugin safety configuration remains an independent
    gate.
    """

    capabilities = ProviderCapabilities()

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the stable provider identifier."""

    @abstractmethod
    async def fetch_messages(
        self, *, limit: int = 50, cursor: str | None = None
    ) -> EmailMessagePage:
        """Return one normalized message page without changing mailbox state."""

    @abstractmethod
    async def get_message(self, message_id: str) -> EmailMessage | None:
        """Return one normalized message by provider-stable identifier."""

    async def check_health(self) -> None:
        """Validate provider readiness without reading or changing messages."""

    def close(self) -> None:
        """Release active provider resources without mailbox mutation."""
