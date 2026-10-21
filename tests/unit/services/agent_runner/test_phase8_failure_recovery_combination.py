"""Phase 8 组合验收（组合 1/2）：三类失败路径组合 + 1210 挂起恢复全链路。

计划文档 Phase 8 重点组合第 1、2 条：
1. 三类失败路径（明确 400 / 无响应 / 不可解析）组合断言单份状态、剩余批次
   处理、计费边界；
2. 1210 挂起 → 恢复 → 消费第二批 → 失败份按策略跳过 → 正常终局，全链路
   不重复抓取/计费（Phase 2+3 组合）。

与各 Phase 独立测试的差异：owner.model_phase 不再用桩替代——owner 复用真实
RunnerLocalLifecycle.model_phase（三分分类、穿透/包装、已消耗尝试位的 usage
receipt 存在性门禁全部走真实代码），外层仅 mock 最外层
llm_gateway.chat_direct 与持久化/存储边界（无真实 PG，集成层缺口见会话结果）。
"""

import base64
import asyncio
import copy
import hashlib
from contextlib import contextmanager
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from PIL import Image

from src.llm.errors import ProviderHTTPError
from src.core.agent_engine.contracts import CheckpointFailure
from src.llm.gateway import llm_gateway
from src.local_tools.durable_flow import LocalContinuationRequired
from src.services.agent_runner.local_owner import RunnerLocalLifecycle
from src.services.agent_runner.ownership import Attempt

pytestmark = pytest.mark.unit

ZHIPU_1210_BODY = '{"error":{"code":"1210","message":"图像内容无法解析"}}'
INVOCATION_ID = '00000000-0000-0000-0000-0000000008d4'
EXECUTION_ID = 'p8-exec'
CALL_ID = 'p8-call'
RUNNER_ID = 'p8-runner'


def rejection_400() -> ProviderHTTPError:
    """智谱 1210 明确拒绝形态：完整 HTTP 响应体带供应商错误码。"""
    return ProviderHTTPError(f"智谱GLM API请求失败: {ZHIPU_1210_BODY}", status_code=400)


def _png_base64(width=8, height=6) -> str:
    buf = BytesIO()
    Image.new('RGB', (width, height)).save(buf, format='PNG')
    return base64.b64encode(buf.getvalue()).decode('ascii')


def _vl_ok_content(name: str) -> str:
    import json
    return json.dumps({"name_seen": name, "resume_summary": "资深后端工程师",
                       "score": 82, "match_summary": "技术栈匹配",
                       "key_info": {"education": "本科"}}, ensure_ascii=False)


def _origins(image: str, names):
    digest = hashlib.sha256(image.encode()).hexdigest()
    return {index: {'invocation_id': INVOCATION_ID, 'item_ordinal': index,
                    'candidate_name': name, 'image_digest': digest}
            for index, name in enumerate(names)}


# ============== owner：真实 model_phase + 持久化边界桩 ==============


class _ReceiptCursor:
    def __init__(self, conn):
        self._conn = conn
        self._last_params = None

    def execute(self, sql, params=None):
        self._last_params = params
        self._conn.queries.append((sql, params))

    def fetchone(self):
        text = str(self._last_params)
        if any(key in text for key in self._conn.consumed_keys):
            self._conn.receipt_hits += 1
            return {'receipt_id': 'r-consumed', 'phase': 'unknown'}
        return None


class _ReceiptConnection:
    """模拟 agent_runner_usage_receipts 查询边界：consumed_keys 命中即有回执。"""

    def __init__(self, consumed_keys):
        self.consumed_keys = consumed_keys
        self.queries = []
        self.receipt_hits = 0

    def cursor(self):
        return _ReceiptCursor(self)


def _combined_owner(*, saved_phase_value, facts=None, consumed_keys=frozenset(), observed):
    """构造 model_phase 为真实方法的批次 owner。

    - saved_domain/complete_phase/start_domain/observe_domain/commit_domain 为
      持久化边界桩：facts 保存已落库域事实，commit 对 completed 事实幂等复用
      （不重复落账），新落账记入 ledger；
    - model_phase 保持真实 RunnerLocalLifecycle.model_phase：失败分类、无响应
      包装、非 dict 响应、已消耗尝试位（usage receipt 门禁）均为真实逻辑。
    """
    conn = _ReceiptConnection(consumed_keys)

    @contextmanager
    def connection_factory():
        yield conn

    owner = object.__new__(RunnerLocalLifecycle)
    owner.control = owner.root = SimpleNamespace(
        durable_owner=True, attempt=Attempt(RUNNER_ID, 'p8-worker', 3),
        repository=SimpleNamespace(connection_factory=connection_factory), usage_scope=None)
    owner.state = SimpleNamespace(execution_id=EXECUTION_ID, identity=None, resources={})
    owner.call_id = CALL_ID
    owner.saved_phase = AsyncMock(return_value=saved_phase_value)
    facts = dict(facts or {})
    ledger, fresh_slots = [], []

    async def saved_domain(**kw):
        fact = facts.get((kw['branch'], kw['ordinal']))
        return copy.deepcopy(fact) if fact is not None else None

    async def complete_phase(**kw):
        facts[(kw['branch'], kw['ordinal'])] = {'request': kw['intent'],
            'phase': 'completed', 'result': kw['result']}
        return kw['result']

    async def start_domain(**kw):
        fresh_slots.append((kw['branch'], kw['ordinal']))
        facts[(kw['branch'], kw['ordinal'])] = {'request': kw['intent'],
            'phase': 'started', 'result': None}

    async def commit_domain(**kw):
        saved = facts.get((kw['branch'], kw['ordinal']))
        if saved is not None and saved.get('phase') == 'completed':
            # 真实 commit_domain 对已完成事实幂等复用：不重新执行 writer、不重复落账
            return saved['result']
        ledger.append((kw['branch'], kw['ordinal']))
        result = {
            'resume.fee': {'credit_cost': 1, 'applied': True},
            'resume.insert': {'applied': True, 'resume_id': 900 + kw['ordinal'],
                              'candidate_name': kw['intent']['candidate_name'],
                              'image_count': 1, 'job_name': None},
            'resume.match': {'match_score': 80, 'match_status': 'matched',
                             'match_summary': None},
        }[kw['branch']]
        facts[(kw['branch'], kw['ordinal'])] = {'request': kw['intent'],
            'phase': 'completed', 'result': result}
        return result

    owner.saved_domain = AsyncMock(side_effect=saved_domain)
    owner.complete_phase = AsyncMock(side_effect=complete_phase)
    owner.start_domain = AsyncMock(side_effect=start_domain)
    owner._domain = AsyncMock()  # rearm 路径（零回执 started 位），本组合不应触达
    owner.observe_domain = AsyncMock(side_effect=lambda **kw: dict(observed.get('last') or {}))
    owner.commit_domain = AsyncMock(side_effect=commit_domain)
    return owner, ledger, fresh_slots, conn


def _patch_common(monkeypatch):
    from src.local_tools import resume_flow
    from src.services import recruiting_resume_service as resumes
    from src.services import resume_vl_service as vl
    import src.tools.context as context
    monkeypatch.setattr(vl, 'resolve_model_spec', lambda *a, **k: ('zhipu', 'GLM-5.3-Flash'))
    monkeypatch.setattr(resume_flow, 'invalidate_domain_fee_cache', lambda *a, **k: None)
    monkeypatch.setattr(resumes, 'prepare_resume_record_from_tool_result',
        lambda tenant_id, payload: {'name': payload['candidate_name'], 'images_base64': []})
    monkeypatch.setattr(context, 'current_tool_execution_context', lambda: SimpleNamespace(
        tenant_id='p8-tenant', agent_execution_id=EXECUTION_ID, tool_call_id=CALL_ID))


# ============== 组合 1：三类失败路径（400 / 不可解析 / 无响应） ==============


@pytest.mark.asyncio
async def test_combo1_rejected_and_unparseable_fail_per_item_batch_continues_fee_only_for_success(monkeypatch):
    """明确 400/1210 与不可解析（非 dict 响应）：各收敛为单份 RESUME_VL_FAILED
    （保留供应商原始码与分类），剩余批次继续处理并真实识别成功；识别费仅对
    成功份落账。全链路走真实 domain_flow + resume_flow + evaluate_resume +
    真实 model_phase，仅 mock 最外层 chat_direct。"""
    from src.local_tools import domain_flow
    from src.local_tools.proxy_tool import BossResumeBatchTool

    _patch_common(monkeypatch)
    image = _png_base64()
    calls, observed = [], {}

    async def fake_chat_direct(provider, model, **kwargs):
        calls.append(len(calls) + 1)
        if len(calls) <= 2:
            # 甲先生：两次明确拒绝（400/1210）
            raise rejection_400()
        if len(calls) <= 4:
            # 乙先生：两次「有响应但非对象」→ 真实 model_phase 判不可解析
            return f'not-a-dict-{len(calls)}'
        # 丙先生：正常识别
        observed['last'] = {'content': _vl_ok_content('丙先生'), 'usage': {},
                            'provider': provider, 'model': model,
                            '_runner_receipt_id': 'receipt-p8-1'}
        return observed['last']

    monkeypatch.setattr(llm_gateway, 'chat_direct', fake_chat_direct)
    original = {'success': True, 'invocation_id': INVOCATION_ID,
                'data': {'resumes': [{'candidate_name': name, 'images': [{'base64': image}]}
                                     for name in ('甲先生', '乙先生', '丙先生')],
                         'failures': []}}
    monkeypatch.setattr('src.local_tools.overlay_flow.run_base', AsyncMock(return_value=original))

    owner, ledger, fresh_slots, _receipt_conn = _combined_owner(
        saved_phase_value={'request': {'device_id': 'p8-device'}}, observed=observed)
    operation = SimpleNamespace(branch='main', ordinal=0, owner=owner)
    result = await domain_flow.resume_result(BossResumeBatchTool(), operation,
        {'_trusted_tenant_id': 'p8-tenant', '_trusted_user_id': 'p8-user',
         '_session_id': 'p8-session'}, batch=True)

    # 批次正常终局：两份失败、一份成功
    assert result['success'] is True
    failures = result['data']['failures']
    assert [f['name'] for f in failures] == ['甲先生', '乙先生']
    assert 'rejected' in failures[0]['error'] and '1210' in failures[0]['error'], \
        '单份失败事实保留供应商分类与原始码'
    assert 'unparseable' in failures[1]['error'] and 'LOCAL_MODEL_RESPONSE_NOT_AN_OBJECT' in failures[1]['error']
    assert [r['candidate_name'] for r in result['data']['resumes']] == ['丙先生']

    # 剩余批次处理：甲 2 次 + 乙 2 次 + 丙 1 次 = 5 次物理调用；每次重试是
    # 经真实 model_phase 新 ordinal 授权的新物理调用
    assert len(calls) == 5
    assert fresh_slots == [('resume.evaluate', 0), ('resume.evaluate', 1),
                           ('resume.evaluate', 2), ('resume.evaluate', 3),
                           ('resume.evaluate', 4)], '重试必须落在独立新尝试位'

    # 计费边界：识别费仅对成功份（丙，item_ordinal=2）落账，失败份零落账
    assert [entry for entry in ledger if entry[0] == 'resume.fee'] == [('resume.fee', 2)]
    assert ('resume.insert', 0) not in ledger and ('resume.insert', 1) not in ledger
    assert ('resume.insert', 2) in ledger


@pytest.mark.asyncio
async def test_combo1_no_response_skips_only_failed_item_and_recovery_never_retries_it(monkeypatch):
    """无响应只跳过乙，丙继续；恢复复用单份失败事实，不重识别、不重复扣费。"""
    from src.local_tools import domain_flow
    from src.local_tools.proxy_tool import BossResumeBatchTool

    _patch_common(monkeypatch)
    image = _png_base64()
    calls, observed = [], {}

    async def fake_chat_direct(provider, model, **kwargs):
        calls.append(len(calls) + 1)
        if len(calls) == 1:
            observed['last'] = {'content': _vl_ok_content('甲先生'), 'usage': {},
                                'provider': provider, 'model': model,
                                '_runner_receipt_id': 'receipt-p8-2'}
            return observed['last']
        if len(calls) == 2:
            # 乙先生：连接超时（未收到完整响应），不重试该份。
            raise httpx.ConnectTimeout('connection timed out')
        observed['last'] = {'content': _vl_ok_content('丙先生'), 'usage': {},
                            'provider': provider, 'model': model,
                            '_runner_receipt_id': 'receipt-p8-3'}
        return observed['last']

    monkeypatch.setattr(llm_gateway, 'chat_direct', fake_chat_direct)
    original = {'success': True, 'invocation_id': INVOCATION_ID,
                'data': {'resumes': [{'candidate_name': name, 'images': [{'base64': image}]}
                                     for name in ('甲先生', '乙先生', '丙先生')],
                         'failures': []}}
    monkeypatch.setattr('src.local_tools.overlay_flow.run_base', AsyncMock(return_value=original))

    owner, ledger, fresh_slots, _receipt_conn = _combined_owner(
        saved_phase_value={'request': {'device_id': 'p8-device'}}, observed=observed)
    operation = SimpleNamespace(branch='main', ordinal=0, owner=owner)
    kwargs = {'_trusted_tenant_id': 'p8-tenant', '_trusted_user_id': 'p8-user',
              '_session_id': 'p8-session'}
    result = await domain_flow.resume_result(BossResumeBatchTool(), operation, kwargs, batch=True)

    assert result['success'] is True
    assert [r['candidate_name'] for r in result['data']['resumes']] == ['甲先生', '丙先生']
    assert [f['name'] for f in result['data']['failures']] == ['乙先生']
    assert '未收到模型响应' in result['data']['failures'][0]['error']
    assert '已跳过 1 份' in result['message'] and '重试' not in result['message']

    # 每份只调用一次；乙超时后直接处理丙，不消耗乙的第二尝试位。
    assert len(calls) == 3
    evaluate_slots = [slot for slot in fresh_slots if slot[0] == 'resume.evaluate']
    assert evaluate_slots == [('resume.evaluate', 0), ('resume.evaluate', 2), ('resume.evaluate', 4)]

    # 计费边界：甲、丙成功落费，乙不入库、不落识别费。
    assert [entry for entry in ledger if entry[0] == 'resume.fee'] == [('resume.fee', 0), ('resume.fee', 2)]
    assert all(ordinal != 1 for _branch, ordinal in ledger
               if _branch == 'resume.fee'), '无响应份不得落识别费'
    assert ('resume.insert', 1) not in ledger

    # 批次后续恢复重入：连失败份也复用已保存结论，零新模型调用、零重复落账。
    before = list(ledger)
    monkeypatch.setattr(llm_gateway, 'chat_direct', AsyncMock(
        side_effect=AssertionError('恢复不得重识别已处理或已跳过的份')))
    replay = await domain_flow.resume_result(BossResumeBatchTool(), operation, kwargs, batch=True)
    assert replay == result and ledger == before
    assert len(fresh_slots) == 3


@pytest.mark.asyncio
async def test_unreadable_image_skips_without_model_call_and_next_resume_is_stored(monkeypatch):
    """坏图片不消耗模型或识别费；下一份正常入库，无需前端确认。"""
    from src.local_tools import domain_flow
    from src.local_tools.proxy_tool import BossResumeBatchTool

    _patch_common(monkeypatch)
    observed = {'last': {'content': _vl_ok_content('乙先生'), 'usage': {},
                        'provider': 'zhipu', 'model': 'GLM-5.3-Flash',
                        '_runner_receipt_id': 'receipt-good-image'}}
    chat = AsyncMock(return_value=observed['last'])
    monkeypatch.setattr(llm_gateway, 'chat_direct', chat)
    original = {'success': True, 'invocation_id': INVOCATION_ID,
                'data': {'resumes': [
                    {'candidate_name': '甲先生', 'images': [{'base64': 'broken-image'}]},
                    {'candidate_name': '乙先生', 'images': [{'base64': _png_base64()}]},
                ]}}
    monkeypatch.setattr('src.local_tools.overlay_flow.run_base', AsyncMock(return_value=original))
    owner, ledger, slots, _ = _combined_owner(
        saved_phase_value={'request': {'device_id': 'p8-device'}}, observed=observed)
    result = await domain_flow.resume_result(BossResumeBatchTool(),
        SimpleNamespace(branch='main', ordinal=0, owner=owner), {}, batch=True)
    assert result['success'] is True
    assert [r['candidate_name'] for r in result['data']['resumes']] == ['乙先生']
    assert [f['name'] for f in result['data']['failures']] == ['甲先生']
    chat.assert_awaited_once()
    assert slots == [('resume.evaluate', 2)]
    assert [entry for entry in ledger if entry[0] == 'resume.fee'] == [('resume.fee', 1)]


@pytest.mark.asyncio
@pytest.mark.parametrize('error', [
    LocalContinuationRequired('LOCAL_PHASE_INTENT_CHANGED'),
    CheckpointFailure('LOCAL_MODEL_RECEIPT_OWNER_MISMATCH'),
    asyncio.CancelledError(),
])
async def test_item_skip_does_not_swallow_cancel_or_execution_integrity_failure(monkeypatch, error):
    """自动跳过只处理单份识别失败，取消和执行完整性失败仍终止处理。"""
    from src.local_tools import resume_flow
    from src.services import resume_vl_service as vl

    _patch_common(monkeypatch)
    monkeypatch.setattr(vl, 'evaluate_resume', AsyncMock(side_effect=error))
    owner, ledger, _, _ = _combined_owner(saved_phase_value={}, observed={})
    with pytest.raises(type(error)) as caught:
        await resume_flow.process_item(owner, tenant_id='p8-tenant', execution_id=EXECUTION_ID,
            tool_call_id=CALL_ID, invocation_id=INVOCATION_ID, device_id='p8-device',
            item_ordinal=0, payload={'candidate_name': '甲先生', 'images': [{'base64': _png_base64()}]})
    assert caught.value is error
    assert ledger == []
    assert all(call.kwargs['branch'] != 'resume.evaluation'
               for call in owner.complete_phase.await_args_list)


# ============== 组合 2：1210 挂起 → 恢复消费第二批（Phase 2+3） ==============


@pytest.mark.asyncio
async def test_combo2_1210_hang_recovery_consumes_batch_without_refetch_or_double_billing(monkeypatch):
    """1210 挂起 → 恢复 → 消费第二批 3 份 → 乙先生单份跳过 → 正常终局。

    - 挂起现场：甲（前一批已入库，全链 completed）、乙（识别首尝试位 started
      且已持有不确定回执——经真实 usage receipt 存在性门禁判定）、丙（未处理）；
    - 恢复只轮询原 invocation（重新选设备/派发即 AssertionError）；
    - 不重复计费：甲零新调用幂等复用；乙从已消耗位换独立新尝试位（新物理调用）
      仍被 1210 拒绝 → 单份跳过；丙正常识别入库，识别费仅对丙落账。
    """
    from src.local_tools import domain_flow
    from src.local_tools.proxy_tool import BossResumeBatchTool
    from src.services.agent_runner.local_invocations import LocalPhase

    _patch_common(monkeypatch)
    image = _png_base64()
    names = ['甲先生', '乙先生', '丙先生']
    origins = _origins(image, names)
    model_intent_of_b = {**origins[1], 'model_purpose': 'resume_recognition_covered',
                         'model_phase_version': 1}
    facts = {
        # 甲：挂起前已入库（全链 completed，恢复仅幂等复用）
        ('resume.policy', 0): {'request': {**origins[0], 'resume_policy_version': 1},
            'phase': 'completed',
            'result': {'job_context': None, 'credit_cost': 1, 'model_spec': 'zhipu/GLM-5.3-Flash'}},
        ('resume.evaluation', 0): {'request': dict(origins[0]), 'phase': 'completed',
            'result': {'name_seen': '甲先生', 'resume_summary': '恢复前已识别', 'score': 88,
                       'match_summary': None, 'key_info': None, 'usage': None,
                       'model': 'GLM-5.3-Flash'}},
        ('resume.fee', 0): {'request': {}, 'phase': 'completed',
            'result': {'credit_cost': 1, 'applied': True}},
        ('resume.insert', 0): {'request': dict(origins[0]), 'phase': 'completed',
            'result': {'applied': True, 'resume_id': 101, 'candidate_name': '甲先生',
                       'image_count': 1, 'job_name': None}},
        ('resume.match', 0): {'request': {}, 'phase': 'completed',
            'result': {'match_score': 88, 'match_status': 'matched', 'match_summary': None}},
        # 乙：policy 已冻结，识别首尝试位 started 且已持有回执（挂起现场）
        ('resume.policy', 1): {'request': {**origins[1], 'resume_policy_version': 1},
            'phase': 'completed',
            'result': {'job_context': None, 'credit_cost': 1, 'model_spec': 'zhipu/GLM-5.3-Flash'}},
        ('resume.evaluate', 2): {'request': dict(model_intent_of_b),
            'phase': 'started', 'result': None},
    }
    consumed_keys = {LocalPhase(EXECUTION_ID, CALL_ID, 'resume.evaluate', 2).key(RUNNER_ID)}

    calls, observed = [], {}

    async def fake_chat_direct(provider, model, **kwargs):
        calls.append(len(calls) + 1)
        if len(calls) == 1:
            # 乙的新尝试位（独立物理调用）仍被 1210 明确拒绝
            raise rejection_400()
        # 丙：正常识别
        observed['last'] = {'content': _vl_ok_content('丙先生'), 'usage': {},
                            'provider': provider, 'model': model,
                            '_runner_receipt_id': 'receipt-p8-3'}
        return observed['last']

    monkeypatch.setattr(llm_gateway, 'chat_direct', fake_chat_direct)

    owner, ledger, fresh_slots, receipt_conn = _combined_owner(
        saved_phase_value={'request': {'arguments': {}, 'tool_name': 'boss_resume_batch',
                                       'device_id': 'p8-device', 'provider_key': None},
                           'invocation': {'id': INVOCATION_ID, 'deadline_at': None}},
        facts=facts, consumed_keys=consumed_keys, observed=observed)

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
        {'_trusted_tenant_id': 'p8-tenant', '_trusted_user_id': 'p8-user',
         '_session_id': 'p8-session'}, batch=True)

    # 任务正常终局：3 份消费，乙按策略单份跳过（保留供应商原始码）
    assert result['success'] is True
    assert [r['candidate_name'] for r in result['data']['resumes']] == ['甲先生', '丙先生']
    failures = result['data']['failures']
    assert [f['name'] for f in failures] == ['乙先生']
    assert 'rejected' in failures[0]['error'] and '1210' in failures[0]['error']

    # 不重新抓取：只轮询原 invocation，零次设备选取/派发
    proxy._poll_invocation.assert_awaited_once()
    proxy._find_ready_device.assert_not_awaited()
    proxy._dispatch_and_wait.assert_not_awaited()

    # 挂起位经真实 usage receipt 存在性门禁判定为已消耗（不重放）
    assert receipt_conn.receipt_hits >= 1, '已消耗尝试位必须由真实 receipt 门禁检出'
    assert owner._domain.await_count == 0, '已消耗位不得走 rearm 重派'
    # 新物理 VL 调用共 2 次（乙的新尝试位 + 丙的首位），甲零新调用
    assert len(calls) == 2
    evaluate_slots = [slot for slot in fresh_slots if slot[0] == 'resume.evaluate']
    assert evaluate_slots == [('resume.evaluate', 3), ('resume.evaluate', 4)], \
        '新视觉调用必须落在独立可追溯的新尝试位上'

    # 不重复计费：甲幂等复用零落账；乙失败零落账；识别费只对丙（新识别）落账
    assert [entry for entry in ledger if entry[0] == 'resume.fee'] == [('resume.fee', 2)]
    assert ('resume.fee', 0) not in ledger and ('resume.insert', 0) not in ledger
    assert ('resume.insert', 2) in ledger and ('resume.match', 2) in ledger
