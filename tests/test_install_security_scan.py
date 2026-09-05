from pathlib import Path
import shutil

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _clean_checkout_snapshot(tmp_path: Path) -> Path:
    target = tmp_path / "hermes-email"
    shutil.copytree(
        ROOT,
        target,
        ignore=shutil.ignore_patterns(
            ".git",
            ".venv*",
            "dist",
            "build",
            "*.egg-info",
            "__pycache__",
            ".pytest_cache",
            ".mypy_cache",
            ".ruff_cache",
        ),
    )
    return target


def test_repository_passes_hermes_plugin_install_guard(tmp_path: Path) -> None:
    guard = pytest.importorskip("tools.plugin_guard")
    snapshot = _clean_checkout_snapshot(tmp_path)
    result = guard.scan_plugin(snapshot, source="MKI13/hermes-email/ci")

    blocking = [
        finding
        for finding in result.findings
        if finding.severity in {"critical", "high"}
    ]
    assert result.verdict == "safe", guard.format_scan_report(result)
    assert blocking == [], guard.format_scan_report(result)
