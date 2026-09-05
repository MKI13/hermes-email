"""Deterministic, operator-configured sender classification."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .addressing import AddressValidationError, canonical_address
from .config import SenderClassificationSettings
from .models import EmailAddress


class SenderCategory(StrEnum):
    INTERNAL = "internal"
    CUSTOMER = "customer"
    SUPPLIER = "supplier"
    UNKNOWN_EXTERNAL = "unknown-external"


@dataclass(frozen=True, slots=True)
class SenderClassification:
    category: SenderCategory
    matched_by: str
    authorization: str = "none"


def classify_sender(
    sender: EmailAddress, settings: SenderClassificationSettings
) -> SenderClassification:
    """Classify one sender using exact-address rules before domain rules.

    Classification is informational only and never grants action authority.
    """
    try:
        address = canonical_address(sender.address)
    except AddressValidationError:
        return SenderClassification(SenderCategory.UNKNOWN_EXTERNAL, "invalid-address")

    address_rules = (
        (settings.internal_addresses, SenderCategory.INTERNAL),
        (settings.customer_addresses, SenderCategory.CUSTOMER),
        (settings.supplier_addresses, SenderCategory.SUPPLIER),
    )
    for values, category in address_rules:
        if address in values:
            return SenderClassification(category, "address")

    domain = address.rsplit("@", 1)[1]
    domain_rules = (
        (settings.internal_domains, SenderCategory.INTERNAL),
        (settings.customer_domains, SenderCategory.CUSTOMER),
        (settings.supplier_domains, SenderCategory.SUPPLIER),
    )
    for values, category in domain_rules:
        if domain in values:
            return SenderClassification(category, "domain")

    return SenderClassification(SenderCategory.UNKNOWN_EXTERNAL, "default")
