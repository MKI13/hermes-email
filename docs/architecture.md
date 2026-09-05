# Architecture

## Version 0.22.0

Hermes Email separates agent behavior from technical mail infrastructure. Hermes owns reasoning, persona, language, style, user preferences, and decisions. The plugin owns validated provider access, profile isolation, local persistence, technical send gates, confirmation binding, durable send intents, uncertainty recovery, and duplicate prevention.

## Official entrypoint and profile guard

The root `__init__.py` now routes Hermes registration through `hermes_email.profile_guard.register(ctx)`.

Profile policy is evaluated **before** the core runtime may:

- resolve an IMAP provider;
- inspect `ctx.state.data_dir`;
- create/open observation or draft SQLite stores;
- resolve mail credentials;
- register read or draft tools;
- register the email skill;
- expose SMTP/send eligibility.

Production capabilities require one explicit `hermes.profile` owner. Exact current-profile equality is required. `profile: auto` remains available only for development configurations that do not enable real IMAP, persistent SQLite mail state, drafts, SMTP submission, or send authorization.

A denied profile gets a minimal `ProfileBlockedRuntime`. It registers only `/email-status` and an unload callback. It does not import/instantiate the core `EmailPlugin` runtime path, register the email skill, or register mail tools.

This design lets deployments use a dedicated mail profile such as `ef-sinn-email` without hard-coding that name in the project.

## Authorized runtime

After profile authorization succeeds, the profile guard assembles the existing core runtime through Hermes public APIs:

```text
Hermes register(ctx)
    -> profile policy
       -> denied: safe status only
       -> authorized:
          -> validated plugin config
          -> optional read provider
          -> optional observation store
          -> optional draft store
          -> EmailPlugin
          -> read/draft tools
          -> email skill
          -> /email-status
```

No model hook, timer, poller, background send worker, provider-draft operation, mailbox delete/move operation, or Hermes send tool is introduced by v0.22.0.

## Read provider

`EmailProvider` remains read-only. The IMAP implementation requires verified TLS and read-only mailbox access, bounded UID `BODY.PEEK` fetches, finite limits, and normalized content. Returned mail fields are untrusted data.

## Draft storage

`SqliteDraftStore` remains separate from observation storage. Draft mutations use caller operation IDs and exact revisions. A conflict is reviewed rather than overwritten. Draft storage contains sensitive plaintext and belongs only to the authorized profile.

## SMTP, confirmation, and send intents

`prepare_send_candidate()` requires exact current-user confirmation for one draft ID and revision. `SmtplibTransport` remains a disconnected one-attempt SMTP seam. `SqliteSendIntentStore` persists one durable send intent before dispatch and prevents duplicate dispatch across retries/restarts.

States are:

- `dispatching`
- `accepted`
- `definite-failure`
- `delivery-unknown`

Prior-process interrupted dispatch is recovered to `delivery-unknown`; live same-process dispatch is not misclassified. `delivery-unknown` is terminal for automatic behavior and requires manual external verification.

## Status

The profile guard owns the official `/email-status` registration. Authorized status is formatted dynamically from the runtime and current package version. Blocked status reports only fixed non-secret fields including current profile, authorized profile, and a fixed diagnostic. This also removes the old stale hard-coded send-version text from the official runtime path.

## Security boundary

The supported security boundary is the official Hermes directory-plugin entrypoint. Direct imports by arbitrary same-account local code are outside the plugin threat model, consistent with the existing same-account malicious-code exclusion.

## Extension rules

1. Production mail access must remain bound to one explicit Hermes profile.
2. Evaluate ownership before provider, state-directory, database, or secret access.
3. Blocked profiles must not register mail tools or the email skill.
4. Keep provider, draft, observation, and send-intent responsibilities separated.
5. Keep mail content untrusted and never treat it as authorization.
6. Require exact user confirmation and durable idempotency before any external send side effect.
7. Never automatically retry `delivery-unknown`.
8. Use stable public Hermes APIs only.
9. Add bounded tests and current documentation with every capability.
