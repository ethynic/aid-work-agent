"""Root pin and invocation must commit together, including child-owned polls."""

import copy
import asyncio
import uuid

import pytest

from src.core.agent_engine.contracts import AgentMode, ExecutionState, Identity, ToolCall, ToolFact
from src.local_tools import repository as devices
from src.local_tools.device_policy import device_ready_error
from src.services.agent_runner.local_invocations import LocalInvocationRepository, LocalPhase, LocalDispatchRejected
from src.services.agent_runner.ownership import LeaseLost
from src.services.agent_runner.wecom_device_binding import RESOURCE
from .test_storage import storage, attempt

pytestmark = pytest.mark.integration


@pytest.fixture
def monitor_store(storage, actors, service_database):
    repository, execution, submit = storage
    actor = actors['a']
    originals = [devices.create_device(actor.tenant_id, actor.user_id, uuid.uuid4().hex,
                 capabilities={'provider_id': 'ai.aidwork.wecom'}) for _ in range(2)]
    assert devices.select_device(actor.tenant_id, actor.user_id, str(originals[0]['id']))

    def prepare(child=False):
        row, principal = submit(actor)
        owned = execution.acquire('monitor-store-owner', 60)
        root = ExecutionState(principal.identity, row['runner_id'], AgentMode.STANDALONE, 'synthetic', [])
        node = ExecutionState(principal.identity, 'child-'+uuid.uuid4().hex, AgentMode.SUBAGENT, 'synthetic', []) if child else root
        call = ToolCall('poll-initial', 'wecom_group_poll', {'target_name': 'Synthetic'})
        node.tools[call.id] = ToolFact(call, phase='dispatching')
        if child:
            root.children['delegate'] = {'execution_id': node.execution_id, 'checkpoint': node.checkpoint()}
        saved = execution.save_checkpoint(attempt(owned), owned['revision'], {'execution': root.checkpoint()})
        return saved, LocalPhase(node.execution_id, call.id, 'main', 0)

    try:
        yield repository, execution, prepare, originals
    finally:
        service_database.rows('DELETE FROM local_tool_events WHERE invocation_id IN (SELECT id FROM local_tool_invocations WHERE user_id=%s)', (actor.user_id,))
        service_database.rows('DELETE FROM local_tool_invocations WHERE user_id=%s', (actor.user_id,))
        service_database.rows('DELETE FROM local_tool_devices WHERE user_id=%s', (actor.user_id,))


def bind(service_database, row, phase, device, tool='wecom_group_poll', arguments=None):
    return LocalInvocationRepository(service_database.connect).bind(attempt(row), row['revision'], phase,
        device_id=str(device['id']), tool_name=tool, arguments=arguments or {'target_name': 'Synthetic'}, provider_key='wecom',
        device_policy=lambda current, now: device_ready_error(current, tool, now))


def add_call(execution, row, phase, tool, arguments):
    checkpoint = copy.deepcopy(row['checkpoint'])
    root = checkpoint['execution']
    node = root if phase.execution_id == row['runner_id'] else root['children']['delegate']['checkpoint']
    call = ToolCall('next-'+uuid.uuid4().hex, tool, arguments)
    node['tools'][call.id] = {
        'call': {'id': call.id, 'name': tool, 'arguments': arguments}, 'phase': 'dispatching'}
    saved = execution.save_checkpoint(attempt(row), row['revision'], checkpoint)
    return saved, LocalPhase(node['execution_id'], call.id, 'main', 0)


@pytest.mark.parametrize('child', [False, True])
def test_first_poll_pins_root_atomically_and_repeat_cas_cannot_enqueue_twice(monitor_store, service_database, child):
    repo, _, prepare, originals = monitor_store
    before, phase = prepare(child)
    saved = bind(service_database, before, phase, originals[0])
    marker = saved['checkpoint']['execution']['resources'][RESOURCE]
    assert marker['device_id'] == str(originals[0]['id']) and marker['runner_id'] == before['runner_id']
    assert marker['identity'] == saved['checkpoint']['execution']['identity']
    if child:
        assert RESOURCE not in saved['checkpoint']['execution']['children']['delegate']['checkpoint']['resources']
    with pytest.raises(LeaseLost, match='CHECKPOINT_REVISION_CHANGED'):
        bind(service_database, before, phase, originals[1])
    repeated = bind(service_database, saved, phase, originals[0])
    assert repeated['revision'] == saved['revision']
    assert repo.get(saved['runner_id'])['checkpoint'] == saved['checkpoint']
    assert len(service_database.rows('SELECT id FROM local_tool_invocations WHERE session_id=%s', (saved['session_id'],))) == 1


@pytest.mark.parametrize('child', [False, True])
def test_new_phase_stays_original_after_selected_changes_and_scope_cannot_be_omitted(monitor_store, service_database, child):
    _, execution, prepare, originals = monitor_store
    row, phase = prepare(child)
    row = bind(service_database, row, phase, originals[0])
    assert devices.select_device(row['tenant_id'], row['user_id'], str(originals[1]['id']))
    args = {'target_name': 'Synthetic', 'text': 'from-model', 'group_cursor': 'opaque'}
    row, next_phase = add_call(execution, row, phase, 'wecom_message_send', args)
    with pytest.raises(LocalDispatchRejected):
        bind(service_database, row, next_phase, originals[1], 'wecom_message_send', args)
    row = bind(service_database, row, next_phase, originals[0], 'wecom_message_send', args)
    for tool, args in [('wecom_message_send', {'target_name': 'Synthetic', 'text': 'missing-scope'}),
                       ('wecom_send_image', {'target_name': 'Synthetic', 'image_path': 'x'}),
                       ('wecom_send_file', {'target_name': 'Synthetic', 'file_path': 'x'})]:
        row, rejected = add_call(execution, row, phase, tool, args)
        with pytest.raises(LocalDispatchRejected):
            bind(service_database, row, rejected, originals[0], tool, args)
    queued = service_database.rows('SELECT device_id FROM local_tool_invocations WHERE session_id=%s', (row['session_id'],))
    assert len(queued) == 2 and all(str(item['device_id']) == str(originals[0]['id']) for item in queued)


def test_revocation_rolls_back_new_poll_and_pin_and_preserves_other_roots(monitor_store, service_database):
    repo, _, prepare, originals = monitor_store
    row, phase = prepare()
    service_database.rows("UPDATE local_tool_devices SET last_seen_at=NOW()-INTERVAL '2 minutes' WHERE id=%s", (str(originals[0]['id']),))
    with pytest.raises(LocalDispatchRejected):
        bind(service_database, row, phase, originals[0])
    assert RESOURCE not in repo.get(row['runner_id'])['checkpoint']['execution']['resources']
    assert not service_database.rows('SELECT id FROM local_tool_invocations WHERE session_id=%s', (row['session_id'],))


def test_child_accept_updates_root_pin_and_saved_phase_reads_original_after_revoke(monitor_store, service_database):
    from src.services.agent_runner.durable_control import DurableControl
    from src.services.agent_runner.runtime.child import ChildControl
    from src.services.agent_runner.local_owner import RunnerLocalLifecycle
    repo, _, prepare, originals = monitor_store
    before, phase = prepare(True)
    saved = bind(service_database, before, phase, originals[0])
    root = ExecutionState.restore(before['checkpoint']['execution'])
    child = ExecutionState.restore(root.children['delegate']['checkpoint'])
    control = DurableControl(repo, attempt(before), before)
    control.state = root
    child_control = ChildControl(root, control, 'delegate')
    owner = RunnerLocalLifecycle(child_control, child, phase.tool_call_id,
        invocations=LocalInvocationRepository(service_database.connect))
    owner._accept(saved)
    assert root.resources[RESOURCE]['device_id'] == str(originals[0]['id'])
    assert owner.wecom_device_id() == str(originals[0]['id'])
    assert RESOURCE not in root.children['delegate']['checkpoint']['resources']
    service_database.rows("UPDATE local_tool_devices SET selected=false,status='revoked' WHERE id=%s", (str(originals[0]['id']),))
    original = asyncio.run(owner.saved_phase(branch='main', ordinal=0))
    assert original['request']['device_id'] == str(originals[0]['id'])
    assert len(service_database.rows('SELECT id FROM local_tool_invocations WHERE session_id=%s', (saved['session_id'],))) == 1
