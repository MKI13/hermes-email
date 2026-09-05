"""Private local send-ledger files and kernel-backed locks.

Locks are advisory and require a local filesystem. This is not isolation from
malicious code with the same OS identity. Never unlink a lock or identity file.
"""
from __future__ import annotations

import errno
import os
import stat
import threading
import weakref
from pathlib import Path


class SendOrchestrationError(RuntimeError):
    """Fixed, non-sensitive send persistence failure."""


class SendStorageError(SendOrchestrationError):
    """Persistence could not be proved safe; submission must not start."""


class SendBusyError(SendOrchestrationError):
    """Another owner holds a required local lock."""


_HANDLES: weakref.WeakSet = weakref.WeakSet()
_HANDLES_LOCK = threading.RLock()


def _after_fork() -> None:
    global _HANDLES_LOCK
    # Close inherited descriptors without LOCK_UN (the parent still owns them).
    for handle in list(_HANDLES):
        handle.close_inherited()
    _HANDLES_LOCK = threading.RLock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork)


def private_status(status: os.stat_result, *, directory: bool = False) -> None:
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(status.st_mode) or (not directory and status.st_nlink != 1):
        raise SendStorageError("send storage path is unsafe")
    if os.name == "posix":
        needed = 0o700 if directory else 0o600
        mode = stat.S_IMODE(status.st_mode)
        if status.st_uid != os.getuid() or mode & 0o077 or mode & needed != needed:
            raise SendStorageError("send storage permissions are unsafe")


def private_parent(path: Path) -> tuple[int, int]:
    for parent in reversed(path.parents):
        try:
            status = parent.lstat()
        except FileNotFoundError:
            try:
                parent.mkdir(mode=0o700)
            except FileExistsError:
                pass
            status = parent.lstat()
        if not stat.S_ISDIR(status.st_mode):
            raise SendStorageError("send storage directory is unsafe")
        if parent == path.parent:
            private_status(status, directory=True)
    status = path.parent.lstat()
    return status.st_dev, status.st_ino


def sync_directory(path: Path) -> None:
    if os.name == "posix":
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def open_private(path: Path, *, create: bool) -> int:
    """Open without following links or blocking on a FIFO; no silent chmod."""
    private_parent(path)
    flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
    created = False
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        if not create:
            raise SendStorageError("send storage file is missing") from None
        try:
            fd = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600)
            created = True
        except FileExistsError:
            fd = os.open(path, flags)
    try:
        private_status(os.fstat(fd))
        verify_descriptor(path, fd)
        if created:
            os.fsync(fd)
            sync_directory(path.parent)
        return fd
    except BaseException:
        os.close(fd)
        raise


def verify_descriptor(path: Path, fd: int) -> None:
    observed = path.lstat()
    held = os.fstat(fd)
    private_status(observed)
    private_status(held)
    if (observed.st_dev, observed.st_ino) != (held.st_dev, held.st_ino):
        raise SendStorageError("send storage file identity changed")


class FileLease:
    """An independently opened, non-inheritable exclusive OS file lock."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.fd: int | None = None
        self.pid = os.getpid()
        self.parent_identity: tuple[int, int] | None = None

    def acquire(self, *, create: bool = True) -> bool:
        if self.fd is not None:
            raise SendStorageError("send lock is already held")
        if os.name not in {"posix", "nt"}:
            raise SendStorageError("send file locking is unsupported")
        with _HANDLES_LOCK:
            fd = open_private(self.path, create=create)
            try:
                if os.name == "posix":
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                else:
                    import msvcrt
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                verify_descriptor(self.path, fd)
                self.fd = fd
                self.pid = os.getpid()
                self.parent_identity = private_parent(self.path)
                _HANDLES.add(self)
                return True
            except OSError as error:
                os.close(fd)
                if error.errno in {errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK}:
                    return False
                raise
            except BaseException:
                os.close(fd)
                raise

    def verify(self) -> None:
        if self.fd is None or self.pid != os.getpid():
            raise SendStorageError("send lock ownership is unavailable")
        verify_descriptor(self.path, self.fd)
        if private_parent(self.path) != self.parent_identity:
            raise SendStorageError("send directory identity changed")

    def close_inherited(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def close(self) -> None:
        with _HANDLES_LOCK:
            # close releases only this open-file description; no premature unlock
            # of a lock still held by another process after fork.
            self.close_inherited()
            _HANDLES.discard(self)
