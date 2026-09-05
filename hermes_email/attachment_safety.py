"""Deterministic attachment handling classification from untrusted metadata only."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath

from .models import EmailAttachment


class AttachmentHandlingClass(StrEnum):
    DOCUMENT = "document"
    IMAGE = "image"
    ARCHIVE = "archive"
    ACTIVE_CONTENT = "active-content"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class AttachmentSafetyAssessment:
    handling_class: AttachmentHandlingClass
    potentially_active: bool
    automatic_open_allowed: bool = False
    automatic_execute_allowed: bool = False
    content_access_allowed: bool = False
    authorization: str = "none"


_ACTIVE_EXTENSIONS = {
    ".exe", ".msi", ".bat", ".cmd", ".com", ".scr", ".ps1", ".sh",
    ".js", ".vbs", ".jar", ".apk", ".appimage", ".deb", ".rpm",
    ".docm", ".xlsm", ".pptm",
}
_ARCHIVE_EXTENSIONS = {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"}
_DOCUMENT_EXTENSIONS = {".pdf", ".txt", ".csv", ".docx", ".xlsx", ".pptx", ".odt", ".ods"}
_ACTIVE_MIME_PREFIXES = ("application/x-msdownload", "application/x-executable", "application/x-sh")


def assess_attachment(attachment: EmailAttachment) -> AttachmentSafetyAssessment:
    """Classify metadata conservatively without opening or trusting the attachment."""
    filename = attachment.filename or ""
    suffix = PurePosixPath(filename.casefold()).suffix
    content_type = (attachment.content_type or "").casefold()
    if suffix in _ACTIVE_EXTENSIONS or any(content_type.startswith(v) for v in _ACTIVE_MIME_PREFIXES):
        kind = AttachmentHandlingClass.ACTIVE_CONTENT
    elif suffix in _ARCHIVE_EXTENSIONS or content_type in {
        "application/zip", "application/x-7z-compressed", "application/x-rar-compressed",
        "application/gzip", "application/x-tar",
    }:
        kind = AttachmentHandlingClass.ARCHIVE
    elif content_type.startswith("image/"):
        kind = AttachmentHandlingClass.IMAGE
    elif suffix in _DOCUMENT_EXTENSIONS or content_type in {
        "application/pdf", "text/plain", "text/csv",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }:
        kind = AttachmentHandlingClass.DOCUMENT
    else:
        kind = AttachmentHandlingClass.UNKNOWN
    return AttachmentSafetyAssessment(
        handling_class=kind,
        potentially_active=kind is AttachmentHandlingClass.ACTIVE_CONTENT,
    )
