import os
import sqlite3
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from hermes_email.config import DraftSettings
from hermes_email.draft_storage import (
    DraftConflictError,
    DraftLimitError,
    DraftStateError,
    DraftStorageClosedError,
    DraftStorageResourceError,
    DraftStorageSchemaError,
    DraftStorageSecurityError,
    DraftValidationError,
    SqliteDraftStore,
    validate_draft_content,
)
from hermes_email.models import EmailAddress, EmailDraft


def settings(**overrides) -> DraftSettings:
    values = {
        "mode": "sqlite",
        "account_namespace": "primary-account",
        "max_drafts": 1_000,
        "max_operations": 10_000,
        "max_database_bytes": 33_554_432,
    }
    values.update(overrides)
    return DraftSettings(**values)


def draft(
    subject: str = "Review this draft",
    body: str = "Hello,\n\nThis is a local draft.",
    *,
    recipient: str = "person@example.invalid",
) -> EmailDraft:
    return EmailDraft(
        recipients=(EmailAddress(recipient, "Example Person"),),
        cc=(EmailAddress("copy@example.invalid"),),
        bcc=(EmailAddress("private@example.invalid"),),
        subject=subject,
        body_text=body,
        in_reply_to="mock-message-customer-001",
    )


def store(tmp_path: Path, **overrides) -> SqliteDraftStore:
    return SqliteDraftStore(
        tmp_path / "data" / "email-drafts.sqlite3",
        settings(**overrides),
    )


def test_content_validation_normalizes_owned_values() -> None:
    normalized = validate_draft_content(
        EmailDraft(
            recipients=(EmailAddress("User@Example.Invalid", "Cafe\u0301"),),
            subject="Cafe\u0301",
            body_text="line one\r\nline two\rline three\tend",
        )
    )

    assert normalized.subject == "Café"
    assert normalized.recipients[0].display_name == "Café"
    assert normalized.body_text == "line one\nline two\nline three\tend"
    assert normalized.draft_id is None


@pytest.mark.parametrize(
    "value",
    [
        EmailDraft(recipients=(), subject="", body_text=""),
        EmailDraft(
            recipients=(EmailAddress("not-an-address"),), subject="", body_text=""
        ),
        EmailDraft(
            recipients=(EmailAddress("ü@example.invalid"),), subject="", body_text=""
        ),
        EmailDraft(
            recipients=(EmailAddress("a@example.invalid\r\nBcc:x@example.invalid"),),
            subject="",
            body_text="",
        ),
        EmailDraft(
            recipients=(EmailAddress("a@example.invalid"),),
            cc=(EmailAddress("a@EXAMPLE.INVALID"),),
            subject="",
            body_text="",
        ),
        EmailDraft(
            recipients=(EmailAddress("a@example.invalid"),),
            subject="bad\nsubject",
            body_text="",
        ),
        EmailDraft(
            recipients=(EmailAddress("a@example.invalid"),),
            subject="",
            body_text="unsafe\u202econtrol",
        ),
        EmailDraft(
            recipients=(EmailAddress("a@example.invalid"),),
            subject="",
            body_text="x",
            draft_id="draft_" + "a" * 32,
        ),
    ],
)
def test_content_validation_rejects_unsafe_or_storage_managed_values(
    value: EmailDraft,
) -> None:
    with pytest.raises(DraftValidationError):
        validate_draft_content(value)


def test_content_validation_enforces_character_and_byte_bounds() -> None:
    valid = EmailDraft(
        recipients=(EmailAddress("a@example.invalid"),),
        subject="s" * 500,
        body_text="😀" * 20_000,
    )
    assert len(validate_draft_content(valid).body_text) == 20_000

    with pytest.raises(DraftValidationError):
        validate_draft_content(
            EmailDraft(
                recipients=(EmailAddress("a@example.invalid"),),
                subject="s" * 501,
                body_text="",
            )
        )
    with pytest.raises(DraftValidationError):
        validate_draft_content(
            EmailDraft(
                recipients=(EmailAddress("a@example.invalid"),),
                subject="",
                body_text="😀" * 20_001,
            )
        )


def test_lazy_creation_schema_permissions_and_round_trip(tmp_path: Path) -> None:
    database = tmp_path / "data" / "email-drafts.sqlite3"
    drafts = SqliteDraftStore(database, settings(), clock=lambda: 1_000_000)
    assert database.exists() is False

    receipt = drafts.create_draft(draft(), "create-operation-0001")
    loaded = drafts.get_draft(receipt.draft_id)

    assert receipt.revision == 1
    assert receipt.replayed is False
    assert loaded is not None
    assert loaded.subject == "Review this draft"
    assert loaded.body_text.endswith("local draft.")
    assert loaded.recipients[0].address == "person@example.invalid"
    assert loaded.cc[0].address == "copy@example.invalid"
    assert loaded.bcc[0].address == "private@example.invalid"
    assert loaded.revision == 1
    assert loaded.created_at == loaded.updated_at
    assert stat.S_IMODE(database.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(database.stat().st_mode) == 0o600

    connection = sqlite3.connect(database)
    try:
        assert connection.execute("PRAGMA application_id").fetchone() == (0x48454452,)
        assert connection.execute("PRAGMA user_version").fetchone() == (1,)
        objects = {
            tuple(row)
            for row in connection.execute(
                "SELECT type,name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
            )
        }
        assert objects == {
            ("table", "drafts"),
            ("table", "draft_recipients"),
            ("table", "draft_operations"),
            ("index", "drafts_updated"),
        }
    finally:
        connection.close()


def test_create_operation_replay_is_idempotent_and_content_free(tmp_path: Path) -> None:
    drafts = store(tmp_path)
    original = draft(subject="PRIVATE SUBJECT", body="PRIVATE BODY")

    first = drafts.create_draft(original, "create-operation-0001")
    replay = drafts.create_draft(original, "create-operation-0001")

    assert replay.draft_id == first.draft_id
    assert replay.revision == 1
    assert replay.replayed is True
    assert drafts.count_drafts() == 1
    connection = sqlite3.connect(drafts.path)
    try:
        operation = connection.execute(
            "SELECT operation_kind,request_digest,draft_id,result_revision "
            "FROM draft_operations"
        ).fetchone()
    finally:
        connection.close()
    assert operation is not None
    assert operation[0] == "create"
    assert len(operation[1]) == 64
    assert "PRIVATE" not in repr(operation)

    with pytest.raises(DraftConflictError):
        drafts.create_draft(
            draft(subject="different"), "create-operation-0001"
        )


def test_update_uses_exact_revision_and_monotonic_timestamp(tmp_path: Path) -> None:
    timestamps = iter((100, 50, 25))
    drafts = SqliteDraftStore(
        tmp_path / "data" / "email-drafts.sqlite3",
        settings(),
        clock=lambda: next(timestamps),
    )
    created = drafts.create_draft(draft(), "create-operation-0001")

    updated = drafts.update_draft(
        created.draft_id,
        1,
        draft(subject="Replacement", body="Replaced"),
        "update-operation-0001",
    )
    replay = drafts.update_draft(
        created.draft_id,
        1,
        draft(subject="Replacement", body="Replaced"),
        "update-operation-0001",
    )

    assert updated.revision == 2
    assert replay.replayed is True
    loaded = drafts.get_draft(created.draft_id)
    assert loaded is not None
    assert loaded.subject == "Replacement"
    assert loaded.updated_at > loaded.created_at
    with pytest.raises(DraftConflictError) as error:
        drafts.update_draft(
            created.draft_id,
            1,
            draft(subject="Stale"),
            "update-operation-0002",
        )
    assert error.value.current_revision == 2


def test_concurrent_same_revision_has_exactly_one_winner(tmp_path: Path) -> None:
    path = tmp_path / "data" / "email-drafts.sqlite3"
    first_store = SqliteDraftStore(path, settings())
    created = first_store.create_draft(draft(), "create-operation-0001")

    def update(index: int):
        candidate = SqliteDraftStore(path, settings())
        try:
            return candidate.update_draft(
                created.draft_id,
                1,
                draft(subject=f"writer-{index}"),
                f"update-operation-{index:04d}",
            )
        except DraftConflictError:
            return None

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(update, range(8)))

    assert len([result for result in results if result is not None]) == 1
    assert first_store.get_draft(created.draft_id).revision == 2


def test_trash_and_restore_are_reversible_revision_checked_mutations(
    tmp_path: Path,
) -> None:
    drafts = store(tmp_path)
    created = drafts.create_draft(draft(), "create-operation-0001")
    trashed = drafts.trash_draft(
        created.draft_id, 1, "trash-operation-0001"
    )

    assert trashed.revision == 2
    assert drafts.get_draft(created.draft_id) is None
    assert drafts.count_drafts(state="active") == 0
    assert drafts.count_drafts(state="trashed") == 1
    assert drafts.list_drafts(state="trashed").drafts[0].draft_id == created.draft_id
    with pytest.raises(DraftStateError):
        drafts.update_draft(
            created.draft_id,
            2,
            draft(subject="blocked"),
            "update-operation-0001",
        )

    restored = drafts.restore_draft(
        created.draft_id, 2, "restore-operation-0001"
    )
    assert restored.revision == 3
    assert drafts.get_draft(created.draft_id).revision == 3
    assert drafts.restore_draft(
        created.draft_id, 2, "restore-operation-0001"
    ).replayed is True


def test_list_is_body_free_bounded_and_caller_paginated(tmp_path: Path) -> None:
    clock = iter((100, 200, 300))
    drafts = SqliteDraftStore(
        tmp_path / "data" / "email-drafts.sqlite3",
        settings(),
        clock=lambda: next(clock),
    )
    for index in range(3):
        drafts.create_draft(
            draft(subject=f"draft-{index}", body="SECRET BODY " + str(index)),
            f"create-operation-{index:04d}",
        )

    first = drafts.list_drafts(limit=2)
    second = drafts.list_drafts(limit=2, cursor=first.next_cursor)

    assert [item.subject for item in first.drafts] == ["draft-2", "draft-1"]
    assert [item.subject for item in second.drafts] == ["draft-0"]
    assert first.drafts[0].recipient_count == 3
    assert first.drafts[0].body_character_count == len("SECRET BODY 2")
    assert "SECRET BODY" not in repr(first)
    assert second.next_cursor is None
    with pytest.raises(DraftValidationError):
        drafts.list_drafts(cursor="not-a-cursor")


def test_account_namespace_separates_draft_visibility(tmp_path: Path) -> None:
    path = tmp_path / "data" / "email-drafts.sqlite3"
    first = SqliteDraftStore(
        path,
        settings(
            account_namespace="account-one", max_drafts=1, max_operations=1
        ),
    )
    second = SqliteDraftStore(
        path,
        settings(
            account_namespace="account-two", max_drafts=1, max_operations=1
        ),
    )
    created = first.create_draft(draft(), "create-operation-0001")

    assert second.get_draft(created.draft_id) is None
    assert second.list_drafts().drafts == ()
    assert second.count_drafts() == 0
    second_created = second.create_draft(
        draft(subject="second namespace"), "create-operation-0001"
    )
    assert second_created.draft_id != created.draft_id
    assert second.count_drafts() == 1


def test_draft_and_operation_capacity_fail_closed_without_eviction(tmp_path: Path) -> None:
    drafts = store(tmp_path, max_drafts=1, max_operations=2)
    created = drafts.create_draft(draft(), "create-operation-0001")
    with pytest.raises(DraftLimitError):
        drafts.create_draft(draft(subject="second"), "create-operation-0002")

    drafts.update_draft(
        created.draft_id,
        1,
        draft(subject="updated"),
        "update-operation-0001",
    )
    with pytest.raises(DraftLimitError):
        drafts.trash_draft(created.draft_id, 2, "trash-operation-0001")
    assert drafts.get_draft(created.draft_id).subject == "updated"


def test_rejects_foreign_corrupt_and_insecure_storage_without_recreation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "data" / "email-drafts.sqlite3"
    path.parent.mkdir(mode=0o700)
    path.write_bytes(b"not a sqlite database")
    path.chmod(0o600)
    original = path.read_bytes()

    with pytest.raises(DraftStorageSchemaError):
        SqliteDraftStore(path, settings()).count_drafts()
    assert path.read_bytes() == original

    path.unlink()
    target = tmp_path / "target.sqlite3"
    target.touch(mode=0o600)
    try:
        path.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(DraftStorageSecurityError):
        SqliteDraftStore(path, settings()).count_drafts()


def test_page_cap_is_reapplied_and_existing_oversize_rejected(tmp_path: Path) -> None:
    path = tmp_path / "data" / "email-drafts.sqlite3"
    drafts = SqliteDraftStore(path, settings(max_database_bytes=33_554_432))
    drafts.count_drafts()
    connection = sqlite3.connect(path)
    connection.execute(
        "INSERT INTO drafts VALUES "
        "('draft_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa','primary-account',1,'active',"
        "'subject',zeroblob(2000000),NULL,1,1,NULL)"
    )
    connection.commit()
    connection.close()

    with pytest.raises(DraftStorageResourceError):
        SqliteDraftStore(
            path, settings(max_database_bytes=1_048_576)
        ).count_drafts()


def test_close_is_idempotent_and_rejects_late_operations(tmp_path: Path) -> None:
    drafts = store(tmp_path)
    drafts.create_draft(draft(), "create-operation-0001")

    drafts.close()
    drafts.close()

    with pytest.raises(DraftStorageClosedError):
        drafts.count_drafts()
    with pytest.raises(DraftStorageClosedError):
        drafts.create_draft(draft(), "create-operation-0002")
