"""Prepared native PG billing/event contract; receipt facts are explicit fixtures.

Uses the original receipt, Finalizer, history/debit DAL and transaction. No
provider is dispatched, so this does not prove physical Gateway observation.
"""
from decimal import Decimal

import pytest

from src.core.agent_engine.contracts import CheckpointFailure
from src.services.agent_runner.finalizer import RunnerFinalizer

from .test_runner_event_ledger import page
from .test_runner_event_transactions import reject_event_insert
from .test_storage import attempt, storage
from .test_usage_storage import ledger, prices, started

pytestmark = pytest.mark.integration


def test_real_terminal_event_fault_rolls_back_fee_history_claim_then_late_usage_only_settles_original_record(
        ledger, actors, service_database):
    repository, execution, usage, owned, _, _ = ledger
    authority = attempt(owned)
    identifier = owned['runner_id']
    first = started(ledger, call='event-first-receipt')
    usage.observe(authority, first['receipt_id'], {'prompt_tokens': 1000, 'completion_tokens': 0})
    late = started(ledger, call='event-late-receipt')
    # Both exact receipts use the existing real price fixture, usage factor100:
    # 1000*1/1e6*100 = .10 each; same main billing boundary totals .20.
    assert first['price_snapshot'] == late['price_snapshot']
    staged = execution.stage_finalization(authority, owned['revision'], {},
        {'status': 'completed', 'output': 'Event terminal fixture', 'images': [], 'messages': []}, {})
    finalizer = RunnerFinalizer(service_database.connect)
    balance = service_database.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s',
        (actors['a'].tenant_id,))[0]['credit_balance']
    claims = service_database.rows('SELECT * FROM agent_runner_session_claims WHERE owner_runner_id=%s', (identifier,))
    receipts = service_database.rows('SELECT * FROM agent_runner_usage_receipts WHERE runner_id=%s ORDER BY receipt_id', (identifier,))
    with reject_event_insert(service_database):
        with pytest.raises(CheckpointFailure, match='^RUNNER_PUBLIC_EVENT_COMMIT_FAILED$'):
            finalizer.finalize(authority)
        assert repository.get(identifier) == staged
        assert service_database.rows('SELECT 1 FROM chat_records WHERE record_id=%s', (owned['record_id'],)) == []
        assert service_database.rows('SELECT 1 FROM chat_messages WHERE session_id=%s', (actors['a'].session_id,)) == []
        assert service_database.rows('SELECT * FROM agent_runner_session_claims WHERE owner_runner_id=%s', (identifier,)) == claims
        assert service_database.rows('SELECT * FROM agent_runner_usage_receipts WHERE runner_id=%s ORDER BY receipt_id', (identifier,)) == receipts
        assert service_database.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s',
            (actors['a'].tenant_id,))[0]['credit_balance'] == balance
    terminal = finalizer.finalize(authority)
    assert terminal['status'] == 'completed' and terminal['settlement_status'] == 'pending'
    terminal_events = page(service_database, identifier)
    assert sum(event['kind'] == 'terminal' for event in terminal_events['events']) == 1
    assert terminal_events['events'][-1]['kind'] == 'terminal'
    history = service_database.rows('SELECT * FROM chat_messages WHERE session_id=%s ORDER BY message_id', (actors['a'].session_id,))
    assert len(history) == 2
    record = service_database.rows('SELECT record_id,credit_cost FROM chat_records WHERE record_id=%s', (owned['record_id'],))
    assert record == [{'record_id': owned['record_id'], 'credit_cost': Decimal('.10')}]
    assert service_database.rows('SELECT 1 FROM agent_runner_session_claims WHERE owner_runner_id=%s', (identifier,)) == []
    with pytest.raises(CheckpointFailure):
        finalizer.finalize(authority)
    # A real late observation may update only original settlement, never reopen
    # execution or recreate terminal/history/claim. Original receipt owner reads
    # after terminal use its immutable recorded authorization.
    usage.observe(authority, late['receipt_id'], {'prompt_tokens': 1000, 'completion_tokens': 0})
    settled = finalizer.settle_pending_usage(identifier)
    assert settled['status'] == 'completed' and settled['settlement_status'] == 'settled'
    for key in ('attempt', 'revision', 'checkpoint', 'result', 'worker_id', 'lease_until', 'finished_at'):
        assert settled[key] == terminal[key]
    assert page(service_database, identifier)['events'][-1]['kind'] == 'settlement_changed'
    assert sum(event['kind'] == 'terminal' for event in page(service_database, identifier)['events']) == 1
    assert service_database.rows('SELECT * FROM chat_messages WHERE session_id=%s ORDER BY message_id', (actors['a'].session_id,)) == history
    assert service_database.rows('SELECT record_id,credit_cost FROM chat_records WHERE record_id=%s', (owned['record_id'],)) == [
        {'record_id': owned['record_id'], 'credit_cost': Decimal('.20')}]
    assert service_database.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s',
        (actors['a'].tenant_id,))[0]['credit_balance'] == balance - Decimal('.20')
    head = settled['event_seq']
    again = finalizer.settle_pending_usage(identifier)
    assert again['event_seq'] == head
    assert service_database.rows('SELECT COUNT(*) AS count FROM chat_records WHERE record_id=%s', (owned['record_id'],)) == [{'count': 1}]
