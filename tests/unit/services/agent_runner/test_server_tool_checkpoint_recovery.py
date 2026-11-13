from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.core.agent_engine.contracts import AgentMode, CheckpointFailure, ExecutionState, Identity, ToolCall, ToolFact
from src.services.agent_runner.browser_recovery import ApplicationToolRecovery
from src.services.agent_runner.contracts import RunnerError
from src.services.agent_runner.recovery import RecoveryCoordinator
from src.services.agent_runner.runtime_manifest import configuration_fingerprint
from src.services.agent_runner.runtime.tools import ToolDispatcher


def recovery_fixture(call, workspace=None):
    identity = Identity('fixture-tenant', 'fixture-user', 'fixture-session')
    state = ExecutionState(identity, 'fixture-root', AgentMode.MASTER, 'fixture', [], profile_id='main')
    state.resources['configuration_fingerprint'] = configuration_fingerprint('fixture-main')
    if workspace:
        state.resources['workspace'] = str(workspace)
    state.tools['saved'] = ToolFact(ToolCall('saved', 'knowledge_file_search', {'file_name': 'old'}),
        phase='completed', result={'count': 1}, result_recorded=True)
    state.tools[call.id] = ToolFact(call, phase='dispatching')
    state.pending = [call]
    row = {'profile_id': 'main', 'profile_fingerprint': 'fixture-main', 'runner_id': state.execution_id,
           'status': 'interrupted', 'checkpoint': {'execution': state.checkpoint()}}
    authorizer = SimpleNamespace(authorize_persisted=lambda row: SimpleNamespace(identity=identity),
                                 assert_credit=lambda principal: None)
    profiles = SimpleNamespace(resolve=lambda profile, tenant=None: (None, 'fixture-main'))
    recovery = RecoveryCoordinator(authorizer, profiles, tool_recovery=ApplicationToolRecovery(None, None, profiles))
    return recovery, row, {'control_id': 'fixture-resume', 'action': 'resume', 'payload': {}}


def quotation_command(source):
    return (f'OUT=/app/storage/tmp/quoteprice_$(date +%s) && mkdir -p $OUT && echo "OUT=$OUT"'
            f' && cd /app/src/skills/quotation-pricing-1.0.0 && python scripts/parse_quotation.py "{source}"'
            ' --json $OUT/items_extracted.json --project "项目名"'
            ' && echo "=====JSON HEAD=====" && head -c 3000 $OUT/items_extracted.json')


@pytest.mark.parametrize('quote', [False, True])
def test_interrupted_repeatable_tool_restores_original_pending_call(tmp_path, quote, monkeypatch):
    from src.config.settings import settings
    from src.saas.services.tenant_skill_cache import tenant_skill_cache
    from src.services.agent_runner.server_tool_recovery import QUOTATION_DIRECTORY
    monkeypatch.setattr(settings.skills.master_agent,'allowed',['quotation-pricing'])
    monkeypatch.setattr(tenant_skill_cache,'get_or_load',lambda tenant, root, allowed: {
        'quotation-pricing':SimpleNamespace(dir=QUOTATION_DIRECTORY,version='1.0.0')} if 'quotation-pricing' in allowed else {})
    source = tmp_path / '原始 清单.xlsx'
    source.touch()
    call = (ToolCall('pending', 'skill_execute', {'skill': 'quotation-pricing', 'command': quotation_command(source.as_posix())})
            if quote else ToolCall('pending', 'knowledge_file_search', {'file_name': '报价单'}))
    recovery, row, control = recovery_fixture(call, tmp_path)
    original = deepcopy(row['checkpoint'])
    restored = recovery.prepare(row, control).states[row['runner_id']]
    assert restored.pending == [call]
    assert restored.tools[call.id].phase == 'dispatching'
    assert restored.tools['saved'].result == {'count': 1}
    assert row['checkpoint'] == original


def test_recovery_preflight_rejects_tenant_override_before_claim(tmp_path, monkeypatch):
    from src.saas.services.tenant_skill_cache import tenant_skill_cache
    monkeypatch.setattr(tenant_skill_cache,'get_or_load',lambda *_: {
        'quotation-pricing':SimpleNamespace(dir=tmp_path / 'tenant-override',version='1.0.0')})
    source = tmp_path / 'test.xlsx'
    source.touch()
    call = ToolCall('pending','skill_execute',{'skill':'quotation-pricing',
        'command':quotation_command(source.as_posix())})
    recovery,row,control = recovery_fixture(call,tmp_path)
    with pytest.raises(RunnerError, match='RECOVERY_TOOL_UNAVAILABLE'):
        recovery.prepare(row,control)
    assert row['checkpoint']['execution']['tools'][call.id]['phase'] == 'dispatching'


@pytest.mark.parametrize('suffix', [' && touch /tmp/external-effect', ' # changed command', '$(touch /tmp/external-effect)'])
def test_unverified_extra_shell_operations_remain_blocked(tmp_path, suffix):
    source = tmp_path / '原始 清单.xlsx'
    source.touch()
    call = ToolCall('pending', 'skill_execute',
                    {'skill': 'quotation-pricing', 'command': quotation_command(source.as_posix()) + suffix})
    recovery, row, control = recovery_fixture(call, tmp_path)
    with pytest.raises(RunnerError, match='RECOVERY_TOOL_VERIFICATION_REQUIRED'):
        recovery.prepare(row, control)


@pytest.mark.parametrize('project', ['$(touch /tmp/external-effect)', '`touch /tmp/external-effect`',
    'project --user-id replacement', 'project {session_id}', 'project --input /tmp/input'])
def test_skill_recovery_does_not_admit_interpolation_or_normal_command_rewrites(tmp_path, project):
    source = tmp_path / '原始清单.xlsx'
    source.touch()
    call = ToolCall('pending', 'skill_execute', {'skill': 'quotation-pricing',
        'command': quotation_command(source.as_posix()).replace('项目名', project)})
    recovery, row, control = recovery_fixture(call, tmp_path)
    with pytest.raises(RunnerError, match='RECOVERY_TOOL_VERIFICATION_REQUIRED'):
        recovery.prepare(row, control)


@pytest.mark.asyncio
@pytest.mark.parametrize('call_type', ['custom_skill', 'missing_knowledge_tool'])
async def test_repeatable_call_drift_interrupts_instead_of_creating_unanswerable_wait(tmp_path, call_type):
    if call_type == 'custom_skill':
        call = ToolCall('pending','skill_execute',{'skill':'quotation-pricing',
            'command':quotation_command((tmp_path / 'test.xlsx').as_posix())})
        skill = SimpleNamespace(dir=tmp_path / 'tenant-override', version='1.0.0')
    else:
        call = ToolCall('pending','knowledge_file_search',{'file_name':'price list'})
        skill = None
    _,row,_ = recovery_fixture(call)
    state = ExecutionState.restore(row['checkpoint']['execution'])
    dispatcher = ToolDispatcher(catalog=SimpleNamespace(_get_tools=lambda: []),
        registry=SimpleNamespace(get_tool=lambda name: None),executor=None,control_tools=None,
        local=None,skills=SimpleNamespace(skill_registry=SimpleNamespace(get=lambda name: skill)),
        plan_manager=None,subagent_config=None,control=SimpleNamespace(durable_owner=True))

    with pytest.raises(CheckpointFailure, match='RECOVERY_TOOL_UNAVAILABLE') as failure:
        await dispatcher.recover(state,call)
    assert failure.value.public_verification
    assert state.tools[call.id].phase == 'dispatching'
    assert state.waiting is None
