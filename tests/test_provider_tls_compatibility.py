"""Provider transport negatives, strict pins and capability-based IMAP auth."""
import asyncio
import hashlib
import imaplib
import ssl
from dataclasses import replace

import pytest

from hermes_email.config import ConfigError, ImapSettings, SmtpSettings
from hermes_email.providers import ImapReadOnlyProvider, ProviderTlsError, ProviderAuthenticationError, ProviderProtocolError
from hermes_email.smtp import SmtplibTransport, SmtpTlsError, SmtpSubmissionError
from test_imap_provider import FakeImapClient, RecordingSecretResolver, imap_settings, provider_with_client
from test_smtp import FakeClient, FakeSocket, Resolver, Factory, settings, submission

CERT = b"synthetic certificate bytes for injected clients"
PIN = hashlib.sha256(CERT).hexdigest()


class PinSocket(FakeSocket):
    def __init__(self, certificate=CERT, version="TLSv1.3"):
        super().__init__(version); self.certificate=certificate
    def getpeercert(self, binary_form=False):
        assert binary_form is True
        return self.certificate


@pytest.mark.parametrize("mode", ["starttls-pinned", "implicit_tls_pinned"])
@pytest.mark.parametrize("certificate", [CERT, b"other", b"", None])
def test_smtp_pin_before_secrets_or_auth(mode, certificate):
    implicit = mode == "implicit_tls_pinned"
    client = FakeClient(implicit=implicit)
    client.sock = PinSocket(certificate) if implicit else None
    original = client.starttls
    def starttls(*,context):
        assert context.verify_mode == ssl.CERT_NONE and not context.check_hostname
        result=original(context=context);client.sock=PinSocket(certificate);return result
    client.starttls=starttls
    cfg=replace(settings(),host="127.0.0.1",security=mode,tls_sha256_fingerprint=PIN)
    resolver=Resolver(lambda:client);factory=Factory(client)
    transport=SmtplibTransport(cfg,resolver,implicit_factory=factory,starttls_factory=factory)
    try:
        if certificate == CERT:
            transport.check_health()
            assert len(resolver.references)==2 and "auth" in client.trace
            assert not any(isinstance(x,tuple) and x[0] in {"mail","rcpt","data"} for x in client.trace)
        else:
            with pytest.raises(SmtpTlsError):transport.check_health()
            assert resolver.references==[] and "auth" not in client.trace
        assert client.closed and client.data_calls==0
    finally:transport.close()


@pytest.mark.parametrize("mode", ["starttls-pinned", "tls-pinned"])
@pytest.mark.parametrize("certificate", [CERT, b"other", b"", None])
def test_imap_pin_before_credentials(mode, certificate):
    client=FakeImapClient();client.sock=PinSocket(certificate)
    client.starttls=lambda **kwargs: ("OK", [])
    cfg=imap_settings(host="127.0.0.1",security=mode,tls_sha256_fingerprint=PIN)
    resolver=RecordingSecretResolver()
    provider=ImapReadOnlyProvider(cfg,resolver,client_factory=lambda *a,**k:client,
                                 starttls_client_factory=lambda *a,**k:client)
    try:
        if certificate==CERT:
            asyncio.run(provider.check_health());assert len(resolver.calls)==2
        else:
            with pytest.raises(ProviderTlsError):asyncio.run(provider.check_health())
            assert resolver.calls==[] and client.auth_calls==[] and client.shutdown_calls>0
    finally:provider.close()


@pytest.mark.parametrize("host", ["localhost", "mail.example.invalid", "127.0.0.1.example.invalid", "0.0.0.0"])
def test_pinned_modes_reject_nonliteral_or_nonloopback_hosts(host):
    with pytest.raises(ConfigError):ImapSettings(host=host,security="tls-pinned",tls_sha256_fingerprint=PIN)
    with pytest.raises(ConfigError):SmtpSettings(host=host,security="implicit_tls_pinned",tls_sha256_fingerprint=PIN)


@pytest.mark.parametrize("pin", [None, 0, "A"*64, "a"*63, "a"*65, "a:"*32])
def test_bad_fingerprint_rejected_at_configuration(pin):
    with pytest.raises(ConfigError):ImapSettings(host="127.0.0.1",security="tls-pinned",tls_sha256_fingerprint=pin)
    with pytest.raises(ConfigError):SmtpSettings(host="127.0.0.1",security="starttls-pinned",tls_sha256_fingerprint=pin)


def test_unpinned_configuration_cannot_silently_consume_a_pin():
    with pytest.raises(ConfigError):ImapSettings(host="127.0.0.1",tls_sha256_fingerprint=PIN)
    with pytest.raises(ConfigError):SmtpSettings(host="127.0.0.1",tls_sha256_fingerprint=PIN)


def test_imap_tls_version_failure_is_before_credentials():
    client=FakeImapClient();client.sock=PinSocket(version="TLSv1.1")
    resolver=RecordingSecretResolver()
    provider=ImapReadOnlyProvider(imap_settings(host="127.0.0.1",security="tls-pinned",tls_sha256_fingerprint=PIN),
        resolver,client_factory=lambda *a,**k:client)
    with pytest.raises(ProviderTlsError):asyncio.run(provider.check_health())
    assert not resolver.calls and client.shutdown_calls>0


def test_login_selected_once_and_username_is_quoted():
    client=FakeImapClient();client.capabilities=("IMAP4rev1","UIDPLUS")
    logins=[]
    client.login=lambda user,password: (logins.append((user,password)) or ("OK", []))
    resolver=RecordingSecretResolver({"HERMES_EMAIL_IMAP_USERNAME":'user "quoted" \\ name',
                                     "HERMES_EMAIL_IMAP_PASSWORD":"synthetic password"})
    provider,_,_=provider_with_client(client,resolver=resolver)
    asyncio.run(provider.check_health())
    assert logins==[(r'"user \"quoted\" \\ name"',"synthetic password")]
    assert client.auth_calls==[]


def test_plain_failure_does_not_retry_login():
    client=FakeImapClient();client.capabilities=(b"IMAP4rev1",b"AUTH=PLAIN")
    client.authenticate_error=imaplib.IMAP4.error("synthetic private rejection")
    client.login=lambda *a:pytest.fail("must not retry auth with LOGIN")
    provider,_,_=provider_with_client(client)
    with pytest.raises(ProviderAuthenticationError):asyncio.run(provider.check_health())
    assert client.auth_calls==["PLAIN"]


def test_login_disabled_rejects_before_resolving_secrets():
    client=FakeImapClient();client.capabilities=("IMAP4REV1","AUTH=XOAUTH2","LOGINDISABLED")
    provider,resolver,_=provider_with_client(client)
    with pytest.raises(ProviderAuthenticationError):asyncio.run(provider.check_health())
    assert not resolver.calls and not client.auth_calls


@pytest.mark.parametrize("capabilities", [[], "AUTH=PLAIN", (b"\xff",), (None,), ("AUTH=PLAIN\r\n",)])
def test_malformed_capabilities_are_redacted_before_secrets(capabilities):
    client=FakeImapClient();client.capabilities=capabilities
    provider,resolver,_=provider_with_client(client)
    with pytest.raises(ProviderProtocolError):asyncio.run(provider.check_health())
    assert not resolver.calls


@pytest.mark.parametrize("value", ["user\r\nINJECT", "user\x00", "üser"])
def test_login_control_or_nonascii_credentials_never_reach_wire(value):
    client=FakeImapClient();client.capabilities=("IMAP4REV1",)
    client.login=lambda *a:pytest.fail("unsafe LOGIN argument reached transport")
    resolver=RecordingSecretResolver({"HERMES_EMAIL_IMAP_USERNAME":value,"HERMES_EMAIL_IMAP_PASSWORD":"synthetic"})
    provider,_,_=provider_with_client(client,resolver=resolver)
    with pytest.raises(ProviderAuthenticationError):asyncio.run(provider.check_health())


def test_guard_rechecked_immediately_before_smtp_data():
    client=FakeClient();factory=Factory(client);resolver=Resolver(lambda:client)
    def deny():raise SmtpSubmissionError("expired review")
    transport=SmtplibTransport(settings(),resolver,implicit_factory=factory,before_data=deny)
    with pytest.raises(SmtpSubmissionError):transport.submit_once(submission())
    assert client.data_calls==0 and client.closed


def test_account_test_requires_exact_recipient_allowlist():
    from hermes_email.config import EmailPluginConfig
    # Configuration checks need no connection or credential access.
    raw = {"hermes":{"profile":"email"},
        "drafts":{"mode":"sqlite","account_namespace":"test-account"},
        "smtp":{"mode":"submission","account_namespace":"test-account",
            "host":"smtp.example.invalid","sender_address":"sender@example.invalid",
            "username_ref":"HERMES_EMAIL_SMTP_USER","password_ref":"HERMES_EMAIL_SMTP_PASS"},
        "safety":{"allow_send":True},"send_workflow":{"mode":"account-test"},
        "recipient_policy":{"mode":"allowlist","allowed_addresses":["test-recipient@example.com"]}}
    cfg = EmailPluginConfig.from_mapping(raw)
    assert cfg.send_workflow.mode == "account-test"
    assert cfg.recipient_policy.permits("test-recipient@example.com")
    assert not cfg.recipient_policy.permits("other@example.com")
    for policy in [{"mode":"all"}, {"mode":"allowlist","allowed_domains":["example.com"]},
                   {"mode":"allowlist","allowed_addresses":["test-recipient@example.com"],"allowed_domains":["example.com"]}]:
        with pytest.raises(ConfigError):EmailPluginConfig.from_mapping({**raw,"recipient_policy":policy})


def test_new_workflow_options_do_not_enable_default_sending():
    from hermes_email.config import EmailPluginConfig
    cfg=EmailPluginConfig()
    assert cfg.send_workflow.mode == "disabled" and cfg.safety.allow_send is False
