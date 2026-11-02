"""Provider thinking metadata must survive real Engine and Runner recovery paths."""
import json
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.core.agent_engine import DispatchResult, ExecutionState, Outcome
from src.core.agent_engine.context import reorder_history
from src.llm.providers.deepseek import DeepSeekProvider
from src.memory.short_term import ShortTermMemory
from src.services.agent_runner.durable_sleep import arm, complete_sleep
from src.services.agent_runner.runtime.history import SessionHistory
from src.services.agent_runner.wait_identity import owned_wait_id
from tests.unit.test_agent_engine_acceptance import Model, Observer, Tools, call, completion, run, state


@pytest.mark.parametrize("kind", ["web", "channel"])
@pytest.mark.parametrize("reasoning", ["", "private reasoning", None])
def test_private_history_reload_keeps_returned_strings_without_inventing_legacy_fields(kind, reasoning):
    metadata = {} if reasoning is None else {"reasoning_content": reasoning}
    metadata["tool_calls"] = [call("read-1")]
    rows = [{"role": "assistant", "content": "", "metadata": metadata},
            {"role": "tool", "content": "read result", "metadata": {"tool_call_id": "read-1"}}]
    # Web storage readers also support serialized JSON metadata.
    if kind == "web":
        rows[0]["metadata"] = json.dumps(metadata)
    reader, memory = Mock(), Mock(short_term=SimpleNamespace(max_messages=20))
    reader.read_web.return_value = deepcopy(rows)
    reader.read_channel.return_value = deepcopy(rows)
    SessionHistory(memory, "web" if kind == "web" else "wecom", reader)._reload_memory_from_db("session")
    messages = reorder_history(memory.load_history.call_args.args[1])
    wire = DeepSeekProvider(api_key="sk-test")._format_messages(messages)
    assert wire[1]["tool_call_id"] == "read-1"
    if reasoning is None:
        assert "reasoning_content" not in wire[0]
    else:
        assert wire[0]["reasoning_content"] == reasoning


@pytest.mark.parametrize("metadata", [{}, {"reasoning_content": None},
                                      {"reasoning_content": ""}, {"reasoning_content": "private reasoning"}])
@pytest.mark.parametrize("serialized", [True, False])
def test_new_worker_memory_cache_miss_preserves_private_metadata(monkeypatch, metadata, serialized):
    from src.channels.session import channel_session_manager
    from src.db.models import MessageDB

    metadata = {**metadata, "tool_calls": [call("read-1")]}
    rows = [{"role": "assistant", "content": "", "metadata": json.dumps(metadata) if serialized else metadata},
            {"role": "tool", "content": "read result", "metadata": {"tool_call_id": "read-1"}}]
    read = Mock(return_value=rows)
    monkeypatch.setattr(MessageDB, "list_by_session", read)
    monkeypatch.setattr(channel_session_manager, "is_channel_session", lambda _: False)
    memory = ShortTermMemory()
    messages = memory.get_context("session")
    wire = DeepSeekProvider(api_key="sk-test")._format_messages(reorder_history(messages))
    assert wire[1]["tool_call_id"] == "read-1"
    if isinstance(metadata.get("reasoning_content"), str):
        assert wire[0]["reasoning_content"] == metadata["reasoning_content"]
    else:
        assert "reasoning_content" not in wire[0]
    assert memory.get_context("session") == messages
    read.assert_called_once_with("session")


@pytest.mark.asyncio
async def test_empty_thinking_survives_two_json_sleep_restores_without_tool_or_usage_replay():
    provider = DeepSeekProvider(api_key="sk-test")

    def response(item):
        return provider._parse_response({"choices": [{"message": {
            "content": "", "reasoning_content": "", "tool_calls": [item]}}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3}})

    class CheckingModel(Model):
        async def complete(self, current, call_id, definitions):
            wire = provider._format_messages(reorder_history(current.messages))
            for message in wire:
                if message["role"] == "assistant" and message.get("tool_calls"):
                    assert message["reasoning_content"] == "", "next DeepSeek request would return HTTP 400"
            return await super().complete(current, call_id, definitions)

    class TimerTools(Tools):
        async def dispatch(self, current, item):
            if item.name != "agent_sleep":
                async for result in super().dispatch(current, item):
                    yield result
                return
            wait = {"kind": "timer", "version": 1, "tool_call_id": item.id,
                    "target_execution_id": current.execution_id, "seconds": 10}
            wait["wait_id"] = owned_wait_id(current.execution_id, current.execution_id, wait)
            yield DispatchResult({"pending": True}, wait=wait)

    current, tools, observer = state(max_iterations=5), TimerTools(), Observer()
    model = CheckingModel(response(call("read-1")), response(call("sleep-1", "agent_sleep", {"seconds": 10})),
                          response(call("read-2")), response(call("sleep-2", "agent_sleep", {"seconds": 10})),
                          response(call("read-3")), completion("finished"))
    for _ in range(2):
        await run(current, model, tools, observer=observer)
        assert current.outcome == Outcome.WAITING
        checkpoint = {"execution": current.checkpoint()}
        deadline = arm(checkpoint, datetime.now(timezone.utc))
        checkpoint = json.loads(json.dumps(checkpoint))
        current = ExecutionState.restore(checkpoint["execution"])
        complete_sleep({"checkpoint": checkpoint, "wake_at": deadline},
                       {current.execution_id: current}, checkpoint, trigger="timer")
    await run(current, model, tools, observer=observer)
    assert current.outcome == Outcome.COMPLETED
    assert len(model.requests) == len(observer.usage) == 6
    assert [item.id for _, item in tools.dispatched] == ["read-1", "read-2", "read-3"]
    assert tools.recovered == []
    results = [message["tool_call_id"] for message in current.messages if message["role"] == "tool"]
    assert results == ["read-1", "sleep-1", "read-2", "sleep-2", "read-3"]
