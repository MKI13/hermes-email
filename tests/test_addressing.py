import pytest

from hermes_email.addressing import (
    AddressValidationError,
    canonical_address,
    normalize_ascii_address,
    normalize_display_name,
)


def test_address_normalization_preserves_local_part_and_folds_domain_only() -> None:
    address = normalize_ascii_address("Case.Local@EXAMPLE.Invalid")

    assert address == "Case.Local@EXAMPLE.Invalid"
    assert canonical_address(address) == "Case.Local@example.invalid"


@pytest.mark.parametrize(
    "address",
    [
        "a" * 65 + "@example.invalid",
        ".start@example.invalid",
        "end.@example.invalid",
        "two..dots@example.invalid",
        "person@-example.invalid",
        "person@example..invalid",
        "tést@example.invalid",
        "person@example.invalid\r\nBcc: bad@example.invalid",
    ],
)
def test_address_normalization_rejects_unsupported_smtp_forms(address: str) -> None:
    with pytest.raises(AddressValidationError):
        normalize_ascii_address(address)


def test_display_name_normalizes_unicode_and_rejects_controls() -> None:
    assert normalize_display_name("Cafe\u0301") == "Café"
    with pytest.raises(AddressValidationError):
        normalize_display_name("Name\nBcc")
