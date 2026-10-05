"""Prepared resident owner timing cases; execute only on the final freeze.

Only original park scheduling or external model response is gated. All owner,
sampler, continuation/control, Browser, database and fee operations remain real.
"""
from contextlib import contextmanager
from decimal import Decimal, ROUND_CEILING
import json
import threading
import uuid

import pytest

from .browser_io import browser_page, browser_redis, owned_descendants, process_is_live
from .browser_diagnostics import browser_diagnostics, _codes
from .browser_observation_fixture import observation_configuration
from .conftest import wait_for
from .provider import Reply, tool_reply
from .test_api import require_status
from .test_browser_owner_boundaries import delete_browser_facts
from .test_worker import workers, api_pair, prices, accept, runner, decoded

pytestmark = [pytest.mark.integration, pytest.mark.real_browser]


@contextmanager
def resident(workers, actor, database, page, redis_environment, configuration, *, mode, decisions, maximum):
    workers.environment.update(redis_environment)
    workers.environment.update(configuration['worker'])
    first = tool_reply('browser_automation', {}, call_id='race-original-browser-call')
    marker = workers.provider.register(first, *decisions, Reply(content='Original race continuation complete'))
    first.tool_calls[0]['function']['arguments'] = json.dumps(
        {'task': marker + ' original local page', 'url': page.url, 'headless': True})
    accepted = accept(workers.api, actor, marker)
    root = workers.processes.root
    suffix = uuid.uuid4().hex
    command, report, entered, release = [root / (prefix + suffix + '.json') for prefix in
        ('continuation-command-', 'continuation-report-', 'continuation-entered-', 'continuation-release-')]
    worker_id = 'continuation-race-' + suffix
    process = workers.processes.start(['-m', 'tests.integration.agent_runner_service.browser_continuation_scenarios_probe',
        str(command), str(report), str(entered), str(release), mode,
        '--worker-id', worker_id, '--max-tasks', str(maximum)],
        environment=workers.environment, private_working_directory=True)
    workers.children.append((process, worker_id))
    value = dict(identifier=accepted['runner_id'], marker=marker, process=process,
                 command=command, report=report, entered=entered, release=release, sequence=0,
                 descendants=set())
    try:
        yield value
    except Exception:
        # Capture original finite facts while resources still exist. Never log
        # model content, page text, arguments, credentials or private bindings.
        diagnosis = browser_diagnostics(workers, database, value['identifier'], marker, page, process)
        diagnosis['controls'] = [{'action': row['action'], 'status': row['status'],
            'consumed_attempt': row['consumed_attempt'], 'codes': _codes(row['error_code'])}
            for row in database.rows('SELECT action,status,consumed_attempt,error_code FROM agent_runner_controls WHERE runner_id=%s',
                                     (value['identifier'],))]
        diagnosis['completion_phases'] = [((decoded(row['completion_fact']) or {}).get('continuation') or {}).get('phase')
            for row in database.rows('SELECT completion_fact FROM bs_browser_assistance_requests WHERE runner_id=%s',
                                     (value['identifier'],))]
        if report.exists():
            diagnosis['original_method_observations'] = json.loads(report.read_text())
        print('SAFE_BROWSER_CONTINUATION_RACE_DIAG=' + json.dumps(diagnosis))
        raise
    finally:
        release.touch()
        workers.processes.stop(process)
        delete_browser_facts(database, accepted['runner_id'])


def native_wait(database, value):
    value['descendants'].update(owned_descendants(value['process'].pid))
    rows = database.rows("SELECT assistance_id,runner_wait_id,run_id,completion_ref,completion_fact FROM bs_browser_assistance_requests WHERE runner_id=%s AND state='pending' ORDER BY id",
                         (value['identifier'],))
    return rows[-1] if rows else None


def complete_original(value, wait):
    value['sequence'] += 1
    request = {'sequence': value['sequence'], 'runner_id': value['identifier'],
               'run_id': wait['run_id'], 'assistance_id': wait['assistance_id']}
    temporary = value['command'].with_suffix('.pending')
    temporary.write_text(json.dumps(request))
    temporary.replace(value['command'])
    return wait_for(lambda: next((item for item in json.loads(value['report'].read_text())['completions']
        if item['sequence'] == value['sequence']), None) if value['report'].exists() else None, timeout=15)


def assert_finished(workers, database, page, actor, value, *, attempts, physical_calls, controls):
    wait_for(lambda: runner(database, value['identifier'])['status'] in {'completed', 'failed', 'cancelled'}, timeout=50)
    workers.assert_clean_exit(value['process'])
    row = runner(database, value['identifier'])
    assert row['status'] == 'completed' and row['attempt'] == attempts
    assert decoded(row['result'])['output'] == 'Original race continuation complete'
    assert database.rows('SELECT count(*) AS count FROM bs_browser_runs WHERE runner_id=%s',
        (value['identifier'],)) == [{'count': 1}]
    assert database.rows('SELECT runtime_state,owner_lease_until FROM bs_browser_runs WHERE runner_id=%s',
        (value['identifier'],)) == [{'runtime_state': 'closed', 'owner_lease_until': None}]
    assert database.rows('SELECT 1 FROM agent_runner_session_claims WHERE owner_runner_id=%s', (value['identifier'],)) == []
    assert page.requests.count(page.path) == 1 and not page.unexpected
    assert len(workers.provider.requests(value['marker'])) == physical_calls and not workers.provider.errors
    receipts = database.rows('SELECT phase,applied,usage,owner,provider,model,billing_boundary,price_snapshot FROM agent_runner_usage_receipts WHERE runner_id=%s',
                             (value['identifier'],))
    assert len(receipts) == physical_calls and all(item['phase'] == 'observed' and item['applied'] for item in receipts)
    assert sum(decoded(item['usage'])['prompt_tokens'] + decoded(item['usage'])['completion_tokens'] for item in receipts) == physical_calls * 18
    # Real frozen price metadata must match this provider's explicit 11 input/
    # 7 output fixture. 100x(11*1+7*2)/1e6 per call, ceil once to .01;
    # five actual calls cost .02 rather than the four-call fixture's .01.
    for item in receipts:
        snapshot = decoded(item['price_snapshot'])
        assert item['owner'] == 'llm' and item['provider'] == 'qwen' and item['model'] == workers.models[0]
        assert item['billing_boundary'] == 'main' and snapshot['usage_factor'] == 100
        assert Decimal(str(snapshot['price']['input_price_per_m'])) == 1
        assert Decimal(str(snapshot['price']['output_price_per_m'])) == 2
        assert not snapshot['price'].get('tiered_pricing')
    expected_credit = (Decimal(physical_calls) * Decimal(25) * Decimal(100) / Decimal(1_000_000)).quantize(
        Decimal('.01'), rounding=ROUND_CEILING)
    assert database.rows('SELECT total_token_count,credit_cost FROM chat_records WHERE session_id=%s',
        (actor.session_id,)) == [{'total_token_count': physical_calls * 18, 'credit_cost': expected_credit}]
    assert database.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s', (actor.tenant_id,)) == [
        {'credit_balance': Decimal(1000) - expected_credit}]
    actual = database.rows('SELECT action,status,consumed_attempt FROM agent_runner_controls WHERE runner_id=%s ORDER BY accepted_at',
                           (value['identifier'],))
    assert actual == controls
    assert not any(process_is_live(pid) for pid in value['descendants'])


def ask():
    return Reply(content=json.dumps({'action': 'ask_user', 'reason': 'Fictional original confirmation'}))


def done():
    return Reply(content=json.dumps({'action': 'done', 'reason': 'Fictional original page ready'}))


def request_control(workers, actor, value, action):
    return require_status(workers.api.call('POST', f'/v1/runners/{value["identifier"]}/controls', actor=actor,
        json={'client_request_id': uuid.uuid4().hex, 'action': action}), 202)['control']


def completion_fact(database, wait):
    return database.rows('SELECT completion_fact FROM bs_browser_assistance_requests WHERE assistance_id=%s',
                         (wait['assistance_id'],))[0]['completion_fact']


def test_pause_rejects_unconsumed_completion_bridge_until_later_explicit_resume_reparks(
        workers, actors, service_database, browser_page, browser_redis, observation_configuration):
    _, _, environment = browser_redis
    with resident(workers, actors['a'], service_database, browser_page, environment,
            observation_configuration, mode='pending', decisions=[ask(), done()], maximum=3) as value:
        wait_for(value['entered'].exists, timeout=40)
        wait = wait_for(lambda: native_wait(service_database, value))
        observed = complete_original(value, wait)
        assert observed['sampled'] and observed['ref_present'] and observed['bridged']
        original = completion_fact(service_database, wait)
        assert original['continuation']['phase'] == 'observed'
        paused = request_control(workers, actors['a'], value, 'pause')
        assert paused['status'] == 'consumed'
        rejected = service_database.rows('''SELECT status,error_code,consumed_attempt FROM agent_runner_controls
            WHERE control_id=%s''', (original['control_id'],))
        assert rejected == [{'status': 'rejected', 'error_code': 'RESUME_SUPERSEDED_BY_PAUSE', 'consumed_attempt': None}]
        assert runner(service_database, value['identifier'])['resume_control_id'] is None
        value['release'].touch()
        # Pause itself grants no continuation; only a later explicit resume is
        # allowed to consume, really park and bridge the same observed fact.
        assert runner(service_database, value['identifier'])['attempt'] == 1
        assert len(workers.provider.requests(value['marker'])) == 2
        assert json.loads(value['report'].read_text())['resume_calls'] == 0
        resumed = request_control(workers, actors['a'], value, 'resume')
        assert resumed['status'] == 'accepted'
        assert_finished(workers, service_database, browser_page, actors['a'], value,
            attempts=3, physical_calls=4, controls=[
                {'action': 'browser_complete', 'status': 'rejected', 'consumed_attempt': None},
                {'action': 'pause', 'status': 'consumed', 'consumed_attempt': None},
                {'action': 'resume', 'status': 'consumed', 'consumed_attempt': 2},
                {'action': 'browser_complete', 'status': 'consumed', 'consumed_attempt': 3}])
        final = completion_fact(service_database, wait)
        assert final['completion_ref'] == original['completion_ref']
        assert final['control_id'] != original['control_id']
        assert final['continuation']['phase'] == 'completed'
        report = json.loads(value['report'].read_text())
        assert report['completion_calls'] == report['resume_calls'] == report['last_confirmation_count'] == 1


def test_pause_after_real_control_consumption_before_start_resumes_original_observed_call_once(
        workers, actors, service_database, browser_page, browser_redis, observation_configuration):
    _, _, environment = browser_redis
    with resident(workers, actors['a'], service_database, browser_page, environment,
            observation_configuration, mode='consume_pause', decisions=[ask(), done()], maximum=3) as value:
        wait_for(lambda: runner(service_database, value['identifier'])['status'] == 'waiting', timeout=40)
        wait = native_wait(service_database, value)
        observed = complete_original(value, wait)
        assert observed['sampled'] and observed['ref_present']
        wait_for(value['entered'].exists, timeout=25)
        original = completion_fact(service_database, wait)
        row = runner(service_database, value['identifier'])
        assert row['status'] == 'running' and row['attempt'] == 2
        assert original['continuation']['phase'] == 'observed'
        assert service_database.rows('SELECT status,consumed_attempt FROM agent_runner_controls WHERE control_id=%s',
            (original['control_id'],)) == [{'status': 'consumed', 'consumed_attempt': 2}]
        assert len(workers.provider.requests(value['marker'])) == 2
        assert json.loads(value['report'].read_text())['resume_calls'] == 0
        paused = request_control(workers, actors['a'], value, 'pause')
        assert paused['status'] == 'accepted'
        value['release'].touch()
        wait_for(lambda: runner(service_database, value['identifier'])['status'] == 'paused', timeout=25)
        paused_root = decoded(runner(service_database, value['identifier'])['checkpoint'])['execution']
        assert not paused_root.get('waiting')
        assert paused_root['resources']['browser_completions']['race-original-browser-call'] == original['completion_ref']
        assert paused_root['resources']['browser_waits']['race-original-browser-call']['assistance_id'] == wait['assistance_id']
        assert paused_root['resources']['browser_waits']['race-original-browser-call']['wait_id'] == wait['runner_wait_id']
        assert completion_fact(service_database, wait)['continuation']['phase'] == 'observed'
        assert json.loads(value['report'].read_text())['resume_calls'] == 0
        assert len(workers.provider.requests(value['marker'])) == 2
        request_control(workers, actors['a'], value, 'resume')
        assert_finished(workers, service_database, browser_page, actors['a'], value,
            attempts=3, physical_calls=4, controls=[
                {'action': 'browser_complete', 'status': 'consumed', 'consumed_attempt': 2},
                {'action': 'pause', 'status': 'consumed', 'consumed_attempt': 2},
                {'action': 'resume', 'status': 'consumed', 'consumed_attempt': 3}])
        final = completion_fact(service_database, wait)
        assert final['completion_ref'] == original['completion_ref'] and final['control_id'] == original['control_id']
        assert final['continuation']['phase'] == 'completed' and final['continuation']['attempt'] == 3
        report = json.loads(value['report'].read_text())
        assert report['completion_calls'] == report['resume_calls'] == report['last_confirmation_count'] == 1


def test_real_early_completion_and_duplicate_fact_bridge_only_after_original_park(
        workers, actors, service_database, browser_page, browser_redis, observation_configuration):
    _, _, environment = browser_redis
    with resident(workers, actors['a'], service_database, browser_page, environment,
            observation_configuration, mode='early', decisions=[ask(), done()], maximum=2) as value:
        wait_for(value['entered'].exists, timeout=40)
        wait = wait_for(lambda: native_wait(service_database, value))
        before = runner(service_database, value['identifier'])
        assert before['status'] == 'running' and before['worker_id'] is not None
        first = complete_original(value, wait)
        assert first['ref_present'] and first['sampled'] and not first['bridged']
        saved = native_wait(service_database, value)
        assert saved['completion_ref'] and saved['completion_fact']['continuation']['phase'] == 'observed'
        assert service_database.rows('SELECT 1 FROM agent_runner_controls WHERE runner_id=%s', (value['identifier'],)) == []
        duplicate = complete_original(value, wait)
        assert duplicate['ref_present'] and duplicate['sampled'] and not duplicate['bridged']
        assert native_wait(service_database, value)['completion_ref'] == saved['completion_ref']
        assert runner(service_database, value['identifier'])['status'] == 'running'
        value['release'].touch()
        assert_finished(workers, service_database, browser_page, actors['a'], value,
            attempts=2, physical_calls=4,
            controls=[{'action': 'browser_complete', 'status': 'consumed', 'consumed_attempt': 2}])
        report = json.loads(value['report'].read_text())
        assert report['completion_calls'] == 2 and report['resume_calls'] == 1
        assert report['original_objects_preserved'] and report['last_confirmation_count'] == 1


def test_real_legitimate_pending_resume_preserves_completion_then_consumes_and_bridges_once(
        workers, actors, service_database, browser_page, browser_redis, observation_configuration):
    _, _, environment = browser_redis
    with resident(workers, actors['a'], service_database, browser_page, environment,
            observation_configuration, mode='pending', decisions=[ask(), done()], maximum=3) as value:
        wait_for(value['entered'].exists, timeout=40)
        wait = wait_for(lambda: native_wait(service_database, value))
        accepted = require_status(workers.api.call('POST', f'/v1/runners/{value["identifier"]}/controls', actor=actors['a'],
            json={'client_request_id': uuid.uuid4().hex, 'action': 'resume'}), 202)['control']
        assert accepted['status'] == 'accepted'
        completed = complete_original(value, wait)
        assert completed['ref_present'] and completed['sampled'] and not completed['bridged']
        assert value['process'].poll() is None
        assert native_wait(service_database, value)['completion_fact']['continuation']['phase'] == 'observed'
        assert runner(service_database, value['identifier'])['resume_control_id'] == accepted['control_id']
        value['release'].touch()
        assert_finished(workers, service_database, browser_page, actors['a'], value,
            attempts=3, physical_calls=4, controls=[
                {'action': 'resume', 'status': 'consumed', 'consumed_attempt': 2},
                {'action': 'browser_complete', 'status': 'consumed', 'consumed_attempt': 3}])
        report = json.loads(value['report'].read_text())
        assert report['resume_calls'] == 1 and report['last_confirmation_count'] == 1


def test_actual_second_human_wait_reuses_original_page_but_consumes_distinct_completion_once(
        workers, actors, service_database, browser_page, browser_redis, observation_configuration):
    _, _, environment = browser_redis
    with resident(workers, actors['a'], service_database, browser_page, environment,
            observation_configuration, mode='normal', decisions=[ask(), ask(), done()], maximum=3) as value:
        wait_for(lambda: runner(service_database, value['identifier'])['status'] == 'waiting', timeout=40)
        first = wait_for(lambda: native_wait(service_database, value))
        event = complete_original(value, first)
        assert event['sampled'] and event['ref_present']
        wait_for(lambda: runner(service_database, value['identifier'])['attempt'] == 2
            and runner(service_database, value['identifier'])['status'] == 'waiting', timeout=35)
        second = native_wait(service_database, value)
        assert second['assistance_id'] != first['assistance_id'] and second['runner_wait_id'] != first['runner_wait_id']
        assert second['run_id'] == first['run_id']
        old = service_database.rows('SELECT completion_fact FROM bs_browser_assistance_requests WHERE assistance_id=%s', (first['assistance_id'],))[0]['completion_fact']
        assert old['continuation']['phase'] == 'completed'
        assert old['continuation']['result']['next_assistance_id'] == second['assistance_id']
        assert service_database.rows('SELECT owner_runner_id FROM agent_runner_session_claims WHERE owner_runner_id=%s',
            (value['identifier'],)) == [{'owner_runner_id': value['identifier']}]
        event = complete_original(value, second)
        assert event['sampled'] and event['ref_present']
        assert_finished(workers, service_database, browser_page, actors['a'], value,
            attempts=3, physical_calls=5, controls=[
                {'action': 'browser_complete', 'status': 'consumed', 'consumed_attempt': 2},
                {'action': 'browser_complete', 'status': 'consumed', 'consumed_attempt': 3}])
        report = json.loads(value['report'].read_text())
        assert report['resume_calls'] == report['completion_calls'] == 2
        assert report['original_objects_preserved'] and report['last_confirmation_count'] == 2


def test_actual_started_original_continuation_unknown_is_not_replayed_after_worker_shutdown(
        workers, actors, service_database, browser_page, browser_redis, observation_configuration):
    _, _, environment = browser_redis
    release = threading.Event()
    response = done()
    response.release = release
    try:
        with resident(workers, actors['a'], service_database, browser_page, environment,
                observation_configuration, mode='normal', decisions=[ask(), response], maximum=2) as value:
            wait_for(lambda: runner(service_database, value['identifier'])['status'] == 'waiting', timeout=40)
            wait = native_wait(service_database, value)
            completed = complete_original(value, wait)
            assert completed['ref_present'] and completed['sampled']
            assert response.arrived.wait(timeout=25)
            fact = service_database.rows('SELECT completion_fact FROM bs_browser_assistance_requests WHERE assistance_id=%s',
                                        (wait['assistance_id'],))[0]['completion_fact']
            assert fact['continuation']['phase'] == 'started' and fact['continuation']['attempt'] == 2
            report = json.loads(value['report'].read_text())
            assert report['resume_calls'] == report['resumed_model_entries'] == 1
            assert report['confirmations_at_model_dispatch'] == 1
            workers.processes.stop(value['process'])
            assert value['process'].poll() is not None
            release.set()
            require_status(workers.api.call('POST', f'/v1/runners/{value["identifier"]}/controls', actor=actors['a'],
                json={'client_request_id': uuid.uuid4().hex, 'action': 'resume'}), 202)
            fresh, _ = workers.start()
            workers.assert_clean_exit(fresh)
            row = runner(service_database, value['identifier'])
            assert row['status'] in {'waiting', 'interrupted'} and row['attempt'] == 2
            assert row['result'] is None
            assert decoded(row['public_snapshot'])['waiting']['kind'] == 'verification'
            assert service_database.rows('SELECT owner_runner_id FROM agent_runner_session_claims WHERE owner_runner_id=%s',
                (value['identifier'],)) == [{'owner_runner_id': value['identifier']}]
            assert len(workers.provider.requests(value['marker'])) == 3
            assert browser_page.requests.count(browser_page.path) == 1
            assert service_database.rows('SELECT count(*) AS count FROM bs_browser_runs WHERE runner_id=%s', (value['identifier'],)) == [{'count': 1}]
            assert service_database.rows('SELECT 1 FROM chat_records WHERE session_id=%s', (actors['a'].session_id,)) == []
            assert service_database.rows('SELECT completion_fact FROM bs_browser_assistance_requests WHERE assistance_id=%s',
                (wait['assistance_id'],))[0]['completion_fact']['continuation']['phase'] == 'started'
    finally:
        release.set()


def test_actual_original_owner_shutdown_before_consume_preserves_control_for_verification(
        workers, actors, service_database, browser_page, browser_redis, observation_configuration):
    _, _, environment = browser_redis
    with resident(workers, actors['a'], service_database, browser_page, environment,
            observation_configuration, mode='owner_lost', decisions=[ask(), done()], maximum=2) as value:
        wait_for(value['entered'].exists, timeout=40)
        wait = native_wait(service_database, value)
        observed = complete_original(value, wait)
        assert observed['sampled'] and observed['ref_present'] and observed['bridged']
        original = completion_fact(service_database, wait)
        workers.processes.stop(value['process'])
        assert value['process'].poll() is not None
        assert not any(process_is_live(pid) for pid in value['descendants'])
        assert service_database.rows('SELECT runtime_state FROM bs_browser_runs WHERE runner_id=%s',
            (value['identifier'],)) == [{'runtime_state': 'closed'}]
        before = runner(service_database, value['identifier'])
        fresh, _ = workers.start()
        workers.assert_clean_exit(fresh)
        after = runner(service_database, value['identifier'])
        assert after['attempt'] == before['attempt'] == 1 and after['result'] is None
        assert after['resume_control_id'] == original['control_id']
        assert decoded(after['public_snapshot'])['waiting']['kind'] == 'verification'
        assert service_database.rows('SELECT status,consumed_attempt FROM agent_runner_controls WHERE control_id=%s',
            (original['control_id'],)) == [{'status': 'accepted', 'consumed_attempt': None}]
        assert completion_fact(service_database, wait) == original
        assert service_database.rows('SELECT owner_runner_id FROM agent_runner_session_claims WHERE owner_runner_id=%s',
            (value['identifier'],)) == [{'owner_runner_id': value['identifier']}]
        assert len(workers.provider.requests(value['marker'])) == 2
        assert browser_page.requests.count(browser_page.path) == 1
        assert service_database.rows('SELECT 1 FROM chat_records WHERE session_id=%s', (actors['a'].session_id,)) == []
        assert service_database.rows('SELECT count(*) AS count FROM agent_runner_usage_receipts WHERE runner_id=%s',
            (value['identifier'],)) == [{'count': 2}]


def test_actual_saved_original_browser_result_survives_cache_clear_fault_without_replay(
        workers, actors, service_database, browser_page, browser_redis, observation_configuration):
    _, _, environment = browser_redis
    with resident(workers, actors['a'], service_database, browser_page, environment,
            observation_configuration, mode='projection_failure', decisions=[ask(), done()], maximum=2) as value:
        wait_for(lambda: runner(service_database, value['identifier'])['status'] == 'waiting', timeout=40)
        wait = native_wait(service_database, value)
        observed = complete_original(value, wait)
        assert observed['sampled'] and observed['ref_present']
        wait_for(lambda: (value['report'].exists() and
            json.loads(value['report'].read_text()).get('postcommit_projection_failure')), timeout=30)
        workers.assert_clean_exit(value['process'])
        before = runner(service_database, value['identifier'])
        assert before['status'] == 'interrupted' and before['attempt'] == 2
        original = completion_fact(service_database, wait)
        assert original['continuation']['phase'] == 'completed'
        assert original['continuation']['result']['tool_result']['success']
        assert len(workers.provider.requests(value['marker'])) == 3
        report = json.loads(value['report'].read_text())
        assert report['resume_calls'] == report['last_confirmation_count'] == 1
        request_control(workers, actors['a'], value, 'resume')
        fresh, _ = workers.start()
        workers.assert_clean_exit(fresh)
        assert_finished(workers, service_database, browser_page, actors['a'], value,
            attempts=3, physical_calls=4, controls=[
                {'action': 'browser_complete', 'status': 'consumed', 'consumed_attempt': 2},
                {'action': 'resume', 'status': 'consumed', 'consumed_attempt': 3}])
        assert completion_fact(service_database, wait) == original
