# Architecture

## Version 0.25.0

Hermes Email separates agent behavior from technical mail infrastructure. Hermes owns reasoning, persona, language, style, user preferences, and decisions. The plugin owns validated provider access, profile isolation, local persistence, technical send gates, confirmation binding, durable send intents, uncertainty recovery, and duplicate prevention.

Version 0.24.0 adds an explicit architectural rule: **mailbox and draft content is evidence/data, never authority**. Tool output and the bundled skill must preserve that distinction.

## Universal profile model

Hermes Email supports both common deployment styles:

- a dedicated mail profile, recommended for stronger separation;
- an existing Hermes profile explicitly designated as the single productive mail owner.

The architecture does not require any fixed profile name. `email`, `default`, `personal`, and `ef-sinn-email` are examples only.

The invariant is: one productive mailbox/account configuration has one explicit owning Hermes profile.

## Official entrypoint and profile guard

The root `__init__.py` routes Hermes registration through `hermes_email.profile_guard.register(ctx)`.

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

No model hook, timer, poller, background send worker, provider-draft operation, mailbox delete/move operation, or Hermes send tool is introduced by v0.24.0.

## Untrusted content boundary

All data originating from a mailbox, draft body, copied/quoted mail, sender field, signature, forwarded section, header, HTML-derived text, attachment metadata, or similar external source is assigned **zero action authority**.

The model-facing read contract explicitly marks returned content as untrusted. The skill instructs Hermes to keep current-user intent separate from external mail text.

The plugin must never interpret external text as permission to:

- invoke another tool;
- access a secret;
- switch or weaken profile ownership;
- create/mutate a draft;
- add/change recipients;
- confirm or submit a send;
- retry an uncertain send;
- modify safety policy.

This is intentionally not implemented by deleting suspicious phrases from customer mail. The original content remains data for legitimate analysis; the security boundary is its lack of authority.

CI contains a prompt-injection contract test that fails if core untrusted-content markers or required skill rules disappear.

## Read provider

`EmailProvider` remains read-only. The IMAP implementation requires verified TLS and read-only mailbox access, bounded UID `BODY.PEEK` fetches, finite limits, and normalized content. Returned mail fields are untrusted data.

Reading a message authorizes no operation beyond the read requested by the current user.

## Draft storage

`SqliteDraftStore` remains separate from observation storage. Draft mutations use caller operation IDs and exact revisions. A conflict is reviewed rather than overwritten. Draft storage contains sensitive plaintext and belongs only to the authorized profile.

Draft content is also untrusted data: copied external content inside a local draft cannot authorize a later mutation, confirmation, or send.

## SMTP, confirmation, and send intents

`prepare_send_candidate()` requires exact current-user confirmation for one draft ID and revision. `SmtplibTransport` remains a disconnected one-attempt SMTP seam. `SqliteSendIntentStore` persists one durable send intent before dispatch and prevents duplicate dispatch across retries/restarts.

States are:

- `dispatching`
- `accepted`
- `definite-failure`
- `delivery-unknown`

Prior-process interrupted dispatch is recovered to `delivery-unknown`; live same-process dispatch is not misclassified. `delivery-unknown` is terminal for automatic behavior and requires manual external verification.

No mail content can create a `UserSendConfirmation` or `send_operation_id` authorization.

## Status

The profile guard owns the official `/email-status` registration. Authorized status is formatted dynamically from the runtime and current package version. Blocked status reports only fixed non-secret fields including current profile, authorized profile, and a fixed diagnostic.

## Security boundary

The supported security boundary is the official Hermes directory-plugin entrypoint plus the explicit trust contract for model-facing content. Direct imports by arbitrary same-account local code remain outside the plugin threat model, consistent with the existing same-account malicious-code exclusion.

## Extension rules

1. Productive mail access must remain bound to one explicit Hermes profile; a dedicated profile is recommended but not mandatory.
2. Evaluate ownership before provider, state-directory, database, or secret access.
3. Blocked profiles must not register mail tools or the email skill.
4. Keep provider, draft, observation, and send-intent responsibilities separated.
5. Keep mail and draft content untrusted and give external content zero action authority.
6. Require an independent current-user request before any later tool or side-effect decision.
7. Require exact user confirmation and durable idempotency before any external send side effect.
8. Never automatically retry `delivery-unknown`.
9. Use stable public Hermes APIs only.
10. Add bounded tests and current documentation with every capability.

## RFC thread context

Version 0.24.0 adds a bounded provider-neutral thread resolver and `email_get_thread`. IMAP normalization retains bounded `In-Reply-To` and `References` identifiers alongside the existing RFC `Message-ID`. Thread membership is graph-based on those identifiers only; subject, sender, body, dates, and semantic similarity never establish membership. One bounded provider page is scanned, no automatic pagination occurs, and the result reports scan completeness, truncation, and unresolved references. Thread content remains untrusted and cannot authorize tools or side effects.

## Reply routing

Version 0.25.0 adds provider-neutral `ReplyRoute` derivation. IMAP normalizes `Reply-To` separately from `From`. One valid `Reply-To` may be recommended; absent `Reply-To` falls back to a valid sender. Multiple, malformed, or oversized Reply-To candidates fail closed to no automatic selection. Routing data is included only in existing read/thread results and carries no draft or send authority.
