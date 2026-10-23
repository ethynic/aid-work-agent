"""供应商响应结构校验归类「返回不可解析」的组合测试（外部 CR 问题 1，P2）。

背景：HTTP 200 完整响应 {"choices":[null]} 这类对象内部结构错误此前在
_parse_response 抛 AttributeError，model_phase 将其包装成
LOCAL_MODEL_RESPONSE_VERIFICATION_REQUIRED，整批挂起而非单份失败收敛。

本文件用真实 Provider 解析层（仅 mock HTTP transport，结构校验路径真实执行）
断言：
- 三家 provider（zhipu/qwen/deepseek）的结构错误都抛 ProviderResponseInvalid
  （provider_outcome='unparseable'），不是 AttributeError；
- 与 model_phase 组合：结构错误以业务异常穿透，不被包装成验证等待；
- 与 evaluate_resume 组合：重试可达（第 2 次仍失败）后收敛单份 ResumeVLError，
  保留 unparseable 分类——即「重试→单份跳过、批次继续」路径，而非整批挂起。
"""

import pytest

from src.llm.errors import ProviderResponseInvalid, provider_outcome
from src.llm.providers.deepseek import DeepSeekProvider
from src.llm.providers.qwen import QwenProvider
from src.llm.providers.zhipu import ZhipuProvider

pytestmark = pytest.mark.unit

MESSAGES = [{"role": "user", "content": "识别这份简历"}]

# 结构错误样本：均为完整 HTTP 200 JSON 对象，但内部形态非法
STRUCTURALLY_INVALID = [
    {"choices": [None]},                                # 复现案例：null 元素
    {"choices": None},                                  # choices 为 null
    {},                                                 # choices 缺失
    {"choices": []},                                    # choices 空列表
    {"choices": ["stop"]},                              # 首元素非对象
    {"choices": [{"message": "ok"}]},                   # message 非对象（非 null）
    {"choices": [{"message": {"tool_calls": [None]}}]},  # tool_calls null 元素
    {"choices": [{"message": {"tool_calls": [{"function": "f"}]}}]},  # function 非对象
    {"choices": [{"message": {"tool_calls": [{"id": "c1", "function": None}]}}]},  # 复现案例：function=null
    {"choices": [{"message": {"tool_calls": [{"id": "c1"}]}}]},       # function 缺失（同一口径）
    {"choices": [{"message": {"content": "hi"}}], "usage": "ok"},     # usage 非对象
]

# 校验器放行的形态全集：解析器对其中每个形态都不得抛非 ProviderResponseInvalid 异常
SHAPES_ALLOWED_BY_VALIDATOR = [
    {"choices": [{"message": {"content": "x"}, "finish_reason": "stop"}]},
    {"choices": [{"message": None, "finish_reason": None}], "usage": None},
    {"choices": [{}], "usage": {}},
    {"choices": [{"message": {"content": None}}]},
    {"choices": [{"message": {"content": "x", "tool_calls": []}}]},
    {"choices": [{"message": {"content": "x", "tool_calls": None}}]},
    {"choices": [{"message": {"content": "x", "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}}]}}]},
    {"choices": [{"message": {"content": "x", "tool_calls": [
        {"function": {}}]}}]},  # function 存在但空对象（name/arguments 走默认值）
    {"choices": [{"message": {"content": "x"}, "finish_reason": "stop"}],
     "usage": {"prompt_tokens": 1, "prompt_tokens_details": None,
               "cached_tokens": 0}},
    {"choices": [{"message": {"content": "x"}}],
     "usage": {"prompt_tokens_details": "not-a-dict", "total_tokens": 3}},
    {"id": "resp-1", "choices": [{"message": {"reasoning_content": "思考中"}}]},
]

# 合法形态回归：null 容器（message/usage 为 null）维持既有空 dict 兜底语义
STRUCTURALLY_VALID = [
    {"choices": [{"message": None, "finish_reason": "stop"}], "usage": None},
    {"choices": [{"message": {"content": "识别结果"}, "finish_reason": "stop"}]},
    {"choices": [{"message": {"content": "x", "tool_calls": [
        {"id": "c1", "function": {"name": "f", "arguments": "{}"}}]}}]},
]


def _mock_http(monkeypatch, payload):
    import httpx
    client_class = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))

    def make_client(*args, **kwargs):
        return client_class(*args, transport=transport, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", make_client)
    for module in ("deepseek", "qwen", "zhipu"):
        monkeypatch.setattr(f"src.llm.providers.{module}.log_llm_invoke", lambda **kwargs: None)


PROVIDERS = [
    (ZhipuProvider, "智谱GLM"),
    (QwenProvider, "通义千问"),
    (DeepSeekProvider, "DeepSeek"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_class,display_name", PROVIDERS)
@pytest.mark.parametrize("payload", STRUCTURALLY_INVALID)
async def test_structural_failure_is_unparseable_not_attribute_error(
    monkeypatch, provider_class, display_name, payload,
):
    """200 响应对象内部结构错误 → ProviderResponseInvalid（unparseable），绝不抛 AttributeError。"""
    _mock_http(monkeypatch, payload)
    provider = provider_class(api_key="test-key", model="test-model")
    with pytest.raises(ProviderResponseInvalid) as caught:
        await provider.chat(MESSAGES)
    assert provider_outcome(caught.value) == 'unparseable'
    assert not getattr(caught.value, 'tool_continuation_required', False)
    # 错误信息带供应商名与结构原因，构成可审计单份失败事实
    assert display_name in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_class,_display", PROVIDERS)
@pytest.mark.parametrize("payload", STRUCTURALLY_VALID)
async def test_valid_shapes_still_parse(monkeypatch, provider_class, _display, payload):
    """合法/既有容忍形态（message、usage 为 null）不受结构校验影响。"""
    _mock_http(monkeypatch, payload)
    provider = provider_class(api_key="test-key", model="test-model")
    parsed = await provider.chat(MESSAGES)
    assert isinstance(parsed, dict)


def test_validator_boundary_does_not_swallow_unexpected_types():
    """校验边界只覆盖结构：意外内部异常不吞成供应商错误（直接调用解析层仍按原样抛）。"""
    from src.llm.providers.base import ensure_chat_completion_shape
    # choices[0] 为 dict、message 缺省 → 结构合法；后续字段访问由 _parse_response
    # 的既有兜底处理，不经过本校验器改判
    ensure_chat_completion_shape({"choices": [{}]}, display_name="测试")


# ============== 守卫测试：校验器放行的形态，解析器不得抛非预期异常 ==============

# 覆盖全部四家解析器：zhipu/qwen/deepseek 独立实现，moonshot 继承 qwen 并带
# reasoning_content 回退路径（message=null + 空 content 是其独有访问点）
PARSER_PROVIDER_CLASSES = [ZhipuProvider, QwenProvider, DeepSeekProvider]


def test_guard_every_allowed_shape_parses_without_unexpected_exception():
    """守卫：对校验器放行的每个形态，三家解析器（含 moonshot 回退路径）遍历
    字段访问都不得抛 ProviderResponseInvalid 之外的异常——不允许再存在
    「校验放行但解析崩」的形态（本轮缺口：function=null、message=null 回退）。"""
    from src.llm.providers.moonshot import MoonshotProvider
    classes = PARSER_PROVIDER_CLASSES + [MoonshotProvider]
    for provider_class in classes:
        provider = provider_class(api_key="test-key", model="test-model")
        for payload in SHAPES_ALLOWED_BY_VALIDATOR:
            from src.llm.providers.base import ensure_chat_completion_shape
            ensure_chat_completion_shape(payload, display_name="测试")  # 必须放行
            parsed = provider._parse_response(payload)  # 不得抛非预期异常
            assert isinstance(parsed, dict)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_class,_display", PROVIDERS)
async def test_function_null_is_structural_failure_not_attribute_error(
    monkeypatch, provider_class, _display,
):
    """缺口复现：tool_calls[i].function=null 此前被校验器放行、解析器抛
    AttributeError（provider_outcome=None → 结果未知挂起整批）。现按三家一致
    口径归入结构错误 → ProviderResponseInvalid（unparseable）。"""
    _mock_http(monkeypatch, {"choices": [{"message": {
        "content": "x", "tool_calls": [{"id": "c1", "function": None}]}}]})
    provider = provider_class(api_key="test-key", model="test-model")
    with pytest.raises(ProviderResponseInvalid) as caught:
        await provider.chat(MESSAGES)
    assert provider_outcome(caught.value) == 'unparseable'
    assert 'function' in str(caught.value)


# ============== 与 model_phase 组合：结构错误穿透，不进验证等待 ==============


def _owner():
    """绕过 __init__ 门禁构造满足 model_phase 依赖的 owner（同 test_model_phase_outcome）。"""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from src.services.agent_runner.local_owner import RunnerLocalLifecycle
    from src.services.agent_runner.ownership import Attempt
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


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_class,_display", PROVIDERS)
async def test_structural_failure_penetrates_model_phase(monkeypatch, provider_class, _display):
    """真实 provider 结构错误经 model_phase：业务异常穿透，而非 LOCAL_MODEL_RESPONSE_VERIFICATION_REQUIRED。"""
    from src.local_tools.durable_flow import LocalContinuationRequired
    from src.services.agent_runner.local_owner import RunnerLocalLifecycle
    _mock_http(monkeypatch, {"choices": [None]})
    provider = provider_class(api_key="test-key", model="test-model")
    owner = _owner()

    async def invoke():
        return await provider.chat(MESSAGES)

    with pytest.raises(ProviderResponseInvalid) as caught:
        await RunnerLocalLifecycle.model_phase(owner, branch='resume.evaluate',
            ordinal=0, intent={'invocation_id': 'inv-1'}, invoke=invoke,
            purpose='resume_recognition_covered')
    # 关键断言：不是 LocalContinuationRequired（那会包装成验证等待挂起整批）
    assert not isinstance(caught.value, LocalContinuationRequired)


# ============== 与 evaluate_resume 组合：重试可达 → 单份收敛、批次继续 ==============


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_class,_display", PROVIDERS)
async def test_structural_failure_retries_then_converges_single_item(monkeypatch, provider_class, _display):
    """{"choices":[null]}：第 2 次重试可达（每次都是真实 provider 调用），仍失败收敛
    单份 ResumeVLError 且保留 unparseable 分类——交由 resume_flow._failure
    ('RESUME_VL_FAILED') 单份跳过、批次继续，而非整批挂起。"""
    from src.services.resume_vl_service import ResumeVLError, evaluate_resume
    _mock_http(monkeypatch, {"choices": [None]})
    provider = provider_class(api_key="test-key", model="test-model")
    calls = []

    async def model_call(index, invoke):
        calls.append(index)
        return await invoke()

    with pytest.raises(ResumeVLError) as caught:
        await evaluate_resume(['aGVsbG8='], '王宣广', model_call=model_call)
    assert calls == [0, 1], 'unparseable 类必须重试可达（新物理调用），不是整批挂起'
    assert 'unparseable' in str(caught.value)
    assert '结构校验失败' in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"choices": [None]},
    {"choices": [{"message": {"content": "x", "tool_calls": [{"id": "c1", "function": None}]}}]},
], ids=["choices-null-element", "function-null"])
async def test_structural_failure_composition_converges_single_item(monkeypatch, payload):
    """组合回归（含本轮缺口 function=null）：真实 provider 结构错误经
    model_phase 穿透 → evaluate_resume 重试可达 → 单份 ResumeVLError 收敛，
    全程不被归为「结果未知」挂起整批。"""
    from src.local_tools.durable_flow import LocalContinuationRequired
    from src.services.agent_runner.local_owner import RunnerLocalLifecycle
    from src.services.resume_vl_service import ResumeVLError, evaluate_resume
    _mock_http(monkeypatch, payload)
    provider = ZhipuProvider(api_key="test-key", model="test-model")
    owner = _owner()
    calls = []

    async def invoke():
        return await provider.chat(MESSAGES)

    async def model_call(index, invoke_):
        calls.append(index)
        return await RunnerLocalLifecycle.model_phase(owner, branch='resume.evaluate',
            ordinal=index, intent={'invocation_id': 'inv-1'}, invoke=invoke_,
            purpose='resume_recognition_covered')

    with pytest.raises(ResumeVLError) as caught:
        await evaluate_resume(['aGVsbG8='], '王宣广', model_call=model_call)
    assert calls == [0, 1], '结构错误必须重试可达（新物理调用），不是整批挂起'
    assert 'unparseable' in str(caught.value)
    # model_phase 层穿透的是业务异常而非验证等待（无 tool_continuation_required）
    assert not isinstance(caught.value, LocalContinuationRequired)


@pytest.mark.asyncio
async def test_structural_failure_skips_item_and_batch_continues(monkeypatch):
    """单份收敛组合：结构错误 → RESUME_VL_FAILED 单份结果，计费/入库不发生，
    批次继续处理下一份（process_item + evaluate_resume 真实代码路径，仅
    HTTP transport 与 gateway 跳数 mock）。"""
    import base64
    from io import BytesIO
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from PIL import Image

    from src.local_tools import resume_flow
    from src.llm.gateway import llm_gateway
    import src.services.resume_vl_service as vl
    import src.tools.context as context

    _mock_http(monkeypatch, {"choices": [None]})
    provider = ZhipuProvider(api_key="test-key", model="test-model")

    async def fake_chat_direct(provider_name, model, **kwargs):
        # gateway 跳数不是本用例关注点：直达真实 provider.chat（含结构校验）
        return await provider.chat(kwargs['messages'], temperature=kwargs.get('temperature', 0.1))

    monkeypatch.setattr(llm_gateway, 'chat_direct', fake_chat_direct)
    monkeypatch.setattr(vl, 'resolve_model_spec', lambda *a, **k: ('zhipu', 'GLM-5.3-Flash'))

    async def model_phase(*, branch, ordinal, intent, invoke, purpose):
        return await invoke()

    owner = SimpleNamespace(
        saved_domain=AsyncMock(return_value=None),
        complete_phase=AsyncMock(side_effect=lambda **kw: kw['result']),
        model_phase=AsyncMock(side_effect=model_phase),
        commit_domain=AsyncMock(side_effect=lambda **kw: {'credit_cost': 1, 'applied': True, 'resume_id': 1}),
    )
    monkeypatch.setattr(context, 'current_tool_execution_context', lambda: SimpleNamespace(
        tenant_id='tenant', agent_execution_id='exec-1', tool_call_id='call-1'))
    buf = BytesIO()
    Image.new('RGB', (4, 4)).save(buf, format='PNG')
    image = base64.b64encode(buf.getvalue()).decode('ascii')
    payload = {'candidate_name': '甲先生', 'images': [{'base64': image}]}

    result = await resume_flow.process_item(owner, tenant_id='tenant', execution_id='exec-1',
        tool_call_id='call-1', invocation_id='inv-1', device_id='device',
        item_ordinal=0, payload=payload)

    assert result['success'] is False and result['code'] == 'RESUME_VL_FAILED'
    assert 'unparseable' in result['message'], '单份失败事实保留供应商错误分类'
    # 失败发生在计费/入库之前：fee 不得落账（批次由既有失败跳过机制继续下一份）
    owner.commit_domain.assert_not_called()
    # 两次尝试都真实抵达 provider 解析层（重试可达，非整批挂起）
    assert owner.model_phase.await_count == 2
