# Hermes Compatibility

Hermes Email version 0.22.0 targets the manifest v1 schema accepted by the pinned Hermes Agent v0.21.0 compatibility target.

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

## v0.22.0 profile isolation

The official root entrypoint now routes registration through `hermes_email.profile_guard.register(ctx)`.

The guard reads only public `ctx.profile_name` and plugin-scoped configuration before authorization. On a profile denial it does **not** access `ctx.state.data_dir`, resolve a provider, resolve mail secrets, instantiate the core email runtime, register email tools, or register the email skill. Only `/email-status` and unload cleanup are registered.

This uses no private Hermes file, database, profile loader, or undocumented runtime field.

For production mail capabilities an explicit `hermes.profile` value must exactly match `ctx.profile_name`. Development-only mock configurations may retain `profile: auto`.

## Existing capabilities

After profile authorization succeeds, v0.22.0 retains:

- bounded read-only IMAP/mock access;
- profile-scoped observation and draft storage;
- exact-revision confirmation gates;
- disconnected SMTP transport;
- durable idempotent send intents;
- strict `delivery-unknown` recovery with no automatic resend.

There is still no model-facing send tool, provider-draft tool, mailbox delete/move tool, model hook, timer, poller, or background send worker.

## Hermes Agent compatibility target

Hermes Agent v0.21.0 remains pinned in CI at the existing immutable upstream commit. This reference is the **Hermes Agent version**, not the Hermes Email plugin version.

Hermes Agent v0.21.0 exposes public profile identity and profile-scoped plugin data, which are sufficient for the v0.22.0 profile guard. No private profile files are inspected or copied.

The fallback environment secret resolver remains targeted and validated because the pinned `PluginContext` does not expose a public one-secret provider lookup API. Profile denial occurs before that resolver can be reached.

## Verification

CI runs:

- Python 3.11, 3.12, and 3.13;
- full pytest suite;
- profile-isolation tests;
- build and distribution-content checks;
- clean built-wheel import including `hermes_email.profile_guard`;
- Hermes Plugin Doctor against the pinned Hermes Agent v0.21.0 environment.

Authoritative upstream references remain:

- Build a Hermes Plugin
- Plugins overview
- Creating Skills
- Hermes Agent repository
