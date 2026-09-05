from hermes_email.models import EmailAddress, EmailMessage
from hermes_email.replying import derive_reply_route


def message(*, reply_to=()):
    return EmailMessage(
        message_id="provider-1",
        subject="Example",
        sender=EmailAddress("sender@example.invalid", "Sender"),
        recipients=(EmailAddress("inbox@example.invalid"),),
        reply_to=reply_to,
    )


def test_no_reply_to_falls_back_to_from_sender() -> None:
    route = derive_reply_route(message())
    assert route.source == "from"
    assert route.selected.address == "sender@example.invalid"
    assert route.candidates == (EmailAddress("sender@example.invalid", "Sender"),)
    assert route.ambiguous is False
    assert route.valid is True
    assert route.truncated is False
    assert route.valid is True
    assert route.truncated is False


def test_single_reply_to_takes_precedence_over_from() -> None:
    reply = EmailAddress("reply@example.invalid", "Replies")
    route = derive_reply_route(message(reply_to=(reply,)))
    assert route.source == "reply-to"
    assert route.selected == reply
    assert route.candidates == (reply,)
    assert route.ambiguous is False


def test_multiple_reply_to_addresses_are_never_auto_selected() -> None:
    first = EmailAddress("one@example.invalid")
    second = EmailAddress("two@example.invalid")
    route = derive_reply_route(message(reply_to=(first, second)))
    assert route.source == "reply-to"
    assert route.selected is None
    assert route.candidates == (first, second)
    assert route.ambiguous is True
    assert route.valid is True
    assert route.truncated is False


def test_invalid_reply_to_does_not_silently_fall_back_to_from() -> None:
    route = derive_reply_route(message(reply_to=(EmailAddress("not-an-address"),)))
    assert route.source == "reply-to"
    assert route.selected is None
    assert route.ambiguous is True
    assert route.valid is False


def test_too_many_reply_to_candidates_are_not_auto_selected() -> None:
    values = tuple(EmailAddress(f"r{i}@example.invalid") for i in range(11))
    route = derive_reply_route(message(reply_to=values))
    assert route.selected is None
    assert route.ambiguous is True
    assert route.truncated is True
    assert len(route.candidates) == 10
