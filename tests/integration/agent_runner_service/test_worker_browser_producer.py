"""Real worker/Engine/BrowserManager/Playwright/Redis producer acceptance.

Only the external model and visited page are fictional loopback HTTP peers.
There is no sidecar, completion bridge, card UI or recovery proof in this slice.
"""
from decimal import Decimal
import json
import threading

import pytest

from .browser_io import browser_page, browser_redis, owned_descendants, process_is_live, redis_run_view
from .browser_diagnostics import browser_diagnostics
from .conftest import wait_for
from .provider import Reply, tool_reply
from .test_worker import workers, api_pair, prices, accept, runner, terminal, decoded

pytestmark = [pytest.mark.integration, pytest.mark.real_browser]


def test_actual_browser_start_live_original_fence_and_confirmed_close(
        workers, actors, service_database, browser_page, browser_redis):
    actor = actors['a']
    client, prefix, redis_environment = browser_redis
    workers.environment.update(redis_environment)
    workers.environment['AGENT_RUNNER_BROWSER_OWNER_ENABLED'] = 'true'
    release = threading.Event()
    decision = Reply(content=json.dumps({'action': 'done', 'reason': 'Fictional page verified'}), release=release)
    initial = tool_reply('browser_automation', {}, call_id='original-browser-producer-call')
    marker = workers.provider.register(initial, decision, Reply(content='Browser original owner completed'))
    # Actual tool input, not the storage fixture's declared snapshot shape.
    initial.tool_calls[0]['function']['arguments'] = json.dumps(
        {'task': marker + ' inspect the fictional local page', 'url': browser_page.url, 'headless': True})
    accepted = accept(workers.api, actor, marker)
    identifier = accepted['runner_id']
    child = None
    descendants = set()
    try:
        child, worker_id = workers.start()
        arrived = decision.arrived.wait(timeout=25)
        diagnosis = browser_diagnostics(workers, service_database, identifier, marker, browser_page, child)
        # A fake peer's script position alone does not identify the physical
        # caller. Prove Browser-specific request and retain safe failure facts.
        assert arrived and diagnosis['requests'][-1]['browser_decision_instruction'], json.dumps(diagnosis)
        rows = service_database.rows('SELECT * FROM bs_browser_runs WHERE runner_id=%s', (identifier,))
        assert len(rows) == 1
        run = rows[0]
        assert (run['tenant_id'], run['user_id'], run['session_id']) == (actor.tenant_id, actor.user_id, actor.session_id)
        assert run['runner_execution_id'] == identifier
        assert run['runner_tool_call_id'] == 'original-browser-producer-call'
        assert run['owner_worker_id'] == worker_id and run['owner_boot_id'] and run['browser_epoch']
        assert run['runtime_state'] == 'live' and run['state'] == 'RUNNING_AGENT', json.dumps(diagnosis)
        assert service_database.rows('SELECT owner_lease_until > clock_timestamp() AS live FROM bs_browser_runs WHERE run_id=%s',
                                     (run['run_id'],)) == [{'live': True}]
        checkpoint = decoded(runner(service_database, identifier)['checkpoint'])['execution']
        fact = checkpoint['resources']['browser_runs']['original-browser-producer-call']
        assert fact['run_id'] == run['run_id'] and fact['owner_boot_id'] == run['owner_boot_id']
        assert fact['browser_epoch'] == run['browser_epoch']
        # Fresh independent Redis client observes original distributed state and
        # actual lease; private owner token remains solely in memory, never output.
        run_key = f"{prefix}:browser_run:{actor.tenant_id}:{run['run_id']}"
        owner_key = f"{prefix}:browser_owner:{actor.tenant_id}:{run['run_id']}"
        assert redis_run_view(client, run_key, owner_key) == {
            'run_id': run['run_id'], 'state': 'RUNNING_AGENT', 'lease_live': True}
        assert browser_page.path in browser_page.requests and not browser_page.unexpected
        descendants = owned_descendants(child.pid)
        assert len(descendants) >= 2, 'Actual Playwright worker/Chromium descendants were not observed'
        release.set()
        finished = terminal(service_database, identifier, timeout=30)
        workers.assert_clean_exit(child)
        assert finished['status'] == 'completed' and finished['settlement_status'] == 'settled'
        closed = service_database.rows('SELECT state,runtime_state,owner_lease_until FROM bs_browser_runs WHERE run_id=%s',
                                      (run['run_id'],))[0]
        assert closed == {'state': 'SUCCEEDED', 'runtime_state': 'closed', 'owner_lease_until': None}
        wait_for(lambda: not any(process_is_live(pid) for pid in descendants), timeout=5)
        assert client.get(owner_key) is None
        assert redis_run_view(client, run_key, owner_key) == {
            'run_id': run['run_id'], 'state': 'SUCCEEDED', 'lease_live': False}
        assert service_database.rows('SELECT 1 FROM agent_runner_session_claims WHERE owner_runner_id=%s', (identifier,)) == []
        receipts = service_database.rows('SELECT phase,applied,usage FROM agent_runner_usage_receipts WHERE runner_id=%s', (identifier,))
        assert len(receipts) == 3 and all(item['phase'] == 'observed' and item['applied'] for item in receipts)
        assert sum(decoded(item['usage'])['prompt_tokens'] + decoded(item['usage'])['completion_tokens'] for item in receipts) == 54
        records = service_database.rows('SELECT total_token_count,credit_cost FROM chat_records WHERE session_id=%s', (actor.session_id,))
        assert records == [{'total_token_count': 54, 'credit_cost': Decimal('0.01')}]
        requests = workers.provider.requests(marker)
        assert len(requests) == 3 and not workers.provider.errors
        assert any(message.get('role') == 'tool' and 'Fictional page verified' in message['content']
                   for message in requests[-1]['messages'])
    finally:
        release.set()
        if child is not None:
            workers.processes.stop(child)
        # Browser independent PG rows belong to this exact fictional Runner.
        service_database.rows('DELETE FROM bs_browser_resume_jobs WHERE run_id IN (SELECT run_id FROM bs_browser_runs WHERE runner_id=%s)', (identifier,))
        service_database.rows('DELETE FROM bs_browser_assistance_requests WHERE runner_id=%s', (identifier,))
        service_database.rows('DELETE FROM bs_browser_runs WHERE runner_id=%s', (identifier,))
