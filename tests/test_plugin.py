from pathlib import Path

from hermes_email.plugin import register


class FakePluginContext:
    profile_name = "test-profile"

    def __init__(self) -> None:
        self.skills: list[tuple[str, Path, str]] = []
        self.commands = []
        self.tools = []
        self.unload_callbacks = []

    def get_config(self, key: str, default=None):
        return default

    def on_unload(self, callback) -> None:
        self.unload_callbacks.append(callback)

    def register_command(self, name: str, handler, description: str = "") -> None:
        self.commands.append((name, handler, description))

    def register_tool(self, **kwargs) -> object:
        self.tools.append(kwargs)
        return object()

    def register_skill(self, name: str, path: Path, *, description: str) -> None:
        self.skills.append((name, path, description))


def test_register_adds_bundled_skill_and_all_local_draft_and_read_tools() -> None:
    context = FakePluginContext()

    runtime = register(context)

    assert runtime.get_hermes_context().profile_name == "test-profile"
    assert len(context.unload_callbacks) == 1
    assert len(context.skills) == 1
    assert {tool["name"] for tool in context.tools} == {
        "email_list_messages",
        "email_get_message",
        "email_search_messages",
        "email_create_draft",
        "email_list_drafts",
        "email_get_draft",
        "email_update_draft",
        "email_trash_draft",
        "email_restore_draft",
    }
    name, path, description = context.skills[0]
    assert name == "email"
    assert path.name == "SKILL.md"
    assert path.exists()
    assert "active Hermes profile" in description
