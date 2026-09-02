# Contributing

Contributions are welcome. Keep each change focused and avoid combining unrelated features.

## Workflow

1. Review the existing architecture and security model.
2. Create `feature/<name>`, `fix/<name>`, or `refactor/<name>` for substantial work.
3. Implement the smallest useful change.
4. Add or update tests and documentation.
5. Run the relevant local checks.
6. Use a clear Conventional Commit message.
7. Open a pull request describing behavior and validation.

Small maintenance changes may be committed directly to the active development branch. Never commit credentials, real account data, message content, tokens, or passwords.

## Development

Hermes Email targets Python 3.11 through 3.13.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest
```

Before contributing provider code, preserve the provider-neutral interfaces and safe defaults. Sending, deletion, and movement must remain explicit opt-ins with independent safety checks.
