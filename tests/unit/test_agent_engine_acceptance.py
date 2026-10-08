"""Independent kernel acceptance: real loop, fake model/tool I/O boundaries.

These tests do not prove durable worker recovery or real BOSS execution; those
require separate process/database/device acceptance after service integration.
"""

import asyncio
from copy import deepcopy
import json

import pytest

from src.core.agent_engine import (
    AgentEngine, AgentMode, DispatchResult, ExecutionState, Outcome,
)
from src.core.agent_engine.contracts import Identity
from src.core.agent_engine.contracts import ToolCall, ToolFact


def state(tenant="a", role=AgentMode.STANDALONE, max_iterations=4):
    return ExecutionState(
        identity=Identity(f"tenant-{tenant}", f"user-{tenant}", f"session-{tenant}"),
        execution_id=f"execution-{tenant}", role=role, system_prompt="Be helpful.",
        messages=[{"role": "user", "content": f"request-{tenant}"}],
        initial_len=1, max_iterations=max_iterations,
    )


def completion(text="done", calls=None):
    return {"content": text, "tool_calls": calls or [],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3}}


def call(call_id="call-1", name="read", args=None):
    return {"id": call_id, "type": "function", "function": {
        "name": name, "arguments": json.dumps(args or {})}}


class Model:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    async def complete(self, current, call_id, definitions):
        self.requests.append((current.checkpoint(), call_id, deepcopy(definitions)))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return deepcopy(response)


class Tools:
    def __init__(self, result=None):
        self.result = result or DispatchResult({"success": True, "data": "read result"})
        self.dispatched = []
        self.recovered = []

    def definitions(self):
        return [{"type": "function", "function": {
            "name": "read", "description": "Read data", "parameters": {"type": "object"}}}]

    def display_name(self, item):
        return item.name

    async def dispatch(self, current, item):
        self.dispatched.append((current.identity, item))
        yield self.result

    async def recover(self, current, item):
        self.recovered.append((current.identity, item))
        return self.result


class Control:
    def __init__(self, command=None):
        self.next_command = command
        self.saved = []

    def command(self):
        return self.next_command

    async def save(self, current, boundary):
        self.saved.append((boundary, current.checkpoint()))


class Observer:
    def __init__(self):
        self.events = []
        self.usage = []

    async def event(self, current, event):
        self.events.append((current.identity, deepcopy(event)))

    async def model_usage(self, current, fact):
        self.usage.append((current.identity, deepcopy(fact)))


async def run(current, model, tools=None, control=None, observer=None, engine=None):
    tools = tools or Tools()
    control = control or Control()
    observer = observer or Observer()
    events = [event async for event in (engine or AgentEngine()).run(
        current, model, tools, control, observer)]
    return events, tools, control, observer


@pytest.mark.asyncio
@pytest.mark.parametrize("role", list(AgentMode))
async def test_each_role_uses_real_multiround_loop_and_paired_tool_results(role):
    current = state(role=role)
    model = Model(completion("", [call()]), completion("final answer"))
    events, tools, _, observer = await run(current, model)
    assert current.outcome == Outcome.COMPLETED
    assert current.output == "final answer"
    assert len(model.requests) == 2
    assert len(tools.dispatched) == 1
    tool_messages = [m for m in model.requests[1][0]["messages"] if m["role"] == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0]["tool_call_id"] == "call-1"
    assert "read result" in tool_messages[0]["content"]
    assert len(observer.usage) == 2
    assert len({entry[1] for entry in model.requests}) == 2
    assert events


@pytest.mark.asyncio
async def test_two_tool_calls_in_one_round_are_paired_before_next_model_call():
    current = state()
    model = Model(completion("", [call("first"), call("second")]), completion())
    _, tools, _, _ = await run(current, model)
    assert [item.id for _, item in tools.dispatched] == ["first", "second"]
    next_messages = model.requests[1][0]["messages"]
    assert [m["tool_call_id"] for m in next_messages if m["role"] == "tool"] == ["first", "second"]
    assert current.outcome == Outcome.COMPLETED


@pytest.mark.asyncio
async def test_concurrent_runs_on_one_engine_do_not_share_identity_output_or_images():
    both_arrived = asyncio.Event()
    arrivals = []

    class ConcurrentModel(Model):
        async def complete(self, current, call_id, definitions):
            if not current.tools:
                arrivals.append(current.identity.tenant_id)
                if len(arrivals) == 2:
                    both_arrived.set()
                await asyncio.wait_for(both_arrived.wait(), timeout=2)
                return completion("", [call(current.execution_id)])
            return completion(current.identity.user_id)

    class IdentityTools(Tools):
        async def dispatch(self, current, item):
            await asyncio.sleep(0)
            yield DispatchResult({"tenant": current.identity.tenant_id}, images=(
                {"file_id": current.execution_id},))

    a, b = state("a"), state("b")
    engine, model, tools, observer = AgentEngine(), ConcurrentModel(), IdentityTools(), Observer()
    await asyncio.gather(run(a, model, tools, observer=observer, engine=engine),
                         run(b, model, tools, observer=observer, engine=engine))
    assert a.output == "user-a" and b.output == "user-b"
    assert a.images == [{"file_id": "execution-a"}]
    assert b.images == [{"file_id": "execution-b"}]
    assert len(observer.usage) == 4
    assert {identity.tenant_id for identity, _ in observer.usage} == {"tenant-a", "tenant-b"}
    assert "tenant-b" not in json.dumps(a.checkpoint())
    assert "tenant-a" not in json.dumps(b.checkpoint())


@pytest.mark.asyncio
@pytest.mark.parametrize("command,expected", [("cancel", Outcome.CANCELLED), ("pause", Outcome.PAUSED)])
async def test_initial_control_stops_before_model_or_tool_dispatch(command, expected):
    current, model, tools = state(), Model(completion()), Tools()
    _, _, control, observer = await run(current, model, tools, Control(command))
    assert current.outcome == expected
    assert model.requests == [] and tools.dispatched == []
    assert observer.usage == []
    assert control.saved


@pytest.mark.asyncio
async def test_iteration_exhaustion_is_explicit_and_never_success():
    current = state(max_iterations=1)
    model = Model(completion("", [call()]), completion("must not run"))
    await run(current, model)
    assert current.outcome == Outcome.ITERATION_LIMIT
    assert len(model.requests) == 1


@pytest.mark.asyncio
async def test_completed_tool_checkpoint_resumes_without_dispatching_it_twice():
    current = state()
    tools = Tools()
    _, _, control, _ = await run(current, Model(completion("", [call()]), completion()), tools)
    snapshots = [snapshot for _, snapshot in control.saved
                 if snapshot["tools"].get("call-1", {}).get("result_recorded")
                 and snapshot["outcome"] == Outcome.RUNNING.value]
    assert snapshots, "No saved safe boundary after recording a completed tool result"
    restored = ExecutionState.restore(json.loads(json.dumps(snapshots[0])))
    model = Model(completion("resumed answer"))
    await run(restored, model, tools)
    assert len(tools.dispatched) == 1
    assert tools.recovered == []
    assert restored.outcome == Outcome.COMPLETED
    tool_messages = [m for m in model.requests[0][0]["messages"] if m["role"] == "tool"]
    assert len(tool_messages) == 1 and tool_messages[0]["tool_call_id"] == "call-1"


def test_checkpoint_roundtrip_isolated_and_rejects_unknown_version():
    original = state()
    snapshot = original.checkpoint()
    restored = ExecutionState.restore(snapshot)
    restored.messages[0]["content"] = "modified"
    assert original.messages[0]["content"] == "request-a"
    assert snapshot["messages"][0]["content"] == "request-a"
    snapshot["schema_version"] = 999
    with pytest.raises(ValueError, match="CHECKPOINT_VERSION_UNSUPPORTED"):
        ExecutionState.restore(snapshot)


@pytest.mark.asyncio
async def test_saved_dispatch_intent_recovers_original_call_instead_of_redispatching():
    current = state()
    original = ToolCall("original-call", "read", {"candidate": "c-1"})
    current.pending = [original]
    current.tools[original.id] = ToolFact(original, phase="dispatching", invocation_id="inv-original")
    current.messages.append({"role": "assistant", "content": "", "tool_calls": [original.message_call()]})
    restored = ExecutionState.restore(current.checkpoint())
    tools = Tools()
    await run(restored, Model(completion()), tools)
    assert tools.dispatched == []
    assert len(tools.recovered) == 1
    assert tools.recovered[0][1].id == original.id
    assert restored.tools[original.id].invocation_id == "inv-original"
    assert [m["tool_call_id"] for m in restored.messages if m["role"] == "tool"] == [original.id]


@pytest.mark.asyncio
async def test_unknown_external_effect_stays_waiting_and_never_repeats_write():
    current = state()
    original = ToolCall("write-original", "read", {})
    current.pending = [original]
    current.tools[original.id] = ToolFact(original, phase="dispatching")
    tools = Tools(DispatchResult(None, wait={"kind": "verify_effect", "call_id": original.id}))
    model = Model(completion("must not execute"))
    await run(current, model, tools)
    assert current.outcome == Outcome.WAITING
    assert current.waiting["call_id"] == original.id
    assert model.requests == [] and tools.dispatched == []
    assert current.pending[0].id == original.id
    assert current.tools[original.id].result_recorded is False


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_count", [0, 2])
async def test_tool_protocol_rejects_missing_or_multiple_terminal_results(terminal_count):
    class BrokenTools(Tools):
        async def dispatch(self, current, item):
            for _ in range(terminal_count):
                yield DispatchResult({"success": True})

    current = state()
    model = Model(completion("", [call()]), completion("must not run"))
    with pytest.raises(RuntimeError, match="TOOL_PROTOCOL"):
        await run(current, model, BrokenTools())
    assert current.outcome == Outcome.FAILED
    assert len(model.requests) == 1
    assert not [m for m in current.messages if m["role"] == "tool"]


@pytest.mark.asyncio
async def test_durable_write_failure_before_dispatch_prevents_tool_side_effect():
    class BrokenStorage(Control):
        async def save(self, current, boundary):
            if boundary == "before_tool":
                raise OSError("storage unavailable")
            await super().save(current, boundary)

    current, tools = state(), Tools()
    with pytest.raises(OSError, match="storage unavailable"):
        await run(current, Model(completion("", [call()])), tools, BrokenStorage())
    assert current.outcome == Outcome.FAILED
    assert tools.dispatched == []


@pytest.mark.asyncio
async def test_echo_retry_reports_each_actual_model_call_usage_once():
    current = state()
    model = Model(completion('[{"id":"x","tool_result":"raw result"}]'), completion("real answer"))
    _, tools, _, observer = await run(current, model)
    assert current.outcome == Outcome.COMPLETED
    assert current.output == "real answer"
    assert len(model.requests) == 2 and len(observer.usage) == 2
    assert tools.dispatched == []
    assert len({fact["call_id"] for _, fact in observer.usage}) == 2


@pytest.mark.asyncio
async def test_kernel_task_cancellation_saves_cancelled_state_and_propagates():
    current = state()
    control = Control()
    with pytest.raises(asyncio.CancelledError):
        await run(current, Model(asyncio.CancelledError()), control=control)
    assert current.outcome == Outcome.CANCELLED
    assert control.saved[-1][1]["outcome"] == Outcome.CANCELLED.value


@pytest.mark.asyncio
async def test_pause_between_sibling_calls_preserves_remaining_work_and_result_pairing():
    class PauseAfterFirstResult(Control):
        async def save(self, current, boundary):
            if boundary == "tool_completed":
                self.next_command = "pause"
            await super().save(current, boundary)

    current, tools, control = state(), Tools(), PauseAfterFirstResult()
    await run(current, Model(completion("", [call("first"), call("second")])), tools, control)
    assert current.outcome == Outcome.PAUSED
    assert [item.id for _, item in tools.dispatched] == ["first"]
    assert [item.id for item in current.pending] == ["second"]
    restored = ExecutionState.restore(control.saved[-1][1])
    await run(restored, Model(completion("resumed")), tools)
    assert restored.outcome == Outcome.COMPLETED
    assert [item.id for _, item in tools.dispatched] == ["first", "second"]
    assert [m["tool_call_id"] for m in restored.messages if m["role"] == "tool"] == ["first", "second"]


@pytest.mark.asyncio
async def test_waiting_tool_resume_recovers_call_then_executes_untouched_siblings():
    class WaitingTools(Tools):
        async def dispatch(self, current, item):
            self.dispatched.append((current.identity, item))
            if item.id == "first":
                yield DispatchResult(None, wait={"kind": "human", "call_id": "first"})
            else:
                yield self.result

    current, tools = state(), WaitingTools()
    model = Model(completion("", [call("first"), call("second")]))
    _, _, control, _ = await run(current, model, tools)
    assert current.outcome == Outcome.WAITING
    assert len(model.requests) == 1
    restored = ExecutionState.restore(control.saved[-1][1])
    await run(restored, Model(completion()), tools)
    assert [item.id for _, item in tools.dispatched] == ["first", "second"]
    assert [item.id for _, item in tools.recovered] == ["first"]
    assert [m["tool_call_id"] for m in restored.messages if m["role"] == "tool"] == ["first", "second"]


@pytest.mark.asyncio
async def test_failed_model_boundary_write_does_not_call_model():
    class OfflineStorage(Control):
        async def save(self, current, boundary):
            raise OSError("storage offline")

    current, model, tools = state(), Model(completion()), Tools()
    with pytest.raises(OSError, match="storage offline"):
        await run(current, model, tools, OfflineStorage())
    assert model.requests == [] and tools.dispatched == []
    assert current.outcome == Outcome.FAILED


@pytest.mark.asyncio
async def test_duplicate_call_ids_fail_before_any_tool_is_dispatched():
    current, tools = state(), Tools()
    with pytest.raises(ValueError, match="DUPLICATE_TOOL_CALL_ID"):
        await run(current, Model(completion("", [call("duplicate"), call("duplicate")])), tools)
    assert tools.dispatched == []
    assert current.outcome == Outcome.FAILED


@pytest.mark.asyncio
async def test_completed_state_observation_does_not_start_execution_again():
    current = state()
    current.outcome = Outcome.COMPLETED
    model, tools = Model(completion("must not call")), Tools()
    events, _, _, observer = await run(current, model, tools)
    assert events == [] and observer.usage == []
    assert model.requests == [] and tools.dispatched == []


@pytest.mark.asyncio
async def test_completed_tool_fact_restores_failure_images_and_untruncated_content():
    current = state()
    original = ToolCall("finished", "read", {})
    full_content = "full skill guide:" + "x" * 50000
    image = {"file_id": "restored-image", "placement": "after_text"}
    current.pending = [original]
    current.messages.append({"role": "assistant", "content": "", "tool_calls": [original.message_call()]})
    current.tools[original.id] = ToolFact(
        original, phase="completed", result=full_content, invocation_id="inv-finished",
        result_recorded=False, success=False, preserve_content=True, images=[image])
    restored = ExecutionState.restore(json.loads(json.dumps(current.checkpoint())))
    model, tools = Model(completion()), Tools()
    events, _, _, _ = await run(restored, model, tools)
    assert tools.dispatched == [] and tools.recovered == []
    result_event = next(e for e in events if e["type"] == "tool_result")
    assert result_event["success"] is False
    assert result_event["result"] == full_content
    assert restored.images == [image]
    assert restored.tools[original.id].invocation_id == "inv-finished"
    tool_message = next(m for m in model.requests[0][0]["messages"] if m["role"] == "tool")
    assert tool_message["content"] == full_content


@pytest.mark.asyncio
async def test_echo_retry_provider_failure_preserves_first_response_and_records_failed_second_call():
    current = state()
    echo = '[{"id":"x","tool_result":"raw result"}]'
    model = Model(completion(echo), RuntimeError("provider unavailable"))
    _, tools, control, observer = await run(current, model)
    assert current.outcome == Outcome.COMPLETED and current.output == echo
    assert len(model.requests) == 2 and len(observer.usage) == 1
    assert [fact["phase"] for fact in current.model_calls] == ["completed", "failed"]
    assert "model_failed" in [boundary for boundary, _ in control.saved]
    assert tools.dispatched == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_boundary", ["before_model", "model_usage", "observer_usage"])
async def test_echo_retry_authoritative_failure_propagates_instead_of_using_first_response(failure_boundary):
    class FailingControl(Control):
        async def save(self, current, boundary):
            if len(current.model_calls) == 2 and boundary == failure_boundary:
                raise OSError("authoritative write unavailable")
            await super().save(current, boundary)
    class FailingObserver(Observer):
        async def model_usage(self, current, fact):
            if len(current.model_calls) == 2 and failure_boundary == "observer_usage":
                raise OSError("authoritative write unavailable")
            await super().model_usage(current, fact)
    current = state()
    with pytest.raises(OSError, match="authoritative write unavailable"):
        await run(current, Model(completion('[{"id":"x","tool_result":"raw"}]'), completion("retry")),
                  control=FailingControl(), observer=FailingObserver())
    assert current.outcome == Outcome.FAILED and current.output == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("child_owner_field", ["execution_id", "child_execution_id"])
async def test_restored_parent_does_not_apply_or_mark_child_model_response_as_its_own(child_owner_field):
    current = state(role=AgentMode.MASTER, max_iterations=1)
    current.iteration = 1
    own = {"call_id": "original-parent-model", "execution_id": current.execution_id,
           "iteration": 1, "phase": "completed", "applied": False,
           "response": completion("saved-original-parent-output")}
    child = {"call_id": "original-child-model", child_owner_field: "other-child-execution",
             "iteration": 1, "phase": "completed", "applied": False,
             "response": completion("", [call("child-only-clarification", "clarify", {"question": "child-only question"})])}
    current.model_calls = [own, child]
    child_state = ExecutionState(identity=current.identity, execution_id="other-child-execution",
        role=AgentMode.SUBAGENT, system_prompt="", messages=[], iteration=1, model_calls=[deepcopy(child)])
    delegated = ToolCall("original-owned-delegate", "delegate_to_subagent", {})
    current.tools[delegated.id] = ToolFact(delegated, phase="completed", result_recorded=True)
    current.children[delegated.id] = {"execution_id": child_state.execution_id,
                                    "checkpoint": child_state.checkpoint(), "status": "running"}
    # JSON reconstruction reproduces a separate process observing the aggregate
    # usage facts. It does not rebuild another loop or fake its return value.
    restored = ExecutionState.restore(json.loads(json.dumps(current.checkpoint())))
    model, tools = Model(), Tools()
    _, _, _, observer = await run(restored, model, tools)
    assert restored.outcome == Outcome.COMPLETED and restored.output == "saved-original-parent-output"
    assert model.requests == [] and tools.dispatched == [] and tools.recovered == []
    assert observer.usage == []
    assert len(restored.model_calls) == 1
    assert restored.model_calls[0]["applied"] is True
    authoritative_child = restored.children[delegated.id]["checkpoint"]
    assert authoritative_child["model_calls"][0]["applied"] is False
    assert not any(message.get("tool_calls") for message in restored.messages)


@pytest.mark.parametrize("child_owner_field", ["execution_id", "child_execution_id"])
def test_legacy_mixed_child_model_fact_without_its_authoritative_checkpoint_requires_verification(child_owner_field):
    current = state(role=AgentMode.MASTER)
    current.model_calls = [{"call_id": "orphaned-child-model", child_owner_field: "missing-child-execution",
                            "iteration": 1, "phase": "completed", "applied": False,
                            "response": completion("must-not-use-or-invent-child-state")}]
    with pytest.raises(ValueError, match="CHECKPOINT_MODEL_OWNER_VERIFICATION_REQUIRED"):
        ExecutionState.restore(json.loads(json.dumps(current.checkpoint())))
