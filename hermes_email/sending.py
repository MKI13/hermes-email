"""Pure technical gates and deterministic SMTP message preparation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from email.headerregistry import Address
from email.message import EmailMessage
from email.policy import SMTP
from typing import Final

from .addressing import AddressValidationError, normalize_ascii_address
from .config import EmailPluginConfig
from .draft_storage import SqliteDraftStore
from .models import EmailAddress

_MAX_RECIPIENTS: Final = 50
_SMTP_POLICY: Final = SMTP.clone(
    utf8=False,
    cte_type="7bit",
    max_line_length=78,
    linesep="\r\n",
)


class SendGateError(PermissionError):
    """Base class for a denied technical send candidate."""


class SendGateDisabledError(SendGateError):
    """Raised when deployment configuration does not arm technical sending."""


class SendGateConfirmationError(SendGateError):
    """Raised when no exact current-user confirmation authorizes this draft revision."""


class SendGateAccountError(SendGateError):
    """Raised when SMTP and draft account identity do not match."""


class SendGateRecipientError(SendGateError):
    """Raised when an envelope recipient is not deployment-authorized."""


class SendGateMessageError(SendGateError):
    """Raised when fixed message construction requirements are not met."""


@dataclass(frozen=True, slots=True)
class UserSendConfirmation:
    """Trusted-runtime proof that the current user approved one exact draft revision.

    This value must only be created by a trusted confirmation surface after the
    current user has reviewed the draft. Model output, email content, draft
    content, configuration, and technical eligibility must never create or
    substitute this confirmation.
    """

    draft_id: str
    revision: int
    confirmation_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.draft_id, str) or not self.draft_id:
            raise ValueError("confirmation draft_id is required")
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 1:
            raise ValueError("confirmation revision must be a positive integer")
        if (
            not isinstance(self.confirmation_id, str)
            or len(self.confirmation_id) < 16
            or len(self.confirmation_id) > 128
            or not self.confirmation_id.isascii()
            or any(character.isspace() for character in self.confirmation_id)
        ):
            raise ValueError("confirmation_id must be an opaque 16-to-128 character ASCII token")


@dataclass(frozen=True, slots=True)
class DraftSendCandidate:
    """Owned immutable bytes that passed confirmation and technical eligibility gates."""

    draft_id: str
    revision: int
    account_namespace: str
    envelope_sender: str
    envelope_recipients: tuple[str, ...]
    message_id: str
    message_date: datetime
    message_bytes: bytes
    confirmation_id: str


def prepare_send_candidate(
    config: EmailPluginConfig,
    draft_store: SqliteDraftStore,
    *,
    draft_id: str,
    expected_revision: int,
    message_id: str,
    message_date: datetime,
    confirmation: UserSendConfirmation | None = None,
) -> DraftSendCandidate:
    """Prepare exact message bytes after exact current-user confirmation.

    This function performs no secret resolution, network access, or SMTP
    submission. A confirmation is valid only for the exact draft ID and exact
    revision being prepared. Any draft revision change requires a new user
    confirmation.
    """
    if not config.safety.allow_send:
        raise SendGateDisabledError("technical sending is not enabled")
    smtp = config.smtp
    if smtp.mode != "submission":
        raise SendGateDisabledError("SMTP submission is not configured")
    if config.recipient_policy.mode == "deny":
        raise SendGateDisabledError("recipient authorization is denied")
    _require_confirmation(confirmation, draft_id, expected_revision)
    account_namespace = smtp.account_namespace
    if account_namespace is None or account_namespace != draft_store.settings.account_namespace:
        raise SendGateAccountError("draft and SMTP account identities do not match")
    sender = smtp.sender_address
    if sender is None:
        raise SendGateMessageError("fixed SMTP sender is unavailable")
    sender = normalize_ascii_address(sender)
    identifier = _message_id(message_id)
    date = _message_date(message_date)
    draft = draft_store.get_active_revision(draft_id, expected_revision)
    recipients = draft.recipients + draft.cc + draft.bcc
    if not recipients or len(recipients) > _MAX_RECIPIENTS:
        raise SendGateRecipientError("draft must contain a bounded recipient set")
    envelope_recipients = tuple(recipient.address for recipient in recipients)
    for address in envelope_recipients:
        if not config.recipient_policy.permits(address):
            raise SendGateRecipientError("draft recipient is not authorized")
    message = EmailMessage(policy=_SMTP_POLICY)
    message["From"] = _header_address(
        EmailAddress(sender, smtp.sender_display_name)
    )
    message["To"] = tuple(_header_address(value) for value in draft.recipients)
    if draft.cc:
        message["Cc"] = tuple(_header_address(value) for value in draft.cc)
    message["Subject"] = draft.subject
    message["Date"] = date
    message["Message-ID"] = identifier
    message.set_content(draft.body_text, subtype="plain", charset="utf-8", cte="quoted-printable")
    message_bytes = message.as_bytes(policy=_SMTP_POLICY)
    if len(message_bytes) > smtp.max_message_bytes:
        raise SendGateMessageError("prepared SMTP message exceeds its byte limit")
    if b"\nBcc:" in message_bytes or b"\rBcc:" in message_bytes:
        raise SendGateMessageError("prepared SMTP message contains a Bcc header")
    if not message_bytes.endswith(b"\r\n"):
        raise SendGateMessageError("prepared SMTP message framing is invalid")
    assert confirmation is not None
    return DraftSendCandidate(
        draft_id=draft_id,
        revision=expected_revision,
        account_namespace=account_namespace,
        envelope_sender=sender,
        envelope_recipients=envelope_recipients,
        message_id=identifier,
        message_date=date,
        message_bytes=message_bytes,
        confirmation_id=confirmation.confirmation_id,
    )


def _require_confirmation(
    confirmation: UserSendConfirmation | None,
    draft_id: str,
    expected_revision: int,
) -> None:
    if confirmation is None:
        raise SendGateConfirmationError("explicit current-user send confirmation is required")
    if confirmation.draft_id != draft_id or confirmation.revision != expected_revision:
        raise SendGateConfirmationError("send confirmation does not match the exact draft revision")


def _header_address(value: EmailAddress) -> Address:
    local, domain = normalize_ascii_address(value.address).rsplit("@", 1)
    return Address(
        display_name=value.display_name or "",
        username=local,
        domain=domain,
    )


def _message_id(value: object) -> str:
    if not isinstance(value, str) or not value.startswith("<") or not value.endswith(">"):
        raise SendGateMessageError("SMTP Message-ID is invalid")
    try:
        inner = normalize_ascii_address(value[1:-1])
    except AddressValidationError:
        raise SendGateMessageError("SMTP Message-ID is invalid") from None
    return "<" + inner + ">"


def _message_date(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise SendGateMessageError("SMTP message date must be timezone-aware")
    return value.astimezone(UTC).replace(microsecond=0)
