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
