from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "docs" / "installation.md"
README = ROOT / "README.md"


def test_installation_supports_dedicated_and_existing_profiles() -> None:
    text = INSTALL.read_text(encoding="utf-8")
    assert "dedicated email profile" in text
    assert "use an existing Hermes profile" in text
    assert "A separate profile is not required" in text
    assert "profile: default" in text
    assert "profile: email" in text


def test_productive_onboarding_requires_one_explicit_owner() -> None:
    text = INSTALL.read_text(encoding="utf-8")
    assert "exactly one explicit Hermes profile owner" in text
    assert "Do not copy the same productive IMAP/SMTP configuration into several Hermes profiles" in text
    assert "profile: auto" in text
    assert "development/mock" in text


def test_readme_links_universal_installation_guide() -> None:
    readme = README.read_text(encoding="utf-8")
    assert "[Installation and profile setup](docs/installation.md)" in readme
    assert "A dedicated email profile is recommended for stronger operational separation, but it is not required." in readme
