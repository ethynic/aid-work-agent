"""WeCom settings use authenticated ownership and a fresh directory scope."""

from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from loguru import logger
from psycopg2.extras import Json

from src.api import scheduled_task
from src.db.database import get_db_connection
from src.local_tools import repository
from src.local_tools.proxy_tool import WecomSessionDirectoryTool
from src.saas.api import billing_balance
from src.saas.middleware import TenantContextMiddleware
from src.scheduler.db import ScheduledTaskDB
from src.wecom_conversation import scheduled_api, scheduled_delivery, scheduled_sends, service

from .test_proxy_tasks_service import ACCOUNT, _locator

BASE = '/api/wecom-conversations/scheduled-sends'
PRICE = '/api/saas/billing/wecom-scheduled-send-price'
FUTURE = '2099-11-17 09:00:00'


def _run(sql, args=()):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, args)
        result = cursor.fetchall() if cursor.description else None
        conn.commit()
        return result


def _authenticated(tenant_id, role):
    uid, token = str(uuid4()), str(uuid4())
    _run('INSERT INTO users (user_id,tenant_id,role) VALUES (%s,%s,%s)', (uid, tenant_id, role))
    _run("INSERT INTO tokens (user_id,token,expires_at) VALUES (%s,%s,NOW()+INTERVAL '8 days')", (uid, token))
    return uid, {'Authorization': f'Bearer {token}'}


def _write(device, **overrides):
    return {'name': '晨间通知', 'text': '  第一行\n第二行 ', 'sessions': [_locator(1), _locator(2)],
            'schedule_type': 'once', 'time_config': {'run_at': FUTURE},
            'expected_device_id': device, 'expected_source_generation': 'gen-a', **overrides}


@pytest.fixture
def bound_api(db_ready, tenant_id, monkeypatch):
    _run("INSERT INTO tenants (tenant_id,tenant_type) VALUES (%s,'real')", (tenant_id,))
    user, headers = _authenticated(tenant_id, 'tenant_admin')
    _, peer_headers = _authenticated(tenant_id, 'user')
    admin, admin_headers = _authenticated(None, 'platform_admin')
    device, other = str(uuid4()), str(uuid4())
    capabilities = Json({'providers': ['wecom'], 'capabilities': ['wecom_session_directory']})
    for device_id, name, selected in [(device, '固定设备', True), (other, '另一设备', False)]:
        _run('''INSERT INTO local_tool_devices (id,tenant_id,user_id,name,token_hash,capabilities_json,selected,last_seen_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,NOW())''', (device_id, tenant_id, user, name, device_id, capabilities, selected))
    source = {'account': ACCOUNT, 'generation': 'gen-a', 'calls': []}

    async def directory(self, tenant, uid, session, selected, arguments, progress):
        source['calls'].append((tenant, uid, str(selected['id']), arguments))
        return {'success': True, 'data': {'schema_version': 1, 'self_user_id': source['account'],
            'source_generation': source['generation'], 'resolved_targets': [
                {'session': loc, 'name': f'会话{index + 1}', 'kind': 'group' if index == 0 else 'single', 'peer_platform': 'wecom'}
                for index, loc in enumerate(arguments['sessions'])]}}

    monkeypatch.setattr(WecomSessionDirectoryTool, '_dispatch_and_wait', directory)
    app = FastAPI()
    app.add_middleware(TenantContextMiddleware)
    app.include_router(scheduled_api.router)
    app.include_router(scheduled_task.router)
    app.include_router(billing_balance.router)
    yield TestClient(app), user, headers, peer_headers, admin_headers, device, other, source
    scheduled_delivery.set_unit_price(None)
    _run('DELETE FROM local_tool_devices WHERE tenant_id=%s', (tenant_id,))
    _run('DELETE FROM tokens WHERE user_id=%s OR user_id IN (SELECT user_id FROM users WHERE tenant_id=%s)', (admin, tenant_id))
    _run('DELETE FROM users WHERE user_id=%s', (admin,))
    _run('DELETE FROM tenants WHERE tenant_id=%s', (tenant_id,))


def _create(client, headers, device):
    response = client.post(BASE, headers=headers, json=_write(device))
    assert response.status_code == 201, response.text
    return response.json()['data']['plan']


def test_plan_binds_resolved_source_starts_paused_and_is_isolated_from_ordinary_tasks(bound_api, tenant_id):
    client, user, headers, peer_headers, _, device, _, source = bound_api
    plan = _create(client, headers, device)
    task_id = plan['task_id']
    assert plan['status'] == 'paused' and plan['next_due_at'] is None
    assert (plan['device_id'], plan['account'], plan['device_name'], plan['text']) == (device, ACCOUNT, '固定设备', _write(device)['text'])
    assert plan['session_names'] == {_locator(1): '会话1', _locator(2): '会话2'}
    assert source['calls'] == [(tenant_id, user, device, {'mode': 'resolve', 'sessions': [_locator(1), _locator(2)]})]
    assert client.get(BASE + '/' + task_id, headers=peer_headers).status_code == 404
    assert client.get(BASE, headers=peer_headers).json()['data']['plans'] == []
    assert client.get(BASE, headers={**headers, 'X-Tenant-Id': 'other-tenant'}).status_code == 403
    forbidden = client.get('/api/scheduled-tasks/' + task_id, headers=headers)
    assert forbidden.status_code == 409 and '企业微信定时消息发送' in forbidden.json()['detail']
    assert client.get('/api/scheduled-tasks', headers=headers).json()['data'] == {'tasks': [], 'total': 0}
    activated = client.post(BASE + '/' + task_id + '/enable', headers=headers)
    assert activated.json()['data']['plan']['status'] == 'active'
    assert client.patch(BASE + '/' + task_id, headers=headers, json=_write(device)).status_code == 409
    assert client.post(BASE + '/' + task_id + '/pause', headers=headers).json()['data']['plan']['status'] == 'paused'


@pytest.mark.parametrize('problem,expected', [('generation', 'IDENTITY_UNCONFIRMED'), ('selection', 'DEVICE_MISMATCH')])
def test_create_rejects_changed_directory_without_enrolling_any_conversation(bound_api, tenant_id, monkeypatch, problem, expected):
    client, user, headers, _, _, device, other, source = bound_api
    if problem == 'generation':
        source['generation'] = 'gen-b'
    else:
        save_plan = scheduled_sends.create_plan

        def switch_after_resolve(*args, **kwargs):
            assert repository.select_device(tenant_id, user, other)
            return save_plan(*args, **kwargs)

        monkeypatch.setattr(scheduled_sends, 'create_plan', switch_after_resolve)
    res = client.post(BASE, headers=headers, json=_write(device))
    assert res.status_code == 409 and res.json()['code'] == expected
    assert client.get(BASE, headers=headers).json()['data']['plans'] == []
    assert service.list_conversations(tenant_id, user) == []


def test_edit_preserves_original_binding_even_when_selections_change(bound_api, tenant_id):
    client, user, headers, _, _, device, other, source = bound_api
    plan = _create(client, headers, device)
    endpoint = BASE + '/' + plan['task_id']
    assert repository.select_device(tenant_id, user, other)
    _run('UPDATE bs_wecom_conversations SET device_id=%s WHERE tenant_id=%s AND user_id=%s AND session_locator=%s',
         (other, tenant_id, user, _locator(1)))
    unchanged = _write(device, text='  新正文\n ', time_config={'run_at': '2099-11-17 11:00:00'})
    unchanged.pop('expected_device_id')
    unchanged.pop('expected_source_generation')
    updated = client.patch(endpoint, headers=headers, json=unchanged).json()['data']['plan']
    assert updated['text'] == unchanged['text'] and updated['device_id'] == device
    assert len(source['calls']) == 1
    addition = _write(device, sessions=[_locator(2), _locator(3)])
    assert client.patch(endpoint, headers=headers, json=addition).json()['code'] == 'DEVICE_MISMATCH'
    assert repository.select_device(tenant_id, user, device)
    source['account'] = 'new-account'
    assert client.patch(endpoint, headers=headers, json=addition).json()['code'] == 'IDENTITY_UNCONFIRMED'
    source['account'] = ACCOUNT
    assert client.patch(endpoint, headers=headers, json=addition).json()['data']['plan']['sessions'] == addition['sessions']
    spoofed = {**addition, 'device_id': other, 'account': 'new-account'}
    assert client.patch(endpoint, headers=headers, json=spoofed).status_code == 422


def test_deleted_plan_history_keeps_diagnostic_even_when_no_device_call_exists(bound_api):
    client, _, headers, peer_headers, _, device, _, _ = bound_api
    plan = _create(client, headers, device)
    task_id = plan['task_id']
    assert client.post(f'{BASE}/{task_id}/enable', headers=headers).status_code == 200
    messages = []
    handler = logger.add(messages.append)
    try:
        ScheduledTaskDB.claim_due(task_id, now=datetime.fromisoformat(FUTURE) + timedelta(minutes=11))
    finally:
        logger.remove(handler)
    assert client.delete(BASE + '/' + task_id, headers=headers).status_code == 200
    assert client.get(BASE, headers=headers).json()['data']['plans'] == []
    assert client.get(BASE + '/' + task_id, headers=headers).status_code == 404
    history = client.get(BASE + '/executions', headers=headers, params={'task_id': task_id}).json()['data']['executions']
    assert len(history) == 2 and all(row['status'] == 'expired' and row['started_at'] is None for row in history)
    assert {row['session_name'] for row in history} == {'会话1', '会话2'}
    assert all(row['diagnostic_id'] == str(row['id']) and '10 分钟' in row['reason'] for row in history)
    assert all(any(f'execution={row["diagnostic_id"]} ' in msg for msg in messages) for row in history)
    assert client.get(BASE + '/executions', headers=peer_headers).json()['data']['executions'] == []


def test_execution_history_explains_known_failures_without_returning_device_payload(bound_api):
    client, _, headers, _, _, device, _, _ = bound_api
    plan = _create(client, headers, device)
    task_id = plan['task_id']
    assert client.post(f'{BASE}/{task_id}/enable', headers=headers).status_code == 200
    ScheduledTaskDB.claim_due(task_id, now=datetime.fromisoformat(FUTURE))
    for code, reason in {
        'DESKTOP_RESOURCE_BUSY': '设备桌面正忙',
        'TARGET_AMBIGUOUS': '匹配到多个会话，无法确认目标',
        'LOCALDB_UNAVAILABLE': '企业微信本地会话库不可用',
        'KEY_SCAN_FAILED': '无法解锁企业微信本地会话库',
        'WINDOW_AMBIGUOUS': '无法确定企业微信主窗口',
        'CONTENT_UNAVAILABLE': '无法读取发送所需的会话或窗口信息',
    }.items():
        _run("UPDATE bs_wecom_scheduled_sends SET status='failed',error_code=%s WHERE task_id=%s", (code, task_id))
        response = client.get(BASE + '/executions', headers=headers, params={'task_id': task_id})
        rows = response.json()['data']['executions']
        assert len(rows) == 2 and all(row['reason'] == reason for row in rows)
        assert all(not {'text', 'error_message', 'result_json'} & row.keys() for row in rows)


def test_price_is_exact_reversible_and_platform_admin_only(bound_api):
    client, _, tenant_admin, tenant_user, platform_admin, *_ = bound_api
    assert client.get(PRICE).status_code == 401
    for headers in (tenant_admin, tenant_user):
        assert client.get(PRICE, headers=headers).status_code == 403
        assert client.put(PRICE, headers=headers, json={'unit_price': '0.20'}).status_code == 403
    assert client.get(PRICE, headers=platform_admin).json()['data'] == {
        'unit_price': '0.50', 'default_price': '0.50', 'overridden': False}
    for bad in (0.5, '1.001', '-1', '1e2'):
        assert client.put(PRICE, headers=platform_admin, json={'unit_price': bad}).status_code == 422
    for value, expected in [('0', '0.00'), ('0.50', '0.50'), ('2.20', '2.20')]:
        res = client.put(PRICE, headers=platform_admin, json={'unit_price': value})
        assert res.json()['data']['unit_price'] == expected
        assert res.json()['data']['overridden'] is True
    reset = client.put(PRICE, headers=platform_admin, json={'unit_price': None}).json()['data']
    assert reset == client.get(PRICE, headers=platform_admin).json()['data'] == {
        'unit_price': '0.50', 'default_price': '0.50', 'overridden': False}
