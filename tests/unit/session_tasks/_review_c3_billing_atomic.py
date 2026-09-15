"""C3 independent billing recovery and control-race checks. Run explicitly."""
import json
import uuid

import pytest

from tests.unit.session_tasks.test_c3_gate import gate_env
from tests.unit.session_tasks.test_c3_decisions import _publish_and_claim, _submit_reply, _task_row
from src.session_tasks import decisions as d, service
from src.db.database import get_db_connection


@pytest.fixture(autouse=True)
def clean_probe_ledger(tenant_id):
    yield
    with get_db_connection() as conn:
        c = conn.cursor()
        for table in ('chat_records', 'session_task_decision_attempts'):
            c.execute(f'DELETE FROM {table} WHERE tenant_id=%s', (tenant_id,))
        conn.commit()


def setup_decision(tenant_id, device_row, binding):
    _, task = _publish_and_claim(tenant_id, device_row, binding)
    _, decision = _submit_reply(tenant_id, device_row, task, binding, 1, [
        {'local_message_id': 'm1', 'sender': 'peer', 'text': 'hello', 'source_evidence_ref': 'e'},
    ])
    return task, decision


def response():
    return {'content': json.dumps({'action': 'reply', 'reply_text': 'hello'}),
            'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2},
            'model': 'review-original-model'}


@pytest.mark.parametrize('action,expected', [('pause', 'paused'), ('stop', 'stopped')])
def test_late_handoff_preserves_user_control(tenant_id, device_row, verified_binding, action, expected):
    task, _ = setup_decision(tenant_id, device_row, verified_binding)
    def model(*args):
        version = _task_row(tenant_id, task['task_id'])['version']
        service.control_task(tenant_id, 'user-1', uuid.UUID(task['task_id']), action, version, reason_code='review-control')
        return {'content': json.dumps({'action': 'handoff', 'reason_code': 'late'}), 'usage': {}}
    d.run_decision_tick(model_call=model)
    assert _task_row(tenant_id, task['task_id'])['status'] == expected


def test_billed_but_unsettled_is_recovered(tenant_id, device_row, verified_binding, monkeypatch):
    task, decision = setup_decision(tenant_id, device_row, verified_binding)
    def fail(*args, **kwargs):
        raise d.SessionTaskError('review settlement failure', 'CONFLICT', 409)
    with monkeypatch.context() as patch:
        patch.setattr(service, 'settle_cost', fail)
        d.run_decision_tick(model_call=lambda *args: response())
    d._recover_pending_billing()
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT state FROM session_task_cost_reservations WHERE tenant_id=%s AND task_id=%s AND ref_key=%s", (tenant_id, task['task_id'], decision['decision_id']))
        state = c.fetchone()['state']
    assert state == 'settled', state


def test_crash_after_billing_does_not_charge_twice(tenant_id, device_row, verified_binding, monkeypatch):
    import src.services.session_record as record_module
    # Both actual ChatRecordDB writes use a fixed test price; no real model/provider.
    monkeypatch.setattr(record_module, 'calculate_credit_cost_with_breakdown', lambda **kwargs: (0.25, {}))
    task, decision = setup_decision(tenant_id, device_row, verified_binding)
    def crash(*args, **kwargs):
        raise RuntimeError('review crash after committed billing and before settlement')
    with monkeypatch.context() as patch:
        patch.setattr(d, '_settle_attempt', crash)
        d.run_decision_tick(model_call=lambda *args: response())
    d._recover_pending_billing()
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute('SELECT COUNT(*) AS n, SUM(credit_cost) AS cost FROM chat_records WHERE tenant_id=%s', (tenant_id,))
        row = dict(c.fetchone())
    assert row['n'] == 1, row


def test_returned_attempt_preserves_original_model(tenant_id, device_row, verified_binding, monkeypatch):
    from src.db.models import ChatRecordDB
    task, decision = setup_decision(tenant_id, device_row, verified_binding)
    def fail(**kwargs):
        raise RuntimeError('review billing failure')
    monkeypatch.setattr(ChatRecordDB, 'create', fail)
    d.run_decision_tick(model_call=lambda *args: response())
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute('SELECT model FROM session_task_decision_attempts WHERE tenant_id=%s AND attempt_ref=%s', (tenant_id, decision['decision_id']))
        model = c.fetchone()['model']
    assert model == response()['model']


def test_price_error_does_not_lose_returned_usage_or_slot(tenant_id, device_row, verified_binding, monkeypatch):
    import src.services.billing as billing
    task, decision = setup_decision(tenant_id, device_row, verified_binding)
    def fail(*args, **kwargs):
        raise RuntimeError('review price calculation failure')
    monkeypatch.setattr(billing, 'calculate_credit_cost_with_breakdown', fail)
    d.run_decision_tick(model_call=lambda *args: response())
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute('''SELECT d.model_call_pending, a.usage_json FROM session_task_decisions d
                     JOIN session_task_decision_attempts a ON a.tenant_id=d.tenant_id AND a.decision_id=d.id
                     WHERE d.tenant_id=%s AND d.id=%s''', (tenant_id, decision['decision_id']))
        row = dict(c.fetchone())
    assert row['model_call_pending'] is False and row['usage_json'], row


def test_review_rejects_mixed_real_and_fabricated_quotes():
    from src.weixin_conversation.prompts import validate_review_conclusion, OutputInvalid
    spec = {'completion_rule': {'criteria': ['criterion']}}
    review = {'agree': True, 'criterion_checks': [
        {'criterion': 'criterion', 'satisfied': True, 'note': '《hello》《invented evidence》'},
    ]}
    with pytest.raises(OutputInvalid):
        validate_review_conclusion(spec, review, {'m1': 'hello'})
