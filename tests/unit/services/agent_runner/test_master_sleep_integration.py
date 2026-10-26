"""Merged truncation policy must preserve durable sleep/call identity semantics.

Real Engine and sleep fact functions are used; only provider, tools and the
checkpoint persistence boundary are fictional. No device/DB/UI is invoked.
"""

import copy

import pytest

from src.core.agent_engine.contracts import CheckpointFailure, ExecutionState, Outcome
from tests.unit.test_agent_engine_acceptance import Control, Model, call, completion, run, state
from tests.unit.services.agent_runner.test_durable_sleep_independent import TimerTools, consume_committed_sleep

pytestmark = pytest.mark.unit


class RecordingTimerTools(TimerTools):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def dispatch(self, current, item):
        self.calls.append(item)
        async for result in super().dispatch(current, item):
            yield result


class CrashAfterCheckpoint(Control):
    def __init__(self, boundary):
        super().__init__()
        self.boundary = boundary

    async def save(self, current, boundary):
        await super().save(current, boundary)
        if boundary == self.boundary:
            raise CheckpointFailure('SIMULATED_PROCESS_STOP_AFTER_CHECKPOINT')


def truncated(tool):
    response = completion('', [call('provider-reused', tool)])
    response['finish_reason'] = 'length'
    response['tool_calls'][0]['function']['arguments'] = (
        '{"seconds":' if tool == 'agent_sleep' else '{"target_name":"Synthetic","text":')
    return response


def versioned_state():
    current = state(max_iterations=50)
    current.resources['tool_identity_version'] = 1
    return current


@pytest.mark.asyncio
@pytest.mark.parametrize('partial_tool', ['agent_sleep', 'wecom_message_send'])
async def test_partial_response_never_dispatches_or_arms_and_later_valid_rounds_keep_namespace(partial_tool):
    current = versioned_state()
    tools = RecordingTimerTools()
    model = Model(truncated(partial_tool),
        completion('', [call('provider-reused', 'wecom_group_poll', {'target_name': 'Synthetic'})]),
        completion('', [call('provider-reused', 'agent_sleep', {'seconds': 1})]))
    _, _, control, _ = await run(current, model, tools)
    rejected = next(saved for boundary, saved in control.saved if boundary == 'model_output_incomplete')
    assert rejected['model_calls'][0]['applied'] is True
    assert rejected['tools'] == {} and rejected['pending'] == [] and rejected['waiting'] is None
    assert [item.name for item in tools.calls] == ['wecom_group_poll', 'agent_sleep']
    assert all(item.id.startswith('call_') and item.id != 'provider-reused' for item in tools.calls)
    assert len({item.id for item in tools.calls}) == 2
    assert [current.resources['tool_cursor_orders'][item.id] for item in tools.calls] == [[2, 0], [3, 0]]
    assert current.outcome == Outcome.WAITING and current.waiting['tool_call_id'] == tools.calls[-1].id
    # Only the later actual committed pure sleep earns a round credit. The
    # truncated sleep-like response remains real paid business work.
    resumed, _ = consume_committed_sleep(current)
    assert resumed.resources['business_iterations'] == 2 and resumed.iteration == 3


@pytest.mark.asyncio
async def test_restart_after_rejection_checkpoint_does_not_reapply_or_reexecute_partial_send():
    current = versioned_state()
    control = CrashAfterCheckpoint('model_output_incomplete')
    original_tools = RecordingTimerTools()
    with pytest.raises(CheckpointFailure):
        await run(current, Model(truncated('wecom_message_send')), original_tools, control)
    saved = next(saved for boundary, saved in control.saved if boundary == 'model_output_incomplete')
    restored = ExecutionState.restore(copy.deepcopy(saved))
    original_response = copy.deepcopy(restored.model_calls[0]['response'])
    assert restored.model_calls[0]['applied'] is True and not original_tools.calls
    fresh_model = Model(completion('', [call('provider-reused', 'wecom_group_poll', {'target_name': 'Synthetic'})]),
                        completion('verified continuation'))
    fresh_tools = RecordingTimerTools()
    await run(restored, fresh_model, fresh_tools)
    assert len(fresh_model.requests) == 2 and restored.outcome == Outcome.COMPLETED
    assert [item.name for item in fresh_tools.calls] == ['wecom_group_poll']
    assert restored.model_calls[0]['response'] == original_response and restored.model_calls[0]['applied'] is True
    assert len(restored.model_calls) == 3
    assert restored.resources['tool_cursor_orders'][fresh_tools.calls[0].id] == [2, 0]
    assert 'incomplete_model_responses' not in restored.resources


@pytest.mark.asyncio
async def test_saved_unapplied_truncation_is_consumed_without_another_provider_call_before_new_sleep():
    current = versioned_state()
    control = CrashAfterCheckpoint('model_usage')
    with pytest.raises(CheckpointFailure):
        await run(current, Model(truncated('agent_sleep')), RecordingTimerTools(), control)
    saved = next(saved for boundary, saved in control.saved if boundary == 'model_usage')
    restored = ExecutionState.restore(copy.deepcopy(saved))
    assert restored.model_calls[0]['applied'] is False
    fresh_model = Model(completion('', [call('provider-reused', 'agent_sleep', {'seconds': 1})]))
    fresh_tools = RecordingTimerTools()
    await run(restored, fresh_model, fresh_tools)
    assert len(fresh_model.requests) == 1 and len(restored.model_calls) == 2
    assert restored.model_calls[0]['applied'] is True
    assert len(fresh_tools.calls) == 1 and fresh_tools.calls[0].id != 'provider-reused'
    assert restored.resources['tool_cursor_orders'][fresh_tools.calls[0].id] == [2, 0]
    assert restored.outcome == Outcome.WAITING and restored.iteration == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('continuous', [False, True])
async def test_fiftieth_business_round_keeps_continuous_guard_separate_from_ordinary_limit_notice(continuous):
    current = versioned_state()
    if continuous:
        current.resources.update(continuous_enabled=True, continuous_segment_start=0)
    model = Model(*[completion('', [call('provider-reused')]) for _ in range(50)],
                  completion('must-not-run-fifty-first'))
    tools = RecordingTimerTools()
    events, _, _, _ = await run(current, model, tools)
    assert len(model.requests) == len(tools.calls) == 50 and len(model.responses) == 1
    assert current.resources['business_iterations'] == current.iteration == 50
    responses = [event for event in events if event['type'] == 'response']
    if continuous:
        assert current.outcome == Outcome.PAUSED and current.waiting['kind'] == 'continuous_guard'
        assert current.error_code is None and not responses and not current.output
    else:
        assert current.outcome == Outcome.ITERATION_LIMIT and current.error_code == 'ITERATION_LIMIT'
        assert current.waiting is None and '未完成' in current.output
        assert len(responses) == 1 and responses[0]['data'] == current.output
