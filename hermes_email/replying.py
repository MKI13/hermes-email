"""Provider-neutral, side-effect-free reply routing for reviewed email."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from .addressing import AddressValidationError, normalize_ascii_address
from .models import EmailAddress, EmailMessage

_MAX_REPLY_CANDIDATES: Final = 10


@dataclass(frozen=True, slots=True)
class ReplyRoute:
    source: str
    candidates: tuple[EmailAddress, ...]
    selected: EmailAddress | None
    ambiguous: bool
    truncated: bool
    valid: bool


def _valid_candidates(values: tuple[EmailAddress, ...]) -> tuple[EmailAddress, ...]:
    result: list[EmailAddress] = []
    for value in values[:_MAX_REPLY_CANDIDATES]:
        try:
            normalize_ascii_address(value.address)
        except AddressValidationError:
            continue
        result.append(value)
    return tuple(result)


def derive_reply_route(message: EmailMessage) -> ReplyRoute:
    """Derive reviewable reply routing without granting action authority."""
    if message.reply_to:
        truncated = len(message.reply_to) > _MAX_REPLY_CANDIDATES
        candidates = _valid_candidates(message.reply_to)
        fully_valid = len(candidates) == min(len(message.reply_to), _MAX_REPLY_CANDIDATES)
        if not fully_valid or truncated or len(candidates) != 1:
            return ReplyRoute("reply-to", candidates, None, True, truncated, fully_valid)
        return ReplyRoute("reply-to", candidates, candidates[0], False, False, True)

    try:
        normalize_ascii_address(message.sender.address)
    except AddressValidationError:
        return ReplyRoute("from", (), None, True, False, False)
    return ReplyRoute("from", (message.sender,), message.sender, False, False, True)
