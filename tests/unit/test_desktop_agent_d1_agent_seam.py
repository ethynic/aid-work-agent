import asyncio
from datetime import datetime, timezone

from src.core.agent import master_agent
from src.desktop_agent.gateway import InMemoryGatewayStore, RemoteToolGateway
from src.desktop_agent.security import AuthorizationTicketSigner
from src.desktop_agent.turn import AgentTurnService, ExistingAgentBackend
from src.tools.base import BaseTool
from src.tools.executor import ToolExecutor
from src.tools.registry import ToolRegistry

from tests.unit.test_desktop_agent_d1 import SECRET, correlation, invoke_request, next_request
import pytest


class CountingEcho(BaseTool):
    name = "test_echo"
    description = "provider-level seam test"
    parameters_schema = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}

    def __init__(self): self.calls = 0

    async def execute(self, **kwargs):
        self.calls += 1
        return {"success": True, "echo": kwargs["text"]}


class FakeModelProvider:
    def __init__(self): self.calls = 0
    def get_model_name(self): return "fake-d1-model"
    def get_provider_name(self): return "fake-provider"

    async def chat_with_tools(self, *, system_prompt, messages, tools):
        assert [tool["name"] for tool in tools] == ["test_echo"]
        self.calls += 1
        if self.calls == 1:
            return {"request_id": "model-1", "content": "", "usage": {}, "tool_calls": [{"id": "provider-call", "function": {"name": "test_echo", "arguments": '{"text":"hello"}'}}]}
        assert messages[-1]["role"] == "tool"
        assert messages[-1]["tool_call_id"] == "invoke-1"
        return {"request_id": "model-2", "content": "completed: hello", "usage": {}, "tool_calls": []}


def test_default_agent_path_still_executes_tools_and_keeps_legacy_memory_order(monkeypatch):
    class LegacyProvider(FakeModelProvider):
        async def chat_with_tools(self, *, system_prompt, messages, tools):
            assert "test_echo" in [tool["name"] for tool in tools]
            self.calls += 1
            if self.calls == 1:
                return {"request_id": "legacy-1", "content": "", "usage": {}, "tool_calls": [{"id": "provider-call", "function": {"name": "test_echo", "arguments": '{"text":"legacy"}'}}]}
            assert messages[-1]["role"] == "tool"
            assert messages[-1]["tool_call_id"] == "provider-call"
            return {"request_id": "legacy-2", "content": "legacy complete", "usage": {}, "tool_calls": []}

    async def scenario():
        registry = ToolRegistry(); tool = CountingEcho(); registry.register(tool)
        provider = LegacyProvider()
        session_id = "desktop-d1-default-isolation"
        monkeypatch.setattr(master_agent, "llm", provider)
        monkeypatch.setattr(master_agent, "tool_registry", registry)
        monkeypatch.setattr(master_agent, "tool_executor", ToolExecutor(registry))
        monkeypatch.setattr(master_agent, "_run_compression_phase", lambda _session: asyncio.sleep(0))
        monkeypatch.setattr(master_agent, "_ensure_tenant_skills_loaded", lambda: None)
        monkeypatch.setattr("src.channels.session.channel_session_manager.is_channel_session", lambda _session: False)
        monkeypatch.setattr("src.db.models.MessageDB.list_by_session", lambda _session, limit: [])
        master_agent.memory.clear(session_id)

        events = [event async for event in master_agent.process_message("hello", session_id)]
        history = master_agent.memory.get_context(session_id)
        master_agent.memory.clear(session_id)

        assert provider.calls == 2 and tool.calls == 1
        assert [item["role"] for item in history] == ["user", "assistant", "tool", "assistant"]
        assert any(event.get("type") == "tool_start" for event in events)
        assert any(event.get("type") == "tool_result" for event in events)
        assert any(event.get("type") == "response" and event.get("data") == "legacy complete" for event in events)
        assert events[-1]["type"] == "tool_messages"

    asyncio.run(scenario())


def test_existing_agent_backend_pauses_real_agent_model_loop_and_resumes(monkeypatch):
    async def scenario():
        registry = ToolRegistry(); tool = CountingEcho(); registry.register(tool)
        provider = FakeModelProvider()
        persisted = []

        async def no_compression(_session_id): return None
        monkeypatch.setattr(master_agent, "llm", provider)
        monkeypatch.setattr(master_agent, "tool_registry", registry)
        monkeypatch.setattr(master_agent, "tool_executor", ToolExecutor(registry))
        monkeypatch.setattr(master_agent, "_run_compression_phase", no_compression)
        monkeypatch.setattr(master_agent, "_ensure_tenant_skills_loaded", lambda: None)
        monkeypatch.setattr("src.channels.session.channel_session_manager.is_channel_session", lambda _session: False)

        def rows(_session_id, limit):
            result = []
            for index, message in enumerate(persisted):
                result.append({"role": message["role"], "content": message["content"], "metadata": message.get("metadata") or {}, "created_at": datetime.now(timezone.utc)})
            return result[-limit:]

        monkeypatch.setattr("src.db.models.MessageDB.list_by_session", rows)

        def persist(_session_ref, batch): persisted.extend(batch); return [object() for _ in batch]

        signer = AuthorizationTicketSigner(SECRET, clock=lambda: 1000)
        gateway = RemoteToolGateway(registry, master_agent.tool_executor, signer, {"test_echo"}, InMemoryGatewayStore())
        backend = ExistingAgentBackend({"test_echo"}, agent_provider=lambda _session, _tenant: master_agent, persist_messages=persist, session_lookup=lambda _session: {"tenant_id": "tenant-1", "user_id": "user-1"})
        turns = AgentTurnService(backend, gateway, "policy-r1")

        first = await turns.next(next_request(), "tenant-1", "user-1")
        assert first["outcome"]["type"] == "remote_tool_call"
        assert tool.calls == 0, "real Agent must pause before its internal ToolExecutor"
        invoked = await gateway.invoke(invoke_request(first["outcome"]), "tenant-1", "user-1")
        assert tool.calls == 1
        final = await turns.next(next_request("production-result", {"type": "tool_result", "invocation_id": "invoke-1", "result": invoked["result"]}), "tenant-1", "user-1")
        assert final["outcome"] == {"type": "final", "message": "completed: hello"}
        assert provider.calls == 2 and tool.calls == 1
        assert [item["role"] for item in persisted] == ["user", "assistant", "tool", "assistant"]
        with pytest.raises(RuntimeError, match="CONTEXT_LOST"):
            await backend.next_outcome({"type": "tool_result", "invocation_id": "invoke-1", "result": invoked["result"]}, correlation(), "tenant-1", "user-1")

    asyncio.run(scenario())


def test_existing_agent_backend_fails_loud_on_parallel_model_tool_calls(monkeypatch):
    class MultiProvider(FakeModelProvider):
        async def chat_with_tools(self, *, system_prompt, messages, tools):
            return {"request_id": "multi", "content": "", "usage": {}, "tool_calls": [
                {"id": "one", "function": {"name": "test_echo", "arguments": '{"text":"one"}'}},
                {"id": "two", "function": {"name": "test_echo", "arguments": '{"text":"two"}'}},
            ]}

    async def scenario():
        registry = ToolRegistry(); tool = CountingEcho(); registry.register(tool)
        monkeypatch.setattr(master_agent, "llm", MultiProvider())
        monkeypatch.setattr(master_agent, "tool_registry", registry)
        monkeypatch.setattr(master_agent, "tool_executor", ToolExecutor(registry))
        monkeypatch.setattr(master_agent, "_run_compression_phase", lambda _session: asyncio.sleep(0))
        monkeypatch.setattr(master_agent, "_ensure_tenant_skills_loaded", lambda: None)
        monkeypatch.setattr("src.channels.session.channel_session_manager.is_channel_session", lambda _session: False)
        monkeypatch.setattr("src.db.models.MessageDB.list_by_session", lambda _session, limit: [])
        backend = ExistingAgentBackend({"test_echo"}, agent_provider=lambda _session, _tenant: master_agent, persist_messages=lambda *_args: [], session_lookup=lambda _session: {"tenant_id": "tenant-1", "user_id": "user-1"})
        with pytest.raises(RuntimeError, match="MULTIPLE_TOOL_CALLS"):
            await backend.next_outcome({"type": "user_message", "content": "two"}, correlation(), "tenant-1", "user-1")
        assert tool.calls == 0

    asyncio.run(scenario())


def test_existing_agent_backend_persists_selected_call_after_empty_provider_call(monkeypatch):
    class EmptyThenValidProvider(FakeModelProvider):
        async def chat_with_tools(self, *, system_prompt, messages, tools):
            return {"request_id": "mixed", "content": "", "usage": {}, "tool_calls": [
                {"id": "empty", "function": {"name": "", "arguments": "{}"}},
                {"id": "valid", "function": {"name": "test_echo", "arguments": '{"text":"ok"}'}},
            ]}

    async def scenario():
        registry = ToolRegistry(); registry.register(CountingEcho())
        monkeypatch.setattr(master_agent, "llm", EmptyThenValidProvider())
        monkeypatch.setattr(master_agent, "tool_registry", registry)
        monkeypatch.setattr(master_agent, "tool_executor", ToolExecutor(registry))
        monkeypatch.setattr(master_agent, "_run_compression_phase", lambda _session: asyncio.sleep(0))
        monkeypatch.setattr(master_agent, "_ensure_tenant_skills_loaded", lambda: None)
        monkeypatch.setattr("src.channels.session.channel_session_manager.is_channel_session", lambda _session: False)
        monkeypatch.setattr("src.db.models.MessageDB.list_by_session", lambda _session, limit: [])
        persisted = []
        backend = ExistingAgentBackend({"test_echo"}, agent_provider=lambda *_args: master_agent, persist_messages=lambda _session, batch: persisted.extend(batch) or batch, session_lookup=lambda _session: {"tenant_id": "tenant-1", "user_id": "user-1"})
        outcome = await backend.next_outcome({"type": "user_message", "content": "one"}, correlation(), "tenant-1", "user-1")
        assert outcome == {"type": "remote_tool_call", "tool_name": "test_echo", "arguments": {"text": "ok"}}
        calls = persisted[1]["metadata"]["tool_calls"]
        assert len(calls) == 1 and calls[0]["function"]["name"] == "test_echo" and calls[0]["id"] == "invoke-1"

    asyncio.run(scenario())


def test_existing_agent_backend_rejects_cross_user_session_before_model(monkeypatch):
    provider = FakeModelProvider()
    monkeypatch.setattr(master_agent, "llm", provider)
    backend = ExistingAgentBackend({"test_echo"}, agent_provider=lambda _session, _tenant: master_agent, persist_messages=lambda *_args: [], session_lookup=lambda _session: {"tenant_id": "tenant-1", "user_id": "other-user"})
    with pytest.raises(ValueError, match="not found or not owned"):
        asyncio.run(backend.next_outcome({"type": "user_message", "content": "hello"}, correlation(), "tenant-1", "user-1"))
    assert provider.calls == 0
