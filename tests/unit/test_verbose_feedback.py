# -*- coding: utf-8 -*-
"""verbose 统一反馈内核 Phase 1 测试（必测场景全覆盖）

对应 docs/plans/plan-agent-intermediate-feedback.md「Phase 1：统一反馈内核」必测场景：
1. 长任务 policy 事件在首个 tool_start 前；无策略的 turn（无论多慢）不产生任何
   verbose（2026-09-01 产品决策：system watchdog 已删除，无系统兜底）；
2. 空 tool call 不触发（注：非法 JSON 参数在现行引擎中直接失败
   INVALID_TOOL_ARGUMENTS，engine.py normalize_calls，不再归一为 {}，见
   tests/integration/test_agent_runtime_acceptance.py）；
3. Skill/Tool 模板含换行/超长/路径/JSON/命令/敏感键/数字 ETA/代码块/HTML/百分比
   → 整体降级 fallback 文案（仍属 policy 决定的提示）；
4. 并行 tool calls 仍最多一条（取第一个长任务策略）；
5. 未注册的 verbose 被 wrapper 防御性丢弃，每轮最多一条不依赖 producer 自觉；
6. 正常/异常/取消均无残留 producer task（asyncio task 计数断言）；
7. 两个并发 session 的状态完全隔离；
8. 原始 generator / 不经包装入口的调用方不被自动注入 verbose；
9. 捕获同一轮后续及下一轮 provider 请求（mock LLM），断言 messages 中不存在
    verbose 文案；Skill prompt 和 tool schema 中不存在 feedback metadata。

33f9dba1 重构后的驱动方式：
- policy 注入（TestPolicyInjection）：真实 AgentEngine + 真实 ToolDispatcher
  （before_tools 注入点），假 registry/executor/model —— 复用
  tests/unit/_agent_runtime_seam.py（与 test_agent_engine_acceptance.py 同款
  fake port 模式）；
- 包装入口（TestSyncAndWrapperEntries）：Agent.__new__ 跳过 __init__ 副作用 +
  实例属性注入 _identity / _execution 替身（薄壳 _process_message_impl 只代理
  execution 事件流，agent.py:88-102 注释明确保留测试替换能力）。

注（差异已修复，2026-10-06）：旧 agent.py 在注入点检查
verbose_config.effective_enabled；现行 ToolDispatcher.before_tools 已补
effective_enabled 门（tools.py），disabled / force_disabled 配置在注入点直接
拦截，由本文件 test_disabled_config_gates_policy_injection 覆盖；包装入口的
门控位于 prepare_turn_feedback（verbose_feedback.py），由 sync 级用例覆盖。
"""

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.core.agent import Agent, AgentMode
from src.core.agent_engine import Outcome
from src.core.agent_engine.contracts import Identity
from src.core.agent_events import make_verbose_event
from src.core.verbose_feedback import (
    DEFAULT_FALLBACK_MESSAGE,
    LongRunningFeedback,
    VerboseFeedbackConfig,
    VerboseFeedbackObserver,
    VerboseFeedbackState,
    default_feedback_config,
    iter_with_verbose_feedback,
    new_verbose_event_id,
    resolve_feedback_policy,
    validate_feedback_text,
)
from tests.unit._agent_runtime_seam import (
    FakeTool,
    Model,
    call,
    completion,
    execution_state,
    make_dispatcher,
    run_engine,
)

pytestmark = pytest.mark.agent

VALID_TEXT = "正在生成报价单，请耐心等待。"
# 测试用兜底文案（合法：单句、28 字、无违规内容）
TEST_FALLBACK = DEFAULT_FALLBACK_MESSAGE


def _fast_config(enabled: bool = True) -> VerboseFeedbackConfig:
    """测试用配置（2026-09-01 产品决策：system watchdog 删除，无 delay 字段）。"""
    return VerboseFeedbackConfig(enabled=enabled, fallback_message=TEST_FALLBACK)


class RecordingObserver(VerboseFeedbackObserver):
    """记录观测回调入参的 observer（结构上不可能记录文案正文）。"""

    def __init__(self):
        self.emitted = []
        self.suppressed = []

    def on_verbose_emitted(self, *, source, text_length, time_to_first_seconds):
        self.emitted.append(
            {"source": source, "text_length": text_length,
             "time_to_first_seconds": time_to_first_seconds}
        )

    def on_verbose_suppressed(self, *, reason):
        self.suppressed.append(reason)


# ============================================================
# 唯一策略解析入口 resolve_feedback_policy（设计 §5）
# ============================================================


class _FakeRegistry:
    """skill_registry 替身：get_user_feedback 返回预置策略。"""

    def __init__(self, feedback_by_skill=None):
        self.feedback_by_skill = feedback_by_skill or {}

    def get_user_feedback(self, name):
        return self.feedback_by_skill.get(name)

    def get(self, name):
        return None


class _FakeToolRegistry:
    """tool_registry 替身：按名称返回带 get_user_feedback 钩子的工具实例。"""

    def __init__(self, tools=None):
        self._tools = tools or {}

    def get_tool(self, name):
        return self._tools.get(name)


class _HookTool:
    """带 get_user_feedback 钩子的普通工具替身。"""

    name = "excel_tool"
    execution_target = None

    def __init__(self, feedback="unset"):
        self.feedback = feedback

    def get_user_feedback(self, tool_args):
        if self.feedback == "raise":
            raise RuntimeError("hook boom")
        if self.feedback is None or self.feedback == "unset":
            return None
        return self.feedback


def _fake_agent(skill_feedback=None, tools=None) -> SimpleNamespace:
    return SimpleNamespace(
        skill_registry=_FakeRegistry(skill_feedback or {}),
        tool_registry=_FakeToolRegistry(tools or {}),
        _tool_controls=MagicMock(),
    )


class TestResolveFeedbackPolicy:
    def test_skill_long_running_valid_message(self):
        agent = _fake_agent({"travel-quote": {"long_running": True, "start_message": VALID_TEXT}})
        fb = resolve_feedback_policy("skill_execute", {"skill": "travel-quote"}, agent)
        assert fb == LongRunningFeedback(start_message=VALID_TEXT)

    def test_skill_short_or_missing_is_none(self):
        agent = _fake_agent({
            "short-skill": {"long_running": False, "start_message": VALID_TEXT},
        })
        assert resolve_feedback_policy("skill_execute", {"skill": "short-skill"}, agent) is None
        assert resolve_feedback_policy("skill_execute", {"skill": "nope"}, agent) is None
        assert resolve_feedback_policy("skill_execute", {}, agent) is None
        assert resolve_feedback_policy("skill_execute", {"skill": ""}, agent) is None

    @pytest.mark.parametrize(
        "bad_message",
        [
            "第一行\n第二行",                      # 换行
            "好" * 61,                              # 超长
            "正在读取 C:\\repos\\x\\a.py，请稍候。",  # Windows 路径
            "正在处理 {\"task\": \"fill_template\"}。",  # JSON
            "正在执行 rm -rf /tmp/data，请稍候。",    # 命令
            "您的 password 已更新，处理中。",          # 敏感键
            "预计 30 秒后完成。",                     # 数字 ETA
            "正在生成 ```code``` 内容。",              # 代码块
            "正在处理 <b>加急</b> 请求。",             # HTML
            "已完成 85%，请稍候。",                    # 百分比
        ],
    )
    def test_skill_invalid_template_returns_empty_message_for_fallback(self, bad_message):
        """长任务命中但模板违规：返回空串 start_message，由调用方降级 fallback 文案。"""
        agent = _fake_agent({"bad": {"long_running": True, "start_message": bad_message}})
        fb = resolve_feedback_policy("skill_execute", {"skill": "bad"}, agent)
        assert fb is not None, "仍算 policy 命中（长任务事实不因文案违规改变）"
        assert fb.start_message == "", "违规文案必须整体拒绝，不做局部清洗"

    def test_delegate_fixed_message(self):
        fb = resolve_feedback_policy("delegate_to_subagent", {}, _fake_agent())
        assert fb is not None
        assert fb.start_message == "正在交由专业数字员工处理，请耐心等待"

    def test_ordinary_tool_hook_long_running(self):
        agent = _fake_agent(tools={"excel_tool": _HookTool(LongRunningFeedback(start_message=VALID_TEXT))})
        fb = resolve_feedback_policy("excel_tool", {"task": "fill_template"}, agent)
        assert fb == LongRunningFeedback(start_message=VALID_TEXT)

    def test_ordinary_tool_without_hook_or_none_is_short(self):
        # 无钩子工具（to_tool_definition 类实例缺省）→ 短任务
        assert resolve_feedback_policy("read", {}, _fake_agent()) is None
        # 钩子返回 None → 短任务
        agent = _fake_agent(tools={"excel_tool": _HookTool(None)})
        assert resolve_feedback_policy("excel_tool", {}, agent) is None

    def test_tool_hook_invalid_message_degrades_to_empty(self):
        agent = _fake_agent(tools={"excel_tool": _HookTool(LongRunningFeedback(start_message="预计 5 分钟完成。"))})
        fb = resolve_feedback_policy("excel_tool", {}, agent)
        assert fb is not None and fb.start_message == ""

    def test_tool_hook_exception_treated_as_short(self):
        """钩子异常视同短任务：verbose 是 best-effort，不得影响工具执行。"""
        agent = _fake_agent(tools={"excel_tool": _HookTool("raise")})
        assert resolve_feedback_policy("excel_tool", {}, agent) is None

    def test_unknown_and_empty_tool_names(self):
        assert resolve_feedback_policy("", {}, _fake_agent()) is None
        assert resolve_feedback_policy(None, {}, _fake_agent()) is None
        assert resolve_feedback_policy("create_plan", {}, _fake_agent()) is None


# ============================================================
# Skill / Tool schema 上下文隔离（设计 §6.2）
# ============================================================


class TestContextIsolation:
    def test_base_tool_default_feedback_none_and_not_in_schema(self):
        from src.tools.base import BaseTool

        class DummyTool(BaseTool):
            name = "dummy"
            description = "dummy tool"

            async def execute(self, **kwargs):
                return {"success": True}

        tool = DummyTool()
        assert tool.get_user_feedback({}) is None
        schema = tool.to_tool_definition()
        assert set(schema.keys()) == {"name", "description", "input_schema"}
        assert "get_user_feedback" not in json.dumps(schema)
        assert "user_feedback" not in json.dumps(schema)
        assert "start_message" not in json.dumps(schema)

    def test_skill_metadata_user_feedback_not_rendered_into_prompt(self, tmp_path):
        """metadata.user_feedback 只保存在编排侧 registry，不进入 Skill 正文/描述。"""
        from src.core.skill_registry import SkillRegistry

        skill_dir = tmp_path / "travel-quote"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: travel-quote\n"
            "description: 生成旅游报价单\n"
            "metadata:\n"
            "  user_feedback:\n"
            "    long_running: true\n"
            "    start_message: \"正在生成报价单，请耐心等待。\"\n"
            "---\n"
            "# 报价单技能正文\n按步骤生成报价。\n",
            encoding="utf-8",
        )
        registry = SkillRegistry(tmp_path)
        # 编排侧可读（策略来源）
        fb = registry.get_user_feedback("travel-quote")
        assert fb == {"long_running": True, "start_message": VALID_TEXT}
        # 正文 / 描述（进 prompt 的两条路径）不含 metadata
        content = registry.get_content("travel-quote")
        assert "user_feedback" not in content
        assert VALID_TEXT not in content
        descriptions = registry.get_descriptions()
        assert "user_feedback" not in descriptions
        assert VALID_TEXT not in descriptions

    def test_registry_get_user_feedback_missing_skill(self):
        from src.core.skill_registry import SkillRegistry

        registry = SkillRegistry()
        assert registry.get_user_feedback("nope") is None


# ============================================================
# Agent 主循环 policy 注入（engine + ToolDispatcher.before_tools）
# ============================================================
# 33f9dba1 重构后的注入点：ToolDispatcher.before_tools（tools.py）由 AgentEngine
# 在首个 tool_start 之前调用（engine.py presentation 接线）；RuntimeExecution 只把
# verbose_config / verbose_state / policy_source=catalog 透传给 dispatcher
# （executor.py 构造 ToolDispatcher 处）。以下用例直测该接缝。

LONG_TOOL_RESULT = {"success": True, "content": "tool-ok"}


def _policy_dispatcher(*, tools, config, state):
    """组装带 verbose 参数的真实 ToolDispatcher（executor 结果按需供给）。"""
    return make_dispatcher(
        tools=tools,
        executor_results=[dict(LONG_TOOL_RESULT)] * 4,
        verbose_config=config,
        verbose_state=state,
    )


def _hooked_read(message=VALID_TEXT):
    if message is None:
        return FakeTool("read")
    return FakeTool("read", feedback=LongRunningFeedback(start_message=message))


async def _collect(gen):
    return [event async for event in gen]


class TestPolicyInjection:
    """policy 事件注入点（真实 AgentEngine + 真实 ToolDispatcher 集成）。"""

    async def test_policy_event_before_first_tool_start(self):
        """长任务 policy 事件出现在首个 tool_start 之前，且恰好一条。"""
        state = VerboseFeedbackState()
        h = _policy_dispatcher(
            tools=[_hooked_read(VALID_TEXT)], config=_fast_config(), state=state)
        model = Model(completion("", [call()]), completion("完成"))
        events = await run_engine(execution_state(), model, h.dispatcher)
        types = [e.get("type") for e in events]
        verbose_idx = [i for i, t in enumerate(types) if t == "verbose"]
        tool_start_idx = types.index("tool_start")
        assert len(verbose_idx) == 1, types
        assert verbose_idx[0] < tool_start_idx, "policy 提示必须在 tool_start 之前"
        verbose = events[verbose_idx[0]]
        assert verbose["source"] == "policy"
        assert verbose["data"] == VALID_TEXT
        assert verbose["eventId"].startswith("verbose_")

    async def test_no_verbose_without_verbose_params(self):
        """原始 generator（Desktop/internal 调用方）不被自动注入 verbose。"""
        h = _policy_dispatcher(tools=[_hooked_read(VALID_TEXT)],
                               config=None, state=None)
        model = Model(completion("", [call()]), completion("完成"))
        events = await run_engine(execution_state(), model, h.dispatcher)
        assert not [e for e in events if e.get("type") == "verbose"]

    async def test_disabled_config_gates_policy_injection(self):
        """enabled=False（含 force_disabled）时 dispatcher 注入点直接拦截：
        不产生任何 verbose 事件——直连 process_message_with_feedback 的调用方
        不会绕过全局关闭开关（对齐 prepare_turn_feedback 的 effective_enabled 门）。"""
        state = VerboseFeedbackState()
        h = _policy_dispatcher(tools=[_hooked_read(VALID_TEXT)],
                               config=_fast_config(enabled=False), state=state)
        model = Model(completion("", [call()]), completion("完成"))
        events = await run_engine(execution_state(), model, h.dispatcher)
        assert not [e for e in events if e.get("type") == "verbose"]

    async def test_empty_tool_call_no_verbose(self):
        """空 tool name 的调用被引擎规范化丢弃（engine.py normalize_calls），
        不触发任何 verbose / tool_start，执行正常完成。

        注：旧用例中「非法 JSON 参数归一为 {} 后不触发」的语义已不存在——
        现行引擎对非法 JSON 参数直接抛 INVALID_TOOL_ARGUMENTS（engine.py），
        由 tests/integration/test_agent_runtime_acceptance.py 覆盖。
        """
        state = VerboseFeedbackState()
        h = _policy_dispatcher(tools=[_hooked_read(VALID_TEXT)],
                               config=_fast_config(), state=state)
        model = Model(completion("", [call("c0", "")]))
        engine_state = execution_state()
        events = await run_engine(engine_state, model, h.dispatcher)
        assert not [e for e in events if e.get("type") == "verbose"], (
            "空/无效 tool call 不得触发 verbose"
        )
        assert not [e for e in events if e.get("type") == "tool_start"]
        assert engine_state.outcome == Outcome.COMPLETED

    async def test_parallel_tool_calls_single_policy_first_wins(self):
        """并行 tool calls 仍最多一条：按顺序取第一个长任务策略，不拼接。"""
        other_text = "正在生成行程表格，请稍候"
        state = VerboseFeedbackState()
        h = _policy_dispatcher(tools=[
            _hooked_read(VALID_TEXT),
            FakeTool("excel_tool", feedback=LongRunningFeedback(start_message=other_text)),
        ], config=_fast_config(), state=state)
        model = Model(
            completion("", [call("c0", "read"), call("c1", "excel_tool")]),
            completion("完成"),
        )
        events = await run_engine(execution_state(), model, h.dispatcher)
        verbose_events = [e for e in events if e.get("type") == "verbose"]
        assert len(verbose_events) == 1
        assert verbose_events[0]["data"] == VALID_TEXT
        assert verbose_events[0]["data"] != other_text, "不得拼接多条文案"

    @pytest.mark.parametrize(
        "bad_message",
        [
            "第一行\n第二行",
            "预计 30 秒后完成。",
        ],
    )
    async def test_invalid_template_degrades_to_system_fallback(self, bad_message):
        """Skill/Tool 模板违规：整体降级为 system fallback 文案，绝不透出原文。

        其余 8 类违规样例（超长/路径/JSON/命令/敏感键/代码块/HTML/百分比）与
        本文件 TestResolveFeedbackPolicy.test_skill_invalid_template_returns_empty_message_for_fallback
        的参数化矩阵一一对应（校验逻辑 validate_feedback_text 无第二实现），
        此处保留两条代表样例验证注入链路的降级接线。
        """
        state = VerboseFeedbackState()
        h = _policy_dispatcher(tools=[_hooked_read(bad_message)],
                               config=_fast_config(), state=state)
        model = Model(completion("", [call()]), completion("完成"))
        events = await run_engine(execution_state(), model, h.dispatcher)
        verbose_events = [e for e in events if e.get("type") == "verbose"]
        assert len(verbose_events) == 1
        assert verbose_events[0]["data"] == TEST_FALLBACK
        assert bad_message not in json.dumps(events, ensure_ascii=False, default=str)

    async def test_verbose_never_enters_llm_context(self):
        """同一轮后续及下一轮 provider 请求的 messages/system prompt 均无 verbose 文案。"""
        state = VerboseFeedbackState()
        h = _policy_dispatcher(tools=[_hooked_read(VALID_TEXT)],
                               config=_fast_config(), state=state)
        model = Model(completion("", [call()]), completion("完成"))
        engine_state = execution_state()
        events = await run_engine(engine_state, model, h.dispatcher)
        assert any(e.get("type") == "verbose" for e in events)
        # 捕获全部 LLM 调用的 system prompt 与 messages（model port 留存每次 checkpoint）
        assert len(model.requests) == 2
        for request, _call_id, _definitions in model.requests:
            assert VALID_TEXT not in str(request["system_prompt"])
            assert VALID_TEXT not in json.dumps(
                request["messages"], ensure_ascii=False, default=str
            )
            assert "user_feedback" not in json.dumps(
                request["messages"], ensure_ascii=False, default=str
            )
        # state.messages 是进入下一轮上下文的唯一权威来源，也不含 verbose 文案
        assert VALID_TEXT not in json.dumps(
            engine_state.messages, ensure_ascii=False, default=str
        )

    async def test_second_long_tool_in_same_turn_stays_single(self):
        """同一 turn 第二轮又命中长任务：状态机拦截，整轮仍只有一条。"""
        state = VerboseFeedbackState()
        h = _policy_dispatcher(tools=[_hooked_read(VALID_TEXT)],
                               config=_fast_config(), state=state)
        model = Model(
            completion("", [call("c1")]),
            completion("", [call("c2")]),
            completion("完成"),
        )
        events = await run_engine(execution_state(), model, h.dispatcher)
        assert len([e for e in events if e.get("type") == "verbose"]) == 1
        assert state.event is not None and state.emitted_at is not None


# ============================================================
# verbose 包装器（事件过滤 / 状态机联动 / task 清理 / 隔离）
# ============================================================


def _slow_producer(events_before=100, interval=0.05, total=0.4, terminal="response"):
    """持续产生技术事件、耗时较久的原始 generator（模拟慢 turn）。"""

    async def gen():
        start = time.monotonic()
        while time.monotonic() - start < total:
            yield {"type": "progress", "data": "working"}
            await asyncio.sleep(interval)
        yield {"type": terminal, "data": "done"}

    return gen


class TestVerboseWrapper:
    async def test_slow_turn_without_policy_emits_nothing(self):
        """无策略的慢 turn 不产生任何 verbose（2026-09-01 删除 system watchdog）。"""
        cfg = _fast_config()
        state = VerboseFeedbackState()
        events = await _collect(iter_with_verbose_feedback(
            _slow_producer(total=0.2, interval=0.05)(),
            surface="web", config=cfg, state=state,
        ))
        assert not [e for e in events if e.get("type") == "verbose"], (
            "无系统兜底：任何无策略 turn 都不得产生 verbose"
        )
        assert state.event is None
        assert events[-1]["type"] == "response"

    async def test_registered_policy_verbose_passthrough_single(self):
        """已注册的 policy verbose 原样透传；慢 turn 也恰好一条。"""
        cfg = _fast_config()
        state = VerboseFeedbackState()

        async def gen():
            event = make_verbose_event(new_verbose_event_id(), VALID_TEXT, "policy")
            if state.try_emit(event):
                yield event
            await asyncio.sleep(0.05)
            yield {"type": "progress", "data": "working"}
            yield {"type": "response", "data": "done"}

        events = await _collect(iter_with_verbose_feedback(
            gen(), surface="web", config=cfg, state=state,
        ))
        verbose_events = [e for e in events if e.get("type") == "verbose"]
        assert len(verbose_events) == 1
        assert verbose_events[0]["source"] == "policy"
        assert verbose_events[0]["data"] == VALID_TEXT
        assert events[-1]["type"] == "response"

    async def test_browser_human_required_marks_response_started(self):
        """browser_human_required 属用户可见终态：原样透传并置 response_started。

        为什么重要：browser 挂起已向用户发出「等待你的操作」，挂起后 state 必须
        拒绝任何后续提示（policy 发射守卫），用户可见语义不倒挂。
        """
        cfg = _fast_config()
        state = VerboseFeedbackState()

        async def gen():
            yield {"type": "progress", "data": "working"}
            yield {"type": "browser_human_required", "assistance_id": "a1"}
            await asyncio.sleep(0.05)
            # 挂起期间的补发尝试被状态机拒绝（不产 verbose）
            event = make_verbose_event(new_verbose_event_id(), VALID_TEXT, "policy")
            if state.try_emit(event):
                yield event
            yield {"type": "response", "data": "resumed"}

        events = await _collect(iter_with_verbose_feedback(
            gen(), surface="web", config=cfg, state=state,
        ))
        assert not [e for e in events if e.get("type") == "verbose"], (
            "browser_human_required 之后不得补发提示"
        )
        assert state.response_started is True, "挂起事件应按设计 §7 视为用户可见终态"
        assert events[-1]["type"] == "response"

    async def test_unregistered_verbose_dropped_by_wrapper(self):
        """绕过 try_emit 直塞事件流的未注册 verbose 被 wrapper 丢弃（防御分支）。"""
        cfg = _fast_config()
        state = VerboseFeedbackState()

        async def gen():
            # 模拟 producer 侧守卫失效：不经 state.try_emit 注册直接 yield
            yield make_verbose_event(new_verbose_event_id(), VALID_TEXT, "policy")
            yield {"type": "response", "data": "done"}

        events = await _collect(iter_with_verbose_feedback(
            gen(), surface="web", config=cfg, state=state,
        ))
        assert [e.get("type") for e in events] == ["response"], (
            "未注册的 verbose 必须被 wrapper 丢弃，每轮最多一条不依赖 producer 自觉"
        )
        assert state.event is None

    async def test_observer_records_metadata_not_text(self):
        """observer 记录 source/长度/time-to-first/抑制原因，结构上不含正文。"""
        cfg = _fast_config()
        state = VerboseFeedbackState()
        observer = RecordingObserver()

        async def gen():
            event = make_verbose_event(new_verbose_event_id(), VALID_TEXT, "policy")
            if state.try_emit(event):
                yield event
            yield {"type": "response", "data": "done"}

        events = await _collect(iter_with_verbose_feedback(
            gen(), surface="web", config=cfg, state=state, observer=observer,
        ))
        assert any(e.get("type") == "verbose" for e in events)
        assert len(observer.emitted) == 1
        record = observer.emitted[0]
        assert record["source"] == "policy"
        assert record["text_length"] == len(VALID_TEXT)
        assert record["time_to_first_seconds"] >= 0
        assert VALID_TEXT not in json.dumps(observer.emitted)

    async def test_two_concurrent_sessions_state_isolated(self):
        """两个并发 session：状态完全隔离，互不串发提示。"""
        cfg = _fast_config()
        state_a = VerboseFeedbackState()
        state_b = VerboseFeedbackState()

        async def consume(state, with_verbose):
            async def gen():
                if with_verbose:
                    event = make_verbose_event(
                        new_verbose_event_id(), VALID_TEXT, "policy")
                    if state.try_emit(event):
                        yield event
                yield {"type": "progress", "data": "working"}
                await asyncio.sleep(0.04)
                yield {"type": "response", "data": "done"}

            events = []
            async for event in iter_with_verbose_feedback(
                gen(), surface="channel", config=cfg, state=state,
            ):
                events.append(event.get("type"))
            return events

        events_a, events_b = await asyncio.gather(
            consume(state_a, with_verbose=True),
            consume(state_b, with_verbose=False),
        )
        assert events_a.count("verbose") == 1
        assert "verbose" not in events_b
        assert state_a.event is not None and state_a.emitted_at is not None
        assert state_b.event is None and state_b.emitted_at is None
        assert state_a is not state_b

    async def test_no_leftover_tasks_normal_error_and_cancel(self):
        """正常/异常/取消路径均无残留 producer task。"""

        async def normal_gen():
            yield {"type": "progress"}
            yield {"type": "response", "data": "ok"}

        async def error_gen():
            yield {"type": "progress"}
            raise RuntimeError("agent boom")

        async def consume(gen, cfg, state):
            n = 0
            async for _ in iter_with_verbose_feedback(gen(), surface="web", config=cfg, state=state):
                n += 1
            return n

        baseline = len(asyncio.all_tasks())

        # 正常
        assert await consume(normal_gen, _fast_config(), VerboseFeedbackState()) >= 1
        # 异常：producer 异常必须原样传播
        with pytest.raises(RuntimeError):
            await consume(error_gen, _fast_config(), VerboseFeedbackState())
        # 取消：消费方中途取消
        task = asyncio.ensure_future(consume(
            _slow_producer(total=1.0, interval=0.02), _fast_config(), VerboseFeedbackState(),
        ))
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        for _ in range(10):
            await asyncio.sleep(0)
        assert len(asyncio.all_tasks()) <= baseline, (
            f"遗留 task：baseline={baseline}，after={len(asyncio.all_tasks())}"
        )

    async def test_disabled_config_is_pure_passthrough(self):
        """配置未生效：纯透传（含未注册的 verbose 也原样通过，不做任何加工）。"""
        state = VerboseFeedbackState()

        async def gen():
            yield make_verbose_event(new_verbose_event_id(), VALID_TEXT, "policy")
            yield {"type": "response", "data": "done"}

        events = await _collect(iter_with_verbose_feedback(
            gen(), surface="web", config=_fast_config(enabled=False), state=state,
        ))
        assert [e.get("type") for e in events] == ["verbose", "response"]

    async def test_invalid_surface_rejected(self):
        cfg = _fast_config()
        with pytest.raises(ValueError):
            async for _ in iter_with_verbose_feedback(
                _slow_producer(total=0.01)(), surface="cli", config=cfg,
                state=VerboseFeedbackState(),
            ):
                pass

    async def test_validate_feedback_text_boundaries(self):
        """文本校验边界补充（内核级）：1/60 字合法，空白/61 字非法。"""
        assert validate_feedback_text("好") == "好"
        text = "好" * 59 + "。"
        assert validate_feedback_text(text) == text
        assert validate_feedback_text("") == ""
        assert validate_feedback_text("   ") == ""
        assert validate_feedback_text("好" * 61) == ""


# ============================================================
# process_message_sync / process_message_with_feedback 包装入口
# ============================================================
# 两个包装入口仍在 Agent 薄壳（agent.py）。驱动方式：Agent.__new__ 跳过
# __init__ 副作用 + 实例属性注入 _identity / _execution 替身（薄壳
# _process_message_impl 只代理 execution 的事件流，agent.py:88-102 注释明确
# 保留 legacy embedders / tests 的替换能力），真实驱动包装入口的
# gating / state 复用 / 事件过滤逻辑。policy 注入本身已由
# TestPolicyInjection 在 engine+dispatcher 接缝覆盖。

class StubExecution:
    """RuntimeExecution 替身：按脚本产出事件，模拟 policy 注入语义。

    "policy" 步骤与真实 ToolDispatcher.before_tools 一致：仅当编排层传入
    verbose_config（非 None）时，用 make_verbose_event + verbose_state.try_emit
    注册并 yield（None 门控）。run 的入参 kwargs 全量记录供断言。
    """

    def __init__(self, script, delay=0.0):
        self.script = list(script)
        self.delay = delay
        self.state = None
        self.run_calls = []

    async def run(self, text, user=None, attachments=None, request_context=None,
                  **kwargs):
        self.run_calls.append(kwargs)
        config = kwargs.get("verbose_config")
        feedback_state = kwargs.get("verbose_state")
        for step in self.script:
            if step == "policy":
                if config is None:
                    continue
                event = make_verbose_event(new_verbose_event_id(), VALID_TEXT, "policy")
                if feedback_state is not None and feedback_state.try_emit(event):
                    yield event
            elif step == "tool_start":
                yield {"type": "tool_start", "toolCallId": "c1", "toolName": "read",
                       "displayName": "read", "toolArgs": {}}
            elif step == "progress":
                if self.delay:
                    await asyncio.sleep(self.delay)
                yield {"type": "progress", "data": "working"}
            elif step == "response":
                yield {"type": "response", "data": "完成"}


def _shell_agent(script, *, delay=0.0):
    """Agent.__new__ + 实例属性注入（不触库、不装配真实资源）。"""
    agent = Agent.__new__(Agent)
    agent.mode = AgentMode.MASTER
    agent.is_master = True
    agent.subagent_config = None
    agent.session_id = None
    agent.execution_id = None
    agent.parent_plan_manager = None
    agent._default_tenant_id = None
    agent._default_user_id = None
    agent._prototype = None
    agent._identity = lambda session_id, user=None: Identity("tenant-1", "user-1", session_id)
    execution = StubExecution(script, delay=delay)
    agent._execution = (lambda identity, cancel_check=None, record=None, task_record=None:
                        execution)
    return agent, execution


class TestSyncAndWrapperEntries:
    async def test_sync_callback_gets_single_policy_verbose_and_reuses_state(self):
        """渠道 sync 路径：有 callback 时启用 wrapper；外部传入 state 必须复用。"""
        agent, _execution = _shell_agent(["policy", "tool_start", "response"])
        state = VerboseFeedbackState()
        received = []

        async def callback(event):
            received.append(event)

        result = await agent.process_message_sync(
            "帮我处理", "s-sync", progress_callback=callback,
            feedback_state=state, verbose_config=_fast_config(),
        )
        assert result == "完成"
        verbose_events = [e for e in received if e.get("type") == "verbose"]
        assert len(verbose_events) == 1
        assert verbose_events[0]["source"] == "policy"
        assert verbose_events[0]["data"] == VALID_TEXT
        # 外部传入的 state 被复用（未内部另建）
        assert state.event is verbose_events[0]

    async def test_sync_slow_tool_without_policy_no_verbose(self):
        """渠道 sync 路径：慢工具但无策略命中 → 无任何 verbose（无系统兜底）。"""
        agent, _execution = _shell_agent(["progress", "progress", "response"], delay=0.05)
        state = VerboseFeedbackState()
        received = []

        async def callback(event):
            received.append(event)

        result = await agent.process_message_sync(
            "帮我处理", "s-slow", progress_callback=callback,
            feedback_state=state, verbose_config=_fast_config(),
        )
        assert result == "完成"
        assert not [e for e in received if e.get("type") == "verbose"], (
            "无系统兜底：无策略的慢 turn 不得产生 verbose"
        )
        assert state.event is None

    async def test_sync_without_callback_not_affected(self):
        """scheduler 等无用户表面（callback=None）：不启用 wrapper、不注入 verbose。"""
        agent, execution = _shell_agent(["policy", "response"])
        state = VerboseFeedbackState()
        result = await agent.process_message_sync(
            "帮我处理", "s-sched",
            feedback_state=state, verbose_config=_fast_config(),
        )
        assert result == "完成"
        assert state.event is None, "无用户表面时不得产生任何 verbose"
        # 编排层未注入 verbose_config（dispatcher 的 None 门控因此关闭注入）
        assert execution.run_calls[0]["verbose_config"] is None

    async def test_sync_disabled_config_gates_policy_injection(self):
        """配置关闭（enabled=False）：包装入口 gating 把 verbose_config 归 None，
        内层不注入任何 verbose——这是该语义在现行架构中的落点
        （prepare_turn_feedback；旧 impl 内联 effective_enabled 门控已不在
        注入点，见文件头说明）。
        """
        agent, execution = _shell_agent(["policy", "tool_start", "response"])
        state = VerboseFeedbackState()
        received = []

        async def callback(event):
            received.append(event)

        result = await agent.process_message_sync(
            "帮我处理", "s-off", progress_callback=callback,
            feedback_state=state, verbose_config=_fast_config(enabled=False),
        )
        assert result == "完成"
        assert not [e for e in received if e.get("type") == "verbose"]
        assert state.event is None
        assert execution.run_calls[0]["verbose_config"] is None

    async def test_sync_state_reused_across_reruns(self):
        """cancel/merge 重跑场景模拟：两次 sync 复用同一 owner state，累计仍一条。"""
        agent, _execution = _shell_agent(["policy", "response"])
        state = VerboseFeedbackState()
        received = []

        async def callback(event):
            received.append(event)

        # 两次 sync 模拟同 owner 的两个 attempt（只看 policy 提示）
        first = await agent.process_message_sync(
            "A", "s-rerun", progress_callback=callback,
            feedback_state=state, verbose_config=_fast_config(),
        )
        assert first == "完成"
        second = await agent.process_message_sync(
            "B", "s-rerun", progress_callback=callback,
            feedback_state=state, verbose_config=_fast_config(),
        )
        assert second == "完成"
        # owner 级 state 跨 attempt 复用：两次长任务命中累计仍只有一条 verbose
        verbose_events = [e for e in received if e.get("type") == "verbose"]
        assert len(verbose_events) == 1, (
            "重跑 attempt 复用 owner 级 state，不得重复提示"
        )
        assert verbose_events[0]["source"] == "policy"
        assert state.event is verbose_events[0]

    async def test_process_message_with_feedback_wrapper_entry(self):
        """显式包装入口：Web/渠道用它，policy 事件在 tool_start 前且最终回复完好。"""
        agent, _execution = _shell_agent(["policy", "tool_start", "response"])
        state = VerboseFeedbackState()
        observer = RecordingObserver()
        events = await _collect(agent.process_message_with_feedback(
            surface="web",
            config=_fast_config(),
            state=state,
            observer=observer,
            user_input="帮我处理",
            session_id="s-web",
        ))
        types = [e.get("type") for e in events]
        assert types.index("verbose") < types.index("tool_start")
        assert events[types.index("response")]["data"] == "完成"
        assert state.event is not None
        assert len(observer.emitted) == 1
        assert observer.emitted[0]["source"] == "policy"

    async def test_process_message_with_feedback_invalid_surface(self):
        agent, execution = _shell_agent([])
        with pytest.raises(ValueError):
            agent.process_message_with_feedback(
                surface="sms", user_input="hi", session_id="s",
            )
        assert execution.run_calls == [], "非法 surface 必须在校验期拒绝，不得触执行"


# ============================================================
# 配置接入（settings.agent / config.yaml / kill switch）
# ============================================================


class TestConfigIntegration:
    def test_settings_agent_declared_and_defaults_on(self):
        """settings.agent 为有类型模型；默认与 YAML 一致（2026-09-01 起全局启用）；reply_style 兼容。"""
        from src.config.settings import AgentConfig, AgentVerboseFeedbackConfig, settings

        assert isinstance(settings.agent, AgentConfig)
        # 既有消费点 _resolve_reply_style 的动态访问路径保持兼容
        agent_cfg = getattr(settings, "agent", None)
        assert getattr(agent_cfg, "reply_style", None) == "human-like"
        vf = settings.agent.verbose_feedback
        assert isinstance(vf, AgentVerboseFeedbackConfig)
        assert vf.enabled is True
        assert vf.force_disabled is False
        assert vf.max_per_turn == 1
        assert vf.max_text_chars == 60
        assert vf.delivery_timeout_seconds == 5
        assert vf.fallback_message == DEFAULT_FALLBACK_MESSAGE

    def test_default_feedback_config_matches_settings(self):
        cfg = default_feedback_config()
        assert cfg.enabled is True
        assert cfg.force_disabled is False
        assert cfg.effective_enabled is True
        assert cfg.max_per_turn == 1
        assert cfg.max_text_chars == 60
        assert cfg.delivery_timeout_seconds == 5
        assert cfg.fallback_message == DEFAULT_FALLBACK_MESSAGE

    def test_kill_switch_beats_request_config(self):
        """force_disabled=true 压过请求级 enabled=true（紧急回滚唯一手段）。"""
        from src.config.settings import AgentVerboseFeedbackConfig

        yaml_like = AgentVerboseFeedbackConfig(enabled=True, force_disabled=True)
        runtime = VerboseFeedbackConfig(
            enabled=yaml_like.enabled,
            force_disabled=yaml_like.force_disabled,
        )
        assert runtime.effective_enabled is False

    def test_config_is_frozen(self):
        from dataclasses import FrozenInstanceError

        cfg = VerboseFeedbackConfig()
        with pytest.raises(FrozenInstanceError):
            cfg.enabled = True


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])


class TestForceDisabledGatesInjection:
    """force_disabled（紧急回滚）最高优先级：注入点必须零 verbose 事件。"""

    async def test_force_disabled_config_gates_policy_injection(self):
        from src.core.verbose_feedback import VerboseFeedbackConfig

        config = VerboseFeedbackConfig(enabled=True, force_disabled=True,
                                       fallback_message=TEST_FALLBACK)
        state = VerboseFeedbackState()
        h = _policy_dispatcher(tools=[_hooked_read(VALID_TEXT)],
                               config=config, state=state)
        model = Model(completion("", [call()]), completion("完成"))
        events = await run_engine(execution_state(), model, h.dispatcher)
        assert not [e for e in events if e.get("type") == "verbose"]
