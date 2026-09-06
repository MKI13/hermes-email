"""Opt-in read-only IMAP mailbox set with scoped IDs and bounded header scans.

No arbitrary folder is accepted from tool arguments. Message IDs survive a
restart; signed continuation cursors intentionally do not survive provider reload.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import secrets
from dataclasses import replace
from typing import Callable

from ..config import ImapSettings
from ..models import EmailMessage, EmailMessagePage, MailboxScan
from ..secrets import SecretResolver
from .base import EmailProvider, ProviderCapabilities
from .errors import ProviderConnectionError, ProviderMailboxError, ProviderProtocolError
from .imap import ImapReadOnlyProvider, ImapCursorError, ImapMessageIdError, ImapLimitError

_MESSAGE = re.compile(r"imap-m1:([0-9a-f]{32}):([0-9a-f]{16}):([1-9][0-9]{0,9}):([1-9][0-9]{0,9})\Z")
_MAX_UID = 4_294_967_295


def _digest(value: object, length: int = 32) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()[:length]


class MultiMailboxImapProvider(EmailProvider):
    """Read only configured folders; one UID-window visit per folder per call."""

    NAME = "imap"
    capabilities = ProviderCapabilities(fetch=True, get=True)

    def __init__(self, settings: ImapSettings, secret_resolver: SecretResolver, *,
                 provider_factory: Callable = ImapReadOnlyProvider) -> None:
        if not settings.mailboxes or not settings.account_namespace:
            raise ValueError("multi-folder settings require explicit mailboxes and account namespace")
        self._settings = settings
        self._closed = False
        self.account_scope = _digest([settings.account_namespace, settings.host,
                                      settings.port, settings.username_ref])
        self.scope_id = _digest([self.account_scope, settings.mailboxes, settings.security,
                                 settings.tls_sha256_fingerprint])
        self._cursor_key = secrets.token_bytes(32)
        self._folder_keys = tuple(_digest(name, 16) for name in settings.mailboxes)
        if len(set(self._folder_keys)) != len(self._folder_keys):
            raise ValueError("mailbox identity collision")
        self._children = tuple(provider_factory(replace(settings, mailbox=name, mailboxes=()),
                                                secret_resolver)
                               for name in settings.mailboxes)

    @property
    def name(self) -> str:
        return self.NAME

    def _ensure_open(self) -> None:
        if self._closed:
            raise ProviderConnectionError("IMAP mailbox set is closed")

    def close(self) -> None:
        self._closed = True
        for child in self._children:
            child.close()

    async def check_health(self) -> None:
        self._ensure_open()
        for child in self._children:
            await child.check_health()
            self._ensure_open()

    def _own(self, index: int, message: EmailMessage) -> EmailMessage:
        validity, uid = ImapReadOnlyProvider._parse_message_id(message.message_id)
        return replace(message,
            message_id=f"imap-m1:{self.account_scope}:{self._folder_keys[index]}:{validity}:{uid}",
            metadata={**message.metadata, "mailbox": self._settings.mailboxes[index],
                      "account_scope": self.account_scope})

    def _locate(self, identifier: str) -> tuple[int, str]:
        if not isinstance(identifier, str) or len(identifier) > 160:
            raise ImapMessageIdError("invalid scoped IMAP message ID")
        match = _MESSAGE.fullmatch(identifier)
        if match is None or match[1] != self.account_scope or match[2] not in self._folder_keys:
            raise ImapMessageIdError("message ID does not belong to this account and mailbox set")
        validity, uid = int(match[3]), int(match[4])
        if not 1 <= validity <= _MAX_UID or not 1 <= uid <= _MAX_UID:
            raise ImapMessageIdError("invalid scoped IMAP message ID")
        return self._folder_keys.index(match[2]), f"imap-v1:{validity}:{uid}"

    async def get_message(self, message_id: str) -> EmailMessage | None:
        return await self.get_message_limited(message_id, self._settings.max_message_bytes)

    async def get_message_limited(self, message_id: str, max_bytes: int) -> EmailMessage | None:
        self._ensure_open()
        index, local = self._locate(message_id)  # Reject foreign/legacy IDs before secrets.
        if type(max_bytes) is not int or not 1 <= max_bytes <= self._settings.max_message_bytes:
            raise ImapLimitError("invalid message byte limit")
        message = await self._children[index].get_message_limited(local, max_bytes)
        self._ensure_open()
        if message is None:
            return None
        if message.message_id != local:
            raise ProviderProtocolError("IMAP returned another message identity")
        return self._own(index, message)

    def _encode_cursor(self, kind: str, rotation: int, partial: int, states: list) -> str:
        raw = json.dumps([self.scope_id, kind, rotation, partial, states], separators=(",", ":")).encode()
        encoded = base64.urlsafe_b64encode(raw).decode().rstrip("=")
        signature = hmac.new(self._cursor_key, raw, hashlib.sha256).hexdigest()[:32]
        value = "imap-mc1:" + encoded + "." + signature
        if len(value) > 512:
            raise ProviderProtocolError("mailbox cursor exceeds its bound")
        return value

    def _decode_cursor(self, cursor: str | None, kind: str) -> tuple[int, int, list]:
        if cursor is None:
            return 0, 0, [0] * len(self._children)
        try:
            if not isinstance(cursor, str) or not cursor.isascii() or not 1 <= len(cursor) <= 512:
                raise ValueError()
            if not cursor.startswith("imap-mc1:"):
                raise ValueError()
            encoded, signature = cursor[9:].split(".")
            raw = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
            if not hmac.compare_digest(signature, hmac.new(self._cursor_key, raw, hashlib.sha256).hexdigest()[:32]):
                raise ValueError()
            scope, old_kind, rotation, partial, states = json.loads(raw)
            if scope != self.scope_id or old_kind != kind or type(rotation) is not int or not 0 <= rotation < len(self._children):
                raise ValueError()
            if type(partial) is not int or partial not in (0, 1) or not isinstance(states, list) or len(states) != len(self._children):
                raise ValueError()
            for state in states:
                if type(state) is int and state in (0, -1, -2):
                    continue
                if not isinstance(state, list) or len(state) != 2 or any(type(v) is not int or not 1 <= v <= _MAX_UID for v in state):
                    raise ValueError()
            if self._encode_cursor(kind, rotation, partial, states) != cursor:
                raise ValueError()
            return rotation, partial, states
        except (ValueError, TypeError, UnicodeError, binascii.Error):
            raise ImapCursorError("mailbox cursor is invalid, stale, or belongs to another query") from None

    async def fetch_messages(self, *, limit: int = 50, cursor: str | None = None) -> EmailMessagePage:
        """Scan headers only. A small limit rotates fairly between configured folders."""
        return await self._scan(limit, cursor, "list")

    async def search_headers(self, query: str, *, limit: int = 50,
                             cursor: str | None = None) -> EmailMessagePage:
        if not isinstance(query, str) or not query.strip() or len(query.strip()) > 256:
            raise ValueError("invalid search query")
        needle = query.strip().casefold()
        page = await self._scan(limit, cursor, "q" + _digest(needle, 16))
        def matches(message: EmailMessage) -> bool:
            values = [message.subject, message.sender.address, message.sender.display_name or ""]
            for address in (*message.recipients, *message.cc, *message.reply_to):
                values.extend([address.address, address.display_name or ""])
            return any(needle in value.casefold() for value in values)
        return replace(page, messages=tuple(message for message in page.messages if matches(message)))

    async def _scan(self, limit: int, cursor: str | None, kind: str) -> EmailMessagePage:
        self._ensure_open()
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ImapLimitError("mailbox scan limit must be from 1 through 100")
        rotation, partial, states = self._decode_cursor(cursor, kind)
        messages: list[EmailMessage] = []
        remaining = limit
        order = [(rotation + i) % len(self._children) for i in range(len(self._children))]
        pending = [i for i in order if states[i] not in (-1, -2)]
        header_limit = min(16_384, self._settings.max_message_bytes,
                           self._settings.max_page_bytes // limit)
        for position, index in enumerate(pending):
            if remaining == 0:
                break
            allowance = max(1, remaining // (len(pending) - position))
            remaining -= allowance  # UID slots, not matches; sparse ranges cannot loop forever.
            old = states[index]
            local_cursor = f"imap-v1:{old[0]}:{old[1]}" if isinstance(old, list) else None
            try:
                page = await self._children[index].fetch_headers(
                    limit=allowance, cursor=local_cursor, max_header_bytes=header_limit)
            except ProviderMailboxError:
                states[index] = -2  # Explicitly unavailable, never silently 'complete'.
                rotation = (index + 1) % len(self._children)
                continue
            self._ensure_open()
            if len(page.messages) > allowance:
                raise ProviderProtocolError("mailbox exceeded its scan budget")
            messages.extend(self._own(index, message) for message in page.messages)
            partial = int(bool(partial) or any(
                m.metadata.get("headers_truncated") == "true"
                or m.metadata.get("headers_normalization_incomplete") == "true"
                or m.recipient_headers_invalid or m.reply_to_invalid or not m.sender.address
                for m in page.messages
            ))
            states[index] = (list(ImapReadOnlyProvider._parse_cursor(page.next_cursor))
                             if page.next_cursor is not None else -1)
            if isinstance(old, list) and isinstance(states[index], list):
                if old[0] != states[index][0] or states[index][1] >= old[1]:
                    raise ProviderProtocolError("mailbox scan cursor did not advance")
            rotation = (index + 1) % len(self._children)
        self._ensure_open()
        statuses = tuple((name, "complete" if state == -1 else "unavailable" if state == -2
                         else "not-scanned" if state == 0 else "partial")
                        for name, state in zip(self._settings.mailboxes, states))
        scan = MailboxScan(self.scope_id, statuses,
                           all(state == -1 for state in states) and not partial,
                           not bool(partial), len(messages), limit)
        next_cursor = (self._encode_cursor(kind, rotation, partial, states)
                       if any(state not in (-1, -2) for state in states) else None)
        return EmailMessagePage(tuple(messages), next_cursor, scan)
