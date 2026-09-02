from pathlib import Path

import pytest

from hermes_email.config import ConfigError, EmailPluginConfig, load_config


def test_defaults_are_safe() -> None:
    config = EmailPluginConfig()

    assert config.email.provider is None
    assert config.email.read_mode == "disabled"
    assert config.email.draft_mode == "mock"
    assert getattr(config.hermes, "profile") == "auto"
    assert config.credentials.username_ref is None
    assert config.credentials.password_ref is None
    assert config.safety.allow_send is False
    assert config.safety.allow_delete is False
    assert config.safety.allow_move is False


def test_example_configuration_loads() -> None:
    path = Path(__file__).parents[1] / "examples" / "config.example.yaml"

    assert load_config(path) == EmailPluginConfig()


def test_unknown_configuration_key_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("safety:\n  allow_sned: true\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="allow_sned"):
        load_config(path)


def test_non_boolean_safety_value_is_rejected() -> None:
    with pytest.raises(ConfigError, match="allow_send must be a boolean"):
        EmailPluginConfig.from_mapping({"safety": {"allow_send": "false"}})


def test_credential_references_are_stored_without_values() -> None:
    config = EmailPluginConfig.from_mapping(
        {
            "credentials": {
                "username_ref": "HERMES_EMAIL_USERNAME",
                "password_ref": "HERMES_EMAIL_PASSWORD",
            }
        }
    )

    assert config.credentials.username_ref == "HERMES_EMAIL_USERNAME"
    assert config.credentials.password_ref == "HERMES_EMAIL_PASSWORD"
    assert "SYNTHETIC VALUE" not in repr(config)


@pytest.mark.parametrize(
    "field_name", ["username_ref", "password_ref"]
)
def test_invalid_credential_reference_is_rejected(field_name: str) -> None:
    with pytest.raises(ConfigError, match=f"credentials.{field_name}"):
        EmailPluginConfig.from_mapping(
            {"credentials": {field_name: "../synthetic-reference"}}
        )


def test_literal_credential_value_is_rejected_without_echo() -> None:
    sensitive_value = "synthetic inline value for rejection test"

    with pytest.raises(ConfigError) as captured:
        EmailPluginConfig.from_mapping(
            {"credentials": {"password_ref": sensitive_value}}
        )

    assert sensitive_value not in str(captured.value)
    assert sensitive_value not in repr(captured.value)
