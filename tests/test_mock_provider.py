import asyncio
import socket

import pytest

from hermes_email.models import EmailAddress, EmailDraft
from hermes_email.providers import EmailProvider, MockEmailProvider, MockSendBlockedError


def run(coroutine):
    return asyncio.run(coroutine)


def test_mock_provider_identity_and_capabilities() -> None:
    provider = MockEmailProvider()

    assert isinstance(provider, EmailProvider)
    assert provider.name == "mock"
    assert provider.capabilities.fetch is True
    assert provider.capabilities.get is True
    assert provider.capabilities.drafts is True
    assert provider.capabilities.send is False
    assert provider.capabilities.delete is False
    assert provider.capabilities.move is False


def test_fetch_returns_deterministic_local_messages_without_state_change() -> None:
    provider = MockEmailProvider()

    first = run(provider.fetch_messages())
    second = run(provider.fetch_messages())

    assert first == second
    assert len(first) == 3
    assert first[0].message_id == "mock-message-customer-001"
    assert "sample plan" in (first[0].body_text or "")
    assert first[1].body_text == ""
    assert first[2].metadata["content_type"] == "text/html"

    first[0].metadata["caller_value"] = "must not persist"
    fetched_again = run(provider.fetch_messages())
    assert "caller_value" not in fetched_again[0].metadata


def test_fetch_respects_limit() -> None:
    provider = MockEmailProvider()

    assert run(provider.fetch_messages(limit=0)).messages == ()
    assert len(run(provider.fetch_messages(limit=2))) == 2
    assert len(run(provider.fetch_messages(limit=100))) == 3

    with pytest.raises(ValueError, match="zero or greater"):
        run(provider.fetch_messages(limit=-1))


def test_get_message_handles_known_and_unknown_ids() -> None:
    provider = MockEmailProvider()

    message = run(provider.get_message("mock-message-empty-002"))

    assert message is not None
    assert message.subject == "Empty test message"
    assert run(provider.get_message("missing-message")) is None


def test_create_draft_uses_stable_in_memory_ids() -> None:
    draft = EmailDraft(
        recipients=(EmailAddress("recipient@example.invalid"),),
        subject="Local draft",
        body_text="This remains in memory.",
    )
    first_provider = MockEmailProvider()
    second_provider = MockEmailProvider()

    first = run(first_provider.create_draft(draft))
    second = run(first_provider.create_draft(draft))
    fresh_instance = run(second_provider.create_draft(draft))

    assert first.draft_id == "mock-draft-0001"
    assert second.draft_id == "mock-draft-0002"
    assert fresh_instance.draft_id == "mock-draft-0001"
    assert first.subject == draft.subject
    assert first.body_text == draft.body_text


def test_create_draft_preserves_explicit_mock_id() -> None:
    provider = MockEmailProvider()
    draft = EmailDraft(
        recipients=(EmailAddress("recipient@example.invalid"),),
        subject="Explicit ID",
        body_text="Local only.",
        draft_id="fixture-draft",
    )

    assert run(provider.create_draft(draft)).draft_id == "fixture-draft"


def test_send_message_is_always_blocked() -> None:
    provider = MockEmailProvider()
    draft = EmailDraft(
        recipients=(EmailAddress("recipient@example.invalid"),),
        subject="Never send",
        body_text="Local only.",
    )
    created = run(provider.create_draft(draft))

    with pytest.raises(MockSendBlockedError, match="send capability is disabled"):
        run(provider.send_message(created.draft_id or ""))

    with pytest.raises(MockSendBlockedError, match="send capability is disabled"):
        run(provider.send_message("unknown-draft"))


def test_supported_operations_do_not_use_the_network(monkeypatch: pytest.MonkeyPatch) -> None:
    async def exercise_provider() -> None:
        def block_socket(*args, **kwargs):
            raise AssertionError(f"unexpected network access: {args!r} {kwargs!r}")

        monkeypatch.setattr(socket, "socket", block_socket)
        provider = MockEmailProvider()
        messages = await provider.fetch_messages()
        assert await provider.get_message(messages[0].message_id) is not None
        await provider.create_draft(
            EmailDraft(
                recipients=(EmailAddress("recipient@example.invalid"),),
                subject="Offline",
                body_text="No network.",
            )
        )

    run(exercise_provider())
