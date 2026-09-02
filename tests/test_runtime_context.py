import builtins
from pathlib import Path

import pytest

from hermes_email.context import ActiveProfileContextSource, HermesContext
from hermes_email.plugin import EmailPlugin, register


class FakeHermesContext:
    def __init__(self, profile_name: object = "ef-sinn-mail") -> None:
        self.profile_name = profile_name
        self.skills: list[tuple[str, Path, str]] = []
        self.commands = []
        self.unload_callbacks = []

    def get_config(self, key: str, default=None):
        return default

    def on_unload(self, callback) -> None:
        self.unload_callbacks.append(callback)

    def register_command(self, name: str, handler, description: str = "") -> None:
        self.commands.append((name, handler, description))

    def register_skill(self, name: str, path: Path, *, description: str) -> None:
        self.skills.append((name, path, description))


class FakeHermesContextWithoutProfile:
    def __init__(self) -> None:
        self.skills: list[tuple[str, Path, str]] = []
        self.commands = []
        self.unload_callbacks = []

    def get_config(self, key: str, default=None):
        return default

    def on_unload(self, callback) -> None:
        self.unload_callbacks.append(callback)

    def register_command(self, name: str, handler, description: str = "") -> None:
        self.commands.append((name, handler, description))

    def register_skill(self, name: str, path: Path, *, description: str) -> None:
        self.skills.append((name, path, description))


def test_active_profile_source_receives_runtime_context() -> None:
    context = FakeHermesContext()

    source = ActiveProfileContextSource(context)

    assert source.get_context() == HermesContext(profile_name="ef-sinn-mail")


def test_register_binds_active_profile_to_email_plugin() -> None:
    context = FakeHermesContext("customer-support")

    plugin = register(context)

    assert isinstance(plugin, EmailPlugin)
    assert isinstance(plugin.context_source, ActiveProfileContextSource)
    assert plugin.get_hermes_context().profile_name == "customer-support"


def test_missing_public_profile_remains_none() -> None:
    plugin = register(FakeHermesContextWithoutProfile())

    assert plugin.get_hermes_context().profile_name is None


def test_invalid_public_profile_type_remains_rejected() -> None:
    plugin = register(FakeHermesContext(42))

    with pytest.raises(TypeError, match="profile_name must be a string"):
        plugin.get_hermes_context()


def test_runtime_context_does_not_create_personality_values() -> None:
    plugin = register(FakeHermesContext("minimal"))

    context = plugin.get_hermes_context()

    assert context.persona is None
    assert context.system_prompt is None
    assert context.preferred_language is None
    assert context.writing_style is None
    assert context.user_preferences == {}
    assert context.available_skills == ()
    assert context.available_tools == ()
    assert context.safety_rules == ()
    assert context.custom_instructions == ()


def test_runtime_context_reads_no_private_files(monkeypatch: pytest.MonkeyPatch) -> None:
    def block_file_read(*args, **kwargs):
        raise AssertionError(f"unexpected private file access: {args!r} {kwargs!r}")

    monkeypatch.setattr(builtins, "open", block_file_read)

    plugin = register(FakeHermesContext("offline-profile"))
    assert plugin.get_hermes_context().profile_name == "offline-profile"


def test_runtime_context_is_released_on_plugin_unload() -> None:
    context = FakeHermesContext("temporary-profile")
    plugin = register(context)

    assert len(context.unload_callbacks) == 1
    context.unload_callbacks[0]()

    assert plugin.context_source is None
    assert plugin.get_hermes_context() == HermesContext()
