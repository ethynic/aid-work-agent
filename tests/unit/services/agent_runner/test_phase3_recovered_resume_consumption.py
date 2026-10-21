"""Phase 3「恢复消费已取回简历成果」单元测试。

d470 场景：第二批简历已从页面取回（原 invocation succeeded）但 VL 识别在
「started + 不确定回执」位挂起。断言四件事：

1. 执行侧：已消耗尝试位抛 LOCAL_MODEL_ATTEMPT_ALREADY_CONSUMED（无阻断
   标记、不重放、无新 IO）；零回执位维持 rearm 重派语义不变；
2. 评估侧：已消耗位换下一尝试位（新物理调用 = 独立可追溯身份），两 位
   均耗尽收敛为单份 ResumeVLError（分类标注 already_consumed）；
3. 恢复证明：_model_proof 对 started + 归属核验通过的回执放行恢复，
   回执不属于本阶段派发（authorized_attempt 不匹配）仍拒绝；
4. 消费链路（domain_flow.resume_result + resume_flow.process_item +
   evaluate_resume 真实代码）：恢复复用原 invocation 轮询（不重新选取
   设备/派发抓取）、已入库份全链幂等复用（零新 VL 调用、fee 不重复落
   账）、挂起份从已消耗位换新尝试位（1210 仍失败 → 单份跳过）、未处理
   份正常识别入库、批次正常终局。
"""

import base64
import hashlib
import json
from contextlib import contextmanager
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from PIL import Image

from src.llm.errors import ProviderHTTPError
from src.llm.gateway import llm_gateway
from src.local_tools.durable_flow import LocalModelAttemptConsumed
from src.services.agent_runner.local_owner import RunnerLocalLifecycle
from src.services.agent_runner.ownership import Attempt

pytestmark = pytest.mark.unit

ZHIPU_1210_BODY = '{"error":{"code":"1210","message":"图像内容无法解析"}}'

INVOCATION_ID = '00000000-0000-0000-0000-0000000000d4'


def rejection_400() -> ProviderHTTPError:
    return ProviderHTTPError(f"智谱GLM API请求失败: {ZHIPU_1210_BODY}", status_code=400)


def _png_base64(width=8, height=6) -> str:
    buf = BytesIO()
    Image.new('RGB', (width, height)).save(buf, format='PNG')
    return base64.b64encode(buf.getvalue()).decode('ascii')


def _vl_ok_content(name: str) -> str:
    return json.dumps({"name_seen": name, "resume_summary": "资深后端工程师",
                       "score": 82, "match_summary": "技术栈匹配",
                       "key_info": {"education": "本科"}}, ensure_ascii=False)


# ============== 1. 执行侧：model_phase 对已消耗尝试位的语义 ==============

_MODEL_INTENT = {'invocation_id': 'inv-1', 'model_purpose': 'resume_recognition_covered',
                 'model_phase_version': 1}


def _owner_with_receipt(receipt_row):
    """绕过 __init__ 门禁构造满足 model_phase 依赖的 owner。

    saved_domain 返回 started 事实；connection_factory 提供 usage receipt
    查询结果（None=零回执，dict=已派发回执）。
    """
    owner = object.__new__(RunnerLocalLifecycle)
    conn = MagicMock()
    conn.cursor.return_value.fetchone.return_value = receipt_row

    @contextmanager
    def connection_factory():
        yield conn

    owner.control = owner.root = SimpleNamespace(
        durable_owner=True, attempt=Attempt('runner-1', 'worker-1', 2),
        repository=SimpleNamespace(connection_factory=lambda: connection_factory()),
        usage_scope=None)
    owner.state = SimpleNamespace(execution_id='exec-1', identity=None, resources={})
    owner.call_id = 'call-1'
    owner.saved_domain = AsyncMock(return_value={
        'request': dict(_MODEL_INTENT), 'phase': 'started', 'authorized_attempt': 1,
        'result': None})
    owner._domain = AsyncMock()
    owner.observe_domain = AsyncMock(return_value={'content': 'ok'})
    return owner


@pytest.mark.asyncio
async def test_consumed_attempt_raises_business_exception_without_replay():
    """重入 started+已有回执的尝试位：抛已消耗异常，不重放、无新 IO、无阻断标记。"""
    owner = _owner_with_receipt({'receipt_id': 'r-1', 'phase': 'unknown',
                                 'authorized_attempt': 1})
    invoke = AsyncMock(return_value={'content': 'x', '_runner_receipt_id': 'r-2'})
    with pytest.raises(LocalModelAttemptConsumed) as caught:
        await RunnerLocalLifecycle.model_phase(owner, branch='resume.evaluate',
            ordinal=2, intent={'invocation_id': 'inv-1'}, invoke=invoke,
            purpose='resume_recognition_covered')
    assert caught.value.code == 'LOCAL_MODEL_ATTEMPT_ALREADY_CONSUMED'
    # 无阻断标记：上层评估循环可换下一尝试位（新物理调用），不进整任务验证等待
    assert caught.value.attempt_consumed is True
    assert not getattr(caught.value, 'tool_continuation_required', False)
    # 已消耗位不得重放：不发起任何新物理调用，也不走 rearm
    invoke.assert_not_called()
    owner._domain.assert_not_called()


@pytest.mark.asyncio
async def test_unissued_started_attempt_keeps_rearm_redispatch():
    """零回执的 started 位维持既有 rearm 语义：同 ordinal 作为新物理调用重派。"""
    owner = _owner_with_receipt(None)
    invoke = AsyncMock(return_value={'content': 'ok', 'usage': {}, 'provider': 'zhipu',
                                     'model': 'glm', '_runner_receipt_id': 'r-2'})
    result = await RunnerLocalLifecycle.model_phase(owner, branch='resume.evaluate',
        ordinal=2, intent={'invocation_id': 'inv-1'}, invoke=invoke,
        purpose='resume_recognition_covered')
    assert result == {'content': 'ok'}
    invoke.assert_awaited_once()
    rearm_calls = [call for call in owner._domain.call_args_list
                   if call.kwargs.get('rearm_model') is True]
    assert len(rearm_calls) == 1, '零回执位必须经 rearm 授权后重派（防重复计费门槛不放松）'


# ============== 2. 评估侧：已消耗位换下一尝试位 / 双位耗尽单份收敛 ==============


@pytest.mark.asyncio
async def test_consumed_first_slot_moves_to_next_ordinal_then_succeeds(monkeypatch):
    """第 1 尝试位已消耗 → 换第 2 尝试位（独立 ordinal 新物理调用）识别成功。"""
    from src.services.resume_vl_service import evaluate_resume
    calls = []

    async def fake_chat_direct(provider, model, **kwargs):
        return {'content': _vl_ok_content('乙先生'), 'usage': {}, 'provider': provider,
                'model': model, '_runner_receipt_id': 'receipt-x'}

    monkeypatch.setattr(llm_gateway, 'chat_direct', fake_chat_direct)

    def model_call(index, invoke):
        calls.append(index)
        if index == 0:
            raise LocalModelAttemptConsumed()
        return invoke()

    result = await evaluate_resume(['aGVsbG8='], '乙先生', model_call=model_call)
    assert calls == [0, 1], '已消耗位必须推进到下一尝试位'
    assert result['name_seen'] == '乙先生'


@pytest.mark.asyncio
async def test_both_slots_consumed_converges_to_single_item_failure():
    """两个尝试位均已消耗：收敛为单份 ResumeVLError，分类标注 already_consumed。"""
    from src.services.resume_vl_service import ResumeVLError, evaluate_resume
    calls = []

    def model_call(index, invoke):
        calls.append(index)
        raise LocalModelAttemptConsumed()

    with pytest.raises(ResumeVLError) as caught:
        await evaluate_resume(['aGVsbG8='], '乙先生', model_call=model_call)
    assert calls == [0, 1]
    assert 'already_consumed' in str(caught.value), '失败事实须可审计地区分已消耗重入'


@pytest.mark.asyncio
async def test_consumed_then_rejected_keeps_original_provider_code():
    """已消耗位 + 新位 1210 拒绝：单份失败保留供应商原始码，逐次失败分类可审计。"""
    import io
    from loguru import logger as loguru_logger
    from src.services.resume_vl_service import ResumeVLError, evaluate_resume
    calls = []

    def model_call(index, invoke):
        calls.append(index)
        if index == 0:
            raise LocalModelAttemptConsumed()
        raise rejection_400()

    sink = io.StringIO()
    handler_id = loguru_logger.add(sink, level='WARNING')
    try:
        with pytest.raises(ResumeVLError) as caught:
            await evaluate_resume(['aGVsbG8='], '乙先生', model_call=model_call)
    finally:
        loguru_logger.remove(handler_id)
    message = str(caught.value)
    # 最终失败事实保留最后一次（新位）供应商原始码与分类
    assert 'rejected' in message and '1210' in message
    # 每次失败逐次分类标注：第一次重入已消耗位、第二次供应商明确拒绝
    logged = sink.getvalue()
    assert 'provider_result=already_consumed' in logged
    assert 'provider_result=rejected' in logged


# ============== 3. 恢复证明：_model_proof 对 started+回执的放行与拒绝 ==============


def _proof_fixture(receipt_row):
    """构造 _model_proof 直测所需的最小 row/state/fact/phase。

    assert_resume_model 打桩为 no-op（其内部证明由集成测试覆盖）；只验证
    _model_proof 自身对回执归属的判定。
    """
    from src.services.agent_runner.local_invocations import LocalPhase
    state = SimpleNamespace(
        execution_id='exec-1',
        resources={'local_domain_phases': {
            LocalPhase('exec-1', 'call-1', 'resume.policy', 1).key('runner-1'): {
                'phase': 'completed', 'result': {'model_spec': 'zhipu/GLM-5.3-Flash'}}}},
        checkpoint=lambda: {'execution_id': 'exec-1', 'identity': {}, 'tools': {},
                            'resources': {}})
    row = {'runner_id': 'runner-1', 'attempt': 2}
    fact = SimpleNamespace(call=SimpleNamespace(id='call-1'))
    request = {'invocation_id': INVOCATION_ID, 'item_ordinal': 1,
               'candidate_name': '乙先生', 'image_digest': 'd' * 64,
               'model_purpose': 'resume_recognition_covered', 'model_phase_version': 1}
    phase = {'branch': 'resume.evaluate', 'ordinal': 3, 'request': request,
             'phase': 'started', 'authorized_attempt': 1}
    key = LocalPhase('exec-1', 'call-1', 'resume.evaluate', 3).key('runner-1')
    return row, state, fact, key, phase, receipt_row


async def _run_model_proof(monkeypatch, receipt_row):
    from src.services.agent_runner import domain_recovery, resume_model_proof
    from src.local_tools import repository as invocation_repository
    monkeypatch.setattr(resume_model_proof, 'assert_resume_model',
                        lambda *args, **kwargs: None)
    conn = MagicMock()

    @contextmanager
    def connection_factory():
        yield conn

    monkeypatch.setattr(invocation_repository, 'get_db_connection', connection_factory)
    conn.cursor.return_value.fetchone.return_value = receipt_row
    row, state, fact, key, phase, _ = _proof_fixture(receipt_row)
    return domain_recovery._model_proof(row, state, fact, key, phase)


@pytest.mark.asyncio
@pytest.mark.parametrize('receipt,expected', [
    # 归属核验通过的已派发回执（uncertain/observed）放行恢复：该位不重放，
    # 恢复沿下一尝试位消费已取回成果
    ({'authorized_attempt': 1, 'phase': 'unknown'}, True),
    ({'authorized_attempt': 1, 'phase': 'observed'}, True),
    # 零回执：维持既有 rearm 重派放行
    (None, True),
    # 回执不属于本阶段原派发 attempt / 非法回执状态：保持拒绝
    ({'authorized_attempt': 9, 'phase': 'unknown'}, False),
    ({'authorized_attempt': 1, 'phase': 'revoked'}, False),
])
async def test_model_proof_started_receipt_gate(monkeypatch, receipt, expected):
    assert await _run_model_proof(monkeypatch, receipt) is expected


# ============== 4. 消费链路：恢复复用原 invocation，逐份消费，不重抓不重计费 ==============


def _recovery_owner(image, names):
    """d470 挂起批次的 owner 桩：item0 已入库、item1 挂起、item2 未处理。

    saved_domain/saved_phase 返回挂起前事实；model_phase 内嵌已消耗尝试位
    语义（item1 的首个尝试位已持有不确定回执）；commit_domain 对 completed
    事实幂等复用（不重复落账），新落账记入 fee_ledger。
    """
    origins = {}
    for ordinal, name in enumerate(names):
        origins[ordinal] = {'invocation_id': INVOCATION_ID, 'item_ordinal': ordinal,
                            'candidate_name': name,
                            'image_digest': hashlib.sha256(image.encode()).hexdigest()}
    policy_result = {'job_context': None, 'credit_cost': 1,
                     'model_spec': 'zhipu/GLM-5.3-Flash'}
    saved_phases = {
        # item0（前一批已入库语义）：全链 completed，恢复仅复用
        ('resume.policy', 0): {'request': {**origins[0], 'resume_policy_version': 1},
            'phase': 'completed', 'result': policy_result},
        ('resume.evaluation', 0): {'request': dict(origins[0]), 'phase': 'completed',
            'result': {'name_seen': names[0], 'resume_summary': '已识别摘要',
                       'score': 88, 'match_summary': None, 'key_info': None,
                       'usage': None, 'model': 'GLM-5.3-Flash'}},
        ('resume.fee', 0): {'request': {}, 'phase': 'completed',
            'result': {'credit_cost': 1, 'applied': True}},
        ('resume.insert', 0): {'request': dict(origins[0]), 'phase': 'completed',
            'result': {'applied': True, 'resume_id': 101, 'candidate_name': names[0],
                       'image_count': 1, 'job_name': None}},
        ('resume.match', 0): {'request': {}, 'phase': 'completed',
            'result': {'match_score': 88, 'match_status': 'matched',
                       'match_summary': None}},
        # item1（挂起份）：仅 policy 已冻结，识别在已消耗尝试位挂起
        ('resume.policy', 1): {'request': {**origins[1], 'resume_policy_version': 1},
            'phase': 'completed', 'result': policy_result},
    }
    consumed_slots = {('resume.evaluate', 1 * 2 + 0)}  # item1 首个尝试位已持有回执
    fee_ledger = []
    vl_new_calls = []

    async def saved_domain(**kw):
        return saved_phases.get((kw['branch'], kw['ordinal']))

    async def saved_phase(**kw):
        return {'request': {'device_id': 'device-original', 'arguments': {},
                            'tool_name': 'boss_resume_batch', 'provider_key': None},
                'invocation': {'id': INVOCATION_ID,
                               'deadline_at': '2126-01-01T00:00:00+00:00'}}

    async def complete_phase(**kw):
        saved_phases[(kw['branch'], kw['ordinal'])] = {
            'request': kw['intent'], 'phase': 'completed', 'result': kw['result']}
        return kw['result']

    async def model_phase(*, branch, ordinal, intent, invoke, purpose):
        if (branch, ordinal) in consumed_slots:
            raise LocalModelAttemptConsumed()
        vl_new_calls.append((branch, ordinal))
        return await invoke()

    async def commit_domain(**kw):
        saved = saved_phases.get((kw['branch'], kw['ordinal']))
        if saved is not None and saved.get('phase') == 'completed':
            # 真实 commit_domain 幂等复用已完成事实：不重新执行 writer、不重复落账
            return saved['result']
        fee_ledger.append((kw['branch'], kw['ordinal']))
        return {
            'resume.fee': {'credit_cost': 1, 'applied': True},
            'resume.insert': {'applied': True, 'resume_id': 202,
                              'candidate_name': kw['intent']['candidate_name'],
                              'image_count': 1, 'job_name': None},
            'resume.match': {'match_score': 80, 'match_status': 'matched',
                             'match_summary': None},
        }[kw['branch']]

    owner = SimpleNamespace(
        saved_phase=AsyncMock(side_effect=saved_phase),
        saved_domain=AsyncMock(side_effect=saved_domain),
        complete_phase=AsyncMock(side_effect=complete_phase),
        model_phase=AsyncMock(side_effect=model_phase),
        commit_domain=AsyncMock(side_effect=commit_domain))
    return owner, fee_ledger, vl_new_calls


@pytest.mark.asyncio
async def test_recovery_consumes_fetched_batch_without_redispatch_or_duplicate_fee(monkeypatch):
    """1210 挂起 → 恢复：复用原 invocation、消费 3 份、失败份跳过、正常终局。

    全链路走真实 domain_flow.resume_result + resume_flow.process_item +
    evaluate_resume，仅 mock 最外层 chat_direct 与持久化/存储边界。
    """
    from src.local_tools import domain_flow, resume_flow
    from src.services import recruiting_resume_service as resumes
    from src.services import resume_vl_service as vl
    import src.tools.context as context

    image = _png_base64()
    names = ['甲先生', '乙先生', '丙先生']
    monkeypatch.setattr(vl, 'resolve_model_spec', lambda *a, **k: ('zhipu', 'GLM-5.3-Flash'))
    monkeypatch.setattr(resume_flow, 'invalidate_domain_fee_cache', lambda *a, **k: None)
    monkeypatch.setattr(resumes, 'prepare_resume_record_from_tool_result',
        lambda tenant_id, payload: {'name': payload['candidate_name'], 'images_base64': []})
    monkeypatch.setattr(context, 'current_tool_execution_context', lambda: SimpleNamespace(
        tenant_id='tenant-3', agent_execution_id='exec-3', tool_call_id='call-3'))

    chat_calls = []

    async def fake_chat_direct(provider, model, **kwargs):
        chat_calls.append(kwargs.get('temperature'))
        # 第 1 次新物理调用：item1（乙先生）的下一尝试位仍被 1210 拒绝
        if len(chat_calls) == 1:
            raise rejection_400()
        # 第 2 次：item2（丙先生）正常识别
        return {'content': _vl_ok_content('丙先生'), 'usage': {}, 'provider': provider,
                'model': model, '_runner_receipt_id': 'receipt-new'}

    monkeypatch.setattr(llm_gateway, 'chat_direct', fake_chat_direct)

    owner, fee_ledger, vl_new_calls = _recovery_owner(image, names)

    from src.local_tools.proxy_tool import BossResumeBatchTool
    proxy = BossResumeBatchTool()
    original = {'success': True, 'invocation_id': INVOCATION_ID,
                'data': {'resumes': [{'candidate_name': name, 'images': [{'base64': image}]}
                                     for name in names], 'failures': []}}
    # 恢复语义：只允许轮询原 invocation；重新选设备/派发抓取即失败
    proxy._poll_invocation = AsyncMock(return_value=original)
    proxy._find_ready_device = AsyncMock(side_effect=AssertionError('恢复不得重新选取设备'))
    proxy._dispatch_and_wait = AsyncMock(side_effect=AssertionError('恢复不得重新派发抓取'))

    operation = SimpleNamespace(branch='main', ordinal=0, owner=owner)
    result = await domain_flow.resume_result(proxy, operation,
        {'_trusted_tenant_id': 'tenant-3', '_trusted_user_id': 'user-3',
         '_session_id': 'session-3'}, batch=True)

    # 任务正常终局：批次成功，失败份按策略跳过
    assert result['success'] is True
    assert [item['candidate_name'] for item in result['data']['resumes']] == ['甲先生', '丙先生']
    failures = result['data']['failures']
    assert len(failures) == 1 and failures[0]['name'] == '乙先生'
    assert '1210' in failures[0]['error'], '单份失败事实保留供应商原始码'

    # 不重新抓取：只轮询原 invocation，零次设备选取/派发
    proxy._poll_invocation.assert_awaited_once()
    proxy._find_ready_device.assert_not_awaited()
    proxy._dispatch_and_wait.assert_not_awaited()

    # 不重复计费：新物理 VL 调用仅 2 次（乙的重试位 + 丙的首位），甲零调用
    assert len(chat_calls) == 2
    assert vl_new_calls == [('resume.evaluate', 3), ('resume.evaluate', 4)], \
        '新视觉调用必须落在独立可追溯的新尝试位上'
    # fee 只对真实新识别的丙落账；甲的 fee 幂等复用不重复入账；乙失败不落费
    assert [entry for entry in fee_ledger if entry[0] == 'resume.fee'] == [('resume.fee', 2)]
    # 甲的入库记录原样保留（幂等复用，不重新 insert）
    assert ('resume.insert', 0) not in fee_ledger
    assert ('resume.insert', 2) in fee_ledger


@pytest.mark.asyncio
async def test_recovery_item_fails_when_both_slots_consumed(monkeypatch):
    """挂起份两个尝试位均已持有回执：恢复后该份直接单份跳过，批次仍终局。"""
    from src.local_tools import domain_flow, resume_flow
    from src.services import recruiting_resume_service as resumes
    from src.services import resume_vl_service as vl
    import src.tools.context as context

    image = _png_base64()
    names = ['甲先生', '乙先生']
    monkeypatch.setattr(vl, 'resolve_model_spec', lambda *a, **k: ('zhipu', 'GLM-5.3-Flash'))
    monkeypatch.setattr(resume_flow, 'invalidate_domain_fee_cache', lambda *a, **k: None)
    monkeypatch.setattr(resumes, 'prepare_resume_record_from_tool_result',
        lambda tenant_id, payload: {'name': payload['candidate_name'], 'images_base64': []})
    monkeypatch.setattr(context, 'current_tool_execution_context', lambda: SimpleNamespace(
        tenant_id='tenant-3', agent_execution_id='exec-3', tool_call_id='call-3'))
    monkeypatch.setattr(llm_gateway, 'chat_direct', AsyncMock(
        side_effect=AssertionError('双位耗尽场景不得发起新供应商调用')))

    owner, fee_ledger, vl_new_calls = _recovery_owner(image, names)
    # 模拟两轮都曾派发：item1 的两个尝试位均已持有不确定回执
    owner.model_phase = AsyncMock(side_effect=LocalModelAttemptConsumed())

    from src.local_tools.proxy_tool import BossResumeBatchTool
    proxy = BossResumeBatchTool()
    original = {'success': True, 'invocation_id': INVOCATION_ID,
                'data': {'resumes': [{'candidate_name': name, 'images': [{'base64': image}]}
                                     for name in names], 'failures': []}}
    proxy._poll_invocation = AsyncMock(return_value=original)
    proxy._find_ready_device = AsyncMock(side_effect=AssertionError('不得重新抓取'))
    proxy._dispatch_and_wait = AsyncMock(side_effect=AssertionError('不得重新派发'))

    operation = SimpleNamespace(branch='main', ordinal=0, owner=owner)
    result = await domain_flow.resume_result(proxy, operation,
        {'_trusted_tenant_id': 'tenant-3', '_trusted_user_id': 'user-3',
         '_session_id': 'session-3'}, batch=True)

    assert result['success'] is True
    assert [item['candidate_name'] for item in result['data']['resumes']] == ['甲先生']
    failures = result['data']['failures']
    assert len(failures) == 1 and failures[0]['name'] == '乙先生'
    assert 'already_consumed' in failures[0]['error'], '失败事实含已消耗重入分类'
    assert vl_new_calls == [] and fee_ledger == [], '无新调用、无新落账'


# ============== 5. 波及面：overlay heal（purpose='llm'）对已消耗尝试位的收敛 ==============


@pytest.mark.asyncio
async def test_overlay_heal_consumed_slot_advances_next_attempt_then_succeeds(monkeypatch):
    """heal.choice 首位已消耗：换下一尝试位（新物理调用）后正常选出关闭文本。"""
    from src.services import overlay_heal_service
    monkeypatch.setattr(overlay_heal_service, 'record_background_llm_usage', lambda *a, **k: None)
    monkeypatch.setattr(llm_gateway, 'get_model_name', lambda: 'test-model')

    async def fake_chat_no_thinking(**kwargs):
        return {'content': '{"found": true, "text": "关闭"}', 'usage': None}

    monkeypatch.setattr(llm_gateway, 'chat_no_thinking', fake_chat_no_thinking)
    calls = []

    def model_call(index, invoke):
        calls.append(index)
        if index == 0:
            raise LocalModelAttemptConsumed()
        return invoke()

    text = await overlay_heal_service.pick_dismiss_text_with_llm(
        'tenant', 'user', candidates=[{'text': '关闭'}], model_call=model_call)
    assert calls == [0, 1], '已消耗位推进到下一尝试位，不重放、不进验证等待'
    assert text == '关闭'


@pytest.mark.asyncio
async def test_overlay_heal_both_slots_consumed_gives_up_gracefully(monkeypatch):
    """heal.choice 两位均已消耗：自愈优雅放弃返回 None，零新物理调用。"""
    from src.services import overlay_heal_service
    monkeypatch.setattr(overlay_heal_service, 'record_background_llm_usage', lambda *a, **k: None)
    monkeypatch.setattr(llm_gateway, 'get_model_name', lambda: 'test-model')
    monkeypatch.setattr(llm_gateway, 'chat_no_thinking', AsyncMock(
        side_effect=AssertionError('双位耗尽不得发起新供应商调用')))
    calls = []

    def model_call(index, invoke):
        calls.append(index)
        raise LocalModelAttemptConsumed()

    text = await overlay_heal_service.pick_dismiss_text_with_llm(
        'tenant', 'user', candidates=[{'text': '关闭'}], model_call=model_call)
    assert calls == [0, 1]
    assert text is None, '自愈放弃而非挂起/阻断'
