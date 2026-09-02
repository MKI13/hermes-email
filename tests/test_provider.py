import inspect

import pytest

from hermes_email.providers import EmailProvider, ProviderCapabilities


def test_provider_base_interface_exists() -> None:
    assert inspect.isabstract(EmailProvider)
    assert EmailProvider.__abstractmethods__ == {
        "name",
        "fetch_messages",
        "get_message",
        "create_draft",
        "send_message",
    }

    with pytest.raises(TypeError):
        EmailProvider()


def test_provider_capabilities_default_to_disabled() -> None:
    capabilities = ProviderCapabilities()

    assert capabilities.fetch is False
    assert capabilities.drafts is False
    assert capabilities.send is False
    assert capabilities.delete is False
    assert capabilities.move is False
