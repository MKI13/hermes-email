"""Provider-neutral, bounded RFC email thread reconstruction.

Thread membership is based only on Message-ID, In-Reply-To, and References
relationships. Subject text, sender names, bodies, and other untrusted content
never establish thread membership or action authority.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Iterable

from .models import EmailMessage

_MAX_THREAD_MESSAGES: Final = 25
_MAX_REFERENCE_IDS: Final = 100
_MESSAGE_ID_RE = re.compile(r"<[^<>\s]{1,500}>")


@dataclass(frozen=True, slots=True)
class EmailThreadContext:
    """One bounded, chronological, untrusted thread context."""

    seed_message_id: str
    messages: tuple[EmailMessage, ...]
    scan_complete: bool
    truncated: bool
    unresolved_reference_count: int

    @property
    def content_is_untrusted(self) -> bool:
        return True


def message_rfc_id(message: EmailMessage) -> str | None:
    """Return one normalized RFC Message-ID from provider metadata."""
    values = parse_message_ids(message.metadata.get("rfc_message_id"))
    return values[0] if values else None


def message_references(message: EmailMessage) -> tuple[str, ...]:
    """Return bounded normalized parent/reference IDs from provider metadata."""
    combined: list[str] = []
    for key in ("in_reply_to", "references"):
        for value in parse_message_ids(message.metadata.get(key)):
            if value not in combined:
                combined.append(value)
            if len(combined) >= _MAX_REFERENCE_IDS:
                return tuple(combined)
    return tuple(combined)


def parse_message_ids(value: object) -> tuple[str, ...]:
    """Parse bounded angle-bracket Message-ID tokens without heuristic repair."""
    if not isinstance(value, str) or not value or len(value) > 16_384:
        return ()
    ids: list[str] = []
    for match in _MESSAGE_ID_RE.finditer(value):
        token = match.group(0)
        if not token.isascii() or token in ids:
            continue
        ids.append(token)
        if len(ids) >= _MAX_REFERENCE_IDS:
            break
    return tuple(ids)


def build_thread_context(
    seed: EmailMessage,
    candidates: Iterable[EmailMessage],
    *,
    scan_complete: bool,
    max_messages: int = _MAX_THREAD_MESSAGES,
) -> EmailThreadContext:
    """Build the RFC-linked component containing ``seed``.

    No subject/sender/body heuristic is used. A message joins only when its
    Message-ID/reference set intersects the growing RFC identity component.
    This prevents ordinary subject collisions from merging unrelated business
    conversations.
    """
    if isinstance(max_messages, bool) or not isinstance(max_messages, int):
        raise ValueError("max_messages must be an integer")
    if not 1 <= max_messages <= _MAX_THREAD_MESSAGES:
        raise ValueError(f"max_messages must be between 1 and {_MAX_THREAD_MESSAGES}")

    by_provider_id: dict[str, EmailMessage] = {seed.message_id: seed}
    for message in candidates:
        by_provider_id.setdefault(message.message_id, message)

    seed_ids = _identity_set(seed)
    if not seed_ids:
        return EmailThreadContext(
            seed_message_id=seed.message_id,
            messages=(seed,),
            scan_complete=scan_complete,
            truncated=False,
            unresolved_reference_count=0,
        )

    known_ids = set(seed_ids)
    selected = {seed.message_id}
    changed = True
    while changed:
        changed = False
        for message in by_provider_id.values():
            if message.message_id in selected:
                continue
            identities = _identity_set(message)
            if identities and known_ids.intersection(identities):
                selected.add(message.message_id)
                known_ids.update(identities)
                changed = True

    messages = [by_provider_id[message_id] for message_id in selected]
    messages.sort(key=_chronological_key)

    present_rfc_ids = {
        rfc_id
        for message in messages
        for rfc_id in (message_rfc_id(message),)
        if rfc_id is not None
    }
    referenced_ids = {
        reference
        for message in messages
        for reference in message_references(message)
    }
    unresolved = len(referenced_ids.difference(present_rfc_ids))

    truncated = len(messages) > max_messages
    if truncated:
        # Preserve the seed and favor the most recent context while returning
        # chronological order.
        seed_message = seed
        recent = messages[-max_messages:]
        if all(item.message_id != seed.message_id for item in recent):
            recent[0] = seed_message
            recent.sort(key=_chronological_key)
        messages = recent

    return EmailThreadContext(
        seed_message_id=seed.message_id,
        messages=tuple(messages),
        scan_complete=scan_complete,
        truncated=truncated,
        unresolved_reference_count=unresolved,
    )


def _identity_set(message: EmailMessage) -> set[str]:
    identities = set(message_references(message))
    own = message_rfc_id(message)
    if own is not None:
        identities.add(own)
    return identities


def _chronological_key(message: EmailMessage) -> tuple[datetime, str]:
    received = message.received_at
    if received is None:
        received = datetime.max.replace(tzinfo=UTC)
    elif received.tzinfo is None:
        received = received.replace(tzinfo=UTC)
    else:
        received = received.astimezone(UTC)
    return received, message.message_id
