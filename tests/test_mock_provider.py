import asyncio
import socket

import pytest

from hermes_email.providers import EmailProvider, MockEmailProvider


def run(coroutine):
    return asyncio.run(coroutine)


def test_mock_provider_identity_and_capabilities() -> None:
    provider = MockEmailProvider()

    assert isinstance(provider, EmailProvider)
    assert provider.name == "mock"
    assert provider.capabilities.fetch is True
    assert provider.capabilities.get is True
    assert set(provider.capabilities.__dataclass_fields__) == {"fetch", "get"}


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


def test_supported_operations_do_not_use_the_network(monkeypatch: pytest.MonkeyPatch) -> None:
    async def exercise_provider() -> None:
        def block_socket(*args, **kwargs):
            raise AssertionError(f"unexpected network access: {args!r} {kwargs!r}")

        monkeypatch.setattr(socket, "socket", block_socket)
        provider = MockEmailProvider()
        messages = await provider.fetch_messages()
        assert await provider.get_message(messages[0].message_id) is not None

    run(exercise_provider())
