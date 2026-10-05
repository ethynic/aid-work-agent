"""Existing model/tool steps precede a new input after checkpoint restoration."""
from copy import deepcopy

import pytest

from src.core.agent_engine import ExecutionState, Outcome
from src.core.agent_engine.contracts import ToolCall, ToolFact
from tests.unit.test_agent_engine_acceptance import Model, Tools, call, completion, run, state


@pytest.mark.asyncio
@pytest.mark.parametrize("saved", ["no_tool_response", "tool_response", "prepared_tool"])
async def test_saved_step_is_not_used_as_answer_to_new_input_and_usage_is_not_replayed(saved):
    current = state()
    current.iteration = 1
    current.outcome = Outcome.PAUSED
    response = completion("old saved response") if saved == "no_tool_response" else completion("", [call("original-read")])
    original = {"call_id": "already-paid-provider", "execution_id": current.execution_id,
                "iteration": 1, "phase": "completed", "applied": saved == "prepared_tool",
                "response": deepcopy(response), "usage": deepcopy(response["usage"])}
    current.model_calls.append(original)
    if saved == "prepared_tool":
        pending = ToolCall("original-read", "read", {})
        current.pending = [pending]
        current.tools[pending.id] = ToolFact(pending)
        current.messages.append({"role": "assistant", "content": "", "tool_calls": [pending.message_call()]})
    current.followup_messages = [{"role": "user", "content": "new resumed question", "metadata": {"control_id": "same-control"}}]
    restored = ExecutionState.restore(current.checkpoint())
    model, tools = Model(completion("new answer")), Tools()
    _, _, _, observer = await run(restored, model, tools)
    assert restored.outcome == Outcome.COMPLETED and restored.iteration == 2
    assert len(model.requests) == len(observer.usage) == 1
    assert len(restored.model_calls) == 2 and restored.model_calls[0]["call_id"] == "already-paid-provider"
    assert restored.model_calls[0]["applied"] is True
    messages = model.requests[0][0]["messages"]
    answer = next(index for index, message in enumerate(messages) if message.get("content") == "new resumed question")
    assert messages[answer]["metadata"]["control_id"] == "same-control"
    if saved == "no_tool_response":
        old = next(index for index, message in enumerate(messages) if message.get("content") == "old saved response")
        assert old < answer and not tools.dispatched
    else:
        old = next(index for index, message in enumerate(messages) if message.get("tool_call_id") == "original-read")
        assert old < answer and len(tools.dispatched) == 1
        assert restored.tools["original-read"].result_recorded
    assert not restored.followup_messages
