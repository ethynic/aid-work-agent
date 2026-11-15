"""飞书应用通知 Adapter（飞书任务通知接入，2026-11-15，设计决策 7 追加）测试：

- validate_target：open_id 形态校验与稳定摘要（sha256 唯一性口径）
- render：卡片文案/回跳 url（含 ref query，租户消息追加 tenant）、字节上限截断、
  无域名失败留痕；飞书渠道仅终态事件可渲染（非终态 fail loud，对齐企微）
- 响应 code 分类（2026-11-15 官方文档查证语义）：0→accepted；230013/230034→
  target_invalid；230020→retryable；其余 code→permanent
- send：fake 连接全分支（限流/5xx/传输错误/取 token 失败）、token 按租户缓存
  （提前 300s 作废）；凭据按 target 租户解析（app_id/app_secret 来自租户飞书
  渠道配置，租户未配置 permanent）；卡片 content 为 JSON 序列化字符串且结构
  符合官方 interactive 卡片（header/elements/button url）
- available：租户自备配置后全局 available() 恒 False（fail-closed）；
  available_for_tenant 有配置可用/无配置不可用/凭据不全不可用/agent_only 不借用
- delivery 写入走通知核心既有机制；expand 渠道事件白名单（waiting_input 不投递）
- app_secret/token 不落日志

参照 test_wecom_app.py 的 Fake 连接/monkeypatch 风格。
"""
import hashlib
import json
from contextlib import contextmanager
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from loguru import logger
from pydantic import ValidationError

from src.config.settings import NotificationsConfig
from src.notifications import feishu_app
from src.notifications.contracts import (AdapterCapabilities, AdapterRegistry,
                                         NotificationError, FeishuAppConfiguration)
from src.notifications.repository import (TASK_EVENT_TYPES, NotificationRepository,
                                          protected, unprotect)
from src.notifications.feishu_app import FeishuAppAdapter, FeishuAppCredentials

# t1 租户自备的飞书通知凭据（连接中心飞书渠道配置 notify_usage ∈ {both, notify_only}
# 解析形态；feishu 类型 secret 明文存储）
CREDENTIALS = FeishuAppCredentials(tenant_id='t1', app_id='cli_a1', app_secret='feishu-secret')


def tenant_source(configured=True, tenant_id='t1', credentials=None):
    """凭据解析桩：configured=True 时指定租户返回凭据，否则 None。"""
    fixed = credentials or CREDENTIALS
    if configured:
        return lambda tid: fixed if tid == tenant_id else None
    return lambda tid: None


def make_adapter(configured=True, timeout_seconds=10):
    return FeishuAppAdapter(timeout_seconds=timeout_seconds,
                            credentials_source=tenant_source(configured))


TARGET = {'feishu_open_id': 'ou_' + 'a' * 32, 'bound_user_name': '张三', 'tenant_id': 't1'}
CARD = {'title': '销售日报相关任务已完成', 'description': '任务已执行完成，点击查看结果。',
        'url': 'https://console.example.test/notification?ref=ref1'}


def message(event_type='task.completed', parameters=None):
    return {'notification_id': 'notification_1', 'notification_ref': 'ref1',
            'event_type': event_type, 'schema_version': 1,
            'parameters': parameters or {'session_title': '销售日报', 'event_kind': 'completed'}}


def fake_settings(base='https://console.example.test'):
    settings = MagicMock()
    settings.app.public_base_url = base
    return settings


# ============== Fake 连接 ==============

class FakeResponse:
    def __init__(self, status, raw):
        self.status = status
        self._raw = raw

    def read(self, size):
        return self._raw


def install_fake_api(monkeypatch, responder):
    """按请求分流应答的假 pinned 连接；calls 依序记录 (method, path, body, headers)。"""
    calls = []

    class FakeConnection:
        def __init__(self, host, address, timeout):
            assert host == 'open.feishu.cn'
            calls.append(('connect', host, address))

        def request(self, method, path, body, headers):
            calls.append((method, path, body, headers))

        def getresponse(self):
            status, payload = responder(calls[-1])
            raw = b'' if payload is None else json.dumps(payload, ensure_ascii=False).encode()
            return FakeResponse(status, raw)

        def close(self):
            pass

    monkeypatch.setattr(feishu_app, 'PinnedHTTPSConnection', FakeConnection)
    monkeypatch.setattr(feishu_app, 'public_addresses', lambda host: ['93.184.216.34'])
    return calls


def make_api(monkeypatch, send_responder):
    """标准装配：token 与 messages 请求都记录进 calls 供断言。"""
    state = {'count': 0}
    calls = []

    def responder(call):
        calls.append(call)
        if 'tenant_access_token' in call[1]:
            state['count'] += 1
            return 200, {'code': 0, 'tenant_access_token': f"t-{state['count']}", 'expire': 7200}
        return send_responder(call, state)

    install_fake_api(monkeypatch, responder)
    return calls, state


# ============== validate_target ==============

class TestValidateTarget:
    def test_valid_open_id_returns_stable_digest(self):
        adapter = FeishuAppAdapter()
        open_id = 'ou_' + 'c' * 32
        first = adapter.validate_target({'feishu_open_id': open_id, 'tenant_id': 't1'})
        second = adapter.validate_target({'feishu_open_id': open_id, 'bound_user_name': '改名'})
        assert first == second
        assert first == hashlib.sha256(open_id.encode()).hexdigest()

    def test_malformed_open_id_rejected(self):
        adapter = FeishuAppAdapter()
        for bad in ['u_xxx', 'ou_zzz-not-hex', 'ou_123', 'ou ' + 'a' * 32]:
            with pytest.raises(NotificationError, match='NOTIFICATION_INVALID_FEISHU_OPEN_ID'):
                adapter.validate_target({'feishu_open_id': bad})
        # 空串在 DTO 层拒绝（min_length）
        with pytest.raises(ValidationError):
            adapter.validate_target({'feishu_open_id': ''})

    def test_longer_than_32_hex_accepted(self):
        """形态量级宽松：>32 位十六进制主体同样通过（服务端来源可信，仅边界校验）。"""
        assert FeishuAppAdapter().validate_target({'feishu_open_id': 'ou_' + 'b' * 40})

    def test_empty_and_oversize_open_id_rejected_by_dto(self):
        with pytest.raises(ValidationError):
            FeishuAppConfiguration.model_validate({'feishu_open_id': ''})
        with pytest.raises(ValidationError):
            FeishuAppConfiguration.model_validate({'feishu_open_id': 'ou_' + 'a' * 64})

    def test_extra_fields_rejected(self):
        with pytest.raises(ValidationError):
            FeishuAppConfiguration.model_validate(
                {'feishu_open_id': 'ou_' + 'a' * 32, 'webhook_url': 'https://x'})

    def test_missing_open_id_rejected(self):
        with pytest.raises(ValidationError):
            FeishuAppConfiguration.model_validate({'bound_user_name': '张三'})


# ============== available（租户自备配置：按租户判定） ==============

class TestAvailableForTenant:
    def test_tenant_with_credentials_available(self):
        assert make_adapter().available_for_tenant('t1') is True

    def test_tenant_without_credentials_unavailable(self):
        assert make_adapter(configured=False).available_for_tenant('t1') is False

    def test_other_tenant_unavailable(self):
        """凭据按租户隔离：t1 的配置不给 t2 用。"""
        assert make_adapter().available_for_tenant('t2') is False

    @pytest.mark.parametrize('tenant_id', ['', None])
    def test_blank_tenant_unavailable(self, tenant_id):
        """无租户上下文（global scope）不可用：fail-closed。"""
        assert make_adapter().available_for_tenant(tenant_id) is False

    def test_global_available_gate_always_false(self):
        """平台无全局通知应用：available() 恒 False（不带租户上下文的 gate
        一律不可用），租户感知点必须走 available_for_tenant。"""
        adapter = make_adapter()
        assert adapter.available() is False
        assert adapter.available_for_tenant('t1') is True

    def test_incomplete_tenant_config_unavailable(self):
        """默认解析：app_id/app_secret 任一缺失 → 无凭据不可用。"""
        incomplete_rows = [
            {'config': {'app_id': 'cli_a1', 'app_secret': ''}},
            {'config': {'app_id': '', 'app_secret': 's'}},
            None,  # 租户无通知用途的飞书渠道配置
        ]

        def fake_get(tenant_id):
            return incomplete_rows.pop(0)

        with patch('src.saas.db.channel_config_db.ChannelConfigDB.get_latest_feishu_notify_usage_decrypted',
                   staticmethod(fake_get)):
            for _ in range(3):
                assert feishu_app._default_credentials_source('t1') is None

    def test_default_source_builds_credentials_from_tenant_config(self):
        """默认解析：租户飞书渠道配置（notify_usage ∈ {both, notify_only}）→ 凭据。"""
        with patch('src.saas.db.channel_config_db.ChannelConfigDB.get_latest_feishu_notify_usage_decrypted',
                   staticmethod(lambda tenant_id:
                                {'config': {'app_id': 'cli_a2', 'app_secret': 'tenant-secret',
                                            'encrypt_key': 'ek', 'verification_token': 'vt'}})):
            credentials = feishu_app._default_credentials_source('t9')
        assert credentials == FeishuAppCredentials(tenant_id='t9', app_id='cli_a2',
                                                   app_secret='tenant-secret')

    def test_agent_only_legacy_config_not_borrowed(self):
        """notify_usage 缺省（agent_only 存量行为不变）的飞书配置不参与通知凭据
        （SQL 按 notify_usage IN (both, notify_only) 过滤，无行返回 None）。"""
        with patch('src.saas.db.channel_config_db.ChannelConfigDB.get_latest_feishu_notify_usage_decrypted',
                   staticmethod(lambda tenant_id: None)):
            assert feishu_app._default_credentials_source('t1') is None

    def test_credentials_resolved_fresh_each_call(self):
        """凭据实时解析（不缓存）：租户保存/删除配置立即生效。"""
        calls = []
        state = {'credentials': CREDENTIALS}

        def counting_source(tenant_id):
            calls.append(tenant_id)
            return state['credentials']

        adapter = FeishuAppAdapter(credentials_source=counting_source)
        assert adapter.available_for_tenant('t1') is True
        state['credentials'] = None  # 租户删除配置
        assert adapter.available_for_tenant('t1') is False
        assert len(calls) == 2

    def test_source_failure_treated_as_unconfigured(self):
        """配置读取异常按未配置处理（不阻断通知主流程）。"""
        def broken_source(tenant_id):
            raise RuntimeError('db down')

        adapter = FeishuAppAdapter(credentials_source=broken_source)
        assert adapter.available_for_tenant('t1') is False


# ============== render（卡片文案与回跳） ==============

class TestRender:
    def _render(self, msg, base='https://console.example.test'):
        with patch('src.config.settings.settings', fake_settings(base)):
            return make_adapter().render(msg, {'installation_ref': 'inst'})

    def test_completed_card_fields(self):
        payload = self._render(message())
        assert payload['title'] == '销售日报相关任务已完成'
        assert payload['description'] == '任务已执行完成，点击查看结果。'
        # 回跳按 web push 设计 §5 payload 语义：notification_ref 放进 url query
        assert payload['url'] == 'https://console.example.test/notification?ref=ref1'
        assert payload['notification_ref'] == 'ref1'
        assert payload['schema_version'] == 1

    def test_failed_card(self):
        payload = self._render(message('task.failed',
                                       {'session_title': '帮我看下数据', 'event_kind': 'failed'}))
        assert payload['title'] == '帮我看下数据相关任务执行失败'

    @pytest.mark.parametrize('event_type', [
        'task.waiting_input', 'task.paused', 'task.needs_action',
        'task.manual_rejected', 'notification.test', 'billing.credit_low',
    ])
    def test_non_terminal_events_unsupported_fail_loud(self, event_type):
        """飞书渠道仅终态通知（与企微同口径），waiting_input/test 等非终态
        映射已删除，误接 fail loud。"""
        with pytest.raises(ValueError, match='NOTIFICATION_EVENT_UNSUPPORTED'):
            self._render(message(event_type, {'session_title': '日报会话',
                                              'event_kind': 'completed'}))

    def test_long_title_clipped(self):
        payload = self._render(message('task.completed',
                                       {'session_title': '长' * 60, 'event_kind': 'completed'}))
        assert len(payload['title'].encode('utf-8')) <= 128
        assert payload['title']  # 按字符边界截断不产生空串

    def test_public_base_url_missing_fails_render(self):
        with pytest.raises(ValueError, match='NOTIFICATION_PUBLIC_URL_REQUIRED'):
            self._render(message(), base='')

    def test_tenant_message_url_appends_tenant(self):
        """租户消息回跳 url 追加 &tenant=（对齐企微评审回炉 P1-2 口径）。"""
        tenant_msg = dict(message(), tenant_id='tenant-1')
        payload = self._render(tenant_msg)
        assert payload['url'] == 'https://console.example.test/notification?ref=ref1&tenant=tenant-1'

    def test_ref_escaped_in_url(self):
        unsafe_ref = message('task.completed', {'session_title': 's', 'event_kind': 'completed'})
        unsafe_ref['notification_ref'] = 'a/b?c'
        with patch('src.config.settings.settings', fake_settings()):
            escaped = make_adapter().render(unsafe_ref, {})['url']
        tail = escaped.split('ref=', 1)[1]
        assert '?' not in tail and '/' not in tail

    def test_duration_description(self):
        payload = self._render(message('task.completed', {'session_title': '销售日报',
                                                          'event_kind': 'completed',
                                                          'duration_seconds': 332}))
        assert payload['description'] == '任务已执行 5分32秒，点击查看结果。'
        payload = self._render(message('task.failed', {'session_title': '销售日报',
                                                       'event_kind': 'failed',
                                                       'duration_seconds': 125}))
        assert payload['description'] == '任务已执行 2分5秒 后失败，点击查看结果。'

    @pytest.mark.parametrize('value', [None, 0, '332', True, -5])
    def test_invalid_duration_falls_back_to_old_copy(self, value):
        """0=时长未知/类型异常/缺省：不虚构耗时，走旧文案兜底。"""
        payload = self._render(message('task.completed', {'session_title': '销售日报',
                                                          'event_kind': 'completed',
                                                          'duration_seconds': value}))
        assert payload['description'] == '任务已执行完成，点击查看结果。'


# ============== 卡片 content JSON 结构（官方 interactive 卡片） ==============

class TestCardContent:
    def test_card_content_json_structure(self):
        adapter = make_adapter()
        with patch('src.config.settings.settings', fake_settings()):
            payload = adapter.render(message('task.completed', {'session_title': '销售日报',
                                                                'event_kind': 'completed'}), {})
        content = adapter._card_content(payload)
        # content 是 JSON 序列化后的字符串（im/v1/messages 协议要求）
        assert isinstance(content, str)
        card = json.loads(content)
        assert card['config'] == {'wide_screen_mode': True}
        assert card['header']['title'] == {'tag': 'plain_text', 'content': '销售日报相关任务已完成'}
        assert card['header']['template'] == 'blue'
        elements = card['elements']
        assert elements[0] == {'tag': 'div', 'text': {'tag': 'lark_md',
                                                      'content': '任务已执行完成，点击查看结果。'}}
        button = elements[1]['actions'][0]
        assert button['tag'] == 'button' and button['type'] == 'primary'
        assert button['text'] == {'tag': 'plain_text', 'content': '查看结果'}
        assert button['url'].startswith('https://console.example.test/notification?ref=')

    def test_request_body_shape(self):
        """im/v1/messages body：receive_id 单 open_id + msg_type interactive +
        content 为序列化字符串。"""
        adapter = make_adapter()
        with patch('src.config.settings.settings', fake_settings()):
            payload = adapter.render(message(), {})
        body = adapter._request_body({'feishu_open_id': TARGET['feishu_open_id']}, payload)
        assert body['receive_id'] == TARGET['feishu_open_id']
        assert body['msg_type'] == 'interactive'
        assert isinstance(body['content'], str)
        assert json.loads(body['content'])['header']['template'] == 'blue'


# ============== 响应 code 分类（2026-11-15 官方文档查证语义） ==============

class TestInterpret:
    @pytest.mark.parametrize('code,expected,reason', [
        (0, 'accepted', None),
        (230013, 'target_invalid', 'feishu_open_id_invalid'),
        (230034, 'target_invalid', 'feishu_open_id_invalid'),
        (230020, 'retryable', 'rate_limited'),
        (99991663, 'permanent_failure', 'provider_rejected'),
        (230002, 'permanent_failure', 'provider_rejected'),
    ])
    def test_code_mapping(self, code, expected, reason):
        result = make_adapter()._interpret(code, {'code': code})
        assert (result.outcome, result.error_class) == (expected, reason)

    def test_accepted_carries_message_id(self):
        result = make_adapter()._interpret(0, {'code': 0, 'data': {'message_id': 'om_9'}})
        assert result.provider_id == 'om_9'
        assert make_adapter()._interpret(0, {'code': 0, 'data': {}}).provider_id is None

    def test_unparseable_body_is_permanent(self):
        result = make_adapter()._interpret(None, None)
        assert (result.outcome, result.error_class) == ('permanent_failure', 'provider_rejected')


# ============== send（fake 连接全分支） ==============

class TestSend:
    def test_accepted_payload_shape(self, monkeypatch):
        calls, _ = make_api(monkeypatch, lambda call, state: (200, {'code': 0, 'data': {'message_id': 'om_1'}}))
        result = make_adapter(timeout_seconds=7).send(TARGET, CARD, 20)
        assert (result.outcome, result.provider_id) == ('accepted', 'om_1')
        token_call = next(call for call in calls if 'tenant_access_token' in call[1])
        # token 在 body 参数（app_id/app_secret），非 Basic 认证（官方文档口径）
        assert token_call[1] == '/open-apis/auth/v3/tenant_access_token/internal'
        assert json.loads(token_call[2]) == {'app_id': 'cli_a1', 'app_secret': 'feishu-secret'}
        send = calls[-1]
        assert send[1] == '/open-apis/im/v1/messages?receive_id_type=open_id'
        # Bearer token 在 Authorization 头
        assert send[3]['Authorization'] == 'Bearer t-1'
        body = json.loads(send[2])
        assert body['receive_id'] == TARGET['feishu_open_id'] and body['msg_type'] == 'interactive'
        card = json.loads(body['content'])
        assert card['header']['title']['content'] == CARD['title']
        assert card['elements'][1]['actions'][0]['url'] == CARD['url']

    @pytest.mark.parametrize('code,expected,reason', [
        (230013, 'target_invalid', 'feishu_open_id_invalid'),
        (230034, 'target_invalid', 'feishu_open_id_invalid'),
        (230020, 'retryable', 'rate_limited'),
        (230002, 'permanent_failure', 'provider_rejected'),
    ])
    def test_code_outcomes(self, monkeypatch, code, expected, reason):
        make_api(monkeypatch, lambda call, state: (200, {'code': code}))
        result = make_adapter().send(TARGET, CARD, 20)
        assert (result.outcome, result.error_class) == (expected, reason)

    def test_http_5xx_retryable(self, monkeypatch):
        make_api(monkeypatch, lambda call, state: (500, None))
        result = make_adapter().send(TARGET, CARD, 20)
        assert (result.outcome, result.error_class) == ('retryable', 'provider_unavailable')

    def test_http_429_retryable(self, monkeypatch):
        make_api(monkeypatch, lambda call, state: (429, None))
        result = make_adapter().send(TARGET, CARD, 20)
        assert (result.outcome, result.error_class) == ('retryable', 'rate_limited')

    def test_http_4xx_permanent(self, monkeypatch):
        make_api(monkeypatch, lambda call, state: (403, None))
        result = make_adapter().send(TARGET, CARD, 20)
        assert (result.outcome, result.error_class) == ('permanent_failure', 'provider_rejected')

    def test_transport_error_retryable(self, monkeypatch):
        class BrokenConnection:
            def __init__(self, *args):
                raise OSError('unreachable')

            def close(self):
                pass

        monkeypatch.setattr(feishu_app, 'PinnedHTTPSConnection', BrokenConnection)
        monkeypatch.setattr(feishu_app, 'public_addresses', lambda host: ['93.184.216.34'])
        result = make_adapter().send(TARGET, CARD, 20)
        assert (result.outcome, result.error_class) == ('retryable', 'transport_unavailable')

    def test_token_failure_is_retryable_without_send(self, monkeypatch):
        calls = install_fake_api(
            monkeypatch, lambda call: (200, {'code': 10003, 'msg': 'invalid app_id'}))
        result = make_adapter().send(TARGET, CARD, 20)
        assert (result.outcome, result.error_class) == ('retryable', 'provider_authentication')
        assert not [call for call in calls if 'im/v1/messages' in call[1]]

    def test_private_resolution_permanent(self, monkeypatch):
        def forbidden(host):
            raise NotificationError('NOTIFICATION_ENDPOINT_FORBIDDEN')

        monkeypatch.setattr(feishu_app, 'public_addresses', forbidden)
        result = make_adapter().send(TARGET, CARD, 20)
        assert (result.outcome, result.error_class) == ('permanent_failure', 'endpoint_forbidden')

    def test_bad_configuration_is_permanent(self, monkeypatch):
        install_fake_api(monkeypatch, lambda call: (200, {'code': 0}))
        result = make_adapter().send({'bound_user_name': '缺 open_id'}, CARD, 20)
        assert (result.outcome, result.error_class) == ('permanent_failure', 'provider_configuration')

    def test_send_uses_tenant_credentials(self, monkeypatch):
        """send 凭据按 target 配置的 tenant_id 解析——app_id/app_secret 全部
        来自该租户的飞书渠道配置。"""
        captured = {}
        credentials = FeishuAppCredentials(tenant_id='tenant-9', app_id='cli_999',
                                           app_secret='tenant-secret')

        def source(tenant_id):
            captured['tenant_id'] = tenant_id
            return credentials if tenant_id == 'tenant-9' else None

        calls, _ = make_api(monkeypatch, lambda call, state: (200, {'code': 0}))
        target = dict(TARGET, tenant_id='tenant-9')
        result = FeishuAppAdapter(credentials_source=source).send(target, CARD, 20)
        assert result.outcome == 'accepted'
        assert captured['tenant_id'] == 'tenant-9'
        token_call = next(call for call in calls if 'tenant_access_token' in call[1])
        assert json.loads(token_call[2])['app_id'] == 'cli_999'

    def test_send_without_tenant_credentials_permanent(self, monkeypatch):
        """租户未配置通知用途的飞书应用（凭据解析 None）：配置类失败不重试。"""
        make_api(monkeypatch, lambda call, state: (200, {'code': 0}))
        result = make_adapter(configured=False).send(TARGET, CARD, 20)
        assert (result.outcome, result.error_class) == ('permanent_failure', 'provider_configuration')

    def test_send_without_tenant_in_configuration_permanent(self, monkeypatch):
        """target 配置缺 tenant_id（无租户上下文）：fail-closed 不外呼。"""
        calls = install_fake_api(monkeypatch, lambda call: (200, {'code': 0}))
        result = make_adapter().send({'feishu_open_id': TARGET['feishu_open_id']}, CARD, 20)
        assert (result.outcome, result.error_class) == ('permanent_failure', 'provider_configuration')
        assert calls == []  # 未解析到凭据，连取 token 都不发


# ============== token 按租户缓存 ==============

class TestTokenHandling:
    def test_token_cached_across_sends(self, monkeypatch):
        calls, state = make_api(monkeypatch, lambda call, state: (200, {'code': 0}))
        adapter = make_adapter()
        assert adapter.send(TARGET, CARD, 20).outcome == 'accepted'
        assert adapter.send(TARGET, CARD, 20).outcome == 'accepted'
        assert state['count'] == 1  # 第二次发送复用缓存 token

    def test_token_isolated_per_tenant(self, monkeypatch):
        """token 缓存按租户隔离：不同租户各取各的 token。"""
        credentials_t2 = FeishuAppCredentials(tenant_id='t2', app_id='cli_2', app_secret='s2')
        calls, state = make_api(monkeypatch, lambda call, state: (200, {'code': 0}))
        adapter = FeishuAppAdapter(credentials_source=lambda tid: (
            CREDENTIALS if tid == 't1' else credentials_t2 if tid == 't2' else None))
        assert adapter.send(TARGET, CARD, 20).outcome == 'accepted'
        target_t2 = dict(TARGET, tenant_id='t2')
        assert adapter.send(target_t2, CARD, 20).outcome == 'accepted'
        assert state['count'] == 2

    def test_expired_token_refetched(self, monkeypatch):
        """提前 300s 作废：缓存过期后下次发送强制取新 token。"""
        calls, state = make_api(monkeypatch, lambda call, state: (200, {'code': 0}))
        adapter = make_adapter()
        assert adapter.send(TARGET, CARD, 20).outcome == 'accepted'
        cached = adapter._tokens['t1']
        adapter._tokens['t1'] = (cached[0], cached[1] - 7200)  # 人为推过有效期
        assert adapter.send(TARGET, CARD, 20).outcome == 'accepted'
        assert state['count'] == 2

    def test_short_expire_clamped(self, monkeypatch):
        """expire 异常（<300s）按 7200 兜底，缓存不立即作废。"""
        calls = install_fake_api(monkeypatch, lambda call:
                                 (200, {'code': 0, 'tenant_access_token': 't-x', 'expire': 60})
                                 if 'tenant_access_token' in call[1] else (200, {'code': 0}))
        adapter = make_adapter()
        assert adapter.send(TARGET, CARD, 20).outcome == 'accepted'
        assert adapter._tokens['t1'][1] > __import__('time').time() + 6000


# ============== 凭据不落日志 ==============

def test_secret_and_token_never_logged(monkeypatch):
    records = []
    sink = logger.add(lambda msg: records.append(str(msg)), level='WARNING')
    try:
        install_fake_api(monkeypatch, lambda call: (200, {'code': 10003}))
        adapter = make_adapter()
        result = adapter.send(TARGET, CARD, 20)
        assert result.outcome == 'retryable'
    finally:
        logger.remove(sink)
    joined = ''.join(records)
    assert 'feishu-secret' not in joined and 'app_secret' not in joined
    assert 'Bearer t-' not in joined


# ============== delivery 写入（通知核心既有机制） ==============

def _repo_with_adapter(configured=True):
    repo = NotificationRepository.__new__(NotificationRepository)
    repo.registry = AdapterRegistry([make_adapter(configured=configured)])
    return repo


class TestDeliveryWrite:
    TARGET_ROW = {'target_id': 'target_f1', 'channel': 'feishu_app', 'provider': 'feishu_app',
                  'tenant_id': 't1',
                  'binding_version': 2, 'installation_ref': 'feishu_bind_u1'}

    def test_append_delivery_writes_notification_core_row(self):
        cursor = MagicMock()
        repo = _repo_with_adapter()
        route = []
        with patch('src.config.settings.settings', fake_settings()):
            repo._append_delivery(cursor, message(), self.TARGET_ROW, route)
        inserts = [call for call in cursor.execute.call_args_list
                   if 'INSERT INTO notification_deliveries' in str(call.args[0])]
        assert len(inserts) == 1
        values = inserts[0].args[1]
        assert values[2] == 'target_f1' and values[3] == 2
        # 渲染产物是卡片字段结构（send 时再拼 receive_id/content）
        rendered = unprotect(values[4])
        assert rendered['title'] == '销售日报相关任务已完成'
        assert rendered['url'].endswith('/notification?ref=ref1')
        assert route == [{'target_id': 'target_f1', 'channel': 'feishu_app',
                          'provider': 'feishu_app', 'binding_version': 2}]

    def test_append_delivery_skips_unavailable_adapter(self):
        """target 租户未配置通知用途的飞书应用（凭据解析 None）→ 不产生 delivery。"""
        cursor = MagicMock()
        repo = _repo_with_adapter(configured=False)
        route = []
        with patch('src.config.settings.settings', fake_settings()):
            repo._append_delivery(cursor, message(), self.TARGET_ROW, route)
        assert route == []
        assert not [call for call in cursor.execute.call_args_list
                    if 'notification_deliveries' in str(call.args[0])]

    def test_append_delivery_skips_target_without_tenant(self):
        """无租户上下文（global scope 行）即使有配置也不投递：fail-closed。"""
        cursor = MagicMock()
        repo = _repo_with_adapter()
        route = []
        target_row = dict(self.TARGET_ROW, tenant_id=None)
        with patch('src.config.settings.settings', fake_settings()):
            repo._append_delivery(cursor, message(), target_row, route)
        assert route == []
        assert not [call for call in cursor.execute.call_args_list
                    if 'notification_deliveries' in str(call.args[0])]


# ============== expand 渠道事件白名单 ==============

class StubWebPushAdapter:
    """expand 资格对照用：web_push 渠道桩（不参与白名单）。"""
    channel = provider = 'web_push'
    capabilities = AdapterCapabilities()

    def available(self):
        return True

    def render(self, message, target):
        return {'ref': message['notification_ref']}


def expand_message(event_type):
    now = datetime(2026, 11, 15, 10, 0, 0)
    waiting = event_type == 'task.waiting_input'
    params = ({'session_title': '日报会话', 'question_title': '请确认报表口径'}
              if waiting else {'session_title': '日报会话', 'event_kind': 'completed',
                               'duration_seconds': 600})
    return {'notification_id': 'notification_1', 'notification_ref': 'ref1',
            'schema_version': 1, 'producer': 'agent_runner', 'event_type': event_type,
            'event_key': 'evt_1', 'scope_key': 'tenant:t1', 'tenant_id': 't1',
            'recipient_user_id': 'u1', 'category': 'task', 'priority': 0,
            'policy_key': 'accepted_before_enabled', 'expansion_state': 'pending',
            'route_snapshot': [], 'read_at': None, 'created_at': now,
            'resource_ref': {'type': 'runner_waiting' if waiting else 'runner_result',
                             'id': 'runner_1'},
            'template_key': None, 'template_version': 1,
            'parameters_encrypted': protected(params),
            'occurred_at': now, 'accepted_at': now, 'expires_at': now + timedelta(minutes=5),
            'database_now': now}


def expand_target(channel):
    return {'target_id': 'target_f1', 'scope_key': 'tenant:t1', 'tenant_id': 't1',
            'recipient_user_id': 'u1', 'channel': channel, 'provider': channel,
            'installation_ref': 'inst_1', 'binding_version': 1, 'state': 'active',
            'rule_id': None, 'enabled_since': datetime(2026, 1, 1),
            'preference_enabled': True, 'preference_since': datetime(2026, 1, 1),
            'event_types': list(TASK_EVENT_TYPES)}


class TestExpandProviderEventGate:
    """飞书渠道仅终态通知（对齐企微白名单）：waiting_input 等过程事件即使偏好
    启用也不产生 delivery；其他渠道与飞书终态事件不受影响。"""

    def _expand(self, event_type, channel='feishu_app', configured=True, target_tenant='t1'):
        repo = NotificationRepository.__new__(NotificationRepository)
        repo.config = NotificationsConfig(batch_size=10)
        repo.registry = AdapterRegistry([make_adapter(configured=configured), StubWebPushAdapter()])
        cursor = MagicMock()
        state = {'inserted': 0}

        def execute_dispatch(sql, params=None):
            if "expansion_state='pending'" in sql:
                cursor.fetchall = lambda: [expand_message(event_type)]
            elif 'FROM users' in sql:
                cursor.fetchone = lambda: {'user_id': 'u1', 'tenant_id': 't1',
                                           'role': 'user', 'status': 'active'}
            elif "FROM tenants WHERE tenant_id=%s AND status='active'" in sql:
                cursor.fetchone = lambda: {'ok': 1}
            elif 'FROM agent_runners' in sql:
                cursor.fetchone = lambda: {'runner_id': 'runner_1', 'session_id': 'sess_1'}
            elif 'FROM notification_targets' in sql:
                cursor.fetchall = lambda: [dict(expand_target(channel), tenant_id=target_tenant)]
            elif 'INSERT INTO notification_deliveries' in sql:
                state['inserted'] += 1

        cursor.execute.side_effect = execute_dispatch
        conn = MagicMock()
        conn.cursor.return_value = cursor

        @contextmanager
        def factory():
            yield conn

        repo.connection_factory = factory
        with patch('src.config.settings.settings', fake_settings()):
            repo.expand()
        return state['inserted']

    def test_waiting_input_not_delivered_to_enabled_feishu_app_target(self):
        assert self._expand('task.waiting_input', 'feishu_app') == 0

    def test_waiting_input_still_delivered_to_web_push_target(self):
        assert self._expand('task.waiting_input', 'web_push') == 1

    def test_completed_still_delivered_to_feishu_app_target(self):
        assert self._expand('task.completed', 'feishu_app') == 1

    def test_completed_not_delivered_when_tenant_unconfigured(self):
        """target 租户没有通知用途的飞书应用配置 → 不产生 delivery。"""
        assert self._expand('task.completed', 'feishu_app', configured=False) == 0

    def test_completed_not_delivered_when_target_tenant_unconfigured(self):
        """凭据按 target 租户隔离：t1 配置不给 t2 的 target 用。"""
        assert self._expand('task.completed', 'feishu_app', target_tenant='t2') == 0
