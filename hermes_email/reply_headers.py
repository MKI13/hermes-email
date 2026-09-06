"""Bounded ASCII RFC reply identifiers; provider-local IDs are never headers."""
from __future__ import annotations

import re
from .models import EmailMessage

_ATOM = r"[A-Za-z0-9!#$%&'*+\-/=?^_`{|}~]+"
_MESSAGE_ID = re.compile(r"<" + _ATOM + r"(?:\." + _ATOM + r")*@" + _ATOM + r"(?:\." + _ATOM + r")*>")
MAX_REFERENCES = 50


def valid_message_id(value: object) -> bool:
    return isinstance(value, str) and len(value) <= 512 and _MESSAGE_ID.fullmatch(value) is not None


def validate_references(values: object) -> tuple[str, ...]:
    if (not isinstance(values, (tuple, list)) or len(values) > MAX_REFERENCES
            or any(not valid_message_id(value) for value in values)):
        raise ValueError("reply references are invalid")
    result = tuple(values)
    if len(result) != len(set(result)) or sum(map(len, result)) > 16384:
        raise ValueError("reply references are invalid")
    return result


def source_reply_headers(message: EmailMessage) -> tuple[str | None, tuple[str, ...]]:
    """No ambiguous parent selection or heuristic repair. Preserve bounded ancestors."""
    raw_parent = message.metadata.get("rfc_message_id", "").strip()
    parent = raw_parent if valid_message_id(raw_parent) else None
    raw_refs = message.metadata.get("references", "")
    if not raw_refs:
        raw_refs = message.metadata.get("in_reply_to", "")
        # RFC fallback is defined only for a single parent's In-Reply-To.
        if not valid_message_id(raw_refs.strip()):
            raw_refs = ""
    values = raw_refs.split() if len(raw_refs) <= 16384 else []
    if any(not valid_message_id(value) for value in values):
        values = []
    chain = list(dict.fromkeys(values))
    if parent:
        chain = [value for value in chain if value != parent] + [parent]
    # Keep root and nearest ancestors, never lose the immediate parent.
    if len(chain) > MAX_REFERENCES:
        chain = chain[:1] + chain[-(MAX_REFERENCES - 1):]
    while sum(map(len, chain)) > 16384:
        del chain[1 if len(chain) > 2 else 0]
    return parent, tuple(chain)
