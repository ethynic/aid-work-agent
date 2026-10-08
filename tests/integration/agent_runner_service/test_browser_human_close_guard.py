"""Actual PG epoch replacement while original Close queues: no write/reap/flag."""
import json
import time
import uuid

import pytest

from .browser_human_page import browser_human_page
from .browser_io import browser_redis, process_is_live
from .browser_observation_fixture import observation_configuration
from .browser_human_service_fixture import human_resident, take
from .provider import Reply
from .test_api import require_status
from .test_worker import workers, api_pair, prices, runner, accept
from .conftest import wait_for

pytestmark = [pytest.mark.integration, pytest.mark.real_browser]


def test_actual_immutable_cancel_attempt_rejected_in_original_io_lock_preserves_native_handle_across_renew_and_worker_serves_other_session(
        workers, actors, service_database, browser_human_page, browser_redis, observation_configuration):
    actor = actors['a']
    client, prefix, environment = browser_redis
    entered = workers.processes.root / ('original-close-entered-' + uuid.uuid4().hex)
    released = workers.processes.root / ('original-close-released-' + uuid.uuid4().hex)
    with human_resident(workers, actor, service_database, browser_human_page, environment,
            observation_configuration, max_tasks=3,
            probe='tests.integration.agent_runner_service.browser_human_close_guard_probe',
            probe_arguments=(entered, released)) as value:
        api, run_id = value['api'], value['browser']['run_id']
        take(value)
        try:
            assert require_status(api.call('POST', f'/api/browser/runs/{run_id}/cancel', actor=actor,
                params={'assistance_id': value['wait']['assistance_id']}), 200) == {'success': True}
            wait_for(entered.exists, timeout=5)
            current = runner(service_database, value['identifier'])
            assert current['attempt'] == 2 and current['status'] == 'finalizing' and current['cancel_requested']
            # Explicit native contract DI: replace authority by a real SQL epoch
            # while its old immutable Attempt2 Close waits on the original lock.
            # This is not a fabricated natural Worker3 execution or a mock fence.
            foreign_worker = 'fixture-foreign-epoch-' + uuid.uuid4().hex
            service_database.rows('UPDATE agent_runners SET attempt=3,worker_id=%s,lease_until=clock_timestamp()+interval \'60 seconds\' '
                'WHERE runner_id=%s AND attempt=2 AND worker_id=%s RETURNING runner_id',
                (foreign_worker, value['identifier'], current['worker_id']))
            foreign = runner(service_database, value['identifier'])
            assert foreign['attempt'] == 3 and foreign['worker_id'] == foreign_worker
            released.touch()
            report = value['report'].with_suffix('.guard.json')
            wait_for(lambda: report.exists() and json.loads(report.read_text()).get('rejected'), timeout=5)
            observed = json.loads(report.read_text())
            assert observed['captured_attempt'] == 2 and observed['guarded_close_calls'] == 1
            assert observed['code'] == 'BROWSER_CANCEL_OWNER_CHANGED'
            assert observed['seq_unchanged'] and observed['same_handle'] and observed['process_live']
            assert not observed['resource_confirmed']
            wait_for(lambda: json.loads(report.read_text()).get('managers_retained') is True, timeout=5)
            observed = json.loads(report.read_text())
            assert observed['executor_handles_present'] and observed['tokens_unchanged']
            before_lease = service_database.rows('SELECT owner_lease_until FROM bs_browser_runs WHERE run_id=%s',
                (run_id,))[0]['owner_lease_until']
            # Real original renew interval is 10s. Do not conclude from the
            # immediate guard result that legacy Redis flags cannot later reap.
            until = time.monotonic() + 10.5
            while time.monotonic() < until:
                assert value['process'].poll() is None
                assert any(process_is_live(pid) for pid in value['descendants'])
                time.sleep(.1)
            after_lease = service_database.rows('SELECT runtime_state,owner_lease_until FROM bs_browser_runs WHERE run_id=%s',
                (run_id,))[0]
            assert after_lease['runtime_state'] == 'live' and after_lease['owner_lease_until'] > before_lease
            raw = json.loads(client.get(f'{prefix}:browser_run:{actor.tenant_id}:{run_id}'))
            assert raw['state'] == 'RUNNING_HUMAN' and not raw['cancel_requested']
            assistance = json.loads(client.get(f'{prefix}:browser_assistance:{actor.tenant_id}:{value["wait"]["assistance_id"]}'))
            assert assistance['completion_mode'] == 'confirm_only'
            assert service_database.rows('SELECT completion_ref FROM bs_browser_assistance_requests WHERE runner_id=%s',
                (value['identifier'],)) == [{'completion_ref': None}]
            assert service_database.rows('SELECT 1 FROM agent_runner_controls WHERE runner_id=%s', (value['identifier'],)) == []
            row = runner(service_database, value['identifier'])
            assert row['attempt'] == 3 and row['worker_id'] == foreign_worker and row['status'] == 'finalizing'
            assert row['checkpoint'] == foreign['checkpoint'] and row['revision'] == foreign['revision']
            assert service_database.rows('SELECT owner_runner_id FROM agent_runner_session_claims WHERE owner_runner_id=%s',
                (value['identifier'],)) == [{'owner_runner_id': value['identifier']}]
            assert browser_human_page.observed_events() == []
            assert len(workers.provider.requests(value['marker'])) == 2
            # Old guarded cancellation must not kill the whole resident Worker.
            marker = workers.provider.register(Reply(content='Healthy other session after ordinary close denial'))
            healthy = accept(workers.api, actors['b'], marker)['runner_id']
            wait_for(lambda: runner(service_database, healthy)['status'] == 'completed', timeout=20)
            workers.assert_clean_exit(value['process'])
            assert len(workers.provider.requests(marker)) == 1
            assert service_database.rows('SELECT 1 FROM agent_runner_session_claims WHERE owner_runner_id=%s', (healthy,)) == []
            # Boot shutdown is legitimate later cleanup, not proof that the
            # rejected Close wrote or that foreign Attempt3 was finalized.
            assert runner(service_database, value['identifier'])['attempt'] == 3
            assert runner(service_database, value['identifier'])['result'] is None
        finally:
            released.touch()
