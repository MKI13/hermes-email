import pytest

from scripts.check_dist import _reject_path


@pytest.mark.parametrize(
    "member_name",
    [
        "/absolute.py",
        "hermes_email/../outside.py",
        "hermes_email-0.15.0/../outside.txt",
        "hermes_email\\outside.py",
        "hermes_email/.GIT/config",
    ],
)
def test_distribution_rejects_unsafe_member_paths(member_name: str) -> None:
    with pytest.raises(AssertionError, match="unsafe path|denied path"):
        _reject_path(member_name)


@pytest.mark.parametrize(
    "member_name",
    [
        "hermes_email/.ENV",
        "hermes_email/.Env.Local",
        "hermes_email/CONFIG.YAML",
        "hermes_email/config.yml",
        "hermes_email/CREDENTIALS.JSON",
        "hermes_email/private.PEM",
        "hermes_email/private.P12",
    ],
)
def test_distribution_rejects_case_insensitive_sensitive_names(
    member_name: str,
) -> None:
    with pytest.raises(AssertionError, match="sensitive path"):
        _reject_path(member_name)


@pytest.mark.parametrize(
    "member_name",
    [
        "hermes_email/secrets.py",
        "hermes_email-0.15.0/README.md",
        "hermes_email-0.15.0/docs/configuration.md",
    ],
)
def test_distribution_allows_expected_source_paths(member_name: str) -> None:
    _reject_path(member_name)
