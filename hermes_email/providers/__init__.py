"""Email provider interfaces."""

from .base import EmailProvider, ProviderCapabilities
from .mock import MockEmailProvider, MockSendBlockedError

__all__ = [
    "EmailProvider",
    "MockEmailProvider",
    "MockSendBlockedError",
    "ProviderCapabilities",
]
