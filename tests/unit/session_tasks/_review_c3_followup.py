"""Independent C3 follow-up probes; run explicitly against authorized agent2 DB."""
import json

from tests.unit.session_tasks.test_c3_gate import gate_env
from tests.unit.session_tasks.test_c3_decisions import (
    _publish_and_claim, _submit_reply, _feed_batch, _task_row,
)
from src.session_tasks import decisions as d


def _message(mid, text):
    return {'local_message_id': mid, 'sender': 'peer', 'text': text, 'source_evidence_ref': 'e'}


def test_finalize_then_new_input_must_not_apply_old_handoff(tenant_id, device_row, verified_binding, monkeypatch):
    """原子化后（finalize+迁移同事务）原两阶段窗口不复存在——验证两种可观测次序：
    ①新输入在原子操作**前**接纳（决策 superseded）→ 旧 handoff 整体无操作（任务 active）；
    ②新输入在原子操作**后**接纳 → handoff 已原子生效（human_required，非"迟到覆盖"），
      且新批次正常 supersede 上下文、不创建任何失效审核。"""
    # 次序①：finalize 事务提交前注入新输入（锁阻塞直至提交，等价"前"接纳）
    _, task = _publish_and_claim(tenant_id, device_row, verified_binding)
    _, decision = _submit_reply(tenant_id, device_row, task, verified_binding, 1, [_message('m1', 'first')])
    original = d._finalize_and_transition
    gate = {'fired': False}
    def finalize_atomic(*args, **kwargs):
        if not gate['fired'] and str(args[2]) == decision['decision_id']:
            gate['fired'] = True
            # 在原子事务开锁前接纳新输入（同租约链）：决策 superseded → 原子条件败者
            _feed_batch(tenant_id, device_row, task, verified_binding, 2, [_message('m2', 'new input')])
        return original(*args, **kwargs)
    monkeypatch.setattr(d, '_finalize_and_transition', finalize_atomic)
    d.run_decision_tick(model_call=lambda *args: {'content': json.dumps({'action': 'handoff', 'reason_code': 'old'}), 'usage': {}})
    assert _task_row(tenant_id, task['task_id'])['status'] == 'active'
    # 次序②：原子操作完成后接纳新输入 → handoff 已生效但无迟到覆盖/无失效审核
    # （次序①结束后任务仍 active 占用会话——先 stop 释放占用再发布第二任务）
    from src.session_tasks import service
    from src.db.database import get_db_connection
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT version FROM session_tasks WHERE tenant_id=%s AND id=%s", (tenant_id, task['task_id']))
        v = int(c.fetchone()['version'])
    service.control_task(tenant_id, 'user-1', __import__('uuid').UUID(task['task_id']), 'stop', v, reason_code='probe')
    _, task2 = _publish_and_claim(tenant_id, device_row, verified_binding, __import__('tests.unit.session_tasks.conftest', fromlist=['build_spec']).build_spec())
    _, decision2 = _submit_reply(tenant_id, device_row, task2, verified_binding, 1, [_message('n1', 'again')])
    def finalize_after(*args, **kwargs):
        result = original(*args, **kwargs)
        if result and str(args[2]) == decision2['decision_id']:
            _feed_batch(tenant_id, device_row, task2, verified_binding, 2, [_message('n2', 'later input')])
        return result
    monkeypatch.setattr(d, '_finalize_and_transition', finalize_after)
    d.run_decision_tick(model_call=lambda *args: {'content': json.dumps({'action': 'handoff', 'reason_code': 'old'}), 'usage': {}})
    from src.db.database import get_db_connection
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) AS n FROM session_task_decisions WHERE tenant_id=%s AND task_id=%s AND decision_kind='completion_review'", (tenant_id, task2['task_id']))
        reviews = int(c.fetchone()['n'])
    assert _task_row(tenant_id, task2['task_id'])['status'] == 'human_required'  # handoff 原子生效（非迟到覆盖）
    assert reviews == 0  # 不创建失效审核


def test_billing_failure_clears_returned_model_slot(tenant_id, device_row, verified_binding, monkeypatch):
    from src.db.database import get_db_connection
    from src.db.models import ChatRecordDB
    def fail(**kwargs):
        raise RuntimeError('review injected ChatRecordDB write failure')
    monkeypatch.setattr(ChatRecordDB, 'create', fail)
    _, task = _publish_and_claim(tenant_id, device_row, verified_binding)
    _, decision = _submit_reply(tenant_id, device_row, task, verified_binding, 1, [_message('m1', 'first')])
    d.run_decision_tick(model_call=lambda *args: {'content': json.dumps({'action': 'reply', 'reply_text': 'hello'}), 'usage': {'prompt_tokens': 1, 'completion_tokens': 1}, 'model': 'test'})
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute('SELECT model_call_pending, model_attempts FROM session_task_decisions WHERE tenant_id=%s AND id=%s', (tenant_id, decision['decision_id']))
        row = dict(c.fetchone())
    assert row == {'model_call_pending': False, 'model_attempts': 1}, row


def test_fabricated_judged_proposal_must_not_complete(tenant_id, device_row, verified_binding):
    from tests.unit.session_tasks.conftest import build_spec
    spec = build_spec('judged')
    criteria = spec['completion_rule']['criteria']
    _, task = _publish_and_claim(tenant_id, device_row, verified_binding, spec)
    _submit_reply(tenant_id, device_row, task, verified_binding, 1, [_message('m1', 'hello')])
    proposal = {'action': 'done', 'criterion_results': [{'criterion': c, 'message_ids': ['m1'], 'quotes': ['fabricated text absent from transcript'], 'reason': 'x'} for c in criteria]}
    review = {'agree': True, 'criterion_checks': [{'criterion': c, 'satisfied': True, 'note': '《hello》'} for c in criteria]}
    d.run_decision_tick(model_call=lambda *args: {'content': json.dumps(proposal), 'usage': {}})
    d.run_decision_tick(model_call=lambda *args: {'content': json.dumps(review), 'usage': {}})
    assert _task_row(tenant_id, task['task_id'])['status'] != 'completed'


def test_judged_review_can_use_accepted_previous_batch(tenant_id, device_row, verified_binding):
    from tests.unit.session_tasks.conftest import build_spec
    spec = build_spec('judged')
    criteria = spec['completion_rule']['criteria']
    _, task = _publish_and_claim(tenant_id, device_row, verified_binding, spec)
    _feed_batch(tenant_id, device_row, task, verified_binding, 1, [_message('m1', '同意沟通，时间周五16:00')])
    _submit_reply(tenant_id, device_row, task, verified_binding, 2, [_message('m2', '谢谢')])
    quotes = ['同意沟通', '周五16:00']
    proposal = {'action': 'done', 'criterion_results': [{'criterion': c, 'message_ids': ['m1'], 'quotes': [q], 'reason': '原文'} for c, q in zip(criteria, quotes)]}
    review = {'agree': True, 'criterion_checks': [{'criterion': c, 'satisfied': True, 'message_ids': ['m1'], 'note': f'《{q}》'} for c, q in zip(criteria, quotes)]}
    d.run_decision_tick(model_call=lambda *args: {'content': json.dumps(proposal), 'usage': {}})
    d.run_decision_tick(model_call=lambda *args: {'content': json.dumps(review), 'usage': {}})
    assert _task_row(tenant_id, task['task_id'])['status'] == 'completed'
