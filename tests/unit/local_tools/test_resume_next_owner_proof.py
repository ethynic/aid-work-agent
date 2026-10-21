"""Real continuation/usage/recovery proofs; replace only persistence reads."""
import copy
import hashlib
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.core.agent_engine.contracts import AgentMode, CheckpointFailure, ExecutionState, Identity, ToolCall, ToolFact
from src.local_tools import repository
from src.services.agent_runner.contracts import RunnerError, canonical_json
from src.services.agent_runner.domain_usage import assert_physical_authorization
from src.services.agent_runner.local_invocations import LocalPhase
from src.services.agent_runner.local_recovery import LocalRecoveryProof
from src.services.agent_runner.ownership import Attempt

pytestmark = pytest.mark.unit


@pytest.fixture
def proof(monkeypatch):
    identity = Identity('tenant', 'user', 'session')
    state = ExecutionState(identity, 'runner', AgentMode.STANDALONE, '', [])
    parent_id, invocation_id = str(uuid4()), str(uuid4())
    call = ToolCall('call', 'boss_resume_next', {'continue_from': parent_id})
    state.tools[call.id] = ToolFact(call, phase='dispatching')
    row = {**vars(identity), 'runner_id': 'runner', 'attempt': 1}
    parent = dict(id=parent_id, tenant_id='tenant', user_id='user', session_id='session',
        device_id='device', tool_name='boss_resume_batch', state='succeeded', effect='none',
        arguments_json={'limit': 1}, result_json={'success': True, 'data': {
            'job_name': 'PHP', 'stop_reason': 'limit_reached', 'processed_names': ['previous'],
            'resumes': [{'candidate_name': 'previous'}], 'failures': []}})
    canonical = dict(continue_from=parent_id, limit=3, exclude_names=['previous'], expected_job_name='PHP')
    phase = LocalPhase('runner', 'call', 'main', 0)
    deadline = datetime.now(timezone.utc) + timedelta(minutes=10)
    owner = dict(runner_id='runner', execution_id='runner', tool_call_id='call', branch='main', ordinal=0)
    request = dict(arguments=canonical, tool_name='boss_resume_next', device_id='device', deadline_at=deadline.isoformat())
    ref = {**owner, 'request': request, 'invocation_id': invocation_id}
    state.resources['local_invocations'] = {phase.key('runner'): ref}
    image = 'fixture-base64-image'
    invocation = dict(id=invocation_id, tenant_id='tenant', user_id='user', session_id='session',
        device_id='device', tool_name='boss_resume_next', state='succeeded', effect='none',
        arguments_json=canonical, business_kind='agent_runner', business_ref=owner,
        dedupe_key=phase.key('runner'), deadline_at=deadline,
        result_json={'success': True, 'data': {'resumes': [{'candidate_name': 'next', 'images': [{'base64': image}]}]}})
    origin = dict(invocation_id=invocation_id, item_ordinal=0, candidate_name='next',
                  image_digest=hashlib.sha256(image.encode()).hexdigest())
    phases = state.resources['local_domain_phases'] = {}

    def save(branch, intent, result=None, status='completed'):
        phase = LocalPhase('runner', 'call', branch, 0)
        phases[phase.key('runner')] = dict(runner_id='runner', execution_id='runner', tool_call_id='call',
            branch=branch, ordinal=0, request=intent, phase=status, result=result,
            intent_digest=hashlib.sha256(canonical_json(intent).encode()).hexdigest(), authorized_attempt=1)
        return phases[phase.key('runner')]

    next_phase = save('resume.next.request', {**call.arguments, 'limit': 3},
                     {**canonical, '_resume_expected_device_id': 'device'})
    save('resume.policy', {**origin, 'resume_policy_version': 1}, {'model_spec': 'zhipu/fixture'})
    model_phase = save('resume.evaluate', {**origin, 'model_purpose': 'resume_recognition_covered',
                                          'model_phase_version': 1}, status='started')
    records = {parent_id: parent, invocation_id: invocation}
    attempts_rows: list = []  # 续筛历史尝试列表（continue_from 查询）；用例可注入模拟 DB 返回顺序

    class Cursor:
        def execute(self, sql, args):
            if 'continue_from' in sql:  # 沿游标的历史尝试列表查询（派发扫描/授权读取共用形态）
                self.result = None
                self.rows = attempts_rows
            else:
                self.result = records.get(args[0]) if 'FROM local_tool_invocations' in sql else None
                self.rows = []
        def fetchone(self):
            return self.result
        def fetchall(self):
            return self.rows

    @contextmanager
    def connection():
        yield SimpleNamespace(cursor=Cursor)

    monkeypatch.setattr(repository, 'get_db_connection', connection)
    monkeypatch.setattr(repository, 'get_invocation', lambda identifier, tenant: copy.deepcopy(records.get(identifier)))
    return SimpleNamespace(state=state, row=row, parent=parent, invocation=invocation,
                          next_phase=next_phase, model_phase=model_phase, cursor=Cursor(),
                          attempts_rows=attempts_rows)


def authorize(proof):
    row = {**proof.row, 'checkpoint': {'execution': proof.state.checkpoint()}}
    phase = LocalPhase('runner', 'call', 'resume.evaluate', 0)
    auth = dict(runner_id='runner', worker_id='worker', attempt=1, execution_id='runner', tool_call_id='call',
        purpose='resume_recognition_covered', physical_call_id='physical', phase_key=phase.key('runner'),
        branch=phase.branch, ordinal=phase.ordinal)
    return assert_physical_authorization(proof.cursor, row, Attempt('runner', 'worker', 1), auth,
        execution_id='runner', tool_call_id='call', purpose='resume_recognition_covered',
        call_id='physical', provider='zhipu', model='fixture')


def test_continuation_accepts_derived_arguments_for_covered_usage_and_recovery(proof):
    assert authorize(proof).endswith(':resume_recognition_covered')
    assert LocalRecoveryProof().can_restore(proof.row, proof.state, proof.state.tools['call'])


def test_late_original_parent_and_item_keep_model_authorization_and_repeated_recovery(proof):
    for invocation in (proof.parent, proof.invocation):
        original = invocation['result_json']
        invocation.update(state='unknown', effect='unknown', result_json={'_late_result': {
            **original, 'effect':'none', 'stopped':True}})
    assert authorize(proof).endswith(':resume_recognition_covered')
    assert LocalRecoveryProof().can_restore(proof.row, proof.state, proof.state.tools['call'])
    assert proof.parent['state'] == proof.invocation['state'] == 'unknown'


@pytest.mark.parametrize('field,value', [
    ('tenant_id', 'foreign'), ('user_id', 'foreign'), ('session_id', 'foreign'),
    ('device_id', 'foreign'), ('effect', 'unknown'), ('state', 'running'),
])
def test_parent_ownership_and_effect_mismatch_blocks_usage_and_recovery(proof, field, value):
    proof.parent[field] = value
    with pytest.raises(CheckpointFailure):
        authorize(proof)
    with pytest.raises(RunnerError):
        LocalRecoveryProof().can_restore(proof.row, proof.state, proof.state.tools['call'])


@pytest.mark.parametrize('change', ['names', 'job', 'limit', 'intent', 'digest', 'phase', 'image', 'candidate', 'target'])
def test_forged_continuation_or_resume_facts_cannot_authorize_model(proof, change):
    if change in {'names', 'job', 'limit'}:
        key, value = {'names': ('exclude_names', ['forged']), 'job': ('expected_job_name', 'forged'), 'limit': ('limit', 2)}[change]
        proof.next_phase['result'][key] = value
    elif change == 'intent':
        proof.next_phase['request']['limit'] = 2
    elif change == 'digest':
        proof.next_phase['intent_digest'] = 'forged'
    elif change == 'phase':
        proof.next_phase['phase'] = 'started'
    elif change in {'image', 'candidate'}:
        proof.model_phase['request']['image_digest' if change == 'image' else 'candidate_name'] = 'forged'
        proof.model_phase['intent_digest'] = hashlib.sha256(canonical_json(proof.model_phase['request']).encode()).hexdigest()
    else:
        key = LocalPhase('runner', 'call', 'resume.policy', 0).key('runner')
        proof.state.resources['local_domain_phases'][key]['result']['model_spec'] = 'other/target'
    with pytest.raises(CheckpointFailure):
        authorize(proof)


def test_device_argument_tampering_is_not_hidden_by_frozen_canonical_request(proof):
    proof.invocation['arguments_json'] = {**proof.invocation['arguments_json'], 'exclude_names': ['forged']}
    with pytest.raises(CheckpointFailure):
        authorize(proof)
    with pytest.raises(RunnerError):
        LocalRecoveryProof().can_restore(proof.row, proof.state, proof.state.tools['call'])


def _failed_attempt(name):
    """同游标「失败且可重试」历史尝试记录：无简历成果（resumes 空）+ 已标记处理过该候选人"""
    return dict(id=str(uuid4()), state='failed', effect='none', error_code='UI_CHANGED',
                result_json={'data': {'resumes': [], 'processed_names': [name], 'attempted': 1,
                                      'failures': [], 'stop_reason': 'UI_CHANGED'}})


@pytest.mark.parametrize('order', ['newest_first', 'oldest_first'])
def test_failed_attempt_names_are_order_insensitive(order):
    # 评审复现：派发按 created_at DESC 合并、模型授权读取无 ORDER BY——名单合成必须与顺序无关
    from src.local_tools.resume_continuation import failed_attempt_names
    rows = [_failed_attempt('乙'), _failed_attempt('甲')]
    if order == 'oldest_first':
        rows.reverse()
    assert failed_attempt_names(rows) == ['乙', '甲']


def test_model_authorization_survives_attempt_record_order_flip(proof):
    # 派发时（DB 按 created_at DESC）：先见乙记录；授权时模拟 DB 返回另一种合法顺序（先甲）。
    # 排序合成后两侧 exclude_names 一致，恢复证明不再误拒 LOCAL_RESUME_CONTINUATION_PROOF_INVALID
    proof.attempts_rows[:] = [_failed_attempt('甲'), _failed_attempt('乙')]
    names = ['previous', '乙', '甲']
    ref = proof.state.resources['local_invocations'][LocalPhase('runner', 'call', 'main', 0).key('runner')]
    ref['request']['arguments']['exclude_names'] = names
    proof.next_phase['result']['exclude_names'] = names
    proof.invocation['arguments_json']['exclude_names'] = names
    assert authorize(proof).endswith(':resume_recognition_covered')
