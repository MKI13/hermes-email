# Architecture

## Purpose

Hermes Email separates technical email infrastructure from agent behavior. The active Hermes agent remains responsible for language, persona, reasoning, user preferences, and decisions.

## Components

### Hermes directory-plugin entry point

The root `plugin.yaml` and `__init__.py` follow Hermes' native standalone plugin convention. `register(ctx)` currently registers only the bundled skill. It deliberately creates no network client, tool, hook, poller, or long-lived task.

### Plugin facade

`hermes_email.plugin.EmailPlugin` is the future orchestration point. It owns validated configuration, an optional provider, and an optional Hermes context source. In version 0.1.0 it can prepare an in-memory draft value and refuses every send attempt.

Future technical responsibilities belong behind this facade:

- provider connection lifecycle;
- message ingestion and normalized events;
- draft storage;
- status tracking and deduplication;
- privacy-aware logging;
- independent safety authorization.

These responsibilities are documented seams, not implemented subsystems in version 0.1.0.

### Provider abstraction

`hermes_email.providers.EmailProvider` defines asynchronous methods for fetching message summaries, retrieving one message, creating a draft, and sending a stored draft. No concrete provider ships in version 0.1.0.

Future IMAP, SMTP, Gmail, Microsoft, Proton Bridge, or other adapters must normalize provider data into `EmailMessage` and `EmailDraft`. A provider's declared capability is never sufficient authorization for an external or destructive action.

### Hermes context adapter

`HermesContext` holds optional values for profile name, persona, system prompt, language, writing style, user preferences, skills, tools, safety rules, and custom instructions.

Hermes currently exposes `ctx.profile_name` as a stable public plugin API. `ActiveProfileContextSource` reads only that property. Other values remain empty until Hermes provides an appropriate public API or an explicit caller supplies them. The project must not read private Hermes files, retain live runtime objects, or invent a fallback personality.

### Email skill

`skill/SKILL.md` describes mail-specific behavior while explicitly inheriting the active Hermes context. It treats email content as untrusted, distinguishes drafting from sending, and claims no mailbox side effects.

## Dependency direction

```text
Hermes runtime
    -> register(ctx)
        -> email skill

EmailPlugin
    -> EmailPluginConfig
    -> HermesContextSource (optional)
    -> EmailProvider (optional, no implementations yet)

EmailProvider
    -> provider-neutral models
```

The skill does not depend on a provider. Providers do not define persona or behavioral rules.

## Extension rules

1. Add one provider or capability per focused change.
2. Keep provider-specific data behind the provider interface.
3. Preserve safe defaults and add independent authorization checks before side effects.
4. Use stable public Hermes APIs; do not couple to private files or process globals.
5. Keep profile-specific behavior in Hermes context or user configuration.
6. Add tests and documentation with every new operational capability.
