"""Continuation uses trusted history and excludes duplicates before recognition."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.local_tools import repository, resume_continuation as continuation
from src.local_tools.proxy_tool import BossResumeBatchTool, BossResumeNextTool, LocalToolProxyTool

pytestmark = pytest.mark.unit
PARENT = str(uuid4())


def kwargs(**extra):
    return {'_trusted_tenant_id': 'tenant', '_trusted_user_id': 'user', '_session_id': 'session',
        'continue_from': PARENT, 'limit': 3, **extra}


def parent(**extra):
    return {'id': PARENT, 'tenant_id': 'tenant', 'user_id': 'user', 'session_id': 'session',
        'device_id': 'device', 'tool_name': 'boss_resume_next', 'state': 'succeeded', 'effect': 'none',
        'arguments_json': {'exclude_names': ['甲先生'], 'expected_job_name': 'PHP开发'},
        'result_json': {'success': True, 'data': {'job_name': 'PHP开发',
            'stop_reason': 'limit_reached',
            'processed_names': ['乙先生', '丙先生'], 'resumes': [{'candidate_name': '乙先生'}],
            'failures': [{'name': '丙先生', 'error': '截图失败'}, {'name': None}]}}, **extra}


@pytest.mark.asyncio
async def test_cumulative_history_not_model_supplied_names(monkeypatch):
    fetch = MagicMock(return_value=parent())
    monkeypatch.setattr(repository, 'get_invocation', fetch)
    result, failure = await continuation.prepare_next(kwargs(exclude_names=['伪造'], expected_job_name='伪造'))
    assert failure is None
    assert result['exclude_names'] == ['甲先生', '乙先生', '丙先生']
    assert result['expected_job_name'] == 'PHP开发'
    assert result['_resume_expected_device_id'] == 'device'
    fetch.assert_called_once_with(PARENT, 'tenant')


@pytest.mark.asyncio
async def test_late_completed_parent_retains_original_cursor_and_device(monkeypatch):
    original = parent()
    original.update(state='unknown', effect='unknown', result_json={'_late_result': {
        **original['result_json'], 'effect':'none', 'stopped':True}})
    monkeypatch.setattr(repository, 'get_invocation', lambda *args: original)
    result, failure = await continuation.prepare_next(kwargs())
    assert failure is None and result['_resume_expected_device_id'] == 'device'
    assert result['exclude_names'] == ['甲先生','乙先生','丙先生']
    assert original['state'] == 'unknown'


@pytest.mark.asyncio
@pytest.mark.parametrize('change', [
    {'tenant_id': 'other'}, {'user_id': 'other'}, {'session_id': 'other'},
    {'tool_name': 'boss_greet'}, {'state': 'running'}, {'effect': 'unknown'},
    {'result_json': {'success': False}},
])
async def test_invalid_origin_never_dispatches(monkeypatch, change):
    monkeypatch.setattr(repository, 'get_invocation', lambda *a: parent(**change))
    result, failure = await continuation.prepare_next(kwargs())
    assert result is None and failure['code'] == 'INVALID_CONTINUATION'


@pytest.mark.asyncio
async def test_missing_identity_or_invalid_cursor(monkeypatch):
    fetch = MagicMock()
    monkeypatch.setattr(repository, 'get_invocation', fetch)
    for args in [kwargs(_session_id=None), kwargs(continue_from='not-uuid'), kwargs(limit=4), kwargs(limit=True)]:
        result, failure = await continuation.prepare_next(args)
        assert result is None and failure['success'] is False
    fetch.assert_not_called()


@pytest.mark.asyncio
async def test_selected_device_change_is_rejected_before_dispatch_or_credit_check(monkeypatch):
    tool = BossResumeNextTool()
    device = AsyncMock(return_value=({'id': 'other-device'}, None))
    dispatch, credit = AsyncMock(), AsyncMock()
    monkeypatch.setattr(tool, '_find_ready_device', device)
    monkeypatch.setattr(tool, '_dispatch_and_wait', dispatch)
    monkeypatch.setattr(tool, '_tenant_credit_blocked', credit)
    result = await LocalToolProxyTool.execute(tool, **kwargs(_resume_expected_device_id='device'))
    assert result['code'] == 'DEVICE_UNAVAILABLE'
    dispatch.assert_not_called()
    credit.assert_not_called()


@pytest.mark.asyncio
async def test_durable_recovery_reuses_canonical_intent(monkeypatch):
    fetch = MagicMock(return_value=parent())
    monkeypatch.setattr(repository, 'get_invocation', fetch)
    saved = None

    async def saved_domain(**ignored):
        return saved

    async def complete_phase(**values):
        nonlocal saved
        saved = {'request': values['intent'], 'result': values['result'], 'phase': 'completed'}
        return values['result']

    operation = SimpleNamespace(ordinal=0, owner=SimpleNamespace(saved_domain=saved_domain, complete_phase=complete_phase))
    first, _ = await continuation.prepare_next(kwargs(), operation)
    fetch.side_effect = AssertionError('recovery must not refetch mutable history')
    resumed, _ = await continuation.prepare_next(kwargs(), operation)
    assert resumed == first
    with pytest.raises(continuation.LocalContinuationRequired):
        await continuation.prepare_next(kwargs(limit=2), operation)


@pytest.mark.asyncio
async def test_legacy_next_duplicate_provider_payload_never_recognized_or_billed(monkeypatch):
    import src.local_tools.proxy_tool as proxy
    monkeypatch.setattr(repository, 'get_invocation', lambda *a: parent())
    dispatch = AsyncMock(return_value={'success': True, 'invocation_id': 'next-id', 'effect': 'none',
        'data': {'job_name': 'PHP开发', 'resumes': [{'candidate_name': '乙先生', 'job_name': 'PHP开发',
            'images': [{'base64': 'must-not-leak'}]}], 'processed_names': ['乙先生'], 'stop_reason': 'limit_reached'}})
    monkeypatch.setattr(LocalToolProxyTool, 'execute', dispatch)
    vl, bill, store = AsyncMock(), AsyncMock(), MagicMock()
    monkeypatch.setattr(proxy, '_evaluate_resume_via_vl', vl)
    monkeypatch.setattr(proxy, '_bill_resume_recognition', bill)
    monkeypatch.setattr(proxy.recruiting_resume_service, 'create_resume_record_from_tool_result', store)
    result = await BossResumeNextTool()._execute_legacy(**kwargs())
    vl.assert_not_called(); bill.assert_not_called(); store.assert_not_called()
    assert result['code'] == 'RESUME_STORE_FAILED'
    assert result['data']['continuation_id'] == 'next-id'
    assert 'must-not-leak' not in str(result)
    assert dispatch.call_args.kwargs['exclude_names'] == ['甲先生', '乙先生', '丙先生']


@pytest.mark.asyncio
async def test_durable_gate_and_empty_batch_preserve_cursor(monkeypatch):
    from src.local_tools import domain_flow, overlay_flow, resume_flow
    import src.tools.context as context
    operation = SimpleNamespace(branch='device', ordinal=0, owner=SimpleNamespace(
        saved_phase=AsyncMock(return_value={'request': {'device_id': 'device'}})))
    monkeypatch.setattr(context, 'current_tool_execution_context', lambda: SimpleNamespace(
        tenant_id='tenant', agent_execution_id='exec', tool_call_id='call'))
    process = AsyncMock()
    monkeypatch.setattr(resume_flow, 'process_item', process)
    monkeypatch.setattr(continuation, 'prepare_next', AsyncMock(return_value=(kwargs(
        exclude_names=['乙先生'], expected_job_name='PHP开发'), None)))
    raw = {'success': True, 'invocation_id': 'next', 'data': {'job_name': 'PHP开发',
        'resumes': [{'candidate_name': '乙先生', 'job_name': 'PHP开发'}], 'stop_reason': 'limit_reached'}}
    monkeypatch.setattr(overlay_flow, 'run_base', AsyncMock(return_value=raw))
    result = await domain_flow.resume_result(BossResumeNextTool(), operation, kwargs(), batch=True)
    assert result['code'] == 'RESUME_STORE_FAILED'
    process.assert_not_called()
    raw['data'] = {'resumes': [], 'stop_reason': 'no_progress', 'job_name': 'PHP开发'}
    result = await domain_flow.resume_result(BossResumeNextTool(), operation, kwargs(), batch=True)
    assert result['success'] is True and result['data']['continuation_id'] == 'next'
    process.assert_not_called()


def test_atomic_stale_guard_does_not_insert_and_keeps_runner_dedupe():
    cursor = MagicMock()
    cursor.fetchone.side_effect = [{'id':'device'}, None, parent()]
    cursor.fetchall.side_effect = [[], [{'id': 'already-next', 'business_kind': 'agent_runner', 'dedupe_key': 'other-call'}]]
    with pytest.raises(repository.ResumeContinuationConflict) as caught:
        repository.create_invocation_in_tx(cursor, 'tenant', 'user', 'device', 'boss_resume_next',
            {'continue_from': PARENT}, 'session', business_kind='agent_runner', dedupe_key='current-call')
    assert caught.value.invocation_id == 'already-next'
    assert any('FOR UPDATE' in call.args[0] for call in cursor.execute.call_args_list)
    assert all('INSERT' not in c.args[0] for c in cursor.execute.call_args_list)


def test_durable_same_request_can_rebind_original_invocation():
    cursor = MagicMock()
    cursor.fetchone.side_effect = [{'id':'device'}, None, parent(), {'id': 'already-next'}]
    cursor.fetchall.side_effect = [[], [{'id': 'already-next', 'business_kind': 'agent_runner', 'dedupe_key': 'same-call'}]]
    actual = repository.create_invocation_in_tx(cursor, 'tenant', 'user', 'device', 'boss_resume_next',
        {'continue_from': PARENT}, 'session', business_kind='agent_runner', dedupe_key='same-call')
    assert actual == 'already-next'
    assert 'INSERT' in cursor.execute.call_args_list[-1].args[0]


@pytest.mark.asyncio
async def test_returned_cloud_arguments_are_valid_and_restore_cumulative_history(monkeypatch):
    raw = {'success': True, 'effect': 'none', 'invocation_id': PARENT,
        'data': {'job_name': 'PHP开发', 'processed_names': ['乙先生', '丙先生'],
            'stop_reason': 'limit_reached', 'attempted': 2}}
    metadata = continuation.batch_metadata(raw, {'exclude_names': ['甲先生']})
    capability = metadata['continuation']
    assert capability['available'] is True and capability['processed_count'] == 3
    assert capability['arguments'] == {'continue_from': PARENT}
    assert capability['filters_verified'] is False
    tool = BossResumeNextTool()
    assert tool.validate_parameters(**capability['arguments'], limit=2)
    monkeypatch.setattr(repository, 'get_invocation', lambda *a: parent(result_json=raw))
    canonical, failure = await continuation.prepare_next(kwargs(**capability['arguments'], limit=2))
    assert failure is None and canonical['limit'] == 2
    assert canonical['exclude_names'] == ['甲先生', '乙先生', '丙先生']
    assert canonical['expected_job_name'] == 'PHP开发'


@pytest.mark.parametrize('reason', ['no_progress', 'search_limit', 'detail_not_closed'])
@pytest.mark.asyncio
async def test_stopped_capability_has_no_action_and_rejects_dispatch(monkeypatch, reason):
    raw = {'success': True, 'effect': 'none', 'invocation_id': PARENT,
        'data': {'job_name': 'PHP开发', 'processed_names': ['乙先生'], 'stop_reason': reason}}
    capability = continuation.batch_metadata(raw)['continuation']
    assert capability['available'] is False and capability['arguments'] is None
    assert capability['reason'] == reason
    monkeypatch.setattr(repository, 'get_invocation', lambda *a: parent(result_json=raw))
    canonical, failure = await continuation.prepare_next(kwargs())
    assert canonical is None and failure['code'] == 'CONTINUATION_STOPPED'


def test_missing_or_unsafe_context_cannot_offer_next_action():
    raw = {'success': True, 'effect': 'none', 'invocation_id': PARENT,
        'data': {'job_name': 'PHP开发', 'processed_names': ['甲先生'], 'stop_reason': 'limit_reached'}}
    for change in [{'invocation_id': None}, {'effect': 'unknown'}, {'success': False}]:
        capability = continuation.batch_metadata({**raw, **change})['continuation']
        assert capability['available'] is False and capability['arguments'] is None
    capability = continuation.batch_metadata(raw, {'exclude_names': [f'候选人{i}' for i in range(999)]})['continuation']
    assert capability['available'] is False and capability['reason'] == 'history_limit'
    assert not BossResumeNextTool().validate_parameters(continue_from='not-uuid', limit=2)
    assert not BossResumeNextTool().validate_parameters(continue_from=PARENT, limit=4)


@pytest.mark.parametrize('change', [
    {'stop_reason': None}, {'stop_reason': 'unknown_stop'}, {'job_name': None},
])
@pytest.mark.asyncio
async def test_incomplete_history_capability_and_dispatch_gate_agree(monkeypatch, change):
    historical = parent()
    historical['result_json']['data'].update(change)
    capability = continuation.batch_metadata({**historical['result_json'],
        'invocation_id': PARENT, 'effect': 'none'}, historical['arguments_json'])['continuation']
    assert capability['available'] is False
    monkeypatch.setattr(repository, 'get_invocation', lambda *a: historical)
    canonical, failure = await continuation.prepare_next(kwargs())
    assert canonical is None and failure['code'] == 'CONTINUATION_STOPPED'
