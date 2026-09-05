import ast
import importlib.util
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_email.plugin import register
from hermes_email.secrets import EnvironmentSecretResolver
from hermes_email.smtp import SmtplibTransport


class Handle:
    def dispose(self) -> None:
        pass


class Context:
    profile_name = "smtp-profile"

    def __init__(self, tmp_path: Path, settings) -> None:
        self._settings = settings
        self.state = SimpleNamespace(data_dir=tmp_path / "plugin-data")
        self.tools = []
        self.commands = []
        self.skills = []
        self.unloads = []

    def get_config(self, key, default=None):
        return self._settings.get(key, default)

    def register_tool(self, **kwargs):
        self.tools.append(kwargs)
        return Handle()

    def register_command(self, name, handler, description=""):
        self.commands.append((name, handler, description))
        return Handle()

    def register_skill(self, name, path, *, description):
        self.skills.append((name, path, description))
        return Handle()

    def on_unload(self, callback):
        self.unloads.append(callback)
        return Handle()


def armed_settings():
    return {
        "drafts": {"mode": "sqlite", "account_namespace": "smtp-account"},
        "smtp": {
            "mode": "submission",
            "account_namespace": "smtp-account",
            "host": "smtp.example.invalid",
            "port": 465,
            "security": "implicit_tls",
            "username_ref": "HERMES_EMAIL_SMTP_USERNAME",
            "password_ref": "HERMES_EMAIL_SMTP_PASSWORD",
            "sender_address": "sender@example.invalid",
        },
        "recipient_policy": {"mode": "all"},
        "safety": {"allow_send": True},
    }


def test_armed_runtime_is_offline_and_exposes_no_submission_surface(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError(f"unexpected external operation: {args!r} {kwargs!r}")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(EnvironmentSecretResolver, "get_secret", forbidden)
    monkeypatch.setattr(SmtplibTransport, "check_health", forbidden)
    monkeypatch.setattr(SmtplibTransport, "submit_once", forbidden)
    context = Context(tmp_path, armed_settings())

    runtime = register(context)
    status = runtime.get_runtime_status()
    output = context.commands[0][1]("")

    assert status.smtp_configured is True
    assert status.technical_send_armed is True
    assert status.send_enabled is False
    assert "SMTP: configured" in output
    assert "Technical send gates: armed" in output
    assert "Send: unavailable" in output
    assert "smtp.example.invalid" not in output
    assert "HERMES_EMAIL" not in output
    assert len(context.tools) == 9
    assert all("send" not in tool["name"] for tool in context.tools)
    assert [command[0] for command in context.commands] == ["email-status"]
    assert not context.state.data_dir.exists()


def test_hermes_entry_import_closure_cannot_reach_smtp_dispatch() -> None:
    repository = Path(__file__).parents[1]
    package = repository / "hermes_email"
    queue = [("plugin_entry", repository / "__init__.py")]
    visited = set()
    called_names = set()

    def queue_module(candidate: str) -> None:
        if not candidate.startswith("hermes_email"):
            return
        relative = candidate.split(".")[1:]
        target_path = package.joinpath(*relative)
        if target_path.with_suffix(".py").is_file():
            queue.append((candidate, target_path.with_suffix(".py")))
        elif target_path.joinpath("__init__.py").is_file():
            queue.append((candidate, target_path / "__init__.py"))

    while queue:
        module_name, path = queue.pop()
        if module_name in visited:
            continue
        visited.add(module_name)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    called_names.add(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    called_names.add(node.func.attr)
            if isinstance(node, ast.Import):
                for alias in node.names:
                    queue_module(alias.name)
                continue
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.level:
                current_package = (
                    module_name
                    if path.name == "__init__.py"
                    else module_name.rpartition(".")[0]
                )
                target = importlib.util.resolve_name(
                    "." * node.level + (node.module or ""), current_package
                )
            else:
                target = node.module or ""
            candidates = [target]
            candidates.extend(
                target + "." + alias.name
                for alias in node.names
                if alias.name != "*"
            )
            for candidate in candidates:
                queue_module(candidate)

    assert "hermes_email.smtp" not in visited
    assert "hermes_email.sending" not in visited
    assert called_names.isdisjoint(
        {
            "SmtplibTransport",
            "prepare_send_candidate",
            "submit_once",
            "import_module",
            "__import__",
            "eval",
            "exec",
        }
    )
