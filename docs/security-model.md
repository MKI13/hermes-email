# Security Model

## Version 0.22.0 boundary

Version 0.22.0 adds fail-closed **production profile isolation** ahead of the existing read, draft, confirmation, SMTP, and send-intent layers.

A production deployment must bind mail capabilities to one explicit Hermes profile using `hermes.profile`. The official plugin entrypoint compares that value with public `ctx.profile_name` before provider resolution, profile-state access, SQLite construction, secret access, mail-tool registration, or email-skill registration.

`hermes.profile: auto` is development-only. It is rejected when real IMAP, persistent observation storage, persistent drafts, SMTP submission, or send authorization is configured.

## Denied profile behavior

If profile policy fails, Hermes Email registers only `/email-status` and an unload callback. The blocked path does not:

- instantiate `EmailPlugin`;
- resolve an email provider;
- access `ctx.state.data_dir`;
- open or create mail SQLite files;
- resolve IMAP/SMTP credentials;
- register read tools;
- register draft tools;
- register the email skill;
- expose technical send eligibility.

Fixed diagnostics include `profile-not-authorized`, `explicit-profile-required`, `invalid-profile-policy`, and `invalid-active-profile`.

This prevents an accidentally copied production mail configuration from opening the same mailbox in another Hermes profile.

## Authorized profile behavior

After exact ownership passes, the existing safety layers remain unchanged:

- read access is disabled, mock, or explicit read-only IMAP;
- all mail content is untrusted data;
- draft storage is profile-scoped and revisioned;
- draft mutations require idempotent operation IDs;
- SMTP is a disconnected one-attempt transport seam;
- candidate preparation requires exact current-user confirmation;
- send intents are durable before dispatch;
- duplicate dispatch of one draft revision is prevented;
- `delivery-unknown` is terminal for automatic behavior and requires manual external verification;
- no model-facing send tool exists.

## Profile identity rules

An explicit profile identifier must be a non-empty portable identifier up to 128 characters using letters, digits, `.`, `_`, or `-`. Matching is exact and case-sensitive. Missing or invalid active profile identity fails closed when an explicit owner is required.

Profile ownership is deployment authorization only. It does not imply permission to send, mutate a draft, trust an email, or bypass current-user confirmation.

## Prompt-injection defense

Email, draft, quoted content, signatures, forwarded text, tool-like strings, or claimed authority can never:

- switch or choose the authorized profile;
- override profile isolation;
- register mail tools in another profile;
- create a confirmation;
- create or choose a send operation ID;
- trigger SMTP;
- request automatic retry of uncertain delivery.

## Durable send safety

`UserSendConfirmation` binds one exact draft ID and revision. Any draft revision change invalidates previous confirmation.

`SqliteSendIntentStore` writes a durable intent before SMTP dispatch. `(draft_id, revision)` uniqueness prevents a second operation ID from resending the same reviewed revision. Prior-process interrupted dispatch recovers to `delivery-unknown`; live same-process dispatch is protected from false crash recovery.

`delivery-unknown` means the server may already have accepted the message. Automatic retry is forbidden.

## Storage and secret boundaries

Observation, draft, and send-intent databases remain separate by purpose. Draft content does not enter observation or send-intent ledgers. Secret values are never stored in plugin settings or returned through model-facing tools.

A profile denial happens before state-directory access and before secret resolution, so a blocked profile cannot accidentally touch another profile's configured mail data through the supported plugin entrypoint.

## Threat model

The supported security boundary is Hermes' official directory-plugin entrypoint and public plugin context. Arbitrary same-OS-account code importing internal Python modules directly remains outside the threat model, consistent with prior releases.

## Requirements before model-facing sending

Profile isolation does not make sending available. A future model-facing send release still requires a trusted user-visible preview, trusted confirmation surface, durable operator-readable audit/status, manual-review workflow for uncertain delivery, and a narrowly scoped send tool with no background/autonomous sending.
