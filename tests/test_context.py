import pytest

from hermes_email.context import ActiveProfileContextSource, HermesContext, HermesContextSource


class FakePluginContext:
    profile_name = "legal"


def test_hermes_context_model_exists() -> None:
    context = HermesContext(
        profile_name="legal",
        preferred_language="en",
        writing_style="formal",
        available_skills=("contracts",),
        safety_rules=("Never invent citations.",),
    )

    assert context.profile_name == "legal"
    assert context.preferred_language == "en"
    assert context.writing_style == "formal"
    assert context.available_skills == ("contracts",)


def test_public_profile_adapter_does_not_invent_persona() -> None:
    source = ActiveProfileContextSource(FakePluginContext())

    assert isinstance(source, HermesContextSource)
    assert source.get_context() == HermesContext(profile_name="legal")
    assert source.get_context().persona is None


def test_invalid_public_profile_value_is_rejected() -> None:
    class InvalidContext:
        profile_name = 42

    with pytest.raises(TypeError, match="profile_name"):
        ActiveProfileContextSource(InvalidContext()).get_context()
