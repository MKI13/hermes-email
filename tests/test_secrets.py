import builtins
import importlib
import socket

import pytest

from hermes_email.secrets import (
    EnvironmentSecretResolver,
    InvalidSecretReferenceError,
    SecretNotFoundError,
    SecretValue,
    validate_secret_reference,
)


class TargetedEnvironment:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values
        self.requested: list[str] = []

    def get(self, key: str, default=None):
        self.requested.append(key)
        return self.values.get(key, default)

    def __iter__(self):
        raise AssertionError("environment enumeration is forbidden")


@pytest.mark.parametrize(
    "reference",
    [
        "HERMES_EMAIL_USERNAME",
        "HERMES_EMAIL_PASSWORD",
        "HERMES_EMAIL_PROVIDER_2_VALUE",
    ],
)
def test_valid_secret_reference_is_accepted_unchanged(reference: str) -> None:
    assert validate_secret_reference(reference) == reference


def test_secret_reference_length_boundary() -> None:
    prefix = "HERMES_EMAIL_"
    maximum_reference = prefix + ("A" * (128 - len(prefix)))
    oversized_reference = maximum_reference + "A"

    assert validate_secret_reference(maximum_reference) == maximum_reference
    with pytest.raises(InvalidSecretReferenceError):
        validate_secret_reference(oversized_reference)


@pytest.mark.parametrize(
    "reference",
    [
        "",
        "HOME",
        "../synthetic",
        "/synthetic",
        "${SYNTHETIC}",
        "$(synthetic)",
        "foo.bar",
        "foo/bar",
        "HERMES_EMAIL__VALUE",
        "HERMES_EMAIL_VALUE_",
    ],
)
def test_invalid_secret_reference_is_blocked(reference: str) -> None:
    with pytest.raises(
        InvalidSecretReferenceError, match="invalid Hermes Email secret reference"
    ):
        validate_secret_reference(reference)


def test_non_string_secret_reference_is_blocked() -> None:
    with pytest.raises(InvalidSecretReferenceError):
        validate_secret_reference(123)  # type: ignore[arg-type]


def test_invalid_reference_never_reaches_environment() -> None:
    invalid_reference = "${SYNTHETIC}"
    sensitive_value = "SYNTHETIC VALUE BEHIND INVALID REFERENCE"
    environment = TargetedEnvironment({invalid_reference: sensitive_value})

    with pytest.raises(InvalidSecretReferenceError) as captured:
        EnvironmentSecretResolver(environment.get).get_secret(invalid_reference)

    assert environment.requested == []
    assert sensitive_value not in str(captured.value)


def test_default_environment_resolver_reads_process_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reference = "HERMES_EMAIL_DEFAULT_TEST"
    sensitive_value = "SYNTHETIC VALUE FROM PROCESS ENVIRONMENT"
    monkeypatch.setenv(reference, sensitive_value)

    resolved = EnvironmentSecretResolver().get_secret(reference)

    assert resolved.reveal() == sensitive_value


def test_environment_resolver_reads_only_requested_reference() -> None:
    reference = "HERMES_EMAIL_TEST_VALUE"
    sensitive_value = "SYNTHETIC VALUE FOR TEST ONLY"
    environment = TargetedEnvironment({reference: sensitive_value, "OTHER": "ignored"})

    resolved = EnvironmentSecretResolver(environment.get).get_secret(reference)

    assert resolved.reveal() == sensitive_value
    assert environment.requested == [reference]


def test_environment_resolver_does_not_cache_values() -> None:
    reference = "HERMES_EMAIL_ROTATING_VALUE"
    environment = TargetedEnvironment({reference: "SYNTHETIC VALUE ONE"})
    resolver = EnvironmentSecretResolver(environment.get)

    first = resolver.get_secret(reference)
    environment.values[reference] = "SYNTHETIC VALUE TWO"
    second = resolver.get_secret(reference)

    assert first.reveal() == "SYNTHETIC VALUE ONE"
    assert second.reveal() == "SYNTHETIC VALUE TWO"
    assert environment.requested == [reference, reference]


def test_missing_secret_error_contains_reference_but_no_value() -> None:
    reference = "HERMES_EMAIL_MISSING_VALUE"
    environment = TargetedEnvironment({})

    with pytest.raises(SecretNotFoundError) as captured:
        EnvironmentSecretResolver(environment.get).get_secret(reference)

    assert str(captured.value) == f"secret reference is not available: {reference}"
    assert environment.requested == [reference]


def test_secret_value_redacts_string_and_representation() -> None:
    sensitive_value = "SYNTHETIC VALUE FOR REDACTION TEST"
    resolved = SecretValue(sensitive_value)

    assert sensitive_value not in repr(resolved)
    assert sensitive_value not in str(resolved)
    assert repr(resolved) == "SecretValue([REDACTED])"
    assert str(resolved) == "[REDACTED]"
    assert resolved.reveal() == sensitive_value


def test_secret_value_cannot_be_serialized() -> None:
    resolved = SecretValue("SYNTHETIC VALUE FOR SERIALIZATION TEST")

    with pytest.raises(TypeError, match="cannot be serialized"):
        resolved.__reduce__()


def test_resolution_logs_no_secret_value(
    caplog: pytest.LogCaptureFixture,
) -> None:
    reference = "HERMES_EMAIL_LOG_TEST"
    sensitive_value = "SYNTHETIC VALUE FOR LOG TEST"
    environment = TargetedEnvironment({reference: sensitive_value})

    EnvironmentSecretResolver(environment.get).get_secret(reference)

    assert sensitive_value not in caplog.text
    assert reference not in caplog.text


def test_resolution_performs_no_file_import_or_network_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reference = "HERMES_EMAIL_ISOLATION_TEST"
    environment = TargetedEnvironment({reference: "SYNTHETIC ISOLATED VALUE"})

    def forbidden(*args, **kwargs):
        raise AssertionError(f"unexpected external operation: {args!r} {kwargs!r}")

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(importlib, "import_module", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)

    resolved = EnvironmentSecretResolver(environment.get).get_secret(reference)

    assert resolved.reveal() == "SYNTHETIC ISOLATED VALUE"
