# Hermes Compatibility

Version 0.11.1 targets the manifest v1 schema accepted by the Hermes Agent v0.21.0 installer. The manifest uses only `manifest_version`, `name`, `version`, `kind`, `description`, and `author`. Optional v2 metadata is omitted because none is required for runtime behavior.

Manifest version identifies the `plugin.yaml` file format; it does not select the runtime context API. An omitted `api_version` is treated as current-compatible. The plugin continues to use these public Hermes extension surfaces:

- native directory plugins use a root `plugin.yaml` and `__init__.py` with `register(ctx)`;
- plugin-provided skills are registered with `ctx.register_skill()` and receive a plugin namespace;
- `ctx.profile_name` is the stable public active-profile identifier;
- `ctx.on_unload()` owns cleanup callbacks for plugin runtime references;
- `ctx.register_command()` registers `/email-status` as an in-session slash command;
- non-secret plugin settings are read through the official plugin-scoped `ctx.get_config()` API;
- plugin validation is available through `hermes plugins doctor`.

Authoritative upstream references:

- [Build a Hermes Plugin](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins)
- [Plugins overview](https://hermes-agent.nousresearch.com/docs/user-guide/features/plugins)
- [Creating Skills](https://hermes-agent.nousresearch.com/docs/developer-guide/creating-skills)
- [Hermes Agent repository](https://github.com/NousResearch/hermes-agent)

This project avoids private Hermes files and does not copy the bundled production email platform adapter. Compatibility should be rechecked against current upstream documentation before adding an operational provider.
