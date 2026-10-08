"""Real compatibility/runtime composition, fake external model/tool/storage I/O.

AgentEngine, ContextAssembler, prompt/skill/history adapters, registry and
ToolExecutor remain real. These are not durable-service or live-device tests.
"""
import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.agent_engine import AgentEngine, AgentMode
from src.core.agent_engine.contracts import Identity
from src.core.request_context import AgentRequestContext
from src.models.subagent import SubagentConfig
from src.tools.base import BaseTool
from src.tools.context import current_tool_execution_context
from src.tools.executor import ToolExecutor
from src.tools.registry import ToolRegistry


def response(text="answer", calls=()):
    return {"content": text, "tool_calls": list(calls),
            "usage": {"prompt_tokens": 7, "completion_tokens": 3, "cached_tokens": 2}}


def tool_call(name="acceptance_probe", identifier="probe-1", arguments=None):
    return {"id": identifier, "type": "function", "function": {
        "name": name, "arguments": json.dumps(arguments or {})}}


class Gateway:
    def __init__(self, *replies):
        self.replies = list(replies or [response()])
        self.requests = []

    def get_model_name(self):
        return "acceptance-model"

    def get_provider_name(self):
        return "qwen"

    async def chat_with_tools(self, **kwargs):
        self.requests.append(deepcopy(kwargs))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return deepcopy(reply)


class ProbeTool(BaseTool):
    catalog = False
    name = "acceptance_probe"
    description = "Read a deterministic test artifact."
    parameters_schema = {"type": "object", "properties": {}}

    def __init__(self):
        self.contexts = []

    async def execute(self, **kwargs):
        context = current_tool_execution_context()
        self.contexts.append(context)
        return {"success": True, "data": {"tenant": context.tenant_id}, "images": [{
            "file_id": f"image-{context.tenant_id}",
            "download_url": "/api/files/test/download", "display_name": "test.png"}]}


class Reader:
    def __init__(self, messages=()):
        self.messages = list(messages)
        self.calls = []
        self.token_updates = []

    def assert_authorized(self):
        # Authorization is a repository I/O boundary here; real tenant/session
        # SQL authorization is covered separately against isolated PostgreSQL.
        self.calls.append(("authorized",))

    def update_context_tokens(self, count):
        self.token_updates.append(count)

    def read_web(self, session_id, **kwargs):
        self.calls.append(("web", session_id, kwargs))
        return deepcopy(self.messages)

    def read_channel(self, session_id, **kwargs):
        self.calls.append(("channel", session_id, kwargs))
        return deepcopy(self.messages)


@pytest.fixture
def boundaries(monkeypatch, tmp_path):
    import hashlib
    import shutil
    from src.core.plan_manager import PlanManager
    from src.config.settings import settings
    from src.saas.services.tenant_skill_cache import TenantSkillCache
    monkeypatch.setattr(settings.memory.mid_term, "enabled", False)
    monkeypatch.setattr(settings.memory.long_term, "enabled", False)
    monkeypatch.setattr("src.services.agent_runner.runtime.history_repository.HistoryRepository.legacy_session_kind",
                        lambda session_id, tenant_id=None: "web")
    monkeypatch.setattr("src.db.models.MessageDB.list_by_session", lambda *args, **kwargs: [])
    monkeypatch.setattr("src.db.subagent_env_var.SubagentEnvVarDB.get_vars", lambda *args: [])
    monkeypatch.setattr("src.saas.db.subscription_db.SubscriptionDB.get_allowed_subagent_types",
                        lambda *args: [])
    monkeypatch.setattr("src.services.agent_runner.runtime.prompt_sources.PromptSources._load_extra_md",
                        lambda self: f"TENANT_EXTRA_{self._init_tenant_id}")
    monkeypatch.setattr("src.services.agent_runner.runtime.prompt_sources.PromptSources._load_template_files",
                        lambda self: [])
    monkeypatch.setattr("src.services.agent_runner.runtime.prompt_sources.PromptSources._load_knowledge_sources",
                        lambda self: [])
    monkeypatch.setattr("src.saas.services.tenant_skill_cache.tenant_skill_cache", TenantSkillCache())

    def temporary_plan_manager(directory, *, execution_scope=None, plan_store=None):
        suffix = hashlib.sha256(str(execution_scope).encode()).hexdigest()
        return PlanManager(tmp_path / "plans" / suffix, execution_scope=execution_scope, plan_store=plan_store)
    monkeypatch.setattr("src.services.agent_runner.runtime.profile.PlanManager", temporary_plan_manager)

    def tenant_directory(tenant_id):
        directory = tmp_path / tenant_id / "skills"
        skill = directory / "acceptance_skill"
        skill.mkdir(parents=True, exist_ok=True)
        (skill / "SKILL.md").write_text(
            "---\nname: acceptance_skill\ndescription: TENANT_SKILL_" + tenant_id +
            "\nversion: 1.0.0\n---\n" + f"Full guide for {tenant_id}.\n" + "x" * 18000)
        return directory

    monkeypatch.setattr("src.saas.services.skill_resolver.SkillResolver.get_tenant_skills_dir", tenant_directory)
    calls = []
    original = AgentEngine.run

    async def observed_engine(self, state, *args, **kwargs):
        calls.append(state)
        async for event in original(self, state, *args, **kwargs):
            yield event

    monkeypatch.setattr(AgentEngine, "run", observed_engine)
    try:
        yield calls
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def config():
    return SubagentConfig(name="Acceptance", dir_name="acceptance", system_prompt="PROFILE_CONSTRAINT",
        tools={"inherit": False, "allowed": ["use_skill", "clarify"]},
        skills={"allowed": ["acceptance_skill"]})


def registry_with_probe():
    registry, probe = ToolRegistry(), ProbeTool()
    registry.register(probe)
    return registry, probe


class Record:
    def __init__(self, tenant="a"):
        self.tenant_id, self.user_id, self.session_id = f"tenant-{tenant}", f"user-{tenant}", f"session-{tenant}"
        self.source_type, self.user_message, self.provider = "chat", "request", "qwen"
        self.usages, self.iterations = [], 0

    def add_llm_usage(self, usage):
        self.usages.append(deepcopy(usage))

    def increment_iterations(self):
        self.iterations += 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [AgentMode.MASTER, AgentMode.STANDALONE])
async def test_public_process_sync_composes_real_runtime_and_reaches_unique_engine(mode, boundaries):
    from src.core.agent import Agent
    agent = Agent(mode=mode, subagent_config=config() if mode != AgentMode.MASTER else None)
    gateway = Gateway(response("", [tool_call()]), response("final"))
    registry, probe = registry_with_probe()
    agent.llm, agent.tool_registry, agent.tool_executor = gateway, registry, ToolExecutor(registry)
    user = SimpleNamespace(tenant_id="tenant-a", user_id="user-a", name="A", phone=None)
    result = await agent.process_message_sync("read artifact", "session-a", user=user,
        request_context=AgentRequestContext(request_data={"trusted_input": "value"}))
    assert str(result) == "final"
    assert len(boundaries) == 1 and boundaries[0].role == mode
    assert len(gateway.requests) == 2 and len(probe.contexts) == 1
    context = probe.contexts[0]
    assert (context.tenant_id, context.user_id, context.session_id, context.tool_call_id) == (
        "tenant-a", "user-a", "session-a", "probe-1")
    assert context.request_data["trusted_input"] == "value"
    assert result.images[0]["file_id"] == "image-tenant-a"
    assert current_tool_execution_context() is None
    if mode == AgentMode.STANDALONE:
        assert "TENANT_EXTRA_tenant-a" in gateway.requests[0]["system_prompt"]
        assert "TENANT_SKILL_tenant-a" in gateway.requests[0]["system_prompt"]
        assert "PROFILE_CONSTRAINT" in gateway.requests[0]["system_prompt"]


@pytest.mark.asyncio
async def test_real_runtime_channel_history_keeps_human_markers_and_tool_pairs(boundaries):
    from src.services.agent_runner.runtime.executor import RuntimeExecution
    from src.services.agent_runner.runtime.profile import RuntimeResources
    identity = Identity("tenant-a", "user-a", "session-a", "wecom_kf", "channel")
    resources = RuntimeResources(mode=AgentMode.STANDALONE, subagent_config=config())
    gateway = Gateway()
    resources.llm = gateway
    history = Reader([
        {"role": "user", "content": "previous question"},
        {"role": "assistant", "content": "", "metadata": {"tool_calls": [tool_call(identifier="old")] }},
        {"role": "tool", "content": "old result", "metadata": {"tool_call_id": "old"}},
        {"role": "user", "content": "[人工客服] already handled", "metadata": {"source": "servicer"}},
        {"role": "system", "content": "TRANSFERRED_TO_HUMAN"},
    ])
    runtime = RuntimeExecution(resources, identity, history_reader=history)
    events = [event async for event in runtime.run("new question")]
    assert len(boundaries) == 1
    assert any(item[0] == "channel" for item in history.calls)
    request = gateway.requests[0]
    assert "TRANSFERRED_TO_HUMAN" in request["system_prompt"]
    assert not [m for m in request["messages"] if m["role"] == "system"]
    assert next(m for m in request["messages"] if m["content"] == "[人工客服] already handled")["role"] == "assistant"
    assert next(m for m in request["messages"] if m["role"] == "tool")["tool_call_id"] == "old"
    assert events[-1]["type"] == "response"


@pytest.mark.asyncio
async def test_real_runtime_loads_full_skill_guide_and_reports_every_actual_model_usage(boundaries):
    from src.services.agent_runner.runtime.executor import RuntimeExecution, CompatibilityObserver
    from src.services.agent_runner.runtime.profile import RuntimeResources
    identity = Identity("tenant-a", "user-a", "session-a")
    resources = RuntimeResources(mode=AgentMode.STANDALONE, subagent_config=config())
    gateway = Gateway(response("", [tool_call("use_skill", arguments={"skill": "acceptance_skill"})]), response())
    resources.llm = gateway
    usage = []
    record = SimpleNamespace(provider="qwen", add_llm_usage=lambda fact: usage.append(fact),
                             increment_iterations=lambda: None)
    runtime = RuntimeExecution(resources, identity, observer=CompatibilityObserver(record), history_reader=Reader())
    [event async for event in runtime.run("load skill")]
    assert len(boundaries) == 1 and len(usage) == 2
    assert usage == [reply["usage"] for reply in [response(), response()]]
    tool_message = next(m for m in gateway.requests[1]["messages"] if m["role"] == "tool")
    assert "Full guide for tenant-a" in tool_message["content"]
    assert len(tool_message["content"]) > 18000
    assert runtime.state.loaded_skills["acceptance_skill"] == "1.0.0"
    assert runtime.history.reader.token_updates == [10, 10]


@pytest.mark.asyncio
async def test_real_child_inherits_trusted_identity_multimodal_input_and_unique_engine(boundaries, tmp_path, monkeypatch):
    from src.services.agent_runner.runtime.child import ChildExecution
    image = tmp_path / "probe.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nimage-bytes")
    parent = Identity("tenant-a", "user-a", "session-a", "feishu", "channel")
    child = ChildExecution(parent, config(), "child-id")
    child.runtime.history.reader = Reader()
    gateway = Gateway(response("child answer"))
    child.runtime.resources.llm = gateway
    result = await child.execute_as_subagent("read image", "session-a", image_paths=[str(image)])
    assert len(boundaries) == 1 and boundaries[0].role == AgentMode.SUBAGENT
    assert boundaries[0].identity == parent
    assert result["result"]["content"] == "child answer"
    assert result["token_usage"] == {"input": 7, "output": 3, "cached": 2}
    content = gateway.requests[0]["messages"][0]["content"]
    assert isinstance(content, list)
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert "delegate_to_subagent" not in {item["name"] for item in gateway.requests[0]["tools"]}


@pytest.mark.asyncio
async def test_d1_multiple_calls_fail_loud_in_real_runtime(boundaries):
    from src.services.agent_runner.runtime.executor import RuntimeExecution, CompatibilityObserver
    from src.services.agent_runner.runtime.profile import RuntimeResources
    from src.core.agent_engine.contracts import ExecutionState
    resources = RuntimeResources(mode=AgentMode.STANDALONE, subagent_config=config())
    registry, probe = registry_with_probe()
    resources.tool_registry, resources.tool_executor = registry, ToolExecutor(registry)
    resources.llm = Gateway(response("", [tool_call(identifier="one"), tool_call(identifier="two")]))
    record = Record()
    runtime = RuntimeExecution(resources, Identity("tenant-a", "user-a", "session-a"),
        observer=CompatibilityObserver(record), history_reader=Reader())
    with pytest.raises(RuntimeError, match="DESKTOP_MULTIPLE_TOOL_CALLS_UNSUPPORTED"):
        [event async for event in runtime.run("remote", defer_names={"acceptance_probe"})]
    assert probe.contexts == []
    assert len(record.usages) == 1 and record.iterations == 1
    completed = [fact for fact in runtime.state.model_calls if fact["phase"] == "completed"]
    assert len(completed) == 1 and completed[0]["usage"]["prompt_tokens"] == 7
    restored = ExecutionState.restore(json.loads(json.dumps(runtime.state.checkpoint())))
    assert restored.model_calls == runtime.state.model_calls
    assert restored.usage_watermark == runtime.state.usage_watermark


@pytest.mark.asyncio
async def test_concurrent_public_calls_keep_real_runtime_tenant_prompts_tools_images_and_record_isolated(boundaries, monkeypatch):
    from src.core.agent import Agent
    from src.services.session_record import SessionRecordManager
    monkeypatch.setattr("src.core.trace_collector.TraceCollector", MagicMock())
    both = asyncio.Event()
    entered = []

    class InterleavingGateway(Gateway):
        async def chat_with_tools(self, **kwargs):
            self.requests.append(deepcopy(kwargs))
            tenant = "a" if "TENANT_EXTRA_tenant-a" in kwargs["system_prompt"] else "b"
            if not any(message["role"] == "tool" for message in kwargs["messages"]):
                entered.append(tenant)
                if len(entered) == 2:
                    both.set()
                await asyncio.wait_for(both.wait(), timeout=5)
                return response("", [tool_call(identifier=f"call-{tenant}")])
            return response(f"answer-{tenant}")

    agent = Agent(mode=AgentMode.STANDALONE, subagent_config=config())
    agent.llm = gateway = InterleavingGateway()
    registry, probe = registry_with_probe()
    agent.tool_registry, agent.tool_executor = registry, ToolExecutor(registry)
    records = [Record("a"), Record("b")]
    async def invoke(tenant, record):
        user = SimpleNamespace(tenant_id=f"tenant-{tenant}", user_id=f"user-{tenant}", name=tenant, phone=None)
        return await agent.process_message_sync(f"task-{tenant}", f"session-{tenant}", user=user, record_service=record)
    a, b = await asyncio.gather(invoke("a", records[0]), invoke("b", records[1]))
    assert str(a) == "answer-a" and str(b) == "answer-b"
    assert a.images[0]["file_id"] == "image-tenant-a"
    assert b.images[0]["file_id"] == "image-tenant-b"
    assert len(boundaries) == 2
    for request in gateway.requests:
        prompt = request["system_prompt"]
        assert ("TENANT_SKILL_tenant-a" in prompt) != ("TENANT_SKILL_tenant-b" in prompt)
    assert {(c.tenant_id, c.session_id, c.tool_call_id) for c in probe.contexts} == {
        ("tenant-a", "session-a", "call-a"), ("tenant-b", "session-b", "call-b")}
    assert [record.iterations for record in records] == [2, 2]
    assert [len(record.usages) for record in records] == [2, 2]
    assert SessionRecordManager.get_current_record() is None
    assert current_tool_execution_context() is None


@pytest.mark.asyncio
async def test_real_child_clarification_uses_waiting_result_without_extra_model_call(boundaries):
    from src.services.agent_runner.runtime.child import ChildExecution
    child = ChildExecution(Identity("tenant-a", "user-a", "session-a"), config(), "child-id")
    child.runtime.history.reader = Reader()
    gateway = Gateway(response("", [tool_call("clarify", arguments={"question": "Which city?", "missing_info": ["city"]})]))
    child.runtime.resources.llm = gateway
    result = await child.execute_as_subagent("find people", "session-a")
    assert len(boundaries) == 1 and len(gateway.requests) == 1
    assert result["status"] == "clarifying"
    assert result["result"]["question"] == "Which city?"
    assert result["result"]["tool_call_id"] == "probe-1"
    assert child.runtime.state.pending[0].id == "probe-1"


@pytest.mark.asyncio
async def test_child_usage_is_recorded_once_without_incrementing_top_level_iterations(boundaries):
    from src.services.agent_runner.runtime.child import ChildExecution
    from src.services.session_record import SessionRecordManager
    child = ChildExecution(Identity("tenant-a", "user-a", "session-a"), config(), "child-id")
    child.runtime.history.reader = Reader()
    child.runtime.resources.llm = Gateway()
    record = Record()
    token = SessionRecordManager.set_current_record(record)
    try:
        result = await child.execute_as_subagent("child task", "session-a")
    finally:
        SessionRecordManager.reset_current_record(token)
    assert len(record.usages) == 1
    assert result["token_usage"] == {"input": 7, "output": 3, "cached": 2}
    assert record.iterations == 0


@pytest.mark.asyncio
async def test_browser_wait_projects_sibling_pairs_and_public_continuation_does_not_replay(boundaries, monkeypatch):
    from src.core.agent import Agent
    from src.core.tool_suspension import ToolSuspension
    from src.services.agent_runner.runtime.executor import RuntimeExecution
    from src.services.agent_runner.runtime.profile import RuntimeResources

    class SuspendTool(ProbeTool):
        name = "acceptance_suspended"

        async def execute(self, **kwargs):
            context = current_tool_execution_context()
            self.contexts.append(context)
            return ToolSuspension(tenant_id=context.tenant_id, user_id=context.user_id,
                session_id=context.session_id, agent_execution_id=context.agent_execution_id,
                tool_call_id=context.tool_call_id, run_id="browser-run", assistance_id="assistance",
                continuation_id="continuation", event={"type": "human_assistance", "assistanceId": "assistance"})

    class UntouchedTool(ProbeTool):
        name = "acceptance_untouched"

    registry = ToolRegistry()
    first, suspended, untouched = ProbeTool(), SuspendTool(), UntouchedTool()
    for tool in (first, suspended, untouched):
        registry.register(tool)
    store = SimpleNamespace(get_assistance=AsyncMock(return_value={"id": "assistance"}),
                            bind_agent=AsyncMock(return_value=True))
    monkeypatch.setattr("src.tools.browser.resume_store.ResumeStore", lambda: store)
    resources = RuntimeResources(mode=AgentMode.STANDALONE, subagent_config=config())
    resources.tool_registry, resources.tool_executor = registry, ToolExecutor(registry)
    resources.llm = Gateway(response("", [tool_call(identifier="A"),
        tool_call("acceptance_suspended", "B"), tool_call("acceptance_untouched", "C")]))
    runtime = RuntimeExecution(resources, Identity("tenant-a", "user-a", "session-a"), history_reader=Reader())
    events = [event async for event in runtime.run("browser task")]
    projected = [event for event in events if event["type"] == "tool_messages" and event.get("suspended")][-1]["messages"]
    paired = {message["tool_call_id"]: message for message in projected if message["role"] == "tool"}
    assert set(paired) == {"A", "C"}, "Legacy storage must preserve A and explicit unexecuted C while B waits"
    deferred = paired["C"]["content"]
    assert "deferred" in (deferred if isinstance(deferred, str) else json.dumps(deferred)).lower()
    assert runtime.state.tools["C"].phase == "prepared"
    assert [item.id for item in runtime.state.pending] == ["B", "C"]
    assert len(first.contexts) == 1 and len(suspended.contexts) == 1 and untouched.contexts == []

    rows = [{"role": "user", "content": "browser task", "metadata": {}}]
    for message in projected:
        metadata = {key: message[key] for key in ("tool_calls", "tool_call_id") if key in message}
        rows.append({"role": message["role"], "content": message["content"], "metadata": metadata})
    monkeypatch.setattr("src.db.models.MessageDB.list_by_session", lambda *args, **kwargs: deepcopy(rows))
    agent = Agent(mode=AgentMode.STANDALONE, subagent_config=config())
    gateway = Gateway(response("continued"))
    agent.llm, agent.tool_registry, agent.tool_executor = gateway, registry, ToolExecutor(registry)
    user = SimpleNamespace(tenant_id="tenant-a", user_id="user-a", name="A", phone=None)
    continued = [event async for event in agent.continue_tool_call(
        session_id="session-a", tool_call_id="B", result={"success": True, "data": "browser result"}, user=user)]
    assert any(event.get("data") == "continued" for event in continued)
    assert [m["tool_call_id"] for m in gateway.requests[0]["messages"] if m["role"] == "tool"] == ["A", "B", "C"]
    assert len(first.contexts) == 1 and len(suspended.contexts) == 1 and untouched.contexts == []


@pytest.mark.asyncio
async def test_user_answer_resumes_pending_child_clarification_instead_of_starting_main_model(boundaries, monkeypatch):
    from src.services.agent_runner.runtime.executor import RuntimeExecution
    from src.services.agent_runner.runtime.profile import RuntimeResources

    class HashStore:
        def __init__(self):
            self.values = {}

        def make_key(self, prefix, session):
            return f"{prefix}:{session}"

        def hset(self, key, field, value):
            self.values.setdefault(key, {})[field] = value

        def hget(self, key, field):
            return self.values.get(key, {}).get(field)

        def expire(self, key, ttl):
            return True

        def delete(self, key):
            self.values.pop(key, None)

    store = HashStore()
    monkeypatch.setattr("src.services.agent_runner.runtime.clarification.redis_client", store)
    resources = RuntimeResources(mode=AgentMode.MASTER)
    gateway = Gateway(response("wrong main model answer"))
    resources.llm = gateway
    delegate = resources._tool_controls.get("delegate_to_subagent")
    execute = AsyncMock(return_value={"success": True, "status": "completed", "summary": "child summary", "result": {"content": "continued child answer"}, "execution_id": "child-1"})
    monkeypatch.setattr(delegate, "execute", execute)
    runtime = RuntimeExecution(resources, Identity("tenant-a", "user-a", "session-a"), history_reader=Reader())
    runtime.clarification.save(runtime.identity, {
        "subagent_name": "hiring", "execution_id": "child-1", "task_description": "find candidates",
        "question": "Which city?"})
    events = [event async for event in runtime.run("Shanghai")]
    assert execute.await_count == 1
    arguments = execute.call_args.kwargs
    assert arguments["subagent_name"] == "hiring"
    assert all(text in arguments["task_description"] for text in ("find candidates", "Which city?", "Shanghai"))
    assert arguments["session_id"] == "session-a"
    assert gateway.requests == [], "Pending answer must not become an unrelated ordinary model turn"
    assert runtime.clarification.read(runtime.identity) is None
    assert any(event.get("data") == "continued child answer" for event in events)



@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["waiting", "paused"])
async def test_real_parent_engine_preserves_child_wait_instead_of_completed_answer(boundaries, monkeypatch, status):
    from src.services.agent_runner.runtime.executor import RuntimeExecution
    from src.services.agent_runner.runtime.profile import RuntimeResources
    resources = RuntimeResources(mode=AgentMode.MASTER)
    gateway = Gateway(response("", [tool_call("delegate_to_subagent", arguments={"subagent_name": "child", "task_description": "task"})]))
    resources.llm = gateway
    monkeypatch.setattr(resources._tool_controls.get("delegate_to_subagent"), "execute",
        AsyncMock(return_value={"success": False, "status": status, "execution_id": "child-id", "waiting": {"kind": "verification_required"}}))
    runtime = RuntimeExecution(resources, Identity("tenant-a", "user-a", "session-a"), history_reader=Reader())
    events = [event async for event in runtime.run("child task")]
    assert runtime.state.outcome.value == "waiting"
    assert len(gateway.requests) == 1
    assert runtime.state.waiting["child_status"] == status
    assert runtime.state.children["probe-1"]["execution_id"] == "child-id"
    assert runtime.state.tools["probe-1"].phase == "waiting"
    assert not any(event["type"] == "response" for event in events)


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", ["{invalid", "[]", '"string"'])
async def test_invalid_provider_arguments_record_actual_usage_before_rejecting_side_effect(boundaries, arguments):
    from src.services.agent_runner.runtime.executor import RuntimeExecution, CompatibilityObserver
    from src.services.agent_runner.runtime.profile import RuntimeResources
    resources = RuntimeResources(mode=AgentMode.STANDALONE, subagent_config=config())
    registry, probe = registry_with_probe()
    resources.tool_registry, resources.tool_executor = registry, ToolExecutor(registry)
    call = tool_call()
    call["function"]["arguments"] = arguments
    resources.llm = Gateway(response("", [call]))
    record = Record()
    runtime = RuntimeExecution(resources, Identity("tenant-a", "user-a", "session-a"),
        observer=CompatibilityObserver(record), history_reader=Reader())
    with pytest.raises(ValueError, match="INVALID_TOOL_ARGUMENTS"):
        [event async for event in runtime.run("bad call")]
    assert probe.contexts == []
    assert len(record.usages) == 1
    assert len(runtime.state.model_calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["tenant", "user", "session", "source", "kind", "profile", "execution"])
async def test_runtime_checkpoint_rejects_foreign_scope_before_model_or_dispatch(boundaries, mismatch):
    from dataclasses import replace
    from src.services.agent_runner.runtime.executor import RuntimeExecution
    from src.services.agent_runner.runtime.profile import RuntimeResources
    identity = Identity("tenant-a", "user-a", "session-a")
    resources = RuntimeResources(mode=AgentMode.STANDALONE, subagent_config=config(), execution_id="run-id")
    gateway = Gateway()
    resources.llm = gateway
    runtime = RuntimeExecution(resources, identity, history_reader=Reader())
    state, _ = await runtime.assembler.prepare("original", execution_id="run-id")
    changed = {"tenant": "tenant_id", "user": "user_id", "session": "session_id", "source": "source", "kind": "session_kind"}
    if mismatch == "execution":
        state.execution_id = "different-execution"
    elif mismatch == "profile":
        state.profile_id = "another-profile"
    else:
        state.identity = replace(identity, **{changed[mismatch]: "different"})
    expected = "EXECUTION" if mismatch == "execution" else "PROFILE" if mismatch == "profile" else "IDENTITY"
    with pytest.raises(ValueError, match=f"CHECKPOINT_{expected}_MISMATCH"):
        [event async for event in runtime.run("resume", state=state)]
    assert gateway.requests == []
    assert boundaries == []


@pytest.mark.asyncio
async def test_unauthorized_runtime_stops_before_history_compression_attachments_or_model(boundaries, monkeypatch):
    from src.services.agent_runner.runtime.executor import RuntimeExecution
    from src.services.agent_runner.runtime.profile import RuntimeResources
    class DeniedReader(Reader):
        def assert_authorized(self):
            raise PermissionError("SESSION_ACCESS_DENIED")
    resources = RuntimeResources(mode=AgentMode.STANDALONE, subagent_config=config())
    gateway = Gateway()
    resources.llm = gateway
    reader = DeniedReader()
    runtime = RuntimeExecution(resources, Identity("tenant-a", "user-a", "session-a"), history_reader=reader)
    compression = AsyncMock()
    attachment = MagicMock()
    monkeypatch.setattr(runtime.compression, "_run_compression_phase", compression)
    monkeypatch.setattr(runtime.assembler.attachments, "prepare", attachment)
    with pytest.raises(PermissionError, match="SESSION_ACCESS_DENIED"):
        [event async for event in runtime.run("task", attachments=[{"name": "secret.txt", "content": "eA=="}])]
    assert gateway.requests == [] and reader.calls == [] and boundaries == []
    assert compression.await_count == 0 and attachment.call_count == 0


def test_parent_and_two_real_child_plan_managers_do_not_overwrite_or_advance_each_other(boundaries, monkeypatch, tmp_path):
    import hashlib
    import uuid
    from src.core.plan_manager import PlanManager
    from src.services.agent_runner.runtime.child import ChildExecution
    from src.services.agent_runner.runtime.profile import RuntimeResources
    def temporary_manager(directory, *, execution_scope=None, plan_store=None):
        suffix = hashlib.sha256(str(execution_scope).encode()).hexdigest()
        return PlanManager(tmp_path / suffix, execution_scope=execution_scope, plan_store=plan_store)
    monkeypatch.setattr("src.services.agent_runner.runtime.profile.PlanManager", temporary_manager)
    session = f"plan-test-{uuid.uuid4().hex}"
    identity = Identity("tenant-a", "user-a", session)
    parent = RuntimeResources(mode=AgentMode.MASTER, session_id=session, tenant_id="tenant-a")
    children = [ChildExecution(identity, config(), f"child-{index}", parent.plan_manager) for index in (1, 2)]
    managers = [parent.plan_manager, *(child.runtime.resources.plan_manager for child in children)]
    try:
        plans = [manager.create_plan(session, title, [{"step_number": 1, "tool": "acceptance_probe", "description": title}])
                 for manager, title in zip(managers, ("parent", "first child", "second child"))]
        assert len({plan.plan_id for plan in plans}) == 3
        assert [manager.get_plan(session).intent for manager in managers] == ["parent", "first child", "second child"]
        managers[1].mark_task_completed(session, "task_1", {"child": 1})
        managers[2].mark_task_failed(session, "task_1", "child failure")
        assert [manager.get_plan(session).tasks[0].status for manager in managers] == ["pending", "completed", "failed"]
        managers[0].mark_task_running(session, "task_1")
        assert [manager.get_plan(session).tasks[0].status for manager in managers] == ["running", "completed", "failed"]
        managers[1]._delete_plan(session)
        assert managers[1].get_plan(session) is None
        assert managers[0].get_plan(session).plan_id == plans[0].plan_id
        assert managers[2].get_plan(session).plan_id == plans[2].plan_id
    finally:
        for manager in managers:
            manager._delete_plan(session)


@pytest.mark.asyncio
async def test_public_agent_runs_real_boss_proxy_with_request_session_and_retains_invocation_progress(boundaries, monkeypatch):
    from datetime import datetime
    from src.core.agent import Agent
    from src.local_tools.proxy_tool import BossFilterOptionsTool
    from unittest.mock import Mock
    monkeypatch.setattr("src.core.trace_collector.TraceCollector", MagicMock())
    device = {"id": "device-a", "selected": True, "status": "active", "last_seen_at": datetime.now(),
              "capabilities_json": {"provider_id": "ai.aidwork.boss-recruiting"}}
    monkeypatch.setattr("src.local_tools.repository.list_devices", lambda tenant, user: [device])
    enqueue = Mock(return_value={"id": "runtime-invocation"})
    monkeypatch.setattr("src.local_tools.service.LocalInvocationService.enqueue", enqueue)
    monkeypatch.setattr("src.local_tools.repository.list_events", lambda *args: [])
    terminal = {"id": "runtime-invocation", "state": "succeeded", "effect": "none",
                "result_json": {"data": {"options": ["salary"]}}, "credit_cost": 0}
    monkeypatch.setattr("src.local_tools.repository.get_invocation", lambda *args: terminal)
    configured = config()
    configured.tools = {"inherit": False, "allowed": ["boss_filter_options"]}
    agent = Agent(mode=AgentMode.STANDALONE, subagent_config=configured, session_id="constructor-wrong-session")
    registry = ToolRegistry()
    registry.register(BossFilterOptionsTool())
    agent.tool_registry, agent.tool_executor = registry, ToolExecutor(registry)
    gateway = Gateway(response("", [tool_call("boss_filter_options", "boss-call")]), response("BOSS options read"))
    agent.llm = gateway
    user = SimpleNamespace(tenant_id="tenant-a", user_id="user-a", name="A", phone=None)
    events = [event async for event in agent.process_message("read BOSS options", "actual-request-session", user=user)]
    assert enqueue.call_count == 1
    assert enqueue.call_args.kwargs["session_id"] == "actual-request-session"
    assert enqueue.call_args.kwargs["tenant_id"] == "tenant-a"
    assert enqueue.call_args.kwargs["user_id"] == "user-a"
    assert enqueue.call_args.kwargs["tool_name"] == "boss_filter_options"
    assert boundaries[0].tools["boss-call"].invocation_id == "runtime-invocation"
    assert any(event["type"] == "progress" and "已下发到本机执行" in str(event.get("data", "")) for event in events)
    assert any(event["type"] == "response" and event.get("data") == "BOSS options read" for event in events)
    assert gateway.requests[1]["messages"][-1]["tool_call_id"] == "boss-call"
