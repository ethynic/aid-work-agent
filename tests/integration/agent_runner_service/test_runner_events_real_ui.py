"""Prepared built UI -> real Web/service/PG/Worker event observation.

Only external model IO is a loopback scripted provider with a timing gate.
No browser fetch/auth/SSE interception or application owner substitution.
This node is not a claim of production deployment or SSE capacity.
"""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import threading
from time import monotonic
import uuid
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect, sync_playwright

from src.local_tools import repository as devices

from .conftest import wait_for
from .provider import Reply, tool_reply
from .test_automatic_local_recovery import _deliver, _enable, _notifications
from .test_local_invocation_recovery import ordinary_local
from .test_local_verification import claim_probe, enable, stopped_proof
from .test_api import start_api_pair
from .test_worker import accept, workers, prices, runner, terminal, assert_one_committed_completion
from .web_gateway import WebGateway

pytestmark = [pytest.mark.integration, pytest.mark.real_browser]


@pytest.fixture(scope='module')
def api_pair(service_processes):
    return start_api_pair(service_processes, extra_environment={
        'AGENT_RUNNER_WEB_SERVICE_ID': 'runner-test', 'REDIS_ENABLED': 'false',
        'AGENT_RUNNER_AUTO_LOCAL_RECOVERY_PROVIDERS': 'boss-recruiting'})


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



@pytest.mark.parametrize('outcome', ['retry-succeeds', 'partial-result-then-cutoff', 'user-cancels-unknown'])
def test_actual_ui_observes_automatic_local_recovery_with_no_page_attached_at_completion(
        workers, ordinary_local, service_database, outcome):
    expires = outcome == 'partial-result-then-cutoff'
    cancels = outcome == 'user-cancels-unknown'
    actor, profile, folder, _ = ordinary_local
    _enable(workers)
    workers.environment.update({
        'AGENT_RUNNER_WEB_ENABLED': 'true',
        'AGENT_RUNNER_API_URL': workers.api.urls[0],
        'AGENT_RUNNER_WEB_SERVICE_ID': workers.api.service_id,
        'AGENT_RUNNER_WEB_SERVICE_TOKEN': workers.api._service_token,
        'AGENT_RUNNER_BROWSER_OWNER_ENABLED': 'false', 'AGENT_RUNNER_BROWSER_VIEW_ENABLED': 'false',
    })
    service_database.rows(
        'INSERT INTO subscriptions(subscription_id,tenant_id,subagent_type,status,starts_at) VALUES(%s,%s,%s,%s,%s)',
        ('recovery-ui-' + uuid.uuid4().hex, actor.tenant_id, 'main', 'active', datetime.now()))
    service_database.rows(
        'INSERT INTO user_agent_permissions(user_id,agent_id,tenant_id) VALUES(%s,%s,%s)',
        (actor.user_id, 'main', actor.tenant_id))
    device = json.loads((folder / 'device-ready.json').read_text())['device_id']
    if cancels:
        enable(service_database, {'id': device}, stop=True)
    successful_output = '后台完成了原本机任务'
    replies = [tool_reply('boss_select_job', {'job_name':'Fixture exact job'}, call_id='first-local')]
    if expires:
        replies.append(tool_reply('boss_select_job', {'job_name':'Fixture second job'}, call_id='second-local'))
    accepted = accept(workers.api, actor, workers.provider.register(
        *replies, Reply(content=successful_output)), profile_id=profile)
    identifier = accepted['runner_id']
    gateway = WebGateway(workers.processes, environment=workers.environment,
        factory='tests.integration.agent_runner_service.browser_card_ui_api:create_app', access_log=False)
    url = f'{gateway.url}/t/{actor.tenant_id}?_load_session={actor.session_id}'
    requests = []
    next_output = '同一会话执行了下一条任务'
    next_input = workers.provider.register(Reply(content=next_output))
    cancel_url = f'/api/chat/runners/{identifier}/cancel'

    def watch(page):
        page.on('request', lambda request: requests.append((request.method, urlsplit(request.url).path)))

    def runner_stream(response):
        return response.status == 200 and urlsplit(response.url).path == f'/api/chat/runners/{identifier}/events'

    def no_recovery_controls(page):
        for name in ('只读核验页面', '停止上一步并继续', '停止旧执行并解锁设备', '核对并继续', '结束等待，继续对话', '继续任务', '重试消息'):
            assert page.get_by_role('button', name=name, exact=True).count() == 0
        assert page.get_by_title('继续上次任务', exact=True).count() == 0
        assert page.get_by_text('结果需要核对', exact=False).count() == 0

    try:
        with tempfile.TemporaryDirectory(prefix='runner-auto-ui-', dir='/tmp') as temporary, sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'],
                env={**os.environ, 'TMPDIR': temporary})
            context = browser.new_context(viewport={'width': 1280, 'height': 950}, service_workers='block')
            context.add_init_script('localStorage.setItem(' + json.dumps('saas_token_' + actor.tenant_id)
                + ',' + json.dumps(actor.token) + ')')
            page = context.new_page()
            watch(page)
            with page.expect_response(runner_stream, timeout=20000):
                page.goto(url)
            child, _ = workers.start()
            if expires:
                _deliver(workers, service_database, identifier, actor, device, effect='applied')
            if expires or cancels:
                original = _deliver(workers, service_database, identifier, actor, device,
                                    code='EXECUTION_UNKNOWN', effect='unknown')
            else:
                original = _deliver(workers, service_database, identifier, actor, device,
                                    code='BUSY', retryable=True)
            progress = page.get_by_text('后台正在恢复本机任务', exact=True)
            progress.wait_for(timeout=20000)
            assert not _notifications(service_database, identifier)
            no_recovery_controls(page)
            page.set_viewport_size({'width': 390, 'height': 844})
            card = page.get_by_role('region', name='任务状态')
            box = card.bounding_box()
            assert box is not None and box['x'] >= 0 and box['x'] + box['width'] <= 390
            with page.expect_response(runner_stream, timeout=20000):
                page.reload()
            progress.wait_for(timeout=20000)
            no_recovery_controls(page)

            if expires:
                workers.assert_clean_exit(child)
                parked = runner(service_database, identifier)
                assert parked['status'] == 'waiting'
                key, episode = next((key, item) for key, item in parked['checkpoint']['automatic_local_recovery'].items()
                    if item['invocation_id'] == original)
                assert episode['status'] == 'waiting'
                draft = page.locator('textarea')
                draft.fill('待恢复后提交的草稿')
                page.get_by_title('发送', exact=True).click()
                expect(draft).to_have_value('待恢复后提交的草稿')

            if cancels:
                workers.assert_clean_exit(child)
                waiting = runner(service_database, identifier)
                assert waiting['status'] == 'waiting'
                reconciler, _ = workers.start()
                workers.assert_clean_exit(reconciler)
                verification = runner(service_database, identifier)['checkpoint']['local_verification']
                probe = verification['invocation_id']
                token, arguments = claim_probe(waiting, {'id': device}, probe)
                with page.expect_response(lambda response: response.status == 200
                        and urlsplit(response.url).path == cancel_url) as stopped:
                    card.get_by_role('button', name='停止任务', exact=True).click()
                response = stopped.value.json()['runner']
                assert response['cancel_requested'] and response['local_recovery_mode'] == 'automatic'
                no_recovery_controls(page)

            page.close()
            assert context.pages == []
            if cancels:
                replacement, _ = workers.start()
                cancelled = terminal(service_database, identifier)
                workers.assert_clean_exit(replacement)
                assert cancelled['status'] == 'cancelled'
                assert cancelled['checkpoint']['detached_local_invocations'] == [original]
                assert not _notifications(service_database, identifier)
                summary = cancelled['result']['output']
                assert '任务已取消' in summary and '结果尚未确认' in summary
                with pytest.raises(devices.DeviceOperationUnresolved, match='LOCAL_DEVICE_STOP_VERIFICATION_REQUIRED'):
                    devices.create_invocation(actor.tenant_id, actor.user_id, device, 'boss_select_job', {'job_name': 'Other job'})
            elif expires:
                # Advance only the persisted cutoff of the genuine wait. The Worker
                # finalizes it while every Web observer is disconnected.
                cutoff = (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat()
                service_database.rows('UPDATE agent_runners SET checkpoint=jsonb_set(checkpoint,%s,to_jsonb(%s::text)) WHERE runner_id=%s',
                                      (['automatic_local_recovery', key, 'deadline'], cutoff, identifier))
                replacement, _ = workers.start()
                failed = terminal(service_database, identifier)
                workers.assert_clean_exit(replacement)
                assert failed['status'] == 'failed'
                assert '已完成 1 个工具步骤' in failed['result']['output']
                summary = failed['result']['output']
                assert len(_notifications(service_database, identifier)) == 1
            else:
                _deliver(workers, service_database, identifier, actor, device, effect='applied')
                assert terminal(service_database, identifier)['status'] == 'completed'
                workers.assert_clean_exit(child)
                summary = successful_output
                assert not _notifications(service_database, identifier)

            page = context.new_page()
            watch(page)
            page.goto(url)
            page.get_by_text(summary, exact=True).wait_for(timeout=20000)
            assert page.get_by_text(summary, exact=True).count() == 1
            no_recovery_controls(page)
            assert page.get_by_role('region', name='任务状态').count() == 0
            assert len(_notifications(service_database, identifier)) == int(expires)
            draft = page.locator('textarea')
            assert draft.is_enabled()
            draft.fill(next_input)
            draft.press('Enter')
            accepted_next = wait_for(lambda: service_database.rows(
                'SELECT runner_id FROM agent_runners WHERE session_id=%s AND runner_id<>%s',
                (actor.session_id, identifier)), timeout=15)
            assert len(accepted_next) == 1
            next_worker, _ = workers.start()
            page.get_by_text(next_output, exact=True).wait_for(timeout=20000)
            assert terminal(service_database, accepted_next[0]['runner_id'])['result']['output'] == next_output
            workers.assert_clean_exit(next_worker)
            if cancels:
                late = devices.write_runtime_result(probe, actor.tenant_id, token, True, effect='none',
                    data=stopped_proof(arguments), device_id=device)
                assert late['state'] == 'succeeded'
                view = workers.api.call('GET', f'/v1/runners/{identifier}', actor=actor).json()['runner']
                assert view['local_recovery_mode'] == 'automatic' and view['status'] == 'cancelled'
                assert view['local_verification']['resource_stopped'] and not view['resume_requested']
                page.reload()
                page.get_by_text(summary, exact=True).wait_for(timeout=20000)
                no_recovery_controls(page)
            runner_posts = [path for method, path in requests if method == 'POST' and '/runners' in path]
            assert runner_posts == ([cancel_url] if cancels else []) + ['/api/chat/runners']
            context.close()
            browser.close()
    finally:
        gateway.close()
