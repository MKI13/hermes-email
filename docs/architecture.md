# Architecture

## Purpose

Hermes Email separates technical email infrastructure from agent behavior. The active Hermes agent remains responsible for language, persona, reasoning, user preferences, and decisions.

## Components

### Hermes directory-plugin entry point

The root `plugin.yaml` targets Hermes manifest v1 and `__init__.py` follows the native standalone directory-plugin entry-point convention. Manifest schema selection is independent of the runtime context API. `register(ctx)` reads plugin-scoped settings through `ctx.get_config()`, creates one `EmailPlugin` runtime, binds `ActiveProfileContextSource`, registers `/email-status` through `ctx.register_command()`, registers one `ctx.on_unload()` cleanup callback, and registers the bundled skill. Registration creates no network client, tool, model hook, poller, or background task.

### Plugin facade

`hermes_email.plugin.EmailPlugin` is the provider-neutral orchestration point. It owns validated configuration, an optional provider, an optional Hermes context source, and a non-sensitive runtime state. `EmailPlugin.from_config(config)` remains the exclusive provider factory. `get_runtime_status()` returns an immutable snapshot containing version, state, provider name, public profile name, read/draft/send readiness flags, and an optional fixed diagnostic code without invoking any mailbox operation.

The retrieval facades share the `read_mode` and provider-presence gates. Fetch additionally requires fetch capability, accepts only limits from 1 through 100, validates an optional non-empty opaque cursor without transforming it, and delegates exactly one page request. Search first validates and trims a query of at most 256 characters, then applies the same read, fetch-capability, limit, and cursor gates. It delegates exactly one provider page request and performs case-insensitive plain substring matching over subject, sender address, sender display name, and body text. The returned `EmailMessagePage` preserves provider order, contains only matches from that page, and carries the provider page's unchanged `next_cursor`.

Future technical responsibilities belong behind this facade:

- provider connection lifecycle;
- message ingestion and normalized events;
- draft storage;
- status tracking and deduplication;
- privacy-aware logging;
- independent safety authorization.

Version 0.12.1 exposes deterministic local message retrieval and single-page search only through the guarded facade and mock provider, plus in-memory mock draft storage. Pagination is explicitly caller-driven: no component follows `next_cursor` automatically. The remaining responsibilities are documented seams, not implemented subsystems.

### Provider abstraction

`hermes_email.providers.EmailProvider` defines `fetch_messages(*, limit=50, cursor=None) -> EmailMessagePage` plus asynchronous methods for retrieving one message, creating a draft, and sending a stored draft. `EmailMessagePage` contains one page's messages and either `None` or an opaque non-empty `next_cursor`; it remains sequence-compatible for existing bounded callers, and search reuses it for one page's local matches. `MockEmailProvider` is the only concrete implementation in version 0.12.1. It alone creates and interprets deterministic mock cursors, uses synthetic messages, stores drafts only in memory, performs no network access, and always blocks sending.

Future IMAP, SMTP, Gmail, Microsoft, Proton Bridge, or other adapters must normalize provider data into `EmailMessage` and `EmailDraft`. A provider's declared capability is never sufficient authorization for an external or destructive action.

### Provider resolver

`resolve_email_provider(config)` normalizes the explicitly configured provider name and compares it with a fixed allowlist. Version 0.12.1 recognizes only `mock`. Missing values raise `ProviderNotConfiguredError`; every other identifier raises `UnsupportedEmailProviderError`. The resolver performs no dynamic imports, discovery, fallback selection, network access, or plugin execution.

### Hermes context adapter

`HermesContext` holds optional values for profile name, persona, system prompt, language, writing style, user preferences, skills, tools, safety rules, and custom instructions.

Hermes currently exposes `ctx.profile_name` as a stable public plugin API. `ActiveProfileContextSource` reads only that property. `register(ctx)` binds this source to the runtime `EmailPlugin`; `get_hermes_context()` returns an owned snapshot with the active profile name. Other values remain empty until Hermes provides an appropriate public API or an explicit caller supplies them. The project does not read private Hermes files or invent a fallback personality.

### Status command

`/email-status` is an in-session slash command registered with Hermes' public `ctx.register_command()` API. Its handler calls `get_runtime_status()` on the registered runtime and formats only that immutable snapshot. It does not access configuration mappings, provider message methods, tools, environment variables, files, or network resources.

### Email skill

`skill/SKILL.md` describes mail-specific behavior while explicitly inheriting the active Hermes context. It treats email content as untrusted, distinguishes drafting from sending, and claims no mailbox side effects.

## Dependency direction

```text
Hermes runtime
    -> register(ctx)
        -> ctx.get_config(plugin-relative sections)
        -> EmailPluginConfig validation
        -> explicit provider resolver when configured
        -> disabled | mock-ready | configuration-error
        -> ActiveProfileContextSource(ctx)
        -> EmailPlugin
        -> ctx.register_command("email-status", same runtime)
            -> get_runtime_status()
            -> fixed text formatter
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

EmailPlugin.fetch_messages(limit, cursor)
    -> shared read-only gates
    -> provider fetch-capability gate
    -> integer limit gate: 1..100, without clamping
    -> cursor gate: None or non-empty string, without normalization
    -> EmailProvider.fetch_messages(limit=limit, cursor=cursor unchanged)
    -> exactly one EmailMessagePage; no automatic continuation

EmailPlugin.get_message(message_id)
    -> shared read-only gates
    -> provider get-capability gate
    -> non-empty string ID gate
    -> EmailProvider.get_message(message_id unchanged)

EmailPlugin.search_messages(query, limit, cursor)
    -> non-empty query of at most 256 characters
    -> shared read-only gates
    -> provider fetch-capability gate
    -> shared integer limit gate: 1..100, without clamping
    -> shared cursor gate: None or non-empty string, without normalization
    -> exactly one EmailProvider.fetch_messages(limit=limit, cursor=cursor unchanged)
    -> local plain substring filtering of that page only
    -> EmailMessagePage(matches, provider page next_cursor unchanged)

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
