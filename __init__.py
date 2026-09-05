"""Hermes directory-plugin entry point with fail-closed profile isolation."""

if __package__:
    from .hermes_email.profile_guard import register
else:
    from hermes_email.profile_guard import register

__all__ = ["register"]
