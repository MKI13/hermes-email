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
)
from .imap import (
    ImapCursorError,
    ImapLimitError,
    ImapMessageIdError,
    ImapReadOnlyProvider,
    ImapWriteBlockedError,
)
from .mock import MockCursorError, MockEmailProvider, MockSendBlockedError
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
    "ImapWriteBlockedError",
    "MockCursorError",
    "MockEmailProvider",
    "MockSendBlockedError",
    "ProviderAuthenticationError",
    "ProviderCapabilities",
    "ProviderConnectionError",
    "ProviderMailboxError",
    "ProviderMessageError",
    "ProviderNotConfiguredError",
    "ProviderProtocolError",
    "ProviderTimeoutError",
    "ProviderTlsError",
    "UnsupportedEmailProviderError",
    "resolve_email_provider",
]
