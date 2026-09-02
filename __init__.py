"""Hermes directory-plugin entry point."""

if __package__:
    from .hermes_email.plugin import register
else:
    from hermes_email.plugin import register

__all__ = ["register"]
