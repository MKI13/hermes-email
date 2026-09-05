"""Fail when built distributions contain unexpected or sensitive paths."""

from __future__ import annotations

import tarfile
import tomllib
from pathlib import Path, PurePosixPath
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
DENIED_PARTS = {".git", ".github", ".pytest_cache", ".venv", "__pycache__"}
DENIED_NAMES = {
    "config.yaml",
    "config.yml",
    "credentials",
    "credentials.json",
    ".netrc",
    ".pgpass",
    ".pypirc",
}
DENIED_SUFFIXES = {".key", ".p12", ".pem", ".pfx"}
REQUIRED_RUNTIME_PATHS = {
    "hermes_email/__init__.py",
    "hermes_email/addressing.py",
    "hermes_email/config.py",
    "hermes_email/draft_storage.py",
    "hermes_email/profile_guard.py",
    "hermes_email/send_orchestration.py",
    "hermes_email/sending.py",
    "hermes_email/smtp.py",
}


def _reject_path(name: str) -> None:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name:
        raise AssertionError(f"distribution contains unsafe path: {name}")
    normalized_parts = {part.casefold() for part in path.parts}
    if DENIED_PARTS.intersection(normalized_parts):
        raise AssertionError(f"distribution contains denied path: {name}")
    normalized_name = path.name.casefold()
    if (
        normalized_name.startswith(".env")
        or normalized_name in DENIED_NAMES
        or path.suffix.casefold() in DENIED_SUFFIXES
    ):
        raise AssertionError(f"distribution contains sensitive path: {name}")


def main() -> None:
    """Validate the single wheel and source distribution produced by build."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = project["project"]["version"]
    wheels = list(DIST.glob("hermes_email-*.whl"))
    sdists = list(DIST.glob("hermes_email-*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise AssertionError("build must produce exactly one wheel and one source distribution")
    if version not in wheels[0].name or version not in sdists[0].name:
        raise AssertionError("distribution filenames must contain the package version")

    with ZipFile(wheels[0]) as archive:
        wheel_names = [name for name in archive.namelist() if not name.endswith("/")]
        metadata_names = [name for name in wheel_names if name.endswith(".dist-info/METADATA")]
        if len(metadata_names) != 1:
            raise AssertionError("wheel must contain exactly one metadata record")
        metadata = archive.read(metadata_names[0]).decode("utf-8")
    if not wheel_names:
        raise AssertionError("wheel is empty")
    missing_runtime = REQUIRED_RUNTIME_PATHS.difference(wheel_names)
    if missing_runtime:
        raise AssertionError(
            "wheel is missing required runtime paths: " + ", ".join(sorted(missing_runtime))
        )
    if f"Version: {version}\n" not in metadata:
        raise AssertionError("wheel metadata version does not match the project")
    if "Requires-Dist: PyYAML" not in metadata:
        raise AssertionError("wheel metadata is missing the runtime dependency")
    for name in wheel_names:
        _reject_path(name)
        if not (name.startswith("hermes_email/") or ".dist-info/" in name):
            raise AssertionError(f"wheel contains unexpected runtime path: {name}")

    root = f"hermes_email-{version}/"
    with tarfile.open(sdists[0], mode="r:gz") as archive:
        members = archive.getmembers()
        member_names = {member.name for member in members if member.isfile()}
        required_sdist = {root + path for path in REQUIRED_RUNTIME_PATHS}
        missing_sdist = required_sdist.difference(member_names)
        if missing_sdist:
            raise AssertionError(
                "source distribution is missing required runtime paths: "
                + ", ".join(sorted(missing_sdist))
            )
        package_info = archive.extractfile(root + "PKG-INFO")
        if package_info is None:
            raise AssertionError("source distribution is missing package metadata")
        sdist_metadata = package_info.read().decode("utf-8")
    if not members:
        raise AssertionError("source distribution is empty")
    if f"Version: {version}\n" not in sdist_metadata:
        raise AssertionError("source distribution version does not match the project")
    if "Requires-Dist: PyYAML" not in sdist_metadata:
        raise AssertionError("source distribution is missing the runtime dependency")
    for member in members:
        if member.name != root.rstrip("/") and not member.name.startswith(root):
            raise AssertionError(f"source distribution escaped its root: {member.name}")
        _reject_path(member.name)
        if member.issym() or member.islnk() or member.isdev():
            raise AssertionError(f"source distribution contains unsafe member: {member.name}")

    print(f"verified {wheels[0].name} and {sdists[0].name}")


if __name__ == "__main__":
    main()
