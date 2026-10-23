"""model_phase 供应商结果三分与单份失败收敛的单元测试（计划 Phase 2）。

三类失败路径（明确拒绝 / 无响应 / 返回不可解析）分别断言：
- 穿透/包装行为：有响应类以可识别业务异常穿透 model_phase（保留原始码与描述），
  无响应类维持 LOCAL_MODEL_RESPONSE_VERIFICATION_REQUIRED 验证保护（防重复计费不放松）；
- evaluate_resume 重试可达性：有响应类第 2 次重试可达，无响应类立即抛出不重试；
- 单份收敛：仍失败走 resume_flow._failure('RESUME_VL_FAILED')，批次继续下一份；
"""

import base64
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from PIL import Image

from src.core.agent_engine.contracts import CheckpointFailure
from src.llm.errors import ProviderHTTPError, ProviderResponseInvalid, provider_outcome
from src.local_tools.durable_flow import LocalContinuationRequired
from src.services.agent_runner.local_owner import RunnerLocalLifecycle
from src.services.agent_runner.ownership import Attempt

pytestmark = pytest.mark.unit

# 智谱 1210 拒绝的真实形态：完整 HTTP 响应体带供应商错误码
ZHIPU_1210_BODY = '{"error":{"code":"1210","message":"图像内容无法解析"}}'


def http_400_1210() -> ProviderHTTPError:
    return ProviderHTTPError(f"智谱GLM API请求失败: {ZHIPU_1210_BODY}", status_code=400)


def no_response_error() -> Exception:
    # httpx 连接/超时族：请求未收到完整响应
    return httpx.ConnectTimeout('connection timed out')


# ============== provider_outcome 三分分类 ==============

@pytest.mark.parametrize('error,expected', [
    (http_400_1210(), 'rejected'),
    (ProviderHTTPError('server error', status_code=500), 'rejected'),
    (ProviderResponseInvalid('智谱GLM响应非 JSON 对象: null'), 'unparseable'),
    (no_response_error(), None),
    (httpx.ReadError('connection broken mid-body'), None),
    (TimeoutError(), None),
    (RuntimeError('未配置 API Key'), None),
])
def test_provider_outcome_classifies_three_ways(error, expected):
    assert provider_outcome(error) == expected


# ============== model_phase 三分穿透/包装 ==============

def _owner():
    """绕过 __init__ 的门禁构造一个只满足 model_phase 依赖的 owner。"""
    owner = object.__new__(RunnerLocalLifecycle)
    owner.control = owner.root = SimpleNamespace(
        durable_owner=True, attempt=Attempt('runner-1', 'worker-1', 1),
        repository=SimpleNamespace(connection_factory=None), usage_scope=None)
    owner.state = SimpleNamespace(execution_id='exec-1', identity=None, resources={})
    owner.call_id = 'call-1'
    owner.saved_domain = AsyncMock(return_value=None)
    owner.start_domain = AsyncMock()
    owner.observe_domain = AsyncMock()
    return owner


async def _model_phase(error=None, returns='__unset__', invoke=None):
    owner = _owner()
    if invoke is None:
        async def invoke():
            if error is not None:
                raise error
            return returns
    return await RunnerLocalLifecycle.model_phase(owner, branch='resume.evaluate',
        ordinal=0, intent={'invocation_id': 'inv-1'}, invoke=invoke,
        purpose='resume_recognition_covered')


@pytest.mark.asyncio
async def test_explicit_rejection_penetrates_with_original_code():
    """明确拒绝（完整 4xx/1210 响应）：业务异常穿透，保留原始码与描述。"""
    with pytest.raises(ProviderHTTPError) as caught:
        await _model_phase(error=http_400_1210())
    assert caught.value.status_code == 400
    assert '1210' in str(caught.value)
    # 无 tool_continuation_required 标记：不会被误升级为整任务验证等待
    assert not getattr(caught.value, 'tool_continuation_required', False)


@pytest.mark.asyncio
async def test_unparseable_provider_response_penetrates():
    """返回不可解析（有响应但内容无效）：同明确拒绝处理。"""
    with pytest.raises(ProviderResponseInvalid):
        await _model_phase(error=ProviderResponseInvalid('智谱GLM响应非 JSON 对象: null'))
    assert not getattr(ProviderResponseInvalid('x'), 'tool_continuation_required', False)


@pytest.mark.asyncio
async def test_non_dict_response_is_auditable_single_item_failure():
    """invoke 返回非 dict：有响应但内容无效 → 可识别业务异常，不再包装验证等待。"""
    with pytest.raises(ProviderResponseInvalid) as caught:
        await _model_phase(returns='not-a-dict')
    assert 'LOCAL_MODEL_RESPONSE_NOT_AN_OBJECT' in str(caught.value)


@pytest.mark.asyncio
async def test_no_response_keeps_verification_protection():
    """无响应/结果未知：保留 LOCAL_MODEL_RESPONSE_VERIFICATION_REQUIRED 验证保护。"""
    with pytest.raises(LocalContinuationRequired) as caught:
        await _model_phase(error=no_response_error())
    assert caught.value.code == 'LOCAL_MODEL_RESPONSE_VERIFICATION_REQUIRED'
    # 防重复计费不放松：该标记阻断同阶段重派与上层立即重试
    assert caught.value.tool_continuation_required is True


@pytest.mark.asyncio
async def test_authoritative_storage_failure_still_passes_through_unwrapped():
    failure = CheckpointFailure('LOCAL_PHASE_STORAGE_FAILED')
    failure.authoritative_storage_failure = True
    with pytest.raises(CheckpointFailure) as caught:
        await _model_phase(error=failure)
    assert caught.value is failure


# ============== evaluate_resume 重试可达性（经 model_call 的 Runner 路径） ==============

BANDS = ['aGVsbG8=']


def _model_call_failures(errors):
    """构造 owner.model_call 桩：按次序抛出给定异常，记录每次调用的 ordinal。"""
    calls = []

    def factory(index, invoke):
        calls.append(index)
        raise errors[index]
    return factory, calls


@pytest.mark.asyncio
async def test_explicit_rejection_retry_reachable_then_single_item_failure(monkeypatch):
    """第 1 次 400/1210 → 第 2 次重试可达（新物理调用）→ 仍失败抛 ResumeVLError。"""
    from src.services import resume_vl_service as vl
    model_call, calls = _model_call_failures([http_400_1210(), http_400_1210()])
    with pytest.raises(vl.ResumeVLError) as caught:
        await vl.evaluate_resume(BANDS, '王宣广', model_call=model_call)
    assert calls == [0, 1], 'Runner 路径上明确拒绝必须能走到第 2 次重试'
    message = str(caught.value)
    assert 'rejected' in message and '1210' in message, '单份失败事实须保留供应商错误分类与原始码'


@pytest.mark.asyncio
async def test_unparseable_retry_reachable_then_single_item_failure():
    from src.services import resume_vl_service as vl
    model_call, calls = _model_call_failures([
        ProviderResponseInvalid('LOCAL_MODEL_RESPONSE_NOT_AN_OBJECT'),
        ProviderResponseInvalid('智谱GLM响应非 JSON 对象: null')])
    with pytest.raises(vl.ResumeVLError) as caught:
        await vl.evaluate_resume(BANDS, '王宣广', model_call=model_call)
    assert calls == [0, 1]
    assert 'unparseable' in str(caught.value)


@pytest.mark.asyncio
async def test_no_response_never_retries_on_runner_path():
    """无响应（验证保护异常带 tool_continuation_required）：立即抛出，第 2 次不可达。"""
    from src.services import resume_vl_service as vl
    model_call, calls = _model_call_failures([
        LocalContinuationRequired('LOCAL_MODEL_RESPONSE_VERIFICATION_REQUIRED')] * 2)
    with pytest.raises(LocalContinuationRequired) as caught:
        await vl.evaluate_resume(BANDS, '王宣广', model_call=model_call)
    assert caught.value.code == 'LOCAL_MODEL_RESPONSE_VERIFICATION_REQUIRED'
    assert calls == [0], '无响应类不得重试，防重复计费'


# ============== 单份收敛：批次继续下一份 ==============

def _png_base64(width=4, height=4) -> str:
    img = Image.new('RGB', (width, height))
    buf = BytesIO()
    img.save(buf, format='PNG')
    return base64.b64encode(buf.getvalue()).decode('ascii')


def _recruiting_context(monkeypatch, *, vl_side_effect=None, candidate='甲先生', item_ordinal=0):
    """为 resume_flow.process_item / domain_flow.resume_result 提供最小桩。"""
    import hashlib
    from src.local_tools import domain_flow, resume_flow
    from src.services import resume_vl_service as vl
    if vl_side_effect is not None:
        monkeypatch.setattr(vl, 'evaluate_resume', AsyncMock(side_effect=vl_side_effect))
    payload_image = _png_base64()
    origin = {'invocation_id': 'inv-1', 'item_ordinal': item_ordinal,
        'candidate_name': candidate,
        'image_digest': hashlib.sha256(payload_image.encode()).hexdigest()}
    policy_fact = {'request': {**origin, 'resume_policy_version': 1}, 'phase': 'completed',
        'result': {'job_context': None, 'credit_cost': 1, 'model_spec': 'zhipu/GLM-5.3-Flash'}}
    owner = SimpleNamespace(
        saved_domain=AsyncMock(side_effect=lambda **kw: (
            policy_fact if kw['branch'] == 'resume.policy' else None)),
        complete_phase=AsyncMock(side_effect=lambda **kw: kw['result']),
        model_phase=AsyncMock(side_effect=vl_side_effect),
        commit_domain=AsyncMock(side_effect=lambda **kw: {'credit_cost': 1, 'applied': True, 'resume_id': 1}),
    )
    import src.tools.context as context
    import src.local_tools.overlay_flow as overlay_flow
    monkeypatch.setattr(context, 'current_tool_execution_context', lambda: SimpleNamespace(
        tenant_id='tenant', agent_execution_id='exec-1', tool_call_id='call-1'))
    original = {'success': True, 'invocation_id': 'inv-1',
        'data': {'resumes': [
            {'candidate_name': candidate, 'images': [{'base64': payload_image}]},
            {'candidate_name': '乙先生', 'images': [{'base64': payload_image}]},
        ]}}
    return domain_flow, resume_flow, overlay_flow, owner, original


@pytest.mark.asyncio
async def test_single_item_vl_failure_skips_and_batch_continues(monkeypatch):
    """1210 明确拒绝收敛为单份 RESUME_VL_FAILED，乙先生继续处理，批次不终止。"""
    from src.services.resume_vl_service import ResumeVLError
    domain_flow, resume_flow, overlay_flow, owner, original = _recruiting_context(
        monkeypatch, vl_side_effect=ResumeVLError(
            '简历 VL 评估失败（重试后仍失败）：LLM 调用失败[rejected]: ProviderHTTPError: '
            '智谱GLM API请求失败: {"error":{"code":"1210"'))
    process = AsyncMock(side_effect=[
        _failure('RESUME_VL_FAILED', '简历云端识别失败：简历 VL 评估失败（重试后仍失败）：'
            'LLM 调用失败[rejected]: ProviderHTTPError: 智谱GLM API请求失败: {"error":{"code":"1210"}}'),  # 甲先生失败
        {'success': True, 'data': {'candidate_name': '乙先生', 'match_score': 80,
            'match_status': 'matched', 'match_summary': None, 'image_count': 1, 'job_name': None}},
    ])
    monkeypatch.setattr(resume_flow, 'process_item', process)
    monkeypatch.setattr(overlay_flow, 'run_base', AsyncMock(return_value=original))
    from src.local_tools.proxy_tool import BossResumeBatchTool
    operation = SimpleNamespace(branch='main', ordinal=0, owner=SimpleNamespace(
        saved_phase=AsyncMock(return_value={'request': {'device_id': 'device'}})))
    result = await domain_flow.resume_result(BossResumeBatchTool(), operation, {
        '_trusted_tenant_id': 'tenant', '_trusted_user_id': 'user', '_session_id': 'session'}, batch=True)
    assert result['success'] is True
    assert [call.kwargs['item_ordinal'] for call in process.call_args_list] == [0, 1], '失败份跳过后批次继续下一份'
    assert result['data']['failures'][0]['name'] == '甲先生'
    assert '1210' in result['data']['failures'][0]['error'], '单份失败事实保留供应商原始码'
    assert result['data']['resumes'][0]['candidate_name'] == '乙先生'


def _failure(code, message='简历云端识别失败（测试）'):
    from src.local_tools.resume_flow import _failure as failure
    return failure(code, message)


@pytest.mark.asyncio
async def test_process_item_converts_vl_error_to_failure_with_diagnostics(monkeypatch):
    """process_item：ResumeVLError → RESUME_VL_FAILED 单份结果；诊断不含简历正文。"""
    from src.services.resume_vl_service import ResumeVLError
    monkeypatch.setattr('src.services.recruiting_match_service._load_job_context', MagicMock())
    _, resume_flow, _, owner, original = _recruiting_context(
        monkeypatch, vl_side_effect=ResumeVLError('简历 VL 评估失败（重试后仍失败）：LLM 调用失败[rejected]: x'))
    payload = original['data']['resumes'][0]
    result = await resume_flow.process_item(owner, tenant_id='tenant', execution_id='exec-1',
        tool_call_id='call-1', invocation_id='inv-1', device_id='device',
        item_ordinal=0, payload=payload)
    assert result['success'] is False and result['code'] == 'RESUME_VL_FAILED'
    # 失败发生在计费/入库之前：commit_domain（fee）不得被调用
    owner.commit_domain.assert_not_called()


# ============== 图像诊断（不含简历正文） ==============

def test_image_diagnostics_reports_metadata_only():
    from src.services.resume_vl_service import image_diagnostics
    payload = _png_base64(width=6, height=9)
    facts = image_diagnostics(payload)
    assert facts['format'] == 'PNG' and facts['mode'] == 'RGB' and facts['size'] == '6x9'
    assert facts['bytes'] == len(base64.b64decode(payload))
    assert facts['base64_bytes'] == len(payload)
    assert len(facts['digest']) == 64
    assert set(facts) == {'format', 'mode', 'size', 'base64_bytes', 'bytes', 'digest'}


def test_image_diagnostics_keeps_facts_on_garbage_without_content():
    from src.services.resume_vl_service import image_diagnostics
    facts = image_diagnostics('不是合法的base64图像数据')
    assert facts['decode_error'] and facts['bytes'] is None
    assert facts['format'] is None and len(facts['digest']) == 64
    serialized = repr(facts)
    assert '不是合法的base64图像数据' not in serialized, '诊断序列化不得携带原文内容'
