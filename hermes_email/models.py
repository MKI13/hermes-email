"""Provider-neutral email data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class MessageStatus(StrEnum):
    """Minimal lifecycle states independent of a mail provider."""

    NEW = "new"
    PROCESSED = "processed"
    DRAFTED = "drafted"


@dataclass(frozen=True, slots=True)
class EmailAddress:
    """An email address and optional display name."""

    address: str
    display_name: str | None = None


@dataclass(frozen=True, slots=True)
class EmailMessage:
    """Normalized message data returned by a future provider."""

    message_id: str
    subject: str
    sender: EmailAddress
    recipients: tuple[EmailAddress, ...]
    body_text: str | None = None
    received_at: datetime | None = None
    status: MessageStatus = MessageStatus.NEW
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EmailDraft:
    """Provider-neutral draft content without send authority."""

    recipients: tuple[EmailAddress, ...]
    subject: str
    body_text: str
    draft_id: str | None = None
    in_reply_to: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)
