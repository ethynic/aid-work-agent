"""Independent DB acceptance: assert real ledger effects and injected failures.

Uses only a random test tenant, fake model and no desktop Provider.
"""
import threading
import uuid
import json
from decimal import Decimal

import pytest

from tests.unit.session_tasks._review_c3_billing_atomic import (
    gate_env, response,
)
from tests.unit.session_tasks.test_c3_decisions import _task_row, _submit_reply, _publish_and_claim
from src.session_tasks import decisions as d, service
from src.db.database import get_db_connection


def setup_decision(tenant_id, device_row, binding):
    _, task = _publish_and_claim(tenant_id, device_row, binding)
    _, decision = _submit_reply(tenant_id, device_row, task, binding, 1, [
        {'local_message_id': 'm1', 'sender': 'peer', 'text': 'hello ' + tenant_id, 'source_evidence_ref': 'e'},
    ])
    return task, decision


def sql(statement, params=(), *, all_rows=False):
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute(statement, params)
        rows = c.fetchall() if c.description else []
        conn.commit()
        return [dict(row) for row in rows] if all_rows else (dict(rows[0]) if rows else None)


@pytest.fixture(autouse=True)
def real_tenant_ledger(tenant_id):
    sql('INSERT INTO tenants (tenant_id,company_name,credit_balance) VALUES (%s,%s,100)',
        (tenant_id, 'independent C3 acceptance'))
    yield
    for table in ('chat_records', 'session_task_decision_attempts', 'tenants'):
        sql(f'DELETE FROM {table} WHERE tenant_id=%s', (tenant_id,))


def ledger(tenant_id):
    return sql('''SELECT t.credit_balance,
        (SELECT COUNT(*) FROM chat_records c WHERE c.tenant_id=t.tenant_id) AS count,
        (SELECT SUM(credit_cost) FROM chat_records c WHERE c.tenant_id=t.tenant_id) AS charged
        FROM tenants t WHERE tenant_id=%s''', (tenant_id,))


def assert_one_charge(tenant_id):
    row = ledger(tenant_id)
    assert row['count'] == 1, row
    assert row['charged'] == Decimal('0.01'), row
    assert row['credit_balance'] == Decimal('99.99'), row


def due(tenant_id):
    sql('UPDATE session_task_decision_attempts SET billing_retry_at=NULL WHERE tenant_id=%s', (tenant_id,))


@pytest.mark.parametrize('action,expected', [('pause', 'paused'), ('stop', 'stopped')])
def test_billing_failure_control_then_recovery_keeps_control(
    tenant_id, device_row, verified_binding, monkeypatch, action, expected,
):
    task, decision = setup_decision(tenant_id, device_row, verified_binding)
    injected = []
    calls = []
    original = d._bill_attempt_once
    def fail(t, attempt, cost):
        if t != tenant_id:
            return original(t, attempt, cost)
        injected.append(attempt['attempt_ref'])
        if len(injected) == 1:
            version = _task_row(tenant_id, task['task_id'])['version']
            service.control_task(tenant_id, 'user-1', uuid.UUID(task['task_id']), action, version,
                                 reason_code='independent-test-control')
        raise RuntimeError('independent injected billing fault')
    def model(*args):
        if tenant_id in json.dumps(args, default=str):
            calls.append(1)
        return response()
    with monkeypatch.context() as patch:
        patch.setattr(d, '_bill_attempt_once', fail)
        d.run_decision_tick(model_call=model)
    assert injected, 'billing fault must actually trigger'
    due(tenant_id)
    d._recover_pending_billing()
    d.run_decision_tick(model_call=model)
    assert len(calls) == 1
    assert _task_row(tenant_id, task['task_id'])['status'] == expected
    row = sql('SELECT status,model_attempts,model_call_pending FROM session_task_decisions WHERE tenant_id=%s AND id=%s',
              (tenant_id, decision['decision_id']))
    assert row['model_attempts'] == 1 and row['model_call_pending'] is False, row
    with pytest.raises(d.SessionTaskError):
        d.prepare_send(tenant_id, uuid.UUID(str(device_row['id'])), uuid.UUID(task['assignment_id']),
                       task['fence'], uuid.UUID(decision['decision_id']))
    assert sql('SELECT COUNT(*) AS n FROM session_task_execution_links WHERE tenant_id=%s',
               (tenant_id,))['n'] == 0
    assert_one_charge(tenant_id)


def test_concurrent_recovery_deducts_real_balance_once(
    tenant_id, device_row, verified_binding, monkeypatch,
):
    task, decision = setup_decision(tenant_id, device_row, verified_binding)
    injected = []
    original = d._bill_attempt_once
    def fail(t, attempt, cost):
        if t != tenant_id:
            return original(t, attempt, cost)
        injected.append(1)
        raise RuntimeError('independent prepare pending ledger')
    with monkeypatch.context() as patch:
        patch.setattr(d, '_bill_attempt_once', fail)
        d.run_decision_tick(model_call=lambda *args: response())
    assert injected and ledger(tenant_id)['count'] == 0
    barrier = threading.Barrier(2)
    errors = []
    def recover():
        try:
            barrier.wait(15)
            d._price_and_bill_attempt(tenant_id, task['task_id'], decision['decision_id'])
        except BaseException as exc:
            errors.append(type(exc).__name__)
    threads = [threading.Thread(target=recover) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert not errors and all(not thread.is_alive() for thread in threads), errors
    assert_one_charge(tenant_id)
    row = sql('''SELECT a.credit_cost,r.settled_amount,r.state FROM session_task_decision_attempts a
        JOIN session_task_cost_reservations r ON r.tenant_id=a.tenant_id AND r.ref_key=a.attempt_ref
        WHERE a.tenant_id=%s AND a.attempt_ref=%s''', (tenant_id, decision['decision_id']))
    assert row['state'] == 'settled' and row['credit_cost'] == row['settled_amount'] == Decimal('0.01'), row


class SimulatedProcessExit(BaseException):
    pass


@pytest.mark.parametrize('mode', ['valid', 'repair', 'repair_second', 'pause', 'stop', 'new_input',
                                  'corrupt', 'missing', 'malformed'])
def test_durable_result_survives_process_exit_without_repeating_model(
    tenant_id, device_row, verified_binding, monkeypatch, mode,
):
    task, decision = setup_decision(tenant_id, device_row, verified_binding)
    calls, crash_refs = [], []
    def model(*args):
        if tenant_id in json.dumps(args, default=str):
            calls.append(1)
        result = response()
        if mode in ('repair', 'repair_second') and len(calls) == 1:
            result['content'] = 'invalid json requiring one repair'
        return result
    original = d._price_and_bill_attempt
    def crash(t, task_id, ref):
        if t != tenant_id:
            return original(t, task_id, ref)
        if mode == 'repair_second' and not ref.endswith(':r2'):
            return original(t, task_id, ref)
        crash_refs.append(ref)
        raise SimulatedProcessExit()
    with monkeypatch.context() as patch:
        patch.setattr(d, '_price_and_bill_attempt', crash)
        with pytest.raises(SimulatedProcessExit):
            d.run_decision_tick(model_call=model)
    before_crash_calls = 2 if mode == 'repair_second' else 1
    assert len(crash_refs) == 1 and len(calls) == before_crash_calls
    row = sql('''SELECT a.result_text_id,d.model_attempts,d.model_call_pending FROM session_task_decision_attempts a
        JOIN session_task_decisions d ON d.tenant_id=a.tenant_id AND d.id=a.decision_id
        WHERE a.tenant_id=%s AND a.attempt_ref=%s''', (tenant_id, decision['decision_id']))
    assert row['result_text_id'] and row['model_attempts'] == before_crash_calls and row['model_call_pending'] is False, row
    if mode == 'corrupt':
        sql('UPDATE session_task_texts SET encrypted_payload=%s WHERE tenant_id=%s AND id=%s',
            ('independent-not-valid-ciphertext', tenant_id, row['result_text_id']))
    elif mode == 'missing':
        sql('DELETE FROM session_task_texts WHERE tenant_id=%s AND id=%s', (tenant_id, row['result_text_id']))
    elif mode == 'malformed':
        with get_db_connection() as conn:
            bad_id = d.store_text(conn, tenant_id, task['task_id'], 'decision', {'content': []})
            conn.commit()
        sql('UPDATE session_task_decision_attempts SET result_text_id=%s WHERE tenant_id=%s AND attempt_ref=%s',
            (bad_id, tenant_id, decision['decision_id']))
    if mode in ('pause', 'stop'):
        service.control_task(tenant_id, 'user-1', uuid.UUID(task['task_id']), mode,
                             _task_row(tenant_id, task['task_id'])['version'], reason_code='crash-control')
    if mode == 'new_input':
        _submit_reply(tenant_id, device_row, task, verified_binding, 2, [
            {'local_message_id': 'm2', 'sender': 'peer', 'text': 'changed request', 'source_evidence_ref': 'e2'},
        ])
    sql("UPDATE session_task_decisions SET lease_expires_at=CURRENT_TIMESTAMP-INTERVAL '1 day' WHERE tenant_id=%s AND id=%s",
        (tenant_id, decision['decision_id']))
    d.run_decision_tick(model_call=model)
    row = sql('SELECT status,model_attempts,model_call_pending,reply_text_id FROM session_task_decisions WHERE tenant_id=%s AND id=%s',
              (tenant_id, decision['decision_id']))
    assert row['model_attempts'] == (2 if mode in ('repair', 'repair_second') else 1), row
    assert row['model_call_pending'] is False, row
    if mode in ('valid', 'repair', 'repair_second'):
        assert row['status'] == 'ready' and row['reply_text_id'], row
    else:
        assert row['status'] != 'ready' and not row['reply_text_id'], row
    if mode in ('pause', 'stop'):
        assert _task_row(tenant_id, task['task_id'])['status'] == {'pause': 'paused', 'stop': 'stopped'}[mode]
    if mode in ('corrupt', 'missing', 'malformed'):
        assert _task_row(tenant_id, task['task_id'])['status'] == 'blocked'
        assert row['status'] == 'failed', row
        assert sql('SELECT failure_code FROM session_task_decisions WHERE tenant_id=%s AND id=%s',
                   (tenant_id, decision['decision_id']))['failure_code'] == 'model_result_unreadable'
    expected_calls = 2 if mode in ('repair', 'repair_second', 'new_input') else 1
    assert len(calls) == expected_calls
    billed = ledger(tenant_id)
    assert billed['count'] == expected_calls, billed
    assert billed['credit_balance'] == Decimal('100') - expected_calls * Decimal('0.01'), billed


def test_failed_recovery_does_not_prevent_other_real_ledger_settlement(
    tenant_id, device_row, verified_binding, monkeypatch,
):
    task, decision = setup_decision(tenant_id, device_row, verified_binding)
    with monkeypatch.context() as patch:
        patch.setattr(d, '_price_and_bill_attempt', lambda *args: False)
        d.run_decision_tick(model_call=lambda *args: response())
    first, second = decision['decision_id'], decision['decision_id'] + ':independent-second'
    sql('''INSERT INTO session_task_decision_attempts
        (tenant_id,task_id,decision_id,attempt_ref,state,usage_json,model,user_id,billing_key)
        SELECT tenant_id,task_id,decision_id,%s,'returned',usage_json,model,user_id,%s
        FROM session_task_decision_attempts WHERE tenant_id=%s AND attempt_ref=%s''',
        (second, 'st-decision:' + second, tenant_id, first))
    sql('''INSERT INTO session_task_cost_reservations(tenant_id,task_id,purpose,ref_key,amount)
        SELECT tenant_id,task_id,purpose,%s,amount FROM session_task_cost_reservations
        WHERE tenant_id=%s AND ref_key=%s''', (second, tenant_id, first))
    due(tenant_id)
    failures = []
    original = d._bill_attempt_once
    def fail_first(t, attempt, cost):
        if t == tenant_id and attempt['attempt_ref'] == first:
            failures.append(1)
            raise RuntimeError('independent first row failure')
        return original(t, attempt, cost)
    with monkeypatch.context() as patch:
        patch.setattr(d, '_bill_attempt_once', fail_first)
        d._recover_pending_billing()
    assert len(failures) == 1
    assert_one_charge(tenant_id)
    row = sql('SELECT state FROM session_task_cost_reservations WHERE tenant_id=%s AND ref_key=%s', (tenant_id, second))
    assert row['state'] == 'settled', row
    due(tenant_id)
    d._recover_pending_billing()
    row = ledger(tenant_id)
    assert row['count'] == 2 and row['credit_balance'] == Decimal('99.98'), row


@pytest.mark.parametrize('failure', ['pricing', 'billing'])
def test_missing_reservation_remains_recoverable_until_restored(
    tenant_id, device_row, verified_binding, monkeypatch, failure,
):
    import src.services.billing as billing
    task, decision = setup_decision(tenant_id, device_row, verified_binding)
    with monkeypatch.context() as patch:
        patch.setattr(d, '_price_and_bill_attempt', lambda *args: False)
        d.run_decision_tick(model_call=lambda *args: response())
    ref = decision['decision_id']
    reservation = sql('DELETE FROM session_task_cost_reservations WHERE tenant_id=%s AND ref_key=%s RETURNING amount',
                      (tenant_id, ref))
    injected = []
    def fail(*args, **kwargs):
        injected.append(1)
        raise RuntimeError('independent missing reservation fault')
    due(tenant_id)
    with monkeypatch.context() as patch:
        if failure == 'pricing':
            patch.setattr(billing, 'calculate_credit_cost_with_breakdown', fail)
        else:
            patch.setattr(d, '_bill_attempt_once', fail)
        d._recover_pending_billing()
    assert injected
    assert ledger(tenant_id)['count'] == 0
    assert sql('SELECT state FROM session_task_decision_attempts WHERE tenant_id=%s AND attempt_ref=%s',
               (tenant_id, ref))['state'] != 'settled'
    due(tenant_id)
    d._recover_pending_billing()
    assert_one_charge(tenant_id)
    assert sql('SELECT state FROM session_task_decision_attempts WHERE tenant_id=%s AND attempt_ref=%s',
               (tenant_id, ref))['state'] != 'settled'
    sql('INSERT INTO session_task_cost_reservations(tenant_id,task_id,purpose,ref_key,amount) VALUES(%s,%s,%s,%s,%s)',
        (tenant_id, task['task_id'], 'decision', ref, reservation['amount']))
    due(tenant_id)
    d._recover_pending_billing()
    assert_one_charge(tenant_id)
    assert sql('SELECT state FROM session_task_cost_reservations WHERE tenant_id=%s AND ref_key=%s',
               (tenant_id, ref))['state'] == 'settled'
