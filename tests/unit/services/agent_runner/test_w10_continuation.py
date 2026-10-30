"""A server restart must preserve a continuous task without replaying its IO."""

import copy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.core.agent_engine.contracts import AgentMode, ExecutionState, Identity, Outcome, ToolCall, ToolFact
from src.services.agent_runner.continuous import interrupted_candidate
from src.services.agent_runner.contracts import RunnerError
from src.services.agent_runner.recovery import RecoveryCoordinator
from src.services.agent_runner.runtime_manifest import (
    configuration_fingerprint, configuration_matches, legacy_configuration_fingerprint)
from src.services.agent_runner.source_receipts import SourceUnavailable


@pytest.fixture
def interrupted(monkeypatch):
    from src.config.settings import settings
    monkeypatch.setattr(settings.agent_runner, 'durable_sleep_enabled', True)
    identity = Identity('tenant', 'user', 'session')
    state = ExecutionState(identity, 'original-runner', AgentMode.MASTER,
        'the accepted role prompt', [{'role':'user','content':'monitor'}], profile_id='main',
        outcome=Outcome.RUNNING, iteration=3)
    state.resources['configuration_fingerprint'] = configuration_fingerprint('profile-fingerprint')
    state.resources['continuous_enabled'] = True
    call = ToolCall('original-read', 'wecom_read_session', {'session':'stable-session'})
    state.pending = [call]
    state.tools[call.id] = ToolFact(call, phase='prepared')
    row = {'runner_id':state.execution_id, 'profile_id':'main',
        'profile_fingerprint':'profile-fingerprint', 'tenant_id':'tenant', 'user_id':'user',
        'session_id':'session', 'source':'chat', 'session_kind':'web', 'status':'interrupted',
        'attempt':4, 'revision':9, 'control_revision':0, 'queue_order':1,
        'cancel_requested':False, 'pause_requested':False, 'resume_control_id':None,
        'input':{'execution_mode':'continuous'},
        'checkpoint':{'continuous':{'version':1,'enabled':True},'execution':state.checkpoint()}}
    authorizer = SimpleNamespace(authorize_persisted=lambda row:SimpleNamespace(identity=identity),
        assert_credit=lambda principal:None)
    profiles = SimpleNamespace(resolve=lambda profile, tenant=None:(None,'profile-fingerprint'))
    return RecoveryCoordinator(authorizer, profiles), row


def test_system_continuation_reuses_original_tree_without_a_synthetic_user_control(interrupted):
    coordinator, row = interrupted
    before = copy.deepcopy(row)
    result = coordinator.prepare(row, continuous_trigger=True)
    restored = result.states[row['runner_id']]
    assert restored.execution_id == row['runner_id'] and restored.iteration == 3
    assert restored.pending[0].id == 'original-read'
    assert restored.tools['original-read'].phase == 'prepared'
    assert restored.system_prompt == 'the accepted role prompt'
    assert restored.messages == before['checkpoint']['execution']['messages']
    assert 'applied_control_id' not in result.checkpoint
    assert result.checkpoint['continuous_recovery'] == {'version':1, 'source_attempt':4}
    assert row == before


def test_crash_before_initial_prepare_preserves_intent_without_inventing_a_tree(interrupted):
    coordinator, row = interrupted
    row['checkpoint'].pop('execution')
    assert interrupted_candidate(row)
    result = coordinator.prepare(row, continuous_trigger=True)
    assert result.states == {} and 'execution' not in result.checkpoint
    assert 'applied_control_id' not in result.checkpoint


def test_lost_model_response_remains_unknown_usage_without_blocking_future_sleep(interrupted):
    coordinator, row = interrupted
    original = {'call_id':'accepted-model-call','execution_id':row['runner_id'],
        'iteration':2,'phase':'started','receipt_id':'original-usage-receipt'}
    completed = {'call_id':'observed-model-call','execution_id':row['runner_id'],
        'iteration':3,'phase':'completed','applied':False,
        'response':{'content':'already observed response'},'usage':{'prompt_tokens':11,'completion_tokens':7}}
    row['checkpoint']['execution']['model_calls'] = copy.deepcopy([original,completed])
    restored = coordinator.prepare(row,continuous_trigger=True).states[row['runner_id']]
    assert restored.model_calls == [{**original,'phase':'unknown'},completed]
    assert row['checkpoint']['execution']['model_calls'] == [original,completed]


@pytest.mark.parametrize('change', [
    {'cancel_requested':True}, {'pause_requested':True}, {'resume_control_id':'accepted-user-input'},
    {'status':'paused'}, {'source':'wecom_kf'}, {'session_kind':'wecom_kf'},
])
def test_pause_cancel_and_user_input_win_over_automatic_restart(interrupted, change):
    coordinator, row = interrupted
    row.update(change)
    assert not interrupted_candidate(row)
    with pytest.raises(RunnerError, match='CONTINUOUS_RECOVERY_SOURCE_INVALID'):
        coordinator.prepare(row, continuous_trigger=True)


def test_ordinary_task_and_human_wait_are_not_silently_restarted(interrupted):
    coordinator, row = interrupted
    row['input'] = {}
    row['checkpoint'].pop('continuous')
    assert not interrupted_candidate(row)
    row['input']['execution_mode'] = 'continuous'
    for kind in ('clarification', 'human_assistance', 'continuous_guard'):
        row['checkpoint']['execution']['waiting'] = {'kind':kind}
        assert not interrupted_candidate(row)


def test_child_user_question_and_saved_terminal_failure_do_not_become_background_work(interrupted):
    _, row = interrupted
    root = row['checkpoint']['execution']
    root['children'] = {'delegated':{'checkpoint':{'outcome':'waiting',
        'waiting':{'kind':'clarification'},'children':{}}}}
    assert not interrupted_candidate(row)
    root['children'] = {}
    root['outcome'] = 'failed'
    assert not interrupted_candidate(row)


def test_unknown_write_still_requires_its_original_adapter_proof(interrupted):
    coordinator, row = interrupted
    fact = row['checkpoint']['execution']['tools']['original-read']
    fact['phase'] = 'waiting'
    fact['call']['name'] = 'wecom_message_send'
    coordinator.tool_recovery = SimpleNamespace(can_restore=Mock(return_value=False))
    with pytest.raises(RunnerError, match='RECOVERY_TOOL_VERIFICATION_REQUIRED'):
        coordinator.prepare(row, continuous_trigger=True)
    assert fact['phase'] == 'waiting'
    coordinator.tool_recovery.can_restore.return_value = True
    result = coordinator.prepare(row, continuous_trigger=True)
    assert result.states[row['runner_id']].tools['original-read'].phase == 'waiting'
    assert result.states[row['runner_id']].pending[0].id == 'original-read'


def test_new_dependency_fingerprint_does_not_scan_unrelated_prompts(monkeypatch):
    before = configuration_fingerprint('profile')
    monkeypatch.setattr(Path, 'rglob', Mock(side_effect=AssertionError('unrelated prompt scan')))
    assert configuration_fingerprint('profile') == before
    assert configuration_matches(before, 'profile')


def test_unused_provider_and_key_rotation_do_not_change_the_accepted_model_dependency(monkeypatch):
    from src.config.settings import settings
    llm = settings.llm.model_copy(deep=True)
    llm.provider = 'qwen'
    llm.lite_model = 'qwen/fixed-lite'
    llm.failover.enabled = False
    monkeypatch.setattr(settings, 'llm', llm)
    before = configuration_fingerprint('profile')
    llm.deepseek.model = 'unused-model-released-later'
    llm.qwen.api_keys = ['test-key-not-persisted']
    assert configuration_fingerprint('profile') == before
    llm.qwen.model = 'different-active-model'
    assert configuration_fingerprint('profile') != before
    assert configuration_fingerprint('changed-profile') != before


def test_legacy_v2_hash_migrates_only_when_it_is_still_exact(interrupted, monkeypatch):
    coordinator, row = interrupted
    legacy = legacy_configuration_fingerprint('profile-fingerprint')
    row['checkpoint']['execution']['resources']['configuration_fingerprint'] = legacy
    result = coordinator.prepare(row, continuous_trigger=True)
    assert result.states[row['runner_id']].resources['configuration_fingerprint'] == configuration_fingerprint('profile-fingerprint')
    assert not configuration_matches('unrecognized-old-release-hash', 'profile-fingerprint')


@pytest.mark.asyncio
async def test_source_outage_defers_original_continuation_instead_of_requiring_manual_resume(interrupted):
    from src.services.agent_runner.worker import RunnerWorker
    _, row = interrupted
    worker = object.__new__(RunnerWorker)
    worker.repository = SimpleNamespace(connection_factory=object())
    worker._continuous_scan_after = 0
    worker.tool_recovery = SimpleNamespace()
    worker.sources = SimpleNamespace(prepare_execution=AsyncMock(side_effect=SourceUnavailable('temporary')))
    repository = SimpleNamespace(next_continuous_candidates=Mock(return_value=[row]),
        defer_continuous=Mock(), claim_continuous=Mock())
    with patch('src.services.agent_runner.recovery_repository.RecoveryRepository', return_value=repository):
        assert await worker._acquire_continuous() is None
    repository.defer_continuous.assert_called_once_with(row, 'SOURCE_RECOVERY_UNAVAILABLE')
    repository.claim_continuous.assert_not_called()
    assert worker._continuous_scan_after == row['queue_order']


@pytest.mark.asyncio
async def test_timer_source_outage_keeps_a_retry_path_instead_of_clearing_its_deadline(interrupted):
    from src.services.agent_runner.worker import RunnerWorker
    _, row = interrupted
    worker = object.__new__(RunnerWorker)
    worker.config = SimpleNamespace(durable_sleep_enabled=True)
    worker.repository = SimpleNamespace(connection_factory=object())
    worker._timer_scan_after = 0
    worker.tool_recovery = SimpleNamespace()
    worker.sources = SimpleNamespace(prepare_execution=AsyncMock(side_effect=SourceUnavailable('temporary')))
    timers = SimpleNamespace(next_candidates=Mock(return_value=[row]), defer=Mock(), hold=Mock())
    with patch('src.services.agent_runner.timer_repository.TimerRepository', return_value=timers):
        assert await worker._acquire_timers() is None
    timers.defer.assert_called_once_with(row, 'SOURCE_RECOVERY_UNAVAILABLE')
    timers.hold.assert_not_called()
