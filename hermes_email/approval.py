"""Single-use, expiring approvals issued outside the model-facing tool surface.

The supported presenter is an authenticated local OS terminal session. This is
not a sandbox against arbitrary code running as that same OS user. Gateway
identity must not be guessed from model arguments, environment flags or text.
No reusable approval is serialized to disk or returned to a model.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from dataclasses import asdict, dataclass
from typing import Callable

from .config import EmailPluginConfig
from .models import EmailDraft
from .confirmation_types import UserSendConfirmation


class ApprovalError(PermissionError):
    """Fixed, non-sensitive approval failure."""


@dataclass(frozen=True, slots=True)
class ApprovalScope:
    profile: str
    profile_home: str
    principal: str
    session: str

    def __post_init__(self) -> None:
        if any(not isinstance(v, str) or not v or len(v) > 4096
               for v in (self.profile, self.profile_home, self.principal, self.session)):
            raise ApprovalError("approval identity is unavailable")
        if self.profile == "auto":
            raise ApprovalError("approval requires an explicit profile")


@dataclass(frozen=True, slots=True)
class ApprovalSnapshot:
    draft_id: str
    revision: int
    account: str
    digest: str
    display: str


def review_snapshot(config: EmailPluginConfig, draft: EmailDraft) -> ApprovalSnapshot:
    """Own the full reviewed content AND deployment settings; no secret lookup."""
    if (draft.draft_id is None or type(draft.revision) is not int
            or draft.revision < 1 or not config.drafts.account_namespace):
        raise ApprovalError("an active persisted draft is required")
    if config.smtp.account_namespace != config.drafts.account_namespace:
        raise ApprovalError("approval account identities do not match")
    if config.smtp.sender_address is None:
        raise ApprovalError("approval sender is not configured")
    content = asdict(draft)
    canonical = json.dumps({"draft": content, "config": asdict(config)},
                           sort_keys=True, ensure_ascii=True, default=str,
                           separators=(",", ":"))
    # JSON escapes all terminal control sequences and visual direction controls.
    # Never truncate the body or Bcc list on the approval surface.
    display = json.dumps({
        "draft_id": draft.draft_id, "revision": draft.revision,
        "account": config.drafts.account_namespace,
        "from": {"address": config.smtp.sender_address,
                 "display_name": config.smtp.sender_display_name},
        "to": content["recipients"], "cc": content["cc"], "bcc": content["bcc"],
        "subject": draft.subject, "body_text": draft.body_text,
        "in_reply_to": draft.in_reply_to,
        "references": content.get("references", []),
    }, ensure_ascii=True, indent=2)
    return ApprovalSnapshot(draft.draft_id, draft.revision,
                            config.drafts.account_namespace,
                            hashlib.sha256(canonical.encode()).hexdigest(), display)


@dataclass(frozen=True, slots=True)
class ApprovalGrant:
    nonce: str
    signature: str


class ApprovalAuthority:
    """Host-private capability issuer; never register this class as a tool.

    A trusted presenter is an integration seam, not model-provided input.
    Grants are single-use, process-local and lost on restart (fail closed).
    """

    def __init__(self, scope: ApprovalScope, *, ttl_seconds: int = 300,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 300:
            raise ApprovalError("approval expiry is invalid")
        self.scope, self.ttl, self.clock = scope, ttl_seconds, clock
        self._key = secrets.token_bytes(32)
        self._pid = os.getpid()
        self._lock = threading.Lock()
        self._pending: dict[str, tuple[str, str, int, float]] = {}

    def request(self, snapshot: ApprovalSnapshot,
                presenter: Callable[[str, str, int], str]) -> ApprovalGrant:
        self._check_process()
        challenge = secrets.token_hex(12)
        start = self.clock()
        expected = "APPROVE " + challenge
        try:
            answer = presenter(snapshot.display, challenge, self.ttl)
        except Exception:
            raise ApprovalError("human approval is unavailable") from None
        elapsed = self.clock() - start
        if type(answer) is not str or not hmac.compare_digest(answer, expected):
            raise ApprovalError("human approval was denied")
        if not 0 <= elapsed < self.ttl:
            raise ApprovalError("human approval expired")
        nonce = secrets.token_hex(32)
        signature = self._signature(nonce, snapshot.digest)
        with self._lock:
            now = self.clock()
            self._pending = {k:v for k,v in self._pending.items() if now < v[3]}
            if len(self._pending) >= 32:
                raise ApprovalError("approval capacity exhausted")
            self._pending[nonce] = (snapshot.digest, snapshot.draft_id,
                                    snapshot.revision, start + self.ttl)
        return ApprovalGrant(nonce, signature)

    def consume(self, grant: ApprovalGrant, snapshot: ApprovalSnapshot,
                scope: ApprovalScope) -> UserSendConfirmation:
        self._check_process()
        if type(grant) is not ApprovalGrant or scope != self.scope:
            raise ApprovalError("approval identity does not match")
        with self._lock:
            row = self._pending.pop(grant.nonce, None)
            if row is None:
                raise ApprovalError("approval is absent or already consumed")
            digest, draft_id, revision, expires = row
            now = self.clock()
            if (not expires - self.ttl <= now < expires
                    or digest != snapshot.digest or draft_id != snapshot.draft_id
                    or revision != snapshot.revision
                    or not hmac.compare_digest(grant.signature, self._signature(grant.nonce, digest))):
                raise ApprovalError("approval expired or reviewed content changed")
        return UserSendConfirmation(draft_id, revision, grant.nonce)

    def close(self) -> None:
        with self._lock:
            self._pending.clear()
            self._key = b""

    def _check_process(self) -> None:
        if os.getpid() != self._pid or not self._key:
            raise ApprovalError("approval runtime is unavailable")

    def _signature(self, nonce: str, digest: str) -> str:
        data = json.dumps([asdict(self.scope), nonce, digest], sort_keys=True).encode()
        return hmac.new(self._key, data, hashlib.sha256).hexdigest()
