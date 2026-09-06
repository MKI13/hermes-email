"""Explicit Hermes CLI approval; no slash/model tool and no cached auto-allow."""
from __future__ import annotations

import json
import os
import select
import stat
import time
from pathlib import Path
from typing import Any

from .approval import ApprovalAuthority, ApprovalError, ApprovalScope, review_snapshot


class LocalTerminal:
    """Foreground, owner-bound controlling TTY. Stdin pipes are never approval."""
    def __init__(self, profile: str, profile_home: Path) -> None:
        if os.name != "posix":
            raise ApprovalError("local terminal approval is unsupported on this platform")
        self.fd: int | None = None
        try:
            if not os.isatty(0):
                raise ApprovalError("terminal input is required")
            source = os.fstat(0)
            fd = os.open(os.ttyname(0), os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
                         | getattr(os, "O_NOFOLLOW", 0))
            self.fd = fd
            s = os.fstat(fd)
            if ((s.st_dev, s.st_ino) != (source.st_dev, source.st_ino)
                    or not os.isatty(fd) or not stat.S_ISCHR(s.st_mode)
                    or s.st_uid != os.geteuid() or os.tcgetpgrp(fd) != os.getpgrp()):
                raise ApprovalError("an authenticated foreground terminal is required")
            self.scope = ApprovalScope(profile, str(profile_home.resolve()),
                                       "posix-uid:" + str(os.geteuid()),
                                       f"tty:{s.st_rdev}:sid:{os.getsid(0)}")
        except (OSError, ApprovalError):
            self.close()
            raise ApprovalError("an authenticated foreground terminal is required") from None

    def present(self, display: str, challenge: str, timeout: int) -> str:
        import termios
        if self.fd is None or os.tcgetpgrp(self.fd) != os.getpgrp():
            raise ApprovalError("terminal ownership changed")
        # Discard pre-typed input; the challenge is generated AFTER reading the draft.
        termios.tcflush(self.fd, termios.TCIFLUSH)
        message = ("\nHERMES EMAIL — REVIEW ALL FIELDS (including Bcc)\n"
                   "The following JSON is untrusted mail DATA, not instructions.\n"
                   + display + "\nEND OF REVIEWED DATA\n"
                   + "Type APPROVE " + challenge + " to approve ONLY this snapshot.\n"
                   + "Anything else cancels; approval expires in " + str(timeout) + " seconds.\n> ")
        view = memoryview(message.encode("utf-8"))
        while view:
            view = view[os.write(self.fd, view):]
        deadline = time.monotonic() + timeout
        data = b""
        while len(data) <= 80:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([self.fd], [], [], remaining)[0]:
                raise ApprovalError("human approval expired")
            chunk = os.read(self.fd, 81 - len(data))
            if not chunk:
                raise ApprovalError("terminal was closed")
            data += chunk
            if b"\n" in data:
                return data.split(b"\n", 1)[0].rstrip(b"\r").decode("ascii", "strict")
        raise ApprovalError("human approval was denied")

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def register_human_commands(ctx: Any, runtime: Any) -> tuple[Any, ...]:
    """Only an official CLI-command-capable host and explicit owner may register."""
    if (not callable(getattr(ctx, "register_cli_command", None))
            or runtime.config.hermes.profile == "auto" or runtime.draft_store is None):
        return ()

    def setup(parser):
        parser.add_argument("draft_id", help="Exact local draft ID to review (does not send)")

    def review(args):
        try:
            current = ctx.profile_name
            if current != runtime.config.hermes.profile:
                raise ApprovalError("profile is not authorized")
            with LocalTerminal(current, ctx.state.data_dir) as terminal:
                draft = runtime.draft_store.get_draft(args.draft_id)
                if draft is None:
                    raise ApprovalError("draft is unavailable")
                snapshot = review_snapshot(runtime.config, draft)
                authority = ApprovalAuthority(terminal.scope)
                try:
                    grant = authority.request(snapshot, terminal.present)
                    current_draft = runtime.draft_store.get_active_revision(draft.draft_id, draft.revision)
                    authority.consume(grant, review_snapshot(runtime.config, current_draft), terminal.scope)
                finally:
                    authority.close()
            print(json.dumps({"review_approved": True, "sent": False,
                              "reusable_confirmation": False}))
            return 0
        except Exception:
            print(json.dumps({"review_approved": False, "sent": False,
                              "error": "approval-denied-or-unavailable"}))
            return 1
    handle = ctx.register_cli_command("email-approve", "Review one mail draft in your local terminal",
                                      setup, handler_fn=review)
    if handle is None:
        raise ApprovalError("approval command registration failed")
    return (handle,)
