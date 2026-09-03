"""TLS-only, read-only IMAP provider with bounded UID pagination."""

from __future__ import annotations

import asyncio
import imaplib
import re
import socket
import ssl
import threading
import time
import unicodedata
from collections.abc import Callable
from datetime import UTC, datetime
from email import policy
from email.message import Message
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any, Final, Never

from ..config import ImapSettings
from ..models import EmailAddress, EmailMessage, EmailMessagePage
from ..secrets import SecretResolutionError, SecretResolver
from .base import EmailProvider, ProviderCapabilities
from .errors import (
    EmailProviderError,
    ProviderAuthenticationError,
    ProviderConnectionError,
    ProviderMailboxError,
    ProviderMessageError,
    ProviderProtocolError,
    ProviderTimeoutError,
    ProviderTlsError,
)

_UID_MAX: Final = 4_294_967_295
_CURSOR_PATTERN = re.compile(r"imap-v1:([1-9][0-9]*):([1-9][0-9]*)\Z")
_MESSAGE_ID_PATTERN = re.compile(r"imap-v1:([1-9][0-9]*):([1-9][0-9]*)\Z")
_FETCH_UID = re.compile(rb"\bUID ([1-9][0-9]*)\b")
_FETCH_SIZE = re.compile(rb"\bRFC822\.SIZE ([0-9]+)\b")
_MAX_PAGE_MESSAGES: Final = 100
_MAX_MIME_PARTS: Final = 100
_MAX_BODY_CHARACTERS: Final = 200_000
_MAX_HEADER_CHARACTERS: Final = 2_000

ImapClientFactory = Callable[..., imaplib.IMAP4_SSL]


class ImapCursorError(ValueError):
    """Raised when an IMAP cursor is malformed or belongs to another mailbox."""


class ImapLimitError(ValueError):
    """Raised when an IMAP page limit is invalid or exceeds the fixed maximum."""


class ImapMessageIdError(ValueError):
    """Raised when an IMAP message identifier is malformed or stale."""


def _raise_redacted(error: Exception) -> Never:
    """Raise a fixed error without retaining a sensitive handled exception."""
    try:
        raise error from None
    finally:
        error.__context__ = None
        error.__cause__ = None


class ImapReadOnlyProvider(EmailProvider):
    """Read one bounded UID window per verified, read-only IMAP transaction."""

    NAME: Final = "imap"
    capabilities = ProviderCapabilities(fetch=True, get=True)

    def __init__(
        self,
        settings: ImapSettings,
        secret_resolver: SecretResolver,
        *,
        client_factory: ImapClientFactory = imaplib.IMAP4_SSL,
    ) -> None:
        if (
            settings.host is None
            or settings.username_ref is None
            or settings.password_ref is None
        ):
            raise ValueError("complete IMAP settings are required")
        self._settings = settings
        self._secret_resolver = secret_resolver
        self._client_factory = client_factory
        self._lifecycle_lock = threading.Lock()
        self._worker_condition = threading.Condition(self._lifecycle_lock)
        self._active_clients: set[imaplib.IMAP4_SSL] = set()
        self._active_workers = 0
        self._closed = False

    @property
    def name(self) -> str:
        """Return the stable IMAP provider identifier."""
        return self.NAME

    async def check_health(self) -> None:
        """Verify TLS, authentication, and read-only mailbox access without fetching."""
        try:
            await asyncio.to_thread(self._run_worker, self._check_health_sync)
        except EmailProviderError as error:
            _raise_redacted(type(error)(str(error)))

    async def fetch_messages(
        self, *, limit: int = 50, cursor: str | None = None
    ) -> EmailMessagePage:
        """Fetch one bounded newest-first UID window without changing flags."""
        try:
            return await asyncio.to_thread(
                self._run_worker, self._fetch_messages_sync, limit, cursor
            )
        except EmailProviderError as error:
            _raise_redacted(type(error)(str(error)))

    async def get_message(self, message_id: str) -> EmailMessage | None:
        """Fetch exactly one provider-stable UID without changing flags."""
        try:
            return await asyncio.to_thread(
                self._run_worker, self._get_message_sync, message_id
            )
        except EmailProviderError as error:
            _raise_redacted(type(error)(str(error)))

    def close(self) -> None:
        """Prevent new work, interrupt sockets, and wait one timeout for workers."""
        with self._lifecycle_lock:
            self._closed = True
            clients = tuple(self._active_clients)
        for client in clients:
            self._shutdown(client)
        deadline = time.monotonic() + self._settings.timeout_seconds
        with self._worker_condition:
            while self._active_workers:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._worker_condition.wait(timeout=remaining)

    def _ensure_open(self) -> None:
        with self._lifecycle_lock:
            if self._closed:
                raise ProviderConnectionError("IMAP provider is closed")

    def _run_worker(self, operation: Callable[..., Any], *args: Any) -> Any:
        with self._worker_condition:
            if self._closed:
                raise ProviderConnectionError("IMAP provider is closed")
            self._active_workers += 1
        try:
            return operation(*args)
        finally:
            with self._worker_condition:
                self._active_workers -= 1
                self._worker_condition.notify_all()

    def _check_health_sync(self) -> None:
        client, _, _ = self._open_readonly_mailbox()
        self._logout(client)

    def _fetch_messages_sync(
        self, limit: int, cursor: str | None
    ) -> EmailMessagePage:
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= _MAX_PAGE_MESSAGES
        ):
            raise ImapLimitError("IMAP limit must be an integer from 1 through 100")
        if cursor is not None:
            self._parse_cursor(cursor)
        client, uid_validity, uid_next = self._open_readonly_mailbox()
        try:
            upper_uid = self._cursor_upper_bound(cursor, uid_validity, uid_next)
            if upper_uid == 0:
                return EmailMessagePage(messages=(), next_cursor=None)
            lower_uid = max(1, upper_uid - limit + 1)
            body_limit = min(
                self._settings.max_message_bytes,
                self._settings.max_page_bytes // limit,
            )
            records = self._fetch_uid_range(client, lower_uid, upper_uid, body_limit)
            messages = tuple(
                self._normalize_record(
                    uid_validity, uid, raw, remote_size, body_limit
                )
                for uid, raw, remote_size in sorted(records, reverse=True)
            )
            next_cursor = (
                self._encode_cursor(uid_validity, lower_uid - 1)
                if lower_uid > 1
                else None
            )
            return EmailMessagePage(messages=messages, next_cursor=next_cursor)
        finally:
            self._logout(client)

    def _get_message_sync(self, message_id: str) -> EmailMessage | None:
        requested_validity, uid = self._parse_message_id(message_id)
        client, uid_validity, _ = self._open_readonly_mailbox()
        try:
            if requested_validity != uid_validity:
                raise ImapMessageIdError("IMAP message identifier is stale")
            records = self._fetch_uid_range(
                client, uid, uid, self._settings.max_message_bytes
            )
            if not records:
                return None
            if len(records) != 1 or records[0][0] != uid:
                raise ProviderProtocolError("IMAP returned an unexpected message")
            _, raw, remote_size = records[0]
            return self._normalize_record(
                uid_validity,
                uid,
                raw,
                remote_size,
                self._settings.max_message_bytes,
            )
        finally:
            self._logout(client)

    def _open_readonly_mailbox(self) -> tuple[imaplib.IMAP4_SSL, int, int]:
        client = self._connect()
        try:
            self._authenticate(client)
            try:
                response_type, message_count_data = client.select(
                    self._settings.mailbox, readonly=True
                )
            except (TimeoutError, socket.timeout):
                _raise_redacted(ProviderTimeoutError("IMAP mailbox selection timed out"))
            except imaplib.IMAP4.abort:
                _raise_redacted(ProviderConnectionError("IMAP connection was interrupted"))
            except imaplib.IMAP4.error:
                _raise_redacted(ProviderMailboxError("IMAP mailbox is unavailable"))
            except ssl.SSLError:
                _raise_redacted(ProviderTlsError("IMAP TLS transport failed"))
            except OSError:
                _raise_redacted(ProviderConnectionError("IMAP connection was interrupted"))
            if response_type != "OK":
                raise ProviderMailboxError("IMAP mailbox is unavailable")
            if not self._has_response_code(client, "READ-ONLY"):
                raise ProviderMailboxError("IMAP server did not confirm read-only access")
            message_count = self._single_response_number(
                message_count_data, "mailbox message count", allow_zero=True
            )
            if message_count > self._settings.max_mailbox_messages:
                raise ProviderMailboxError("IMAP mailbox exceeds the configured read bound")
            uid_validity = self._required_response_number(client, "UIDVALIDITY")
            uid_next = self._required_response_number(client, "UIDNEXT")
            if uid_validity > _UID_MAX or uid_next > _UID_MAX + 1:
                raise ProviderProtocolError("IMAP mailbox identifiers are out of range")
            return client, uid_validity, uid_next
        except Exception:
            self._discard(client)
            raise

    def _connect(self) -> imaplib.IMAP4_SSL:
        with self._lifecycle_lock:
            if self._closed:
                raise ProviderConnectionError("IMAP provider is closed")
        try:
            tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            tls_context.minimum_version = ssl.TLSVersion.TLSv1_2
            tls_context.check_hostname = True
            tls_context.verify_mode = ssl.CERT_REQUIRED
            tls_context.load_default_certs(ssl.Purpose.SERVER_AUTH)
            client = self._client_factory(
                self._settings.host,
                self._settings.port,
                ssl_context=tls_context,
                timeout=self._settings.timeout_seconds,
            )
        except ssl.SSLCertVerificationError:
            _raise_redacted(ProviderTlsError("IMAP certificate verification failed"))
        except ssl.SSLError:
            _raise_redacted(ProviderTlsError("IMAP TLS negotiation failed"))
        except (TimeoutError, socket.timeout):
            _raise_redacted(ProviderTimeoutError("IMAP connection timed out"))
        except (imaplib.IMAP4.abort, OSError):
            _raise_redacted(ProviderConnectionError("IMAP endpoint is unreachable"))
        client.debug = 0
        with self._lifecycle_lock:
            if self._closed:
                self._shutdown(client)
                raise ProviderConnectionError("IMAP provider is closed")
            self._active_clients.add(client)
        return client

    def _authenticate(self, client: imaplib.IMAP4_SSL) -> None:
        self._ensure_open()
        username = None
        password = None
        try:
            username = self._secret_resolver.get_secret(self._settings.username_ref)
            with self._lifecycle_lock:
                closed = self._closed
            if closed:
                username = None
                raise ProviderConnectionError("IMAP provider is closed")
            password = self._secret_resolver.get_secret(self._settings.password_ref)
        except SecretResolutionError:
            username = None
            password = None
            _raise_redacted(
                ProviderAuthenticationError("IMAP credentials are unavailable")
            )
        with self._lifecycle_lock:
            closed = self._closed
        if closed:
            username = None
            password = None
            raise ProviderConnectionError("IMAP provider is closed")
        try:
            username_bytes = username.reveal().encode("utf-8")
            password_bytes = password.reveal().encode("utf-8")
        except UnicodeEncodeError:
            username_bytes = b""
            password_bytes = b""
            del username
            del password
            _raise_redacted(ProviderAuthenticationError("IMAP credentials are invalid"))

        def sasl_plain(_challenge: bytes) -> bytes:
            return b"\0" + username_bytes + b"\0" + password_bytes

        try:
            response_type, _ = client.authenticate("PLAIN", sasl_plain)
        except (TimeoutError, socket.timeout):
            _raise_redacted(ProviderTimeoutError("IMAP authentication timed out"))
        except imaplib.IMAP4.abort:
            _raise_redacted(ProviderConnectionError("IMAP connection was interrupted"))
        except imaplib.IMAP4.error:
            _raise_redacted(ProviderAuthenticationError("IMAP authentication failed"))
        except ssl.SSLError:
            _raise_redacted(ProviderTlsError("IMAP TLS transport failed"))
        except OSError:
            _raise_redacted(ProviderConnectionError("IMAP connection was interrupted"))
        finally:
            del username_bytes
            del password_bytes
            del username
            del password
        if response_type != "OK":
            raise ProviderAuthenticationError("IMAP authentication failed")

    def _fetch_uid_range(
        self,
        client: imaplib.IMAP4_SSL,
        lower_uid: int,
        upper_uid: int,
        body_limit: int,
    ) -> list[tuple[int, bytes, int]]:
        uid_set = f"{lower_uid}:{upper_uid}"
        query = f"(UID RFC822.SIZE BODY.PEEK[]<0.{body_limit}>)"
        try:
            response_type, data = client.uid("FETCH", uid_set, query)
        except (TimeoutError, socket.timeout):
            _raise_redacted(ProviderTimeoutError("IMAP message fetch timed out"))
        except imaplib.IMAP4.abort:
            _raise_redacted(ProviderConnectionError("IMAP connection was interrupted"))
        except imaplib.IMAP4.error:
            _raise_redacted(ProviderProtocolError("IMAP message fetch failed"))
        except ssl.SSLError:
            _raise_redacted(ProviderTlsError("IMAP TLS transport failed"))
        except OSError:
            _raise_redacted(ProviderConnectionError("IMAP connection was interrupted"))
        if response_type != "OK" or not isinstance(data, list):
            raise ProviderProtocolError("IMAP returned an invalid fetch response")
        records: list[tuple[int, bytes, int]] = []
        seen_uids: set[int] = set()
        for item in data:
            if item is None:
                continue
            if isinstance(item, bytes):
                if item.strip() == b")":
                    continue
                raise ProviderProtocolError("IMAP returned unexpected fetch data")
            if not isinstance(item, tuple) or len(item) != 2:
                raise ProviderProtocolError("IMAP returned unexpected fetch data")
            metadata, raw = item
            if not isinstance(metadata, bytes) or not isinstance(raw, bytes):
                raise ProviderProtocolError("IMAP returned an invalid message literal")
            uid_match = _FETCH_UID.search(metadata)
            size_match = _FETCH_SIZE.search(metadata)
            if uid_match is None or size_match is None:
                raise ProviderProtocolError("IMAP fetch metadata is incomplete")
            uid = int(uid_match.group(1))
            remote_size = int(size_match.group(1))
            if not lower_uid <= uid <= upper_uid or uid > _UID_MAX:
                raise ProviderProtocolError("IMAP returned an unexpected UID")
            if uid in seen_uids:
                raise ProviderProtocolError("IMAP returned a duplicate UID")
            maximum_size = min(remote_size, body_limit)
            if len(raw) > maximum_size:
                raise ProviderMessageError("IMAP message literal length is inconsistent")
            seen_uids.add(uid)
            records.append((uid, raw, remote_size))
        return records

    def _normalize_record(
        self,
        uid_validity: int,
        uid: int,
        raw: bytes,
        remote_size: int,
        body_limit: int,
    ) -> EmailMessage:
        try:
            return self._normalize_message(
                uid_validity, uid, raw, remote_size, body_limit
            )
        except ProviderMessageError:
            raise
        except Exception:
            _raise_redacted(ProviderMessageError("IMAP message could not be normalized"))

    def _normalize_message(
        self,
        uid_validity: int,
        uid: int,
        raw: bytes,
        remote_size: int,
        body_limit: int,
    ) -> EmailMessage:
        try:
            parsed = BytesParser(policy=policy.default).parsebytes(raw)
        except Exception:
            _raise_redacted(ProviderMessageError("IMAP message could not be parsed safely"))
        subject = _clean_header(str(parsed.get("Subject", "")))
        sender = _first_address(parsed.get_all("From", []))
        recipients = _addresses(
            parsed.get_all("To", []) + parsed.get_all("Cc", [])
        )
        body_text, body_kind = _extract_body(parsed)
        received_at = _parse_received_at(parsed.get("Date"))
        metadata = {
            "provider": self.NAME,
            "imap_uid": str(uid),
            "imap_uidvalidity": str(uid_validity),
            "content": body_kind,
            "truncated": "true" if len(raw) < remote_size else "false",
        }
        rfc_message_id = _clean_header(str(parsed.get("Message-ID", "")))
        if rfc_message_id:
            metadata["rfc_message_id"] = rfc_message_id
        return EmailMessage(
            message_id=self._encode_message_id(uid_validity, uid),
            subject=subject,
            sender=sender,
            recipients=recipients,
            body_text=body_text,
            received_at=received_at,
            metadata=metadata,
        )

    def _cursor_upper_bound(
        self, cursor: str | None, uid_validity: int, uid_next: int
    ) -> int:
        if cursor is None:
            return max(0, uid_next - 1)
        cursor_validity, upper_uid = self._parse_cursor(cursor)
        if cursor_validity != uid_validity:
            raise ImapCursorError("IMAP pagination cursor is stale")
        if upper_uid >= uid_next:
            raise ImapCursorError("IMAP pagination cursor is outside the mailbox snapshot")
        return upper_uid

    @staticmethod
    def _parse_cursor(cursor: str) -> tuple[int, int]:
        if not isinstance(cursor, str):
            raise ImapCursorError("invalid IMAP pagination cursor")
        match = _CURSOR_PATTERN.fullmatch(cursor)
        if match is None:
            raise ImapCursorError("invalid IMAP pagination cursor")
        uid_validity, upper_uid = (int(value) for value in match.groups())
        if uid_validity > _UID_MAX or upper_uid > _UID_MAX:
            raise ImapCursorError("invalid IMAP pagination cursor")
        return uid_validity, upper_uid

    @staticmethod
    def _encode_cursor(uid_validity: int, upper_uid: int) -> str:
        return f"imap-v1:{uid_validity}:{upper_uid}"

    @staticmethod
    def _parse_message_id(message_id: str) -> tuple[int, int]:
        if not isinstance(message_id, str):
            raise ImapMessageIdError("invalid IMAP message identifier")
        match = _MESSAGE_ID_PATTERN.fullmatch(message_id)
        if match is None:
            raise ImapMessageIdError("invalid IMAP message identifier")
        uid_validity, uid = (int(value) for value in match.groups())
        if uid_validity > _UID_MAX or uid > _UID_MAX:
            raise ImapMessageIdError("invalid IMAP message identifier")
        return uid_validity, uid

    @staticmethod
    def _encode_message_id(uid_validity: int, uid: int) -> str:
        return f"imap-v1:{uid_validity}:{uid}"

    @classmethod
    def _has_response_code(cls, client: imaplib.IMAP4_SSL, name: str) -> bool:
        values = cls._response_values(client, name)
        return isinstance(values, list) and any(value is not None for value in values)

    @classmethod
    def _required_response_number(
        cls, client: imaplib.IMAP4_SSL, name: str
    ) -> int:
        values = cls._response_values(client, name)
        return cls._single_response_number(values, name, allow_zero=False)

    @staticmethod
    def _response_values(client: imaplib.IMAP4_SSL, name: str) -> Any:
        try:
            _, values = client.response(name)
        except (TimeoutError, socket.timeout):
            _raise_redacted(ProviderTimeoutError("IMAP response timed out"))
        except imaplib.IMAP4.abort:
            _raise_redacted(ProviderConnectionError("IMAP connection was interrupted"))
        except imaplib.IMAP4.error:
            _raise_redacted(ProviderProtocolError("IMAP response is unavailable"))
        except ssl.SSLError:
            _raise_redacted(ProviderTlsError("IMAP TLS transport failed"))
        except OSError:
            _raise_redacted(ProviderConnectionError("IMAP connection was interrupted"))
        return values

    @staticmethod
    def _single_response_number(
        values: Any, name: str, *, allow_zero: bool
    ) -> int:
        if not isinstance(values, list) or len(values) != 1:
            raise ProviderProtocolError(f"IMAP {name} response is invalid")
        value = values[0]
        if isinstance(value, bytes):
            try:
                text = value.decode("ascii", errors="strict")
            except UnicodeDecodeError:
                _raise_redacted(ProviderProtocolError(f"IMAP {name} response is invalid"))
        elif isinstance(value, str):
            text = value
        else:
            raise ProviderProtocolError(f"IMAP {name} response is invalid")
        if not text.isdigit():
            raise ProviderProtocolError(f"IMAP {name} response is invalid")
        number = int(text)
        if number < (0 if allow_zero else 1):
            raise ProviderProtocolError(f"IMAP {name} response is invalid")
        return number

    def _logout(self, client: imaplib.IMAP4_SSL) -> None:
        try:
            client.logout()
        except Exception:
            self._shutdown(client)
        finally:
            with self._lifecycle_lock:
                self._active_clients.discard(client)

    def _discard(self, client: imaplib.IMAP4_SSL) -> None:
        self._shutdown(client)
        with self._lifecycle_lock:
            self._active_clients.discard(client)

    @staticmethod
    def _shutdown(client: imaplib.IMAP4_SSL) -> None:
        try:
            client.shutdown()
        except Exception:
            pass


class _PlainTextExtractor(HTMLParser):
    _BLOCK_TAGS: Final = {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "div",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "li",
        "main",
        "p",
        "section",
        "table",
        "tr",
    }
    _SUPPRESSED_TAGS: Final = {"head", "noscript", "script", "style", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._open_tags: list[tuple[str, bool]] = []
        self._chunks: list[str] = []
        self.has_stylesheet = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = tag.casefold()
        if normalized == "style":
            self.has_stylesheet = True
        normalized_attrs = {
            name.casefold(): value.casefold() if isinstance(value, str) else None
            for name, value in attrs
        }
        hidden = (
            normalized in self._SUPPRESSED_TAGS
            or "hidden" in normalized_attrs
            or normalized_attrs.get("aria-hidden") not in {None, "false"}
            or "style" in normalized_attrs
        )
        self._open_tags.append((normalized, hidden))
        if not self._is_suppressed() and normalized in self._BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.casefold()
        for index in range(len(self._open_tags) - 1, -1, -1):
            if self._open_tags[index][0] == normalized:
                del self._open_tags[index:]
                break
        if not self._is_suppressed() and normalized in self._BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._is_suppressed():
            self._chunks.append(data)

    def _is_suppressed(self) -> bool:
        return any(hidden for _, hidden in self._open_tags)

    def text(self) -> str:
        return _clean_body("".join(self._chunks))


def _extract_body(message: Message) -> tuple[str | None, str]:
    plain_parts: list[str] = []
    html_parts: list[str] = []
    for part in _body_parts(message):
        content_type = part.get_content_type().casefold()
        if content_type not in {"text/plain", "text/html"}:
            continue
        text = _decode_text_part(part)
        if content_type == "text/plain":
            plain_parts.append(text)
        else:
            html_parts.append(text)
    if plain_parts:
        return _clean_body("\n\n".join(plain_parts)), "text/plain"
    if html_parts:
        extractor = _PlainTextExtractor()
        try:
            extractor.feed("\n".join(html_parts))
            extractor.close()
        except Exception:
            return None, "html-omitted"
        if extractor.has_stylesheet:
            return None, "html-omitted"
        return extractor.text(), "text/html-as-plain-text"
    return None, "none"


def _body_parts(message: Message) -> tuple[Message, ...]:
    parts: list[Message] = []
    visited = 0

    def visit(part: Message) -> None:
        nonlocal visited
        if visited >= _MAX_MIME_PARTS:
            return
        visited += 1
        if part.get_content_disposition() == "attachment" or part.get_filename():
            return
        if part.get_content_maintype().casefold() == "multipart":
            payload = part.get_payload()
            if isinstance(payload, list):
                for child in payload:
                    if isinstance(child, Message):
                        visit(child)
            return
        if part.is_multipart():
            return
        parts.append(part)

    visit(message)
    return tuple(parts)


def _decode_text_part(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        undecoded = part.get_payload()
        return undecoded if isinstance(undecoded, str) else ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except (LookupError, UnicodeError):
        return payload.decode("utf-8", errors="replace")


def _clean_header(value: str) -> str:
    unfolded = value.replace("\r", " ").replace("\n", " ")
    without_controls = "".join(
        character
        for character in unfolded
        if not unicodedata.category(character).startswith("C")
    )
    return " ".join(without_controls.split())[:_MAX_HEADER_CHARACTERS]


def _clean_body(value: str) -> str:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    without_controls = "".join(
        character
        for character in normalized
        if character in {"\n", "\t"}
        or not unicodedata.category(character).startswith("C")
    )
    lines = [" ".join(line.split()) for line in without_controls.split("\n")]
    compact: list[str] = []
    for line in lines:
        if line or not compact or compact[-1]:
            compact.append(line)
    return "\n".join(compact).strip()[:_MAX_BODY_CHARACTERS]


def _addresses(header_values: list[Any]) -> tuple[EmailAddress, ...]:
    text_values = [_clean_header(str(value)) for value in header_values]
    normalized: list[EmailAddress] = []
    try:
        parsed_addresses = getaddresses(text_values)
    except (TypeError, ValueError):
        return ()
    for display_name, address in parsed_addresses:
        safe_address = _clean_header(address)
        if not safe_address:
            continue
        safe_name = _clean_header(display_name) or None
        normalized.append(EmailAddress(address=safe_address, display_name=safe_name))
    return tuple(normalized)


def _first_address(header_values: list[Any]) -> EmailAddress:
    addresses = _addresses(header_values)
    return addresses[0] if addresses else EmailAddress(address="")


def _parse_received_at(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = parsedate_to_datetime(str(value))
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    try:
        return parsed.astimezone(UTC)
    except (ValueError, OverflowError):
        return None
