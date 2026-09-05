import hermes_email
import hermes_email.addressing
import hermes_email.config
import hermes_email.context
import hermes_email.draft_storage
import hermes_email.draft_tools
import hermes_email.models
import hermes_email.plugin
import hermes_email.profile_guard
import hermes_email.providers
import hermes_email.providers.errors
import hermes_email.providers.imap
import hermes_email.secrets
import hermes_email.send_orchestration
import hermes_email.sending
import hermes_email.smtp
import hermes_email.storage
import hermes_email.tools
import hermes_email.threading


def test_public_modules_import() -> None:
    assert hermes_email.__version__ == "0.26.0"
    assert hermes_email.config.EmailPluginConfig is hermes_email.EmailPluginConfig
    assert hermes_email.context.HermesContext is hermes_email.HermesContext
    assert hermes_email.models.EmailDraft is not None
    assert hermes_email.plugin.EmailPlugin is not None
    assert hermes_email.profile_guard.evaluate_profile_policy is not None
    assert (
        hermes_email.secrets.EnvironmentSecretResolver
        is hermes_email.EnvironmentSecretResolver
    )
    assert hermes_email.secrets.SecretNotFoundError is hermes_email.SecretNotFoundError
    assert (
        hermes_email.secrets.validate_secret_reference
        is hermes_email.validate_secret_reference
    )
    assert hermes_email.providers.EmailProvider is not None
    assert hermes_email.config.ImapSettings is hermes_email.ImapSettings
    assert hermes_email.config.StorageSettings is hermes_email.StorageSettings
    assert hermes_email.storage.SqliteObservationStore is not None
    assert hermes_email.providers.ImapReadOnlyProvider is not None
    assert hermes_email.providers.ProviderAuthenticationError is not None
    assert hermes_email.send_orchestration.IdempotentSendOrchestrator is not None
    assert hermes_email.tools.LIST_TOOL == "email_list_messages"
    assert hermes_email.threading.build_thread_context is not None
