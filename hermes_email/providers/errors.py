"""Provider-neutral failures for bounded email operations."""


class EmailProviderError(RuntimeError):
    """Base class for expected provider operation failures."""


class ProviderConnectionError(EmailProviderError):
    """Raised when a provider endpoint cannot be reached."""


class ProviderAuthenticationError(EmailProviderError):
    """Raised when provider authentication cannot complete."""


class ProviderTlsError(ProviderConnectionError):
    """Raised when verified transport security cannot be established."""


class ProviderMailboxError(EmailProviderError):
    """Raised when the configured mailbox cannot be opened safely."""


class ProviderProtocolError(EmailProviderError):
    """Raised for malformed or rejected provider protocol responses."""


class ProviderTimeoutError(ProviderConnectionError):
    """Raised when a bounded provider operation times out."""


class ProviderMessageError(EmailProviderError):
    """Raised when one remote message cannot be retrieved safely."""

def provider_error_code(error: BaseException) -> str:
    """Return one fixed non-sensitive provider failure code."""
    if isinstance(error, ProviderAuthenticationError):
        return "authentication-failed"
    if isinstance(error, ProviderTlsError):
        return "tls-failed"
    if isinstance(error, ProviderTimeoutError):
        return "provider-timeout"
    if isinstance(error, ProviderConnectionError):
        return "provider-unreachable"
    if isinstance(error, ProviderMailboxError):
        return "mailbox-unavailable"
    if isinstance(error, ProviderProtocolError):
        return "protocol-error"
    if isinstance(error, ProviderMessageError):
        return "message-error"
    if isinstance(error, EmailProviderError):
        return "provider-error"
    raise TypeError("error is not an EmailProviderError")
