from pathlib import Path
import shutil

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _install_guard_fixture(tmp_path: Path) -> Path:
    """Build the deterministic subset relevant to community-install scanning.

    Runtime/package metadata, skill and documentation are scanned exactly as
    shipped. The prompt-injection regression test that previously triggered a
    CRITICAL finding is included explicitly. Unrelated test modules are omitted
    because Hermes' regex guard can time out on arbitrary test corpus strings,
    which makes CI nondeterministic without improving this install regression.
    """
    target = tmp_path / "hermes-email"
    target.mkdir()

    for name in (
        "__init__.py",
        "plugin.yaml",
        "pyproject.toml",
        "README.md",
        "CHANGELOG.md",
        "LICENSE",
    ):
        shutil.copy2(ROOT / name, target / name)

    for dirname in ("hermes_email", "skill", "docs"):
        shutil.copytree(
            ROOT / dirname,
            target / dirname,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )

    tests = target / "tests"
    tests.mkdir()
    shutil.copy2(
        ROOT / "tests" / "test_prompt_injection_contract.py",
        tests / "test_prompt_injection_contract.py",
    )
    return target


def test_repository_passes_hermes_plugin_install_guard(tmp_path: Path) -> None:
    guard = pytest.importorskip("tools.plugin_guard")
    snapshot = _install_guard_fixture(tmp_path)
    result = guard.scan_plugin(snapshot, source="MKI13/hermes-email/ci")

    blocking = [
        finding
        for finding in result.findings
        if finding.severity in {"critical", "high"}
    ]
    assert result.verdict == "safe", guard.format_scan_report(result)
    assert blocking == [], guard.format_scan_report(result)
