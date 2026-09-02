"""Provider-neutral, lazy secret reference resolution."""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from typing import Protocol


_SECRET_REFERENCE = re.compile(r"HERMES_EMAIL_[A-Z0-9]+(?:_[A-Z0-9]+)*\Z")
_SECRET_REFERENCE_MAX_LENGTH = 128


class SecretResolutionError(ValueError):
    """Base error for safe secret reference resolution failures."""


class InvalidSecretReferenceError(SecretResolutionError):
    """Raised before an invalid reference can reach a secret source."""


class SecretNotFoundError(SecretResolutionError):
    """Raised when a valid reference has no available value."""


class SecretValue:
    """Process-local secret whose text and representation are always redacted."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if not isinstance(value, str) or not value:
            raise ValueError("secret value must be a non-empty string")
        self._value = value

    def reveal(self) -> str:
        """Return the value only to the provider operation that requires it."""
        return self._value

    def __repr__(self) -> str:
        return "SecretValue([REDACTED])"

    def __str__(self) -> str:
        return "[REDACTED]"

    def __reduce__(self) -> object:
        raise TypeError("SecretValue cannot be serialized")


class SecretResolver(Protocol):
    """Resolve one explicit reference without listing or persisting secrets."""

    def get_secret(self, reference: str) -> SecretValue:
        """Return one process-local secret or fail without exposing its value."""
        ...


def validate_secret_reference(reference: str) -> str:
    """Return a valid plugin-scoped environment reference unchanged."""
    if (
        not isinstance(reference, str)
        or len(reference) > _SECRET_REFERENCE_MAX_LENGTH
        or _SECRET_REFERENCE.fullmatch(reference) is None
    ):
        raise InvalidSecretReferenceError("invalid Hermes Email secret reference")
    return reference


class EnvironmentSecretResolver:
    """Read only an explicitly requested, validated environment reference."""

    def __init__(self, getter: Callable[[str], str | None] | None = None) -> None:
        self._getter = os.environ.get if getter is None else getter

    def get_secret(self, reference: str) -> SecretValue:
        """Resolve one environment value without expansion, enumeration, or caching."""
        validated_reference = validate_secret_reference(reference)
        value = self._getter(validated_reference)
        if value is None or value == "":
            raise SecretNotFoundError(
                f"secret reference is not available: {validated_reference}"
            )
        return SecretValue(value)
