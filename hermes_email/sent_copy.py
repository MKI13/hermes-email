"""Optional private sent-message copies, separate from mandatory send evidence."""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
from .send_files import FileLease, open_private, private_parent, sync_directory, verify_descriptor


def save_sent_copy(data_dir: Path, operation_id: str, message: bytes) -> str:
    """Never resubmit mail to repair a copy. Return a warning on ANY copy failure."""
    directory = data_dir / "sent-copies"
    name = hashlib.sha256(operation_id.encode("ascii")).hexdigest() + ".eml"
    path = directory / name
    lease = FileLease(directory / "copy.guard")
    fd = None
    try:
        if not isinstance(message, bytes) or not 1 <= len(message) <= 10_000_000:
            return "local-copy-failed"
        if not lease.acquire():
            return "local-copy-failed"
        if len(list(directory.glob("*.eml"))) >= 10000 and not path.exists():
            return "local-copy-failed"
        existed = path.exists() or path.is_symlink()
        fd = open_private(path, create=True)
        verify_descriptor(path, fd)
        if existed:
            if os.fstat(fd).st_size != len(message) or os.read(fd, len(message)+1) != message:
                return "local-copy-failed"
        else:
            view = memoryview(message)
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd)
            sync_directory(directory)
        verify_descriptor(path, fd); lease.verify()
        return "saved-local"
    except Exception:
        return "local-copy-failed"
    finally:
        if fd is not None:
            os.close(fd)
        lease.close()
