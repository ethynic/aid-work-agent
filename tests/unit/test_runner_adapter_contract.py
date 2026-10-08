"""Adapter contract version gate on recovery; authorization/profile reads are IO ports."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.core.agent_engine.contracts import AgentMode, ExecutionState, Identity, Outcome, ToolCall, ToolFact
from src.services.agent_runner.adapter_contract import (ADAPTER_CONTRACT_BASELINE, ADAPTER_CONTRACT_VERSION,
    SUPPORTED_ADAPTER_CONTRACT_VERSIONS, adapter_contract_mismatch, adapter_contract_supported,
    persisted_adapter_contract_version)
from src.services.agent_runner.contracts import RunnerError
from src.services.agent_runner.recovery import RecoveryCoordinator
from src.services.agent_runner.runtime_manifest import configuration_fingerprint

ROOT_FINGERPRINT, CHILD_FINGERPRINT = 'fixture-current-main', 'fixture-child-current'


def contract_fixture(*, root_version='current', child_outcome=Outcome.RUNNING, child_version='current'):
    """Root active state with one delegated child; versions 'current'/None/integer literal."""
    identity = Identity('fixture-tenant', 'fixture-user', 'fixture-session')

    def stamped(state, version, fingerprint):
        state.resources['configuration_fingerprint'] = configuration_fingerprint(fingerprint)
        if version is not None:
            state.resources['adapter_contract_version'] = (ADAPTER_CONTRACT_VERSION
                                                           if version == 'current' else version)
        return state

    parent = stamped(ExecutionState(identity, 'fixture-root', AgentMode.MASTER, 'fixture', [],
                                    profile_id='main', outcome=Outcome.RUNNING), root_version, ROOT_FINGERPRINT)
    call = ToolCall('fixture-delegate', 'delegate_to_subagent', {'subagent_name': 'fixture-child'})
    parent.tools[call.id] = ToolFact(call, phase='waiting')
    child = stamped(ExecutionState(identity, 'fixture-child-execution', AgentMode.SUBAGENT, 'fixture', [],
                                   profile_id='fixture-child', outcome=child_outcome), child_version, CHILD_FINGERPRINT)
    parent.children[call.id] = {'execution_id': child.execution_id, 'status': child_outcome.value,
        'profile_id': 'fixture-child', 'profile_fingerprint': CHILD_FINGERPRINT,
        'checkpoint': child.checkpoint(), 'task_record': {'execution_id': child.execution_id,
            'task_id': 'fixture-original-task', 'subagent_name': 'fixture-child'}}
    authorizer = SimpleNamespace(authorize_persisted=lambda row: SimpleNamespace(identity=identity),
        assert_credit=lambda principal: None,
        authorize_child_profile=lambda principal, profile: None)
    fingerprints = {'main': ROOT_FINGERPRINT, 'fixture-child': CHILD_FINGERPRINT}
    profiles = SimpleNamespace(resolve=lambda profile: (None, fingerprints[profile]))
    row = {'profile_id': 'main', 'profile_fingerprint': ROOT_FINGERPRINT, 'runner_id': parent.execution_id,
           'checkpoint': {'execution': parent.checkpoint()}}
    control = {'control_id': 'fixture-control', 'action': 'resume', 'payload': {}}
    return RecoveryCoordinator(authorizer, profiles), row, control


def test_contract_constants_declare_bounded_supported_versions():
    # Bounded manual integers, not a source hash: copy/prompt changes never bump these.
    assert type(ADAPTER_CONTRACT_BASELINE) is int and ADAPTER_CONTRACT_BASELINE == 1
    assert type(ADAPTER_CONTRACT_VERSION) is int and ADAPTER_CONTRACT_VERSION >= ADAPTER_CONTRACT_BASELINE
    assert isinstance(SUPPORTED_ADAPTER_CONTRACT_VERSIONS, frozenset)
    assert ADAPTER_CONTRACT_VERSION in SUPPORTED_ADAPTER_CONTRACT_VERSIONS
    assert ADAPTER_CONTRACT_BASELINE in SUPPORTED_ADAPTER_CONTRACT_VERSIONS
    assert all(type(version) is int for version in SUPPORTED_ADAPTER_CONTRACT_VERSIONS)


@pytest.mark.parametrize('resources,expected', [
    ({}, False),  # legacy missing field -> declared baseline
    ({'adapter_contract_version': ADAPTER_CONTRACT_VERSION}, False),
    ({'adapter_contract_version': ADAPTER_CONTRACT_BASELINE}, False),
    ({'adapter_contract_version': 0}, True),
    ({'adapter_contract_version': 2}, True),
    ({'adapter_contract_version': '1'}, True),  # tampered JSON type is not a version
    ({'adapter_contract_version': True}, True),
    ({'adapter_contract_version': None}, True),
    (None, False),  # absent resources dict still means the baseline
])
def test_adapter_contract_mismatch_matrix(resources, expected):
    assert adapter_contract_mismatch(resources) is expected
    assert adapter_contract_supported(persisted_adapter_contract_version(resources)) is (not expected)


@pytest.mark.parametrize('version', [0, 2, 99])
def test_prepare_rejects_unknown_root_version_before_consuming_control(version):
    coordinator, row, control = contract_fixture(root_version=version)
    original = deepcopy(row)
    with pytest.raises(RunnerError, match='RECOVERY_ADAPTER_VERSION_UNSUPPORTED') as error:
        coordinator.prepare(row, control)
    assert error.value.status == 409
    assert row == original and 'applied_control_id' not in row['checkpoint']


@pytest.mark.parametrize('version', ['1', True, None])
def test_prepare_rejects_non_integer_tampered_root_version(version):
    coordinator, row, control = contract_fixture(root_version=None)
    # An explicit JSON null/string/bool is a tampered fact, not the legacy
    # missing-field baseline, and must be rejected as unknown.
    row['checkpoint']['execution']['resources']['adapter_contract_version'] = version
    with pytest.raises(RunnerError, match='RECOVERY_ADAPTER_VERSION_UNSUPPORTED'):
        coordinator.prepare(row, control)


def test_prepare_rejects_unknown_version_on_active_child_before_configuration_check():
    coordinator, row, control = contract_fixture(child_version=2)
    original = deepcopy(row)
    with pytest.raises(RunnerError, match='RECOVERY_ADAPTER_VERSION_UNSUPPORTED'):
        coordinator.prepare(row, control)
    assert row == original


def test_prepare_accepts_missing_version_as_declared_baseline():
    coordinator, row, control = contract_fixture(root_version=None, child_version=None)
    prepared = coordinator.prepare(row, control)
    assert set(prepared.states) == {'fixture-root', 'fixture-child-execution'}
    assert prepared.states['fixture-root'].outcome == Outcome.RUNNING


def test_prepare_accepts_current_version_on_root_and_active_child():
    coordinator, row, control = contract_fixture()
    prepared = coordinator.prepare(row, control)
    assert prepared.states['fixture-child-execution'].outcome == Outcome.RUNNING


def test_prepare_leaves_completed_read_only_child_with_unknown_version_unvalidated():
    # Same boundary as configuration_fingerprint: finished subtrees are read-only
    # facts and never re-executed, so their persisted version is not gated.
    coordinator, row, control = contract_fixture(child_outcome=Outcome.COMPLETED, child_version=99)
    prepared = coordinator.prepare(row, control)
    assert prepared.states['fixture-child-execution'].outcome == Outcome.COMPLETED
