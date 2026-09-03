import smtplib
import socket
import ssl
import threading
from types import SimpleNamespace

import pytest

from hermes_email.config import SmtpSettings
from hermes_email.secrets import SecretValue
from hermes_email.smtp import (
    SmtpAuthenticationError,
    SmtpClosedError,
    SmtpConnectionError,
    SmtpDeliveryRejectedError,
    SmtpDeliveryUnknownError,
    SmtpProtocolError,
    SmtpRecipientRejectedError,
    SmtpSubmission,
    SmtpSubmissionError,
    SmtpTimeoutError,
    SmtpTlsError,
    SmtplibTransport,
)

MESSAGE = (
    b"From: sender@example.invalid\r\n"
    b"To: recipient@example.invalid\r\n"
    b"Subject: Test\r\n"
    b"\r\n"
    b"Local only.\r\n"
)


class Resolver:
    def __init__(self, client_getter=None) -> None:
        self.references = []
        self.client_getter = client_getter

    def get_secret(self, reference: str) -> SecretValue:
        if self.client_getter is not None:
            assert self.client_getter().tls is True
        self.references.append(reference)
        if reference.endswith("USERNAME"):
            return SecretValue("synthetic-user")
        return SecretValue("synthetic-password")


class FakeSocket:
    def __init__(self, version: str = "TLSv1.3") -> None:
        self._version = version

    def version(self) -> str:
        return self._version


class FakeClient:
    def __init__(self, *, implicit: bool = True) -> None:
        self.trace = []
        self.tls = implicit
        self.sock = FakeSocket() if implicit else None
        self.esmtp_features = {}
        self.reject_recipient = None
        self.data_error = None
        self.data_code = 250
        self.quit_error = None
        self.noop_code = 250
        self.mail_code = 250
        self.ehlo_code = 250
        self.closed = False
        self.data_calls = 0

    def ehlo(self):
        self.trace.append("ehlo")
        if self.tls:
            self.esmtp_features = {"auth": "PLAIN", "size": "1000000"}
        else:
            self.esmtp_features = {"starttls": ""}
        return self.ehlo_code, b"SYNTHETIC SERVER TEXT"

    def set_debuglevel(self, value):
        self.trace.append(("debug", value))

    def starttls(self, *, context):
        self.trace.append(("starttls", context))
        self.tls = True
        self.sock = FakeSocket()
        return 220, b"SYNTHETIC SERVER TEXT"

    def auth(self, mechanism, authobject):
        assert self.tls is True
        assert mechanism == "PLAIN"
        assert authobject() == "\x00synthetic-user\x00synthetic-password"
        self.trace.append("auth")
        return 235, b"SYNTHETIC SERVER TEXT"

    def noop(self):
        self.trace.append("noop")
        return self.noop_code, b"SYNTHETIC SERVER TEXT"

    def mail(self, sender, options=()):
        self.trace.append(("mail", sender, tuple(options)))
        return self.mail_code, b"SYNTHETIC SERVER TEXT"

    def rcpt(self, recipient):
        self.trace.append(("rcpt", recipient))
        if recipient == self.reject_recipient:
            return 550, b"SYNTHETIC PRIVATE RECIPIENT RESPONSE"
        return 250, b"SYNTHETIC SERVER TEXT"

    def data(self, payload):
        self.data_calls += 1
        self.trace.append(("data", payload))
        if self.data_error is not None:
            raise self.data_error
        return self.data_code, b"SYNTHETIC SERVER TEXT"

    def rset(self):
        self.trace.append("rset")
        return 250, b"SYNTHETIC SERVER TEXT"

    def quit(self):
        self.trace.append("quit")
        if self.quit_error is not None:
            raise self.quit_error
        self.closed = True
        return 221, b"bye"

    def close(self):
        self.trace.append("close")
        self.closed = True


class Factory:
    def __init__(self, client: FakeClient) -> None:
        self.client = client
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.client


def settings(*, security: str = "implicit_tls") -> SmtpSettings:
    return SmtpSettings(
        mode="submission",
        account_namespace="smtp-account",
        host="smtp.example.invalid",
        port=465 if security == "implicit_tls" else 587,
        security=security,
        username_ref="HERMES_EMAIL_SMTP_USERNAME",
        password_ref="HERMES_EMAIL_SMTP_PASSWORD",
        sender_address="sender@example.invalid",
    )


def submission(*, recipients=("recipient@example.invalid",), payload=MESSAGE):
    return SmtpSubmission(
        envelope_sender="sender@example.invalid",
        envelope_recipients=recipients,
        message_bytes=payload,
        max_message_bytes=1_000_000,
    )


def transport(client: FakeClient, *, security: str = "implicit_tls"):
    factory = Factory(client)
    resolver = Resolver(lambda: client)
    value = SmtplibTransport(
        settings(security=security),
        resolver,
        implicit_factory=factory,
        starttls_factory=factory,
    )
    return value, factory, resolver


def test_implicit_tls_submission_uses_one_all_recipient_transaction(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    keylog = tmp_path / "must-not-exist.keys"
    monkeypatch.setenv("SSLKEYLOGFILE", str(keylog))
    client = FakeClient()
    value, factory, resolver = transport(client)
    item = submission(recipients=("one@example.invalid", "two@example.invalid"))

    result = value.submit_once(item)

    assert result.accepted is True
    assert resolver.references == [
        "HERMES_EMAIL_SMTP_USERNAME",
        "HERMES_EMAIL_SMTP_PASSWORD",
    ]
    args, kwargs = factory.calls[0]
    assert args == ("smtp.example.invalid", 465)
    assert kwargs["local_hostname"] == "[127.0.0.1]"
    assert kwargs["timeout"] == 15
    context = kwargs["context"]
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.minimum_version == ssl.TLSVersion.TLSv1_2
    assert context.keylog_filename is None
    assert keylog.exists() is False
    assert client.trace == [
        "ehlo",
        ("debug", 0),
        "auth",
        ("mail", "sender@example.invalid", (f"SIZE={len(MESSAGE)}",)),
        ("rcpt", "one@example.invalid"),
        ("rcpt", "two@example.invalid"),
        ("data", MESSAGE),
        "quit",
    ]


def test_starttls_ehloes_twice_and_resolves_secrets_only_after_tls() -> None:
    client = FakeClient(implicit=False)
    value, factory, resolver = transport(client, security="starttls")

    value.check_health()

    assert resolver.references == [
        "HERMES_EMAIL_SMTP_USERNAME",
        "HERMES_EMAIL_SMTP_PASSWORD",
    ]
    assert [entry if isinstance(entry, str) else entry[0] for entry in client.trace] == [
        "ehlo",
        "debug",
        "starttls",
        "ehlo",
        "debug",
        "auth",
        "noop",
        "close",
    ]
    assert "context" not in factory.calls[0][1]
    starttls_context = client.trace[2][1]
    assert starttls_context.minimum_version == ssl.TLSVersion.TLSv1_2


def test_health_never_uses_envelope_or_data_commands() -> None:
    client = FakeClient()
    value, _, _ = transport(client)

    value.check_health()

    names = [entry if isinstance(entry, str) else entry[0] for entry in client.trace]
    assert names == ["ehlo", "debug", "auth", "noop", "close"]
    assert "mail" not in names and "rcpt" not in names and "data" not in names


def test_any_recipient_rejection_resets_and_never_starts_data() -> None:
    client = FakeClient()
    client.reject_recipient = "blocked@example.invalid"
    value, _, _ = transport(client)

    with pytest.raises(SmtpRecipientRejectedError) as captured:
        value.submit_once(
            submission(
                recipients=("accepted@example.invalid", "blocked@example.invalid")
            )
        )

    assert "SYNTHETIC" not in repr(captured.value)
    assert client.data_calls == 0
    assert "rset" in client.trace
    assert client.closed is True


def test_data_disconnect_is_unknown_and_never_retried() -> None:
    client = FakeClient()
    client.data_error = smtplib.SMTPServerDisconnected("SYNTHETIC PRIVATE RESPONSE")
    value, _, _ = transport(client)

    with pytest.raises(SmtpDeliveryUnknownError) as captured:
        value.submit_once(submission())

    assert client.data_calls == 1
    assert "SYNTHETIC" not in str(captured.value)
    assert client.closed is True


def test_rcpt_252_is_positive_acceptance() -> None:
    client = FakeClient()

    def accepted_without_verification(recipient):
        client.trace.append(("rcpt", recipient))
        return 252, b"accepted"

    client.rcpt = accepted_without_verification
    value, _, _ = transport(client)

    assert value.submit_once(submission()).accepted is True
    assert client.data_calls == 1


def test_pre_data_interruption_closes_client_and_is_definite() -> None:
    client = FakeClient()

    def interrupted_auth(mechanism, authobject):
        del mechanism, authobject
        raise KeyboardInterrupt()

    client.auth = interrupted_auth
    value, _, _ = transport(client)

    with pytest.raises(SmtpConnectionError, match="interrupted"):
        value.check_health()

    assert client.closed is True
    value.close()
    value.close()
    with pytest.raises(SmtpClosedError):
        value.check_health()


def test_interruption_after_data_begins_is_delivery_unknown() -> None:
    client = FakeClient()
    client.data_error = KeyboardInterrupt()
    value, _, _ = transport(client)

    with pytest.raises(SmtpDeliveryUnknownError):
        value.submit_once(submission())

    assert client.data_calls == 1


def test_definite_data_rejection_and_quit_failure_after_acceptance() -> None:
    rejected = FakeClient()
    rejected.data_code = 554
    rejected_transport, _, _ = transport(rejected)
    with pytest.raises(SmtpDeliveryRejectedError):
        rejected_transport.submit_once(submission())
    assert rejected.data_calls == 1

    accepted = FakeClient()
    accepted.quit_error = smtplib.SMTPServerDisconnected("private")
    accepted_transport, _, _ = transport(accepted)
    assert accepted_transport.submit_once(submission()).accepted is True
    assert accepted.data_calls == 1
    assert accepted.closed is True


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (socket.timeout("private"), SmtpTimeoutError),
        (ssl.SSLError("private"), SmtpTlsError),
        (smtplib.SMTPException("private"), SmtpConnectionError),
    ],
)
def test_pre_data_failures_are_fixed_and_redacted(failure, expected) -> None:
    client = FakeClient()

    def failing_mail(sender, options=()):
        del sender, options
        raise failure

    client.mail = failing_mail
    value, _, _ = transport(client)

    with pytest.raises(expected) as captured:
        value.submit_once(submission())

    assert "private" not in str(captured.value)
    assert client.data_calls == 0


def test_tls_auth_and_capability_failures_are_fixed() -> None:
    old_tls = FakeClient()
    old_tls.sock = FakeSocket("TLSv1.1")
    value, _, _ = transport(old_tls)
    with pytest.raises(SmtpTlsError):
        value.check_health()

    no_plain = FakeClient()
    original_ehlo = no_plain.ehlo

    def ehlo_without_plain():
        result = original_ehlo()
        no_plain.esmtp_features["auth"] = "LOGIN"
        return result

    no_plain.ehlo = ehlo_without_plain
    value, _, _ = transport(no_plain)
    with pytest.raises(SmtpProtocolError):
        value.check_health()

    auth_failure = FakeClient()

    def reject_auth(mechanism, authobject):
        del mechanism, authobject
        raise smtplib.SMTPAuthenticationError(535, b"SYNTHETIC PRIVATE")

    auth_failure.auth = reject_auth
    value, _, _ = transport(auth_failure)
    with pytest.raises(SmtpAuthenticationError) as captured:
        value.check_health()
    assert "SYNTHETIC" not in repr(captured.value)


def test_close_interrupts_active_client_and_rejects_late_work() -> None:
    started = threading.Event()
    release = threading.Event()
    client = FakeClient()

    def blocking_noop():
        started.set()
        release.wait(timeout=5)
        return 250, b"ok"

    def closing():
        client.closed = True
        release.set()
        client.trace.append("close")

    client.noop = blocking_noop
    client.close = closing
    value, _, _ = transport(client)
    worker = threading.Thread(target=value.check_health)
    worker.start()
    assert started.wait(timeout=5)

    value.close()
    worker.join(timeout=5)

    assert worker.is_alive() is False
    assert client.closed is True
    with pytest.raises(SmtpClosedError):
        value.check_health()


def test_transport_rechecks_fixed_sender_and_configured_size() -> None:
    client = FakeClient()
    value, _, _ = transport(client)
    wrong_sender = SmtpSubmission(
        envelope_sender="other@example.invalid",
        envelope_recipients=("recipient@example.invalid",),
        message_bytes=MESSAGE,
        max_message_bytes=1_000_000,
    )
    with pytest.raises(SmtpSubmissionError, match="does not match"):
        value.submit_once(wrong_sender)
    assert client.trace == []

    tiny_settings = settings().__class__(
        **{
            **{
                field: getattr(settings(), field)
                for field in settings().__dataclass_fields__
            },
            "max_message_bytes": 1024,
        }
    )
    factory = Factory(client)
    tiny = SmtplibTransport(
        tiny_settings,
        Resolver(lambda: client),
        implicit_factory=factory,
    )
    oversized = SmtpSubmission(
        envelope_sender="sender@example.invalid",
        envelope_recipients=("recipient@example.invalid",),
        message_bytes=b"Subject: test\r\n\r\n" + (b"x" * 100 + b"\r\n") * 15,
        max_message_bytes=2000,
    )
    with pytest.raises(SmtpSubmissionError, match="configured byte limit"):
        tiny.submit_once(oversized)
    assert factory.calls == []


def test_submission_validation_rejects_injection_bcc_duplicates_and_framing() -> None:
    with pytest.raises(SmtpSubmissionError):
        submission(recipients=("same@example.invalid", "same@EXAMPLE.INVALID"))
    with pytest.raises(SmtpSubmissionError):
        submission(payload=MESSAGE.replace(b"Subject:", b"Bcc: hidden@example.invalid\r\nSubject:"))
    with pytest.raises(SmtpSubmissionError):
        submission(payload=MESSAGE.replace(b"\r\n", b"\n"))
    with pytest.raises(SmtpSubmissionError):
        SmtpSubmission(
            envelope_sender="sender@example.invalid\r\nRCPT TO:<bad@example.invalid>",
            envelope_recipients=("recipient@example.invalid",),
            message_bytes=MESSAGE,
            max_message_bytes=1_000_000,
        )
