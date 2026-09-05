from datetime import UTC, datetime, timedelta

from hermes_email.models import EmailAddress, EmailMessage
from hermes_email.threading import build_thread_context, parse_message_ids


def msg(pid: str, rid: str | None, refs: str = '', minutes: int = 0, subject: str = 'Same') -> EmailMessage:
    metadata = {}
    if rid:
        metadata['rfc_message_id'] = rid
    if refs:
        metadata['references'] = refs
    return EmailMessage(
        message_id=pid,
        subject=subject,
        sender=EmailAddress(f'{pid}@example.invalid'),
        recipients=(EmailAddress('inbox@example.invalid'),),
        body_text=f'body {pid}',
        received_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=minutes),
        metadata=metadata,
    )


def test_parse_message_ids_is_bounded_and_exact() -> None:
    assert parse_message_ids('noise <a@example.invalid> <b@example.invalid>') == (
        '<a@example.invalid>', '<b@example.invalid>'
    )
    assert parse_message_ids('no angle ids here') == ()


def test_thread_uses_rfc_links_not_subject_similarity() -> None:
    root = msg('root', '<root@example.invalid>', minutes=0, subject='Invoice')
    reply = msg('reply', '<reply@example.invalid>', '<root@example.invalid>', 1, 'Re: Invoice')
    unrelated = msg('other', '<other@example.invalid>', minutes=2, subject='Re: Invoice')

    thread = build_thread_context(root, (root, reply, unrelated), scan_complete=True)

    assert [m.message_id for m in thread.messages] == ['root', 'reply']
    assert thread.scan_complete is True
    assert thread.truncated is False


def test_thread_links_sibling_replies_through_shared_reference() -> None:
    root = msg('root', '<root@example.invalid>')
    a = msg('a', '<a@example.invalid>', '<root@example.invalid>', 1)
    b = msg('b', '<b@example.invalid>', '<root@example.invalid>', 2)

    thread = build_thread_context(a, (root, a, b), scan_complete=True)

    assert [m.message_id for m in thread.messages] == ['root', 'a', 'b']


def test_missing_rfc_headers_never_fall_back_to_subject() -> None:
    seed = msg('seed', None, subject='Identical subject')
    other = msg('other', None, minutes=1, subject='Identical subject')

    thread = build_thread_context(seed, (seed, other), scan_complete=False)

    assert [m.message_id for m in thread.messages] == ['seed']
    assert thread.scan_complete is False


def test_thread_reports_unresolved_references_and_truncation() -> None:
    root = msg('root', '<root@example.invalid>', '<missing@example.invalid>')
    replies = tuple(
        msg(f'r{i}', f'<r{i}@example.invalid>', '<root@example.invalid>', i + 1)
        for i in range(5)
    )

    thread = build_thread_context(root, (root, *replies), scan_complete=False, max_messages=3)

    assert len(thread.messages) == 3
    assert any(m.message_id == 'root' for m in thread.messages)
    assert thread.truncated is True
    assert thread.unresolved_reference_count >= 1
