# Trusted local user approval

Current package version: 0.43.0.

## Supported identity boundary

The initial supported approval surface is the foreground local terminal of the
OS user running the selected Hermes profile. It uses the official
`PluginContext.register_cli_command` API, not a model tool, slash command,
mail message, model-provided `confirmed` boolean or generic cached tool approval.
Piped input, unavailable TTYs and unsupported hosts fail closed. No cloud model
is called. The generic Hermes tool-approval gate is deliberately not reused:
its session/permanent and unattended approvals are inappropriate for email.

`hermes -p <profile> email-approve <draft_id>` exercises a full human review,
then discards its grant. It does NOT send and produces no transferable token.
The complete reviewed-send command is the next workflow integration step.

The operator must configure an exact profile owner, local drafts and a fixed
SMTP account/sender for a meaningful preview. This command never resolves
credentials, opens a provider or creates a send intent.

## Binding and lifecycle

An immutable snapshot covers the full stored draft, all To/Cc/Bcc recipients,
subject, complete body, reply headers, sender, account and deployment settings.
The terminal renders mail as escaped JSON, without truncating body or Bcc.
It displays a fresh random challenge AFTER snapshot creation. Pre-typed input
is discarded; only that exact challenge answer approves. Permission is bound
to profile, canonical profile data directory, OS principal and terminal session.

The host-private authority keeps a random HMAC key and pending grants only in
memory. A grant expires after at most 300 seconds, is consumed at most once,
cannot cross authorities/processes/restarts/forks, and becomes invalid on any
snapshot change. No session-wide, permanent, smart-model or cron approval exists.
A restart requires a new review, never reconstruction of consent from mail text.
Low-level `UserSendConfirmation` is an internal compatibility marker, not proof
of authenticated human identity. Host sending MUST pass through this authority;
it must never expose raw candidate preparation or `send_once` to a model.

## Deliberate limits

An OS user with arbitrary code/terminal control is part of the trusted computing
base. A TTY is NOT proof of a physical human against malicious same-user Python,
PTY automation, a compromised terminal or a compromised agent with arbitrary
shell access. Use separate OS identities/permission boundaries for that threat.
This release does not silently claim those guarantees or modify Hermes core.

Gateway/Telegram/desktop approvals are not enabled: a routing session string is
not enough to authenticate the approving person. Such adapters need an explicit
host-authenticated human response and tested per-user/session provenance before
being exposed. Lack of that adapter denies approval, never falls back to a model.

## Evidence

Tests cover exact/single-use grants, changed content/policy/sender/endpoint,
wrong profile/principal/session, forged grants, timeout and backward clocks,
restart/fork rejection, real controlling-terminal acceptance/denial, non-TTY
refusal, and registration/unload through the actual pinned Hermes PluginContext.
These are deterministic integration tests, not claims of physical human testing.
