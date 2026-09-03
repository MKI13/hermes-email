"""Typed, safe-by-default configuration for Hermes Email."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from ipaddress import ip_address
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
        _choice("email.read_mode", self.read_mode, {"disabled", "mock", "readonly"})
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
    """Optional provider-neutral credential references, never their values."""

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
class ImapSettings:
    """Verified implicit-TLS settings for one read-only IMAP mailbox."""

    host: str | None = None
    port: int = 993
    security: str = "tls"
    username_ref: str | None = None
    password_ref: str | None = None
    mailbox: str = "INBOX"
    timeout_seconds: int = 15
    max_mailbox_messages: int = 10_000
    max_message_bytes: int = 2_000_000
    max_page_bytes: int = 5_000_000

    def __post_init__(self) -> None:
        if self.host is not None:
            _validate_imap_host(self.host)
        if isinstance(self.port, bool) or not isinstance(self.port, int):
            raise ConfigError("imap.port must be an integer")
        if not 1 <= self.port <= 65_535:
            raise ConfigError("imap.port must be between 1 and 65535")
        _choice("imap.security", self.security, {"tls"})
        for field_name in ("username_ref", "password_ref"):
            reference = getattr(self, field_name)
            if reference is None:
                continue
            if not isinstance(reference, str):
                raise ConfigError(f"imap.{field_name} must be a string or null")
            try:
                validate_secret_reference(reference)
            except InvalidSecretReferenceError as exc:
                raise ConfigError(
                    f"imap.{field_name} must be a valid Hermes Email secret reference"
                ) from exc
        if (self.username_ref is None) != (self.password_ref is None):
            raise ConfigError(
                "imap.username_ref and imap.password_ref must be configured together"
            )
        if (
            not isinstance(self.mailbox, str)
            or not self.mailbox
            or len(self.mailbox) > 255
            or any(ord(character) < 32 or ord(character) > 126 for character in self.mailbox)
        ):
            raise ConfigError("imap.mailbox must be 1 to 255 printable ASCII characters")
        _bounded_integer("imap.timeout_seconds", self.timeout_seconds, 1, 120)
        _bounded_integer(
            "imap.max_mailbox_messages", self.max_mailbox_messages, 1, 50_000
        )
        _bounded_integer(
            "imap.max_message_bytes", self.max_message_bytes, 4_096, 10_000_000
        )
        _bounded_integer(
            "imap.max_page_bytes", self.max_page_bytes, 409_600, 20_000_000
        )


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
    imap: ImapSettings = field(default_factory=ImapSettings)
    behavior: BehaviorSettings = field(default_factory=BehaviorSettings)
    safety: SafetySettings = field(default_factory=SafetySettings)

    def __post_init__(self) -> None:
        provider = self.email.provider
        normalized_provider = provider.strip().casefold() if isinstance(provider, str) else None
        if normalized_provider == "imap":
            if self.email.read_mode not in {"disabled", "readonly"}:
                raise ConfigError("email.read_mode must be readonly or disabled for imap")
            if self.imap.host is None:
                raise ConfigError("imap.host is required for the imap provider")
            if self.imap.username_ref is None or self.imap.password_ref is None:
                raise ConfigError(
                    "imap username and password references are required for the imap provider"
                )
        if normalized_provider == "mock" and self.email.read_mode == "readonly":
            raise ConfigError("email.read_mode readonly requires a non-mock provider")

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> EmailPluginConfig:
        """Build validated configuration from a YAML-compatible mapping."""
        raw = data or {}
        if not isinstance(raw, Mapping):
            raise ConfigError("configuration root must be a mapping")
        _reject_unknown(
            "configuration",
            raw,
            {"email", "hermes", "credentials", "imap", "behavior", "safety"},
        )
        return cls(
            email=_build_section(EmailSettings, "email", raw.get("email")),
            hermes=_build_section(HermesSettings, "hermes", raw.get("hermes")),
            credentials=_build_section(
                CredentialReferences, "credentials", raw.get("credentials")
            ),
            imap=_build_section(ImapSettings, "imap", raw.get("imap")),
            behavior=_build_section(BehaviorSettings, "behavior", raw.get("behavior")),
            safety=_build_section(SafetySettings, "safety", raw.get("safety")),
        )


Section = TypeVar(
    "Section",
    EmailSettings,
    HermesSettings,
    CredentialReferences,
    ImapSettings,
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


def _validate_imap_host(host: str) -> None:
    if not isinstance(host, str) or host != host.strip() or not host or len(host) > 253:
        raise ConfigError("imap.host must be a non-empty ASCII hostname")
    try:
        host.encode("ascii")
    except UnicodeEncodeError:
        raise ConfigError("imap.host must be an ASCII hostname") from None
    if ":" in host or all(character.isdigit() or character == "." for character in host):
        try:
            ip_address(host)
        except ValueError:
            raise ConfigError("imap.host must be a valid hostname or IP address") from None
        return
    labels = host.rstrip(".").split(".")
    if any(
        not label
        or len(label) > 63
        or not label[0].isalnum()
        or not label[-1].isalnum()
        or any(not character.isalnum() and character != "-" for character in label)
        for label in labels
    ):
        raise ConfigError("imap.host must be a valid hostname or IP address")


def _bounded_integer(name: str, value: Any, minimum: int, maximum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{name} must be an integer")
    if not minimum <= value <= maximum:
        raise ConfigError(f"{name} must be between {minimum} and {maximum}")


def _choice(name: str, value: Any, allowed: set[str]) -> None:
    if not isinstance(value, str) or value not in allowed:
        choices = ", ".join(sorted(allowed))
        raise ConfigError(f"{name} must be one of: {choices}")
