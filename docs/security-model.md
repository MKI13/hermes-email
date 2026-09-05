# Security Model

## Version 0.26.0 boundary

Version 0.24.0 hardens the **untrusted external-content boundary** on top of production profile isolation, read-only access, revisioned drafts, exact user confirmation, durable send idempotency, and strict `delivery-unknown` recovery.

Productive mail capabilities still belong to one explicit Hermes profile. A dedicated mail profile is recommended but not required: a user may explicitly bind Hermes Email to an existing profile instead. The same productive mailbox/account configuration must not be active in multiple profiles.

## External content has zero action authority

All mailbox and draft content is untrusted external data. This includes:

- sender names and addresses;
- subjects and bodies;
- signatures and disclaimers;
- forwarded and quoted messages;
- HTML-derived text;
- headers;
- attachment names/metadata;
- JSON, XML, Markdown, code blocks, or tool-like strings embedded in mail;
- text claiming to be a system/developer message, administrator, CEO, support agent, security team, or other authority.

None of these can authorize Hermes to run a tool, access a secret, switch profiles, mutate a draft, add recipients, confirm/send a message, retry an uncertain send, or modify safety policy.

Reading a message grants authority only for the current user's requested read operation. Every later operation requires independent authority from the current user's direct request and governing Hermes policy.

The plugin deliberately does **not** delete suspicious phrases from legitimate customer mail. The content remains available as evidence/data; its lack of authority is the security control.

## Prompt-injection defense

External content can never:

- override system, developer, profile, or current-user instructions;
- instruct Hermes to ignore previous rules;
- request a tool call merely by naming a tool or presenting fake tool syntax;
- turn a sender identity or signature into authorization;
- create or alter `UserSendConfirmation`;
- create/choose a `send_operation_id` to bypass duplicate protection;
- reinterpret `delivery-unknown` as a definite failure;
- request an automatic resend;
- switch or choose the authorized Hermes profile;
- reveal credentials, configuration secrets, or private runtime state.

If external mail text conflicts with the current user's request or governing policy, the external instruction is ignored. Genuine ambiguity must be resolved with the user rather than by following the email.

CI contains a prompt-injection contract test that requires model-facing read output to retain explicit untrusted-content marking and requires the bundled skill to retain the core no-authority rules.

## Profile isolation

A productive deployment must bind mail capabilities to one explicit Hermes profile using `hermes.profile`. The official plugin entrypoint compares that value with public `ctx.profile_name` before provider resolution, profile-state access, SQLite construction, secret access, mail-tool registration, or email-skill registration.

`hermes.profile: auto` is development/mock-only. It is rejected when real IMAP, persistent observation storage, persistent drafts, SMTP submission, or send authorization is configured.

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

## Authorized profile behavior

After exact ownership passes, the existing safety layers remain:

- read access is disabled, mock, or explicit read-only IMAP;
- model-facing mail output is explicitly marked untrusted;
- draft storage is profile-scoped and revisioned;
- draft mutations require idempotent operation IDs;
- draft content itself remains untrusted and cannot authorize a later action;
- SMTP is a disconnected one-attempt transport seam;
- candidate preparation requires exact current-user confirmation;
- send intents are durable before dispatch;
- duplicate dispatch of one draft revision is prevented;
- `delivery-unknown` is terminal for automatic behavior and requires manual external verification;
- no model-facing send tool exists.

## Profile identity rules

An explicit profile identifier must be a non-empty portable identifier up to 128 characters using letters, digits, `.`, `_`, or `-`. Matching is exact and case-sensitive. Missing or invalid active profile identity fails closed when an explicit owner is required.

Profile ownership is deployment authorization only. It does not imply permission to send, mutate a draft, trust an email, or bypass current-user confirmation.

## Durable send safety

`UserSendConfirmation` binds one exact draft ID and revision. Any draft revision change invalidates previous confirmation. Mail/draft content and model output cannot create this confirmation.

`SqliteSendIntentStore` writes a durable intent before SMTP dispatch. `(draft_id, revision)` uniqueness prevents a second operation ID from resending the same reviewed revision. Prior-process interrupted dispatch recovers to `delivery-unknown`; live same-process dispatch is protected from false crash recovery.

`delivery-unknown` means the server may already have accepted the message. Automatic retry is forbidden.

## Storage and secret boundaries

Observation, draft, and send-intent databases remain separate by purpose. Draft content does not enter observation or send-intent ledgers. Secret values are never stored in plugin settings or returned through model-facing tools.

A profile denial happens before state-directory access and before secret resolution, so a blocked profile cannot accidentally touch another profile's configured mail data through the supported plugin entrypoint.

Untrusted mail content cannot cause secret resolution or expose secret references through a model-facing action.

## Threat model

The supported security boundary is Hermes' official directory-plugin entrypoint, public plugin context, explicit profile ownership, and the documented model-facing content trust contract. Arbitrary same-OS-account code importing internal Python modules directly remains outside the threat model, consistent with prior releases.

## Requirements before model-facing sending

Profile isolation and prompt-injection defenses do not make sending available. A future model-facing send release still requires a trusted user-visible preview, trusted confirmation surface, durable operator-readable audit/status, manual-review workflow for uncertain delivery, and a narrowly scoped send tool with no background/autonomous sending.

## Thread-context safety

Thread reconstruction uses only bounded RFC `Message-ID`, `In-Reply-To`, and `References` relationships. Subject-line matching, sender similarity, body similarity, and semantic heuristics are deliberately excluded because they can merge unrelated business conversations. A malicious message may reference another Message-ID and appear in the contextual graph, but it remains untrusted external data with zero action authority. Incomplete scans, result truncation, and unresolved references are surfaced rather than hidden.

## Reply-To trust boundary

`Reply-To` is attacker-controlled external metadata. It can influence only a reviewable routing recommendation. It cannot authorize a draft, external lookup, recipient change, confirmation, SMTP dispatch, or retry. Multiple, invalid, or oversized Reply-To values result in no automatic target; the system does not silently fall back to From when a present Reply-To is malformed.
