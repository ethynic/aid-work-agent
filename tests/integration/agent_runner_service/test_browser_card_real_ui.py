"""Actual built UI -> original auth/Web Runner/API/Worker/Browser end-to-end.

Only provider and the visited page are fictional loopback IO. There are no
intercepted HTTP/WS APIs, fake identity endpoints, or substituted domain owners.
"""
from dataclasses import replace
from datetime import datetime
import json
import os
from pathlib import Path
import uuid

import httpx
import pytest
from playwright.sync_api import sync_playwright

from .browser_human_page import browser_human_page
from .browser_human_service_fixture import assert_finished
from .browser_io import browser_redis, owned_descendants
from .browser_observation_fixture import observation_configuration
from .conftest import wait_for
from .provider import Reply, tool_reply
from .test_browser_card_projection import api_pair
from .test_browser_owner_boundaries import delete_browser_facts
from .test_worker import workers, prices, runner, decoded
from .web_gateway import WebGateway

pytestmark = [pytest.mark.integration, pytest.mark.real_browser]


def test_actual_built_ui_keyboard_submit_take_input_complete_and_original_worker_terminal(
        workers, actors, service_database, browser_human_page, browser_redis, observation_configuration):
    actor = actors['a']
    # Original active principal and real subscription/user grant, not an auth stub.
    service_database.rows('INSERT INTO subscriptions(subscription_id,tenant_id,subagent_type,status,starts_at) VALUES(%s,%s,%s,%s,%s)',
        ('card-ui-' + uuid.uuid4().hex, actor.tenant_id, 'main', 'active', datetime.now()))
    service_database.rows('INSERT INTO user_agent_permissions(user_id,agent_id,tenant_id) VALUES(%s,%s,%s)',
        (actor.user_id, 'main', actor.tenant_id))
    _, _, redis_environment = browser_redis
    workers.environment.update(redis_environment)
    workers.environment.update(observation_configuration['worker'])
    workers.environment.update({'AGENT_RUNNER_WEB_ENABLED': 'true',
        'AGENT_RUNNER_API_URL': workers.api.urls[0], 'AGENT_RUNNER_WEB_SERVICE_ID': workers.api.service_id,
        'AGENT_RUNNER_WEB_SERVICE_TOKEN': workers.api._service_token})
    original = tool_reply('browser_automation', {}, call_id='human-risk-original-call')
    marker = workers.provider.register(original,
        Reply(content=json.dumps({'action': 'ask_user', 'reason': 'Fictional actual UI wait'})),
        Reply(content=json.dumps({'action': 'done', 'reason': 'Original page completed'})),
        Reply(content='Human risk original Browser completed'))
    original.tool_calls[0]['function']['arguments'] = json.dumps(
        {'task': marker + ' original fictional page', 'url': browser_human_page.url, 'headless': True})
    api = WebGateway(workers.processes, environment={**workers.environment, **observation_configuration['gateway']},
        factory='tests.integration.agent_runner_service.browser_card_ui_api:create_app', access_log=False)
    process = None
    identifier = None
    observed = []
    screenshot = Path('/app/tests/.artifacts/card-real-ui.png')
    screenshot.parent.mkdir(exist_ok=True)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True,
                args=['--no-sandbox', '--disable-dev-shm-usage'],
                env={**os.environ, 'TMPDIR': str(workers.processes.browser_temporary_directory)})
            try:
                context = browser.new_context(viewport={'width': 1280, 'height': 1000}, service_workers='block')
                # Store only the actual opaque PG token. /me supplies all user/tenant data.
                context.add_init_script('localStorage.setItem(' + json.dumps('saas_token_' + actor.tenant_id)
                    + ',' + json.dumps(actor.token) + ')')
                page = context.new_page()
                # Only path class/method/status observed; never tickets/query/body.
                page.on('response', lambda response: observed.append((response.request.method,
                    response.url.split('?', 1)[0].split('/api/', 1)[-1], response.status))
                    if '/api/' in response.url else None)
                page.goto(api.url + '/t/' + actor.tenant_id)
                textarea = page.locator('textarea')
                textarea.wait_for(state='visible', timeout=20000)
                assert textarea.is_enabled(), 'ACTUAL_UI_BOOTSTRAP_INPUT_DISABLED'
                textarea.fill(marker)
                textarea.press('Enter')
                accepted = wait_for(lambda: service_database.rows(
                    'SELECT runner_id,session_id,tenant_id FROM agent_runners WHERE user_id=%s',
                    (actor.user_id,)), timeout=15)
                assert len(accepted) == 1
                identifier = accepted[0]['runner_id']
                assert accepted[0]['tenant_id'] == actor.tenant_id
                assert service_database.rows('SELECT user_id,tenant_id FROM chat_sessions WHERE session_id=%s',
                    (accepted[0]['session_id'],)) == [{'user_id': actor.user_id, 'tenant_id': actor.tenant_id}]
                actor = replace(actor, session_id=accepted[0]['session_id'])
                worker_id = 'card-ui-' + uuid.uuid4().hex
                report = workers.processes.root / ('card-ui-report-' + uuid.uuid4().hex + '.json')
                process = workers.processes.start(['-m',
                    'tests.integration.agent_runner_service.browser_human_service_probe', str(report),
                    '--worker-id', worker_id, '--max-tasks', '2'],
                    environment=workers.environment, private_working_directory=True)
                workers.children.append((process, worker_id))
                page.get_by_role('button', name='开始接管', exact=True).wait_for(timeout=45000)
                assert runner(service_database, identifier)['status'] == 'waiting'
                native = service_database.rows('SELECT * FROM bs_browser_runs WHERE runner_id=%s', (identifier,))[0]
                wait = service_database.rows('SELECT * FROM bs_browser_assistance_requests WHERE runner_id=%s', (identifier,))[0]
                descendants = owned_descendants(process.pid)
                page.get_by_alt_text('浏览器实时画面').wait_for(timeout=15000)
                # A visible read-only frame rejects user input without calling device IO.
                assert page.get_by_text('只读观察', exact=True).count() == 1
                page.get_by_role('button', name='开始接管', exact=True).click()
                page.get_by_text('人工控制', exact=True).wait_for(timeout=15000)
                surface = page.get_by_alt_text('浏览器实时画面').locator('..')
                box = surface.bounding_box()
                page.mouse.click(box['x'] + box['width'] * 140 / 1280, box['y'] + box['height'] * 120 / 720)
                surface.press('F')
                wait_for(lambda: {'kind': 'input', 'value': 'F'} in browser_human_page.observed_events(), timeout=10)
                page.mouse.click(box['x'] + box['width'] * 440 / 1280, box['y'] + box['height'] * 120 / 720)
                wait_for(lambda: {'kind': 'ready', 'value': True} in browser_human_page.observed_events(), timeout=10)
                page.set_viewport_size({'width': 390, 'height': 844})
                page.get_by_title('收起侧边栏', exact=True).click()
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1')
                page.get_by_role('button', name='完成并继续', exact=True).scroll_into_view_if_needed()
                page.screenshot(path=str(screenshot), full_page=True)
                # Actual current PG credit denies a new completion action; original Browser remains owned.
                service_database.rows('UPDATE tenants SET credit_balance=0 WHERE tenant_id=%s', (actor.tenant_id,))
                with page.expect_response(lambda response: response.request.method == 'POST'
                        and response.url.split('?', 1)[0].endswith('/complete')) as rejected:
                    page.get_by_role('button', name='完成并继续', exact=True).click()
                rejection_status = rejected.value.status
                assert rejection_status == 402
                page.get_by_text('暂时无法确认操作结果，请稍后重试。', exact=True).wait_for(timeout=10000)
                assert service_database.rows('SELECT completion_ref FROM bs_browser_assistance_requests WHERE assistance_id=%s',
                    (wait['assistance_id'],)) == [{'completion_ref': None}]
                assert runner(service_database, identifier)['status'] == 'waiting'
                assert service_database.rows('SELECT owner_runner_id FROM agent_runner_session_claims WHERE owner_runner_id=%s',
                    (identifier,)) == [{'owner_runner_id': identifier}]
                service_database.rows('UPDATE tenants SET credit_balance=1000 WHERE tenant_id=%s', (actor.tenant_id,))
                page.screenshot(path=str(screenshot.with_name('card-real-ui-rejected.png')), full_page=True)
                page.get_by_role('button', name='完成并继续', exact=True).click()
                value = {'identifier': identifier, 'marker': marker, 'browser': native, 'wait': wait, 'actor': actor,
                    'process': process, 'report': report, 'descendants': descendants}
                assert_finished(workers, service_database, value, browser_human_page)
                page.get_by_text('Human risk original Browser completed', exact=True).wait_for(timeout=15000)
                assert page.get_by_text('Human risk original Browser completed', exact=True).count() == 1
                assert page.get_by_alt_text('浏览器实时画面').count() == 0
                assert page.get_by_role('button', name='开始接管', exact=True).count() == 0
                assert textarea.is_enabled()
                assert len([event for event in observed if event[0] == 'POST' and event[1] == 'chat/runners']) == 1
                assert not any('continuations/' in event[1] for event in observed)
                # M7 旧缓存卡片门槛：原随机 bac 在 bind_wait 时持久写入 native wait 行
                # （独立于 completion 相位，终态清 Redis 后仍可辨认归属）。
                persisted = service_database.rows(
                    'SELECT continuation_id FROM bs_browser_assistance_requests WHERE assistance_id=%s',
                    (wait['assistance_id'],))[0]
                assert (persisted['continuation_id'] or '').startswith('bac_')
                assert service_database.rows(
                    'SELECT count(*) AS total FROM bs_browser_assistance_requests WHERE continuation_id=%s',
                    (persisted['continuation_id'],))[0]['total'] == 1
                # 旧 events 薄读桥：Redis 投影不存在时从 runner 持久事实补读终态与停止
                # 事件，纯只读、不入旧 jobs（新页路径未轮询 continuations/ 的断言见上）。
                replayed = httpx.get(
                    f"{api.url}/api/agent/continuations/{persisted['continuation_id']}/events",
                    headers={'Authorization': f'Bearer {actor.token}'}, timeout=10)
                assert replayed.status_code == 200
                replay_events = replayed.json()['events']
                assert [item['type'] for item in replay_events] == [
                    'browser_resume_started', 'response', 'agent_continuation_completed']
                assert service_database.rows(
                    'SELECT 1 FROM bs_browser_resume_jobs WHERE tenant_id=%s', (actor.tenant_id,)) == []
                facts = json.loads(report.read_text())
                assert facts['input_accepted'] >= 3 and facts['resume_calls'] == 1
                assert facts['same_page_ops'] and facts['same_raw_executor'] and facts['confirmation_added'] == 1
                assert decoded(runner(service_database, identifier)['public_snapshot'])['browserAssistance']['completion_status'] == 'closed'
                page.screenshot(path=str(screenshot.with_name('card-real-ui-terminal.png')), full_page=True)
                context.close()
            finally:
                browser.close()
    except Exception:
        rows = service_database.rows('SELECT status,attempt FROM agent_runners WHERE user_id=%s', (actor.user_id,))
        # Safe finite transport status summary, not raw response/URL or user details.
        print('SAFE_CARD_UI_DIAG=' + json.dumps({'runner_states': rows,
            'http_status_counts': {str(status): sum(item[2] == status for item in observed) for status in {item[2] for item in observed}},
            'runner_posts': sum(item[0] == 'POST' and item[1] == 'chat/runners' for item in observed)}))
        raise
    finally:
        if process is not None:
            workers.processes.stop(process)
        api.close()
        if identifier is not None:
            delete_browser_facts(service_database, identifier)
