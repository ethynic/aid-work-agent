"""Real admission/receipt transactions: proof releases resources, never business unknowns."""
import uuid
import copy
import json
from dataclasses import replace
from datetime import datetime
import pytest
from .test_storage import storage
from .test_api import api_pair
from .test_worker import workers, prices, terminal
from .test_local_wait_recovery import lost_local
from src.local_tools import repository as devices
from src.services.agent_runner.local_verification import verify_local
from src.services.agent_runner.local_wait import end_waiting
from src.services.agent_runner.contracts import RunnerError
from src.services.agent_runner.local_recovery import LocalRecoveryProof
from src.core.agent_engine.contracts import ExecutionState
from src.services.agent_runner.local_invocations import LocalPhase

pytestmark = pytest.mark.integration


def enable(database, device, *, stop=False):
    database.rows('UPDATE local_tool_devices SET capabilities_json=%s::jsonb WHERE id=%s',
        (json.dumps({'providers':['boss-recruiting','runtime-recovery'], 'capabilities':['runtime_recovery_observe_v2'] + (['runtime_recovery_stop_v1'] if stop else [])}), device['id']))


def claim_probe(row, device, identifier):
    claim = uuid.uuid4().hex
    queued = devices.claim_next(str(device['id']), row['tenant_id'], claim, 60,
        expected_invocation_id=identifier, provider_keys=['runtime-recovery'])
    assert queued and queued['tool_name'] == 'runtime_recovery_observe'
    assert devices.mark_started(identifier, row['tenant_id'], claim)['state'] == 'running'
    return claim, queued['arguments_json']


def proof(args, **changes):
    value = dict(version=2, scope_id=args['scope_id'], device_id=args['device_id'],
        original_invocation_ids=args['original_invocation_ids'],
        resource_stopped=False, stop_error_code='LOCAL_RECOVERY_STOP_UNPROVED', target_relation='current_unbound',
        browser_target_ref=str(uuid.uuid4()), page_summary=dict(observed=True, page_kind='other', result='unknown'))
    value.update(changes)
    return dict(recovery_observation=value)


def test_observation_succeeds_immediately_without_stop_and_late_completion_alone_releases_hold(lost_local, service_database):
    repository, _, principal, row, device, original, original_claim = lost_local
    enable(service_database, device)
    accepted = verify_local(repository, principal, row['runner_id'], 'first-observation')
    first = accepted['checkpoint']['local_verification']['invocation_id']
    assert verify_local(repository, principal, row['runner_id'], 'first-observation')['checkpoint']['local_verification']['invocation_id'] == first
    token, args = claim_probe(row, device, first)
    end_waiting(repository, principal, row['runner_id'])
    result = devices.write_runtime_result(first, row['tenant_id'], token, True, effect='none', data=proof(args), device_id=str(device['id']))
    assert result['state'] == 'succeeded' and result['credit_cost'] == 0
    assert devices.write_runtime_result(first, row['tenant_id'], token, True, effect='none', data=proof(args), device_id=str(device['id']))['result_json'] == result['result_json']
    current = repository.get(row['runner_id'])
    observed = current['checkpoint']['local_verification']
    assert observed['page_observed'] is True and observed['resource_stopped'] is False
    assert observed['stop_error_code'] == 'LOCAL_RECOVERY_STOP_UNPROVED'
    assert not current['checkpoint'].get('verified_local_invocations')
    raw = devices.get_invocation(original, row['tenant_id'])
    assert raw['state'] == raw['effect'] == 'unknown' and raw['result_json'] is None
    state = ExecutionState.restore(current['checkpoint']['execution'])
    assert not LocalRecoveryProof().can_restore(current, state, state.tools['original-call'])
    with pytest.raises(devices.DeviceOperationUnresolved):
        devices.create_invocation(row['tenant_id'], row['user_id'], str(device['id']), 'boss_goto', {'target':'recommend'})
    devices.write_result(original, row['tenant_id'], original_claim, True, effect='none', device_id=str(device['id']))
    assert devices.create_invocation(row['tenant_id'], row['user_id'], str(device['id']), 'boss_goto', {'target':'recommend'})
    from src.services.agent_runner.public_view import public_runner
    public = public_runner(repository.get(row['runner_id']))
    assert not public.get('local_verification_available'), 'late completion must remove the stale verification entry without deleting audit IDs'
    assert repository.get(row['runner_id'])['checkpoint']['detached_local_invocations'] == [original]


@pytest.mark.parametrize('malformed', [
    {'original_invocation_ids': []}, {'scope_id': str(uuid.uuid4())}, {'device_id': str(uuid.uuid4())},
    {'target_relation': 'original'}, {'resource_stopped': True}, {'page_summary': {'observed': True, 'private_text': 'forbidden'}},
    {'stop_error_code': 'idle_means_stopped'},
])
def test_receipt_cannot_release_another_scope_or_invent_original_target(lost_local, service_database, malformed):
    repository, _, principal, row, device, original, _ = lost_local
    enable(service_database, device)
    accepted = verify_local(repository, principal, row['runner_id'], 'bad-proof')
    identifier = accepted['checkpoint']['local_verification']['invocation_id']
    token, args = claim_probe(row, device, identifier)
    end_waiting(repository, principal, row['runner_id'])
    assert devices.write_runtime_result(identifier, row['tenant_id'], 'wrong-claim', True, effect='none', data=proof(args), device_id=str(device['id'])) is None
    assert devices.write_runtime_result(identifier, row['tenant_id'], token, True, effect='none', data=proof(args, **malformed), device_id=str(device['id']))['state'] == 'failed'
    with pytest.raises(devices.DeviceOperationUnresolved):
        devices.create_invocation(row['tenant_id'], row['user_id'], str(device['id']), 'boss_goto', {'target':'recommend'})
    assert devices.get_invocation(original, row['tenant_id'])['state'] == 'unknown'


def test_older_runtime_has_actionable_upgrade_error_and_no_probe(lost_local):
    repository, _, principal, row, _, _, _ = lost_local
    with pytest.raises(RunnerError) as error:
        verify_local(repository, principal, row['runner_id'], 'unsupported')
    assert error.value.code == 'LOCAL_VERIFICATION_RUNTIME_UPGRADE_REQUIRED'
    assert 'local_verification' not in repository.get(row['runner_id'])['checkpoint']


@pytest.mark.parametrize('state', ['queued', 'running', 'cancel_requested'])
def test_readonly_verification_never_cancels_an_active_original(lost_local, service_database, state):
    repository, _, principal, row, device, original, _ = lost_local
    enable(service_database, device)
    service_database.rows('UPDATE local_tool_invocations SET state=%s WHERE id=%s', (state, original))
    with pytest.raises(RunnerError) as error:
        verify_local(repository, principal, row['runner_id'], 'read-only-not-cancel')
    assert error.value.code == 'LOCAL_VERIFICATION_ORIGINAL_ACTIVE'
    assert devices.get_invocation(original, row['tenant_id'])['state'] == state
    assert not repository.get(row['runner_id'])['cancel_requested']


def add_unknown(database, row, device):
    checkpoint = copy.deepcopy(row['checkpoint'])
    root = checkpoint['execution']
    ref = copy.deepcopy(next(iter(root['resources']['local_invocations'].values())))
    phase = LocalPhase(row['runner_id'], 'second-call', 'main', 0)
    reference = dict(runner_id=row['runner_id'], execution_id=row['runner_id'], tool_call_id=phase.tool_call_id, branch='main', ordinal=0)
    request = ref['request']
    identifier = devices.create_invocation(row['tenant_id'], row['user_id'], str(device['id']), request['tool_name'], request['arguments'], row['session_id'],
        provider_key=request['provider_key'], business_kind='agent_runner', business_ref=reference,
        dedupe_key=phase.key(row['runner_id']), deadline_at=datetime.fromisoformat(request['deadline_at']))
    claim = uuid.uuid4().hex
    devices.claim_next(str(device['id']), row['tenant_id'], claim, 60, expected_invocation_id=identifier)
    devices.write_result(identifier, row['tenant_id'], claim, False, code='EXECUTION_UNKNOWN', effect='unknown', device_id=str(device['id']))
    ref.update(reference, invocation_id=identifier)
    root['resources']['local_invocations'][phase.key(row['runner_id'])] = ref
    root['tools'][phase.tool_call_id] = copy.deepcopy(root['tools']['original-call'])
    root['tools'][phase.tool_call_id]['call']['id'] = phase.tool_call_id
    database.rows('UPDATE agent_runners SET checkpoint=%s::jsonb WHERE runner_id=%s', (json.dumps(checkpoint), row['runner_id']))
    return identifier


def test_old_request_replay_cannot_rebind_current_smaller_scope_or_consume_its_late_proof(lost_local, service_database):
    repository, _, principal, row, device, original, original_claim = lost_local
    sibling = add_unknown(service_database, row, device)
    enable(service_database, device)
    first = verify_local(repository, principal, row['runner_id'], 'R1')['checkpoint']['local_verification']['invocation_id']
    token, first_args = claim_probe(row, device, first)
    service_database.rows("UPDATE local_tool_invocations SET state='unknown',result_json=NULL WHERE id=%s", (first,))
    devices.write_result(original, row['tenant_id'], original_claim, True, effect='none', device_id=str(device['id']))
    second = verify_local(repository, principal, row['runner_id'], 'R2')['checkpoint']['local_verification']
    assert second['original_invocation_ids'] == [sibling]
    assert verify_local(repository, principal, row['runner_id'], 'R1')['checkpoint']['local_verification'] == second
    devices.write_runtime_result(first, row['tenant_id'], token, True, effect='none', data=proof(first_args), device_id=str(device['id']))
    current = repository.get(row['runner_id'])['checkpoint']
    assert current['local_verification'] == second and not current.get('verified_local_invocations')


def test_stale_original_page_keeps_page_and_resource_unknown_without_retargeting(lost_local, service_database):
    repository, _, principal, row, device, original, claim = lost_local
    target = str(uuid.uuid4())
    devices.write_result(original, row['tenant_id'], claim, False, code='EXECUTION_UNKNOWN', effect='unknown', data={'browser_target_ref':target}, device_id=str(device['id']))
    enable(service_database, device)
    accepted = verify_local(repository, principal, row['runner_id'], 'known-original-target')
    identifier = accepted['checkpoint']['local_verification']['invocation_id']
    token, args = claim_probe(row, device, identifier)
    assert args['browser_target_ref'] == target, 'trusted raw/late receipts retain a known original target'
    end_waiting(repository, principal, row['runner_id'])
    result = devices.write_runtime_result(identifier, row['tenant_id'], token, False, code='WRONG_PAGE', effect='none',
        data=proof(args, target_relation='original', browser_target_ref=target,
            page_summary=dict(observed=False, page_kind='unavailable', result='unknown'), page_error_code='WRONG_PAGE'), device_id=str(device['id']))
    assert result['state'] == 'failed'
    public = __import__('src.services.agent_runner.public_view', fromlist=['public_runner']).public_runner(repository.get(row['runner_id']))
    assert public['local_verification']['resource_stopped'] is False
    assert public['local_verification']['page_observed'] is False
    assert devices.get_invocation(original, row['tenant_id'])['state'] == 'unknown'
    with pytest.raises(devices.DeviceOperationUnresolved):
        devices.create_invocation(row['tenant_id'], row['user_id'], str(device['id']), 'boss_goto', {'target':'recommend'})


def test_pure_probe_can_cross_two_same_machine_holds_without_releasing_either_unknown(lost_local, storage, actors, service_database):
    repository, _, principal, row, device, original, _ = lost_local
    actor = replace(actors['a'], session_id='independent-session-' + uuid.uuid4().hex)
    generator = globals()['lost_local'].__wrapped__(storage, {'a':actor}, service_database)
    second_repository, _, second_principal, second, second_device, second_original, _ = next(generator)
    try:
        fingerprint = service_database.rows('SELECT machine_fingerprint_hash FROM local_tool_devices WHERE id=%s', (device['id'],))[0]['machine_fingerprint_hash']
        service_database.rows('UPDATE local_tool_devices SET machine_fingerprint_hash=%s WHERE id=%s', (fingerprint, second_device['id']))
        enable(service_database, device)
        enable(service_database, second_device)
        first_probe = verify_local(repository, principal, row['runner_id'], 'first-machine-scope')['checkpoint']['local_verification']['invocation_id']
        second_probe = verify_local(second_repository, second_principal, second['runner_id'], 'second-machine-scope')['checkpoint']['local_verification']['invocation_id']
        end_waiting(repository, principal, row['runner_id'])
        end_waiting(second_repository, second_principal, second['runner_id'])
        token, args = claim_probe(row, device, first_probe)
        devices.write_runtime_result(first_probe, row['tenant_id'], token, True, effect='none', data=proof(args), device_id=str(device['id']))
        assert not repository.get(row['runner_id'])['checkpoint'].get('verified_local_invocations')
        assert not second_repository.get(second['runner_id'])['checkpoint'].get('verified_local_invocations')
        with pytest.raises(devices.DeviceOperationUnresolved):
            devices.create_invocation(row['tenant_id'], row['user_id'], str(device['id']), 'boss_goto', {'target':'recommend'})
        token, args = claim_probe(second, second_device, second_probe)
        devices.write_runtime_result(second_probe, row['tenant_id'], token, True, effect='none', data=proof(args), device_id=str(second_device['id']))
        assert not second_repository.get(second['runner_id'])['checkpoint'].get('verified_local_invocations')
        with pytest.raises(devices.DeviceOperationUnresolved):
            devices.create_invocation(row['tenant_id'], row['user_id'], str(device['id']), 'boss_goto', {'target':'recommend'})
    finally:
        generator.close()


def test_conflicting_trusted_original_target_facts_are_not_replaced_by_current_page(lost_local, service_database):
    repository, _, principal, row, device, original, _ = lost_local
    enable(service_database, device)
    receipt = dict(data={'browser_target_ref':str(uuid.uuid4())}, _late_result={'data':{'browser_target_ref':str(uuid.uuid4())}})
    service_database.rows('UPDATE local_tool_invocations SET result_json=%s::jsonb WHERE id=%s', (json.dumps(receipt), original))
    with pytest.raises(RunnerError) as error:
        verify_local(repository, principal, row['runner_id'], 'conflicting-targets')
    assert error.value.code == 'LOCAL_VERIFICATION_TARGET_SCOPE_REQUIRED'
    assert not repository.get(row['runner_id'])['checkpoint'].get('local_verification')


def test_disabled_runner_service_never_creates_host_observation():
    from types import SimpleNamespace
    from unittest.mock import Mock
    from fastapi.testclient import TestClient
    from src.services.agent_runner.api import create_app
    manager = Mock()
    with TestClient(create_app(config=SimpleNamespace(enabled=False), manager=manager)) as client:
        response = client.post('/v1/runners/original/verify-local', json={'client_request_id':'disabled'})
    assert response.status_code == 503
    manager.verify_local.assert_not_called()


@pytest.mark.parametrize('success,code,effect', [(True, None, 'none'), (False, 'WRONG_PAGE', 'none')])
def test_late_completion_racing_verify_is_a_readonly_refresh_not_a_conflict(lost_local, service_database, workers, success, code, effect):
    from src.services.agent_runner.public_view import public_runner
    repository, _, principal, row, device, original, claim = lost_local
    enable(service_database, device)
    end_waiting(repository, principal, row['runner_id'])
    # The browser already ended its observer only after real cancel-only Worker
    # and Finalizer committed terminal + settled; detachment alone is not enough.
    child, _ = workers.start()
    finished = terminal(service_database, row['runner_id'])
    workers.assert_clean_exit(child)
    assert finished['status'] == 'cancelled' and finished['settlement_status'] == 'settled'
    assert public_runner(repository.get(row['runner_id']))['local_verification_available']
    devices.write_result(original, row['tenant_id'], claim, success, code=code, effect=effect, device_id=str(device['id']))
    refreshed = verify_local(repository, principal, row['runner_id'], 'button-rendered-before-late-receipt')
    assert not public_runner(refreshed).get('local_verification_available')
    assert not refreshed['checkpoint'].get('local_verification'), 'no Host observation was dispatched'
    assert refreshed['checkpoint']['detached_local_invocations'] == [original]
    assert devices.get_invocation(original, row['tenant_id'])['state'] == 'unknown'


def stopped_proof(args, **changes):
    return proof(args, version=3, resource_stopped=True, stop_evidence='supervised_device_boundary',
        resource_scope='managed_provider_pool', runtime_generation=str(uuid.uuid4()), **changes)


def test_explicit_device_boundary_stop_unlocks_old_result_lost_invocation_without_replaying(lost_local, service_database, workers):
    repository, _, principal, row, device, original, _ = lost_local
    enable(service_database, device, stop=True)
    raw = devices.get_invocation(original, row['tenant_id'])
    assert raw['state'] == 'unknown' and raw['result_json'] is None
    assert not (raw.get('arguments_json') or {}).get('runtime_generation')
    end_waiting(repository, principal, row['runner_id'])
    child, _ = workers.start()
    finished = terminal(service_database, row['runner_id'])
    workers.assert_clean_exit(child)
    assert finished['status'] == 'cancelled' and finished['settlement_status'] == 'settled'
    accepted = verify_local(repository, principal, row['runner_id'], 'stop-old-device', stop_resources=True)
    probe = accepted['checkpoint']['local_verification']['invocation_id']
    token, args = claim_probe(row, device, probe)
    assert args['version'] == 3 and args['stop_resources'] is True
    with pytest.raises(devices.DeviceOperationUnresolved):
        devices.create_invocation(row['tenant_id'], row['user_id'], str(device['id']), 'boss_goto', {'target':'recommend'})
    completed = devices.write_runtime_result(probe, row['tenant_id'], token, True, effect='none', data=stopped_proof(args), device_id=str(device['id']))
    assert completed['credit_cost'] == 0 and completed['state'] == 'succeeded'
    current = repository.get(row['runner_id'])
    assert current['checkpoint']['verified_local_invocations'] == [original]
    assert devices.get_invocation(original, row['tenant_id'])['result_json'] is None
    assert devices.get_invocation(original, row['tenant_id'])['state'] == 'unknown'
    new = devices.create_invocation(row['tenant_id'], row['user_id'], str(device['id']), 'boss_goto', {'target':'recommend'})
    assert devices.claim_next(str(device['id']), row['tenant_id'], 'new-explicit-intent', 60, expected_invocation_id=new)


def test_pending_stop_fences_new_business_and_actual_other_call_is_busy(lost_local, service_database):
    repository, _, principal, row, device, _, _ = lost_local
    enable(service_database, device, stop=True)
    other = devices.create_invocation(row['tenant_id'], row['user_id'], str(device['id']), 'boss_goto', {'target':'chat'})
    devices.claim_next(str(device['id']), row['tenant_id'], 'other-active', 60, expected_invocation_id=other)
    with pytest.raises(RunnerError) as error:
        verify_local(repository, principal, row['runner_id'], 'must-not-kill-active-other', stop_resources=True)
    assert error.value.code == 'LOCAL_RECOVERY_BUSY'
    devices.write_result(other, row['tenant_id'], 'other-active', True, effect='none', device_id=str(device['id']))
    accepted = verify_local(repository, principal, row['runner_id'], 'idle-stop', stop_resources=True)
    with pytest.raises(devices.DeviceOperationUnresolved):
        devices.create_invocation(row['tenant_id'], row['user_id'], str(device['id']), 'boss_goto', {'target':'recommend'})
    # A version2 read-only probe stays independently available.
    assert accepted['checkpoint']['local_verification']['version'] == 3


def test_stopped_resources_survive_page_failure_and_only_exact_scope_is_released(lost_local, service_database):
    repository, _, principal, row, device, original, _ = lost_local
    enable(service_database, device, stop=True)
    accepted = verify_local(repository, principal, row['runner_id'], 'stop-page-missing', stop_resources=True)
    identifier = accepted['checkpoint']['local_verification']['invocation_id']
    token, args = claim_probe(row, device, identifier)
    value = stopped_proof(args, browser_target_ref=None, page_summary={'observed':False,'page_kind':'unavailable','result':'unknown'}, page_error_code='CHROME_UNAVAILABLE')
    result = devices.write_runtime_result(identifier, row['tenant_id'], token, False, code='CHROME_UNAVAILABLE', effect='none', data=value, device_id=str(device['id']))
    assert result['state'] == 'failed'
    verification = repository.get(row['runner_id'])['checkpoint']['local_verification']
    assert verification['resource_stopped'] is True and verification['page_observed'] is False
    assert repository.get(row['runner_id'])['checkpoint']['verified_local_invocations'] == [original]


def test_pure_request_key_cannot_be_reused_to_elevate_stop_mode(lost_local, service_database):
    repository, _, principal, row, device, _, _ = lost_local
    enable(service_database, device, stop=True)
    verify_local(repository, principal, row['runner_id'], 'same-key')
    with pytest.raises(RunnerError) as error:
        verify_local(repository, principal, row['runner_id'], 'same-key', stop_resources=True)
    assert error.value.code == 'LOCAL_VERIFICATION_REQUEST_MISMATCH'


def test_expired_queued_stop_cannot_starve_or_later_execute_after_replacement(lost_local, service_database):
    repository, _, principal, row, device, _, _ = lost_local
    enable(service_database, device, stop=True)
    old = verify_local(repository, principal, row['runner_id'], 'expired-A', stop_resources=True)['checkpoint']['local_verification']['invocation_id']
    service_database.rows("UPDATE local_tool_invocations SET deadline_at=NOW()-INTERVAL '1 second' WHERE id=%s", (old,))
    new = verify_local(repository, principal, row['runner_id'], 'replacement-B', stop_resources=True)['checkpoint']['local_verification']['invocation_id']
    token = 'replacement-claim'
    claimed = devices.claim_next(str(device['id']), row['tenant_id'], token, 60, provider_keys=['runtime-recovery'])
    assert str(claimed['id']) == new, 'expired queue head cannot monopolize recovery selection'
    assert devices.mark_started(new, row['tenant_id'], token)['state'] == 'running'
    assert devices.claim_next(str(device['id']), row['tenant_id'], 'stale-claim', 60, expected_invocation_id=old) is None
    # A stale client retaining an old started token also cannot start physical stop.
    service_database.rows("UPDATE local_tool_invocations SET state='claimed',claim_token_hash='stale-token' WHERE id=%s", (old,))
    assert devices.mark_started(old, row['tenant_id'], 'stale-token') is None


def test_stop_does_not_require_a_page_target_when_original_target_evidence_conflicts(lost_local, service_database):
    repository, _, principal, row, device, original, _ = lost_local
    enable(service_database, device, stop=True)
    service_database.rows('UPDATE local_tool_invocations SET result_json=%s::jsonb WHERE id=%s',
        (json.dumps({'data':{'browser_target_ref':str(uuid.uuid4())}, '_late_result':{'data':{'browser_target_ref':str(uuid.uuid4())}}}), original))
    accepted = verify_local(repository, principal, row['runner_id'], 'stop-without-fake-retarget', stop_resources=True)
    token, args = claim_probe(row, device, accepted['checkpoint']['local_verification']['invocation_id'])
    assert args['page_target_unavailable'] is True and args['browser_target_ref'] is None
    result = devices.write_runtime_result(accepted['checkpoint']['local_verification']['invocation_id'], row['tenant_id'], token, False,
        code='WRONG_PAGE', effect='none', data=stopped_proof(args, target_relation='unavailable', browser_target_ref=None,
            page_summary={'observed':False,'page_kind':'unavailable','result':'unknown'}, page_error_code='WRONG_PAGE'), device_id=str(device['id']))
    assert result['state'] == 'failed'
    assert repository.get(row['runner_id'])['checkpoint']['local_verification']['resource_stopped'] is True
    assert repository.get(row['runner_id'])['checkpoint']['verified_local_invocations'] == [original]
