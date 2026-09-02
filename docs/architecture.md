# Architecture

## Purpose

Hermes Email separates technical email infrastructure from agent behavior. The active Hermes agent remains responsible for language, persona, reasoning, user preferences, and decisions.

## Components

### Hermes directory-plugin entry point

The root `plugin.yaml` and `__init__.py` follow Hermes' native standalone plugin convention. `register(ctx)` creates an `ActiveProfileContextSource` from the public Hermes plugin context, attaches it to an `EmailPlugin`, registers one official `ctx.on_unload()` callback to release that reference, and registers the bundled skill. It creates no network client, tool, model hook, provider, poller, or background task.

### Plugin facade

`hermes_email.plugin.EmailPlugin` is the provider-neutral orchestration point. It owns validated configuration, an optional provider, and an optional Hermes context source. `EmailPlugin.from_config(config)` delegates provider creation exclusively to `resolve_email_provider(config)`, preserves the supplied configuration, and propagates resolver errors unchanged. Version 0.7.0 exposes read-only `fetch_messages(limit=...)` and `get_message(message_id)` facades, can prepare a local draft value, and refuses every send attempt.

Both retrieval facades share the `read_mode`, provider-presence, and provider fetch-capability gates. `get_message()` then requires a non-empty string, trims surrounding whitespace, and delegates the opaque identifier to `EmailProvider.get_message()`. Neither facade contains provider-specific retrieval logic or transforms or persists provider results.

Future technical responsibilities belong behind this facade:

- provider connection lifecycle;
- message ingestion and normalized events;
- draft storage;
- status tracking and deduplication;
- privacy-aware logging;
- independent safety authorization.

Version 0.7.0 exposes deterministic local message retrieval only through the guarded facade and mock provider, plus in-memory mock draft storage. The remaining responsibilities are documented seams, not implemented subsystems.

### Provider abstraction

`hermes_email.providers.EmailProvider` defines asynchronous methods for fetching message summaries, retrieving one message, creating a draft, and sending a stored draft. `MockEmailProvider` is the only concrete implementation in version 0.7.0. It uses deterministic synthetic messages, stores drafts only in memory, performs no network access, and always blocks sending.

Future IMAP, SMTP, Gmail, Microsoft, Proton Bridge, or other adapters must normalize provider data into `EmailMessage` and `EmailDraft`. A provider's declared capability is never sufficient authorization for an external or destructive action.

### Provider resolver

`resolve_email_provider(config)` normalizes the explicitly configured provider name and compares it with a fixed allowlist. Version 0.7.0 recognizes only `mock`. Missing values raise `ProviderNotConfiguredError`; every other identifier raises `UnsupportedEmailProviderError`. The resolver performs no dynamic imports, discovery, fallback selection, network access, or plugin execution.

### Hermes context adapter

`HermesContext` holds optional values for profile name, persona, system prompt, language, writing style, user preferences, skills, tools, safety rules, and custom instructions.

Hermes currently exposes `ctx.profile_name` as a stable public plugin API. `ActiveProfileContextSource` reads only that property. `register(ctx)` binds this source to the runtime `EmailPlugin`; `get_hermes_context()` returns an owned snapshot with the active profile name. Other values remain empty until Hermes provides an appropriate public API or an explicit caller supplies them. The project does not read private Hermes files or invent a fallback personality.

### Email skill

`skill/SKILL.md` describes mail-specific behavior while explicitly inheriting the active Hermes context. It treats email content as untrusted, distinguishes drafting from sending, and claims no mailbox side effects.

## Dependency direction

```text
Hermes runtime
    -> register(ctx)
        -> ActiveProfileContextSource(ctx)
        -> EmailPlugin
        -> email skill
        -> ctx.on_unload(runtime release)

EmailPlugin.from_config
    -> EmailPluginConfig
    -> Provider resolver
        -> MockEmailProvider (only for explicit mock)
    -> EmailPlugin

EmailPlugin
    -> HermesContextSource (optional)
    -> EmailProvider (optional; MockEmailProvider for local tests)

EmailPlugin.fetch_messages(limit)
    -> shared read-only gates
    -> positive finite limit gate
    -> EmailProvider.fetch_messages(limit=limit)

EmailPlugin.get_message(message_id)
    -> shared read-only gates
    -> non-empty string ID gate
    -> trim surrounding whitespace
    -> EmailProvider.get_message(message_id)

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
