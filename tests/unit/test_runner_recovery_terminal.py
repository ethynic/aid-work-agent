"""Recovery owner state transitions; authorization/profile reads are IO ports."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.core.agent_engine.contracts import AgentMode, ExecutionState, Identity, Outcome, ToolCall, ToolFact
from src.services.agent_runner.contracts import RunnerError
from src.services.agent_runner.recovery import RecoveryCoordinator
from src.services.agent_runner.runtime_manifest import configuration_fingerprint


def checkpoint_fixture(outcome, phase='completed'):
    identity = Identity('fixture-tenant', 'fixture-user', 'fixture-session')
    parent = ExecutionState(identity, 'fixture-root', AgentMode.MASTER, 'fixture', [], profile_id='main', outcome=Outcome.PAUSED)
    parent.resources['configuration_fingerprint'] = configuration_fingerprint('fixture-current-main')
    call = ToolCall('fixture-delegate', 'delegate_to_subagent', {'subagent_name': 'fixture-child'})
    parent.tools[call.id] = ToolFact(call, phase='waiting')
    child = ExecutionState(identity, 'fixture-child-execution', AgentMode.SUBAGENT, 'fixture', [],
                           profile_id='fixture-child', outcome=outcome)
    child.tools['fixture-write'] = ToolFact(ToolCall('fixture-write', 'write', {}), phase=phase)
    parent.children[call.id] = {'execution_id': child.execution_id, 'status': outcome.value,
        'checkpoint': child.checkpoint(), 'task_record': {'execution_id': child.execution_id,
            'task_id': 'fixture-original-task', 'subagent_name': 'fixture-child'}}
    authorized_children = []
    authorizer = SimpleNamespace(authorize_persisted=lambda row: SimpleNamespace(identity=identity),
        assert_credit=lambda principal: None,
        authorize_child_profile=lambda principal, profile: authorized_children.append(profile))
    profiles = SimpleNamespace(resolve=lambda profile: (None, 'fixture-current-main'))
    row = {'profile_id': 'main', 'profile_fingerprint': 'fixture-current-main', 'runner_id': parent.execution_id,
           'checkpoint': {'execution': parent.checkpoint()}}
    control = {'control_id': 'fixture-control', 'action': 'resume', 'payload': {}}
    return RecoveryCoordinator(authorizer, profiles), row, control, child, authorized_children


@pytest.mark.parametrize('outcome', [Outcome.COMPLETED, Outcome.FAILED, Outcome.CANCELLED, Outcome.ITERATION_LIMIT])
def test_prepare_preserves_known_stopped_child_without_rearming_steps_or_requiring_historical_permission(outcome):
    coordinator, row, control, child, authorized = checkpoint_fixture(outcome)
    original = deepcopy(row)
    prepared = coordinator.prepare(row, control)
    restored = prepared.states[child.execution_id]
    assert restored.outcome == outcome and restored.tools == child.tools
    assert prepared.checkpoint['execution']['children']['fixture-delegate']['checkpoint'] == child.checkpoint()
    assert prepared.states[row['runner_id']].outcome == Outcome.RUNNING
    assert authorized == [] and row == original


@pytest.mark.parametrize('outcome', [Outcome.FAILED, Outcome.CANCELLED])
@pytest.mark.parametrize('phase', ['dispatching', 'waiting'])
def test_prepare_rejects_unknown_stopped_child_effect_before_consuming_or_rearming(outcome, phase):
    coordinator, row, control, _, _ = checkpoint_fixture(outcome, phase)
    original = deepcopy(row)
    with pytest.raises(RunnerError, match='RECOVERY_TOOL_VERIFICATION_REQUIRED'):
        coordinator.prepare(row, control)
    assert row == original and 'applied_control_id' not in row['checkpoint']
