"""Explicit allowlist resolver for configured email providers."""

from __future__ import annotations

from ..config import EmailPluginConfig
from ..secrets import EnvironmentSecretResolver, SecretResolver
from .base import EmailProvider
from .imap import ImapReadOnlyProvider
from .mock import MockEmailProvider


class EmailProviderResolutionError(ValueError):
    """Base error for provider resolution failures."""


class ProviderNotConfiguredError(EmailProviderResolutionError):
    """Raised when configuration does not select a provider."""


class UnsupportedEmailProviderError(EmailProviderResolutionError):
    """Raised when a provider is not in the resolver's fixed allowlist."""


def resolve_email_provider(
    config: EmailPluginConfig,
    *,
    secret_resolver: SecretResolver | None = None,
) -> EmailProvider:
    """Create the explicitly configured provider without connecting it.

    Version 0.17.0 recognizes ``mock`` and read-only ``imap``. Configuration values are normalized
    as user-facing identifiers and are never interpreted as modules, classes,
    paths, entry points, or executable code.
    """
    configured_name = config.email.provider
    if configured_name is None or not configured_name.strip():
        raise ProviderNotConfiguredError("no email provider configured")

    normalized_name = configured_name.strip().casefold()
    if normalized_name == MockEmailProvider.NAME:
        return MockEmailProvider()
    if normalized_name == ImapReadOnlyProvider.NAME:
        resolver = (
            EnvironmentSecretResolver() if secret_resolver is None else secret_resolver
        )
        return ImapReadOnlyProvider(config.imap, resolver)

    raise UnsupportedEmailProviderError(
        f"unsupported email provider: {configured_name!r}; supported providers: imap, mock"
    )
