"""Real resident own runtime beyond a page of declared native foreign facts.

Foreign facts use actual PG bind/activate/wait/park/control ports; they are
storage-contract fixtures, not sixteen claimed live Chromium processes. The
positive original runtime is real and the Worker must skip foreign boot rows
before any authorizer/profile/prepare/consume and advance its bounded cursor.
"""
from datetime import datetime, timedelta
import json
import uuid

import pytest

from .browser_io import browser_page, browser_redis
from .browser_diagnostics import browser_diagnostics
from .browser_observation_fixture import observation_configuration
from .conftest import wait_for
from .provider import Reply, tool_reply
from .test_browser_continuation_races import ask, done, complete_original, native_wait, assert_finished
from .test_browser_owner_boundaries import delete_browser_facts
from .test_worker import workers, api_pair, prices, accept, runner

pytestmark = [pytest.mark.integration, pytest.mark.real_browser]


def seed_foreign_page(database, actor, owned_ids):
    from src.core.agent_engine.contracts import AgentMode, ExecutionState, Identity, ToolCall, ToolFact
    from src.services.agent_runner.browser_binding import BrowserOwnerRepository
    from src.services.agent_runner.contracts import Principal, RunnerSubmit
    from src.services.agent_runner.control_contracts import RunnerControl
    from src.services.agent_runner.control_repository import ControlRepository
    from src.services.agent_runner.execution_repository import ExecutionRepository
    from src.services.agent_runner.ownership import Attempt
    from src.services.agent_runner.repository import RunnerRepository
    repository, execution = RunnerRepository(database.connect), ExecutionRepository(database.connect)
    browser = BrowserOwnerRepository(database.connect)
    originals = []
    for _ in range(16):
        session_id = 'native-foreign-browser-' + uuid.uuid4().hex
        database.rows('INSERT INTO chat_sessions(session_id,user_id,tenant_id,title) VALUES (%s,%s,%s,%s)',
                      (session_id, actor.user_id, actor.tenant_id, 'Native foreign boot contract'))
        principal = Principal(Identity(actor.tenant_id, actor.user_id, session_id), 'user', actor.user_id, 'storage-test')
        submitted, created = repository.submit(principal, RunnerSubmit(client_request_id=uuid.uuid4().hex,
            session={'kind': 'web', 'session_id': session_id}, text='Native foreign boot input'), 'fixture-profile')
        assert created
        owned_ids.append(submitted['runner_id'])
        row = execution.acquire('native-foreign-worker-' + uuid.uuid4().hex, 120)
        assert row['runner_id'] == submitted['runner_id']
        attempt = Attempt(row['runner_id'], row['worker_id'], row['attempt'])
        state = ExecutionState(principal.identity, row['runner_id'], AgentMode.MASTER, 'Native foreign boot fixture', [])
        call_id = 'native-foreign-browser-call'
        state.tools[call_id] = ToolFact(ToolCall(call_id, 'browser_automation', {'steps': [{'action': 'snapshot'}]}), phase='dispatching')
        saved = execution.save_checkpoint(attempt, row['revision'], {'execution': state.checkpoint()})
        boot, epoch, run_id = (uuid.uuid4().hex for _ in range(3))
        record = dict(tenant_id=actor.tenant_id, user_id=actor.user_id, session_id=session_id,
                      run_id='br_native_' + run_id, state='CREATED', execution_target='server')
        bound = browser.bind_run(attempt, saved['revision'], execution_id=state.execution_id, call_id=call_id,
            record=record, worker_boot=boot, browser_epoch=epoch,
            endpoint='http://127.0.0.1:39999/internal/runner-browser', lease_seconds=300)
        browser.activate_run(attempt, execution_id=state.execution_id, call_id=call_id,
            run_id=record['run_id'], worker_boot=boot, browser_epoch=epoch, lease_seconds=300)
        request = dict(record, assistance_id='ba_native_' + uuid.uuid4().hex, agent_execution_id=state.execution_id,
            tool_call_id=call_id, reason_code='CAPTCHA_REQUIRED', instruction_code='PAGE_VERIFICATION',
            completion_mode='auto_or_confirm', expires_at=datetime.now() + timedelta(minutes=5))
        waited = browser.bind_wait(attempt, bound['revision'], execution_id=state.execution_id, call_id=call_id,
                                  assistance=request, worker_boot=boot, browser_epoch=epoch)
        checkpoint = waited['checkpoint']
        root = checkpoint['execution']
        wait_id = next(iter(root['resources']['browser_runs'][call_id]['waits']))
        root['outcome'] = 'waiting'
        root['tools'][call_id]['phase'] = 'waiting'
        root['waiting'] = dict(kind='human_assistance', tool_call_id=call_id, assistance_id=request['assistance_id'],
                               wait_id=wait_id, target_execution_id=state.execution_id)
        execution.park(attempt, waited['revision'], 'waiting', checkpoint, {})
        control, _ = ControlRepository(database.connect).submit(principal, row['runner_id'],
            RunnerControl(client_request_id=uuid.uuid4().hex, action='resume'))
        originals.append((repository.get(row['runner_id']), control))
    return originals


def test_real_resident_owned_browser_after_foreign_full_page_is_not_starved_or_consumed_by_other_boot(
        workers, actors, service_database, browser_page, browser_redis, observation_configuration):
    originals = []
    foreign_ids = []
    own = None
    process = None
    try:
        originals = seed_foreign_page(service_database, actors['a'], foreign_ids)
        boundary = originals[-1][0]['queue_order']
        _, _, environment = browser_redis
        workers.environment.update(environment)
        workers.environment.update(observation_configuration['worker'])
        initial = tool_reply('browser_automation', {}, call_id='race-original-browser-call')
        marker = workers.provider.register(initial, ask(), done(), Reply(content='Original race continuation complete'))
        initial.tool_calls[0]['function']['arguments'] = json.dumps(
            {'task': marker + ' original later page', 'url': browser_page.url, 'headless': True})
        own = accept(workers.api, actors['a'], marker)
        assert own['queue_order'] > boundary
        suffix = uuid.uuid4().hex
        command, report, entered, release, affinity = [workers.processes.root / (prefix + suffix + '.json') for prefix in
            ('fair-command-', 'fair-report-', 'fair-entered-', 'fair-release-', 'fair-acquisition-')]
        worker_id = 'fair-continuation-' + suffix
        process = workers.processes.start(['-m', 'tests.integration.agent_runner_service.browser_continuation_affinity_probe',
            str(command), str(report), str(entered), str(release), 'normal', str(affinity),
            '--worker-id', worker_id, '--max-tasks', '2'], environment=workers.environment, private_working_directory=True)
        workers.children.append((process, worker_id))
        value = dict(identifier=own['runner_id'], marker=marker, process=process, command=command, report=report,
                     entered=entered, release=release, sequence=0, descendants=set())
        wait_for(lambda: runner(service_database, own['runner_id'])['status'] == 'waiting', timeout=45)
        wait = native_wait(service_database, value)
        observed = complete_original(value, wait)
        assert observed['sampled'] and observed['ref_present']
        assert_finished(workers, service_database, browser_page, actors['a'], value,
            attempts=2, physical_calls=4,
            controls=[{'action': 'browser_complete', 'status': 'consumed', 'consumed_attempt': 2}])
        scans = json.loads(affinity.read_text())
        assert any(scan['after'] == boundary for scan in scans)
        assert any(scan['before'] == boundary and scan['after'] > boundary for scan in scans)
        for original, control in originals:
            current = runner(service_database, original['runner_id'])
            for key in ('status', 'attempt', 'revision', 'checkpoint', 'input', 'resume_control_id'):
                assert current[key] == original[key]
            assert service_database.rows('SELECT status,consumed_attempt FROM agent_runner_controls WHERE control_id=%s',
                (control['control_id'],)) == [{'status': 'accepted', 'consumed_attempt': None}]
            assert service_database.rows('SELECT owner_runner_id FROM agent_runner_session_claims WHERE owner_runner_id=%s',
                (original['runner_id'],)) == [{'owner_runner_id': original['runner_id']}]
            assert service_database.rows('SELECT 1 FROM agent_runner_usage_receipts WHERE runner_id=%s',
                (original['runner_id'],)) == []
    except Exception:
        diagnosis = {'foreign_rows_bound': len(originals), 'fixture_rows_created': len(foreign_ids)}
        if own is not None:
            diagnosis['own'] = browser_diagnostics(workers, service_database, own['runner_id'], marker, browser_page, process)
        if process is not None and affinity.exists():
            diagnosis['observed_acquisition_cursors'] = json.loads(affinity.read_text())
        print('SAFE_BROWSER_CONTINUATION_AFFINITY_DIAG=' + json.dumps(diagnosis))
        raise
    finally:
        if process is not None:
            workers.processes.stop(process)
        for identifier in foreign_ids:
            delete_browser_facts(service_database, identifier)
        if own is not None:
            delete_browser_facts(service_database, own['runner_id'])
