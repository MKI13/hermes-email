from datetime import UTC, datetime
from pathlib import Path

import pytest

from hermes_email.send_orchestration import (
    DuplicateDraftSendError,
    IdempotentSendOrchestrator,
    SendOperationConflictError,
    SqliteSendIntentStore,
)
from hermes_email.sending import DraftSendCandidate
from hermes_email.smtp import (
    SmtpConnectionError,
    SmtpDeliveryUnknownError,
    SmtpSubmissionResult,
)


OPERATION_ID = "send-operation-0001"


def candidate(*, revision: int = 1, confirmation_id: str = "confirmation-0001") -> DraftSendCandidate:
    return DraftSendCandidate(
        draft_id="draft_" + "a" * 32,
        revision=revision,
        account_namespace="primary-account",
        envelope_sender="sender@example.invalid",
        envelope_recipients=("to@example.invalid",),
        message_id="<message-0001@example.invalid>",
        message_date=datetime(2026, 9, 5, 0, 30, tzinfo=UTC),
        message_bytes=(
            b"From: sender@example.invalid\r\n"
            b"To: to@example.invalid\r\n"
            b"Subject: Test\r\n"
            b"Message-ID: <message-0001@example.invalid>\r\n"
            b"\r\n"
            b"Body\r\n"
        ),
        confirmation_id=confirmation_id,
    )


class FakeTransport:
    def __init__(self, outcome: str = "accepted") -> None:
        self.outcome = outcome
        self.calls = 0

    def check_health(self) -> None:
        return None

    def submit_once(self, submission):
        self.calls += 1
        if self.outcome == "unknown":
            raise SmtpDeliveryUnknownError("unknown")
        if self.outcome == "failure":
            raise SmtpConnectionError("failed")
        if self.outcome == "crash":
            raise RuntimeError("synthetic crash")
        return SmtpSubmissionResult()

    def close(self) -> None:
        return None


def store(tmp_path: Path) -> SqliteSendIntentStore:
    return SqliteSendIntentStore(tmp_path / "profile-data")


def test_successful_send_is_persisted_and_replayed_without_second_smtp_call(tmp_path: Path) -> None:
    ledger = store(tmp_path)
    transport = FakeTransport()
    orchestrator = IdempotentSendOrchestrator(ledger, transport)

    first = orchestrator.send_once(OPERATION_ID, candidate())
    second = orchestrator.send_once(OPERATION_ID, candidate())

    assert first.state == "accepted"
    assert first.replayed is False
    assert second.state == "accepted"
    assert second.replayed is True
    assert transport.calls == 1


def test_restart_replay_never_redispatches(tmp_path: Path) -> None:
    first_transport = FakeTransport()
    IdempotentSendOrchestrator(store(tmp_path), first_transport).send_once(
        OPERATION_ID, candidate()
    )

    restarted_transport = FakeTransport()
    replay = IdempotentSendOrchestrator(store(tmp_path), restarted_transport).send_once(
        OPERATION_ID, candidate()
    )

    assert replay.state == "accepted"
    assert replay.replayed is True
    assert first_transport.calls == 1
    assert restarted_transport.calls == 0


def test_delivery_unknown_is_terminal_and_never_retried_after_restart(tmp_path: Path) -> None:
    first_transport = FakeTransport("unknown")
    first = IdempotentSendOrchestrator(store(tmp_path), first_transport).send_once(
        OPERATION_ID, candidate()
    )

    restarted_transport = FakeTransport()
    replay = IdempotentSendOrchestrator(store(tmp_path), restarted_transport).send_once(
        OPERATION_ID, candidate()
    )

    assert first.state == "delivery-unknown"
    assert replay.state == "delivery-unknown"
    assert replay.replayed is True
    assert first_transport.calls == 1
    assert restarted_transport.calls == 0


def test_crash_after_persisting_intent_leaves_dispatching_and_blocks_retry(tmp_path: Path) -> None:
    first_transport = FakeTransport("crash")
    with pytest.raises(RuntimeError, match="synthetic crash"):
        IdempotentSendOrchestrator(store(tmp_path), first_transport).send_once(
            OPERATION_ID, candidate()
        )

    restarted_transport = FakeTransport()
    replay = IdempotentSendOrchestrator(store(tmp_path), restarted_transport).send_once(
        OPERATION_ID, candidate()
    )

    assert replay.state == "dispatching"
    assert replay.replayed is True
    assert first_transport.calls == 1
    assert restarted_transport.calls == 0


def test_same_draft_revision_cannot_send_again_under_new_operation_id(tmp_path: Path) -> None:
    ledger = store(tmp_path)
    first_transport = FakeTransport()
    IdempotentSendOrchestrator(ledger, first_transport).send_once(OPERATION_ID, candidate())

    with pytest.raises(DuplicateDraftSendError):
        IdempotentSendOrchestrator(ledger, FakeTransport()).send_once(
            "send-operation-0002", candidate(confirmation_id="confirmation-0002")
        )


def test_same_operation_id_with_changed_candidate_fails_closed(tmp_path: Path) -> None:
    ledger = store(tmp_path)
    ledger.begin(OPERATION_ID, candidate())

    with pytest.raises(SendOperationConflictError):
        ledger.begin(OPERATION_ID, candidate(revision=2))


def test_definite_failure_is_recorded_and_same_operation_does_not_retry(tmp_path: Path) -> None:
    transport = FakeTransport("failure")
    orchestrator = IdempotentSendOrchestrator(store(tmp_path), transport)

    first = orchestrator.send_once(OPERATION_ID, candidate())
    second = orchestrator.send_once(OPERATION_ID, candidate())

    assert first.state == "definite-failure"
    assert second.state == "definite-failure"
    assert second.replayed is True
    assert transport.calls == 1


def test_send_ledger_uses_private_posix_permissions(tmp_path: Path) -> None:
    ledger = store(tmp_path)
    ledger.begin(OPERATION_ID, candidate())

    if ledger.path.stat().st_mode & 0o777 != 0o600:
        pytest.fail("send-intent database must be owner-only")
    if ledger.path.parent.stat().st_mode & 0o777 != 0o700:
        pytest.fail("send-intent directory must be owner-only")
