from pathlib import Path
import re
import tomllib

import yaml

import hermes_email


ROOT = Path(__file__).resolve().parents[1]
EXPECTED = "0.23.0"


def test_current_version_is_consistent_across_live_surfaces() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    manifest = yaml.safe_load((ROOT / "plugin.yaml").read_text(encoding="utf-8"))
    skill = (ROOT / "skill" / "SKILL.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert hermes_email.__version__ == EXPECTED
    assert project["project"]["version"] == EXPECTED
    assert manifest["version"] == EXPECTED
    assert re.search(r"(?m)^version: 0\.23\.0$", skill)
    assert "Hermes Email v0.23.0" in skill
    assert "## Version 0.23.0" in readme
    assert "Version 0.23.0" in (ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
    assert "Version 0.23.0" in (ROOT / "docs" / "configuration.md").read_text(encoding="utf-8")
    assert "Version 0.23.0" in (ROOT / "docs" / "security-model.md").read_text(encoding="utf-8")
    assert "Hermes Email version 0.23.0" in (ROOT / "docs" / "official-hermes-compatibility.md").read_text(encoding="utf-8")
    assert workflow.count("0.23.0") >= 2


def test_stale_runtime_status_version_text_is_absent() -> None:
    plugin = (ROOT / "hermes_email" / "plugin.py").read_text(encoding="utf-8")
    assert "Send: unavailable in v0.18" not in plugin
    assert "Version 0.18.0 registers" not in plugin
