"""Public seam for inheriting the active Hermes agent context."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class HermesContext:
    """Provider-neutral snapshot of agent-owned behavior and policy.

    Empty values mean that Hermes has not exposed the corresponding value via
    a stable public API. They must not be replaced with a plugin personality.
    """

    profile_name: str | None = None
    persona: str | None = None
    system_prompt: str | None = None
    preferred_language: str | None = None
    writing_style: str | None = None
    user_preferences: Mapping[str, Any] = field(default_factory=dict)
    available_skills: tuple[str, ...] = ()
    available_tools: tuple[str, ...] = ()
    safety_rules: tuple[str, ...] = ()
    custom_instructions: tuple[str, ...] = ()


@runtime_checkable
class HermesContextSource(Protocol):
    """Adapter interface for a future official Hermes context source."""

    def get_context(self) -> HermesContext:
        """Return an owned snapshot without exposing live Hermes objects."""
        ...


class ActiveProfileContextSource:
    """Minimal adapter using Hermes' public ``ctx.profile_name`` property.

    Hermes does not currently expose stable public plugin APIs for every field
    in :class:`HermesContext`. Those values remain unset rather than reading
    private runtime files or inventing defaults.
    """

    def __init__(self, plugin_context: Any) -> None:
        self._plugin_context = plugin_context

    def get_context(self) -> HermesContext:
        """Return the active public profile identity."""
        profile_name = getattr(self._plugin_context, "profile_name", None)
        if profile_name is not None and not isinstance(profile_name, str):
            raise TypeError("Hermes plugin context profile_name must be a string")
        return HermesContext(profile_name=profile_name)
