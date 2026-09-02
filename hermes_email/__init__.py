"""Universal Hermes email plugin foundation."""

from .config import CredentialReferences, EmailPluginConfig, load_config
from .context import ActiveProfileContextSource, HermesContext, HermesContextSource
from .secrets import (
    EnvironmentSecretResolver,
    InvalidSecretReferenceError,
    SecretNotFoundError,
    SecretResolutionError,
    SecretResolver,
    SecretValue,
    validate_secret_reference,
)

__all__ = [
    "ActiveProfileContextSource",
    "CredentialReferences",
    "EmailPluginConfig",
    "EnvironmentSecretResolver",
    "HermesContext",
    "HermesContextSource",
    "InvalidSecretReferenceError",
    "SecretNotFoundError",
    "SecretResolutionError",
    "SecretResolver",
    "SecretValue",
    "load_config",
    "validate_secret_reference",
]
__version__ = "0.13.0"
