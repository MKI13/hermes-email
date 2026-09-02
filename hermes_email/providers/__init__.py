"""Email provider interfaces."""

from .base import EmailProvider, ProviderCapabilities
from .mock import MockCursorError, MockEmailProvider, MockSendBlockedError
from .resolver import (
    EmailProviderResolutionError,
    ProviderNotConfiguredError,
    UnsupportedEmailProviderError,
    resolve_email_provider,
)

__all__ = [
    "EmailProvider",
    "EmailProviderResolutionError",
    "MockCursorError",
    "MockEmailProvider",
    "MockSendBlockedError",
    "ProviderCapabilities",
    "ProviderNotConfiguredError",
    "UnsupportedEmailProviderError",
    "resolve_email_provider",
]
