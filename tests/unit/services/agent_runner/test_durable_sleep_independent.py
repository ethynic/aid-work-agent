"""Independent behavioral acceptance of round allowances and closed facts."""
import copy
from datetime import datetime, timezone
from contextlib import contextmanager
import json
from types import SimpleNamespace

import pytest

from src.core.agent_engine.contracts import DispatchResult, ExecutionState, Outcome
from src.services.agent_runner.durable_sleep import arm, complete_sleep
from src.services.agent_runner.wait_identity import owned_wait_id
from src.services.agent_runner.continuous_history import closed_prefix, compact_facts
from tests.unit.test_agent_engine_acceptance import Model, Tools, call, completion, run, state


@pytest.mark.asyncio
@pytest.mark.parametrize('business_calls,expected,model_calls', [(49, Outcome.COMPLETED, 50),
                                                              (50, Outcome.ITERATION_LIMIT, 50)])
async def test_default_fifty_round_allowance_reaches_fiftieth_and_stops_before_fifty_first(
        business_calls, expected, model_calls):
    current = state()
    current.max_iterations = ExecutionState.__dataclass_fields__['max_iterations'].default
    model = Model(*[completion('', [call(f'read-{index}')]) for index in range(business_calls)],
                  completion('finished-within-allowance'))
    await run(current, model)
    assert current.outcome == expected
    assert len(model.requests) == model_calls
    assert current.resources['business_iterations'] == 50


class TimerTools(Tools):
    async def dispatch(self, current, item):
        if item.name != 'agent_sleep':
            async for result in super().dispatch(current, item):
                yield result
            return
        wait = {'kind': 'timer', 'version': 1, 'tool_call_id': item.id,
                'target_execution_id': current.execution_id, 'seconds': item.arguments['seconds']}
        wait['wait_id'] = owned_wait_id(current.execution_id, current.execution_id, wait)
        yield DispatchResult({'pending': True}, wait=wait)


def consume_committed_sleep(current):
    checkpoint = {'execution': current.checkpoint()}
    deadline = arm(checkpoint, datetime.now(timezone.utc))
    restored = ExecutionState.restore(checkpoint['execution'])
    complete_sleep({'checkpoint': checkpoint, 'wake_at': deadline},
                   {restored.execution_id: restored}, checkpoint, trigger='timer')
    return restored, checkpoint


@pytest.mark.asyncio
async def test_sixty_committed_pure_sleeps_keep_allowance_and_pair_each_result_once():
    current = state(max_iterations=2)
    model = Model(*[completion('', [call(f'sleep-{index}', 'agent_sleep', {'seconds': 1})])
                    for index in range(60)], completion('after-sixty-sleeps'))
    for index in range(60):
        await run(current, model, TimerTools())
        assert current.outcome == Outcome.WAITING
        current, checkpoint = consume_committed_sleep(current)
        assert current.iteration == index + 1
        assert current.resources['business_iterations'] == 0
        # Retrying arm of the same wait must not grant another round.
        credited = copy.deepcopy(checkpoint)
        # The persisted armed execution is the authoritative pre-consumption copy.
        arm(credited, datetime.now(timezone.utc), existing=checkpoint['durable_sleep'])
        assert credited['execution']['resources']['business_iterations'] == 0
    await run(current, model, TimerTools())
    assert current.outcome == Outcome.COMPLETED and current.resources['business_iterations'] == 1
    assert current.iteration == 61 and len(model.requests) == 61
    messages = [message for message in current.messages if message['role'] == 'tool']
    assert len(messages) == 60 and len({message['tool_call_id'] for message in messages}) == 60


@pytest.mark.asyncio
async def test_business_plus_sleep_counts_once_and_does_not_replenish_ordinary_allowance():
    current = state(max_iterations=2)
    model = Model(completion('', [call('read-1'), call('sleep-1', 'agent_sleep', {'seconds': 1})]),
                  completion('', [call('read-2'), call('sleep-2', 'agent_sleep', {'seconds': 1})]),
                  completion('must-never-request-third-business-round'))
    for expected in (1, 2):
        await run(current, model, TimerTools())
        current, _ = consume_committed_sleep(current)
        assert current.resources['business_iterations'] == expected
    await run(current, model, TimerTools())
    assert current.outcome == Outcome.ITERATION_LIMIT
    assert len(model.requests) == 2


@pytest.mark.asyncio
async def test_continuous_sleep_segments_preserve_total_work_and_reset_only_segment_guard():
    current = state(max_iterations=3)
    current.resources['continuous_enabled'] = True
    model = Model(*[completion('', [call(f'read-{index}'), call(f'sleep-{index}', 'agent_sleep', {'seconds': 1})])
                    for index in range(10)], *[completion('still-active') for _ in range(3)])
    for index in range(10):
        await run(current, model, TimerTools())
        current, _ = consume_committed_sleep(current)
        assert current.resources['business_iterations'] == index + 1
    await run(current, model, TimerTools())
    assert current.outcome == Outcome.PAUSED
    assert current.waiting['kind'] == 'continuous_guard'
    assert current.resources['business_iterations'] == 13 and current.iteration == 13


@pytest.mark.asyncio
async def test_previous_segment_guard_identity_cannot_authorize_a_later_guard_of_same_execution():
    from src.services.agent_runner.contracts import RunnerError
    from src.services.agent_runner.control_repository import ControlRepository
    from src.services.agent_runner.durable_control import DurableControl
    from src.services.agent_runner.ownership import Attempt
    current = state(max_iterations=2)
    current.resources['continuous_enabled'] = True
    model = Model(*[completion('still-active') for _ in range(4)])
    owner = DurableControl(None, Attempt(current.execution_id, 'guard-owner', 1),
        {'revision': 0, 'public_snapshot': {}, 'cancel_requested': False, 'status': 'running'})
    await run(current, model)
    owner.decorate_wait(current)
    first = copy.deepcopy(current.waiting)
    current.resources['continuous_segment_start'] = current.resources['business_iterations']
    current.outcome = Outcome.RUNNING
    await run(current, model)
    owner.decorate_wait(current)
    assert current.waiting['wait_id'] != first['wait_id']
    assert current.resources['business_iterations'] == 4
    row = {'checkpoint': {'execution': current.checkpoint()}}
    with pytest.raises(RunnerError, match='CONTROL_WAIT_STALE'):
        ControlRepository.assert_wait(row, SimpleNamespace(action='resume', wait_id=first['wait_id'],
                                                           target_execution_id=current.execution_id))
    assert current.resources['business_iterations'] == 4


def test_closed_history_prefix_never_splits_parallel_tool_results():
    messages = [{'role': 'assistant', 'tool_calls': [{'id': 'a'}, {'id': 'b'}]},
                {'role': 'tool', 'tool_call_id': 'a', 'content': 'read-a'},
                {'role': 'tool', 'tool_call_id': 'b', 'content': 'read-b'},
                {'role': 'assistant', 'content': 'next'}]
    assert [closed_prefix(messages, limit) for limit in (1, 2, 3, 4)] == [0, 0, 3, 4]


def test_model_fact_compaction_preserves_all_tokens_and_unresolved_responses():
    current = state()
    current.model_calls = [{'call_id': f'completed-{index}', 'phase': 'completed', 'applied': True,
                            'usage': {'prompt_tokens': index+1, 'completion_tokens': 2, 'cached_tokens': 1}}
                           for index in range(180)]
    current.model_calls.append({'call_id': 'unapplied', 'phase': 'completed', 'applied': False,
                                'response': completion('must-be-restored'), 'usage': {'prompt_tokens': 9}})
    current.model_calls.append({'call_id': 'unknown-io', 'phase': 'started'})
    before = sum((fact.get('usage') or {}).get('prompt_tokens', 0) for fact in current.model_calls)
    compact_facts(current)
    totals = current.resources['archived_token_usage']
    assert sum((fact.get('usage') or {}).get('prompt_tokens', 0) for fact in current.model_calls) + totals['input'] == before
    assert len(current.model_calls) == 34
    assert any(fact['call_id'] == 'unapplied' and fact['response']['content'] == 'must-be-restored'
               for fact in current.model_calls)
    assert any(fact['call_id'] == 'unknown-io' for fact in current.model_calls)
    previous = copy.deepcopy(current.checkpoint())
    compact_facts(current)
    assert current.checkpoint() == previous


@pytest.mark.asyncio
async def test_accelerated_parent_child_capacity_preserves_five_cursors_history_cost_and_duration(monkeypatch):
    from src.config.settings import settings
    from src.core.agent_engine.contracts import AgentMode, ToolCall, ToolFact
    from src.core.execution_usage import execution_token_usage
    from src.services.agent_runner import continuous_history as history
    from src.services.agent_runner.continuous_context import prepare_compaction, context_prompt
    from src.services.agent_runner.contracts import canonical_json
    from src.services.agent_runner.durable_control import DurableControl
    from src.services.agent_runner.ownership import Attempt
    from src.services.agent_runner.usage_pricing import accumulate_cost, grouped_cost
    @contextmanager
    def connect():
        yield SimpleNamespace(cursor=lambda: None, commit=lambda: None)
    archived = []
    monkeypatch.setattr(history, 'lock_runner', lambda *args: {'status': 'running'})
    monkeypatch.setattr(history, 'write_history', lambda cursor, row, result:
                        archived.extend(message['_history_id'] for message in result['messages']))
    root = state()
    root.initial_len = len(root.messages)
    root.resources['continuous_instructions'] = {'accepted-rule': {'text': 'Reply only to important updates', 'attachments': []}}
    unpruned = copy.deepcopy(root)
    control = SimpleNamespace(attempt=Attempt(root.execution_id, 'capacity-owner', 1),
        repository=SimpleNamespace(connection_factory=connect), envelope={}, snapshot={})
    all_receipts, summary_calls, maximum_bytes, measured = [], 0, 0, []
    accumulator = None
    duration_envelope = {}
    snapshot = {'version': 1, 'price': {'input_price_per_m': 1, 'output_price_per_m': 2,
                                      'cached_input_price_per_m': .2}, 'usage_factor': 100}
    def usage_receipt(prompt, output):
        return {'phase': 'observed', 'owner': 'llm', 'provider': 'fixture', 'model': 'fixture',
                'billing_boundary': 'main', 'price_snapshot': snapshot,
                'usage': {'prompt_tokens': prompt, 'completion_tokens': output}}
    async def summarize(*args, **kwargs):
        nonlocal summary_calls
        summary_calls += 1
        all_receipts.append(usage_receipt(3, 1))
        return 'Continue original task; cursors remain opaque facts.', {'prompt_tokens': 3, 'completion_tokens': 1}, False
    for index in range(160):
        call = ToolCall(f'read-{index}', 'read_fixture', {'target': f'group-{index%5}'})
        delegate = ToolCall(f'delegate-{index}', 'delegate_to_subagent', {'subagent_name': 'fictional-child'})
        result = json.dumps({'next_cursor': f'opaque:{index%5}:{index}', 'data': 'x'*20000})
        child = state()
        child.execution_id, child.role, child.outcome = f'child-{index}', AgentMode.SUBAGENT, Outcome.COMPLETED
        child.messages = [{'role': 'assistant', 'tool_calls': [call.message_call()]},
                          {'role': 'tool', 'tool_call_id': call.id, 'content': result}]
        child.initial_len = 0
        child.tools = {call.id: ToolFact(call, phase='completed', result=result, result_recorded=True)}
        child.model_calls = [{'call_id': f'child-model-{index}', 'execution_id': child.execution_id,
                              'phase': 'completed', 'applied': True,
                              'usage': {'prompt_tokens': 3, 'completion_tokens': 1}}]
        messages = [{'role': 'assistant', 'tool_calls': [call.message_call(), delegate.message_call()]},
                    {'role': 'tool', 'tool_call_id': call.id, 'content': result},
                    {'role': 'tool', 'tool_call_id': delegate.id, 'content': 'Child observation complete'}]
        for current in (root, unpruned):
            current.messages.extend(copy.deepcopy(messages))
            current.tools[call.id] = ToolFact(call, phase='completed', result=result, result_recorded=True)
            current.tools[delegate.id] = ToolFact(delegate, phase='completed', result='Child observation complete', result_recorded=True)
            current.children[delegate.id] = {'execution_id': child.execution_id, 'status': 'completed', 'checkpoint': child.checkpoint()}
            current.model_calls.append({'call_id': f'root-model-{index}', 'execution_id': current.execution_id,
                'phase': 'completed', 'applied': True, 'usage': {'prompt_tokens': 10, 'completion_tokens': 2},
                'response': {'content': result}})
            current.iteration += 1
        before = len(all_receipts)
        all_receipts.extend([usage_receipt(10, 2), usage_receipt(3, 1)])
        if index%16 == 15:
            size = len(canonical_json({'execution': root.checkpoint(), **control.envelope}).encode())
            measured.append((index, size))
            maximum_bytes = max(maximum_bytes, size)
            await prepare_compaction(root, summarize)
            history.archive_and_compact(control, root)
        cost, totals, breakdown = accumulate_cost(all_receipts[before:], accumulator)
        accumulator = breakdown['runner_accumulator']
        tracker = DurableControl(None, Attempt(root.execution_id, 'capacity-owner', index+1),
            {'revision': 0, 'public_snapshot': {}, 'cancel_requested': False, 'status': 'running', 'checkpoint': duration_envelope})
        tracker.capture_duration()
        duration_envelope = tracker.envelope
        assert len(duration_envelope) <= 3
    unpruned_bytes = len(canonical_json(unpruned.checkpoint()).encode())
    final_bytes = len(canonical_json(root.checkpoint()).encode())
    maximum_bytes = max(maximum_bytes, final_bytes)
    assert unpruned_bytes > settings.agent_runner.limits.checkpoint_bytes
    assert maximum_bytes < settings.agent_runner.limits.checkpoint_bytes
    assert max(size for index, size in measured if index >= 80) <= max(
        size for index, size in measured if 48 <= index < 80)+65536
    assert execution_token_usage(root.checkpoint()) == {'input': 2080, 'output': 480, 'cached': 0}
    assert len(root.resources['continuous_cursors']) == 5
    assert 'Reply only to important updates' in context_prompt(root)
    assert all(f'group-{target}' in context_prompt(root) for target in range(5))
    assert len(archived) == len(set(archived))
    expected_cost, expected_totals, _ = grouped_cost(all_receipts)
    assert cost == expected_cost and totals == expected_totals
    assert totals['prompt_tokens'] == 2080+summary_calls*3
    assert len(accumulator['groups']) == 1
    print({'cycles': 160, 'simulated_model_receipts': 320, 'summary_fixtures': summary_calls,
           'unpruned_bytes': unpruned_bytes, 'max_checkpoint_bytes': maximum_bytes,
           'final_checkpoint_bytes': final_bytes, 'unique_archived_ids': len(archived),
           'cumulative_credit': str(cost)})
