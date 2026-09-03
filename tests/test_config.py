from pathlib import Path

import pytest

from hermes_email.config import (
    ConfigError,
    DraftSettings,
    EmailPluginConfig,
    ImapSettings,
    StorageSettings,
    load_config,
)


def test_defaults_are_safe() -> None:
    config = EmailPluginConfig()

    assert config.email.provider is None
    assert config.email.read_mode == "disabled"
    assert getattr(config.hermes, "profile") == "auto"
    assert config.credentials.username_ref is None
    assert config.credentials.password_ref is None
    assert config.imap.host is None
    assert config.imap.port == 993
    assert config.imap.security == "tls"
    assert config.imap.mailbox == "INBOX"
    assert config.storage == StorageSettings()
    assert config.storage.mode == "disabled"
    assert config.storage.account_namespace is None
    assert config.drafts == DraftSettings()
    assert config.drafts.mode == "disabled"
    assert config.drafts.account_namespace is None
    assert config.safety.allow_send is False
    assert config.safety.allow_delete is False
    assert config.safety.allow_move is False


def test_example_configuration_loads() -> None:
    path = Path(__file__).parents[1] / "examples" / "config.example.yaml"

    assert load_config(path) == EmailPluginConfig()


@pytest.mark.parametrize("value", [False, 0, [], ""])
def test_falsey_non_mapping_root_is_rejected(value) -> None:
    with pytest.raises(ConfigError, match="configuration root"):
        EmailPluginConfig.from_mapping(value)


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


def valid_imap_mapping() -> dict[str, object]:
    return {
        "email": {"provider": "imap", "read_mode": "readonly"},
        "imap": {
            "host": "mail.example.invalid",
            "port": 993,
            "security": "tls",
            "username_ref": "HERMES_EMAIL_IMAP_USERNAME",
            "password_ref": "HERMES_EMAIL_IMAP_PASSWORD",
            "mailbox": "INBOX",
        },
    }


def test_valid_imap_configuration_contains_references_only() -> None:
    config = EmailPluginConfig.from_mapping(valid_imap_mapping())

    assert config.email.provider == "imap"
    assert config.email.read_mode == "readonly"
    assert config.imap == ImapSettings(
        host="mail.example.invalid",
        username_ref="HERMES_EMAIL_IMAP_USERNAME",
        password_ref="HERMES_EMAIL_IMAP_PASSWORD",
    )


@pytest.mark.parametrize(
    "host",
    ["", " mail.example.invalid", "imap://mail.example.invalid", "user@host", "../host", "bad\r\nhost", "máil.invalid", "999.999.1.1"],
)
def test_invalid_imap_hosts_are_rejected(host: str) -> None:
    with pytest.raises(ConfigError, match="imap.host"):
        ImapSettings(host=host)


@pytest.mark.parametrize("security", ["", "plain", "starttls", "TLS", None, False])
def test_only_implicit_tls_security_is_allowed(security: object) -> None:
    with pytest.raises(ConfigError, match="imap.security"):
        ImapSettings(security=security)  # type: ignore[arg-type]


@pytest.mark.parametrize("port", [0, 65_536, -1, True, "993", None])
def test_invalid_imap_ports_are_rejected(port: object) -> None:
    with pytest.raises(ConfigError, match="imap.port"):
        ImapSettings(port=port)  # type: ignore[arg-type]


@pytest.mark.parametrize("mailbox", ["", "bad\nmailbox", "bad\x00mailbox", "Posteingang-ä"])
def test_unsafe_imap_mailbox_names_are_rejected(mailbox: str) -> None:
    with pytest.raises(ConfigError, match="imap.mailbox"):
        ImapSettings(mailbox=mailbox)


def test_imap_credential_references_must_be_valid_and_complete() -> None:
    with pytest.raises(ConfigError, match="configured together"):
        ImapSettings(username_ref="HERMES_EMAIL_IMAP_USERNAME")
    with pytest.raises(ConfigError, match="imap.password_ref"):
        ImapSettings(
            username_ref="HERMES_EMAIL_IMAP_USERNAME",
            password_ref="${SYNTHETIC}",
        )


def test_imap_provider_requires_complete_section() -> None:
    with pytest.raises(ConfigError, match="imap.host"):
        EmailPluginConfig.from_mapping(
            {"email": {"provider": "imap", "read_mode": "readonly"}}
        )


def test_imap_provider_rejects_mock_read_mode() -> None:
    mapping = valid_imap_mapping()
    mapping["email"] = {"provider": "imap", "read_mode": "mock"}

    with pytest.raises(ConfigError, match="read_mode"):
        EmailPluginConfig.from_mapping(mapping)


def test_mock_provider_rejects_readonly_mode() -> None:
    with pytest.raises(ConfigError, match="readonly"):
        EmailPluginConfig.from_mapping(
            {"email": {"provider": "mock", "read_mode": "readonly"}}
        )


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("timeout_seconds", 0),
        ("timeout_seconds", 121),
        ("max_mailbox_messages", 0),
        ("max_mailbox_messages", 50_001),
        ("max_message_bytes", 4_095),
        ("max_message_bytes", 10_000_001),
        ("max_page_bytes", 409_599),
        ("max_page_bytes", 20_000_001),
    ],
)
def test_imap_resource_bounds_are_enforced(field_name: str, value: int) -> None:
    with pytest.raises(ConfigError, match=field_name):
        ImapSettings(**{field_name: value})


def test_valid_local_draft_configuration_is_provider_independent() -> None:
    config = EmailPluginConfig.from_mapping(
        {
            "drafts": {
                "mode": "sqlite",
                "account_namespace": "primary-account",
                "max_drafts": 500,
                "max_operations": 5000,
                "max_database_bytes": 33554432,
            }
        }
    )

    assert config.email.provider is None
    assert config.email.read_mode == "disabled"
    assert config.drafts.mode == "sqlite"
    assert config.drafts.account_namespace == "primary-account"


def test_local_drafts_require_portable_account_namespace() -> None:
    with pytest.raises(ConfigError, match="account_namespace is required"):
        EmailPluginConfig.from_mapping({"drafts": {"mode": "sqlite"}})
    with pytest.raises(ConfigError, match="portable identifier"):
        DraftSettings(mode="sqlite", account_namespace="private address@example.invalid")


@pytest.mark.parametrize("field_name", ["allow_send", "allow_delete", "allow_move"])
def test_unavailable_external_or_mailbox_side_effect_opt_in_is_rejected(
    field_name: str,
) -> None:
    with pytest.raises(ConfigError, match="unavailable"):
        EmailPluginConfig.from_mapping({"safety": {field_name: True}})


def test_legacy_provider_draft_mode_is_rejected() -> None:
    with pytest.raises(ConfigError, match="draft_mode"):
        EmailPluginConfig.from_mapping({"email": {"draft_mode": "mock"}})


def test_draft_resource_bounds_reject_booleans_and_oversize_values() -> None:
    with pytest.raises(ConfigError, match="max_drafts"):
        DraftSettings(max_drafts=True)
    with pytest.raises(ConfigError, match="max_operations"):
        DraftSettings(max_operations=100_001)
    with pytest.raises(ConfigError, match="max_database_bytes"):
        DraftSettings(max_database_bytes=1_048_575)


def test_explicit_sqlite_storage_configuration_is_valid() -> None:
    config = EmailPluginConfig.from_mapping(
        {
            "email": {"provider": "mock", "read_mode": "mock"},
            "storage": {
                "mode": "sqlite",
                "account_namespace": "primary-inbox",
                "retention_days": 30,
                "max_observations": 500,
                "max_database_bytes": 4_194_304,
            },
        }
    )

    assert config.storage == StorageSettings(
        mode="sqlite",
        account_namespace="primary-inbox",
        retention_days=30,
        max_observations=500,
        max_database_bytes=4_194_304,
    )


@pytest.mark.parametrize(
    "namespace",
    ["", " space", "contains space", "../escape", "a" * 65, 123],
)
def test_storage_account_namespace_is_a_bounded_portable_slug(namespace) -> None:
    with pytest.raises(ConfigError, match="account_namespace"):
        StorageSettings(mode="sqlite", account_namespace=namespace)


def test_sqlite_storage_requires_namespace_and_readable_provider() -> None:
    with pytest.raises(ConfigError, match="account_namespace"):
        StorageSettings(mode="sqlite")
    with pytest.raises(ConfigError, match="readable email provider"):
        EmailPluginConfig.from_mapping(
            {
                "storage": {
                    "mode": "sqlite",
                    "account_namespace": "primary-inbox",
                }
            }
        )


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("retention_days", 0),
        ("retention_days", 3_651),
        ("max_observations", 0),
        ("max_observations", 100_001),
        ("max_database_bytes", 1_048_575),
        ("max_database_bytes", 1_073_741_825),
    ],
)
def test_storage_resource_bounds_are_enforced(field_name: str, value: int) -> None:
    with pytest.raises(ConfigError, match=field_name):
        StorageSettings(**{field_name: value})
