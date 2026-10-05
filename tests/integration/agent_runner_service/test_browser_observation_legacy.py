"""Associated actual legacy PG/Redis/Browser/Hub observation stays available."""
import asyncio
import json
import uuid

import pytest

from .browser_io import browser_page, browser_redis, owned_descendants, process_is_live
from .browser_observation_fixture import observation_configuration
from .browser_observation_io import observation_socket
from .test_browser_observation import issue_ticket
from .test_browser_observation_authority import refused_socket
from .web_gateway import WebGateway
from .provider import Reply
from .conftest import wait_for
from .test_worker import workers, api_pair, prices

pytestmark = [pytest.mark.integration, pytest.mark.real_browser]


@pytest.mark.asyncio
async def test_actual_legacy_tool_uses_known_null_pg_binding_and_original_local_hub_wire(
        workers, actors, service_database, browser_page, browser_redis,
        observation_configuration):
    _, _, environment = browser_redis
    actor = actors['a']
    marker = workers.provider.register(Reply(content=json.dumps(
        {'action':'ask_user','reason':'Fictional legacy Browser assistance'})))
    report = workers.root/'legacy-suspended.json'
    inputs = workers.root/'legacy-input.json'
    inputs.write_text(json.dumps({'tenant_id':actor.tenant_id,'user_id':actor.user_id,
        'session_id':actor.session_id,'execution_id':'legacy-test-'+uuid.uuid4().hex,
        'call_id':'legacy-browser-call','task':marker+' observe fictional page',
        'url':browser_page.url,'report':str(report)}))
    inputs.chmod(0o600)
    api = WebGateway(workers.processes,
        environment={**workers.environment,**environment,**observation_configuration['gateway'],
                     'AID_TEST_LEGACY_BROWSER_INPUT':str(inputs)},
        factory='tests.integration.agent_runner_service.browser_observation_legacy_api:create_app',
        access_log=False)
    run_id = None
    descendants = set()
    try:
        await asyncio.to_thread(wait_for, report.is_file, timeout=35)
        actual = json.loads(report.read_text())
        assert actual.get('legacy_suspended') is True
        run_id = actual['run_id']
        rows = service_database.rows('SELECT runner_id,state FROM bs_browser_runs WHERE run_id=%s',(run_id,))
        assert rows == [{'runner_id':None,'state':'WAITING_HUMAN'}]
        assert service_database.rows('SELECT runner_id FROM bs_browser_assistance_requests WHERE run_id=%s',
                                     (run_id,)) == [{'runner_id':None}]
        ticket = issue_ticket(api, actor, run_id)
        async with observation_socket(api.url, run_id, ticket) as first:
            _, jpeg = await first.frame()
            assert len(jpeg)>100
        await refused_socket(api, run_id, ticket)
        second = issue_ticket(api, actor, run_id)
        async with observation_socket(api.url, run_id, second) as wire:
            await wire.frame()
        assert api.call('POST',f'/api/browser/runs/{run_id}/view_ticket',actor=actors['a_other']).status_code in {403,404}
        assert len(workers.provider.requests(marker)) == 1 and not workers.provider.errors
        assert service_database.rows('SELECT 1 FROM agent_runners WHERE user_id=%s',(actor.user_id,)) == []
        descendants = owned_descendants(api.child.pid)
        assert descendants, 'No actual original Browser resource was owned by legacy API'
    finally:
        api.close()
        if descendants:
            wait_for(lambda:not any(process_is_live(pid) for pid in descendants),timeout=8)
        if run_id is not None:
            service_database.rows('DELETE FROM bs_browser_assistance_requests WHERE run_id=%s',(run_id,))
            service_database.rows('DELETE FROM bs_browser_runs WHERE run_id=%s',(run_id,))
