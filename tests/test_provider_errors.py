from hermes_email.providers import (
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


def test_provider_error_codes_are_fixed_and_specific() -> None:
    cases = (
        (ProviderAuthenticationError("private"), "authentication-failed"),
        (ProviderTlsError("private"), "tls-failed"),
        (ProviderTimeoutError("private"), "provider-timeout"),
        (ProviderConnectionError("private"), "provider-unreachable"),
        (ProviderMailboxError("private"), "mailbox-unavailable"),
        (ProviderProtocolError("private"), "protocol-error"),
        (ProviderMessageError("private"), "message-error"),
        (EmailProviderError("private"), "provider-error"),
    )
    for error, expected in cases:
        assert provider_error_code(error) == expected
        assert "private" not in provider_error_code(error)
