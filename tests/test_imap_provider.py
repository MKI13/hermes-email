import asyncio
import imaplib
import inspect
import socket
import ssl
import threading
from collections.abc import Callable

import pytest

from hermes_email.config import ImapSettings
from hermes_email.providers import (
    ImapCursorError,
    ImapLimitError,
    ImapMessageIdError,
    ImapReadOnlyProvider,
    ProviderAuthenticationError,
    ProviderConnectionError,
    ProviderMailboxError,
    ProviderMessageError,
    ProviderTimeoutError,
    ProviderTlsError,
)
from hermes_email.secrets import SecretNotFoundError, SecretValue


PLAIN_MESSAGE = b"""From: Example Sender <sender@example.invalid>\r
To: One <one@example.invalid>, two@example.invalid\r
Cc: Copy <copy@example.invalid>\r
Subject: Example message\r
Date: Tue, 02 Sep 2026 10:00:00 +0200\r
Message-ID: <remote-1@example.invalid>\r
Content-Type: text/plain; charset=utf-8\r
\r
Hello from IMAP.\r
"""
HTML_MESSAGE = b"""From: sender@example.invalid\r
To: recipient@example.invalid\r
Subject: HTML only\r
Content-Type: text/html; charset=utf-8\r
\r
<html><head><title>hidden</title></head><body><p>Hello</p><script>run()</script><img src=\"https://remote.invalid/pixel\"><a href=\"https://remote.invalid\">safe label</a></body></html>\r
"""


class RecordingSecretResolver:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = values or {
            "HERMES_EMAIL_IMAP_USERNAME": "SYNTHETIC USER VALUE",
            "HERMES_EMAIL_IMAP_PASSWORD": "SYNTHETIC PASSWORD VALUE",
        }
        self.calls: list[str] = []

    def get_secret(self, reference: str) -> SecretValue:
        self.calls.append(reference)
        if reference not in self.values:
            raise SecretNotFoundError(f"secret reference is not available: {reference}")
        return SecretValue(self.values[reference])


class FakeImapClient:
    def __init__(self) -> None:
        self.debug = 99
        self.auth_calls: list[str] = []
        self.select_calls: list[tuple[str, bool]] = []
        self.uid_calls: list[tuple[str, str, str]] = []
        self.logout_calls = 0
        self.shutdown_calls = 0
        self.message_count = 3
        self.uid_validity = 77
        self.uid_next = 11
        self.readonly_confirmed = True
        self.fetch_responses: dict[str, list[object]] = {}
        self.authenticate_error: Exception | None = None
        self.select_error: Exception | None = None
        self.uid_error: Exception | None = None
        self.response_error: Exception | None = None
        self.select_result = "OK"
        self.logout_error: Exception | None = None

    def authenticate(self, mechanism: str, callback: Callable[[bytes], bytes]):
        self.auth_calls.append(mechanism)
        payload = callback(b"")
        assert payload.startswith(b"\0")
        if self.authenticate_error is not None:
            raise self.authenticate_error
        return "OK", [b"authenticated"]

    def select(self, mailbox: str, readonly: bool = False):
        self.select_calls.append((mailbox, readonly))
        if self.select_error is not None:
            raise self.select_error
        return self.select_result, [str(self.message_count).encode("ascii")]

    def response(self, name: str):
        if self.response_error is not None:
            raise self.response_error
        if name == "READ-ONLY":
            return name, [b""] if self.readonly_confirmed else [None]
        if name == "UIDVALIDITY":
            return name, [str(self.uid_validity).encode("ascii")]
        if name == "UIDNEXT":
            return name, [str(self.uid_next).encode("ascii")]
        return name, [None]

    def uid(self, command: str, uid_set: str, query: str):
        self.uid_calls.append((command, uid_set, query))
        if self.uid_error is not None:
            raise self.uid_error
        return "OK", self.fetch_responses.get(uid_set, [])

    def logout(self):
        self.logout_calls += 1
        if self.logout_error is not None:
            raise self.logout_error
        return "BYE", [b"logout"]

    def shutdown(self) -> None:
        self.shutdown_calls += 1


def imap_settings(**overrides) -> ImapSettings:
    values = {
        "host": "mail.example.invalid",
        "username_ref": "HERMES_EMAIL_IMAP_USERNAME",
        "password_ref": "HERMES_EMAIL_IMAP_PASSWORD",
    }
    values.update(overrides)
    return ImapSettings(**values)


def fetch_record(uid: int, raw: bytes, *, remote_size: int | None = None):
    size = len(raw) if remote_size is None else remote_size
    metadata = f"1 (UID {uid} RFC822.SIZE {size} BODY[]<0> {{{len(raw)}}}".encode()
    return metadata, raw


def provider_with_client(
    client: FakeImapClient,
    *,
    resolver: RecordingSecretResolver | None = None,
    settings: ImapSettings | None = None,
):
    calls: list[tuple[str, int, ssl.SSLContext, int]] = []

    def factory(host: str, port: int, *, ssl_context, timeout: int):
        calls.append((host, port, ssl_context, timeout))
        return client

    effective_resolver = resolver or RecordingSecretResolver()
    provider = ImapReadOnlyProvider(
        settings or imap_settings(), effective_resolver, client_factory=factory
    )
    return provider, effective_resolver, calls


def test_constructor_is_lazy_and_credential_free() -> None:
    resolver = RecordingSecretResolver()
    factory_calls = 0

    def factory(*args, **kwargs):
        nonlocal factory_calls
        factory_calls += 1
        raise AssertionError("connection must be lazy")

    ImapReadOnlyProvider(imap_settings(), resolver, client_factory=factory)

    assert resolver.calls == []
    assert factory_calls == 0


def test_health_uses_verified_tls_sasl_plain_and_readonly_mailbox(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    key_log_path = tmp_path / "synthetic-key-log"
    monkeypatch.setenv("SSLKEYLOGFILE", str(key_log_path))
    client = FakeImapClient()
    provider, resolver, factory_calls = provider_with_client(client)

    asyncio.run(provider.check_health())

    assert resolver.calls == [
        "HERMES_EMAIL_IMAP_USERNAME",
        "HERMES_EMAIL_IMAP_PASSWORD",
    ]
    assert len(factory_calls) == 1
    host, port, context, timeout = factory_calls[0]
    assert (host, port, timeout) == ("mail.example.invalid", 993, 15)
    assert context.check_hostname is True
    assert context.verify_mode is ssl.CERT_REQUIRED
    assert context.minimum_version is ssl.TLSVersion.TLSv1_2
    assert context.keylog_filename is None
    assert key_log_path.exists() is False
    assert client.debug == 0
    assert client.auth_calls == ["PLAIN"]
    assert client.select_calls == [("INBOX", True)]
    assert client.uid_calls == []
    assert client.logout_calls == 1


def test_fetch_uses_one_bounded_uid_window_and_body_peek() -> None:
    client = FakeImapClient()
    client.fetch_responses["9:10"] = [
        fetch_record(9, PLAIN_MESSAGE),
        b")",
        fetch_record(10, HTML_MESSAGE),
    ]
    provider, _, _ = provider_with_client(client)

    page = asyncio.run(provider.fetch_messages(limit=2))

    assert [message.message_id for message in page] == ["imap-v1:77:10", "imap-v1:77:9"]
    assert page.next_cursor == "imap-v1:77:8"
    assert client.uid_calls == [
        ("FETCH", "9:10", "(UID RFC822.SIZE BODY.PEEK[]<0.2000000>)")
    ]
    assert client.select_calls == [("INBOX", True)]
    assert client.logout_calls == 1


def test_cursor_continues_with_strictly_smaller_uid_window() -> None:
    client = FakeImapClient()
    client.fetch_responses["7:8"] = [fetch_record(8, PLAIN_MESSAGE)]
    provider, _, _ = provider_with_client(client)

    page = asyncio.run(provider.fetch_messages(limit=2, cursor="imap-v1:77:8"))

    assert [message.message_id for message in page] == ["imap-v1:77:8"]
    assert page.next_cursor == "imap-v1:77:6"
    assert client.uid_calls[0][1] == "7:8"


def test_sparse_empty_window_remains_caller_driven() -> None:
    client = FakeImapClient()
    provider, _, _ = provider_with_client(client)

    page = asyncio.run(provider.fetch_messages(limit=2, cursor="imap-v1:77:8"))

    assert page.messages == ()
    assert page.next_cursor == "imap-v1:77:6"
    assert len(client.uid_calls) == 1


def test_page_byte_budget_reduces_each_partial_literal_bound() -> None:
    client = FakeImapClient()
    provider, _, _ = provider_with_client(
        client,
        settings=imap_settings(max_message_bytes=2_000_000, max_page_bytes=500_000),
    )

    asyncio.run(provider.fetch_messages(limit=100))

    assert client.uid_calls == [
        ("FETCH", "1:10", "(UID RFC822.SIZE BODY.PEEK[]<0.5000>)")
    ]


@pytest.mark.parametrize("limit", [0, 101, -1, True, None, "1"])
def test_invalid_direct_provider_limit_prevents_external_access(limit: object) -> None:
    client = FakeImapClient()
    provider, resolver, factory_calls = provider_with_client(client)

    with pytest.raises(ImapLimitError):
        asyncio.run(provider.fetch_messages(limit=limit))  # type: ignore[arg-type]

    assert resolver.calls == []
    assert factory_calls == []


def test_stale_or_malformed_cursor_never_fetches() -> None:
    for cursor in (
        "imap-v1:76:8",
        "imap-v1:77:0",
        "../cursor",
        "imap-v1:77:x",
        "imap-v1:77:11",
        True,
    ):
        client = FakeImapClient()
        provider, _, _ = provider_with_client(client)

        with pytest.raises(ImapCursorError):
            asyncio.run(provider.fetch_messages(limit=2, cursor=cursor))

        assert client.uid_calls == []


def test_get_message_validates_uidvalidity_and_fetches_one_uid() -> None:
    client = FakeImapClient()
    client.fetch_responses["9:9"] = [fetch_record(9, PLAIN_MESSAGE)]
    provider, _, _ = provider_with_client(client)

    message = asyncio.run(provider.get_message("imap-v1:77:9"))

    assert message is not None
    assert message.message_id == "imap-v1:77:9"
    assert message.subject == "Example message"
    assert message.sender.address == "sender@example.invalid"
    assert [recipient.address for recipient in message.recipients] == [
        "one@example.invalid",
        "two@example.invalid",
        "copy@example.invalid",
    ]
    assert message.body_text == "Hello from IMAP."
    assert message.received_at is not None
    assert message.received_at.isoformat() == "2026-09-02T08:00:00+00:00"
    assert message.metadata["rfc_message_id"] == "<remote-1@example.invalid>"
    assert client.uid_calls[0][1] == "9:9"


def test_missing_message_returns_none() -> None:
    client = FakeImapClient()
    provider, _, _ = provider_with_client(client)

    assert asyncio.run(provider.get_message("imap-v1:77:9")) is None


def test_stale_and_malformed_message_ids_never_fetch() -> None:
    for message_id in ("imap-v1:76:9", "imap-v1:77:0", "not-an-id", "../message"):
        client = FakeImapClient()
        provider, _, _ = provider_with_client(client)

        with pytest.raises(ImapMessageIdError):
            asyncio.run(provider.get_message(message_id))

        assert client.uid_calls == []


def test_html_is_plain_text_without_scripts_urls_or_remote_access() -> None:
    client = FakeImapClient()
    client.fetch_responses["10:10"] = [fetch_record(10, HTML_MESSAGE)]
    provider, _, _ = provider_with_client(client)

    message = asyncio.run(provider.get_message("imap-v1:77:10"))

    assert message is not None
    assert message.body_text == "Hello\nsafe label"
    assert "run()" not in message.body_text
    assert "remote.invalid" not in message.body_text
    assert message.metadata["content"] == "text/html-as-plain-text"


def test_malformed_html_and_controls_cannot_escape_plain_text_normalization() -> None:
    raw = b"""From: sender@example.invalid\r
To: recipient@example.invalid\r
Subject: Safe\x1b[31m subject\x7f\r
Content-Type: text/html; charset=utf-8\r
\r
<script></style>SUPPRESSED SECRET</script><div hidden>HIDDEN ATTRIBUTE</div><span aria-hidden=\"true\">ARIA HIDDEN</span><b style=\"display:none\">STYLE HIDDEN</b><p>Visible\x1b[2J text</p>\r
"""
    client = FakeImapClient()
    client.fetch_responses["9:9"] = [fetch_record(9, raw)]
    provider, _, _ = provider_with_client(client)

    message = asyncio.run(provider.get_message("imap-v1:77:9"))

    assert message is not None
    assert "SUPPRESSED SECRET" not in (message.body_text or "")
    assert "HIDDEN ATTRIBUTE" not in (message.body_text or "")
    assert "ARIA HIDDEN" not in (message.body_text or "")
    assert "STYLE HIDDEN" not in (message.body_text or "")
    assert message.body_text == "Visible[2J text"
    assert message.subject == "Safe[31m subject"
    assert "\x1b" not in message.subject
    assert "\x7f" not in message.subject


def test_html_with_stylesheet_is_omitted_instead_of_exposing_hidden_text() -> None:
    raw = b"""From: sender@example.invalid\r
To: recipient@example.invalid\r
Subject: Styled HTML\r
Content-Type: text/html; charset=utf-8\r
\r
<style>.secret{display:none}</style><div class=\"secret\">HIDDEN INJECTION</div><p>Visible</p>\r
"""
    client = FakeImapClient()
    client.fetch_responses["9:9"] = [fetch_record(9, raw)]
    provider, _, _ = provider_with_client(client)

    message = asyncio.run(provider.get_message("imap-v1:77:9"))

    assert message is not None
    assert message.body_text is None
    assert message.metadata["content"] == "html-omitted"


def test_attachment_content_is_not_exposed_as_body() -> None:
    raw = b"""From: sender@example.invalid\r
To: recipient@example.invalid\r
Subject: Multipart\r
MIME-Version: 1.0\r
Content-Type: multipart/mixed; boundary=BOUNDARY\r
\r
--BOUNDARY\r
Content-Type: text/plain; charset=utf-8\r
\r
Visible body\r
--BOUNDARY\r
Content-Type: text/plain; charset=utf-8\r
Content-Disposition: attachment; filename=note.txt\r
\r
ATTACHMENT CONTENT\r
--BOUNDARY--\r
"""
    client = FakeImapClient()
    client.fetch_responses["9:9"] = [fetch_record(9, raw)]
    provider, _, _ = provider_with_client(client)

    message = asyncio.run(provider.get_message("imap-v1:77:9"))

    assert message is not None
    assert message.body_text == "Visible body"
    assert "ATTACHMENT" not in message.body_text


def test_nested_message_attachment_content_is_not_exposed_as_body() -> None:
    raw = b"""From: sender@example.invalid\r
To: recipient@example.invalid\r
Subject: Nested attachment\r
MIME-Version: 1.0\r
Content-Type: multipart/mixed; boundary=OUTER\r
\r
--OUTER\r
Content-Type: text/plain; charset=utf-8\r
\r
Visible body\r
--OUTER\r
Content-Type: message/rfc822\r
Content-Disposition: attachment; filename=attached.eml\r
\r
From: nested@example.invalid\r
To: recipient@example.invalid\r
Subject: Nested\r
Content-Type: text/plain; charset=utf-8\r
\r
NESTED ATTACHMENT CONTENT\r
--OUTER--\r
"""
    client = FakeImapClient()
    client.fetch_responses["9:9"] = [fetch_record(9, raw)]
    provider, _, _ = provider_with_client(client)

    message = asyncio.run(provider.get_message("imap-v1:77:9"))

    assert message is not None
    assert message.body_text == "Visible body"
    assert "NESTED" not in message.body_text


def test_truncated_remote_message_is_marked_without_unbounded_fetch() -> None:
    client = FakeImapClient()
    partial = PLAIN_MESSAGE + (b"X" * (4_096 - len(PLAIN_MESSAGE)))
    client.fetch_responses["9:9"] = [
        fetch_record(9, partial, remote_size=9_000_000)
    ]
    provider, _, _ = provider_with_client(
        client, settings=imap_settings(max_message_bytes=4_096)
    )

    message = asyncio.run(provider.get_message("imap-v1:77:9"))

    assert message is not None
    assert message.metadata["truncated"] == "true"
    assert "<0.4096>" in client.uid_calls[0][2]


def test_legal_short_partial_literal_is_accepted_and_marked() -> None:
    client = FakeImapClient()
    client.fetch_responses["9:9"] = [
        fetch_record(9, PLAIN_MESSAGE, remote_size=len(PLAIN_MESSAGE) + 10)
    ]
    provider, _, _ = provider_with_client(client)

    message = asyncio.run(provider.get_message("imap-v1:77:9"))

    assert message is not None
    assert message.metadata["truncated"] == "true"


def test_literal_larger_than_server_report_is_rejected() -> None:
    client = FakeImapClient()
    client.fetch_responses["9:9"] = [
        fetch_record(9, PLAIN_MESSAGE, remote_size=len(PLAIN_MESSAGE) - 1)
    ]
    provider, _, _ = provider_with_client(client)

    with pytest.raises(ProviderMessageError, match="literal length is inconsistent"):
        asyncio.run(provider.get_message("imap-v1:77:9"))


def test_unexpected_message_normalization_failure_is_redacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeImapClient()
    client.fetch_responses["9:9"] = [fetch_record(9, PLAIN_MESSAGE)]
    provider, _, _ = provider_with_client(client)

    def fail(*args, **kwargs):
        del args, kwargs
        raise ValueError("SYNTHETIC PRIVATE MESSAGE CONTENT")

    monkeypatch.setattr(provider, "_normalize_message", fail)

    with pytest.raises(ProviderMessageError) as captured:
        asyncio.run(provider.get_message("imap-v1:77:9"))

    assert str(captured.value) == "IMAP message could not be normalized"
    assert "SYNTHETIC" not in repr(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


def test_provider_exposes_no_draft_or_send_operations() -> None:
    provider, _, _ = provider_with_client(FakeImapClient())

    assert not hasattr(provider, "create_draft")
    assert not hasattr(provider, "send_message")
    assert set(provider.capabilities.__dataclass_fields__) == {"fetch", "get"}


def test_missing_secret_is_redacted_as_authentication_error() -> None:
    client = FakeImapClient()
    resolver = RecordingSecretResolver(
        {"HERMES_EMAIL_IMAP_USERNAME": "SYNTHETIC USER VALUE"}
    )
    provider, _, _ = provider_with_client(client, resolver=resolver)

    with pytest.raises(ProviderAuthenticationError) as captured:
        asyncio.run(provider.check_health())

    assert str(captured.value) == "IMAP credentials are unavailable"
    assert "SYNTHETIC" not in repr(captured.value)
    assert captured.value.__context__ is None
    assert client.select_calls == []
    assert client.shutdown_calls == 1


def test_unencodable_credential_is_rejected_without_value_disclosure() -> None:
    sensitive_value = "SYNTHETIC-PRIVATE-VALUE-\udcff"
    resolver = RecordingSecretResolver(
        {
            "HERMES_EMAIL_IMAP_USERNAME": sensitive_value,
            "HERMES_EMAIL_IMAP_PASSWORD": "SYNTHETIC PASSWORD VALUE",
        }
    )
    provider, _, _ = provider_with_client(FakeImapClient(), resolver=resolver)

    with pytest.raises(ProviderAuthenticationError) as captured:
        asyncio.run(provider.check_health())

    assert str(captured.value) == "IMAP credentials are invalid"
    assert sensitive_value not in repr(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


@pytest.mark.parametrize(
    ("factory_error", "expected_error"),
    [
        (ssl.SSLCertVerificationError(), ProviderTlsError),
        (ssl.SSLError(), ProviderTlsError),
        (socket.timeout(), ProviderTimeoutError),
        (OSError(), ProviderConnectionError),
    ],
)
def test_connection_failures_map_to_fixed_safe_errors(
    factory_error: Exception,
    expected_error: type[Exception],
) -> None:
    resolver = RecordingSecretResolver()

    def failing_factory(*args, **kwargs):
        raise factory_error

    provider = ImapReadOnlyProvider(
        imap_settings(), resolver, client_factory=failing_factory
    )

    with pytest.raises(expected_error) as captured:
        asyncio.run(provider.check_health())

    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert resolver.calls == []


@pytest.mark.parametrize(
    ("failure_field", "failure", "expected_error", "operation"),
    [
        ("select_error", OSError("SYNTHETIC OS DETAIL"), ProviderConnectionError, "health"),
        ("response_error", ssl.SSLError("SYNTHETIC TLS DETAIL"), ProviderTlsError, "health"),
        ("uid_error", OSError("SYNTHETIC OS DETAIL"), ProviderConnectionError, "fetch"),
    ],
)
def test_post_connect_transport_failures_are_redacted(
    failure_field: str,
    failure: Exception,
    expected_error: type[Exception],
    operation: str,
) -> None:
    client = FakeImapClient()
    setattr(client, failure_field, failure)
    provider, _, _ = provider_with_client(client)

    with pytest.raises(expected_error) as captured:
        if operation == "health":
            asyncio.run(provider.check_health())
        else:
            asyncio.run(provider.fetch_messages(limit=1))

    assert "SYNTHETIC" not in repr(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert client.shutdown_calls + client.logout_calls == 1


def test_authentication_failure_never_echoes_server_or_secret() -> None:
    client = FakeImapClient()
    client.authenticate_error = imaplib.IMAP4.error("SYNTHETIC SERVER DETAIL")
    provider, _, _ = provider_with_client(client)

    with pytest.raises(ProviderAuthenticationError) as captured:
        asyncio.run(provider.check_health())

    assert str(captured.value) == "IMAP authentication failed"
    assert "SYNTHETIC" not in repr(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert client.shutdown_calls == 1


def test_server_must_confirm_readonly_mailbox() -> None:
    client = FakeImapClient()
    client.readonly_confirmed = False
    provider, _, _ = provider_with_client(client)

    with pytest.raises(ProviderMailboxError, match="confirm read-only"):
        asyncio.run(provider.check_health())

    assert client.uid_calls == []
    assert client.shutdown_calls == 1


def test_mailbox_size_bound_fails_before_fetch() -> None:
    client = FakeImapClient()
    client.message_count = 10_001
    provider, _, _ = provider_with_client(client)

    with pytest.raises(ProviderMailboxError, match="read bound"):
        asyncio.run(provider.fetch_messages(limit=1))

    assert client.uid_calls == []


def test_logout_failure_falls_back_to_socket_shutdown() -> None:
    client = FakeImapClient()
    client.logout_error = OSError("synthetic logout failure")
    provider, _, _ = provider_with_client(client)

    asyncio.run(provider.check_health())

    assert client.logout_calls == 1
    assert client.shutdown_calls == 1


def test_closed_provider_denies_new_connections_and_secret_access() -> None:
    client = FakeImapClient()
    provider, resolver, factory_calls = provider_with_client(client)
    provider.close()

    with pytest.raises(ProviderConnectionError, match="closed"):
        asyncio.run(provider.check_health())

    assert resolver.calls == []
    assert factory_calls == []


def test_close_waits_for_connecting_worker_and_prevents_authentication() -> None:
    started = threading.Event()
    release = threading.Event()
    client = FakeImapClient()
    resolver = RecordingSecretResolver()

    def blocking_factory(*args, **kwargs):
        del args, kwargs
        started.set()
        assert release.wait(timeout=2)
        return client

    provider = ImapReadOnlyProvider(
        imap_settings(timeout_seconds=2), resolver, client_factory=blocking_factory
    )

    async def exercise() -> None:
        health_task = asyncio.create_task(provider.check_health())
        assert await asyncio.to_thread(started.wait, 1)
        close_task = asyncio.create_task(asyncio.to_thread(provider.close))
        await asyncio.sleep(0.01)
        assert close_task.done() is False
        release.set()
        await close_task
        with pytest.raises(ProviderConnectionError, match="closed"):
            await health_task

    asyncio.run(exercise())

    assert resolver.calls == []
    assert client.shutdown_calls == 1


def test_close_during_secret_lookup_prevents_second_lookup_and_authentication() -> None:
    started = threading.Event()
    release = threading.Event()

    class BlockingResolver:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def get_secret(self, reference: str) -> SecretValue:
            self.calls.append(reference)
            if len(self.calls) > 1:
                raise AssertionError("a closed provider must not resolve another secret")
            started.set()
            assert release.wait(timeout=2)
            return SecretValue("SYNTHETIC USER VALUE")

    client = FakeImapClient()
    resolver = BlockingResolver()
    provider, _, _ = provider_with_client(client, resolver=resolver)

    async def exercise() -> None:
        health_task = asyncio.create_task(provider.check_health())
        assert await asyncio.to_thread(started.wait, 1)
        close_task = asyncio.create_task(asyncio.to_thread(provider.close))
        await asyncio.sleep(0.01)
        release.set()
        await close_task
        with pytest.raises(ProviderConnectionError, match="closed"):
            await health_task

    asyncio.run(exercise())

    assert resolver.calls == ["HERMES_EMAIL_IMAP_USERNAME"]
    assert client.auth_calls == []
    assert client.shutdown_calls >= 1


def test_source_contains_no_mutating_or_seen_setting_imap_commands() -> None:
    source = inspect.getsource(ImapReadOnlyProvider)

    assert "BODY.PEEK[]" in source
    assert 'authenticate("PLAIN"' in source
    assert ".login(" not in source
    for forbidden in (
        "client.store(",
        "client.append(",
        "client.copy(",
        "client.move(",
        "client.expunge(",
        "client.close(",
    ):
        assert forbidden not in source
