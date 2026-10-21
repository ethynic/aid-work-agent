"""Continuation uses trusted history and excludes duplicates before recognition."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.local_tools import repository, resume_continuation as continuation
from src.local_tools.proxy_tool import BossResumeBatchTool, BossResumeNextTool, LocalToolProxyTool

pytestmark = pytest.mark.unit
PARENT = str(uuid4())


@pytest.fixture(autouse=True)
def _no_history_attempts(monkeypatch):
    """默认无历史续筛尝试记录（隔离 DB）；需要失败候选人合并语义的用例单独覆盖。"""
    monkeypatch.setattr(repository, 'list_resume_next_attempts', lambda *args, **kwargs: [])


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


@pytest.mark.parametrize('reason', ['search_limit'])
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


# ============== 登录失败续筛恢复（评审问题 1）：诊断元数据 ≠ 业务成果 ==============


def cli_diagnostic_failure(code, *, with_target=True, extra_data=None):
    """真实 CLI 回执形态：失败结果由 withLocalRecovery 固定附加 operation_recovery /
    visual_recovery（及可能的 browser_target_ref）诊断元数据，不含业务读取成果。"""
    data = {
        'operation_recovery': {'schema_version': 2, 'owner': 'cli',
            'operation': {'name': 'boss_resume_next', 'goal': '逐项读取简历并恢复推荐列表',
                'expected': '推荐列表可读且可继续逐项打开详情', 'result_kind': 'page_state'},
            'execution': {'stage': 'prepared', 'input_status': 'not_started',
                'attempts_used': 1, 'ordinary_retries_used': 0},
            'verification': {'status': 'inconclusive', 'evidence': ['批次入口页面尚未通过校验，未开始读取']}},
        'visual_recovery': {'schema_version': 1, 'owner': 'cli', 'state': 'not_eligible',
            'receipts': [], 'observations': [], 'steps': [], 'original_run_id': 'run-1',
            'original_code': code, 'original_effect': 'none', 'original_execution': {},
            'policy': 'readonly'},
    }
    if with_target:
        data['browser_target_ref'] = '00000000-0000-4000-8000-000000000000'
    data.update(extra_data or {})
    return {'success': False, 'code': code, 'message': '请确认已登录BOSS', 'effect': 'none',
            'run_id': 'run-1', 'data': data}


def failed_next(receipt):
    return {'id': 'next-failed', 'business_kind': 'agent_runner', 'dedupe_key': 'other-call',
            'state': 'failed', 'effect': 'none', 'error_code': receipt['code'],
            'result_json': receipt}


@pytest.mark.parametrize('code', ['NOT_LOGGED_IN', 'WRONG_PAGE'])
@pytest.mark.parametrize('with_target', [True, False])
def test_explicitly_not_executed_failure_with_cli_diagnostics_is_retry_safe(code, with_target):
    """登录失败/错页（effect=none、明确未执行）带真实 CLI 诊断元数据 → 允许重试，
    不再把诊断元数据误当已有业务成果拦截续筛。"""
    assert repository.continuation_retry_safe(failed_next(
        cli_diagnostic_failure(code, with_target=with_target))) is True


@pytest.mark.parametrize('business_keys', [
    {'resumes': [{'candidate_name': '甲先生'}]},   # 已读简历=成果（唯一成果载体）
    {'resumes': 'x'},                               # 类型异常（fail-closed）
    {'future_field': 1},                            # 未知新键=成果
])
def test_unknown_or_nonempty_business_keys_still_block_retry(business_keys):
    """fail-closed 方向：resumes 非空或类型异常、集合外未知键（含未来 CLI 新增
    业务字段）都视为业务成果 → 拦截重试；「没有简历成果」以 resumes 空列表为准
    （processed_names/attempted 不再拦截——取卡即标记含失败路径）。"""
    assert repository.continuation_retry_safe(failed_next(
        cli_diagnostic_failure('NOT_LOGGED_IN', extra_data=business_keys))) is False


def test_retry_after_login_continues_along_original_cursor_without_rereading():
    """评审场景：boss_resume_next 登录失败（回执带诊断元数据）→ 用户登录后重新
    续筛 → 旧失败记录不再拦截（无 ResumeContinuationConflict，允许 INSERT 沿
    原游标派发）；已入库成果经 exclude_names 游标排除，不重复识别/计费。"""
    cursor = MagicMock()
    cursor.fetchone.side_effect = [{'id': 'device'}, None, parent(), {'id': 'next-new'}]
    cursor.fetchall.side_effect = [[], [failed_next(cli_diagnostic_failure('NOT_LOGGED_IN'))]]
    actual = repository.create_invocation_in_tx(cursor, 'tenant', 'user', 'device',
        'boss_resume_next', {'continue_from': PARENT}, 'session',
        business_kind='agent_runner', dedupe_key='fresh-call')
    assert actual == 'next-new', '诊断元数据失败记录不得以 STALE_CONTINUATION 拦截重试'
    insert = [call for call in cursor.execute.call_args_list if 'INSERT INTO local_tool_invocations' in call.args[0]]
    assert insert and insert[-1].args[1][3] == 'boss_resume_next', '沿原游标派发新的续筛调用'


@pytest.mark.asyncio
async def test_cursor_after_retry_excludes_all_recognized_names(monkeypatch):
    """重试续筛沿原游标：父批已识别名单（resumes + processed_names + 历史
    exclude_names）全部进入新的 exclude_names——已入库份零重复识别、零重复计费。"""
    monkeypatch.setattr(repository, 'get_invocation', lambda *a: parent())
    canonical, failure = await continuation.prepare_next(kwargs())
    assert failure is None
    assert canonical['exclude_names'] == ['甲先生', '乙先生', '丙先生']
    assert canonical['continue_from'] == PARENT and canonical['limit'] == 3


# ============== 页面已恢复后沿游标续筛（评审问题 2） ==============


def restored_receipt(stop_reason='detail_not_closed'):
    """真实 CLI 回执形态：批次部分成功暂停 + 自动恢复确认页面可用
    （visual_recovery.page_restored=true，state=page_restored_result_preserved），
    原 stop_reason 诊断信息保留。"""
    return {'success': True, 'code': 'OK', 'effect': 'none', 'invocation_id': PARENT,
        'data': {'job_name': 'PHP开发', 'processed_names': ['乙先生'],
                 'resumes': [{'candidate_name': '乙先生'}],
                 'failures': [{'name': '乙先生', 'error': 'Escape 后简历详情未关闭，无法继续处理后续卡片'}],
                 'attempted': 1, 'stop_reason': stop_reason,
                 'visual_recovery': {'schema_version': 1, 'owner': 'cli',
                     'state': 'page_restored_result_preserved', 'page_restored': True,
                     'receipts': [], 'observations': [], 'steps': [], 'original_code': 'OK',
                     'policy': 'readonly'}}}


@pytest.mark.asyncio
async def test_page_restored_receipt_opens_continuation_along_cursor(monkeypatch):
    """评审场景：关闭详情失败暂停 → 自动恢复确认（page_restored=true）→ 续筛
    可沿原游标继续（available=True、原 stop_reason 保留），已读成果不重复。"""
    raw = restored_receipt()
    capability = continuation.batch_metadata(raw)['continuation']
    assert capability['available'] is True, '页面已恢复确认后不再被旧停止原因禁止'
    assert capability['reason'] == 'detail_not_closed', '原停止原因作为诊断保留'
    assert capability['arguments'] == {'continue_from': PARENT}
    monkeypatch.setattr(repository, 'get_invocation', lambda *a: parent(result_json=raw))
    canonical, failure = await continuation.prepare_next(kwargs())
    assert failure is None, '不再返回 CONTINUATION_STOPPED'
    assert canonical['exclude_names'] == ['甲先生', '乙先生'], '已读成果经游标排除，不整批重读'
    assert canonical['expected_job_name'] == 'PHP开发'


@pytest.mark.asyncio
@pytest.mark.parametrize('legacy_reason', ['search_limit', 'unknown_stop', None])
async def test_legacy_non_page_state_reasons_without_restored_mark_stay_stopped(monkeypatch, legacy_reason):
    """兼容：非页面状态类停止原因（搜索 30 轮上限/用户取消/未知原因）且无恢复
    标记 → 行为不变仍不可续筛（no_progress 已按评审第八轮归入页面状态类放行，
    另见 test_manual_recovery_after_page_failure_opens_continuation）。"""
    data = {'job_name': 'PHP开发', 'processed_names': ['乙先生'],
            'stop_reason': legacy_reason}
    raw = {'success': True, 'effect': 'none', 'invocation_id': PARENT, 'data': data}
    assert continuation.batch_metadata(raw)['continuation']['available'] is False
    monkeypatch.setattr(repository, 'get_invocation', lambda *a: parent(result_json=raw))
    canonical, failure = await continuation.prepare_next(kwargs())
    assert canonical is None and failure['code'] == 'CONTINUATION_STOPPED'


@pytest.mark.parametrize('case', [
    # 恢复标记/页面状态放宽不能凌驾于游标完整性/终局判定之上
    {'success': False}, {'effect': 'unknown'},                                  # unsafe_result
    {'data': {'job_name': None, 'stop_reason': 'detail_not_closed',
              'processed_names': ['乙先生']}},                                  # missing_context
    {'data': {'job_name': 'PHP开发', 'stop_reason': 'no_matching_candidates'}},  # 终局筛选
])
@pytest.mark.parametrize('via', ['restored_mark', 'page_state_reason'])
def test_relaxations_never_override_cursor_integrity_gates(case, via):
    raw = restored_receipt()
    if via == 'page_state_reason':
        raw['data']['visual_recovery'] = {'schema_version': 1, 'owner': 'cli',
                                          'state': 'dismiss_unconfirmed'}  # 无恢复标记
    if 'success' in case:
        raw['success'] = case['success']
    if 'effect' in case:
        raw['effect'] = case['effect']
    if 'data' in case:
        raw['data'].update(case['data'])
    if via == 'restored_mark':
        raw['data']['visual_recovery'] = restored_receipt()['data']['visual_recovery']
    if 'processed_names' not in raw['data']:
        raw['data']['processed_names'] = ['乙先生']
    capability = continuation.batch_metadata(raw)['continuation']
    assert capability['available'] is False


# ============== 人工恢复分支（评审第七轮） ==============


@pytest.mark.asyncio
@pytest.mark.parametrize('stop_reason', ['page_operation_failed', 'detail_not_closed', 'UI_CHANGED'])
async def test_manual_recovery_after_page_failure_opens_continuation(monkeypatch, stop_reason):
    """评审场景 1：甲已读、乙处理时页面被遮挡且自动恢复未成功（无 page_restored
    标记）→ 用户人工关闭遮挡后回复「继续」→ 不再被旧回执的页面状态类停止原因
    永久禁止：available 放行，prepare_next 沿原游标（exclude 含甲）给出续筛
    参数；实际页面可用性由 boss_resume_next 既有入口校验（推荐页/职位核验）
    本次调用时再核验。"""
    raw = {'success': True, 'code': 'OK', 'effect': 'none', 'invocation_id': PARENT,
        'data': {'job_name': 'PHP开发', 'processed_names': ['甲先生'],
                 'resumes': [{'candidate_name': '甲先生'}],
                 'failures': [{'name': '甲先生', 'error': 'Escape 后简历详情未关闭，无法继续处理后续卡片'}],
                 'attempted': 1, 'stop_reason': stop_reason,
                 'visual_recovery': {'schema_version': 1, 'owner': 'cli',
                     'state': 'dismiss_unconfirmed', 'receipts': [], 'observations': [],
                     'steps': [], 'original_code': 'OK', 'policy': 'readonly'}}}
    capability = continuation.batch_metadata(raw)['continuation']
    assert capability['available'] is True, '页面状态类停止不再仅凭旧回执永久禁止续筛'
    assert capability['reason'] == stop_reason, '原停止原因作为诊断保留'
    monkeypatch.setattr(repository, 'get_invocation', lambda *a: parent(result_json=raw))
    canonical, failure = await continuation.prepare_next(kwargs())
    assert failure is None, '人工处理后「继续」不再返回 CONTINUATION_STOPPED'
    assert canonical['exclude_names'] == ['甲先生'], '已读的甲经游标排除：乙可处理、甲不重读不重复计费'
    assert canonical['expected_job_name'] == 'PHP开发'


@pytest.mark.asyncio
async def test_manual_recovery_verification_failure_is_retryable_not_permanent(monkeypatch):
    """人工恢复后页面仍未修好：续筛派发经入口校验失败（如 WRONG_PAGE）→ 如实
    报错；该失败回执不永久占用游标，修好后再次「继续」仍可重试。"""
    raw = {'success': True, 'effect': 'none', 'invocation_id': PARENT,
        'data': {'job_name': 'PHP开发', 'processed_names': ['甲先生'],
                 'stop_reason': 'page_operation_failed'}}
    monkeypatch.setattr(repository, 'get_invocation', lambda *a: parent(result_json=raw))
    # 第一次：人工「继续」→ 放行进入入口校验
    canonical, failure = await continuation.prepare_next(kwargs())
    assert failure is None and canonical['exclude_names'] == ['甲先生']
    # 入口校验失败（WRONG_PAGE，回执带诊断元数据）→ 可重试（不永久阻断）
    assert repository.continuation_retry_safe(failed_next(
        cli_diagnostic_failure('WRONG_PAGE'))) is True
    # 判定不依赖任何已变化状态：修好后再次「继续」仍放行
    canonical_again, failure_again = await continuation.prepare_next(kwargs())
    assert failure_again is None and canonical_again['exclude_names'] == ['甲先生']


@pytest.mark.parametrize('code', ['UI_CHANGED', 'NOT_LOGGED_IN', 'WRONG_PAGE'])
def test_zero_read_page_failure_with_real_receipt_is_retry_safe(code):
    """评审场景 2：入口残留详情无法自动关闭返回 UI_CHANGED、attempted=0、
    resumes/processed_names 均空（真实回执形态：空结果字段 + 失败诊断 +
    诊断元数据）→ retry-safe；UI_CHANGED 与 NOT_LOGGED_IN/WRONG_PAGE 在
    「无成果」语义下同等对待。"""
    receipt = {'success': False, 'code': code, 'message': 'Escape 后简历详情未关闭', 'effect': 'none',
        'run_id': 'run-2', 'data': {
            'operation_recovery': {'schema_version': 2, 'owner': 'cli',
                'operation': {'name': 'boss_resume_next', 'result_kind': 'page_state'},
                'execution': {'stage': 'postcheck', 'input_status': 'not_started',
                    'attempts_used': 2, 'ordinary_retries_used': 1},
                'verification': {'status': 'not_satisfied', 'evidence': ['入口残留详情未关闭']}},
            'visual_recovery': {'schema_version': 1, 'owner': 'cli', 'state': 'dismiss_unconfirmed',
                'receipts': [], 'observations': [], 'steps': [], 'original_code': code,
                'original_effect': 'none', 'policy': 'readonly'},
            'browser_target_ref': '00000000-0000-4000-8000-000000000000',
            'resumes': [], 'processed_names': [], 'attempted': 0,
            'failures': [{'name': None, 'error': '入口残留详情未关闭，未点击背景候选人'}],
            'stop_reason': 'detail_not_closed'}}
    assert repository.continuation_retry_safe(failed_next(receipt)) is True


@pytest.mark.parametrize('partial', [
    {'resumes': [{'candidate_name': '甲先生'}]},          # 已读简历=成果（唯一拦截面）
    {'resumes': [], 'resumes_extra': [{'candidate_name': '甲'}]},  # 未知新键=成果
    {'resumes': 'x'},                                       # 类型异常（fail-closed）
])
def test_zero_read_relaxation_only_for_confirmed_empty_outcomes(partial):
    """「没有简历成果」以 resumes 空列表为准：resumes 非空/类型异常/未知新键
    仍拦截；processed_names 非空与 attempted>0 不再拦截（见补充放行测试）。"""
    receipt = {'success': False, 'code': 'UI_CHANGED', 'effect': 'none',
        'data': {'operation_recovery': {'schema_version': 2, 'owner': 'cli'},
                 'resumes': [], 'processed_names': [], 'attempted': 0, **partial}}
    assert repository.continuation_retry_safe(failed_next(receipt)) is False


def test_zero_read_page_failure_does_not_block_new_continuation_along_cursor():
    """评审场景 2（事务级）：入口残留详情失败记录（无成果）不再以 STALE_CONTINUATION
    拦截 → 人工关闭详情后新续筛沿原游标派发；游标沿用上一批 processed_names。"""
    receipt = {'success': False, 'code': 'UI_CHANGED', 'effect': 'none',
        'data': {'operation_recovery': {'schema_version': 2, 'owner': 'cli'},
                 'visual_recovery': {'schema_version': 1, 'owner': 'cli',
                                     'state': 'dismiss_unconfirmed'},
                 'resumes': [], 'processed_names': [], 'attempted': 0,
                 'failures': [{'name': None, 'error': '入口残留详情未关闭'}],
                 'stop_reason': 'detail_not_closed'}}
    cursor = MagicMock()
    cursor.fetchone.side_effect = [{'id': 'device'}, None, parent(), {'id': 'next-retry'}]
    cursor.fetchall.side_effect = [[], [failed_next(receipt)]]
    actual = repository.create_invocation_in_tx(cursor, 'tenant', 'user', 'device',
        'boss_resume_next', {'continue_from': PARENT}, 'session',
        business_kind='agent_runner', dedupe_key='retry-call')
    assert actual == 'next-retry', '无成果页面失败不得永久占用原游标（不再 STALE_CONTINUATION）'


@pytest.mark.asyncio
async def test_zero_read_retry_cursor_reuses_parent_processed_names(monkeypatch):
    """新续筛游标沿用上一批名单：父批 processed_names 全部进入 exclude_names，
    已入库份零重复识别、零重复计费。"""
    monkeypatch.setattr(repository, 'get_invocation', lambda *a: parent())
    canonical, failure = await continuation.prepare_next(kwargs())
    assert failure is None
    assert canonical['exclude_names'] == ['甲先生', '乙先生', '丙先生']


# ============== 第八轮：人工恢复两个残余阻断分支 ==============


@pytest.mark.parametrize('partial', [
    {'processed_names': ['甲先生'], 'attempted': 1},   # 复现形态：取卡即标记+已尝试
    {'processed_names': ['甲先生']},                     # 仅已处理名单非空
    {'attempted': 3},                                    # 仅已尝试>0
    {'failures': [{'name': '甲先生', 'error': '当前简历被外部客服弹窗遮挡'}]},
])
@pytest.mark.parametrize('code', ['UI_CHANGED', 'NOT_LOGGED_IN', 'WRONG_PAGE'])
def test_zero_read_with_attempted_cards_is_retry_safe(code, partial):
    """评审复现（问题 1）：第一人处理过程中失败（UI_CHANGED、resumes=[]、
    processed_names=[该候选人]、attempted=1）→「没有简历成果」以 resumes 空
    列表为准 → retry-safe；取卡即标记含失败路径，processed_names/attempted
    不再作为拦截必要条件。"""
    receipt = {'success': False, 'code': code, 'effect': 'none',
        'data': {'operation_recovery': {'schema_version': 2, 'owner': 'cli'},
                 'visual_recovery': {'schema_version': 1, 'owner': 'cli',
                                     'state': 'dismiss_unconfirmed'},
                 'resumes': [], **partial}}
    assert repository.continuation_retry_safe(failed_next(receipt)) is True


def test_zero_read_with_attempted_cards_does_not_block_cursor():
    """评审复现（事务级）：上述失败记录不再以 STALE_CONTINUATION 拦截 →
    人工关闭弹窗后新续筛沿原游标 INSERT。"""
    receipt = {'success': False, 'code': 'UI_CHANGED', 'effect': 'none',
        'data': {'operation_recovery': {'schema_version': 2, 'owner': 'cli'},
                 'visual_recovery': {'schema_version': 1, 'owner': 'cli',
                                     'state': 'dismiss_unconfirmed'},
                 'resumes': [], 'processed_names': ['甲先生'], 'attempted': 1,
                 'failures': [{'name': '甲先生', 'error': '当前简历被外部客服弹窗遮挡'}],
                 'stop_reason': 'page_operation_failed'}}
    cursor = MagicMock()
    cursor.fetchone.side_effect = [{'id': 'device'}, None, parent(), {'id': 'next-retry'}]
    cursor.fetchall.side_effect = [[], [failed_next(receipt)]]
    actual = repository.create_invocation_in_tx(cursor, 'tenant', 'user', 'device',
        'boss_resume_next', {'continue_from': PARENT}, 'session',
        business_kind='agent_runner', dedupe_key='retry-call')
    assert actual == 'next-retry'


@pytest.mark.asyncio
async def test_retry_excludes_failed_candidates_along_cursor(monkeypatch):
    """沿用游标排除语义：失败且可重试的历史尝试中已标记的候选人（含失败路径
    取卡标记）合并进新 exclude_names——重试不再重复尝试失败候选人，已完成
    简历继续排除；成功/挂起/有成果失败的记录不合并（名单在各自回执游标里）。"""
    parent_row = parent()
    monkeypatch.setattr(repository, 'get_invocation', lambda *a: parent_row)
    attempts = [
        failed_next({'success': False, 'code': 'UI_CHANGED', 'effect': 'none',
            'data': {'resumes': [], 'processed_names': ['丁先生'], 'attempted': 1}}),
        {'id': 'next-succeeded', 'state': 'succeeded', 'effect': 'none',
         'result_json': {'success': True, 'data': {'processed_names': ['戊先生'],
             'resumes': [{'candidate_name': '戊先生'}]}}},
        {'id': 'next-outcome-failed', 'state': 'failed', 'effect': 'none',
         'error_code': 'EXECUTION_UNKNOWN',
         'result_json': {'success': False, 'code': 'EXECUTION_UNKNOWN', 'effect': 'unknown',
             'data': {'resumes': [{'candidate_name': '己先生'}]}}},
    ]
    monkeypatch.setattr(repository, 'list_resume_next_attempts',
                        lambda *args, **kwargs: attempts)
    canonical, failure = await continuation.prepare_next(kwargs())
    assert failure is None
    assert canonical['exclude_names'] == ['甲先生', '乙先生', '丙先生', '丁先生'], \
        '失败候选人丁沿用游标排除；成功戊/有成果失败己不合并（前者游标已含、后者不可重试）'


@pytest.mark.asyncio
async def test_recovery_proof_matches_merged_cursor(monkeypatch):
    """恢复证明与派发口径一致：continuation_arguments 用同一合并规则复算
    exclude_names（排除当前调用自身记录——派发时尚不存在），saved 结果一致
    即通过；合并名单不一致即拒绝。"""
    from types import SimpleNamespace as NS
    from src.services.agent_runner.resume_next_proof import continuation_arguments
    parent_row = parent()
    parent_row['arguments_json'] = {'exclude_names': ['甲先生']}
    attempts = [
        {'id': 'attempt-failed', 'state': 'failed', 'effect': 'none',
         'error_code': 'UI_CHANGED',
         'result_json': {'success': False, 'code': 'UI_CHANGED', 'effect': 'none',
             'data': {'resumes': [], 'processed_names': ['丁先生'], 'attempted': 1}}},
        {'id': 'current-next', 'state': 'unknown', 'effect': 'unknown',
         'error_code': 'EXECUTION_UNKNOWN',
         'result_json': {'success': False, 'data': {'resumes': []}}},
    ]
    import hashlib as _hashlib
    from src.services.agent_runner.contracts import canonical_json as _canonical
    from src.services.agent_runner.local_invocations import LocalPhase as _Phase
    intent = {'continue_from': PARENT, 'limit': 3}
    saved = {'runner_id': 'runner', 'execution_id': 'exec', 'tool_call_id': 'call',
             'branch': 'resume.next.request', 'ordinal': 0, 'phase': 'completed',
             'request': intent, 'intent_digest': _hashlib.sha256(
                 _canonical(intent).encode()).hexdigest(),
             'result': {'continue_from': PARENT, 'limit': 3,
                        'exclude_names': ['甲先生', '乙先生', '丙先生', '丁先生'],
                        'expected_job_name': 'PHP开发', '_resume_expected_device_id': 'device'}}
    node = {'identity': {'tenant_id': 'tenant', 'user_id': 'user', 'session_id': 'session',
                         'source': 'chat', 'session_kind': 'web'}, 'execution_id': 'exec',
            'resources': {'local_domain_phases': {
                _Phase('exec', 'call', 'resume.next.request', 0).key('runner'): saved},
                'local_invocations': {
                    _Phase('exec', 'call', 'main', 0).key('runner'): {
                        'request': {'device_id': 'device'},
                        'invocation_id': 'current-next'}}}}
    row = {'runner_id': 'runner', 'tenant_id': 'tenant', 'user_id': 'user',
           'session_id': 'session', 'source': 'chat', 'session_kind': 'web'}
    proven = continuation_arguments(row, node, 'call',
        {'continue_from': PARENT, 'limit': 3},
        lambda parent_id: parent_row,
        load_attempts=lambda parent: attempts,
        current_invocation_id='current-next')
    assert proven == {'continue_from': PARENT, 'limit': 3,
                      'exclude_names': ['甲先生', '乙先生', '丙先生', '丁先生'],
                      'expected_job_name': 'PHP开发'}
    with pytest.raises(Exception):
        continuation_arguments(row, node, 'call', {'continue_from': PARENT, 'limit': 3},
            lambda parent_id: parent_row, load_attempts=lambda parent: [],
            current_invocation_id='current-next')


@pytest.mark.asyncio
async def test_manual_recovery_after_no_progress_opens_continuation(monkeypatch):
    """评审复现（问题 2）：甲已读、列表两次未推进且自动恢复未成功（无
    page_restored）→ 人工修复列表后回复「继续」→ available 放行 → prepare_next
    沿原游标（exclude 含甲）；页面核验不过时报错可再重试（非永久阻断）。"""
    raw = {'success': True, 'code': 'OK', 'effect': 'none', 'invocation_id': PARENT,
        'data': {'job_name': 'PHP开发', 'processed_names': ['甲先生'],
                 'resumes': [{'candidate_name': '甲先生'}], 'attempted': 1,
                 'stop_reason': 'no_progress',
                 'visual_recovery': {'schema_version': 1, 'owner': 'cli',
                     'state': 'recovery_limit_reached', 'receipts': [], 'observations': [],
                     'steps': [], 'original_code': 'OK', 'policy': 'readonly'}}}
    capability = continuation.batch_metadata(raw)['continuation']
    assert capability['available'] is True, 'no_progress 不再永久禁止（人工修复后本次再核验）'
    assert capability['reason'] == 'no_progress', '原停止原因作为诊断保留'
    monkeypatch.setattr(repository, 'get_invocation', lambda *a: parent(result_json=raw))
    canonical, failure = await continuation.prepare_next(kwargs())
    assert failure is None and canonical['exclude_names'] == ['甲先生']
    # 核验不过（如页面仍是坏的 → WRONG_PAGE 回执）→ 可重试，再「继续」仍放行
    assert repository.continuation_retry_safe(failed_next(
        cli_diagnostic_failure('WRONG_PAGE'))) is True
    canonical_again, failure_again = await continuation.prepare_next(kwargs())
    canonical_again, failure_again = await continuation.prepare_next(kwargs())
    assert failure_again is None and canonical_again['exclude_names'] == ['甲先生']


@pytest.mark.asyncio
@pytest.mark.parametrize('gate', [
    {'success': False}, {'effect': 'unknown'},
    {'data': {'job_name': None, 'stop_reason': 'no_progress', 'processed_names': ['甲先生']}},
    {'data': {'job_name': 'PHP开发', 'stop_reason': 'no_matching_candidates'}},
])
async def test_no_progress_relaxation_keeps_cursor_integrity_gates(gate):
    """门禁保留回归（双通道扩展覆盖 no_progress）：unsafe/missing_context/
    终局筛选不被 no_progress 放宽凌驾；history_limit 由既有
    test_missing_or_unsafe_context_cannot_offer_next_action 覆盖。"""
    raw = {'success': True, 'effect': 'none', 'invocation_id': PARENT,
        'data': {'job_name': 'PHP开发', 'processed_names': ['甲先生'],
                 'stop_reason': 'no_progress'}}
    if 'success' in gate:
        raw['success'] = gate['success']
    if 'effect' in gate:
        raw['effect'] = gate['effect']
    if 'data' in gate:
        raw['data'].update(gate['data'])
    capability = continuation.batch_metadata(raw)['continuation']
    assert capability['available'] is False
