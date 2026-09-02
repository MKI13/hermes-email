import hermes_email
import hermes_email.config
import hermes_email.context
import hermes_email.models
import hermes_email.plugin
import hermes_email.providers


def test_public_modules_import() -> None:
    assert hermes_email.__version__ == "0.2.0"
    assert hermes_email.config.EmailPluginConfig is hermes_email.EmailPluginConfig
    assert hermes_email.context.HermesContext is hermes_email.HermesContext
    assert hermes_email.models.EmailDraft is not None
    assert hermes_email.plugin.EmailPlugin is not None
    assert hermes_email.providers.EmailProvider is not None
