"""A provider truncation or empty reply must never become a successful task."""

import pytest

from src.core.agent_engine import AgentEngine, Outcome
from tests.unit.test_agent_engine_acceptance import state, Model, Tools, Control, Observer, completion, run, call


@pytest.mark.parametrize("response", [
    {"content": "", "finish_reason": "length"},
    {"content": "unfinished", "finish_reason": "length", "tool_calls": [
        {"id": "broken", "function": {"name": "write", "arguments": '{"content":'}}]},
    {"content": "", "finish_reason": "stop"},
])
async def test_incomplete_generation_recovers_without_executing_partial_tools(response):
    current = state()
    events, tools, control, _ = await run(current, Model(response, completion("verified answer")))
    assert current.outcome == Outcome.COMPLETED and current.output == "verified answer"
    assert not tools.dispatched
    assert current.model_calls[0]["applied"]
    assert any(boundary == "model_output_incomplete" for boundary, _ in control.saved)
    assert [event["data"] for event in events if event["type"] == "response"] == ["verified answer"]


async def test_repeated_incomplete_generation_is_failed_and_has_visible_notice():
    current = state(max_iterations=50)
    events, _, _, _ = await run(current, Model(
        {"content": "", "finish_reason": "length"}, {"content": "", "finish_reason": "length"}))
    assert current.outcome == Outcome.FAILED and current.error_code == "MODEL_OUTPUT_INCOMPLETE"
    assert current.iteration == 2 and "未完成" in current.output
    assert not any(event["type"] == "execution_completed" for event in events)


async def test_iteration_limit_remains_unsuccessful_and_emits_partial_delivery_notice():
    current = state(max_iterations=1)
    events, _, _, _ = await run(current, Model(completion("", [call()])))
    assert current.outcome == Outcome.ITERATION_LIMIT
    assert "未完成" in current.output
    assert any(event["type"] == "response" and "未完成" in event["data"] for event in events)




async def test_original_control_still_cancels_before_truncated_reply_can_retry():
    current = state()
    control = Control("cancel")
    events, _, _, _ = await run(current, Model(), control=control)
    assert current.outcome == Outcome.CANCELLED and current.iteration == 0


@pytest.mark.parametrize("provider_name", ["deepseek", "qwen", "zhipu"])
@pytest.mark.parametrize("arguments", ['{"content": "partial"}', '{"content":'])
async def test_provider_parsing_preserves_truncation_before_engine_can_execute_partial_tools(provider_name, arguments):
    import importlib
    names = {"deepseek": "DeepSeekProvider", "qwen": "QwenProvider", "zhipu": "ZhipuProvider"}
    provider_class = getattr(importlib.import_module(f"src.llm.providers.{provider_name}"), names[provider_name])
    provider = provider_class(api_key="unit-test-key", model="unit-test-model")
    parsed = provider._parse_response({"choices": [{"finish_reason": "length", "message": {
        "content": "", "tool_calls": [{"id": "partial", "function": {"name": "write", "arguments": arguments}}]}}]})
    assert parsed["finish_reason"] == "length"
    current = state()
    events, tools, _, _ = await run(current, Model(parsed, completion("recovered delivery")))
    assert current.outcome == Outcome.COMPLETED and not tools.dispatched
    assert [event["data"] for event in events if event["type"] == "response"] == ["recovered delivery"]


async def test_cancel_during_repeated_incomplete_model_reply_takes_priority_over_generation_failure():
    control = Control()

    class CancellingModel:
        calls = 0

        async def complete(self, current, call_id, definitions):
            self.calls += 1
            if self.calls == 2:
                control.next_command = "cancel"
            return {"content": "", "finish_reason": "length"}

    current = state()
    events, _, _, _ = await run(current, CancellingModel(), control=control)
    assert current.outcome == Outcome.CANCELLED and current.output == ""
    assert not any(event["type"] in {"execution_completed", "response"} for event in events)
