"""Provider-neutral email data models."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import overload


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
    """Normalized message data returned by a provider."""

    message_id: str
    subject: str
    sender: EmailAddress
    recipients: tuple[EmailAddress, ...]
    body_text: str | None = None
    received_at: datetime | None = None
    status: MessageStatus = MessageStatus.NEW
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EmailMessagePage(Sequence[EmailMessage]):
    """One immutable provider page with an optional opaque continuation cursor."""

    messages: tuple[EmailMessage, ...]
    next_cursor: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "messages", tuple(self.messages))
        if self.next_cursor is not None and (
            not isinstance(self.next_cursor, str) or not self.next_cursor.strip()
        ):
            raise ValueError("next_cursor must be None or a non-empty string")

    def __len__(self) -> int:
        return len(self.messages)

    @overload
    def __getitem__(self, index: int) -> EmailMessage: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[EmailMessage, ...]: ...

    def __getitem__(
        self, index: int | slice
    ) -> EmailMessage | tuple[EmailMessage, ...]:
        return self.messages[index]

    def __iter__(self) -> Iterator[EmailMessage]:
        return iter(self.messages)


@dataclass(frozen=True, slots=True)
class EmailDraft:
    """Provider-neutral draft content without send authority."""

    recipients: tuple[EmailAddress, ...]
    subject: str
    body_text: str
    draft_id: str | None = None
    in_reply_to: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)
