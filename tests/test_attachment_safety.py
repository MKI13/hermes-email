from hermes_email.attachment_safety import AttachmentHandlingClass, assess_attachment
from hermes_email.models import EmailAttachment


def a(name, mime):
    return EmailAttachment("p", name, mime, 1, "attachment")


def test_deterministic_attachment_handling_classes_fail_closed():
    assert assess_attachment(a("invoice.pdf", "application/pdf")).handling_class is AttachmentHandlingClass.DOCUMENT
    assert assess_attachment(a("photo.png", "image/png")).handling_class is AttachmentHandlingClass.IMAGE
    assert assess_attachment(a("files.zip", "application/zip")).handling_class is AttachmentHandlingClass.ARCHIVE
    active = assess_attachment(a("macro.xlsm", "application/octet-stream"))
    assert active.handling_class is AttachmentHandlingClass.ACTIVE_CONTENT
    assert active.potentially_active is True
    unknown = assess_attachment(a("blob.bin", "application/octet-stream"))
    assert unknown.handling_class is AttachmentHandlingClass.UNKNOWN
    for result in (active, unknown):
        assert result.automatic_open_allowed is False
        assert result.automatic_execute_allowed is False
        assert result.content_access_allowed is False
        assert result.authorization == "none"
