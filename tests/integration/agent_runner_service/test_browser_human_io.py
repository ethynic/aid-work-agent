"""Real queued IPC guard, single seq, and an observer detached after actual write."""
import json
import uuid

import pytest

from .browser_human_page import browser_human_page
from .browser_io import browser_redis
from .browser_observation_fixture import observation_configuration
from .browser_observation_io import observation_socket
from .browser_human_service_fixture import human_resident, take, complete, assert_owned, assert_finished
from .test_browser_human_service import input_denial
from .test_browser_observation import issue_ticket
from .test_worker import workers, api_pair, prices
from .conftest import wait_for

pytestmark = [pytest.mark.integration, pytest.mark.real_browser]


@pytest.mark.asyncio
async def test_actual_invalid_input_and_queued_permission_rejection_preserve_seq_then_written_detached_keyboard_ack_and_sampler_resume_succeed(
        workers, actors, service_database, browser_human_page, browser_redis, observation_configuration):
    actor = actors['a']
    _, _, environment = browser_redis
    paths = [workers.processes.root / ('original-human-io-' + uuid.uuid4().hex) for _ in range(4)]
    entered, released, written, acknowledged = paths
    with human_resident(workers, actor, service_database, browser_human_page, environment,
            observation_configuration, probe='tests.integration.agent_runner_service.browser_human_io_probe',
            probe_arguments=paths) as value:
        api, run_id = value['api'], value['browser']['run_id']
        status = service_database.rows('SELECT status FROM tenants WHERE tenant_id=%s', (actor.tenant_id,))[0]['status']
        try:
            async with observation_socket(api.url, run_id, issue_ticket(api, actor, run_id)) as view:
                _, jpeg = await view.frame()
                assert len(jpeg) > 100
                take(value)
                await view.connection.send(json.dumps({'type': 'keyboard'}))
                assert await input_denial(view) == 'HUMAN_INPUT_INVALID'
                await view.connection.send(json.dumps({'type': 'keyboard', 'key': 'G'}))
                wait_for(entered.exists, timeout=5)
                # First action auth passed. Actual original _request is now
                # queued on its same IO lock before DTO/seq/IPC construction.
                service_database.rows("UPDATE tenants SET status='suspended' WHERE tenant_id=%s", (actor.tenant_id,))
                released.touch()
                assert await input_denial(view) == 'TENANT_UNAVAILABLE'
                service_database.rows('UPDATE tenants SET status=%s WHERE tenant_id=%s', (status, actor.tenant_id))
                observed = json.loads(value['report'].read_text())
                assert observed['input_denied'] == 2 and observed['denied_without_fields'] == [True, True]
                assert observed['input_fields'] == 0 and browser_human_page.observed_events() == []
                assert_owned(workers, service_database, value)
                await view.connection.send(json.dumps({'type': 'pointer', 'action': 'click', 'x': 140, 'y': 120}))
                await view.connection.send(json.dumps({'type': 'keyboard', 'key': 'F'}))
                wait_for(written.exists, timeout=5)
                wait_for(lambda: browser_human_page.observed_events() == [{'kind': 'input', 'value': 'F'}], timeout=5)
                # Detach this actual WS while original owner task still awaits
                # its real ACK. It cannot cancel the written physical command.
            acknowledged.touch()
            wait_for(lambda: json.loads(value['report'].read_text()).get('input_accepted') == 2, timeout=5)
            assert_owned(workers, service_database, value)
            async with observation_socket(api.url, run_id, issue_ticket(api, actor, run_id)) as resumed_view:
                await resumed_view.frame()
                await resumed_view.connection.send(json.dumps({'type': 'pointer', 'action': 'click', 'x': 440, 'y': 120}))
                wait_for(lambda: browser_human_page.observed_events() == [
                    {'kind': 'input', 'value': 'F'}, {'kind': 'ready', 'value': True}], timeout=5)
                complete(value)
            assert_finished(workers, service_database, value, browser_human_page)
            final = json.loads(value['report'].read_text())
            assert final['input_calls'] == 5 and final['input_accepted'] == 3 and final['input_fields'] == 3
            assert final['input_denied'] == 2 and final['denied_without_fields'] == [True, True]
            assert final['sample_fields'] >= 1 and final['resume_calls'] == final['confirmation_added'] == 1
            assert final['same_page_ops'] and final['same_raw_executor'] and final['resume_success']
        finally:
            released.touch()
            acknowledged.touch()
            service_database.rows('UPDATE tenants SET status=%s WHERE tenant_id=%s', (status, actor.tenant_id))
