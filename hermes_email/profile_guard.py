"""Official-entrypoint profile isolation for Hermes Email.

Production mail capabilities are bound to one explicit Hermes profile before
provider resolution, durable-store construction, secret access, or tool
registration.  Mock/development-only configurations may keep ``profile: auto``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Mapping

from . import __version__

_MISSING: Final = object()
_MAX_PROFILE_LENGTH: Final = 128


@dataclass(frozen=True, slots=True)
class ProfilePolicyDecision:
    allowed: bool
    current_profile: str | None
    required_profile: str | None
    diagnostic: str | None
    development_auto: bool = False


class ProfileBlockedRuntime:
    """Minimal runtime returned when profile policy fails closed."""

    def __init__(self, decision: ProfilePolicyDecision) -> None:
        self.decision = decision
        self.closed = False

    def close(self) -> None:
        self.closed = True


def evaluate_profile_policy(ctx: Any) -> ProfilePolicyDecision:
    """Evaluate profile ownership without touching provider, state, or secrets."""
    current = getattr(ctx, "profile_name", None)
    if current is not None and not isinstance(current, str):
        return ProfilePolicyDecision(False, None, None, "invalid-active-profile")

    hermes = _config_section(ctx, "hermes")
    if hermes is _MISSING:
        configured = "auto"
    elif not isinstance(hermes, Mapping):
        return ProfilePolicyDecision(False, current, None, "invalid-profile-policy")
    else:
        configured = hermes.get("profile", "auto")

    if configured == "auto":
        if _production_capability_configured(ctx):
            return ProfilePolicyDecision(
                False,
                current,
                None,
                "explicit-profile-required",
            )
        return ProfilePolicyDecision(True, current, None, None, development_auto=True)

    if not _valid_profile_identifier(configured):
        return ProfilePolicyDecision(False, current, None, "invalid-profile-policy")
    if current != configured:
        return ProfilePolicyDecision(
            False,
            current,
            configured,
            "profile-not-authorized",
        )
    return ProfilePolicyDecision(True, current, configured, None)


def register(ctx: Any) -> Any:
    """Register Hermes Email only when the active profile owns mail access."""
    decision = evaluate_profile_policy(ctx)
    if not decision.allowed:
        return _register_blocked(ctx, decision)
    return _register_authorized(ctx, decision)


def _register_authorized(ctx: Any, decision: ProfilePolicyDecision) -> Any:
    # Import the core runtime only after profile authorization has succeeded.
    # This preserves the no-provider/no-database/no-secret boundary on denials.
    from .draft_tools import register_draft_tools
    from .plugin import _create_runtime_plugin
    from .tools import register_read_tools

    runtime = _create_runtime_plugin(ctx)

    def release_runtime_context() -> None:
        runtime.close()

    unload_handle: Any | None = None
    command_handle: Any | None = None
    draft_handles: tuple[Any, ...] = ()
    read_handles: tuple[Any, ...] = ()
    try:
        unload_handle = ctx.on_unload(release_runtime_context)
        command_handle = ctx.register_command(
            "email-status",
            handler=lambda raw_args: _authorized_status(runtime, decision, raw_args),
            description="Show safe Hermes Email runtime and profile-isolation status.",
        )
        draft_handles = register_draft_tools(ctx, runtime)
        read_handles = register_read_tools(ctx, runtime)
        skill_path = Path(__file__).resolve().parent.parent / "skill" / "SKILL.md"
        ctx.register_skill(
            "email",
            skill_path,
            description="Handle email only inside the authorized Hermes mail profile.",
        )
    except Exception:
        for handle in reversed(read_handles + draft_handles):
            handle.dispose()
        if command_handle is not None:
            command_handle.dispose()
        if unload_handle is not None:
            unload_handle.dispose()
        runtime.close()
        raise
    return runtime


def _register_blocked(ctx: Any, decision: ProfilePolicyDecision) -> ProfileBlockedRuntime:
    # Intentionally do not import or instantiate EmailPlugin here. In
    # particular, do not inspect ctx.state.data_dir, create SQLite stores,
    # resolve a provider, register mail tools, or register the email skill.
    runtime = ProfileBlockedRuntime(decision)
    unload_handle: Any | None = None
    command_handle: Any | None = None
    try:
        unload_handle = ctx.on_unload(runtime.close)
        command_handle = ctx.register_command(
            "email-status",
            handler=lambda raw_args: _blocked_status(runtime, raw_args),
            description="Show why Hermes Email is blocked in this profile.",
        )
    except Exception:
        if command_handle is not None:
            command_handle.dispose()
        if unload_handle is not None:
            unload_handle.dispose()
        runtime.close()
        raise
    return runtime


def _authorized_status(runtime: Any, decision: ProfilePolicyDecision, raw_args: str) -> str:
    del raw_args
    status = runtime.get_runtime_status()
    required = decision.required_profile or "auto (development-only)"
    lines = [
        "Hermes Email",
        f"Version: {status.version}",
        f"Status: {status.state.value}",
        f"Provider: {status.provider or 'none'}",
        f"Profile: {status.profile or 'none'}",
        f"Authorized profile: {required}",
        "Profile isolation: enforced" if decision.required_profile else "Profile isolation: development-auto",
    ]
    if status.diagnostic is not None:
        lines.append(f"Diagnostic: {status.diagnostic}")
    if status.draft_diagnostic is not None:
        lines.append(f"Draft diagnostic: {status.draft_diagnostic}")
    lines.extend(
        (
            f"Read: {'enabled' if status.read_enabled else 'disabled'}",
            f"Storage: {'enabled' if status.storage_enabled else 'disabled'}",
            f"Draft: {'enabled' if status.draft_enabled else 'disabled'}",
            f"SMTP: {'configured' if status.smtp_configured else 'disabled'}",
            "Technical send gates: "
            + ("armed" if status.technical_send_armed else "disabled"),
            "Send: unavailable",
        )
    )
    return "\n".join(lines)


def _blocked_status(runtime: ProfileBlockedRuntime, raw_args: str) -> str:
    del raw_args
    decision = runtime.decision
    required = decision.required_profile or "explicit profile required"
    return "\n".join(
        (
            "Hermes Email",
            f"Version: {__version__}",
            "Status: profile-blocked",
            "Provider: none",
            f"Profile: {decision.current_profile or 'none'}",
            f"Authorized profile: {required}",
            f"Diagnostic: {decision.diagnostic or 'profile-not-authorized'}",
            "Profile isolation: enforced",
            "Read: disabled",
            "Storage: disabled",
            "Draft: disabled",
            "SMTP: disabled",
            "Technical send gates: disabled",
            "Send: unavailable",
        )
    )


def _production_capability_configured(ctx: Any) -> bool:
    email = _config_section(ctx, "email")
    if email is not _MISSING:
        if not isinstance(email, Mapping):
            return True
        provider = email.get("provider")
        read_mode = email.get("read_mode", "disabled")
        if provider not in {None, "", "mock"} or read_mode == "readonly":
            return True

    for section_name in ("storage", "drafts"):
        section = _config_section(ctx, section_name)
        if section is _MISSING:
            continue
        if not isinstance(section, Mapping):
            return True
        if section.get("mode", "disabled") != "disabled":
            return True

    smtp = _config_section(ctx, "smtp")
    if smtp is not _MISSING:
        if not isinstance(smtp, Mapping):
            return True
        if smtp.get("mode", "disabled") != "disabled":
            return True

    safety = _config_section(ctx, "safety")
    if safety is not _MISSING:
        if not isinstance(safety, Mapping):
            return True
        if safety.get("allow_send", False) is not False:
            return True
    return False


def _config_section(ctx: Any, name: str) -> object:
    try:
        return ctx.get_config(name, default=_MISSING)
    except Exception:
        # Configuration lookup failure is never a reason to weaken isolation.
        return {"__invalid__": True}


def _valid_profile_identifier(value: object) -> bool:
    if not isinstance(value, str) or not value or len(value) > _MAX_PROFILE_LENGTH:
        return False
    if value != value.strip() or value == "auto":
        return False
    return all(character.isalnum() or character in "._-" for character in value)
