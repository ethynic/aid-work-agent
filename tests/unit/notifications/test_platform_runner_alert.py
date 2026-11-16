"""Platform Runner alerts must never route through tenant notification targets."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.runner_alert_targets import create_router
from src.notifications.contracts import (AdapterCapabilities, NotificationError,
                                         PLATFORM_RUNNER_POLICY_KEY, Subject)
from src.notifications.im_webhook import FeishuBotAdapter
from src.notifications.producer import publish_platform_runner_alert_in_tx
from src.notifications.repository import NotificationRepository


NOW = datetime(2026, 11, 6, 11, tzinfo=timezone.utc)
PARAMETERS = {'runner_id': 'runner_1', 'tenant_id': 'tenant_1',
              'profile_id': 'profile_1', 'duration_seconds': 1800}


def runner():
    return {'runner_id': 'runner_1', 'attempt': 2, 'tenant_id': 'tenant_1',
            'profile_id': 'profile_1', 'execution_started_at': NOW}


def event(**changes):
    result = {'notification_id': 'notice_1', 'notification_ref': 'ref_1',
              'scope_key': 'global', 'tenant_id': None, 'recipient_user_id': None,
              'producer': 'runner-monitor', 'event_type': 'runner.long_running',
              'policy_key': PLATFORM_RUNNER_POLICY_KEY, 'template_key': 'runner_long_running',
              'schema_version': 1, 'resource_ref': {'type': 'runner_alert', 'id': 'runner_1'},
              'parameters_encrypted': 'sealed_params', 'expires_at': NOW + timedelta(days=1),
              'database_now': NOW, 'category': 'system'}
    result.update(changes)
    return result


def target(**changes):
    result = {'target_id': 'target_platform', 'scope_key': 'global', 'tenant_id': None,
              'recipient_user_id': None, 'channel': 'im_webhook', 'provider': 'feishu_bot',
              'installation_ref': 'runner_alert_platform', 'rule_id': None,
              'state': 'active', 'binding_version': 1,
              'configuration_encrypted': 'sealed_config'}
    result.update(changes)
    return result


def repository(monkeypatch):
    from src.notifications import repository as module
    monkeypatch.setattr(module, 'unprotect', lambda value: {
        'sealed_params': PARAMETERS, 'sealed_config': {'webhook_url': 'https://open.feishu.cn/hook'},
        'sealed_payload': {'text': 'rendered'}}[value])
    monkeypatch.setattr(module, 'protected', lambda value: 'sealed_payload')
    adapter = MagicMock()
    adapter.available.return_value = True
    adapter.capabilities = AdapterCapabilities()
    adapter.render.return_value = {'text': 'rendered'}
    # 平台飞书 target 是全局渠道（非租户自备）：删除 MagicMock 自动生成的
    # available_for_tenant，让 repository.provider_available 走全局 available
    del adapter.available_for_tenant
    repo = NotificationRepository.__new__(NotificationRepository)
    repo.registry = MagicMock()
    repo.registry.get.return_value = adapter
    repo.config = SimpleNamespace(batch_size=10)
    return repo, adapter


def test_publish_uses_caller_cursor_and_global_null_identity(monkeypatch):
    from src.notifications import repository as module
    monkeypatch.setattr(module, 'protected', lambda value: 'encrypted')
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        {'status': 'running', 'attempt': 2, 'tenant_id': 'tenant_1',
         'profile_id': 'profile_1', 'execution_started_at': NOW},
        {'now': NOW}, {'notification_ref': 'ref_1'},
    ]
    assert publish_platform_runner_alert_in_tx(cursor, runner=runner(), duration_seconds=1800) == 'ref_1'
    statements = [call.args[0] for call in cursor.execute.call_args_list]
    assert 'FOR UPDATE' in statements[0]
    assert "'global',NULL,NULL" in statements[2]
    assert 'ON CONFLICT(scope_key,producer,event_key)' in statements[2]
    assert 'runner_1:2' in cursor.execute.call_args_list[2].args[1]


def test_publish_rejects_stale_attempt_before_creating_event():
    cursor = MagicMock()
    cursor.fetchone.return_value = {'status': 'completed', 'attempt': 2,
        'tenant_id': 'tenant_1', 'profile_id': 'profile_1', 'execution_started_at': NOW}
    with pytest.raises(ValueError, match='RUNNER_ALERT_NOT_ACTIVE'):
        publish_platform_runner_alert_in_tx(cursor, runner=runner(), duration_seconds=1800)
    assert cursor.execute.call_count == 1


def test_repeated_publish_returns_original_notification_ref(monkeypatch):
    from src.notifications import repository as module
    monkeypatch.setattr(module, 'protected', lambda value: 'encrypted')
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        {'status': 'running', 'attempt': 2, 'tenant_id': 'tenant_1',
         'profile_id': 'profile_1', 'execution_started_at': NOW},
        {'now': NOW}, None, {'notification_ref': 'original_ref'},
    ]
    assert publish_platform_runner_alert_in_tx(cursor, runner=runner(),
                                               duration_seconds=1800) == 'original_ref'
    assert cursor.execute.call_count == 4


@pytest.mark.parametrize('selected,expected', [
    (None, 'platform_target_missing'),
    (target(scope_key='tenant:tenant_1', tenant_id='tenant_1'), 'platform_target_invalid'),
])
def test_expand_never_routes_missing_or_tenant_target(monkeypatch, selected, expected):
    repo, adapter = repository(monkeypatch)
    cursor = MagicMock()
    cursor.fetchone.return_value = selected
    state, route = repo._expand_platform_runner_alert(cursor, event())
    assert state == 'expanded'
    assert route == [{'skipped': expected}]
    assert not any('INSERT INTO notification_deliveries' in call.args[0]
                   for call in cursor.execute.call_args_list)
    adapter.render.assert_not_called()


def test_expand_routes_one_fixed_global_target(monkeypatch):
    repo, adapter = repository(monkeypatch)
    cursor = MagicMock()
    cursor.fetchone.return_value = target()
    state, route = repo._expand_platform_runner_alert(cursor, event())
    assert state == 'expanded'
    assert route == [{'target_id': 'target_platform', 'channel': 'im_webhook',
                      'provider': 'feishu_bot', 'binding_version': 1}]
    assert sum('INSERT INTO notification_deliveries' in call.args[0]
               for call in cursor.execute.call_args_list) == 1
    adapter.render.assert_called_once()


@pytest.mark.parametrize('message,selected', [
    (event(), target(scope_key='tenant:tenant_1', tenant_id='tenant_1')),
    (event(policy_key='rule_event', tenant_id='tenant_1', scope_key='tenant:tenant_1'), target()),
])
def test_prepare_send_rejects_platform_tenant_cross_route(monkeypatch, message, selected):
    repo, adapter = repository(monkeypatch)
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        {'binding_version': 1, 'rendered_encrypted': 'sealed_payload'}, message, selected,
    ]
    connection = MagicMock()
    connection.cursor.return_value = cursor
    @contextmanager
    def factory():
        yield connection
    repo.connection_factory = factory
    claim = {'delivery_id': 'd1', 'owner': 'owner', 'claim_version': 1,
             'notification_id': 'notice_1', 'target_id': 'target_platform'}
    assert repo.prepare_send(claim) is None
    assert any(call.args[1][1] == 'resource_revoked' for call in cursor.execute.call_args_list
               if 'UPDATE notification_deliveries SET state' in call.args[0])
    adapter.send.assert_not_called()


def test_prepare_send_accepts_only_matching_platform_pair(monkeypatch):
    repo, adapter = repository(monkeypatch)
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        {'binding_version': 1, 'rendered_encrypted': 'sealed_payload'},
        event(), target(),
    ]
    connection = MagicMock()
    connection.cursor.return_value = cursor
    @contextmanager
    def factory():
        yield connection
    repo.connection_factory = factory
    claim = {'delivery_id': 'd1', 'owner': 'owner', 'claim_version': 1,
             'notification_id': 'notice_1', 'target_id': 'target_platform'}
    prepared = repo.prepare_send(claim)
    assert prepared[0] is adapter
    assert prepared[1] == {'webhook_url': 'https://open.feishu.cn/hook'}
    assert prepared[2] == {'text': 'rendered'}
    assert prepared[3] > 0


def test_feishu_text_contains_only_allowed_runner_fields(monkeypatch):
    from src.config.settings import settings
    monkeypatch.setattr(settings.app, 'public_base_url', 'https://portal.example', raising=False)
    message = event(parameters=PARAMETERS)
    payload = FeishuBotAdapter().render(message, target())
    text = payload['text']
    for value in ('runner_1', 'tenant_1', 'profile_1', '30 分钟',
                  'https://portal.example/portal/runner-alerts?runner_id=runner_1'):
        assert value in text
    assert '原问题' not in text
    assert 'user_id' not in text


def test_alert_parameters_accept_load_class_without_pool_label():
    """池路由退役（2026-11-13 §9）：历史 load_class 参数仍可校验通过，但不再渲染负载池标签。"""
    from src.notifications.events import EVENTS, render_message_text
    runner = {'runner_id': 'runner_1', 'attempt': 1, 'tenant_id': 'tenant_1',
              'profile_id': 'profile_1', 'load_class': 'heavy'}
    _, parameters = EVENTS.validate('runner.long_running', {
        'runner_id': runner['runner_id'], 'tenant_id': runner['tenant_id'],
        'profile_id': runner['profile_id'], 'duration_seconds': 1800,
        'load_class': runner['load_class'],
    }, {'type': 'runner_alert', 'id': 'runner_1'})
    assert parameters['load_class'] == 'heavy'
    _, body = render_message_text('runner.long_running', parameters)
    assert '负载池' not in body
    _, light_body = render_message_text('runner.long_running',
                                        {**parameters, 'load_class': 'light'})
    assert '负载池' not in light_body


def test_feishu_alert_requires_usable_detail_link(monkeypatch):
    from src.config.settings import settings
    monkeypatch.setattr(settings.app, 'public_base_url', '', raising=False)
    with pytest.raises(ValueError, match='RUNNER_ALERT_PUBLIC_URL_REQUIRED'):
        FeishuBotAdapter().render(event(parameters=PARAMETERS), target())


def test_missing_detail_link_records_failed_delivery_without_sending(monkeypatch):
    from src.config.settings import settings
    from src.notifications import repository as module
    monkeypatch.setattr(settings.app, 'public_base_url', '', raising=False)
    monkeypatch.setattr(module, 'protected', lambda value: 'sealed_payload')
    repo = NotificationRepository.__new__(NotificationRepository)
    repo.registry = MagicMock()
    repo.registry.get.return_value = FeishuBotAdapter()
    cursor = MagicMock()
    route = []
    repo._append_delivery(cursor, event(parameters=PARAMETERS), target(), route)
    insertion = next(call for call in cursor.execute.call_args_list
                     if 'INSERT INTO notification_deliveries' in call.args[0])
    assert insertion.args[1][-2:] == ('failed', 'render_failure')
    assert route[0]['target_id'] == 'target_platform'


def test_platform_target_api_rejects_delegated_tenant_and_non_admin():
    repo = MagicMock()
    app = FastAPI()
    app.include_router(create_router(repo, lambda _: {'user_id': 'admin',
        'role': 'platform_admin', 'tenant_id': None}))
    client = TestClient(app)
    response = client.get('/api/platform/runner-alert-target', headers={'X-Tenant-Id': 'tenant_1'})
    assert response.status_code == 403
    repo.get_platform_runner_target.assert_not_called()
    app2 = FastAPI()
    app2.include_router(create_router(repo, lambda _: {'user_id': 'user',
        'role': 'tenant_admin', 'tenant_id': 'tenant_1'}))
    response = TestClient(app2).put('/api/platform/runner-alert-target', json={'webhook_url': 'https://open.feishu.cn/hook'})
    assert response.status_code == 403
    repo.register_platform_runner_target.assert_not_called()


def test_platform_target_api_masks_invalid_webhook_input():
    repo = MagicMock()
    app = FastAPI()
    app.include_router(create_router(repo, lambda _: {'user_id': 'admin',
        'role': 'platform_admin', 'tenant_id': None}))
    credential = 'https://open.feishu.cn/open-apis/bot/v2/hook/secret-code'
    response = TestClient(app).put('/api/platform/runner-alert-target',
                                   json={'webhook_url': credential, 'unexpected': 'secret-code'})
    assert response.status_code == 422
    assert response.json() == {'error': 'NOTIFICATION_INVALID_REQUEST',
                               'debug': 'NOTIFICATION_INVALID_REQUEST'}
    assert credential not in response.text
    repo.register_platform_runner_target.assert_not_called()
