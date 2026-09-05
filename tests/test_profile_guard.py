from __future__ import annotations

import pytest

from hermes_email.profile_guard import (
    ProfileBlockedRuntime,
    evaluate_profile_policy,
    register,
)


class Handle:
    def __init__(self) -> None:
        self.disposed = False

    def dispose(self) -> None:
        self.disposed = True


class ForbiddenState:
    def __getattribute__(self, name):
        raise AssertionError(f"profile denial must not access ctx.state.{name}")


class FakeContext:
    def __init__(self, profile_name: str | None, settings: dict[str, object]) -> None:
        self.profile_name = profile_name
        self.settings = settings
        self.state = ForbiddenState()
        self.commands: dict[str, object] = {}
        self.unload_callbacks = []
        self.tool_registrations = 0
        self.skill_registrations = 0

    def get_config(self, name, default=None):
        return self.settings.get(name, default)

    def on_unload(self, callback):
        self.unload_callbacks.append(callback)
        return Handle()

    def register_command(self, name, *, handler, description):
        del description
        self.commands[name] = handler
        return Handle()

    def register_tool(self, **kwargs):
        del kwargs
        self.tool_registrations += 1
        raise AssertionError("blocked profile must not register email tools")

    def register_skill(self, *args, **kwargs):
        del args, kwargs
        self.skill_registrations += 1
        raise AssertionError("blocked profile must not register the email skill")


class ExplodingConfigContext(FakeContext):
    def get_config(self, name, default=None):
        del name, default
        raise RuntimeError("synthetic config lookup failure")


def production_settings(profile: str = "ef-sinn-email") -> dict[str, object]:
    return {
        "hermes": {"profile": profile},
        "email": {"provider": "imap", "read_mode": "readonly"},
        "drafts": {"mode": "sqlite", "account_namespace": "primary-account"},
        "smtp": {"mode": "submission"},
    }


def test_explicit_mail_profile_allows_only_exact_active_profile() -> None:
    allowed = evaluate_profile_policy(FakeContext("ef-sinn-email", production_settings()))
    denied = evaluate_profile_policy(FakeContext("ef-sinn-main", production_settings()))

    assert allowed.allowed is True
    assert allowed.required_profile == "ef-sinn-email"
    assert denied.allowed is False
    assert denied.required_profile == "ef-sinn-email"
    assert denied.diagnostic == "profile-not-authorized"


def test_production_capabilities_require_explicit_profile_binding() -> None:
    context = FakeContext(
        "ef-sinn-email",
        {
            "hermes": {"profile": "auto"},
            "email": {"provider": "imap", "read_mode": "readonly"},
        },
    )

    decision = evaluate_profile_policy(context)

    assert decision.allowed is False
    assert decision.diagnostic == "explicit-profile-required"


def test_mock_development_may_keep_auto_profile() -> None:
    context = FakeContext(
        "development",
        {
            "hermes": {"profile": "auto"},
            "email": {"provider": "mock", "read_mode": "mock"},
        },
    )

    decision = evaluate_profile_policy(context)

    assert decision.allowed is True
    assert decision.development_auto is True


def test_profile_mismatch_registers_only_safe_status_without_state_access() -> None:
    context = FakeContext("ef-sinn-main", production_settings())

    runtime = register(context)

    assert isinstance(runtime, ProfileBlockedRuntime)
    assert context.tool_registrations == 0
    assert context.skill_registrations == 0
    assert set(context.commands) == {"email-status"}
    status = context.commands["email-status"]("")
    assert "Status: profile-blocked" in status
    assert "Profile: ef-sinn-main" in status
    assert "Authorized profile: ef-sinn-email" in status
    assert "Diagnostic: profile-not-authorized" in status
    assert "Read: disabled" in status
    assert "Draft: disabled" in status
    assert "SMTP: disabled" in status


def test_auto_production_denial_does_not_register_mail_surfaces() -> None:
    context = FakeContext(
        "ef-sinn-email",
        {
            "hermes": {"profile": "auto"},
            "drafts": {"mode": "sqlite", "account_namespace": "primary-account"},
        },
    )

    runtime = register(context)

    assert isinstance(runtime, ProfileBlockedRuntime)
    assert runtime.decision.diagnostic == "explicit-profile-required"
    assert context.tool_registrations == 0
    assert context.skill_registrations == 0


def test_config_lookup_failure_is_never_treated_as_safe_auto_mode() -> None:
    context = ExplodingConfigContext("ef-sinn-email", {})

    decision = evaluate_profile_policy(context)
    runtime = register(context)

    assert decision.allowed is False
    assert decision.diagnostic == "invalid-profile-policy"
    assert isinstance(runtime, ProfileBlockedRuntime)
    assert context.tool_registrations == 0
    assert context.skill_registrations == 0


@pytest.mark.parametrize("active_profile", [None, "", "bad profile", "bad/profile"])
def test_invalid_active_profile_fails_closed_for_explicit_owner(active_profile: str | None) -> None:
    decision = evaluate_profile_policy(FakeContext(active_profile, production_settings()))

    assert decision.allowed is False
    assert decision.required_profile == "ef-sinn-email"
    assert decision.diagnostic == "invalid-active-profile"


@pytest.mark.parametrize(
    "profile",
    ["", " auto ", "bad profile", "bad/profile", "x" * 129, 42],
)
def test_invalid_explicit_profile_policy_fails_closed(profile: object) -> None:
    context = FakeContext(
        "ef-sinn-email",
        {
            "hermes": {"profile": profile},
            "email": {"provider": "imap", "read_mode": "readonly"},
        },
    )

    decision = evaluate_profile_policy(context)

    assert decision.allowed is False
    assert decision.diagnostic == "invalid-profile-policy"
