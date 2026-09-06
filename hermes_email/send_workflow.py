"""Human-reviewed send workflow. NEVER register raw sending as a model tool."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Callable

from .approval import ApprovalAuthority, ApprovalError, ApprovalScope, review_snapshot
from .config import EmailPluginConfig
from .draft_storage import SqliteDraftStore
from .send_orchestration import IdempotentSendOrchestrator, SqliteSendIntentStore
from .sending import prepare_send_candidate
from .smtp import SmtplibTransport, SmtpSubmissionError
from .secrets import EnvironmentSecretResolver
from .sent_copy import save_sent_copy


class ReviewedSendWorkflow:
    """Host-private orchestration with one mandatory fresh human approval.

    Injection seams are for trusted host integration/tests, never tool arguments.
    Grants are not serialized. Existing send evidence returns status without any
    attempt to regenerate consent or resubmit bytes.
    """
    def __init__(self, config: EmailPluginConfig, drafts: SqliteDraftStore, data_dir: Path,
                 scope_provider: Callable[[], ApprovalScope], *, transport_factory=None,
                 clock: Callable[[], float] = time.monotonic,
                 config_provider: Callable[[], EmailPluginConfig] | None = None) -> None:
        self.config, self.drafts, self.data_dir = config, drafts, Path(data_dir)
        self.scope_provider, self.clock = scope_provider, clock
        self.config_provider = config_provider or (lambda: self.config)
        self.transport_factory = transport_factory

    def run(self, draft_id: str, presenter: Callable[[str, str, int], str]) -> dict:
        cfg = self.config
        scope = self.scope_provider()
        if self.config_provider() != cfg:
            raise ApprovalError("send settings changed; reload and review again")
        if (cfg.send_workflow.mode == "disabled" or not cfg.safety.allow_send
                or cfg.smtp.mode != "submission" or cfg.hermes.profile != scope.profile
                or scope.profile_home != str(self.data_dir.resolve())
                or self.drafts.settings.account_namespace != cfg.smtp.account_namespace
                or self.drafts.path.parent.resolve() != self.data_dir.resolve()):
            raise ApprovalError("human send workflow is not authorized")
        draft = self.drafts.get_draft(draft_id)
        if draft is None:
            raise ApprovalError("draft is unavailable")
        recipients = draft.recipients + draft.cc + draft.bcc
        if not recipients or any(not cfg.recipient_policy.permits(a.address) for a in recipients):
            raise ApprovalError("recipient policy does not authorize this draft")
        if cfg.send_workflow.mode == "local-test":
            if any(not a.address.rsplit("@",1)[-1].lower().endswith(".invalid") for a in recipients):
                raise ApprovalError("local-test sends require reserved .invalid recipients")
        material = json.dumps([scope.profile_home, scope.profile, cfg.smtp.account_namespace,
                               draft.draft_id, draft.revision], separators=(",", ":")).encode()
        operation_id = "reviewed_" + hashlib.sha256(material).hexdigest()
        ledger = SqliteSendIntentStore(self.data_dir)
        authority = ApprovalAuthority(scope, clock=self.clock)
        try:
            previous = ledger.get(operation_id)
            if previous is not None:
                return _receipt(previous, "not-rechecked")
            snapshot = review_snapshot(cfg, draft, purpose="send")
            grant = authority.request(snapshot, presenter)
            validity = authority.validity(grant)
            with self.drafts.hold_active_revision(draft_id, draft.revision) as (current, verify):
                confirmation = authority.consume(grant, review_snapshot(self.config_provider(), current, purpose="send"), self.scope_provider())
                class PinnedDraft:
                    settings = self.drafts.settings
                    def get_active_revision(inner, identifier, revision):
                        verify()
                        if identifier != current.draft_id or revision != current.revision:
                            raise ApprovalError("reviewed revision changed")
                        return current
                candidate = prepare_send_candidate(cfg, PinnedDraft(), draft_id=current.draft_id,
                    expected_revision=current.revision, confirmation=confirmation, send_operation_id=operation_id)
                def check_approval():
                    verify()
                    if (self.scope_provider() != scope or self.config_provider() != cfg
                            or not validity[0] <= self.clock() < validity[1]):
                        raise SmtpSubmissionError("approval expired or identity changed before transport")
                transport = (self.transport_factory() if self.transport_factory is not None
                    else SmtplibTransport(cfg.smtp, EnvironmentSecretResolver(), before_data=check_approval))
                class ApprovedTransport:
                    def submit_once(inner, submission):
                        check_approval()
                        return transport.submit_once(submission)
                try:
                    record = IdempotentSendOrchestrator(ledger, ApprovedTransport()).send_once(operation_id, candidate)
                except Exception:
                    # May have accepted DATA before local persistence failed.
                    # Do not misreport failure as permission to try again.
                    return {"ok": False, "operation_id": operation_id,
                            "state": "outcome-unknown", "automatic_retry_forbidden": True,
                            "manual_review_required": True, "delivery_confirmed": False}
                finally:
                    close = getattr(transport, "close", None)
                    if close:
                        try: close()
                        except Exception: pass
                copy = "not-requested"
                if record.state == "accepted" and cfg.send_workflow.save_local_copy:
                    copy = save_sent_copy(self.data_dir, operation_id, candidate.message_bytes)
                return _receipt(record, copy)
        finally:
            authority.close()
            ledger.close()


def _receipt(record, copy: str) -> dict:
    return {"ok": record.state == "accepted", "operation_id": record.operation_id,
            "draft_id": record.draft_id, "revision": record.revision,
            "state": record.state, "replayed": record.replayed,
            "message_id": record.message_id,
            "smtp_accepted": record.state == "accepted", "delivery_confirmed": False,
            "automatic_retry_forbidden": True,
            "manual_review_required": record.delivery_is_uncertain,
            "sent_copy": copy}
