from datetime import UTC, datetime
from email import policy
from email.parser import BytesParser
from pathlib import Path

import pytest

from hermes_email.config import EmailPluginConfig
from hermes_email.draft_storage import (
    DraftConflictError,
    DraftStateError,
    SqliteDraftStore,
)
from hermes_email.models import EmailAddress, EmailDraft
from hermes_email.sending import (
    SendGateAccountError,
    SendGateConfirmationError,
    SendGateDisabledError,
    SendGateMessageError,
    SendGateRecipientError,
    UserSendConfirmation,
    prepare_send_candidate,
)

DATE = datetime(2026, 9, 8, 12, 30, tzinfo=UTC)
MESSAGE_ID = "<hermes-operation-0001@example.invalid>"
CONFIRMATION_ID = "confirm-operation-0001"


def config(*, policy_mode: str = "all", max_bytes: int = 1_000_000) -> EmailPluginConfig:
    recipient_policy = {"mode": policy_mode}
    if policy_mode == "allowlist":
        recipient_policy["allowed_domains"] = ["example.invalid"]
    return EmailPluginConfig.from_mapping(
        {
            "drafts": {"mode": "sqlite", "account_namespace": "smtp-account"},
            "smtp": {
                "mode": "submission",
                "account_namespace": "smtp-account",
                "host": "smtp.example.invalid",
                "port": 465,
                "security": "implicit_tls",
                "username_ref": "HERMES_EMAIL_SMTP_USERNAME",
                "password_ref": "HERMES_EMAIL_SMTP_PASSWORD",
                "sender_address": "sender@example.invalid",
                "sender_display_name": "Universal Sender",
                "max_message_bytes": max_bytes,
            },
            "recipient_policy": recipient_policy,
            "safety": {"allow_send": True},
        }
    )


def draft() -> EmailDraft:
    return EmailDraft(
        recipients=(EmailAddress("to@example.invalid", "Tö Recipient"),),
        cc=(EmailAddress("copy@example.invalid"),),
        bcc=(EmailAddress("private@example.invalid", "Private"),),
        subject="Héllo from Hermes",
        body_text="Plain text only.\nUnicode: café.",
        in_reply_to="provider-local-message-id",
    )


def store(tmp_path: Path, configuration: EmailPluginConfig | None = None) -> SqliteDraftStore:
    value = configuration or config()
    return SqliteDraftStore(tmp_path / "plugin-data" / "email-drafts.sqlite3", value.drafts)


def create(store: SqliteDraftStore) -> str:
    return store.create_draft(draft(), "create-operation-0001").draft_id


def confirmation(draft_id: str, revision: int = 1) -> UserSendConfirmation:
    return UserSendConfirmation(draft_id, revision, CONFIRMATION_ID)


def prepare(configuration: EmailPluginConfig, drafts: SqliteDraftStore, draft_id: str):
    return prepare_send_candidate(
        configuration,
        drafts,
        draft_id=draft_id,
        expected_revision=1,
        message_id=MESSAGE_ID,
        message_date=DATE,
        confirmation=confirmation(draft_id),
    )


def test_requires_explicit_confirmation_before_draft_access(tmp_path: Path) -> None:
    configuration = config()
    drafts = store(tmp_path, configuration)
    draft_id = "draft_" + "a" * 32

    with pytest.raises(SendGateConfirmationError, match="confirmation"):
        prepare_send_candidate(
            configuration,
            drafts,
            draft_id=draft_id,
            expected_revision=1,
            message_id=MESSAGE_ID,
            message_date=DATE,
        )
    assert drafts.path.exists() is False


def test_confirmation_must_match_exact_draft_and_revision(tmp_path: Path) -> None:
    configuration = config()
    drafts = store(tmp_path, configuration)
    draft_id = create(drafts)

    with pytest.raises(SendGateConfirmationError):
        prepare_send_candidate(
            configuration,
            drafts,
            draft_id=draft_id,
            expected_revision=1,
            message_id=MESSAGE_ID,
            message_date=DATE,
            confirmation=confirmation("draft_" + "b" * 32),
        )
    with pytest.raises(SendGateConfirmationError):
        prepare_send_candidate(
            configuration,
            drafts,
            draft_id=draft_id,
            expected_revision=1,
            message_id=MESSAGE_ID,
            message_date=DATE,
            confirmation=confirmation(draft_id, 2),
        )


def test_prepares_deterministic_plain_text_bytes_and_complete_envelope(tmp_path: Path) -> None:
    configuration = config()
    drafts = store(tmp_path, configuration)
    draft_id = create(drafts)
    first = prepare(configuration, drafts, draft_id)
    second = prepare(configuration, drafts, draft_id)
    parsed = BytesParser(policy=policy.default).parsebytes(first.message_bytes)

    assert first == second
    assert first.confirmation_id == CONFIRMATION_ID
    assert first.account_namespace == "smtp-account"
    assert first.envelope_sender == "sender@example.invalid"
    assert first.envelope_recipients == ("to@example.invalid", "copy@example.invalid", "private@example.invalid")
    assert parsed["From"].addresses[0].addr_spec == "sender@example.invalid"
    assert parsed["To"].addresses[0].display_name == "Tö Recipient"
    assert parsed["Cc"].addresses[0].addr_spec == "copy@example.invalid"
    assert parsed["Bcc"] is None
    assert parsed["Subject"] == "Héllo from Hermes"
    assert parsed["Message-ID"] == MESSAGE_ID
    assert parsed["In-Reply-To"] is None
    assert parsed.get_content_type() == "text/plain"
    assert parsed.get_content().replace("\r\n", "\n").rstrip("\n") == "Plain text only.\nUnicode: café."
    assert b"private@example.invalid" not in first.message_bytes


def test_disabled_gate_rejects_before_confirmation_or_database(tmp_path: Path) -> None:
    configuration = EmailPluginConfig.from_mapping({"drafts": {"mode": "sqlite", "account_namespace": "smtp-account"}})
    drafts = store(tmp_path, configuration)
    with pytest.raises(SendGateDisabledError):
        prepare_send_candidate(
            configuration,
            drafts,
            draft_id="draft_" + "a" * 32,
            expected_revision=1,
            message_id=MESSAGE_ID,
            message_date=DATE,
        )
    assert drafts.path.exists() is False


def test_gate_requires_exact_active_revision(tmp_path: Path) -> None:
    configuration = config()
    drafts = store(tmp_path, configuration)
    draft_id = create(drafts)
    drafts.update_draft(draft_id, 1, draft(), "update-operation-0001")

    with pytest.raises(DraftConflictError) as stale:
        prepare_send_candidate(
            configuration,
            drafts,
            draft_id=draft_id,
            expected_revision=1,
            message_id=MESSAGE_ID,
            message_date=DATE,
            confirmation=confirmation(draft_id, 1),
        )
    assert stale.value.current_revision == 2

    drafts.trash_draft(draft_id, 2, "trash-operation-0001")
    with pytest.raises(DraftStateError):
        prepare_send_candidate(
            configuration,
            drafts,
            draft_id=draft_id,
            expected_revision=3,
            message_id=MESSAGE_ID,
            message_date=DATE,
            confirmation=confirmation(draft_id, 3),
        )


def test_gate_binds_store_account_namespace(tmp_path: Path) -> None:
    configuration = config()
    other = SqliteDraftStore(
        tmp_path / "plugin-data" / "email-drafts.sqlite3",
        configuration.drafts.__class__(mode="sqlite", account_namespace="other-account"),
    )
    draft_id = "draft_" + "a" * 32
    with pytest.raises(SendGateAccountError):
        prepare_send_candidate(
            configuration,
            other,
            draft_id=draft_id,
            expected_revision=1,
            message_id=MESSAGE_ID,
            message_date=DATE,
            confirmation=confirmation(draft_id),
        )
    assert other.path.exists() is False


def test_recipient_policy_checks_to_cc_and_bcc(tmp_path: Path) -> None:
    configuration = config(policy_mode="allowlist")
    drafts = store(tmp_path, configuration)
    blocked = EmailDraft(
        recipients=(EmailAddress("to@example.invalid"),),
        cc=(),
        bcc=(EmailAddress("private@blocked.invalid"),),
        subject="Blocked Bcc",
        body_text="No submission.",
    )
    draft_id = drafts.create_draft(blocked, "create-operation-0001").draft_id
    with pytest.raises(SendGateRecipientError):
        prepare(configuration, drafts, draft_id)


def test_message_id_date_and_final_byte_cap_fail_closed(tmp_path: Path) -> None:
    configuration = config(max_bytes=1024)
    drafts = store(tmp_path, configuration)
    large = EmailDraft(recipients=(EmailAddress("to@example.invalid"),), subject="Large", body_text="x" * 5000)
    draft_id = drafts.create_draft(large, "create-operation-0001").draft_id

    with pytest.raises(SendGateMessageError, match="byte limit"):
        prepare(configuration, drafts, draft_id)
    with pytest.raises(SendGateMessageError, match="Message-ID"):
        prepare_send_candidate(
            configuration,
            drafts,
            draft_id=draft_id,
            expected_revision=1,
            message_id="bad\r\nBcc: attacker@example.invalid",
            message_date=DATE,
            confirmation=confirmation(draft_id),
        )
    with pytest.raises(SendGateMessageError, match="timezone-aware"):
        prepare_send_candidate(
            configuration,
            drafts,
            draft_id=draft_id,
            expected_revision=1,
            message_id=MESSAGE_ID,
            message_date=datetime(2026, 9, 8),
            confirmation=confirmation(draft_id),
        )


def test_candidate_is_owned_snapshot_not_changed_by_later_draft_update(tmp_path: Path) -> None:
    configuration = config()
    drafts = store(tmp_path, configuration)
    draft_id = create(drafts)
    candidate = prepare(configuration, drafts, draft_id)
    drafts.update_draft(
        draft_id,
        1,
        EmailDraft(recipients=(EmailAddress("other@example.invalid"),), subject="Changed", body_text="Changed"),
        "update-operation-0001",
    )
    assert candidate.revision == 1
    assert candidate.envelope_recipients[0] == "to@example.invalid"
    assert b"Changed" not in candidate.message_bytes
