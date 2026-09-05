# Hermes Compatibility

Hermes Email version 0.28.0 targets the manifest v1 schema accepted by the pinned Hermes Agent v0.21.0 compatibility target.

The plugin uses only public Hermes extension surfaces:

- root directory-plugin `plugin.yaml` plus `__init__.py` with `register(ctx)`;
- public `ctx.profile_name` for active-profile identity;
- plugin-scoped `ctx.get_config()` for non-secret settings and secret references;
- public `ctx.state.data_dir` for authorized profile-scoped durable data;
- `ctx.on_unload()` for cleanup;
- `ctx.register_command()` for `/email-status`;
- `ctx.register_tool()` for bounded read/draft tools;
- `ctx.register_skill()` for the email skill;
- `hermes plugins doctor` for plugin validation.

## Universal profile setup

Hermes Email does not require users to create a dedicated email profile. A dedicated profile is recommended for stronger separation, but a normal existing Hermes profile can be the productive mail owner when `hermes.profile` is set to that exact active profile name.

This profile policy uses only public `ctx.profile_name`; the plugin does not create, copy, inspect, or switch Hermes profiles.

For productive mail capabilities an explicit `hermes.profile` value must exactly match `ctx.profile_name`. Development/mock-only configurations may retain `profile: auto`.

## Profile isolation

The official root entrypoint routes registration through `hermes_email.profile_guard.register(ctx)`.

The guard reads only public `ctx.profile_name` and plugin-scoped configuration before authorization. On a profile denial it does **not** access `ctx.state.data_dir`, resolve a provider, resolve mail secrets, instantiate the core email runtime, register email tools, or register the email skill. Only `/email-status` and unload cleanup are registered.

This uses no private Hermes file, database, profile loader, or undocumented runtime field.

## v0.24.0 content trust contract

The existing model-facing read tools already label returned mail as untrusted external content. Version 0.24.0 makes that behavior a tested compatibility contract and strengthens the bundled skill so mailbox/draft content cannot become action authorization.

No new Hermes runtime API is required. The boundary is expressed through:

- tool descriptions that identify returned mail as untrusted;
- JSON result fields retaining `content_is_untrusted` marking;
- skill rules separating current-user authority from external mail text;
- CI regression tests that fail if the core trust contract disappears.

The plugin does not ask Hermes Agent for a private prompt filter or undocumented security hook, and it does not rewrite legitimate mail content merely because it resembles instructions.

## Existing capabilities

After profile authorization succeeds, v0.24.0 retains:

- bounded read-only IMAP/mock access;
- profile-scoped observation and draft storage;
- exact-revision confirmation gates;
- disconnected SMTP transport;
- durable idempotent send intents;
- strict `delivery-unknown` recovery with no automatic resend.

There is still no model-facing send tool, provider-draft tool, mailbox delete/move tool, model hook, timer, poller, or background send worker.

## Hermes Agent compatibility target

Hermes Agent v0.21.0 remains pinned in CI at the existing immutable upstream commit. This reference is the **Hermes Agent version**, not the Hermes Email plugin version.

Hermes Agent v0.21.0 exposes public profile identity and profile-scoped plugin data, which are sufficient for the profile guard. No private profile files are inspected or copied.

The fallback environment secret resolver remains targeted and validated because the pinned `PluginContext` does not expose a public one-secret provider lookup API. Profile denial occurs before that resolver can be reached.

## Verification

CI runs:

- Python 3.11, 3.12, and 3.13;
- full pytest suite;
- profile-isolation tests;
- prompt-injection/content-trust contract tests;
- build and distribution-content checks;
- clean built-wheel import including `hermes_email.profile_guard`;
- Hermes Plugin Doctor against the pinned Hermes Agent v0.21.0 environment.

Authoritative upstream references remain:

- Build a Hermes Plugin
- Plugins overview
- Creating Skills
- Hermes Agent repository

## v0.24.0 thread context

The fourth read-only tool, `email_get_thread`, uses the same public `ctx.register_tool()` surface as existing read tools. It adds no new Hermes private API, background worker, write capability, or send path. Thread reconstruction is provider-neutral and bounded; IMAP contributes normalized RFC relationship metadata while the model-facing result remains explicitly untrusted.

## v0.28.0 Reply-To handling

Reply routing uses no new private Hermes API and no new tool. Existing `email_get_message` and `email_get_thread` results expose a bounded `reply_route` with source, candidates, ambiguity, validity, truncation, selected address, and `authorization: none`.

## v0.28.0 sender classification

Classification uses only plugin-local validated configuration and existing public Hermes tool output surfaces. It introduces no private Hermes API dependency and no new tool registration.

## v0.28.0 attachment metadata

Attachment metadata is carried through the existing provider-neutral message model and existing read/thread tools. No new Hermes tool registration or private Hermes API is introduced.
