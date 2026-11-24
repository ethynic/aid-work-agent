"""Actual SQL and HTTP workflow for one signed per-group summary and scheduled batches."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import json
import secrets

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.config.settings import settings
from src.db.database import get_db_connection
from src.local_tools import repository, weixin_summary, weixin_tracking as tracking
from src.local_tools import weixin_tracking_schedule as schedules
from src.local_tools.api import router
from src.local_tools.security import sha256_hex
from src.notifications.authorization import resource_in_tx
from src.notifications.contracts import NotificationError
from src.notifications.events import render_inbox_text, scheduled_result_link
from src.notifications.im_webhook import WecomBotAdapter
from src.notifications.repository import NotificationRepository, unprotect
from src.saas.middleware import TenantContextMiddleware
from tests.fixtures import runtime_plugin_registry

pytestmark = pytest.mark.integration
PATH = '/api/local-tools/weixin-agent'
RUNTIME = '/api/local-tools/runtime'


def _headers(actor):
    return {'Authorization': f'Bearer {actor.token}', 'X-Tenant-Id': actor.tenant_id}


@pytest.fixture
def web_client(monkeypatch):
    monkeypatch.setattr(settings.deployment, 'private_cloud', False)
    app = FastAPI()
    app.add_middleware(TenantContextMiddleware)
    app.include_router(router)
    with TestClient(app) as client:
        yield client


def _device(owner):
    token = secrets.token_urlsafe(32)
    device = repository.create_device(owner.tenant_id, owner.user_id, sha256_hex(token))
    return str(device['id']), {'Authorization': f'Bearer {token}'}


def _signed_device(client, owner, tmp_path, monkeypatch, tool=tracking.TOOL):
    monkeypatch.setattr(runtime_plugin_registry, 'PROVIDER', 'weixin')
    seal = runtime_plugin_registry.signed_registry(tmp_path)
    tool_def = runtime_plugin_registry.definition('target_name', name=tool)
    bundle = seal({**tool_def, 'annotations': {'readOnlyHint': True}})
    digest = bundle['envelope']['manifest_digest']
    monkeypatch.setattr(settings.agent_runner, 'plugin_trust_roots', str(tmp_path / 'trust-roots.json'))
    device_id, runtime_headers = _device(owner)
    assert repository.select_device(owner.tenant_id, owner.user_id, device_id)
    assert client.post(f'{RUNTIME}/heartbeat', headers=runtime_headers, json={
        'capabilities': {
            'capabilities': ['tool_declarations_v1'],
            'declared_tools': {'weixin': [tool]},
            'plugin_manifest_digests': {'weixin': digest},
        },
        'plugin_manifests': {'weixin': bundle},
    }).status_code == 200
    return device_id, runtime_headers, {'plugin_manifest_digests': {'weixin': digest}}


def _group(client, headers, name):
    response = client.post(f'{PATH}/groups', headers=headers, json={'name': name})
    assert response.status_code == 200, response.text
    return response.json()['id']


def _claim(client, headers, declaration):
    response = client.post(f'{RUNTIME}/claim?wait=0', headers=headers, json=declaration)
    assert response.status_code == 200, response.text
    call = response.json()
    assert call['tool_name'] == tracking.TOOL
    return call


def _started(client, call, headers):
    path = f"{RUNTIME}/invocations/{call['invocation_id']}"
    token = {'claim_token': call['claim_token']}
    assert client.post(path + '/started', headers=headers, json=token).status_code == 200
    return path, token


def test_multi_group_upload_attempts_and_group_history_isolation(
        actors, service_database, web_client, tmp_path, monkeypatch):
    client, owner = web_client, actors['a']
    headers = _headers(owner)
    device_id, runtime_headers, declaration = _signed_device(client, owner, tmp_path, monkeypatch)
    first = _group(client, headers, '合成产品群')
    second = _group(client, headers, '合成实施群')
    starting = {'device_id': device_id, 'group_ids': [first, second, first], 'preset': '3d'}
    assert client.post(f'{PATH}/groups', headers=headers, json={'name': '合成产品群'}).status_code == 409
    foreign = _group(client, _headers(actors['a_other']), '合成产品群')
    assert client.post(f'{PATH}/batches', headers=headers,
                       json={**starting, 'group_ids': [first, foreign]}).status_code == 404
    assert client.post(f'{PATH}/batches', headers=headers, json={
        **starting, 'preset': 'custom', 'start_at': '2020-01-01T00:00:00Z', 'end_at': '2020-01-01T01:00:00Z',
    }).status_code == 400
    other_device, _ = _device(owner)
    assert repository.select_device(owner.tenant_id, owner.user_id, other_device)
    stale = client.post(f'{PATH}/batches', headers=headers, json=starting)
    assert stale.status_code == 409 and stale.json()['detail']['code'] == 'DEVICE_NOT_SELECTED'
    assert repository.select_device(owner.tenant_id, owner.user_id, device_id)

    created = client.post(f'{PATH}/batches', headers=headers, json=starting)
    assert created.status_code == 200, created.text
    batch = created.json()
    assert len(batch['groups']) == 2 and batch['state'] == 'queued'
    batch_path = f"{PATH}/batches/{batch['id']}"
    for actor in (actors['a_other'], actors['b']):
        assert client.get(batch_path, headers=_headers(actor)).status_code == 404
    assert client.post(f'{batch_path}/retry/{first}', headers=headers).status_code == 409

    used_messages = []
    source = 'private synthetic body 7e553'
    summary = '确认合成里程碑'

    async def model(messages, tenant_id, user_id, *, for_range=False):
        used_messages.append((messages, tenant_id, user_id, for_range))
        return summary

    monkeypatch.setattr(weixin_summary, 'summarize', model)
    for _ in range(2):
        call = _claim(client, runtime_headers, declaration)
        arguments = call['arguments']
        assert 'allow_open_group' not in arguments
        is_first = arguments['target_name'] == '合成产品群'
        grant_header = {'Authorization': f"Bearer {arguments['grant']}"}
        upload_path = f'{RUNTIME}/weixin-summary'
        upload_body = {key: arguments[key] for key in ('tenant_id', 'start_at', 'end_at')}
        upload_body['messages'] = [source]
        assert client.post(upload_path, headers=grant_header, json=upload_body).status_code == 403
        path, token = _started(client, call, runtime_headers)
        if is_first:
            assert client.post(upload_path, headers=grant_header, json={
                **upload_body, 'end_at': arguments['start_at'],
            }).status_code == 403
            assert client.post(upload_path, headers=grant_header, json={
                'tenant_id': owner.tenant_id, 'business_date': '2026-11-24', 'messages': [source],
            }).status_code == 403
            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(lambda _: client.post(upload_path, headers=grant_header, json=upload_body), range(2)))
            assert sorted(response.status_code for response in responses) == [200, 403]
        receipt = {'summary': summary, 'message_count': 1, 'partial': False, 'messages': [source]}
        result = client.post(path + '/result', headers=runtime_headers, json={
            **token, 'success': is_first, 'code': 'OK' if is_first else 'TARGET_AMBIGUOUS',
            'effect': 'none', 'data': receipt,
        })
        assert result.status_code == 200, result.text
        stored = repository.get_invocation(call['invocation_id'], owner.tenant_id)
        assert source not in json.dumps(stored, default=str)
        assert client.get(f"{RUNTIME.split('/runtime')[0]}/weixin-summary/runs/{call['invocation_id']}",
                          headers=headers).status_code == 404

    original = client.get(batch_path, headers=headers).json()
    assert original['state'] == 'partial'
    assert [(group['group_id'], group['state']) for group in original['groups']] == [(first, 'succeeded'), (second, 'failed')]
    assert original['groups'][0]['summary'] == summary
    assert original['groups'][1]['error_code'] == 'TARGET_AMBIGUOUS'
    assert used_messages == [([source], owner.tenant_id, owner.user_id, True)]
    assert client.post(f'{batch_path}/retry/{first}', headers=headers).status_code == 409

    # Renaming a saved group and changing the global selection do not retarget the retry.
    assert client.put(f'{PATH}/groups/{second}', headers=headers, json={'name': '新实施群'}).status_code == 200
    assert repository.select_device(owner.tenant_id, owner.user_id, other_device)
    retry = client.post(f'{batch_path}/retry/{second}', headers=headers)
    assert retry.status_code == 200, retry.text
    repeated = retry.json()
    assert repeated['id'] != original['id'] and repeated['retry_batch_id'] == original['id']
    assert (repeated['start_at'], repeated['end_at']) == (original['start_at'], original['end_at'])
    assert repeated['device_id'] == device_id and [group['name'] for group in repeated['groups']] == ['合成实施群']
    assert client.delete(f'{PATH}/groups/{second}', headers=headers).status_code == 204
    history = client.get(f'{PATH}/batches', params={'group_id': second}, headers=headers).json()['batches']
    assert [item['id'] for item in history] == [repeated['id'], original['id']]
    assert len(history[1]['groups']) == 2 and 'summary' not in history[1]['groups'][0]
    assert client.get(batch_path, headers=headers).json() == original
    retry_args = service_database.rows("""SELECT arguments_json FROM local_tool_invocations
        WHERE id=(SELECT (groups_json->0->>'invocation_id')::uuid FROM bs_weixin_agent_batches
        WHERE id=%s)""", (repeated['id'],))[0]['arguments_json']
    assert retry_args['allow_open_group'] is True
    service_database.rows("""UPDATE local_tool_invocations SET deadline_at=clock_timestamp() - interval '1 second'
                              WHERE id=(SELECT (groups_json->0->>'invocation_id')::uuid FROM bs_weixin_agent_batches
                                        WHERE id=%s)""", (repeated['id'],))
    schedules.run_tick()
    retry_result = client.get(f"{PATH}/batches/{repeated['id']}", headers=headers).json()
    assert retry_result['groups'][0]['state'] == 'expired'
    assert not service_database.rows("""SELECT 1 FROM local_tool_invocations
        WHERE id=(SELECT (groups_json->0->>'invocation_id')::uuid FROM bs_weixin_agent_batches
        WHERE id=%s) AND arguments_json ? 'grant'""", (repeated['id'],))


def test_schedules_are_bound_to_device_and_notification_is_single_owner_only_link(
        actors, service_database, web_client, monkeypatch):
    client, owner = web_client, actors['a']
    headers = _headers(owner)
    first = _group(client, headers, '不对外显示的合成群')
    device_id, _ = _device(owner)
    assert repository.select_device(owner.tenant_id, owner.user_id, device_id)
    definition = {'device_id': device_id, 'title': '研发周报', 'group_ids': [first], 'frequency': 'weekly',
                  'weekday': 1, 'hour': 18, 'minute': 30, 'preset': '24h'}
    created = client.post(f'{PATH}/schedules', headers=headers, json=definition)
    assert created.status_code == 200, created.text
    planned = created.json()
    assert planned['notify'] is False and planned['notification_rule_id'] is None
    planned_dt = datetime.fromisoformat(planned['next_due_at']).astimezone(tracking.BEIJING)
    assert (planned_dt.weekday(), planned_dt.hour, planned_dt.minute) == (1, 18, 30)
    path = f"{PATH}/schedules/{planned['id']}"
    assert client.patch(path, headers=_headers(actors['a_other']), json={'paused': True}).status_code == 404
    paused = client.patch(path, headers=headers, json={'paused': True}).json()
    assert paused['paused'] and paused['next_due_at'] is None and paused['preview_at']
    edit = client.put(path, headers=headers, json={**definition, 'title': '合成任务', 'notify': True})
    assert edit.status_code == 200 and edit.json()['paused']
    assert client.patch(path, headers=headers, json={'paused': False}).json()['next_due_at']
    service_database.rows('UPDATE bs_weixin_agent_schedules SET next_due_at=clock_timestamp() - interval \'1 second\' WHERE id=%s',
                          (planned['id'],))

    service_database.rows("UPDATE local_tool_devices SET last_seen_at=clock_timestamp()::timestamp - interval '2 minutes' WHERE id=%s",
                          (device_id,))
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: schedules.run_tick(), range(2)))
    history = client.get(f'{PATH}/batches', headers=headers).json()['batches']
    assert len(history) == 1
    batch = history[0]
    assert batch['source'] == 'schedule' and batch['state'] == 'failed' and batch['device_id'] == device_id
    assert batch['groups'][0]['error_code'] == 'DEVICE_OFFLINE'
    assert datetime.fromisoformat(batch['end_at']) - datetime.fromisoformat(batch['start_at']) == timedelta(days=1)
    rows = service_database.rows('''SELECT * FROM notification_messages WHERE tenant_id=%s AND recipient_user_id=%s
                                  AND producer='weixin-agent-scheduler' ''', (owner.tenant_id, owner.user_id))
    assert len(rows) == 1
    notification = dict(rows[0])
    parameters = unprotect(notification['parameters_encrypted'])
    assert render_inbox_text(notification['event_type'], parameters) == ('合成任务', '执行失败')
    assert '不对外显示' not in json.dumps(parameters, ensure_ascii=False)
    monkeypatch.setattr(settings.app, 'public_base_url', 'https://public.example.test')
    link = scheduled_result_link({**notification, 'parameters': parameters})
    assert link.startswith('https://public.example.test/notification?ref=')
    assert '/share/' not in link and owner.tenant_id in link
    with get_db_connection() as conn:
        cursor = conn.cursor()
        assert resource_in_tx(cursor, notification)['batch_id'] == batch['id']
        with pytest.raises(NotificationError):
            resource_in_tx(cursor, {**notification, 'recipient_user_id': actors['a_other'].user_id})



def test_completion_only_robot_rule_expands_partial_once_and_skips_failed_batches(
        actors, service_database, web_client, tmp_path, monkeypatch):
    client, owner = web_client, actors['a']
    headers = _headers(owner)
    device_id, runtime_headers, declaration = _signed_device(client, owner, tmp_path, monkeypatch)
    groups = [_group(client, headers, '群名不进入通知'), _group(client, headers, '第二合成测试群')]
    rule = 'weixin-rule-test-' + owner.tenant_id
    rejected = rule + '-paused-only'
    recipient = 'tenant:' + owner.tenant_id
    for identifier, conditions in ((rule, '{"events":["task.completed"]}'), (rejected, '{"events":["task.paused"]}')):
        service_database.rows("""INSERT INTO notification_rules
            (rule_id,tenant_id,event_type,name,recipient_mode,conditions)
            VALUES (%s,%s,'scheduled.task',%s,'task_owner',%s::jsonb)""",
            (identifier, owner.tenant_id, identifier, conditions))
    for identifier, channel, provider, rule_id in (
        (rule + '-robot', 'im_webhook', 'wecom_bot', rule),
        (rule + '-email', 'email', 'smtp', rule),
        (rejected + '-robot', 'im_webhook', 'wecom_bot', rejected),
    ):
        service_database.rows("""INSERT INTO notification_targets
                (target_id,tenant_id,scope_key,channel,provider,rule_id)
                VALUES (%s,%s,%s,%s,%s,%s)""",
            (identifier, owner.tenant_id, recipient, channel, provider, rule_id))

    available = client.get(f'{PATH}/notification-rules', headers=headers).json()
    assert [item['rule_id'] for item in available] == [rule]
    assert available[0]['channels'] == ['wecom_bot']
    bad = {'device_id': device_id, 'title': '合成研发提醒', 'group_ids': groups, 'preset': '24h',
           'frequency': 'daily', 'hour': 17, 'minute': 0, 'notify': True, 'notification_rule_id': rejected}
    assert client.post(f'{PATH}/schedules', headers=headers, json=bad).status_code == 409
    created = client.post(f'{PATH}/schedules', headers=headers, json={**bad, 'notification_rule_id': rule})
    assert created.status_code == 200
    scheduled_id = created.json()['id']
    service_database.rows("UPDATE bs_weixin_agent_schedules SET next_due_at=clock_timestamp() - interval '1 second' WHERE id=%s",
                          (scheduled_id,))
    schedules.run_tick()
    for success in (True, False):
        call = _claim(client, runtime_headers, declaration)
        assert 'allow_open_group' not in call['arguments']
        path, token = _started(client, call, runtime_headers)
        reply = client.post(path + '/result', headers=runtime_headers, json={
            **token, 'success': success, 'code': 'OK' if success else 'TARGET_AMBIGUOUS', 'effect': 'none',
            'data': {'summary': None, 'message_count': 0, 'partial': False} if success else {},
        })
        assert reply.status_code == 200, reply.text
    schedules.run_tick()

    monkeypatch.setattr(settings.app, 'public_base_url', 'https://public.example.test')

    class Registry:
        def get(self, channel, provider):
            assert (channel, provider) == ('im_webhook', 'wecom_bot')
            return WecomBotAdapter()

    NotificationRepository(settings.notifications, Registry()).expand()
    deliveries = service_database.rows("""SELECT d.target_id,d.state,d.rendered_encrypted,n.notification_ref,n.event_type
        FROM notification_deliveries d JOIN notification_messages n USING (notification_id)
        WHERE n.tenant_id=%s AND n.producer='weixin-agent-scheduler'""", (owner.tenant_id,))
    assert len(deliveries) == 1 and deliveries[0]['state'] == 'pending'
    assert deliveries[0]['target_id'] == rule + '-robot'
    display = unprotect(deliveries[0]['rendered_encrypted'])['text']
    assert '合成研发提醒' in display and '部分完成' in display
    assert deliveries[0]['event_type'] == 'task.completed'
    assert '群名不进入通知' not in display
    assert f"https://public.example.test/notification?ref={deliveries[0]['notification_ref']}&tenant={owner.tenant_id}" in display

    service_database.rows("UPDATE local_tool_devices SET last_seen_at=clock_timestamp()::timestamp - interval '2 minutes' WHERE id=%s",
                          (device_id,))
    service_database.rows("UPDATE bs_weixin_agent_schedules SET next_due_at=clock_timestamp() - interval '1 second' WHERE id=%s",
                          (scheduled_id,))
    schedules.run_tick()
    history = client.get(f'{PATH}/batches', headers=headers).json()['batches']
    assert [batch['state'] for batch in history] == ['failed', 'partial']
    assert history[0]['notification_status'] == 'skipped'
    messages = service_database.rows("""SELECT event_type FROM notification_messages WHERE tenant_id=%s
        AND producer='weixin-agent-scheduler'""", (owner.tenant_id,))
    assert [row['event_type'] for row in messages] == ['task.completed']



def test_legacy_today_run_is_discoverable_as_an_owner_scoped_single_group_history_row(
        actors, service_database, web_client, tmp_path, monkeypatch):
    client, owner = web_client, actors['a']
    headers = _headers(owner)
    device_id, _, _ = _signed_device(client, owner, tmp_path, monkeypatch, tool=weixin_summary.TOOL)
    tracked = _group(client, headers, '旧记录合成群')
    path = '/api/local-tools/weixin-summary/runs'
    created = client.post(path, headers=headers, json={
        'device_id': device_id, 'confirmed': True, 'target_name': '旧记录合成群',
    })
    assert created.status_code == 200, created.text
    run_id = created.json()['id']
    history = client.get(f'{PATH}/batches', headers=headers).json()['batches']
    assert [row['id'] for row in history] == [run_id]
    assert history[0]['legacy'] is True and len(history[0]['groups']) == 1
    assert history[0]['groups'][0]['name'] == '旧记录合成群'
    assert client.get(path + '/' + run_id, headers=headers).status_code == 200
    assert not client.get(f'{PATH}/batches', headers=headers, params={'group_id': tracked}).json()['batches']
    assert not client.get(f'{PATH}/batches', headers=_headers(actors['a_other'])).json()['batches']



def test_signed_schedule_summarizes_at_the_planned_time_with_its_original_device(
        actors, service_database, web_client, tmp_path, monkeypatch):
    client, owner = web_client, actors['a']
    headers = _headers(owner)
    device_id, runtime_headers, declaration = _signed_device(client, owner, tmp_path, monkeypatch)
    first = _group(client, headers, '保留合成群')
    removed = _group(client, headers, '移除合成群')
    result = client.post(f'{PATH}/schedules', headers=headers, json={
        'device_id': device_id, 'title': '定时验收', 'group_ids': [first, removed],
        'preset': '24h', 'frequency': 'daily', 'hour': 18, 'minute': 0,
    })
    assert result.status_code == 200, result.text
    assert client.delete(f'{PATH}/groups/{removed}', headers=headers).status_code == 204
    other_device, _ = _device(owner)
    assert repository.select_device(owner.tenant_id, owner.user_id, other_device)
    persisted = service_database.rows("""UPDATE bs_weixin_agent_schedules
        SET next_due_at=date_trunc('second',clock_timestamp()-interval '5 seconds') WHERE id=%s
        RETURNING next_due_at""", (result.json()['id'],))[0]['next_due_at']
    schedules.run_tick()
    claimed = _claim(client, runtime_headers, declaration)
    assert claimed['arguments']['target_name'] == '保留合成群'
    assert datetime.fromisoformat(claimed['arguments']['end_at']) == persisted
    assert datetime.fromisoformat(claimed['arguments']['start_at']) == persisted - timedelta(hours=24)
    path, token = _started(client, claimed, runtime_headers)
    reply = client.post(path + '/result', headers=runtime_headers, json={
        **token, 'success': True, 'code': 'OK', 'effect': 'none',
        'data': {'summary': None, 'message_count': 0, 'partial': False},
    })
    assert reply.status_code == 200, reply.text
    schedules.run_tick()
    batch = client.get(f'{PATH}/batches', headers=headers).json()['batches'][0]
    result = client.get(f"{PATH}/batches/{batch['id']}", headers=headers).json()
    assert result['groups'] == [{'group_id': first, 'name': '保留合成群', 'state': 'succeeded',
                                 'summary': None, 'message_count': 0, 'partial': False}]
    assert result['device_id'] == device_id and result['state'] == 'succeeded'
    assert 'notification_status' not in result
    assert not service_database.rows("""SELECT 1 FROM notification_messages
        WHERE tenant_id=%s AND producer='weixin-agent-scheduler'""", (owner.tenant_id,))
