"""Email provider interfaces."""

from .base import EmailProvider, ProviderCapabilities
from .errors import (
    EmailProviderError,
    ProviderAuthenticationError,
    ProviderConnectionError,
    ProviderMailboxError,
    ProviderMessageError,
    ProviderProtocolError,
    ProviderTimeoutError,
    ProviderTlsError,
    provider_error_code,
)
from .imap import (
    ImapCursorError,
    ImapLimitError,
    ImapMessageIdError,
    ImapReadOnlyProvider,
)
from .mock import MockCursorError, MockEmailProvider
from .resolver import (
    EmailProviderResolutionError,
    ProviderNotConfiguredError,
    UnsupportedEmailProviderError,
    resolve_email_provider,
)

__all__ = [
    "EmailProvider",
    "EmailProviderError",
    "EmailProviderResolutionError",
    "ImapCursorError",
    "ImapLimitError",
    "ImapMessageIdError",
    "ImapReadOnlyProvider",
    "MockCursorError",
    "MockEmailProvider",
    "ProviderAuthenticationError",
    "ProviderCapabilities",
    "ProviderConnectionError",
    "ProviderMailboxError",
    "ProviderMessageError",
    "ProviderNotConfiguredError",
    "ProviderProtocolError",
    "ProviderTimeoutError",
    "ProviderTlsError",
    "provider_error_code",
    "UnsupportedEmailProviderError",
    "resolve_email_provider",
]
