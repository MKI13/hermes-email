"""Typed, safe-by-default configuration for Hermes Email."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Mapping, TypeVar

import yaml

from .secrets import InvalidSecretReferenceError, validate_secret_reference


class ConfigError(ValueError):
    """Raised when a configuration value is invalid."""


@dataclass(frozen=True, slots=True)
class EmailSettings:
    """Provider selection and non-destructive mailbox modes."""

    provider: str | None = None
    read_mode: str = "disabled"
    draft_mode: str = "mock"

    def __post_init__(self) -> None:
        if self.provider is not None and not isinstance(self.provider, str):
            raise ConfigError("email.provider must be a string or null")
        _choice("email.read_mode", self.read_mode, {"disabled", "mock"})
        _choice("email.draft_mode", self.draft_mode, {"disabled", "mock"})


@dataclass(frozen=True, slots=True)
class HermesSettings:
    """Hermes profile selection without private runtime coupling."""

    profile: str = "auto"

    def __post_init__(self) -> None:
        profile = getattr(self, "profile")
        if not isinstance(profile, str) or not profile.strip():
            raise ConfigError("Hermes profile must be a non-empty string")


@dataclass(frozen=True, slots=True)
class CredentialReferences:
    """Optional references to future provider credentials, never their values."""

    username_ref: str | None = None
    password_ref: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("username_ref", "password_ref"):
            reference = getattr(self, field_name)
            if reference is None:
                continue
            if not isinstance(reference, str):
                raise ConfigError(f"credentials.{field_name} must be a string or null")
            try:
                validate_secret_reference(reference)
            except InvalidSecretReferenceError as exc:
                raise ConfigError(
                    f"credentials.{field_name} must be a valid Hermes Email secret reference"
                ) from exc


@dataclass(frozen=True, slots=True)
class BehaviorSettings:
    """Controls which active Hermes characteristics should be inherited."""

    inherit_persona: bool = True
    inherit_language: bool = True
    inherit_style: bool = True
    inherit_user_preferences: bool = True
    inherit_safety_rules: bool = True

    def __post_init__(self) -> None:
        _validate_booleans("behavior", self)


@dataclass(frozen=True, slots=True)
class SafetySettings:
    """Explicit gates for destructive or external side effects."""

    allow_send: bool = False
    allow_delete: bool = False
    allow_move: bool = False

    def __post_init__(self) -> None:
        _validate_booleans("safety", self)


@dataclass(frozen=True, slots=True)
class EmailPluginConfig:
    """Complete validated plugin configuration."""

    email: EmailSettings = field(default_factory=EmailSettings)
    hermes: HermesSettings = field(default_factory=HermesSettings)
    credentials: CredentialReferences = field(default_factory=CredentialReferences)
    behavior: BehaviorSettings = field(default_factory=BehaviorSettings)
    safety: SafetySettings = field(default_factory=SafetySettings)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> EmailPluginConfig:
        """Build validated configuration from a YAML-compatible mapping."""
        raw = data or {}
        if not isinstance(raw, Mapping):
            raise ConfigError("configuration root must be a mapping")
        _reject_unknown(
            "configuration", raw, {"email", "hermes", "credentials", "behavior", "safety"}
        )
        return cls(
            email=_build_section(EmailSettings, "email", raw.get("email")),
            hermes=_build_section(HermesSettings, "hermes", raw.get("hermes")),
            credentials=_build_section(
                CredentialReferences, "credentials", raw.get("credentials")
            ),
            behavior=_build_section(BehaviorSettings, "behavior", raw.get("behavior")),
            safety=_build_section(SafetySettings, "safety", raw.get("safety")),
        )


Section = TypeVar(
    "Section",
    EmailSettings,
    HermesSettings,
    CredentialReferences,
    BehaviorSettings,
    SafetySettings,
)


def load_config(path: str | Path) -> EmailPluginConfig:
    """Load and validate a UTF-8 YAML configuration file."""
    config_path = Path(path)
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"could not load configuration: {exc}") from exc
    return EmailPluginConfig.from_mapping(data)


def _build_section(section_type: type[Section], name: str, value: Any) -> Section:
    if value is None:
        return section_type()
    if not isinstance(value, Mapping):
        raise ConfigError(f"{name} must be a mapping")
    allowed = {field.name for field in fields(section_type)}
    _reject_unknown(name, value, allowed)
    return section_type(**dict(value))


def _reject_unknown(name: str, value: Mapping[str, Any], allowed: set[str]) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ConfigError(f"unknown {name} key(s): {', '.join(unknown)}")


def _validate_booleans(name: str, value: object) -> None:
    for field in fields(value):
        if not isinstance(getattr(value, field.name), bool):
            raise ConfigError(f"{name}.{field.name} must be a boolean")


def _choice(name: str, value: Any, allowed: set[str]) -> None:
    if not isinstance(value, str) or value not in allowed:
        choices = ", ".join(sorted(allowed))
        raise ConfigError(f"{name} must be one of: {choices}")
