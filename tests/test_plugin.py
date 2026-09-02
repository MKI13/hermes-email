import asyncio
from pathlib import Path

import pytest

from hermes_email.models import EmailAddress, EmailDraft
from hermes_email.plugin import EmailPlugin, SendingUnavailableError, register


class FakePluginContext:
    def __init__(self) -> None:
        self.skills: list[tuple[str, Path, str]] = []

    def register_skill(self, name: str, path: Path, *, description: str) -> None:
        self.skills.append((name, path, description))


def test_register_adds_only_the_bundled_skill() -> None:
    context = FakePluginContext()

    register(context)

    assert len(context.skills) == 1
    name, path, description = context.skills[0]
    assert name == "email"
    assert path.name == "SKILL.md"
    assert path.exists()
    assert "active Hermes profile" in description


def test_prepare_draft_has_no_external_effect() -> None:
    draft = EmailDraft(
        recipients=(EmailAddress("recipient@example.invalid"),),
        subject="Example",
        body_text="Draft body",
    )

    assert EmailPlugin().prepare_draft(draft) is draft


def test_sending_is_unavailable_even_with_local_opt_in() -> None:
    async def attempt_send() -> None:
        with pytest.raises(SendingUnavailableError, match="not implemented"):
            await EmailPlugin().send_message("draft-1")

    asyncio.run(attempt_send())
