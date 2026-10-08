"""Prepared built UI -> real Web/service/PG/Worker event observation.

Only external model IO is a loopback scripted provider with a timing gate.
No browser fetch/auth/SSE interception or application owner substitution.
This node is not a claim of production deployment or SSE capacity.
"""
from dataclasses import replace
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
import threading
from time import monotonic
import uuid

import pytest
from playwright.sync_api import sync_playwright

from .conftest import wait_for
from .provider import Reply
from .test_api import start_api_pair
from .test_worker import workers, prices, runner, assert_one_committed_completion
from .web_gateway import WebGateway

pytestmark = [pytest.mark.integration, pytest.mark.real_browser]


@pytest.fixture(scope='module')
def api_pair(service_processes):
    return start_api_pair(service_processes, extra_environment={
        'AGENT_RUNNER_WEB_SERVICE_ID': 'runner-test', 'REDIS_ENABLED': 'false'})


def test_actual_built_ui_event_stream_queries_original_worker_then_refresh_detach_and_find_terminal(
        workers, actors, service_database):
    actor = actors['a']
    service_database.rows(
        'INSERT INTO subscriptions(subscription_id,tenant_id,subagent_type,status,starts_at) VALUES(%s,%s,%s,%s,%s)',
        ('events-ui-' + uuid.uuid4().hex, actor.tenant_id, 'main', 'active', datetime.now()))
    service_database.rows(
        'INSERT INTO user_agent_permissions(user_id,agent_id,tenant_id) VALUES(%s,%s,%s)',
        (actor.user_id, 'main', actor.tenant_id))
    workers.environment.update({'AGENT_RUNNER_WEB_ENABLED': 'true',
        'AGENT_RUNNER_API_URL': workers.api.urls[0],
        'AGENT_RUNNER_WEB_SERVICE_ID': workers.api.service_id,
        'AGENT_RUNNER_WEB_SERVICE_TOKEN': workers.api._service_token,
        'AGENT_RUNNER_BROWSER_OWNER_ENABLED': 'false', 'AGENT_RUNNER_BROWSER_VIEW_ENABLED': 'false'})
    release = threading.Event()
    output = 'Actual event UI original worker result'
    reply = Reply(content=output, release=release)
    marker = workers.provider.register(reply)
    gateway = WebGateway(workers.processes, environment=workers.environment,
        factory='tests.integration.agent_runner_service.browser_card_ui_api:create_app', access_log=False)
    child = None
    identifier = None
    observed = []
    completed_streams = []
    beginning = monotonic()
    timing = {}
    # Only the UI renderer uses this private Linux parent. No Redis is needed.
    with tempfile.TemporaryDirectory(prefix='runner-event-ui-', dir='/tmp') as temporary:
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True,
                    args=['--no-sandbox', '--disable-dev-shm-usage'],
                    env={**os.environ, 'TMPDIR': temporary})
                try:
                    context = browser.new_context(viewport={'width': 1280, 'height': 1000}, service_workers='block')
                    context.add_init_script('localStorage.setItem(' + json.dumps('saas_token_' + actor.tenant_id)
                        + ',' + json.dumps(actor.token) + ')')

                    def observe(response):
                        # Store only original response class/status/safe counters.
                        path = response.url.split('?', 1)[0].split('/api/', 1)[-1]
                        if not '/api/' in response.url:
                            return
                        item = {'method': response.request.method, 'path': path, 'status': response.status,
                            'at': monotonic() - beginning}
                        if path.endswith('/events') and response.status == 200:
                            completed_streams.append(response)
                        elif identifier and path == 'chat/runners/' + identifier and response.status == 200:
                            payload = response.json()['runner']
                            item.update(runner_status=payload['status'], view_revision=payload['view_revision'])
                        observed.append(item)

                    page = context.new_page()
                    page.on('response', observe)
                    page.goto(gateway.url + '/t/' + actor.tenant_id)
                    textarea = page.locator('textarea')
                    textarea.wait_for(state='visible', timeout=20000)
                    assert textarea.is_enabled()
                    textarea.fill(marker)
                    textarea.press('Enter')
                    accepted = wait_for(lambda: service_database.rows(
                        'SELECT runner_id,session_id,tenant_id FROM agent_runners WHERE user_id=%s',
                        (actor.user_id,)), timeout=15)
                    assert len(accepted) == 1 and accepted[0]['tenant_id'] == actor.tenant_id
                    identifier = accepted[0]['runner_id']
                    actor = replace(actor, session_id=accepted[0]['session_id'])
                    assert service_database.rows('SELECT user_id,tenant_id FROM chat_sessions WHERE session_id=%s',
                        (actor.session_id,)) == [{'user_id': actor.user_id, 'tenant_id': actor.tenant_id}]
                    # Pump real browser events while waiting, without intercepting fetch.
                    def wait_browser(predicate, timeout=10000):
                        end = monotonic() + timeout / 1000
                        while monotonic() < end:
                            if predicate():
                                return
                            page.wait_for_timeout(25)
                        raise AssertionError('ACTUAL_EVENT_UI_OBSERVATION_TIMEOUT')

                    wait_browser(lambda: any(item['path'] == 'chat/runners/' + identifier + '/events'
                        and item['status'] == 200 for item in observed))
                    wait_browser(lambda: any(item.get('runner_status') == 'queued' for item in observed))
                    assert runner(service_database, identifier)['attempt'] == 0
                    timing['worker_start'] = monotonic() - beginning
                    child, _ = workers.start()
                    wait_browser(lambda: reply.arrived.is_set())
                    assert runner(service_database, identifier)['status'] == 'running'
                    wait_browser(lambda: any(item.get('runner_status') == 'running' for item in observed))
                    page.get_by_title('停止生成', exact=True).wait_for(timeout=10000)
                    # Actual refresh re-discovers the original accepted runner and subscribes again.
                    before = len(completed_streams)
                    page.reload()
                    page.locator('textarea').wait_for(state='visible', timeout=15000)
                    wait_browser(lambda: len(completed_streams) > before)
                    page.get_by_title('停止生成', exact=True).wait_for(timeout=10000)
                    assert len(service_database.rows('SELECT runner_id FROM agent_runners WHERE user_id=%s',
                        (actor.user_id,))) == 1
                    page.close()
                    timing['all_pages_detached'] = monotonic() - beginning
                    assert child.poll() is None and runner(service_database, identifier)['status'] == 'running'
                    assert not runner(service_database, identifier)['cancel_requested']
                    # Closing every page detaches observation only. The new page reattaches before model return.
                    before = len(completed_streams)
                    page = context.new_page()
                    page.on('response', observe)
                    page.goto(gateway.url + '/t/' + actor.tenant_id)
                    wait_browser(lambda: len(completed_streams) > before)
                    page.get_by_title('停止生成', exact=True).wait_for(timeout=10000)
                    timing['reattached_before_release'] = monotonic() - beginning
                    release.set()
                    page.get_by_text(output, exact=True).wait_for(timeout=20000)
                    wait_browser(lambda: any(item.get('runner_status') == 'completed' for item in observed))
                    workers.assert_clean_exit(child)
                    assert_one_committed_completion(service_database, actor, identifier, output)
                    assert page.get_by_text(output, exact=True).count() == 1
                    assert page.locator('textarea').is_enabled()
                    assert page.get_by_title('停止生成', exact=True).count() == 0
                    assert sum(item['method'] == 'POST' and item['path'] == 'chat/runners' for item in observed) == 1
                    assert not any(item['path'].endswith('/cancel') or 'continuations/' in item['path'] for item in observed)
                    assert len(workers.provider.requests(marker)) == 1 and not workers.provider.errors
                    assert runner(service_database, identifier)['event_seq'] >= 3
                    screenshot = Path('/app/tests/.artifacts/runner-events-real-ui-terminal.png')
                    screenshot.parent.mkdir(exist_ok=True)
                    page.screenshot(path=str(screenshot), full_page=True)
                    summary = {'scope': 'real built UI subscription and authoritative GET; GET trigger is not attributed solely to event',
                        'timing_seconds': timing, 'event_200_count': len(completed_streams),
                        'authoritative_gets': [{'at': item['at'], 'status': item['runner_status'],
                            'view_revision': item['view_revision']} for item in observed if 'runner_status' in item],
                        'runner_posts': 1, 'provider_calls': 1, 'claim_released': True,
                        'screenshot': str(screenshot)}
                    screenshot.with_suffix('.json').write_text(json.dumps(summary, indent=2))
                    context.close()
                finally:
                    browser.close()
        finally:
            release.set()
            if child is not None:
                workers.processes.stop(child)
            gateway.close()
