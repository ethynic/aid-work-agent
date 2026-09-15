"""Independent checks for the new direct-SQL billing implementation."""
import json
import threading
from decimal import Decimal

import pytest

from tests.unit.session_tasks._review_c3_billing_atomic import (
    gate_env, clean_probe_ledger, setup_decision, response,
)
from src.session_tasks import decisions as d
from src.db.database import get_db_connection


def test_billing_failure_does_not_discard_model_result(tenant_id, device_row, verified_binding, monkeypatch):
    task, decision = setup_decision(tenant_id, device_row, verified_binding)
    calls = []
    def model(*args):
        calls.append(1)
        return response()
    original = d._bill_attempt_once
    def fail(t, attempt, cost):
        if t == tenant_id:
            raise RuntimeError('review direct billing write failed')
        return original(t, attempt, cost)
    with monkeypatch.context() as patch:
        patch.setattr(d, '_bill_attempt_once', fail)
        d.run_decision_tick(model_call=model)
    d.run_decision_tick(model_call=model)
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute('SELECT status, reply_text_id FROM session_task_decisions WHERE tenant_id=%s AND id=%s', (tenant_id, decision['decision_id']))
        row = dict(c.fetchone())
    assert len(calls) == 1
    assert row['status'] == 'ready' and row['reply_text_id'], row


def test_one_failed_billing_does_not_block_other_recoveries(tenant_id, device_row, verified_binding, monkeypatch):
    task, decision = setup_decision(tenant_id, device_row, verified_binding)
    with monkeypatch.context() as patch:
        patch.setattr(d, '_price_and_bill_attempt', lambda *args: None)
        d.run_decision_tick(model_call=lambda *args: response())
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("INSERT INTO session_task_decision_attempts (tenant_id,task_id,decision_id,attempt_ref,state,usage_json,credit_cost) VALUES (%s,%s,%s,%s,'returned','{}',0.01)", (tenant_id, task['task_id'], decision['decision_id'], decision['decision_id'] + ':second'))
        conn.commit()
    visited = []
    original = d._price_and_bill_attempt
    def recover(t, task_id, ref):
        if t != tenant_id:
            return original(t, task_id, ref)
        visited.append(ref)
        if len(visited) == 1:
            raise RuntimeError('review one persistent billing failure')
    monkeypatch.setattr(d, '_price_and_bill_attempt', recover)
    try:
        d._recover_pending_billing()
    except RuntimeError:
        pass
    assert len(visited) == 2, visited


def test_concurrent_pricing_freezes_one_amount(tenant_id, device_row, verified_binding, monkeypatch):
    import src.services.billing as billing
    task, decision = setup_decision(tenant_id, device_row, verified_binding)
    with monkeypatch.context() as patch:
        patch.setattr(d, '_price_and_bill_attempt', lambda *args: None)
        d.run_decision_tick(model_call=lambda *args: response())
    # Both workers have read an unpriced attempt. Worker B observed a newer tariff.
    first_finished = threading.Event()
    both_pricing = threading.Barrier(2)
    def price(*args, **kwargs):
        both_pricing.wait(15)
        if threading.current_thread().name == 'review-price-b':
            assert first_finished.wait(15)
            return Decimal('0.02'), {}
        return Decimal('0.01'), {}
    monkeypatch.setattr(billing, 'calculate_credit_cost_with_breakdown', price)
    errors = []
    def run():
        try:
            d._price_and_bill_attempt(tenant_id, task['task_id'], decision['decision_id'])
        except Exception as exc:
            errors.append(type(exc).__name__)
        finally:
            if threading.current_thread().name == 'review-price-a':
                first_finished.set()
    a = threading.Thread(target=run, name='review-price-a')
    b = threading.Thread(target=run, name='review-price-b')
    a.start(); b.start()
    a.join(20); b.join(20)
    assert not a.is_alive() and not b.is_alive() and not errors, errors
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute('''SELECT a.credit_cost, r.settled_amount, cr.credit_cost AS billed
                     FROM session_task_decision_attempts a
                     JOIN session_task_cost_reservations r ON r.tenant_id=a.tenant_id AND r.ref_key=a.attempt_ref
                     JOIN chat_records cr ON cr.tenant_id=a.tenant_id AND cr.billing_ref=a.billing_key
                     WHERE a.tenant_id=%s AND a.attempt_ref=%s''', (tenant_id, decision['decision_id']))
        row = dict(c.fetchone())
    assert row['credit_cost'] == row['settled_amount'] == row['billed'], row


def test_missing_reservation_does_not_mark_failed_pricing_settled(tenant_id, device_row, verified_binding, monkeypatch):
    import src.services.billing as billing
    task, decision = setup_decision(tenant_id, device_row, verified_binding)
    with monkeypatch.context() as patch:
        patch.setattr(d, '_price_and_bill_attempt', lambda *args: None)
        d.run_decision_tick(model_call=lambda *args: response())
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute('DELETE FROM session_task_cost_reservations WHERE tenant_id=%s AND task_id=%s', (tenant_id, task['task_id']))
        conn.commit()
    def fail(*args, **kwargs):
        raise RuntimeError('review pricing failure during missing-reservation recovery')
    monkeypatch.setattr(billing, 'calculate_credit_cost_with_breakdown', fail)
    d._recover_pending_billing()
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute('SELECT state FROM session_task_decision_attempts WHERE tenant_id=%s AND attempt_ref=%s', (tenant_id, decision['decision_id']))
        state = c.fetchone()['state']
    assert state != 'settled', state
