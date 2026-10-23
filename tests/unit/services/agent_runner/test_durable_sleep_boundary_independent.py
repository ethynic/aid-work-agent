"""Independent recovery intent checks, beyond the implementation's examples."""
import copy
import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from src.core.agent_engine.contracts import AgentMode, ExecutionState, Identity, Outcome, ToolCall, ToolFact
from src.core.agent_engine.engine import normalize_calls
from src.core.agent_events import _truncate_tool_content
from src.services.agent_runner.continuous_context import retained_context
from src.services.agent_runner.continuous_history import archive_and_compact, compact_facts, retire_child_resources
from src.services.agent_runner.control_repository import ControlRepository
from src.services.agent_runner.contracts import RunnerError, canonical_json
from src.services.agent_runner.legacy_stream_bridge import _LegacyFrameState
from src.services.agent_runner.resource_paths import workspace_directory
from tests.unit.test_agent_engine_acceptance import Model, call, completion, run, state
from tests.unit.services.agent_runner.test_durable_sleep_independent import TimerTools, consume_committed_sleep


@pytest.mark.asyncio
async def test_provider_reusing_id_after_fact_retirement_cannot_accept_previous_wakeup():
    current = state(max_iterations=4)
    current.resources['tool_identity_version'] = 1
    model = Model(completion('', [call('always-the-same', 'agent_sleep', {'seconds': 1})]),
                  completion('', [call('always-the-same', 'agent_sleep', {'seconds': 1})]))
    await run(current, model, TimerTools())
    previous = copy.deepcopy(current.waiting)
    old_id = current.pending[0].id
    current, _ = consume_committed_sleep(current)
    # Complete the consumed result without requesting the following model yet.
    current.tools[old_id].result_recorded = True
    current.messages.append({'role': 'tool', 'tool_call_id': old_id,
        'content': json.dumps(current.tools[old_id].result)})
    current.pending.clear()
    for index in range(40):
        item = ToolCall(f'closed-{index}', 'read', {})
        current.tools[item.id] = ToolFact(item, phase='completed', result={}, result_recorded=True)
    current.resources.pop('round_tool_ids', None)
    compact_facts(current)
    assert old_id not in current.tools
    await run(current, model, TimerTools())
    assert current.pending[0].id != old_id
    assert current.waiting['wait_id'] != previous['wait_id']
    with pytest.raises(RunnerError, match='CONTROL_WAIT_STALE'):
        ControlRepository.assert_wait({'checkpoint': {'execution': current.checkpoint()}},
            SimpleNamespace(action='resume', wait_id=previous['wait_id'], target_execution_id=current.execution_id))


@pytest.mark.asyncio
@pytest.mark.parametrize('version', [None, 1])
async def test_recovering_unapplied_response_keeps_its_original_identity_policy(version):
    current = state()
    current.iteration = 1
    current.resources.update(tool_identity_version=1, business_iterations=1)
    fact = {'call_id': 'original-model-receipt', 'execution_id': current.execution_id, 'iteration': 1,
        'phase': 'completed', 'applied': False,
        'response': completion('', [call('provider-original', 'agent_sleep', {'seconds': 1})])}
    if version is not None:
        fact['tool_identity_version'] = version
    current.model_calls = [fact]
    original = copy.deepcopy(current.checkpoint())
    results = []
    for _ in range(2):
        restored = ExecutionState.restore(original)
        unused_model = Model()
        await run(restored, unused_model, TimerTools())
        assert unused_model.requests == []
        results.append(restored.pending[0].id)
    assert results[0] == results[1]
    assert (results[0] == 'provider-original') is (version is None)


def test_duplicate_id_in_one_provider_response_remains_invalid_after_namespacing():
    with pytest.raises(ValueError, match='DUPLICATE_TOOL_CALL_ID'):
        normalize_calls([call('same'), call('same')], namespace='owned-model-receipt')


def test_exact_cursor_from_large_original_results_survives_removal_of_facts_and_messages():
    current = state()
    expected = {}
    for target in range(5):
        item = ToolCall(f'target-{target}', 'read_messages', {'group': target, 'cursor': 'old'})
        result = {'messages': 'x' * 24000, 'paging': {'next_cursor': f'opaque-{target}-\u2603-000007'}}
        current.tools[item.id] = ToolFact(item, phase='completed', result=result, result_recorded=True)
        current.messages.extend([{'role': 'assistant', 'tool_calls': [item.message_call()]},
            {'role': 'tool', 'tool_call_id': item.id, 'content': _truncate_tool_content(json.dumps(result))}])
        expected[canonical_json(['read_messages', {'group': target}])] = {'/paging/next_cursor': result['paging']['next_cursor']}
    for index in range(40):
        item = ToolCall(f'later-{index}', 'irrelevant_read', {})
        current.tools[item.id] = ToolFact(item, phase='completed', result={}, result_recorded=True)
    compact_facts(current)
    assert all(f'target-{target}' not in current.tools for target in range(5))
    current.messages.clear()
    assert retained_context(current, [])[1] == expected


def test_old_local_reference_cannot_roll_back_more_recent_consumed_target_cursor():
    current = state()
    old = ToolCall('old-local-read', 'read_messages', {'group': 'a'})
    latest = ToolCall('latest-read', 'read_messages', {'group': 'a'})
    for item, cursor in ((old, 'cursor-1'), (latest, 'cursor-2')):
        current.tools[item.id] = ToolFact(item, phase='completed', result={'next_cursor': cursor}, result_recorded=True)
    current.resources['local_invocations'] = {'old-proof': {'tool_call_id': old.id, 'invocation_id': 'old-operation'}}
    for index in range(40):
        item = ToolCall(f'unrelated-{index}', 'no_cursor_result', {})
        current.tools[item.id] = ToolFact(item, phase='completed', result={}, result_recorded=True)
    compact_facts(current)
    assert old.id in current.tools and latest.id not in current.tools
    compact_facts(current)
    assert retained_context(current, [])[1][canonical_json(['read_messages', {'group': 'a'}])] == {'/next_cursor': 'cursor-2'}


@pytest.mark.asyncio
async def test_first_pairing_of_legacy_pending_result_can_advance_previously_archived_cursor():
    current = state(max_iterations=30)
    current.iteration = 17
    target = canonical_json(['read_messages', {'group': 'a'}])
    current.resources.update(business_iterations=17, durable_sleep_version=1, tool_identity_version=1,
        continuous_cursors={target: {'/next_cursor': 'cursor-old'}},
        continuous_cursor_orders={target: [9, 0]})
    legacy = ToolCall('original-legacy-call-id', 'read_messages', {'group': 'a'})
    current.pending = [legacy]
    current.tools[legacy.id] = ToolFact(legacy, phase='completed', result={'next_cursor': 'cursor-new'}, result_recorded=False)
    await run(current, Model(completion('finished')))
    assert legacy.id in current.tools and current.tools[legacy.id].result_recorded
    assert [message['tool_call_id'] for message in current.messages if message.get('role') == 'tool'] == [legacy.id]
    compact_facts(current)
    assert current.resources['continuous_cursors'][target] == {'/next_cursor': 'cursor-new'}


def test_legacy_already_paired_result_advances_saved_cursor_then_orphan_fact_cannot_rewind_it():
    current = state()
    target = canonical_json(['read_messages', {'group': 'a'}])
    current.resources['continuous_cursors'] = {target: {'/next_cursor': 'cursor-9'}}
    orphan = ToolCall('old-completed-local-reference', 'read_messages', {'group': 'a'})
    recent = ToolCall('legacy-completed-and-paired', 'read_messages', {'group': 'a'})
    current.tools[orphan.id] = ToolFact(orphan, phase='completed', result={'next_cursor': 'cursor-1'}, result_recorded=True)
    current.tools[recent.id] = ToolFact(recent, phase='completed', result={'next_cursor': 'cursor-10'}, result_recorded=True)
    current.resources['local_invocations'] = {'old-proof': {'tool_call_id': orphan.id, 'invocation_id': 'original-operation'}}
    current.messages.extend([{'role': 'assistant', 'tool_calls': [recent.message_call()]},
        {'role': 'tool', 'tool_call_id': recent.id, 'content': json.dumps(current.tools[recent.id].result)}])
    for index in range(40):
        item = ToolCall(f'later-closed-{index}', 'unrelated_read', {})
        current.tools[item.id] = ToolFact(item, phase='completed', result={}, result_recorded=True)
    compact_facts(current)
    assert current.resources['continuous_cursors'][target] == {'/next_cursor': 'cursor-10'}
    assert orphan.id in current.tools and recent.id not in current.tools
    current.messages[:] = current.messages[:current.initial_len]
    compact_facts(current)
    assert current.resources['continuous_cursors'][target] == {'/next_cursor': 'cursor-10'}


def test_two_hundred_artifact_rounds_in_paused_child_cannot_grow_checkpoint_projection(monkeypatch):
    from src.services.agent_runner import continuous_history as module
    @contextmanager
    def connect():
        yield SimpleNamespace(cursor=lambda: None, commit=lambda: None)
    monkeypatch.setattr(module, 'lock_runner', lambda *args: {'status': 'running'})
    archived = []
    monkeypatch.setattr(module, 'write_history', lambda cursor, row, result: archived.extend(result['messages']))
    root = state()
    item = ToolCall('waiting-child', 'delegate_to_subagent', {})
    child = state()
    child.execution_id, child.role, child.outcome = 'still-live-child', AgentMode.SUBAGENT, Outcome.PAUSED
    root.pending = [item]
    root.tools[item.id] = ToolFact(item, phase='waiting')
    port = SimpleNamespace(attempt=SimpleNamespace(runner_id=root.execution_id),
        repository=SimpleNamespace(connection_factory=connect), envelope={}, snapshot={})
    for turn in range(200):
        child.images.extend({'file_id': f'child-image-{turn}-{index}'} for index in range(50))
        root.children[item.id] = {'execution_id': child.execution_id, 'status': 'paused', 'checkpoint': child.checkpoint()}
        archive_and_compact(port, root)
        child = ExecutionState.restore(root.children[item.id]['checkpoint'])
    assert len(child.images) <= 48
    assert len(canonical_json(root.checkpoint()).encode()) < 100_000
    retained = {image['file_id'] for image in child.images}
    retained.update(image['file_id'] for message in archived for image in (message.get('metadata') or {}).get('images') or [])
    assert len(retained) == 10_000


def test_sliding_legacy_sse_windows_deliver_each_new_fact_once_for_two_hundred_turns():
    bridge = _LegacyFrameState()
    observed = {'progress': [], 'verbose': [], 'images': []}
    for end in range(1, 201):
        indices = range(max(0, end - 48), end)
        snapshot = {
            'progressMessages': [{'type': 'tool_result', 'toolCallId': str(index), 'data': str(index)} for index in indices],
            'verboseMessages': [{'eventId': f'v-{index}', 'data': str(index)} for index in indices],
            'images': [{'file_id': f'i-{index}'} for index in indices],
        }
        row = {'status': 'running', 'snapshot': snapshot}
        frames, terminal = bridge.project(row)
        assert not terminal
        observed['progress'].extend(item['toolCallId'] for item in frames if item['type'] == 'tool_result')
        observed['verbose'].extend(item['eventId'] for item in frames if item['type'] == 'verbose')
        observed['images'].extend(image['file_id'] for item in frames if item['type'] == 'images' for image in item['images'])
        assert bridge.project(copy.deepcopy(row))[0] == []
    assert observed == {'progress': [str(index) for index in range(200)],
        'verbose': [f'v-{index}' for index in range(200)], 'images': [f'i-{index}' for index in range(200)]}


def child_with_resources(tmp_path):
    root = ExecutionState(Identity('tenant', 'user', 'session'), 'root', AgentMode.MASTER, '', [])
    child = ExecutionState(root.identity, 'closed-child', AgentMode.SUBAGENT, '', [], outcome=Outcome.COMPLETED)
    owned = workspace_directory(tmp_path, root.identity, child.execution_id)
    workspace = owned / 'skill_ws_test'
    workspace.mkdir(parents=True)
    (workspace / 'input.txt').write_text('only this owned temporary input')
    image = owned / 'image_owned.png'
    image.write_bytes(b'original temporary image')
    child.resources = {'workspace': str(workspace), 'image_artifacts': [{'path': str(image)}]}
    root.children['delegate'] = {'execution_id': child.execution_id, 'checkpoint': child.checkpoint(), 'status': 'completed'}
    return root, child.checkpoint(), workspace, image


def test_partial_cleanup_failure_retains_original_owner_for_idempotent_retry(tmp_path, monkeypatch):
    import shutil
    root, child, workspace, image = child_with_resources(tmp_path)
    original = copy.deepcopy(child)
    port = SimpleNamespace(resource_directory=tmp_path)
    remove = shutil.rmtree
    monkeypatch.setattr(shutil, 'rmtree', lambda *args, **kwargs: (_ for _ in ()).throw(PermissionError('fixture busy')))
    assert retire_child_resources(port, root, child) is False
    assert workspace.exists() and not image.exists()
    assert child == original
    monkeypatch.setattr(shutil, 'rmtree', remove)
    assert retire_child_resources(port, root, child) is True
    assert not workspace.exists() and not image.exists()


@pytest.mark.parametrize('blocker', ['live-reference', 'foreign-path', 'workspace-became-file'])
def test_resource_retirement_cannot_drop_owner_while_cleanup_is_unproved(tmp_path, blocker):
    root, child, workspace, image = child_with_resources(tmp_path)
    if blocker == 'live-reference':
        root.messages.append({'role': 'tool', 'content': str(workspace / 'input.txt')})
    elif blocker == 'foreign-path':
        foreign = tmp_path / 'skill_ws_business'
        foreign.mkdir()
        (foreign / 'business.txt').write_text('must remain')
        child['resources']['workspace'] = str(foreign)
    else:
        (workspace / 'input.txt').unlink()
        workspace.rmdir()
        workspace.write_text('still exists; owner must remain')
    assert retire_child_resources(SimpleNamespace(resource_directory=tmp_path), root, child) is False
    assert workspace.exists()
