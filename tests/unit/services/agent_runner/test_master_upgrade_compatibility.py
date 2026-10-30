"""A server upgrade must preserve master-created BOSS tasks without client IO."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.core.agent_engine.contracts import AgentMode, ExecutionState, Identity, Outcome, ToolCall, ToolFact
from src.services.agent_runner.contracts import RunnerError
from src.services.agent_runner.profiles import MainProfileCatalog
from src.services.agent_runner.recovery import RecoveryCoordinator
from src.services.agent_runner import runtime_manifest
from src.subagents.loader import SubagentLoader


FROZEN = Path(__file__).resolve().parents[3] / 'fixtures' / 'agent_runner_master_v1'
ROOT = FROZEN.parents[2]


@pytest.fixture
def legacy_master():
    # This is the original master function, not the new compatibility helper.
    manifest = json.loads((FROZEN / 'provenance.json').read_text())
    for name, entry in manifest['files'].items():
        assert hashlib.sha256((FROZEN / name).read_bytes()).hexdigest() == entry['sha256']
    scope = {'__package__': 'src.services.agent_runner', '__file__': runtime_manifest.__file__}
    exec(compile((FROZEN / 'runtime_manifest.py').read_text(), 'frozen_master_manifest.py', 'exec'), scope)
    return scope['configuration_fingerprint']


@pytest.fixture
def profiles(monkeypatch):
    old_registry = SubagentLoader(FROZEN)
    new_registry = SubagentLoader(ROOT / 'subagents')
    # A deployment replaces files at the same /app path. The fixture lives
    # elsewhere only to retain the original bytes, not to simulate a relocation.
    previous = old_registry.get('recruiting-operator')
    current = new_registry.get('recruiting-operator')
    previous.path, previous.dir = current.path, current.dir
    old, new = MainProfileCatalog(), MainProfileCatalog()
    monkeypatch.setattr(old, 'registry', lambda: old_registry)
    monkeypatch.setattr(new, 'registry', lambda: new_registry)
    return old, new


@pytest.mark.parametrize('profile', ['main', 'recruiting-operator'])
def test_original_master_checkpoint_recovers_same_boss_call_and_upgrades_hash(legacy_master, profiles, profile):
    old, new = profiles
    config, fingerprint = old.resolve(profile)
    assert new.resolve(profile)[1] == fingerprint
    identity = Identity('tenant', 'user', 'original-session')
    state = ExecutionState(identity, 'original-runner',
        AgentMode.MASTER if profile == 'main' else AgentMode.STANDALONE,
        'original accepted prompt', [{'role': 'user', 'content': 'read BOSS candidates'}],
        profile_id=profile, profile_version=getattr(config, 'version', None),
        outcome=Outcome.RUNNING, iteration=3, max_iterations=20)
    state.resources['configuration_fingerprint'] = legacy_master(fingerprint)
    call = ToolCall('original-boss-call', 'boss_list_jobs', {})
    state.pending = [call]
    state.tools[call.id] = ToolFact(call, phase='prepared')
    row = {'runner_id': state.execution_id, 'profile_id': profile,
        'profile_fingerprint': fingerprint, 'tenant_id': identity.tenant_id,
        'checkpoint': {'execution': state.checkpoint()}}
    authorizer = SimpleNamespace(authorize_persisted=Mock(return_value=SimpleNamespace(identity=identity)),
                                 assert_credit=Mock())
    prepared = RecoveryCoordinator(authorizer, new).prepare(row,
        {'action': 'resume', 'control_id': 'original-resume', 'payload': {}})
    restored = prepared.states[state.execution_id]
    assert restored.pending == [call] and restored.tools[call.id].phase == 'prepared'
    assert restored.execution_id == state.execution_id and restored.iteration == 3
    assert restored.max_iterations == 20 and restored.system_prompt == state.system_prompt
    assert restored.messages == state.messages
    assert restored.resources['configuration_fingerprint'] == runtime_manifest.configuration_fingerprint(
        fingerprint, config)
    assert row['checkpoint']['execution']['resources']['configuration_fingerprint'] == legacy_master(fingerprint)
    assert prepared.checkpoint['applied_control_id'] == 'original-resume'
    authorizer.assert_credit.assert_called_once()


def test_master_hash_still_rejects_real_runtime_configuration_drift(legacy_master, monkeypatch):
    from src.config.settings import settings
    fingerprint = 'unchanged-profile'
    stored = legacy_master(fingerprint)
    assert runtime_manifest.configuration_matches(stored, fingerprint)
    changed = settings.llm.model_copy(deep=True)
    changed.qwen.model = 'different-model-after-acceptance'
    monkeypatch.setattr(settings, 'llm', changed)
    assert not runtime_manifest.configuration_matches(stored, fingerprint)


def test_unknown_hash_is_not_treated_as_a_legacy_checkpoint(profiles):
    _, new = profiles
    _, fingerprint = new.resolve('main')
    identity = Identity('tenant', 'user', 'session')
    state = ExecutionState(identity, 'runner', AgentMode.MASTER, 'prompt', [], profile_id='main')
    state.resources['configuration_fingerprint'] = 'unrecognized-original-hash'
    row = {'runner_id': 'runner', 'profile_id': 'main', 'profile_fingerprint': fingerprint,
           'tenant_id': 'tenant', 'checkpoint': {'execution': state.checkpoint()}}
    authorizer = SimpleNamespace(authorize_persisted=lambda _: SimpleNamespace(identity=identity),
                                 assert_credit=lambda _: None)
    with pytest.raises(RunnerError, match='RECOVERY_CONFIGURATION_CHANGED'):
        RecoveryCoordinator(authorizer, new).prepare(row,
            {'action': 'resume', 'control_id': 'resume', 'payload': {}})
