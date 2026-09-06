from dataclasses import replace
from datetime import timedelta
from email import policy
from email.parser import BytesParser

import pytest

from hermes_email.send_orchestration import IdempotentSendOrchestrator, SendOperationConflictError, SqliteSendIntentStore
from hermes_email.sending import prepare_send_candidate, SendGateMessageError
from test_sending import config, store, create, confirmation
from test_send_orchestration import candidate, FakeTransport, OPERATION_ID


def test_stable_preparation_survives_new_store_and_orchestrator(tmp_path):
    cfg=config(); drafts=store(tmp_path,cfg); draft_id=create(drafts)
    kw=dict(draft_id=draft_id,expected_revision=1,confirmation=confirmation(draft_id),send_operation_id=OPERATION_ID)
    first=prepare_send_candidate(cfg,drafts,**kw)
    other=store(tmp_path,cfg)
    second=prepare_send_candidate(cfg,other,**kw)
    assert first == second
    assert first.message_bytes == second.message_bytes
    mail=BytesParser(policy=policy.default).parsebytes(first.message_bytes)
    assert str(mail['Message-ID']).strip() == first.message_id
    assert mail['Date'].datetime == first.message_date
    ledger=SqliteSendIntentStore(tmp_path/'send-data'); transport=FakeTransport()
    record=IdempotentSendOrchestrator(ledger,transport).send_once(OPERATION_ID,first)
    assert record.message_id == first.message_id
    assert record.message_date == first.message_date
    ledger.close()
    new_transport=FakeTransport()
    restarted=SqliteSendIntentStore(tmp_path/'send-data')
    replay=IdempotentSendOrchestrator(restarted,new_transport).send_once(OPERATION_ID,second)
    assert replay.replayed and replay.state=='accepted'
    assert replay.message_id==first.message_id and replay.message_date==first.message_date
    assert transport.calls==1 and new_transport.calls==0


@pytest.mark.parametrize('field', ['message_date','message_id','message_bytes','envelope_recipients','confirmation_id'])
def test_replay_rejects_changed_frozen_submission_fields(tmp_path,field):
    ledger=SqliteSendIntentStore(tmp_path/'data'); transport=FakeTransport(); sender=IdempotentSendOrchestrator(ledger,transport)
    original=candidate();sender.send_once(OPERATION_ID,original)
    changes={'message_date':original.message_date+timedelta(seconds=1),'message_id':'<different@example.invalid>',
             'message_bytes':original.message_bytes.replace(b'Body',b'Changed'),'envelope_recipients':('other@example.invalid',),
             'confirmation_id':'confirmation-0002'}
    with pytest.raises(SendOperationConflictError):
        sender.send_once(OPERATION_ID,replace(original,**{field:changes[field]}))
    assert transport.calls==1
    assert ledger.get(OPERATION_ID).message_id==original.message_id


def test_partial_explicit_message_identity_is_not_silently_generated(tmp_path):
    cfg=config();drafts=store(tmp_path,cfg);draft_id=create(drafts)
    with pytest.raises(SendGateMessageError):
        prepare_send_candidate(cfg,drafts,draft_id=draft_id,expected_revision=1,
                               message_id='<explicit@example.invalid>',confirmation=confirmation(draft_id))


def test_generated_identity_cannot_be_claimed_under_another_operation(tmp_path):
    from hermes_email.send_orchestration import InvalidSendOperationError
    cfg=config();drafts=store(tmp_path,cfg);draft_id=create(drafts)
    prepared=prepare_send_candidate(cfg,drafts,draft_id=draft_id,expected_revision=1,
                                    confirmation=confirmation(draft_id),send_operation_id=OPERATION_ID)
    transport=FakeTransport();ledger=SqliteSendIntentStore(tmp_path/'send-data')
    sender=IdempotentSendOrchestrator(ledger,transport)
    with pytest.raises(InvalidSendOperationError):
        sender.send_once('another-operation-0001',prepared)
    assert transport.calls==0
    assert ledger.get('another-operation-0001') is None
