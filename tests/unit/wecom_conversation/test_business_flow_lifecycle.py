"""业务分支 × 真实 LocalToolAdapter 生命周期接缝测试（评审 P1-1）。

复现原缺陷：直接调用真实 LocalToolAdapter（durable_owner 控制器 + 未注册
ToolFact 的 context）会抛 CheckpointFailure('LOCAL_EXECUTION_OWNER_REQUIRED')。
验证修复：wecom_business_flow._read_tool_page 在调用真实 adapter 前注册
ToolFact、派生携带 tool_call_id 的 context 并持久化 dispatch 意图，
RunnerLocalLifecycle 构造（该检查所在点）通过。
"""

import asyncio
import uuid
from unittest.mock import patch

import pytest

from src.core.agent_engine.contracts import (AgentMode, CheckpointFailure,
    ExecutionState, Identity)
from src.services.agent_runner.runtime.local_tools import LocalToolAdapter
from src.services.agent_runner.runtime import wecom_business_flow as flow


class _Attempt:
    runner_id = 'run-lifecycle-test'


class _RepositoryStub:
    """RunnerLocalLifecycle 构造期只取 connection_factory 属性（不发 IO）。"""

    connection_factory = staticmethod(lambda: None)


class _DurableControl:
    """最小 durable_owner 控制器：满足 RunnerLocalLifecycle 构造与授权签名。"""

    durable_owner = True  # 根控制器无 parent_control 属性（与真实 durable 控制器一致）
    repository = _RepositoryStub()
    attempt = _Attempt()
    lock = asyncio.Lock()

    def __init__(self):
        self.saved = []

    def command(self):
        return None

    async def save(self, state, boundary):
        self.saved.append(boundary)

    async def authorize_dispatch(self, profile_id=None):
        return None

    async def _check_usage(self):
        return None


class _ToolStub:
    """真实 proxy 工具替身：走 owned 分支的判定依据（不重写 execute 语义）。"""

    name = 'wecom_read_session'

    def __init__(self):
        from src.local_tools.proxy_tool import LocalToolProxyTool
        # 借真实基类类型标记：supports_original_invocation 依据 type(tool).execute
        # is LocalToolProxyTool.execute；真实读取工具即满足。直接用真实工具类。
        self._real = __import__('src.local_tools.proxy_tool', fromlist=['WecomReadSessionTool']) \
            .WecomReadSessionTool()

    def __getattr__(self, item):
        return getattr(self._real, item)


class _RegistryStub:
    def get_tool(self, name):
        from src.local_tools.proxy_tool import WecomReadSessionTool
        return WecomReadSessionTool()


class _ExecutorStub:
    """真实 adapter 的执行出口：到达即证明生命周期构造已通过。"""

    def __init__(self):
        self.registry = _RegistryStub()

    async def execute(self, tool_name, args, context=None):
        raise RuntimeError('stop-after-lifecycle')


def _real_state():
    identity = Identity(tenant_id='t1', user_id='u1', session_id='wecom_business_lifecycle')
    return ExecutionState(identity=identity, execution_id=str(uuid.uuid4()),
                          role=AgentMode.MASTER, system_prompt='', messages=[])


def _base_context():
    from src.tools.context import ExecutionContextFactory
    return ExecutionContextFactory.for_agent_call(
        tenant_id='t1', user_id='u1', session_id='wecom_business_lifecycle',
        agent_execution_id='exec-1')


def _real_adapter(control, state):
    return LocalToolAdapter(session_id='wecom_business_lifecycle',
                            execution_id=state.execution_id, subagent_config=None,
                            llm=None, tool_executor=_ExecutorStub(),
                            cancel_on_detach=False, control=control, state=state)


class _ExecutionView:
    """_read_tool_page 需要的最小执行视图（真实 state/control）。"""

    def __init__(self, control, state):
        self.control, self.state = control, state


@pytest.mark.asyncio
async def test_direct_adapter_call_without_fact_reproduces_owner_required():
    """复现评审缺陷：未注册 ToolFact 直接调真实 adapter → 权威存储失败。"""
    control, state = _DurableControl(), _real_state()
    adapter = _real_adapter(control, state)
    context = _base_context().derive(tool_call_id='call_missing_fact',
                                     agent_execution_id=state.execution_id)
    with pytest.raises(CheckpointFailure) as error:
        async for _kind, _payload in adapter._run_local_required_tool(
                'wecom_read_session', {'session': 'ws1.x'}, 't1', 'u1', context=context):
            pass
    assert 'LOCAL_EXECUTION_OWNER_REQUIRED' in str(error.value)


@pytest.mark.asyncio
async def test_read_tool_page_registers_fact_before_real_adapter():
    """修复验证：真实 adapter + durable_owner 下 _read_tool_page 通过生命周期构造。"""
    control, state = _DurableControl(), _real_state()
    adapter = _real_adapter(control, state)
    execution = _ExecutionView(control, state)
    with pytest.raises(RuntimeError) as error:
        await flow._read_tool_page(execution, adapter, control, state,
                                   _base_context(), {'session': 'ws1.x', 'date': '2026-11-03'})
    assert 'stop-after-lifecycle' in str(error.value)  # 通过了 owner 检查后到达执行出口
    assert 'before_tool' in control.saved  # dispatch 意图先于外部副作用持久化
    facts = [fact for fact in state.tools.values() if fact.call.name == 'wecom_read_session']
    assert facts and facts[0].phase == 'dispatching'
    # 执行失败时消息不配对（配对只随成功结果写入，见下一条用例）
    assert state.messages == []


@pytest.mark.asyncio
async def test_read_tool_page_records_result_and_invocation(monkeypatch):
    """结果/invocation 回写：事实完成、invocation_id 保存、消息配对。"""
    control, state = _DurableControl(), _real_state()
    adapter = _real_adapter(control, state)
    execution = _ExecutionView(control, state)

    class _InvokedExecutor(_ExecutorStub):
        def __init__(self):
            super().__init__()
            self.invocation_id = None

        async def execute(self, tool_name, args, context=None):
            self.invocation_id = context.tool_call_id
            queue = args.get('_progress_queue')
            if queue is not None:
                queue.put_nowait({'invocation_id': 'inv-1'})
            return {'success': True, 'data': {'read_window': {'start_utc': 1, 'end_utc': 2},
                                              'messages': [], 'has_more': False}}

    adapter.tool_executor = _InvokedExecutor()
    result = await flow._read_tool_page(execution, adapter, control, state,
                                        _base_context(), {'session': 'ws1.x'})
    assert result['success'] is True
    fact = next(iter(state.tools.values()))
    assert fact.phase == 'completed' and fact.invocation_id == 'inv-1'
    assert 'tool_invocation' in control.saved and 'tool_completed' in control.saved
    # 配对消息（assistant tool_calls 与 tool 回执）随成功结果写入
    call_ids = [call['id'] for message in state.messages
                for call in (message.get('tool_calls') or [])]
    assert call_ids and call_ids[0] in state.tools
    assert state.messages[-1]['role'] == 'tool' and state.messages[-1]['tool_call_id'] == call_ids[0]
