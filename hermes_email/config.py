"""Typed, safe-by-default configuration for Hermes Email."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, fields
from ipaddress import ip_address
from pathlib import Path
from typing import Any, Mapping, TypeVar

import yaml

from .addressing import (
    AddressValidationError,
    canonical_address,
    normalize_ascii_address,
    normalize_display_name,
)
from .secrets import InvalidSecretReferenceError, validate_secret_reference


class ConfigError(ValueError):
    """Raised when a configuration value is invalid."""


@dataclass(frozen=True, slots=True)
class EmailSettings:
    """Provider selection and non-destructive mailbox modes."""

    provider: str | None = None
    read_mode: str = "disabled"

    def __post_init__(self) -> None:
        if self.provider is not None and not isinstance(self.provider, str):
            raise ConfigError("email.provider must be a string or null")
        _choice("email.read_mode", self.read_mode, {"disabled", "mock", "readonly"})


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
class StorageSettings:
    """Opt-in local observation ledger limits and account namespace."""

    mode: str = "disabled"
    account_namespace: str | None = None
    retention_days: int = 90
    max_observations: int = 10_000
    max_database_bytes: int = 16_777_216

    def __post_init__(self) -> None:
        _choice("storage.mode", self.mode, {"disabled", "sqlite"})
        namespace = self.account_namespace
        if namespace is not None and (
            not isinstance(namespace, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", namespace) is None
        ):
            raise ConfigError(
                "storage.account_namespace must be 1 to 64 portable identifier characters"
            )
        if self.mode == "sqlite" and namespace is None:
            raise ConfigError(
                "storage.account_namespace is required when SQLite storage is enabled"
            )
        _bounded_integer("storage.retention_days", self.retention_days, 1, 3_650)
        _bounded_integer("storage.max_observations", self.max_observations, 1, 100_000)
        _bounded_integer(
            "storage.max_database_bytes",
            self.max_database_bytes,
            1_048_576,
            1_073_741_824,
        )


@dataclass(frozen=True, slots=True)
class DraftSettings:
    """Opt-in local draft database resource limits."""

    mode: str = "disabled"
    account_namespace: str | None = None
    max_drafts: int = 1_000
    max_operations: int = 10_000
    max_database_bytes: int = 33_554_432

    def __post_init__(self) -> None:
        _choice("drafts.mode", self.mode, {"disabled", "sqlite"})
        namespace = self.account_namespace
        if namespace is not None and (
            not isinstance(namespace, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", namespace) is None
        ):
            raise ConfigError(
                "drafts.account_namespace must be 1 to 64 portable identifier characters"
            )
        if self.mode == "sqlite" and namespace is None:
            raise ConfigError(
                "drafts.account_namespace is required when local drafts are enabled"
            )
        _bounded_integer("drafts.max_drafts", self.max_drafts, 1, 10_000)
        _bounded_integer("drafts.max_operations", self.max_operations, 1, 100_000)
        _bounded_integer(
            "drafts.max_database_bytes",
            self.max_database_bytes,
            1_048_576,
            268_435_456,
        )


@dataclass(frozen=True, slots=True)
class SmtpSettings:
    """Disconnected SMTP submission configuration with a fixed sender."""

    mode: str = "disabled"
    account_namespace: str | None = None
    host: str | None = None
    port: int = 465
    security: str = "implicit_tls"
    username_ref: str | None = None
    password_ref: str | None = None
    sender_address: str | None = None
    sender_display_name: str | None = None
    timeout_seconds: int = 15
    max_message_bytes: int = 1_000_000

    def __post_init__(self) -> None:
        _choice("smtp.mode", self.mode, {"disabled", "submission"})
        _choice("smtp.security", self.security, {"implicit_tls", "starttls"})
        _bounded_integer("smtp.port", self.port, 1, 65_535)
        _bounded_integer("smtp.timeout_seconds", self.timeout_seconds, 1, 120)
        _bounded_integer(
            "smtp.max_message_bytes", self.max_message_bytes, 1_024, 10_000_000
        )
        if self.account_namespace is not None:
            _portable_namespace("smtp.account_namespace", self.account_namespace)
        if self.host is not None:
            _validate_service_host("smtp.host", self.host)
        for field_name in ("username_ref", "password_ref"):
            reference = getattr(self, field_name)
            if reference is None:
                continue
            if not isinstance(reference, str):
                raise ConfigError(f"smtp.{field_name} must be a string or null")
            try:
                validate_secret_reference(reference)
                if not reference.startswith("HERMES_EMAIL_SMTP_"):
                    raise InvalidSecretReferenceError(
                        "SMTP secret references require their dedicated namespace"
                    )
            except InvalidSecretReferenceError as exc:
                raise ConfigError(
                    f"smtp.{field_name} must be a valid SMTP secret reference"
                ) from exc
        if (self.username_ref is None) != (self.password_ref is None):
            raise ConfigError("smtp username and password references must be configured together")
        if self.sender_address is not None:
            try:
                sender = normalize_ascii_address(self.sender_address)
            except AddressValidationError as exc:
                raise ConfigError("smtp.sender_address must be an ASCII email address") from exc
            object.__setattr__(self, "sender_address", sender)
        if self.sender_display_name is not None:
            try:
                display_name = normalize_display_name(self.sender_display_name)
            except AddressValidationError as exc:
                raise ConfigError("smtp.sender_display_name is invalid") from exc
            object.__setattr__(self, "sender_display_name", display_name)
        if self.mode == "submission":
            required = (
                self.account_namespace,
                self.host,
                self.username_ref,
                self.password_ref,
                self.sender_address,
            )
            if any(value is None for value in required):
                raise ConfigError(
                    "SMTP submission requires account namespace, host, credentials, and sender"
                )


@dataclass(frozen=True, slots=True)
class RecipientPolicySettings:
    """Deployment authorization for SMTP envelope recipients."""

    mode: str = "deny"
    allowed_addresses: tuple[str, ...] = ()
    allowed_domains: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _choice("recipient_policy.mode", self.mode, {"deny", "allowlist", "all"})
        addresses = _string_sequence(
            "recipient_policy.allowed_addresses", self.allowed_addresses, 100
        )
        domains = _string_sequence(
            "recipient_policy.allowed_domains", self.allowed_domains, 100
        )
        normalized_addresses = []
        for value in addresses:
            try:
                normalized_addresses.append(canonical_address(value))
            except AddressValidationError as exc:
                raise ConfigError(
                    "recipient_policy.allowed_addresses contains an invalid address"
                ) from exc
        normalized_domains = []
        for value in domains:
            if (
                not value.isascii()
                or value != value.strip()
                or value != value.casefold()
                or len(value) > 253
            ):
                raise ConfigError(
                    "recipient_policy.allowed_domains must contain lowercase ASCII domains"
                )
            try:
                normalize_ascii_address("local@" + value)
            except AddressValidationError as exc:
                raise ConfigError(
                    "recipient_policy.allowed_domains contains an invalid domain"
                ) from exc
            normalized_domains.append(value)
        if len(set(normalized_addresses)) != len(normalized_addresses):
            raise ConfigError("recipient_policy.allowed_addresses contains duplicates")
        if len(set(normalized_domains)) != len(normalized_domains):
            raise ConfigError("recipient_policy.allowed_domains contains duplicates")
        if self.mode == "allowlist" and not normalized_addresses and not normalized_domains:
            raise ConfigError("recipient allowlist must contain an address or domain")
        if self.mode != "allowlist" and (normalized_addresses or normalized_domains):
            raise ConfigError("recipient lists require recipient_policy.mode allowlist")
        object.__setattr__(self, "allowed_addresses", tuple(normalized_addresses))
        object.__setattr__(self, "allowed_domains", tuple(normalized_domains))

    def permits(self, address: str) -> bool:
        """Return whether one validated ASCII address is deployment-authorized."""
        if self.mode == "all":
            return True
        if self.mode == "deny":
            return False
        canonical = canonical_address(address)
        domain = canonical.rsplit("@", 1)[1]
        return canonical in self.allowed_addresses or domain in self.allowed_domains


@dataclass(frozen=True, slots=True)
class SenderClassificationSettings:
    """Operator-owned sender categories; never inferred from email content."""

    internal_addresses: tuple[str, ...] = ()
    internal_domains: tuple[str, ...] = ()
    customer_addresses: tuple[str, ...] = ()
    customer_domains: tuple[str, ...] = ()
    supplier_addresses: tuple[str, ...] = ()
    supplier_domains: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        address_fields = ("internal_addresses", "customer_addresses", "supplier_addresses")
        domain_fields = ("internal_domains", "customer_domains", "supplier_domains")
        normalized: dict[str, tuple[str, ...]] = {}
        for field_name in address_fields:
            values = _string_sequence(f"classification.{field_name}", getattr(self, field_name), 200)
            items: list[str] = []
            for value in values:
                try:
                    items.append(canonical_address(value))
                except AddressValidationError as exc:
                    raise ConfigError(f"classification.{field_name} contains an invalid address") from exc
            if len(items) != len(set(items)):
                raise ConfigError(f"classification.{field_name} contains duplicates")
            normalized[field_name] = tuple(items)
        for field_name in domain_fields:
            values = _string_sequence(f"classification.{field_name}", getattr(self, field_name), 200)
            items: list[str] = []
            for value in values:
                if not value.isascii() or value != value.strip() or value != value.casefold() or len(value) > 253:
                    raise ConfigError(f"classification.{field_name} must contain lowercase ASCII domains")
                try:
                    normalize_ascii_address("local@" + value)
                except AddressValidationError as exc:
                    raise ConfigError(f"classification.{field_name} contains an invalid domain") from exc
                items.append(value)
            if len(items) != len(set(items)):
                raise ConfigError(f"classification.{field_name} contains duplicates")
            normalized[field_name] = tuple(items)

        for field_name, values in normalized.items():
            object.__setattr__(self, field_name, values)
        for names, label in ((address_fields, "address"), (domain_fields, "domain")):
            seen: dict[str, str] = {}
            for field_name in names:
                for value in normalized[field_name]:
                    previous = seen.get(value)
                    if previous is not None:
                        raise ConfigError(f"classification {label} appears in multiple categories: {value}")
                    seen[value] = field_name


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
    storage: StorageSettings = field(default_factory=StorageSettings)
    drafts: DraftSettings = field(default_factory=DraftSettings)
    smtp: SmtpSettings = field(default_factory=SmtpSettings)
    recipient_policy: RecipientPolicySettings = field(
        default_factory=RecipientPolicySettings
    )
    classification: SenderClassificationSettings = field(default_factory=SenderClassificationSettings)
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
        if self.safety.allow_delete or self.safety.allow_move:
            raise ConfigError("mailbox delete and mailbox move are unavailable in this version")
        if self.smtp.mode == "submission":
            if self.drafts.mode != "sqlite":
                raise ConfigError("SMTP submission requires enabled local draft storage")
            if self.smtp.account_namespace != self.drafts.account_namespace:
                raise ConfigError("SMTP and draft account namespaces must match")
        if self.safety.allow_send and (
            self.smtp.mode != "submission" or self.recipient_policy.mode == "deny"
        ):
            raise ConfigError(
                "allow_send requires SMTP submission and an authorized recipient policy"
            )
        if self.storage.mode == "sqlite" and (
            normalized_provider is None
            or self.email.read_mode not in {"mock", "readonly"}
        ):
            raise ConfigError(
                "SQLite storage requires an explicitly readable email provider"
            )

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> EmailPluginConfig:
        """Build validated configuration from a YAML-compatible mapping."""
        raw: Mapping[str, Any] | object = {} if data is None else data
        if not isinstance(raw, Mapping):
            raise ConfigError("configuration root must be a mapping")
        _reject_unknown(
            "configuration",
            raw,
            {
                "email",
                "hermes",
                "credentials",
                "imap",
                "storage",
                "drafts",
                "smtp",
                "recipient_policy",
                "classification",
                "behavior",
                "safety",
            },
        )
        return cls(
            email=_build_section(EmailSettings, "email", raw.get("email")),
            hermes=_build_section(HermesSettings, "hermes", raw.get("hermes")),
            credentials=_build_section(
                CredentialReferences, "credentials", raw.get("credentials")
            ),
            imap=_build_section(ImapSettings, "imap", raw.get("imap")),
            storage=_build_section(StorageSettings, "storage", raw.get("storage")),
            drafts=_build_section(DraftSettings, "drafts", raw.get("drafts")),
            smtp=_build_section(SmtpSettings, "smtp", raw.get("smtp")),
            recipient_policy=_build_section(
                RecipientPolicySettings,
                "recipient_policy",
                raw.get("recipient_policy"),
            ),
            classification=_build_section(
                SenderClassificationSettings, "classification", raw.get("classification")
            ),
            behavior=_build_section(BehaviorSettings, "behavior", raw.get("behavior")),
            safety=_build_section(SafetySettings, "safety", raw.get("safety")),
        )


Section = TypeVar(
    "Section",
    EmailSettings,
    HermesSettings,
    CredentialReferences,
    ImapSettings,
    StorageSettings,
    DraftSettings,
    SmtpSettings,
    RecipientPolicySettings,
    SenderClassificationSettings,
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
    _validate_service_host("imap.host", host)


def _validate_service_host(name: str, host: str) -> None:
    if not isinstance(host, str) or host != host.strip() or not host or len(host) > 253:
        raise ConfigError(f"{name} must be a non-empty ASCII hostname")
    try:
        host.encode("ascii")
    except UnicodeEncodeError:
        raise ConfigError(f"{name} must be an ASCII hostname") from None
    if ":" in host or all(character.isdigit() or character == "." for character in host):
        try:
            ip_address(host)
        except ValueError:
            raise ConfigError(f"{name} must be a valid hostname or IP address") from None
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
        raise ConfigError(f"{name} must be a valid hostname or IP address")


def _portable_namespace(name: str, value: object) -> str:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", value) is None
    ):
        raise ConfigError(f"{name} must be 1 to 64 portable identifier characters")
    return value


def _string_sequence(name: str, value: object, maximum: int) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or len(value) > maximum:
        raise ConfigError(f"{name} must be a bounded string list")
    if any(not isinstance(item, str) for item in value):
        raise ConfigError(f"{name} must be a bounded string list")
    return tuple(value)


def _bounded_integer(name: str, value: Any, minimum: int, maximum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{name} must be an integer")
    if not minimum <= value <= maximum:
        raise ConfigError(f"{name} must be between {minimum} and {maximum}")


def _choice(name: str, value: Any, allowed: set[str]) -> None:
    if not isinstance(value, str) or value not in allowed:
        choices = ", ".join(sorted(allowed))
        raise ConfigError(f"{name} must be one of: {choices}")
