from pathlib import Path

import pytest

from hermes_email.config import ConfigError, EmailPluginConfig, load_config


def test_defaults_are_safe() -> None:
    config = EmailPluginConfig()

    assert config.email.provider is None
    assert config.email.read_mode == "disabled"
    assert config.email.draft_mode == "mock"
    assert config.hermes.profile == "auto"
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
