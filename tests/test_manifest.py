from pathlib import Path
from runpy import run_path

import yaml


ROOT = Path(__file__).resolve().parent.parent
V1_MANIFEST_FIELDS = {
    "manifest_version",
    "name",
    "version",
    "description",
    "author",
    "requires_env",
    "provides_tools",
    "provides_hooks",
    "kind",
    "hooks",
    "label",
    "optional_env",
    "platforms",
    "external_dependencies",
    "pip_dependencies",
    "provides_browser_providers",
    "provides_web_providers",
}


def test_plugin_manifest_uses_only_hermes_v1_fields() -> None:
    manifest = yaml.safe_load((ROOT / "plugin.yaml").read_text(encoding="utf-8"))

    assert manifest["manifest_version"] == 1
    assert set(manifest) <= V1_MANIFEST_FIELDS
    assert manifest["name"] == "hermes-email"
    assert manifest["kind"] == "standalone"


def test_directory_plugin_entrypoint_remains_available() -> None:
    entrypoint = ROOT / "__init__.py"

    assert entrypoint.is_file()
    assert callable(run_path(str(entrypoint))["register"])
