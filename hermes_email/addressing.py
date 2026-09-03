"""Shared normalization for ASCII email addresses and safe display names."""

from __future__ import annotations

import re
import unicodedata
from typing import Final

_MAX_ADDRESS_CHARACTERS: Final = 254
_MAX_LOCAL_PART_CHARACTERS: Final = 64
_MAX_DISPLAY_NAME_CHARACTERS: Final = 200
_LOCAL_PART_PATTERN: Final = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+")
_DOMAIN_PATTERN: Final = re.compile(
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)"
    r"(?:\.(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?))*"
)


class AddressValidationError(ValueError):
    """Raised when an address or display name is outside the supported subset."""


def normalize_ascii_address(value: object) -> str:
    """Return one syntactically valid ASCII addr-spec without case rewriting."""
    if not isinstance(value, str):
        raise AddressValidationError("email address must be text")
    address = unicodedata.normalize("NFC", value)
    if (
        not address
        or len(address) > _MAX_ADDRESS_CHARACTERS
        or len(address.encode("utf-8")) > 1_280
        or not address.isascii()
        or address.strip() != address
        or address.count("@") != 1
        or _contains_unsupported_controls(address)
    ):
        raise AddressValidationError("email address syntax is unsupported")
    local, domain = address.rsplit("@", 1)
    if (
        len(local) > _MAX_LOCAL_PART_CHARACTERS
        or not _LOCAL_PART_PATTERN.fullmatch(local)
        or local.startswith(".")
        or local.endswith(".")
        or ".." in local
        or not _DOMAIN_PATTERN.fullmatch(domain)
        or len(domain) > 253
    ):
        raise AddressValidationError("email address syntax is unsupported")
    return address


def canonical_address(value: str) -> str:
    """Return the duplicate-comparison form with domain-only case folding."""
    address = normalize_ascii_address(value)
    local, domain = address.rsplit("@", 1)
    return local + "@" + domain.casefold()


def normalize_display_name(value: object) -> str:
    """Return one non-empty NFC display name without control code points."""
    if not isinstance(value, str):
        raise AddressValidationError("display name must be text")
    normalized = unicodedata.normalize("NFC", value)
    if (
        not normalized
        or len(normalized) > _MAX_DISPLAY_NAME_CHARACTERS
        or len(normalized.encode("utf-8")) > 800
        or _contains_unsupported_controls(normalized)
    ):
        raise AddressValidationError("display name is invalid")
    return normalized


def _contains_unsupported_controls(value: str) -> bool:
    return any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in value)
