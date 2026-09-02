import inspect
import socket
from pathlib import Path
from typing import Any, Callable

import pytest

import hermes_email
from hermes_email.plugin import EmailPlugin, _handle_email_status, register


class FakeHermesContext:
    def __init__(
        self,
        settings: dict[str, Any] | None = None,
        *,
        profile_name: str = "ef-sinn-mail",
    ) -> None:
        self.settings = settings or {}
        self.profile_name = profile_name
        self.commands: list[tuple[str, Callable[[str], str], str]] = []
        self.skills: list[tuple[str, Path, str]] = []
        self.unload_callbacks = []

    def get_config(self, key: str, default=None):
        return self.settings.get(key, default)

    def on_unload(self, callback) -> None:
        self.unload_callbacks.append(callback)

    def register_command(
        self,
        name: str,
        handler: Callable[[str], str],
        description: str = "",
    ) -> None:
        self.commands.append((name, handler, description))

    def register_skill(self, name: str, path: Path, *, description: str) -> None:
        self.skills.append((name, path, description))


def mock_settings() -> dict[str, Any]:
    return {
        "email": {
            "provider": "mock",
            "read_mode": "mock",
            "draft_mode": "mock",
        },
        "safety": {
            "allow_send": False,
            "allow_delete": False,
            "allow_move": False,
        },
    }


def registered_command(
    context: FakeHermesContext,
) -> Callable[[str], str]:
    assert len(context.commands) == 1
    name, handler, description = context.commands[0]
    assert name == "email-status"
    assert "status" in description.lower()
    return handler


def test_email_status_uses_public_hermes_command_api() -> None:
    context = FakeHermesContext()

    register(context)

    registered_command(context)


def test_disabled_status_command_output() -> None:
    context = FakeHermesContext(profile_name="ef-sinn-mail")
    register(context)

    output = registered_command(context)("")

    assert output == "\n".join(
        (
            "Hermes Email",
            f"Version: {hermes_email.__version__}",
            "Status: disabled",
            "Provider: none",
            "Profile: ef-sinn-mail",
            "Read: disabled",
            "Draft: disabled",
            "Send: disabled",
        )
    )


def test_mock_ready_status_command_output() -> None:
    context = FakeHermesContext(mock_settings(), profile_name="mock-profile")
    register(context)

    output = registered_command(context)("")

    assert "Status: mock-ready" in output
    assert "Provider: mock" in output
    assert "Profile: mock-profile" in output
    assert "Read: enabled" in output
    assert "Draft: enabled" in output
    assert "Send: disabled" in output


def test_configuration_error_command_shows_safe_diagnostic() -> None:
    context = FakeHermesContext({"email": {"provider": "gmail"}})
    register(context)

    output = registered_command(context)("")

    assert "Status: configuration-error" in output
    assert "Provider: none" in output
    assert "Diagnostic: unsupported-provider" in output
    assert "Read: disabled" in output
    assert "Draft: disabled" in output
    assert "Send: disabled" in output


def test_command_uses_same_registered_runtime_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = FakeHermesContext()
    runtime = register(context)
    original = runtime.get_runtime_status
    calls = 0

    def tracked_status():
        nonlocal calls
        calls += 1
        return original()

    monkeypatch.setattr(runtime, "get_runtime_status", tracked_status)

    registered_command(context)("")

    assert calls == 1


def test_command_contains_no_secret_or_mail_content() -> None:
    secret = "command-secret-token-value"
    context = FakeHermesContext(
        {"email": {"provider": "gmail", "credential": secret}},
        profile_name="safe-profile",
    )
    register(context)

    output = registered_command(context)(secret)

    assert secret not in output
    assert "credential" not in output
    assert "subject" not in output.lower()
    assert "body" not in output.lower()
    assert "message" not in output.lower()
    assert "environment" not in output.lower()


def test_command_invokes_no_mail_operation(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError(f"unexpected mail operation: {args!r} {kwargs!r}")

    for method_name in (
        "fetch_messages",
        "get_message",
        "search_messages",
        "prepare_draft",
        "send_message",
    ):
        monkeypatch.setattr(EmailPlugin, method_name, forbidden)

    context = FakeHermesContext(mock_settings())
    register(context)

    assert "Status: mock-ready" in registered_command(context)("")


def test_command_uses_no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def block_socket(*args, **kwargs):
        raise AssertionError(f"unexpected network access: {args!r} {kwargs!r}")

    monkeypatch.setattr(socket, "socket", block_socket)

    context = FakeHermesContext(mock_settings())
    register(context)
    output = registered_command(context)("")

    assert "Status: mock-ready" in output


def test_command_handler_has_no_mail_operation_references() -> None:
    source = inspect.getsource(_handle_email_status)

    for forbidden_name in (
        "fetch_messages",
        "get_message",
        "search_messages",
        "prepare_draft",
        "send_message",
    ):
        assert forbidden_name not in source
    assert "get_runtime_status" in source


def test_lifecycle_cleanup_still_releases_runtime_context() -> None:
    context = FakeHermesContext(profile_name="temporary-profile")
    runtime = register(context)

    assert len(context.unload_callbacks) == 1
    context.unload_callbacks[0]()

    assert runtime.context_source is None
    assert "Profile: none" in registered_command(context)("")
