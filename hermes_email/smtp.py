"""Single-attempt SMTP submission over mandatory verified TLS."""

from __future__ import annotations

import smtplib
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Final, Protocol

from .addressing import AddressValidationError, canonical_address, normalize_ascii_address
from .config import SmtpSettings
from .secrets import SecretResolutionError, SecretResolver

_MAX_RECIPIENTS: Final = 50
_MAX_SMTP_LINE: Final = 998
_LOCAL_HOSTNAME: Final = "[127.0.0.1]"


class SmtpError(RuntimeError):
    """Base class for fixed SMTP transport failures."""


class SmtpSubmissionError(SmtpError):
    """Raised when a prepared submission violates transport invariants."""


class SmtpClosedError(SmtpError):
    """Raised when work is requested after transport closure."""


class SmtpConnectionError(SmtpError):
    """Raised when a pre-DATA connection operation fails definitely."""


class SmtpTlsError(SmtpError):
    """Raised when verified TLS cannot be established."""


class SmtpAuthenticationError(SmtpError):
    """Raised when SMTP authentication fails without exposing details."""


class SmtpTimeoutError(SmtpError):
    """Raised when a pre-DATA operation times out definitely."""


class SmtpProtocolError(SmtpError):
    """Raised when the server violates required pre-DATA SMTP behavior."""


class SmtpRecipientRejectedError(SmtpError):
    """Raised before DATA when any envelope recipient is rejected."""


class SmtpDeliveryRejectedError(SmtpError):
    """Raised when the server gives a definite non-success DATA response."""


class SmtpDeliveryUnknownError(SmtpError):
    """Raised when connection loss after DATA begins leaves acceptance unknown."""


@dataclass(frozen=True, slots=True)
class SmtpSubmission:
    """Validated exact envelope and RFC message bytes for one SMTP attempt."""

    envelope_sender: str
    envelope_recipients: tuple[str, ...]
    message_bytes: bytes
    max_message_bytes: int

    def __post_init__(self) -> None:
        try:
            sender = normalize_ascii_address(self.envelope_sender)
        except AddressValidationError:
            raise SmtpSubmissionError("SMTP envelope sender is invalid") from None
        recipients_value = self.envelope_recipients
        if not isinstance(recipients_value, (tuple, list)):
            raise SmtpSubmissionError("SMTP envelope recipients are invalid")
        if not 1 <= len(recipients_value) <= _MAX_RECIPIENTS:
            raise SmtpSubmissionError("SMTP envelope recipient count is invalid")
        recipients = []
        canonical = set()
        for value in recipients_value:
            try:
                address = normalize_ascii_address(value)
                key = canonical_address(address)
            except AddressValidationError:
                raise SmtpSubmissionError("SMTP envelope recipient is invalid") from None
            if key in canonical:
                raise SmtpSubmissionError("SMTP envelope recipients contain duplicates")
            canonical.add(key)
            recipients.append(address)
        if (
            isinstance(self.max_message_bytes, bool)
            or not isinstance(self.max_message_bytes, int)
            or not 1_024 <= self.max_message_bytes <= 10_000_000
        ):
            raise SmtpSubmissionError("SMTP message byte limit is invalid")
        payload = self.message_bytes
        if not isinstance(payload, bytes) or not payload:
            raise SmtpSubmissionError("SMTP message bytes are invalid")
        if len(payload) > self.max_message_bytes:
            raise SmtpSubmissionError("SMTP message exceeds its byte limit")
        if b"\x00" in payload or not payload.endswith(b"\r\n"):
            raise SmtpSubmissionError("SMTP message framing is invalid")
        if b"\n" in payload.replace(b"\r\n", b"") or b"\r" in payload.replace(
            b"\r\n", b""
        ):
            raise SmtpSubmissionError("SMTP message contains a bare line ending")
        header_block = payload.split(b"\r\n\r\n", 1)[0]
        if any(line.lower().startswith(b"bcc:") for line in header_block.split(b"\r\n")):
            raise SmtpSubmissionError("SMTP message must not contain a Bcc header")
        if any(len(line) > _MAX_SMTP_LINE for line in payload.split(b"\r\n")):
            raise SmtpSubmissionError("SMTP message line exceeds its limit")
        object.__setattr__(self, "envelope_sender", sender)
        object.__setattr__(self, "envelope_recipients", tuple(recipients))


@dataclass(frozen=True, slots=True)
class SmtpSubmissionResult:
    """Definite final SMTP acceptance; it does not prove mailbox delivery."""

    accepted: bool = True


class SmtpTransport(Protocol):
    """Disconnected synchronous transport for one bounded SMTP attempt."""

    def check_health(self) -> None:
        """Verify TLS, authentication, and NOOP without envelope commands."""
        ...

    def submit_once(self, submission: SmtpSubmission) -> SmtpSubmissionResult:
        """Perform exactly one SMTP transaction without retry."""
        ...

    def close(self) -> None:
        """Prevent new work and interrupt active clients."""
        ...


class SmtplibTransport:
    """Production SMTP transport with dependency-injected protocol clients."""

    def __init__(
        self,
        settings: SmtpSettings,
        secret_resolver: SecretResolver,
        *,
        implicit_factory: Callable[..., Any] = smtplib.SMTP_SSL,
        starttls_factory: Callable[..., Any] = smtplib.SMTP,
    ) -> None:
        if (
            settings.mode != "submission"
            or settings.host is None
            or settings.username_ref is None
            or settings.password_ref is None
        ):
            raise ValueError("complete SMTP submission settings are required")
        self._settings = settings
        self._secret_resolver = secret_resolver
        self._implicit_factory = implicit_factory
        self._starttls_factory = starttls_factory
        self._lifecycle_lock = threading.Lock()
        self._worker_condition = threading.Condition(self._lifecycle_lock)
        self._active_clients: set[Any] = set()
        self._active_workers = 0
        self._closed = False

    def check_health(self) -> None:
        """Run one explicit authenticated NOOP without MAIL, RCPT, or DATA."""
        self._run_worker(self._check_health_sync)

    def submit_once(self, submission: SmtpSubmission) -> SmtpSubmissionResult:
        """Perform exactly one all-recipient SMTP submission transaction."""
        if not isinstance(submission, SmtpSubmission):
            raise SmtpSubmissionError("SMTP submission object is invalid")
        if submission.envelope_sender != self._settings.sender_address:
            raise SmtpSubmissionError("SMTP envelope sender does not match configuration")
        if len(submission.message_bytes) > self._settings.max_message_bytes:
            raise SmtpSubmissionError("SMTP message exceeds the configured byte limit")
        return self._run_worker(self._submit_sync, submission)

    def close(self) -> None:
        """Interrupt active clients and wait at most one configured timeout."""
        with self._lifecycle_lock:
            self._closed = True
            clients = tuple(self._active_clients)
        for client in clients:
            self._close_client(client)
        deadline = time.monotonic() + self._settings.timeout_seconds
        with self._worker_condition:
            while self._active_workers:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._worker_condition.wait(timeout=remaining)

    def _run_worker(self, operation: Callable[..., Any], *arguments: Any) -> Any:
        with self._worker_condition:
            if self._closed:
                raise SmtpClosedError("SMTP transport is closed")
            self._active_workers += 1
        try:
            return operation(*arguments)
        finally:
            with self._worker_condition:
                self._active_workers -= 1
                self._worker_condition.notify_all()

    def _check_health_sync(self) -> None:
        client = self._open_authenticated()
        try:
            code, _response = client.noop()
            if code != 250:
                raise SmtpProtocolError("SMTP health check was rejected")
        except SmtpError:
            raise
        except (TimeoutError, socket.timeout):
            raise SmtpTimeoutError("SMTP health check timed out") from None
        except (smtplib.SMTPException, OSError):
            raise SmtpConnectionError("SMTP health check failed") from None
        except Exception:
            raise SmtpConnectionError("SMTP health check failed") from None
        finally:
            self._finish_client(client, known_success=False)

    def _submit_sync(self, submission: SmtpSubmission) -> SmtpSubmissionResult:
        client = self._open_authenticated()
        accepted = False
        try:
            size_supported = self._check_server_size(client, submission)
            options = (
                (f"SIZE={len(submission.message_bytes)}",)
                if size_supported
                else ()
            )
            code, _response = client.mail(
                submission.envelope_sender,
                options=options,
            )
            if code != 250:
                raise SmtpProtocolError("SMTP envelope sender was rejected")
            for recipient in submission.envelope_recipients:
                code, _response = client.rcpt(recipient)
                if code not in {250, 251, 252}:
                    self._reset(client)
                    raise SmtpRecipientRejectedError("SMTP recipient was rejected")
            try:
                code, _response = client.data(submission.message_bytes)
            except (Exception, KeyboardInterrupt):
                raise SmtpDeliveryUnknownError(
                    "SMTP acceptance is unknown after DATA began"
                ) from None
            if code != 250:
                raise SmtpDeliveryRejectedError("SMTP DATA was rejected")
            accepted = True
            return SmtpSubmissionResult()
        except SmtpError:
            raise
        except (TimeoutError, socket.timeout):
            raise SmtpTimeoutError("SMTP submission timed out before DATA") from None
        except ssl.SSLError:
            raise SmtpTlsError("SMTP TLS transport failed") from None
        except (smtplib.SMTPException, OSError):
            raise SmtpConnectionError("SMTP submission failed before DATA") from None
        except Exception:
            raise SmtpConnectionError("SMTP submission failed before DATA") from None
        finally:
            self._finish_client(client, known_success=accepted)

    def _open_authenticated(self) -> Any:
        context = self._tls_context()
        client = None
        ownership_returned = False
        try:
            if self._settings.security == "implicit_tls":
                client = self._implicit_factory(
                    self._settings.host,
                    self._settings.port,
                    local_hostname=_LOCAL_HOSTNAME,
                    timeout=self._settings.timeout_seconds,
                    context=context,
                )
                self._register_client(client)
                self._verify_tls(client)
                self._ehlo(client)
            else:
                client = self._starttls_factory(
                    self._settings.host,
                    self._settings.port,
                    local_hostname=_LOCAL_HOSTNAME,
                    timeout=self._settings.timeout_seconds,
                )
                self._register_client(client)
                self._ehlo(client)
                features = getattr(client, "esmtp_features", {})
                if not isinstance(features, dict) or "starttls" not in features:
                    raise SmtpTlsError("SMTP STARTTLS is required")
                code, _response = client.starttls(context=context)
                if code != 220:
                    raise SmtpTlsError("SMTP STARTTLS was rejected")
                self._verify_tls(client)
                self._ehlo(client)
            self._ensure_open()
            features = getattr(client, "esmtp_features", None)
            if not isinstance(features, dict):
                raise SmtpProtocolError("SMTP capabilities are unavailable")
            mechanisms = str(features.get("auth", "")).upper().split()
            if "PLAIN" not in mechanisms:
                raise SmtpProtocolError("SMTP AUTH PLAIN is required")
            username_secret = self._secret_resolver.get_secret(
                self._settings.username_ref
            )
            password_secret = self._secret_resolver.get_secret(
                self._settings.password_ref
            )
            username = username_secret.reveal()
            password = password_secret.reveal()
            username_secret = password_secret = None
            try:
                username.encode("ascii")
                password.encode("ascii")
            except UnicodeEncodeError:
                raise SmtpAuthenticationError(
                    "SMTP credentials use an unsupported encoding"
                ) from None
            if "\x00" in username or "\x00" in password:
                raise SmtpAuthenticationError("SMTP credentials are invalid")
            auth_value = "\x00" + username + "\x00" + password
            username = password = ""
            client.auth("PLAIN", lambda challenge=None: auth_value)
            auth_value = ""
            ownership_returned = True
            return client
        except SmtpError:
            raise
        except SecretResolutionError:
            raise SmtpAuthenticationError("SMTP credentials are unavailable") from None
        except smtplib.SMTPAuthenticationError:
            raise SmtpAuthenticationError("SMTP authentication failed") from None
        except (TimeoutError, socket.timeout):
            raise SmtpTimeoutError("SMTP connection timed out") from None
        except ssl.SSLError:
            raise SmtpTlsError("SMTP TLS transport failed") from None
        except (smtplib.SMTPException, OSError):
            raise SmtpConnectionError("SMTP connection failed") from None
        except KeyboardInterrupt:
            raise SmtpConnectionError("SMTP connection was interrupted") from None
        except Exception:
            raise SmtpConnectionError("SMTP connection failed") from None
        finally:
            if client is not None and not ownership_returned:
                self._finish_client(client, known_success=False)

    @staticmethod
    def _tls_context() -> ssl.SSLContext:
        try:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.check_hostname = True
            context.verify_mode = ssl.CERT_REQUIRED
            context.load_default_certs(ssl.Purpose.SERVER_AUTH)
            context.keylog_filename = None
            return context
        except (OSError, ssl.SSLError):
            raise SmtpTlsError("SMTP TLS configuration failed") from None

    @staticmethod
    def _verify_tls(client: Any) -> None:
        sock = getattr(client, "sock", None)
        version_method = getattr(sock, "version", None)
        if not callable(version_method):
            raise SmtpTlsError("SMTP TLS state is unavailable")
        if version_method() not in {"TLSv1.2", "TLSv1.3"}:
            raise SmtpTlsError("SMTP TLS version is unsupported")

    @staticmethod
    def _ehlo(client: Any) -> None:
        code, _response = client.ehlo()
        if code != 250:
            raise SmtpProtocolError("SMTP EHLO was rejected")
        client.set_debuglevel(0)

    @staticmethod
    def _check_server_size(client: Any, submission: SmtpSubmission) -> bool:
        features = getattr(client, "esmtp_features", {})
        if "size" not in features:
            return False
        advertised = str(features["size"]).strip().split(" ", 1)[0]
        if not advertised:
            return True
        if not advertised.isdigit():
            raise SmtpProtocolError("SMTP SIZE capability is invalid")
        if len(submission.message_bytes) > int(advertised):
            raise SmtpSubmissionError("SMTP server size limit is too small")
        return True

    def _register_client(self, client: Any) -> None:
        with self._lifecycle_lock:
            if self._closed:
                self._close_client(client)
                raise SmtpClosedError("SMTP transport is closed")
            self._active_clients.add(client)

    def _ensure_open(self) -> None:
        with self._lifecycle_lock:
            if self._closed:
                raise SmtpClosedError("SMTP transport is closed")

    def _finish_client(self, client: Any, *, known_success: bool) -> None:
        with self._lifecycle_lock:
            self._active_clients.discard(client)
        if known_success:
            try:
                client.quit()
                return
            except Exception:
                pass
        self._close_client(client)

    @staticmethod
    def _reset(client: Any) -> None:
        try:
            client.rset()
        except Exception:
            pass

    @staticmethod
    def _close_client(client: Any) -> None:
        try:
            client.close()
        except Exception:
            pass
