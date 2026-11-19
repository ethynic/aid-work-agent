"""Real Runner, Runtime result storage and PostgreSQL recovery acceptance."""
from datetime import datetime, timedelta, timezone
import json
import uuid

import pytest

from src.local_tools import repository as devices
from src.local_tools.catalog import EXECUTION_RECEIPT_CAPABILITY
from src.notifications.repository import unprotect
from src.services.agent_runner.auto_local_recovery import CHECKPOINT_KEY
from .conftest import wait_for
from .provider import Reply, tool_reply
from .test_local_invocation_recovery import interrupted, local_diagnosis, ordinary_local
from .test_local_verification import claim_probe, enable, stopped_proof
from .test_storage import storage
from .test_worker import accept, api_pair, prices, runner, terminal, workers

pytestmark = pytest.mark.integration


def _enable(workers, provider='boss-recruiting'):
    workers.environment['AGENT_RUNNER_AUTO_LOCAL_RECOVERY_PROVIDERS'] = provider


def _accept_local(workers, actor, profile):
    prompt = workers.provider.register(tool_reply('boss_select_job',
        {'job_name': 'Fixture exact job'}, call_id='recovery-local-call'),
        Reply(content='本机步骤已处理'))
    return accept(workers.api, actor, prompt, profile_id=profile)['runner_id']


def _deliver(workers, database, identifier, actor, device, *, code=None, effect='none', retryable=False,
             tool_name=None, data=None):
    try:
        queued = wait_for(lambda: next(iter(database.rows(
            "SELECT id FROM local_tool_invocations WHERE tenant_id=%s AND user_id=%s AND state='queued'",
            (actor.tenant_id, actor.user_id))), None), timeout=15)
    except AssertionError:
        diagnosis = local_diagnosis(workers, database, identifier)
        failed = runner(database, identifier)
        diagnosis['failure_code'] = (failed.get('result') or {}).get('error_code')
        results = [fact.get('result') for fact in failed['checkpoint']['execution']['tools'].values()]
        diagnosis['tool_codes'] = [item.get('code') if isinstance(item, dict) else type(item).__name__
                                   for item in results]
        diagnosis['fixture_device_state'] = database.rows(
            "SELECT selected,status,capabilities_json,EXTRACT(epoch FROM now()::timestamp-last_seen_at) AS age "
            "FROM local_tool_devices WHERE tenant_id=%s", (actor.tenant_id,))
        diagnosis['reason'] = [str(item.get('message', ''))[:150] for item in results if isinstance(item, dict)]
        raise AssertionError(diagnosis) from None
    identifier = str(queued['id'])
    token = uuid.uuid4().hex
    claimed = devices.claim_next(device, actor.tenant_id, token, 60, expected_invocation_id=identifier)
    assert claimed
    if tool_name:
        assert claimed['tool_name'] == tool_name
    assert devices.mark_started(identifier, actor.tenant_id, token)['state'] == 'running'
    first = devices.write_runtime_result(identifier, actor.tenant_id, token, code is None,
        code=code, effect=effect, retryable=retryable, device_id=device, data=data)
    assert first and first['effect'] == effect
    return identifier


def _notifications(database, identifier):
    return database.rows("SELECT event_type,parameters_encrypted FROM notification_messages WHERE event_key=%s",
                         ('terminal:' + identifier,))


@pytest.mark.parametrize('outcome', ['ordinary-success', 'retry-success', 'later-model-failure', 'exhausted', 'login'])
def test_configured_real_worker_retries_only_safe_local_failures(workers, ordinary_local, service_database, outcome):
    _enable(workers)
    actor, profile, folder, _ = ordinary_local
    device = json.loads((folder/'device-ready.json').read_text())['device_id']
    if outcome == 'later-model-failure':
        prompt = workers.provider.register(tool_reply('boss_select_job',
            {'job_name': 'Fixture exact job'}, call_id='recovery-local-call'), Reply(status=400))
        identifier = accept(workers.api, actor, prompt, profile_id=profile)['runner_id']
    else:
        identifier = _accept_local(workers, actor, profile)
    retries_succeed = outcome in {'retry-success', 'later-model-failure'}
    child, _ = workers.start()
    if outcome == 'ordinary-success':
        call_ids = [_deliver(workers, service_database, identifier, actor, device, effect='applied')]
    elif outcome == 'login':
        call_ids = [_deliver(workers, service_database, identifier, actor, device, code='NOT_LOGGED_IN', retryable=True)]
    else:
        attempts = 1 if retries_succeed else 3
        call_ids = [_deliver(workers, service_database, identifier, actor, device, code='BUSY', retryable=True)
                    for _ in range(attempts)]
        if retries_succeed:
            call_ids.append(_deliver(workers, service_database, identifier, actor, device, effect='applied'))
    finished = terminal(service_database, identifier, timeout=25)
    workers.assert_clean_exit(child)
    records = service_database.rows("""SELECT id,arguments_json,business_ref FROM local_tool_invocations
        WHERE tenant_id=%s ORDER BY created_at""", (actor.tenant_id,))
    assert [str(item['id']) for item in records] == call_ids
    assert len({json.dumps(item['arguments_json'], sort_keys=True) for item in records}) == 1
    if len(records) > 1:
        assert len({item['business_ref']['branch'] for item in records[1:]}) == 1
        assert [item['business_ref']['ordinal'] for item in records[1:]] == list(range(1, len(records)))
    notices = _notifications(service_database, identifier)
    if retries_succeed or outcome == 'ordinary-success':
        assert finished['status'] == ('failed' if outcome == 'later-model-failure' else 'completed')
        episode = next(iter(finished['checkpoint'][CHECKPOINT_KEY].values()))
        assert episode['status'] == 'recovered'
        assert bool(episode['deadline']) == retries_succeed
        view = workers.api.call('GET', f'/v1/runners/{identifier}', actor=actor).json()['runner']
        assert view.get('local_recovery_mode') == ('automatic' if retries_succeed else None)
        assert not notices  # Later model failure is outside the local-recovery notice policy.
    else:
        assert finished['status'] == 'failed'
        assert finished['result']['error_code'] == ('NOT_LOGGED_IN' if outcome == 'login' else 'LOCAL_RECOVERY_EXHAUSTED')
        assert finished['result']['output'] and len(notices) == 1
        assert notices[0]['event_type'] == 'task.failed'
        summary = unprotect(notices[0]['parameters_encrypted'])['recovery_summary']
        assert ('登录' if outcome == 'login' else 'BUSY') in summary
        followup = accept(workers.api, actor, workers.provider.register(Reply(content='same-session-new-task')))
        second, _ = workers.start()
        assert terminal(service_database, followup['runner_id'])['result']['output'] == 'same-session-new-task'
        workers.assert_clean_exit(second)


@pytest.mark.parametrize(('tool_name', 'arguments', 'failure_code', 'followup', 'recovered'), [
    ('boss_list_jobs', {}, 'WRONG_PAGE', {}, True),
    ('boss_list_jobs', {}, 'WRONG_PAGE', None, False),
    ('boss_resume_batch', {'limit': 3}, 'WRONG_PAGE', {'limit': 3}, True),
    ('boss_resume_batch', {'limit': 3}, 'WRONG_PAGE', {'limit': 2}, False),
    ('boss_list_jobs', {}, 'NOT_LOGGED_IN', {}, True),
], ids=['list-jobs-recovered', 'navigation-alone', 'resume-batch-recovered', 'different-batch-limit', 'login-resolved'])
def test_agent2_recovery_summary_needs_a_later_success_for_the_same_request(
        workers, ordinary_local, service_database, tool_name, arguments, failure_code, followup, recovered):
    _enable(workers)
    actor, profile, folder, _ = ordinary_local
    device = json.loads((folder/'device-ready.json').read_text())['device_id']
    assert devices.touch_device_seen(device, capabilities={'provider_id': 'ai.aidwork.boss-recruiting',
        'capabilities': [EXECUTION_RECEIPT_CAPABILITY]})
    later_calls = [('boss_goto', {'target': 'recommend'}, 'return-to-recommend', 'none', None),
        ('boss_filter', {'experience': '3-5年'}, 'apply-filters', 'applied', {'applied': True, 'filter_count': 1})]
    if followup is not None and tool_name == 'boss_list_jobs':
        later_calls.insert(1, (tool_name, followup, 'after-page-ready', 'none', {'jobs': [{'name': 'Fixture exact job'}]}))
    if followup is not None and tool_name == 'boss_resume_batch':
        later_calls.append((tool_name, followup, 'after-page-ready', 'none',
                            {'resumes': [], 'failures': [], 'stop_reason': 'no_progress'}))
    replies = [tool_reply(tool_name, arguments, call_id='before-page-ready')]
    replies.extend(tool_reply(name, args, call_id=call_id) for name, args, call_id, _, _ in later_calls)
    replies.append(Reply(content='已应用筛选条件'))
    identifier = accept(workers.api, actor, workers.provider.register(*replies), profile_id=profile)['runner_id']
    child, _ = workers.start()
    for _ in range(3 if failure_code == 'WRONG_PAGE' else 1):
        _deliver(workers, service_database, identifier, actor, device, tool_name=tool_name,
                 code=failure_code, retryable=failure_code == 'WRONG_PAGE',
                 data={'receipt_version': '1.1', 'step': 'execute', 'known_effect': 'none',
                       'reason': {'code': failure_code, 'message': '未找到筛选按钮' if failure_code == 'WRONG_PAGE'
                                  else '已进入登录页'}})
    for name, _, _, effect, data in later_calls:
        _deliver(workers, service_database, identifier, actor, device, tool_name=name, effect=effect, data=data)
    finished = terminal(service_database, identifier, timeout=25)
    workers.assert_clean_exit(child)
    assert finished['checkpoint']['execution']['outcome'] == 'completed'
    notices = _notifications(service_database, identifier)
    if recovered:
        assert finished['status'] == 'completed'
        assert finished['result']['output'] == '已应用筛选条件'
        assert not notices
    else:
        assert finished['status'] == 'failed'
        assert finished['result']['error_code'] == 'LOCAL_RECOVERY_EXHAUSTED'
        assert tool_name in finished['result']['output']
        assert 'execute' in finished['result']['output']
        assert 'WRONG_PAGE' in finished['result']['output']
        assert len(notices) == 1
        assert tool_name in unprotect(notices[0]['parameters_encrypted'])['recovery_summary']


def test_expired_parked_unknown_ends_and_keeps_only_original_device_hold(workers, ordinary_local, service_database):
    _enable(workers)
    actor, profile, folder, _ = ordinary_local
    device = json.loads((folder/'device-ready.json').read_text())['device_id']
    identifier = _accept_local(workers, actor, profile)
    child, _ = workers.start()
    original = _deliver(workers, service_database, identifier, actor, device, code='EXECUTION_UNKNOWN', effect='unknown', retryable=True)
    waiting = wait_for(lambda: (row if (row := runner(service_database, identifier))['status'] == 'waiting' else None))
    workers.assert_clean_exit(child)
    assert _notifications(service_database, identifier) == []
    key, episode = next(iter(waiting['checkpoint'][CHECKPOINT_KEY].items()))
    assert episode['invocation_id'] == original and episode['status'] == 'waiting'
    # Advance only the test deadline. The real receipt, park and takeover remain unchanged.
    past = (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat()
    service_database.rows("UPDATE agent_runners SET checkpoint=jsonb_set(checkpoint,%s,to_jsonb(%s::text)) WHERE runner_id=%s",
                          ([CHECKPOINT_KEY, key, 'deadline'], past, identifier))
    replacement, _ = workers.start()
    expired = terminal(service_database, identifier)
    workers.assert_clean_exit(replacement)
    assert expired['status'] == 'failed' and expired['result']['error_code'] == 'LOCAL_RECOVERY_DEADLINE'
    assert expired['checkpoint']['detached_local_invocations'] == [original]
    assert len(_notifications(service_database, identifier)) == 1
    assert service_database.rows('SELECT 1 FROM agent_runner_session_claims WHERE owner_runner_id=%s', (identifier,)) == []
    assert [str(item['id']) for item in service_database.rows(
        'SELECT id FROM local_tool_invocations WHERE tenant_id=%s', (actor.tenant_id,))] == [original]
    with pytest.raises(devices.DeviceOperationUnresolved):
        devices.create_invocation(actor.tenant_id, actor.user_id, device, 'boss_select_job', {'job_name': 'Second job'})
    followup = accept(workers.api, actor, workers.provider.register(Reply(content='same-session-after-deadline')))
    new_worker, _ = workers.start()
    assert terminal(service_database, followup['runner_id'])['result']['output'] == 'same-session-after-deadline'
    workers.assert_clean_exit(new_worker)


@pytest.mark.asyncio
async def test_configured_directory_external_provider_uses_same_persisted_retry(
        storage, actors, service_database):
    import asyncio

    from src.config.settings import AgentRunnerConfig
    from src.core.agent_engine.contracts import AgentMode, ExecutionState, Identity, ToolCall, ToolFact
    from src.local_tools.durable_flow import original_invocation
    from src.local_tools.lifecycle import LocalOperationScope, local_operation_scope
    from src.local_tools.proxy_tool import LocalToolProxyTool
    from src.services.agent_runner.durable_control import DurableControl
    from src.services.agent_runner.local_owner import RunnerLocalLifecycle
    from .test_storage import attempt

    class DeclaredTool(LocalToolProxyTool):
        name = 'fixture_document_read'
        provider_key = invocation_provider_key = 'fixture-document'
        display_name = '测试工具'

    _, executions, submit = storage
    actor = actors['a']
    submit(actor)
    owned = executions.acquire('fixture-worker', 60)
    identity = Identity(actor.tenant_id, actor.user_id, actor.session_id, 'chat', 'web')
    state = ExecutionState(identity, owned['runner_id'], AgentMode.MASTER, 'Fixture', [])
    call = ToolCall('fixture-call', DeclaredTool.name, {})
    state.tools[call.id] = ToolFact(call, phase='dispatching')
    state.pending = [call]
    row = executions.save_checkpoint(attempt(owned), owned['revision'], {'execution': state.checkpoint()})
    control = DurableControl(executions, attempt(row), row)
    control.auto_local_recovery_providers = AgentRunnerConfig(
        auto_local_recovery_providers=[DeclaredTool.provider_key]).auto_local_recovery_providers
    owner = RunnerLocalLifecycle(control, state, call.id)
    capabilities = {'capabilities':['tool_declarations_v1'],
                    'declared_tools':{DeclaredTool.provider_key:[DeclaredTool.name]}}
    device = devices.create_device(actor.tenant_id, actor.user_id, uuid.uuid4().hex, capabilities=capabilities)
    device_id = str(device['id'])
    assert devices.select_device(actor.tenant_id, actor.user_id, device_id)
    try:
        original = await original_invocation(DeclaredTool(), LocalOperationScope(owner, 'main', 0), device, {})
        original_id = str(original['id'])
        assert devices.claim_next(device_id, actor.tenant_id, 'original-claim', 60,
                                 provider_keys=[DeclaredTool.provider_key])
        devices.write_runtime_result(original_id, actor.tenant_id, 'original-claim',
                                     False, code='BUSY', effect='none', retryable=True)

        with local_operation_scope(owner):
            task = asyncio.create_task(DeclaredTool()._poll_invocation(
                original, actor.tenant_id, None, durable=True))
        try:
            retry = await asyncio.to_thread(wait_for,
                lambda: devices.claim_next(device_id, actor.tenant_id, 'retry-claim', 60,
                    provider_keys=[DeclaredTool.provider_key]))
            assert retry and str(retry['id']) != original_id
            retry = devices.get_invocation(str(retry['id']), actor.tenant_id)
            assert retry['provider_key'] == DeclaredTool.provider_key
            assert retry['dedupe_key'] != original['dedupe_key']
            assert retry['business_ref']['branch'] == 'auto.retry.' + original['dedupe_key']
            devices.write_runtime_result(str(retry['id']), actor.tenant_id, 'retry-claim', True, effect='none')
            assert (await asyncio.wait_for(task, 10))['success'] is True
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    finally:
        for table in ('local_tool_events', 'local_tool_invocations', 'local_tool_devices'):
            service_database.rows(f'DELETE FROM {table} WHERE tenant_id=%s', (actor.tenant_id,))


_PAUSE_AFTER_BIND_COMMIT = '''
import runpy
import sys
import threading
from pathlib import Path
from src.services.agent_runner.local_invocations import LocalInvocationRepository

path = Path(sys.argv[1])
pause_on = sys.argv[2]
bind = LocalInvocationRepository._bind

def pause_after_commit(self, attempt, revision, phase, **options):
    row = bind(self, attempt, revision, phase, **options)
    original = options.get('automatic_provider') is not None
    retry = options.get('recovery_key') is not None and phase.ordinal == 1
    if (pause_on == 'original' and original) or (pause_on == 'retry' and retry):
        path.touch()
        threading.Event().wait()
    return row

LocalInvocationRepository._bind = pause_after_commit
sys.argv = ['worker', *sys.argv[3:]]
runpy.run_module('src.services.agent_runner.worker', run_name='__main__')
'''


@pytest.mark.parametrize('pause_on', ['original', 'retry'])
def test_worker_restarts_at_committed_bind_without_resetting_recovery(
        workers, ordinary_local, service_database, pause_on):
    _enable(workers)
    actor, profile, folder, _ = ordinary_local
    device = json.loads((folder/'device-ready.json').read_text())['device_id']
    identifier = _accept_local(workers, actor, profile)
    marker = folder / 'after-bind-commit'
    worker_id = 'worker-test-' + uuid.uuid4().hex
    first = workers.processes.start(
        ['-c', _PAUSE_AFTER_BIND_COMMIT, str(marker), pause_on, '--worker-id', worker_id, '--once'],
        environment=workers.environment, private_working_directory=True)
    workers.children.append((first, worker_id))
    attempted = []
    if pause_on == 'retry':
        attempted.append(_deliver(workers, service_database, identifier, actor, device, code='BUSY', retryable=True))
    wait_for(marker.exists, timeout=20)
    saved = runner(service_database, identifier)
    key, episode = next(iter(saved['checkpoint'][CHECKPOINT_KEY].items()))
    physical = service_database.rows('SELECT id,dedupe_key FROM local_tool_invocations WHERE tenant_id=%s ORDER BY created_at',
                                     (actor.tenant_id,))
    assert len(physical) == (1 if pause_on == 'original' else 2)
    assert key == physical[0]['dedupe_key']
    assert episode['invocation_id'] == str(physical[-1]['id'])
    assert (episode['deadline'] is None) == (pause_on == 'original')
    parked = interrupted(workers, service_database, {'runner_id': identifier}, first, hard=True)
    assert parked['checkpoint'][CHECKPOINT_KEY][key]['invocation_id'] == episode['invocation_id']
    if pause_on == 'original':
        attempted.append(_deliver(workers, service_database, identifier, actor, device, effect='applied'))
    else:
        attempted.append(_deliver(workers, service_database, identifier, actor, device, code='BUSY', retryable=True))
    replacement, _ = workers.start()
    if pause_on == 'retry':
        attempted.append(_deliver(workers, service_database, identifier, actor, device, code='BUSY', retryable=True))
    finished = terminal(service_database, identifier)
    workers.assert_clean_exit(replacement)
    if pause_on == 'original':
        assert finished['status'] == 'completed'
        assert finished['result']['output'] == '本机步骤已处理'
    else:
        assert finished['status'] == 'failed'
        assert finished['result']['error_code'] == 'LOCAL_RECOVERY_EXHAUSTED'
        assert finished['checkpoint'][CHECKPOINT_KEY][key]['deadline'] == episode['deadline']
    assert [str(item['id']) for item in service_database.rows(
        'SELECT id FROM local_tool_invocations WHERE tenant_id=%s ORDER BY created_at',
        (actor.tenant_id,))] == attempted
    assert not service_database.rows('SELECT 1 FROM agent_runner_session_claims WHERE owner_runner_id=%s',
                                     (identifier,))


@pytest.mark.parametrize('receipt_before_scan', [False, True])
def test_worker_restart_after_recovery_cutoff_keeps_started_retry(
        workers, ordinary_local, service_database, receipt_before_scan):
    _enable(workers)
    actor, profile, folder, _ = ordinary_local
    device = json.loads((folder/'device-ready.json').read_text())['device_id']
    identifier = _accept_local(workers, actor, profile)
    marker = folder / 'after-retry-bind'
    worker_id = 'worker-test-' + uuid.uuid4().hex
    first = workers.processes.start(
        ['-c', _PAUSE_AFTER_BIND_COMMIT, str(marker), 'retry', '--worker-id', worker_id, '--once'],
        environment=workers.environment, private_working_directory=True)
    workers.children.append((first, worker_id))
    original = _deliver(workers, service_database, identifier, actor, device, code='BUSY', retryable=True)
    wait_for(marker.exists, timeout=20)
    episode_key, episode = next(iter(runner(service_database, identifier)['checkpoint'][CHECKPOINT_KEY].items()))
    retry = episode['invocation_id']
    token = uuid.uuid4().hex
    assert devices.claim_next(device, actor.tenant_id, token, 60, expected_invocation_id=retry)
    assert devices.mark_started(retry, actor.tenant_id, token)['state'] == 'running'
    devices.append_event(retry, actor.tenant_id, token, 'fixture-progress', 1, 2, 'Fixture retry running', 60)

    # Advance the persisted recovery cutoff, leaving the Runtime lease and
    # real receipt path untouched.
    past = (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat()
    service_database.rows("UPDATE agent_runners SET checkpoint=jsonb_set(checkpoint,%s,to_jsonb(%s::text)) WHERE runner_id=%s",
        ([CHECKPOINT_KEY, episode_key, 'deadline'], past, identifier))
    if receipt_before_scan:
        assert devices.write_runtime_result(retry, actor.tenant_id, token, True,
                                           effect='applied', device_id=device)['state'] == 'succeeded'
        workers.processes.stop(first, force=True)
        assert first.returncode == -9
        service_database.rows("UPDATE agent_runners SET lease_until=clock_timestamp()-INTERVAL '1 second' WHERE runner_id=%s",
                              (identifier,))
    else:
        parked = interrupted(workers, service_database, {'runner_id': identifier}, first, hard=True)
        assert parked['status'] == 'interrupted'
        current = devices.get_invocation(retry, actor.tenant_id)
        assert current['state'] == 'running'
        assert not _notifications(service_database, identifier)
        assert devices.write_runtime_result(retry, actor.tenant_id, token, True,
                                           effect='applied', device_id=device)['state'] == 'succeeded'

    replacement, _ = workers.start()
    finished = terminal(service_database, identifier)
    workers.assert_clean_exit(replacement)
    assert finished['status'] == 'completed'
    assert finished['result']['output'] == '本机步骤已处理'
    assert finished['checkpoint'][CHECKPOINT_KEY][episode_key]['deadline'] == past
    assert not [item for item in _notifications(service_database, identifier) if item['event_type'] == 'task.failed']
    assert [str(item['id']) for item in service_database.rows(
        'SELECT id FROM local_tool_invocations WHERE tenant_id=%s ORDER BY created_at',
        (actor.tenant_id,))] == [original, retry]


def test_automatic_worker_orchestrates_verified_stop_without_replaying_original(
        workers, ordinary_local, service_database):
    _enable(workers)
    actor, profile, folder, _ = ordinary_local
    device = {'id': json.loads((folder/'device-ready.json').read_text())['device_id']}
    enable(service_database, device, stop=True)
    identifier = _accept_local(workers, actor, profile)
    child, _ = workers.start()
    original = _deliver(workers, service_database, identifier, actor, device['id'],
                        code='EXECUTION_UNKNOWN', effect='unknown', retryable=True)
    waiting = wait_for(lambda: (row if (row := runner(service_database, identifier))['status'] == 'waiting' else None))
    workers.assert_clean_exit(child)
    assert not _notifications(service_database, identifier)

    reconciler, _ = workers.start()
    workers.assert_clean_exit(reconciler)
    verification = runner(service_database, identifier)['checkpoint']['local_verification']
    assert verification['original_invocation_ids'] == [original]
    assert verification['automatic_key']
    probe = verification['invocation_id']
    token, arguments = claim_probe(waiting, device, probe)
    receipt = devices.write_runtime_result(probe, actor.tenant_id, token, True, effect='none',
        data=stopped_proof(arguments), device_id=device['id'])
    assert receipt['state'] == 'succeeded'
    row = runner(service_database, identifier)
    assert row['checkpoint']['verified_local_invocations'] == [original]
    assert row['resume_control_id']

    resumed, _ = workers.start()
    finished = terminal(service_database, identifier)
    workers.assert_clean_exit(resumed)
    assert finished['status'] == 'failed'
    assert finished['result']['error_code'] == 'LOCAL_EFFECT_VERIFICATION_REQUIRED'
    assert len(_notifications(service_database, identifier)) == 1
    rows = service_database.rows('SELECT id,effect FROM local_tool_invocations WHERE tenant_id=%s',
                                 (actor.tenant_id,))
    assert {str(item['id']): item['effect'] for item in rows} == {original: 'unknown', probe: 'none'}

    # A verified stop releases the original device as well as the chat session.
    followup = _accept_local(workers, actor, profile)
    next_worker, _ = workers.start()
    new_invocation = _deliver(workers, service_database, followup, actor, device['id'], effect='applied')
    assert terminal(service_database, followup)['status'] == 'completed'
    workers.assert_clean_exit(next_worker)
    assert new_invocation not in {original, probe}
