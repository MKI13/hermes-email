"""Hermes directory-plugin entry point."""

from .hermes_email.plugin import register

__all__ = ["register"]
