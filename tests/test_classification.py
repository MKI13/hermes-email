import pytest

from hermes_email.classification import SenderCategory, classify_sender
from hermes_email.config import ConfigError, SenderClassificationSettings
from hermes_email.models import EmailAddress


def test_defaults_classify_every_sender_as_unknown_external() -> None:
    result = classify_sender(EmailAddress("person@example.invalid"), SenderClassificationSettings())
    assert result.category is SenderCategory.UNKNOWN_EXTERNAL
    assert result.matched_by == "default"
    assert result.authorization == "none"


def test_exact_address_rule_wins_over_domain_rule() -> None:
    settings = SenderClassificationSettings(
        customer_addresses=("special@vendor.invalid",),
        supplier_domains=("vendor.invalid",),
    )
    exact = classify_sender(EmailAddress("special@vendor.invalid"), settings)
    general = classify_sender(EmailAddress("other@vendor.invalid"), settings)
    assert (exact.category, exact.matched_by) == (SenderCategory.CUSTOMER, "address")
    assert (general.category, general.matched_by) == (SenderCategory.SUPPLIER, "domain")


def test_internal_customer_supplier_and_unknown_are_deterministic() -> None:
    settings = SenderClassificationSettings(
        internal_domains=("company.invalid",),
        customer_domains=("customer.invalid",),
        supplier_addresses=("sales@supplier.invalid",),
    )
    assert classify_sender(EmailAddress("me@company.invalid"), settings).category is SenderCategory.INTERNAL
    assert classify_sender(EmailAddress("a@customer.invalid"), settings).category is SenderCategory.CUSTOMER
    assert classify_sender(EmailAddress("sales@supplier.invalid"), settings).category is SenderCategory.SUPPLIER
    assert classify_sender(EmailAddress("other@outside.invalid"), settings).category is SenderCategory.UNKNOWN_EXTERNAL


def test_classification_normalizes_addresses_and_requires_lowercase_domains() -> None:
    settings = SenderClassificationSettings(customer_addresses=("Case@EXAMPLE.invalid",))
    assert settings.customer_addresses == ("Case@example.invalid",)
    with pytest.raises(ConfigError, match="lowercase ASCII"):
        SenderClassificationSettings(customer_domains=("Example.invalid",))


def test_same_exact_rule_cannot_belong_to_multiple_categories() -> None:
    with pytest.raises(ConfigError, match="multiple categories"):
        SenderClassificationSettings(
            customer_addresses=("same@example.invalid",),
            supplier_addresses=("same@example.invalid",),
        )
    with pytest.raises(ConfigError, match="multiple categories"):
        SenderClassificationSettings(
            internal_domains=("same.invalid",),
            customer_domains=("same.invalid",),
        )


def test_invalid_or_unbounded_rules_fail_closed() -> None:
    with pytest.raises(ConfigError, match="invalid address"):
        SenderClassificationSettings(customer_addresses=("not-an-address",))
    with pytest.raises(ConfigError, match="bounded string list"):
        SenderClassificationSettings(customer_domains=tuple(f"d{i}.invalid" for i in range(201)))
