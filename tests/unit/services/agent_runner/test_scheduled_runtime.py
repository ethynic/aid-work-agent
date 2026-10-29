"""scheduled 非交互执行模式单测（设计 §5.3/§5.5，Phase 2）。

- 零历史：ContextAssembler 按 source=='scheduled' 跳过历史/摘要/压缩装配，
  消息仅含本次任务 prompt 与补充指令（augmentations）；
- 非交互装配：ToolControlSet(non_interactive) 的 definitions 与 dispatch
  （controls.get）同一口径滤除 ask_user/clarify，chat 执行不受影响；
- 子执行继承：ChildExecution 继承父 identity.source（scheduled → 非交互装配），
  根执行的 prompt_augmentations 经 request_context 到达子执行上下文装配；
- chat_only 过滤机制：非 chat 来源注销 chat_only 工具（当前无声明，机制就位）。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.agent_engine.contracts import AgentMode, Identity, Outcome
from src.core.request_context import AgentRequestContext
from src.services.agent_runner.runtime.child import ChildExecution, child_factory
from src.services.agent_runner.runtime.context_assembler import ContextAssembler
from src.services.agent_runner.runtime.profile import filter_source_visible_tools
from src.tools.assembly import AgentToolBundle
from src.tools.control_set import ControlToolDependencies, ToolControlSet


# ==================== 零历史 ====================


def make_assembler(identity):
    attachments = MagicMock()
    attachments.prepare.return_value = ("enhanced-text", None, None)
    attachments.build_image_reference_content.return_value = (None, None)
    attachments._build_multimodal_user_content.return_value = None
    history = MagicMock()
    # MagicMock 拒绝自动生成 assert_* 属性，显式挂可调用替身
    history.reader = SimpleNamespace(assert_authorized=MagicMock())
    history.memory.get_context.return_value = []
    history.active_summary.return_value = None
    compression = MagicMock()
    compression.event = None
    compression._run_compression_phase = AsyncMock()
    remember = MagicMock()
    remember._handle_remember_intent = AsyncMock()
    prompt_sources = MagicMock()
    assembler = ContextAssembler(identity=identity, role=AgentMode.MASTER,
        profile_config=None, history=history, compression=compression,
        prompt_sources=prompt_sources, skills=MagicMock(), remember=remember,
        attachments=attachments, visibility=MagicMock())
    # visibility.prime 是协程
    assembler.visibility.prime = AsyncMock()
    return assembler, history, compression


def _run(assembler):
    import asyncio
    return asyncio.run(assembler.prepare("生成日报", execution_id="ae_1"))


def test_scheduled_assembles_zero_history():
    identity = Identity("t1", "u1", "sess_task", "scheduled", "web")
    assembler, history, compression = make_assembler(identity)
    state, _events = _run(assembler)
    # 不读会话历史/摘要/压缩（设计 §5.5：历次结果是展示账本）
    history._reload_memory_from_db.assert_not_called()
    history.memory.get_context.assert_not_called()
    history.active_summary.assert_not_called()
    compression._run_compression_phase.assert_not_called()
    # 消息仅含本次任务 prompt
    assert [message["role"] for message in state.messages] == ["user"]
    assert state.messages[0]["content"] == "enhanced-text"


def test_scheduled_zero_history_keeps_augmentations():
    identity = Identity("t1", "u1", "sess_task", "scheduled", "web")
    assembler, history, _ = make_assembler(identity)
    state, _events = _run(assembler)
    # 补充指令经 request_context 装配为受信补充 user 消息（非系统提示词）
    import asyncio
    state, _events = asyncio.run(assembler.prepare("生成日报",
        request_context=AgentRequestContext(prompt_augmentations=("本次为定时自动执行，无法向用户提问",))))
    assert [m["content"] for m in state.messages] == [
        "enhanced-text", "本次为定时自动执行，无法向用户提问"]
    history._reload_memory_from_db.assert_not_called()


def test_chat_source_still_reads_history():
    identity = Identity("t1", "u1", "sess_chat", "chat", "web")
    assembler, history, compression = make_assembler(identity)
    _run(assembler)
    history._reload_memory_from_db.assert_called_once()
    history.memory.get_context.assert_called_once()
    compression._run_compression_phase.assert_called_once()


# ==================== 非交互装配（definitions 与 dispatch 同口径） ====================


class FakeSkillRegistry:
    def get_skill_tool_definition(self):
        return {"name": "use_skill", "description": "skills", "input_schema": {}}


def make_controls(non_interactive=False):
    return ToolControlSet(ControlToolDependencies(
        plan_manager=object(), skill_registry=FakeSkillRegistry(),
        skill_executor=object(), tool_registry=object(),
        non_interactive=non_interactive))


def test_interactive_tools_visible_by_default():
    controls = make_controls()
    names = {item["name"] for item in controls.definitions(available_subagents=[])}
    assert {"ask_user", "clarify"} <= names
    assert controls.get("ask_user") is not None and controls.get("clarify") is not None


def test_non_interactive_filters_definitions_and_dispatch():
    controls = make_controls(non_interactive=True)
    names = {item["name"] for item in controls.definitions(available_subagents=[])}
    # LLM 可见 definitions 与 dispatch 合法性（controls.get）同一口径滤除
    assert "ask_user" not in names and "clarify" not in names
    assert controls.get("ask_user") is None and controls.get("clarify") is None
    # 非提问控制工具与技能入口保留
    assert {"skill_execute", "create_plan", "use_skill"} <= names
    guides = controls.usage_guides()
    assert "### clarify" not in guides and "### ask_user" not in guides


# ==================== chat_only 过滤机制 ====================


class FakeRegistry:
    def __init__(self, tools):
        self._tools = dict(tools)

    def list_tools(self):
        return list(self._tools)

    def get_tool(self, name):
        return self._tools.get(name)

    def remove_many(self, names):
        for name in names:
            self._tools.pop(name, None)


def test_chat_only_tools_hidden_for_non_chat_sources():
    registry = FakeRegistry({
        "chat_only_tool": SimpleNamespace(name="chat_only_tool", chat_only=True),
        "common_tool": SimpleNamespace(name="common_tool", chat_only=False),
    })
    bundle = AgentToolBundle(registry=registry, controls=object(),
                             discovered_names=(), final_names=())
    filter_source_visible_tools(bundle, "scheduled")
    assert registry.list_tools() == ["common_tool"]


def test_chat_only_tools_kept_for_chat_source():
    registry = FakeRegistry({
        "chat_only_tool": SimpleNamespace(name="chat_only_tool", chat_only=True),
    })
    bundle = AgentToolBundle(registry=registry, controls=object(),
                             discovered_names=(), final_names=())
    filter_source_visible_tools(bundle, "chat")
    assert registry.list_tools() == ["chat_only_tool"]


def test_real_chat_only_scheduler_tools_filtered_for_scheduled_source():
    """真实 chat_only 工具（Phase 4 定时任务两工具）一致性验证：

    Phase 2 CR 遗留项——工具声明 chat_only 后验证过滤口径：scheduled 执行的
    registry（definitions 与 dispatch 同源）不含定时任务工具；chat 保留。
    """
    from src.tools.registry import ToolRegistry, discover_tool_classes
    registry = ToolRegistry()
    for cls in discover_tool_classes().values():
        registry.register(cls())
    bundle = AgentToolBundle(registry=registry, controls=object(),
                             discovered_names=(), final_names=())
    filter_source_visible_tools(bundle, "scheduled")
    assert "create_scheduled_task" not in registry.list_tools()
    assert "manage_scheduled_task" not in registry.list_tools()

    registry_chat = ToolRegistry()
    for cls in discover_tool_classes().values():
        registry_chat.register(cls())
    bundle_chat = AgentToolBundle(registry=registry_chat, controls=object(),
                                  discovered_names=(), final_names=())
    filter_source_visible_tools(bundle_chat, "chat")
    assert "create_scheduled_task" in registry_chat.list_tools()
    assert "manage_scheduled_task" in registry_chat.list_tools()


# ==================== 子执行继承（source + augmentations） ====================


class FakeChildConfig:
    dir_name = "child-1"
    version = "v1"

    def model_dump(self, mode="json"):
        return {"name": "child-1"}


class FakeChildRuntime:
    def __init__(self, resources, identity, control=None, observer=None,
                 history_reader=None, profile_id=None, instance_id=None):
        self.resources, self.identity = resources, identity
        self.run_kwargs = None
        self.profile_id, self.instance_id = profile_id, instance_id
        self.state = SimpleNamespace(outcome=Outcome.COMPLETED, output="done",
                                     images=[], checkpoint=lambda: {})

    async def run(self, text, **kwargs):
        self.run_kwargs = dict(text=text, **kwargs)
        return
        yield  # pragma: no cover


def _install_child_fakes(monkeypatch, calls):
    class FakeResources:
        def __init__(self, **kwargs):
            calls["resources_kwargs"] = kwargs
    monkeypatch.setattr("src.services.agent_runner.runtime.profile.RuntimeResources", FakeResources)
    monkeypatch.setattr("src.services.agent_runner.runtime.executor.RuntimeExecution", FakeChildRuntime)


def test_child_execution_inherits_scheduled_source(monkeypatch):
    """子执行继承父 identity.source：scheduled 根的非交互装配口径到达子执行。"""
    calls = {}
    _install_child_fakes(monkeypatch, calls)
    identity = Identity("t1", "u1", "sess_task", "scheduled", "web")
    child = ChildExecution(identity, FakeChildConfig(), "exec_child_1")
    assert calls["resources_kwargs"]["source"] == "scheduled"
    assert calls["resources_kwargs"]["session_kind"] == "web"


def test_child_execution_receives_root_augmentations(monkeypatch):
    """根执行的 prompt_augmentations 经 child_factory 传入子执行上下文装配。"""
    calls = {}
    _install_child_fakes(monkeypatch, calls)
    identity = Identity("t1", "u1", "sess_task", "scheduled", "web")
    build = child_factory(identity, prompt_augmentations=("本次为定时自动执行，无法向用户提问",))
    child = build(config=FakeChildConfig(), execution_id="exec_child_1")
    runtime = child.runtime
    import asyncio
    asyncio.run(child.execute_as_subagent("做点事", "sess_task"))
    request_context = runtime.run_kwargs["request_context"]
    assert request_context is not None
    assert request_context.prompt_augmentations == ("本次为定时自动执行，无法向用户提问",)


def test_child_execution_without_augmentations_has_no_request_context(monkeypatch):
    """无补充指令（普通 chat 根）时子执行不注入 request_context，行为不变。"""
    calls = {}
    _install_child_fakes(monkeypatch, calls)
    identity = Identity("t1", "u1", "sess_chat", "chat", "web")
    build = child_factory(identity)
    child = build(config=FakeChildConfig(), execution_id="exec_child_2")
    assert calls["resources_kwargs"]["source"] == "chat"
    import asyncio
    asyncio.run(child.execute_as_subagent("做点事", "sess_chat"))
    assert child.runtime.run_kwargs["request_context"] is None
