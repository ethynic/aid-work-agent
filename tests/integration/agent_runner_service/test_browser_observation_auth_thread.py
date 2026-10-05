"""A held real synchronous authorization must not block owner lease renewal.

Timing DI suspends the original synchronous method before its SQL query. The
method is eventually called, no identity or result is manufactured. Observer
closure on its finite auth timeout is allowed; original Browser is unaffected.
"""
import asyncio
import json
from pathlib import Path
import uuid

import pytest

from .browser_io import browser_page, browser_redis
from .browser_observation_fixture import observation_api, observation_configuration
from .browser_observation_io import observation_socket
from .test_browser_observation import issue_ticket
from .test_browser_owner_boundaries import delete_browser_facts
from .provider import Reply, tool_reply
from .conftest import wait_for
from .test_worker import workers, api_pair, prices, accept, runner, decoded

pytestmark = [pytest.mark.integration,pytest.mark.real_browser]


@pytest.mark.asyncio
async def test_held_original_synchronous_view_authorization_does_not_stop_browser_lease_loop(
        workers,actors,service_database,browser_page,browser_redis,observation_configuration):
    _,_,environment = browser_redis
    workers.environment.update(environment)
    workers.environment.update(observation_configuration['worker'])
    original = tool_reply('browser_automation',{},call_id='thread-auth-original-call')
    marker = workers.provider.register(original,Reply(content=json.dumps({'action':'ask_user','reason':'Fictional wait'})))
    original.tool_calls[0]['function']['arguments'] = json.dumps(
        {'task':marker+' inspect local fixture','url':browser_page.url,'headless':True})
    identifier = accept(workers.api,actors['a'],marker)['runner_id']
    report,release = workers.root/'auth-thread-held',workers.root/'auth-thread-release'
    worker_id = 'auth-thread-'+uuid.uuid4().hex
    probe = Path(__file__).with_name('browser_observation_auth_thread_probe.py')
    child = workers.processes.start([str(probe),str(report),str(release),'--worker-id',worker_id],
        environment=workers.environment,private_working_directory=True)
    workers.children.append((child,worker_id))
    try:
        await asyncio.to_thread(wait_for,lambda:runner(service_database,identifier)['status']=='waiting',timeout=40)
        node = decoded(runner(service_database,identifier)['checkpoint'])['execution']
        run_id = node['resources']['browser_runs']['thread-auth-original-call']['run_id']
        with observation_api(workers.processes,{**workers.environment,**observation_configuration['gateway']}) as api:
            ticket = issue_ticket(api,actors['a'],run_id)
            async with observation_socket(api.url,run_id,ticket) as wire:
                await wire.frame()
                await asyncio.to_thread(wait_for,report.is_file,timeout=4)
                before = service_database.rows('SELECT owner_lease_until FROM bs_browser_runs WHERE run_id=%s',(run_id,))[0]['owner_lease_until']
                await asyncio.to_thread(wait_for,lambda:service_database.rows(
                    'SELECT owner_lease_until FROM bs_browser_runs WHERE run_id=%s',(run_id,))[0]['owner_lease_until']>before,timeout=15)
                assert not release.exists(), 'Held synchronous method was unexpectedly released'
                assert child.poll() is None
                assert runner(service_database,identifier)['status']=='waiting'
                assert not runner(service_database,identifier)['cancel_requested']
                assert service_database.rows('SELECT runtime_state FROM bs_browser_runs WHERE run_id=%s',(run_id,))==[{'runtime_state':'live'}]
                release.touch()
            assert len(workers.provider.requests(marker))==2
    finally:
        release.touch()
        workers.processes.stop(child)
        delete_browser_facts(service_database,identifier)
