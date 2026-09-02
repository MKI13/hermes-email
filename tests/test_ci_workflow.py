from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def load_workflow() -> dict:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict)
    return workflow


def test_ci_triggers_only_expected_branch_events() -> None:
    workflow = load_workflow()
    triggers = workflow.get("on", workflow.get(True))

    assert triggers == {
        "push": {"branches": ["main"]},
        "pull_request": {"branches": ["main"]},
    }


def test_ci_permissions_are_read_only() -> None:
    assert load_workflow()["permissions"] == {"contents": "read"}


def test_ci_uses_supported_python_matrix() -> None:
    job = load_workflow()["jobs"]["test-and-build"]

    assert job["runs-on"] == "ubuntu-latest"
    assert job["strategy"]["fail-fast"] is False
    assert job["strategy"]["matrix"]["python-version"] == ["3.11", "3.12", "3.13"]


def test_ci_quality_steps_are_fail_closed() -> None:
    workflow_text = WORKFLOW.read_text(encoding="utf-8")

    assert "python -m pytest" in workflow_text
    assert "python -m build" in workflow_text
    assert "python scripts/check_dist.py" in workflow_text
    assert "hermes plugins doctor . --ci" in workflow_text
    assert "continue-on-error" not in workflow_text


def test_ci_dependency_files_are_pinned() -> None:
    bootstrap_requirements = (ROOT / "requirements-bootstrap.txt").read_text(
        encoding="utf-8"
    )
    dev_requirements = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8")
    hermes_requirements = (ROOT / "requirements-hermes-ci.txt").read_text(
        encoding="utf-8"
    )

    assert bootstrap_requirements == "pip==26.2.1\n"
    assert "build==1.3.0" in dev_requirements
    assert "pytest==8.3.5" in dev_requirements
    assert "PyYAML==6.0.2" in dev_requirements
    assert "6064668c8fd2dbbb232ea073b32c9d06d932fa56" in hermes_requirements
