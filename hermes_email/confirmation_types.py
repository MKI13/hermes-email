"""Internal send marker; authenticated identity comes from ApprovalAuthority.

Constructing this value alone grants no model-facing send capability.
"""
from __future__ import annotations
from dataclasses import dataclass


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


