# Hermes Compatibility

Version 0.21.0 targets the manifest v1 schema accepted by the Hermes Agent v0.21.0 installer. The manifest uses only `manifest_version`, `name`, `version`, `kind`, `description`, `author`, and `provides_tools`. Optional v2 metadata is omitted because none is required for runtime behavior.

Manifest version identifies the `plugin.yaml` file format; it does not select the runtime context API. An omitted `api_version` is treated as current-compatible. The plugin continues to use these public Hermes extension surfaces:

- native directory plugins use a root `plugin.yaml` and `__init__.py` with `register(ctx)`;
- plugin-provided skills are registered with `ctx.register_skill()` and receive a plugin namespace;
- `ctx.profile_name` is the stable public active-profile identifier;
- `ctx.state.data_dir` is the public profile-scoped directory for a plugin's durable data;
- `ctx.on_unload()` owns cleanup callbacks for plugin runtime references;
- `ctx.register_command()` registers `/email-status` as an in-session slash command;
- `ctx.register_tool()` registers asynchronous JSON-string handlers with side-effect-free availability checks;
- non-secret plugin settings and secret references are read through the official plugin-scoped `ctx.get_config()` API;
- plugin validation is available through `hermes plugins doctor`.

Hermes Agent v0.21.0 lets plugins register new secret-source backends, but `PluginContext` does not expose a public method for reading one credential on behalf of a provider. Hermes documents its loaded environment as the standard credential path. The fallback resolver therefore performs only a validated, targeted environment lookup; it does not register a secret source or inspect private Hermes files.

Authoritative upstream references:

- [Build a Hermes Plugin](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins)
- [Plugins overview](https://hermes-agent.nousresearch.com/docs/user-guide/features/plugins)
- [Creating Skills](https://hermes-agent.nousresearch.com/docs/developer-guide/creating-skills)
- [Hermes Agent repository](https://github.com/NousResearch/hermes-agent)

This project avoids private Hermes files and does not copy Hermes' bundled email platform adapter. Version 0.21.0 keeps IMAP access inside this plugin, registers three bounded read tools plus six provider-independent local draft tools, and uses only public profile-scoped plugin state for observation and draft databases. It registers no model hook, provider-draft tool, send tool, SMTP command, or mailbox-write tool.

The SMTP transport, exact current-user confirmation gate, durable `SqliteSendIntentStore`, strict uncertain-delivery recovery, and `IdempotentSendOrchestrator` remain ordinary disconnected Python library APIs. They use no new Hermes extension surface and are not reachable from `EmailPlugin`, tools, commands, hooks, callbacks, timers, or pollers. The send-intent layer can persist under a caller-provided profile data directory, but the current Hermes runtime does not instantiate it on behalf of the model.

Version 0.21.0's recovery behavior is internal and deterministic: prior-process or legacy unresolved `dispatching` records become `delivery-unknown`; current-process live dispatches remain live; no recovery path invokes SMTP. This requires no private Hermes API and no background worker.

CI continues to pin Hermes Agent v0.21.0, run Plugin Doctor against an empty HOME, execute the full read/draft/SMTP/confirmation/idempotency/recovery test set, verify distribution contents, and import the built wheel in a clean environment.
