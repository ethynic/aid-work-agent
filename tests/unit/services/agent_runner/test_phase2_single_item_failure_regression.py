"""Phase 2「错误分类与单份失败收敛」独立回归（测试会话，非开发自测）。

与开发自测的差异：不在 vl.evaluate_resume 一层打桩，而是 mock 最外层
llm_gateway.chat_direct，让分类、重试、单份收敛、批次继续、计费边界都在
真实业务代码路径上运行：

1. 三类失败路径（明确拒绝 / 无响应 / 返回不可解析）分别断言穿透/包装、
   单份失败状态、批次继续处理、计费账本只记实际回执（失败份 fee 不落）；
2. 无响应路径仍抛 LOCAL_MODEL_RESPONSE_VERIFICATION_REQUIRED（保护不放松）；
3. 明确拒绝第 2 次重试可达，最终 RESUME_VL_FAILED 单份跳过；
4. cloud_flow.read_original 纯读恢复可直接重读、意图变化可执行指引。
"""

import base64
import hashlib
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from PIL import Image

from src.llm.errors import ProviderHTTPError, ProviderResponseInvalid, provider_outcome
from src.llm.gateway import llm_gateway
from src.local_tools.durable_flow import LocalContinuationRequired
from src.services.agent_runner.local_owner import RunnerLocalLifecycle
from src.services.agent_runner.ownership import Attempt

pytestmark = pytest.mark.unit

ZHIPU_1210_BODY = '{"error":{"code":"1210","message":"图像内容无法解析"}}'


def rejection_400() -> ProviderHTTPError:
    """智谱 1210 明确拒绝形态：完整 HTTP 响应体带供应商错误码。"""
    return ProviderHTTPError(f"智谱GLM API请求失败: {ZHIPU_1210_BODY}", status_code=400)


# ============== 断言点 1/3：真实 chat_direct 路径的三分与重试 ==============


def _vl_ok_content(name: str) -> str:
    import json
    return json.dumps({"name_seen": name, "resume_summary": "资深后端工程师",
                       "score": 82, "match_summary": "技术栈匹配",
                       "key_info": {"education": "本科"}}, ensure_ascii=False)


@pytest.mark.asyncio
async def test_rejected_response_retries_twice_via_real_gateway_then_fails(monkeypatch):
    """明确拒绝（400/1210）：经真实 evaluate_resume 两次调用 chat_direct 后转单份失败。"""
    calls = []

    async def fake_chat_direct(provider, model, **kwargs):
        calls.append((provider, model))
        raise rejection_400()

    monkeypatch.setattr(llm_gateway, 'chat_direct', fake_chat_direct)
    from src.services.resume_vl_service import ResumeVLError, evaluate_resume
    with pytest.raises(ResumeVLError) as caught:
        await evaluate_resume(['aGVsbG8='], '王宣广')
    assert len(calls) == 2, '明确拒绝路径第 2 次重试必须可达（每次是新物理调用）'
    message = str(caught.value)
    assert 'rejected' in message and '1210' in message, '失败事实保留供应商分类与原始码'


@pytest.mark.asyncio
async def test_unparseable_response_retries_twice_then_fails(monkeypatch):
    """返回不可解析（200 但非 JSON 对象）：同明确拒绝处理，重试可达。"""
    calls = []

    async def fake_chat_direct(provider, model, **kwargs):
        calls.append(1)
        raise ProviderResponseInvalid('智谱GLM响应非 JSON 对象: null')

    monkeypatch.setattr(llm_gateway, 'chat_direct', fake_chat_direct)
    from src.services.resume_vl_service import ResumeVLError, evaluate_resume
    with pytest.raises(ResumeVLError) as caught:
        await evaluate_resume(['aGVsbG8='], '王宣广')
    assert len(calls) == 2
    assert 'unparseable' in str(caught.value)


# ============== 断言点 2：无响应保护未放松 ==============


def _bare_owner():
    """绕过 __init__ 门禁构造满足 model_phase 依赖的最小 owner。"""
    owner = object.__new__(RunnerLocalLifecycle)
    owner.control = owner.root = SimpleNamespace(
        durable_owner=True, attempt=Attempt('reg-runner', 'worker-1', 1),
        repository=SimpleNamespace(connection_factory=None), usage_scope=None)
    owner.state = SimpleNamespace(execution_id='reg-exec', identity=None, resources={})
    owner.call_id = 'reg-call'
    owner.saved_domain = AsyncMock(return_value=None)
    owner.start_domain = AsyncMock()
    owner.observe_domain = AsyncMock()
    return owner


async def _run_model_phase(invoke):
    return await RunnerLocalLifecycle.model_phase(_bare_owner(),
        branch='resume.evaluate', ordinal=0, intent={'invocation_id': 'inv-1'},
        invoke=invoke, purpose='resume_recognition_covered')


@pytest.mark.asyncio
@pytest.mark.parametrize('error', [
    httpx.ConnectTimeout('connection timed out'),
    httpx.ReadTimeout('read timed out'),
    httpx.RemoteProtocolError('server disconnected'),
])
async def test_no_response_family_keeps_verification_protection(error):
    """无响应/结果未知：一律包装 LOCAL_MODEL_RESPONSE_VERIFICATION_REQUIRED 且带阻断标记。"""

    async def invoke():
        raise error

    with pytest.raises(LocalContinuationRequired) as caught:
        await _run_model_phase(invoke)
    assert caught.value.code == 'LOCAL_MODEL_RESPONSE_VERIFICATION_REQUIRED'
    assert caught.value.tool_continuation_required is True, '防重复计费阻断标记不得丢失'


@pytest.mark.asyncio
async def test_no_response_does_not_retry_in_evaluate_resume(monkeypatch):
    """无响应异常在 evaluate_resume 立即抛出：第 2 次不可达（不放松防重复计费）。

    model_call 模拟 owner.model_phase 对无响应异常的包装行为（真实链路中
    model_phase 抛出该异常），绝不能触达真实 chat_direct。
    """
    from src.services.resume_vl_service import evaluate_resume
    calls = []

    async def chat_direct_must_not_run(*args, **kwargs):
        raise AssertionError('无响应保护测试不得发起真实供应商调用')

    monkeypatch.setattr(llm_gateway, 'chat_direct', chat_direct_must_not_run)

    def model_call(index, invoke):
        calls.append(index)
        raise LocalContinuationRequired('LOCAL_MODEL_RESPONSE_VERIFICATION_REQUIRED')

    with pytest.raises(LocalContinuationRequired) as caught:
        await evaluate_resume(['aGVsbG8='], '王宣广', model_call=model_call)
    assert caught.value.code == 'LOCAL_MODEL_RESPONSE_VERIFICATION_REQUIRED'
    assert calls == [0], '无响应类不得重试'


@pytest.mark.asyncio
async def test_provider_outcome_classification_boundaries():
    """分类器边界：HTTPError= rejected，ResponseInvalid= unparseable，其余 None。"""
    assert provider_outcome(rejection_400()) == 'rejected'
    assert provider_outcome(ProviderHTTPError('boom', status_code=500)) == 'rejected'
    assert provider_outcome(ProviderResponseInvalid('bad body')) == 'unparseable'
    assert provider_outcome(httpx.ConnectError('no route')) is None
    assert provider_outcome(TimeoutError()) is None
    assert provider_outcome(RuntimeError('anything')) is None


# ============== 断言点 1/3：端到端单份收敛 + 批次继续 + 计费边界 ==============


def _png_base64(width=8, height=6) -> str:
    buf = BytesIO()
    Image.new('RGB', (width, height)).save(buf, format='PNG')
    return base64.b64encode(buf.getvalue()).decode('ascii')


def _batch_owner():
    """真实 process_item 所需的最小 owner 桩；commit_domain 按 (branch, ordinal) 记账。"""
    fee_ledger = []

    async def saved_domain(**kw):
        return None

    async def complete_phase(**kw):
        return kw['result']

    async def model_phase(*, branch, ordinal, intent, invoke, purpose):
        return await invoke()

    async def commit_domain(**kw):
        fee_ledger.append((kw['branch'], kw['ordinal']))
        return {'resume.fee': {'credit_cost': 1, 'applied': True},
                'resume.insert': {'applied': True, 'resume_id': 1,
                                  'candidate_name': kw['intent']['candidate_name'],
                                  'image_count': 1},
                'resume.match': {'match_score': 80, 'match_status': 'matched',
                                 'match_summary': None}}[kw['branch']]

    owner = SimpleNamespace(
        saved_phase=AsyncMock(return_value={'request': {'device_id': 'reg-device'}}),
        saved_domain=AsyncMock(side_effect=saved_domain),
        complete_phase=AsyncMock(side_effect=complete_phase),
        model_phase=AsyncMock(side_effect=model_phase),
        commit_domain=AsyncMock(side_effect=commit_domain))
    return owner, fee_ledger


@pytest.mark.asyncio
async def test_batch_first_item_rejected_second_item_continues_with_fee_only_for_success(monkeypatch):
    """1210 单份失败：第 1 份 RESUME_VL_FAILED 跳过、第 2 份真实识别成功、fee 只记成功份。

    全链路使用真实 domain_flow.resume_result + resume_flow.process_item +
    evaluate_resume，仅 mock 最外层 chat_direct 与持久化/存储边界。
    """
    from src.local_tools import domain_flow, resume_flow
    from src.services import recruiting_resume_service as resumes
    import src.tools.context as context
    from src.local_tools.overlay_flow import run_base
    from src.services import resume_vl_service as vl

    image = _png_base64()
    attempt = {'count': 0}

    async def fake_chat_direct(provider, model, **kwargs):
        attempt['count'] += 1
        # 前两次调用（第 1 份的两次尝试）明确拒绝；之后（第 2 份）返回合法评估
        if attempt['count'] <= 2:
            raise rejection_400()
        return {'content': _vl_ok_content('乙先生'), 'usage': {}, 'provider': provider,
                'model': model, '_runner_receipt_id': 'receipt-x'}

    monkeypatch.setattr(llm_gateway, 'chat_direct', fake_chat_direct)
    monkeypatch.setattr(resume_flow, 'invalidate_domain_fee_cache', lambda *a, **k: None)
    monkeypatch.setattr(resumes, 'prepare_resume_record_from_tool_result',
        lambda tenant_id, payload: {'name': payload['candidate_name'], 'images_base64': []})
    monkeypatch.setattr(context, 'current_tool_execution_context', lambda: SimpleNamespace(
        tenant_id='reg-tenant', agent_execution_id='reg-exec', tool_call_id='reg-call'))
    monkeypatch.setattr('src.local_tools.overlay_flow.run_base', AsyncMock(return_value={
        'success': True, 'invocation_id': '00000000-0000-0000-0000-000000000001',
        'data': {'resumes': [
            {'candidate_name': '甲先生', 'images': [{'base64': image}]},
            {'candidate_name': '乙先生', 'images': [{'base64': image}]},
        ], 'failures': []}}))

    owner, fee_ledger = _batch_owner()
    operation = SimpleNamespace(branch='main', ordinal=0, owner=owner)
    from src.local_tools.proxy_tool import BossResumeBatchTool
    result = await domain_flow.resume_result(BossResumeBatchTool(), operation,
        {'_trusted_tenant_id': 'reg-tenant', '_trusted_user_id': 'reg-user',
         '_session_id': 'reg-session'}, batch=True)

    # 批次不终止且整体成功
    assert result['success'] is True
    # 第 1 份失败收敛为单份 RESUME_VL_FAILED 并保留供应商原始码 1210
    failures = result['data']['failures']
    assert failures and failures[0]['name'] == '甲先生'
    assert 'RESUME_VL_FAILED' in failures[0]['error'] or '1210' in failures[0]['error']
    assert '1210' in failures[0]['error'], '单份失败事实保留供应商原始码'
    # 第 2 份继续处理且真实识别成功（第 3 次 chat_direct 调用属第 2 份）
    assert attempt['count'] == 3, '第 1 份两次失败后第 2 份继续发起新物理调用'
    assert result['data']['resumes'][0]['candidate_name'] == '乙先生'
    # 计费账本只记实际回执：fee 只对成功份（item_ordinal=1）落账一次
    assert ('resume.fee', 1) in fee_ledger
    assert [entry for entry in fee_ledger if entry[0] == 'resume.fee' and entry[1] == 0] == [], \
        '失败份不得落识别费'


@pytest.mark.asyncio
async def test_process_item_rejected_path_returns_single_item_failure_without_fee(monkeypatch):
    """process_item 直测：两轮 1210 拒绝 → RESUME_VL_FAILED 单份结果，fee 不落账。"""
    from src.local_tools import resume_flow
    import src.tools.context as context

    async def fake_chat_direct(provider, model, **kwargs):
        raise rejection_400()

    monkeypatch.setattr(llm_gateway, 'chat_direct', fake_chat_direct)
    monkeypatch.setattr(context, 'current_tool_execution_context', lambda: SimpleNamespace(
        tenant_id='reg-tenant', agent_execution_id='reg-exec', tool_call_id='reg-call'))
    image = _png_base64()
    owner, fee_ledger = _batch_owner()
    result = await resume_flow.process_item(owner, tenant_id='reg-tenant',
        execution_id='reg-exec', tool_call_id='reg-call', invocation_id='inv-1',
        device_id='reg-device', item_ordinal=0,
        payload={'candidate_name': '甲先生', 'images': [{'base64': image}]})
    assert result['success'] is False and result['code'] == 'RESUME_VL_FAILED'
    assert '1210' in result['message'], '单份失败消息保留供应商原始码'
    assert fee_ledger == [], '失败发生在计费前，fee 不得落账'


# ============== 纯读恢复 ==============


def _read_owner(saved=None):
    return SimpleNamespace(
        saved_domain=AsyncMock(return_value=saved),
        complete_phase=AsyncMock(side_effect=lambda **kw: kw['result']))


@pytest.mark.asyncio
async def test_incomplete_pure_read_rereads_directly_without_verification_wait():
    """未完成纯读：允许直接重读并完成阶段，不进验证等待。"""
    from src.local_tools.cloud_flow import read_original
    saved = {'request': {'kind': 'jobs_list'}, 'phase': 'started', 'result': None}
    owner = _read_owner(saved)
    invoked = []

    async def invoke():
        invoked.append(1)
        return {'jobs': []}

    result = await read_original(owner, branch='jobs.list', intent={'kind': 'jobs_list'},
                                 invoke=invoke)
    assert result == {'jobs': []}
    assert invoked == [1], '纯读未完成必须直接重读'
    owner.complete_phase.assert_awaited_once()


@pytest.mark.asyncio
async def test_changed_read_intent_raises_actionable_code():
    """读取意图变化：抛 LOCAL_CLOUD_READ_INTENT_CHANGED（前端可执行指引），不重读。"""
    from src.local_tools.cloud_flow import read_original
    saved = {'request': {'kind': 'jobs_list', 'page': 1}, 'phase': 'completed',
             'result': {'jobs': ['old']}}
    owner = _read_owner(saved)

    async def invoke():
        raise AssertionError('意图变化不得沿用旧调用重读')

    with pytest.raises(LocalContinuationRequired) as caught:
        await read_original(owner, branch='jobs.list',
            intent={'kind': 'jobs_list', 'page': 2}, invoke=invoke)
    assert caught.value.code == 'LOCAL_CLOUD_READ_INTENT_CHANGED'


@pytest.mark.asyncio
async def test_completed_read_replays_saved_result_without_new_call():
    """已完成纯读：直接复用已保存结果，不发起新调用。"""
    from src.local_tools.cloud_flow import read_original
    saved = {'request': {'kind': 'jobs_list'}, 'phase': 'completed',
             'result': {'jobs': ['cached']}}

    async def invoke():
        raise AssertionError('已完成读取不得重复调用')

    result = await read_original(_read_owner(saved), branch='jobs.list',
        intent={'kind': 'jobs_list'}, invoke=invoke)
    assert result == {'jobs': ['cached']}
