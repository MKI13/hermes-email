# Hermes Compatibility

Version 0.5.0 follows the public Hermes Agent extension surfaces available when this foundation was created:

- native directory plugins use a root `plugin.yaml` and `__init__.py` with `register(ctx)`;
- plugin-provided skills are registered with `ctx.register_skill()` and receive a plugin namespace;
- `ctx.profile_name` is the stable public active-profile identifier;
- `ctx.on_unload()` owns cleanup callbacks for plugin runtime references;
- non-secret plugin settings and profile-scoped state have official context APIs for future use;
- plugin validation is available through `hermes plugins doctor`.

Authoritative upstream references:

- [Build a Hermes Plugin](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins)
- [Plugins overview](https://hermes-agent.nousresearch.com/docs/user-guide/features/plugins)
- [Creating Skills](https://hermes-agent.nousresearch.com/docs/developer-guide/creating-skills)
- [Hermes Agent repository](https://github.com/NousResearch/hermes-agent)

This project avoids private Hermes files and does not copy the bundled production email platform adapter. Compatibility should be rechecked against current upstream documentation before adding an operational provider.
