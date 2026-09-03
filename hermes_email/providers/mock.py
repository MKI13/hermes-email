"""Deterministic, in-memory email provider for local tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Final, Sequence

from ..models import EmailAddress, EmailMessage, EmailMessagePage
from .base import EmailProvider, ProviderCapabilities


class MockCursorError(ValueError):
    """Raised when a cursor is not valid for the deterministic mock mailbox."""


class MockEmailProvider(EmailProvider):
    """Local provider with deterministic read-only messages."""

    NAME: Final = "mock"
    capabilities = ProviderCapabilities(fetch=True, get=True)

    def __init__(self, messages: Sequence[EmailMessage] | None = None) -> None:
        source = messages if messages is not None else _default_messages()
        self._messages = tuple(_copy_message(message) for message in source)
        self._messages_by_id = {message.message_id: message for message in self._messages}
        if len(self._messages_by_id) != len(self._messages):
            raise ValueError("mock message IDs must be unique")

    @property
    def name(self) -> str:
        """Return the stable mock provider identifier."""
        return self.NAME

    async def fetch_messages(
        self, *, limit: int = 50, cursor: str | None = None
    ) -> EmailMessagePage:
        """Return one deterministic local page without changing state."""
        if limit < 0:
            raise ValueError("limit must be zero or greater")
        start = self._offset_from_cursor(cursor)
        end = min(start + limit, len(self._messages))
        messages = tuple(_copy_message(message) for message in self._messages[start:end])
        next_cursor = self._cursor_for_offset(end) if end < len(self._messages) else None
        return EmailMessagePage(messages=messages, next_cursor=next_cursor)

    def _offset_from_cursor(self, cursor: str | None) -> int:
        if cursor is None:
            return 0
        prefix = "mock-page-offset-"
        if not isinstance(cursor, str) or not cursor.startswith(prefix):
            raise MockCursorError("unknown mock pagination cursor")
        encoded_offset = cursor[len(prefix) :]
        if (
            len(encoded_offset) != 4
            or not encoded_offset.isascii()
            or not encoded_offset.isdigit()
        ):
            raise MockCursorError("unknown mock pagination cursor")
        offset = int(encoded_offset)
        if offset < 0 or offset >= len(self._messages):
            raise MockCursorError("unknown mock pagination cursor")
        return offset

    @staticmethod
    def _cursor_for_offset(offset: int) -> str:
        return f"mock-page-offset-{offset:04d}"

    async def get_message(self, message_id: str) -> EmailMessage | None:
        """Return a local message by ID, or ``None`` when it is unknown."""
        message = self._messages_by_id.get(message_id)
        return _copy_message(message) if message is not None else None

def _copy_message(message: EmailMessage) -> EmailMessage:
    return replace(message, metadata=dict(message.metadata))


def _default_messages() -> tuple[EmailMessage, ...]:
    inbox = EmailAddress("support@example.invalid", "Example Support")
    return (
        EmailMessage(
            message_id="mock-message-customer-001",
            subject="Question about the sample service",
            sender=EmailAddress("customer@example.invalid", "Example Customer"),
            recipients=(inbox,),
            body_text="Could you explain which sample plan includes team access?",
            received_at=datetime(2026, 1, 15, 9, 30, tzinfo=UTC),
            metadata={"content_type": "text/plain"},
        ),
        EmailMessage(
            message_id="mock-message-empty-002",
            subject="Empty test message",
            sender=EmailAddress("empty@example.invalid"),
            recipients=(inbox,),
            body_text="",
            received_at=datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
            metadata={"content_type": "text/plain"},
        ),
        EmailMessage(
            message_id="mock-message-html-003",
            subject="HTML test message",
            sender=EmailAddress("html@example.invalid"),
            recipients=(inbox,),
            body_text="<p>Hello from a <strong>local HTML fixture</strong>.</p>",
            received_at=datetime(2026, 1, 15, 10, 30, tzinfo=UTC),
            metadata={"content_type": "text/html"},
        ),
    )
