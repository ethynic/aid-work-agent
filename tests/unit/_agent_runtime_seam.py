# -*- coding: utf-8 -*-
"""33f9dba1 重构后的共享测试 harness：真实 AgentEngine + 真实 ToolDispatcher /
ControlToolAdapter，仅替换 I/O 边界（与 test_agent_engine_acceptance.py 的
fake port 模式同款，供 test_tool_result_truncation.py / test_verbose_feedback.py
复用）。

为什么选这个接缝：
- 工具结果截断的现行接入点是 AgentEngine（src/core/agent_engine/engine.py
  ``_truncate_tool_content``，``result.preserve_content`` 豁免）；preserve_content
  由 ToolDispatcher（tools.py ``result.pop("_no_truncate")``）与
  ControlToolAdapter（controls.py：use_skill 专属豁免 + 控制工具结果
  ``_no_truncate`` 键同样 pop 生效）决定；
- verbose policy 注入的现行接缝是 ToolDispatcher.before_tools（tools.py），由
  engine 在首个 tool_start 之前 yield；RuntimeExecution 只做参数透传
  （executor.py 构造 ToolDispatcher 处）。
master / subagent 在新架构下共用同一条 engine 路径，无第二个接入点。
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from src.core.agent_engine import AgentEngine, ExecutionState
from src.core.agent_engine.contracts import AgentMode, Identity
from src.services.agent_runner.runtime.controls import ControlToolAdapter
from src.services.agent_runner.runtime.executor import CompatibilityControl
from src.services.agent_runner.runtime.tool_catalog import ToolCatalog
from src.services.agent_runner.runtime.tools import ToolDispatcher
from tests.unit.test_agent_engine_acceptance import Model, Observer, call, completion


def execution_state(session_id="sess", system_prompt="SYSTEM-PROMPT", max_iterations=10):
    """构造 engine 直测用 ExecutionState（单条 user 消息开局）。"""
    return ExecutionState(
        identity=Identity("tenant-1", "user-1", session_id),
        execution_id="execution-1", role=AgentMode.STANDALONE,
        system_prompt=system_prompt,
        messages=[{"role": "user", "content": "request"}],
        initial_len=1, max_iterations=max_iterations)


class FakeRegistry:
    """ToolRegistry 替身：schema 定义 + 按名取工具实例。"""

    def __init__(self, tools=None, extra_definitions=None):
        self._tools = {tool.name: tool for tool in (tools or [])}
        self._extra = list(extra_definitions or [])

    def get_tool_definitions(self):
        names = [{"name": name, "description": name,
                  "parameters": {"type": "object"}}
                 for name in self._tools]
        return names + self._extra

    def get_usage_guides(self):
        return ""

    def get_tool(self, name):
        return self._tools.get(name)


class FakeControls:
    """_tool_controls 替身（虚拟工具集），默认为空。"""

    def definitions(self, available_subagents=None):
        return []

    def get(self, name):
        return None

    def get_display_name(self, name, args):
        return None

    def usage_guides(self):
        return ""


class FakeTool:
    """普通工具替身：可带 get_user_feedback 钩子与自定义显示名。"""

    def __init__(self, name, display_name=None, feedback="unset"):
        self.name = name
        self.execution_target = None
        self._display = display_name
        self._feedback = feedback

    def get_display_name(self, args):
        return self._display or self.name

    def get_user_feedback(self, tool_args):
        if self._feedback == "raise":
            raise RuntimeError("hook boom")
        if self._feedback in (None, "unset"):
            return None
        return self._feedback

    async def execute(self, **kwargs):
        return {"success": True}


class FakeExecutor:
    """tool_executor 替身：按序弹出结果并记录 (name, args, context)。"""

    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    async def execute(self, name, args, context=None):
        self.calls.append((name, args, context))
        return self.results.pop(0)


class FakeToolContext:
    """ExecutionContext 替身：记录 derive 调用参数。"""

    def __init__(self):
        self.derived = []

    def derive(self, **kwargs):
        self.derived.append(kwargs)
        return SimpleNamespace(derived_from=kwargs)


def no_plan_manager():
    manager = MagicMock()
    manager.get_plan.return_value = None
    manager.get_next_pending_task.return_value = None
    return manager


def plan_manager_with_pending(*, plan_id="plan-1", task_id="task-1", status="pending"):
    """构造带一个待执行计划任务的 PlanManager 替身。"""
    manager = MagicMock()
    task = SimpleNamespace(task_id=task_id, status=status)
    manager.get_plan.return_value = SimpleNamespace(plan_id=plan_id, tasks=[task])
    manager.get_next_pending_task.return_value = task
    return manager


def make_dispatcher(*, tools=None, extra_definitions=None, executor_results,
                    verbose_config=None, verbose_state=None, plan_manager=None,
                    control_tools=None):
    """组装 真实 ToolCatalog + 真实 ToolDispatcher + 假 I/O。

    policy_source 使用真实 ToolCatalog（与 RuntimeExecution 的接线一致），
    verbose 策略解析经由 catalog.tool_registry / catalog._tool_controls。
    """
    registry = FakeRegistry(tools=tools, extra_definitions=extra_definitions)
    catalog = ToolCatalog(AgentMode.STANDALONE, registry, FakeControls(),
                          SimpleNamespace(available=lambda: None), None)
    executor = FakeExecutor(executor_results)
    control_tools = control_tools or SimpleNamespace(controls=FakeControls(),
                                                     context=FakeToolContext())
    dispatcher = ToolDispatcher(
        catalog=catalog, registry=registry, executor=executor,
        control_tools=control_tools,
        local=SimpleNamespace(state=None),
        skills=MagicMock(skill_registry=SimpleNamespace(get=lambda name: None)),
        plan_manager=plan_manager or no_plan_manager(),
        subagent_config=None, control=CompatibilityControl(),
        verbose_config=verbose_config, verbose_state=verbose_state,
        policy_source=catalog)
    return SimpleNamespace(dispatcher=dispatcher, executor=executor, catalog=catalog,
                           control_tools=control_tools)


def use_skill_control_tools(skill_result, *, skill_dir="/tmp/fake_skill"):
    """真实 ControlToolAdapter（controls.py use_skill 分支）+ 假 use_skill 工具。

    返回值可直接作 ToolDispatcher 的 control_tools：dispatcher 经
    adapter.controls.get / adapter.execute 走控制工具路径，最终由
    controls.py ``preserve_content = name == "use_skill"`` 决定豁免截断。
    """
    async def _execute(**kwargs):
        return skill_result

    tool = SimpleNamespace(execute=_execute)
    skill = SimpleNamespace(dir=skill_dir, hooks=None)

    class _Registry:
        @staticmethod
        def get(name):
            return skill

    return ControlToolAdapter(
        controls={"use_skill": tool}, skills=MagicMock(), registry=_Registry(),
        plan_manager=no_plan_manager(), clarification_store=MagicMock(),
        context=FakeToolContext(), user_input="加载技能")


async def run_engine(state_obj, model, dispatcher):
    """驱动真实 AgentEngine，返回全部事件（Observer 仅记录，不做断言）。"""
    observer = Observer()
    return [event async for event in AgentEngine().run(
        state_obj, model, dispatcher, CompatibilityControl(), observer)]


def tool_messages_of(model, call_index=1):
    """第 call_index 次 LLM 调用（0 基）收到的 role=tool 消息列表。"""
    assert len(model.requests) > call_index, "LLM 调用次数不足"
    request = model.requests[call_index][0]
    return [m for m in request["messages"] if m.get("role") == "tool"]
