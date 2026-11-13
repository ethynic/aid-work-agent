"""企微应用通知 Adapter（Phase 3，设计 §4.3；五轮：租户级配置改造）测试：

- validate_target：userid 校验与稳定摘要（sha256 唯一性口径）
- render：textcard 文案/回跳 url（含 ref query，租户消息追加 tenant）、
  字节上限截断、无域名失败留痕；企微渠道仅终态事件可渲染（评审回炉 P2-5，
  非终态 fail loud）
- errcode 分类：0→accepted；40031/43004/81013→target_invalid；42009/45009→
  retryable；其余 errcode→permanent（im_webhook 兜底语义）
- send：fake 连接全分支（限流/5xx/传输错误/gettoken 失败）、token 缓存与
  40014/42001 刷新重试一次；凭据按 target 租户解析（corp_id/agentid/secret
  来自租户配置，租户未配置 permanent）
- available：租户自备配置后全局 available() 恒 False（fail-closed）；
  available_for_tenant 有配置可用/无配置不可用/凭据不全不可用；凭据 TTL 缓存
- delivery 写入走通知核心既有机制
- secret/access_token 不落日志

参照 test_im_webhook.py 的 Fake 连接/monkeypatch 风格。
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
from src.notifications import wecom_app
from src.notifications.contracts import (AdapterCapabilities, AdapterRegistry,
                                         NotificationError, WecomAppConfiguration)
from src.notifications.repository import (TASK_EVENT_TYPES, NotificationRepository,
                                          protected, unprotect)
from src.notifications.wecom_app import WecomAppAdapter, WecomAppCredentials

# t1 租户自备的通知应用凭据（连接中心渠道配置 wecom_app_notify 解密后形态）
CREDENTIALS = WecomAppCredentials(tenant_id='t1', corp_id='corp123',
                                  agentid=1000001, secret='app-secret')


def tenant_source(configured=True, tenant_id='t1', credentials=None):
    """凭据解析桩：configured=True 时指定租户返回凭据，否则 None。"""
    fixed = credentials or CREDENTIALS
    if configured:
        return lambda tid: fixed if tid == tenant_id else None
    return lambda tid: None


def make_adapter(configured=True, timeout_seconds=10):
    return WecomAppAdapter(timeout_seconds=timeout_seconds,
                           credentials_source=tenant_source(configured))


TARGET = {'wecom_userid': 'zhangsan', 'bound_user_name': '张三', 'tenant_id': 't1'}
TEXTCARD = {'title': '销售日报相关任务已完成', 'description': '任务已执行完成，点击查看结果。',
            'url': 'https://console.example.test/notification?ref=ref1', 'btntxt': '点击查看'}


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
    """按请求分流应答的假 pinned 连接；calls 依序记录 (method, path, body)。"""
    calls = []

    class FakeConnection:
        def __init__(self, host, address, timeout):
            assert host == 'qyapi.weixin.qq.com'
            calls.append(('connect', host, address))

        def request(self, method, path, body, headers):
            calls.append((method, path, body))

        def getresponse(self):
            status, payload = responder(calls[-1])
            raw = b'' if payload is None else json.dumps(payload, ensure_ascii=False).encode()
            return FakeResponse(status, raw)

        def close(self):
            pass

    monkeypatch.setattr(wecom_app, 'PinnedHTTPSConnection', FakeConnection)
    monkeypatch.setattr(wecom_app, 'public_addresses', lambda host: ['93.184.216.34'])
    return calls


def token_counter_ok(state):
    """gettoken 应答：递增 token 序号（供刷新重试断言新旧 token 不同）。"""
    def responder(call):
        state['count'] = state.get('count', 0) + 1
        return 200, {'errcode': 0, 'access_token': f"token_{state['count']}", 'expires_in': 7200}
    return responder


def send_error(errcode):
    """message/send 应答：固定 errcode（token 计数递增，供重试取新 token）。"""
    def responder(call):
        if 'gettoken' in call[1]:
            return token_counter_ok({})(call)
        return 200, {'errcode': errcode, 'errmsg': 'x'}
    return responder


def make_api(monkeypatch, send_responder):
    """标准装配：gettoken 与 message/send 请求都记录进 calls 供断言。"""
    state = {'count': 0}
    calls = []

    def responder(call):
        calls.append(call)
        if 'gettoken' in call[1]:
            state['count'] += 1
            return 200, {'errcode': 0, 'access_token': f"token_{state['count']}", 'expires_in': 7200}
        return send_responder(call, state)

    install_fake_api(monkeypatch, responder)
    return calls, state


# ============== validate_target ==============

class TestValidateTarget:
    def test_valid_userid_returns_stable_digest(self):
        adapter = WecomAppAdapter()
        first = adapter.validate_target(TARGET)
        second = adapter.validate_target(dict(TARGET, bound_user_name='改名'))
        assert first == second
        assert first == hashlib.sha256(b'zhangsan').hexdigest()

    def test_invalid_userid_rejected(self):
        adapter = WecomAppAdapter()
        with pytest.raises(NotificationError, match='NOTIFICATION_INVALID_WECOM_USERID'):
            adapter.validate_target({'wecom_userid': 'a b'})       # 含空白
        with pytest.raises(NotificationError, match='NOTIFICATION_INVALID_WECOM_USERID'):
            adapter.validate_target({'wecom_userid': 'a\x01b'})    # 控制字符

    def test_empty_and_oversize_userid_rejected_by_dto(self):
        with pytest.raises(ValidationError):
            WecomAppConfiguration.model_validate({'wecom_userid': ''})
        with pytest.raises(ValidationError):
            WecomAppConfiguration.model_validate({'wecom_userid': 'a' * 65})

    def test_extra_fields_rejected(self):
        with pytest.raises(ValidationError):
            WecomAppConfiguration.model_validate(
                {'wecom_userid': 'zhangsan', 'webhook_url': 'https://x'})

    def test_missing_wecom_userid_rejected(self):
        with pytest.raises(ValidationError):
            WecomAppConfiguration.model_validate({'bound_user_name': '张三'})


# ============== available（租户自备配置：按租户判定，五轮） ==============

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
        """默认解析：corp_id/agentid/secret 任一缺失 → 无凭据不可用。"""
        incomplete_rows = [
            {'config': {'corp_id': 'ww123', 'agentid': 1000002, 'secret': ''}},
            {'config': {'corp_id': 'ww123', 'agentid': 0, 'secret': 's'}},
            {'config': {'corp_id': '', 'agentid': 1000002, 'secret': 's'}},
            {'config': {'corp_id': 'ww123', 'agentid': 'not-a-number', 'secret': 's'}},
            None,  # 租户无 wecom_app_notify 渠道配置
        ]

        def fake_get(tenant_id, channel_type):
            assert channel_type == 'wecom_app_notify'
            return incomplete_rows.pop(0)

        with patch('src.saas.db.channel_config_db.ChannelConfigDB.get_by_tenant_and_type_decrypted',
                   staticmethod(fake_get)):
            for _ in range(5):
                assert wecom_app._default_credentials_source('t1') is None

    def test_default_source_builds_credentials_from_tenant_config(self):
        """默认解析：租户渠道配置（解密后）→ WecomAppCredentials；agentid 字符串可强转。"""
        with patch('src.saas.db.channel_config_db.ChannelConfigDB.get_by_tenant_and_type_decrypted',
                   staticmethod(lambda tenant_id, channel_type:
                                {'config': {'corp_id': 'ww123', 'agentid': '1000002',
                                            'secret': 'tenant-secret'} })):
            credentials = wecom_app._default_credentials_source('t9')
        assert credentials == WecomAppCredentials(tenant_id='t9', corp_id='ww123',
                                                  agentid=1000002, secret='tenant-secret')

    def test_credentials_resolved_fresh_each_call(self):
        """凭据实时解析（不缓存）：租户保存/删除配置立即生效。"""
        calls = []
        state = {'credentials': CREDENTIALS}

        def counting_source(tenant_id):
            calls.append(tenant_id)
            return state['credentials']

        adapter = WecomAppAdapter(credentials_source=counting_source)
        assert adapter.available_for_tenant('t1') is True
        state['credentials'] = None  # 租户删除配置
        assert adapter.available_for_tenant('t1') is False
        assert len(calls) == 2

    def test_source_failure_treated_as_unconfigured(self):
        """配置读取异常按未配置处理（不阻断通知主流程）。"""
        def broken_source(tenant_id):
            raise RuntimeError('db down')

        adapter = WecomAppAdapter(credentials_source=broken_source)
        assert adapter.available_for_tenant('t1') is False


# ============== render（textcard 文案与回跳） ==============

class TestRender:
    def _render(self, msg, base='https://console.example.test'):
        with patch('src.config.settings.settings', fake_settings(base)):
            return make_adapter().render(msg, {'installation_ref': 'inst'})

    def test_completed_textcard_fields(self):
        payload = self._render(message())
        assert payload['title'] == '销售日报相关任务已完成'
        assert payload['description'] == '任务已执行完成，点击查看结果。'
        # 回跳按 web push 设计 §5 payload 语义：notification_ref 放进 url query
        assert payload['url'] == 'https://console.example.test/notification?ref=ref1'
        assert payload['btntxt'] == '点击查看'
        assert payload['notification_ref'] == 'ref1'
        assert payload['schema_version'] == 1

    def test_failed_textcard(self):
        payload = self._render(message('task.failed',
                                       {'session_title': '帮我看下数据', 'event_kind': 'failed'}))
        assert payload['title'] == '帮我看下数据相关任务执行失败'

    @pytest.mark.parametrize('event_type', [
        'task.waiting_input', 'task.paused', 'task.needs_action',
        'task.manual_rejected', 'notification.test', 'billing.credit_low',
    ])
    def test_non_terminal_events_unsupported_fail_loud(self, event_type):
        """评审回炉 P2-5：企微渠道仅终态通知（设计决策 3/§4.3），waiting_input/
        paused/needs_action/manual_rejected/test 等非终态映射已删除，误接 fail loud。"""
        with pytest.raises(ValueError, match='NOTIFICATION_EVENT_UNSUPPORTED'):
            self._render(message(event_type, {'session_title': '日报会话',
                                              'event_kind': 'completed',
                                              'task_name': '日报任务'}))

    def test_long_title_clipped_to_protocol_limit(self):
        payload = self._render(message('task.completed',
                                       {'session_title': '长' * 60, 'event_kind': 'completed'}))
        assert len(payload['title'].encode('utf-8')) <= 128
        assert payload['title']  # 按字符边界截断不产生空串

    def test_public_base_url_missing_fails_render(self):
        with pytest.raises(ValueError, match='NOTIFICATION_PUBLIC_URL_REQUIRED'):
            self._render(message(), base='')

    def test_ref_escaped_in_url(self):
        unsafe_ref = message('task.completed', {'session_title': 's', 'event_kind': 'completed'})
        unsafe_ref['notification_ref'] = 'a/b?c'
        with patch('src.config.settings.settings', fake_settings()):
            escaped = make_adapter().render(unsafe_ref, {})['url']
        tail = escaped.split('ref=', 1)[1]
        assert '?' not in tail and '/' not in tail

    def test_tenant_message_url_appends_tenant(self):
        """评审回炉 P1-2：租户消息回跳 url 追加 &tenant=，落地页按其选租户
        凭证（缺省会读平台凭证把租户用户送登录页）。"""
        tenant_msg = dict(message(), tenant_id='tenant-1')
        payload = self._render(tenant_msg)
        assert payload['url'] == 'https://console.example.test/notification?ref=ref1&tenant=tenant-1'

    def test_tenant_id_escaped_in_url(self):
        tenant_msg = dict(message(), tenant_id='a/b?c')
        payload = self._render(tenant_msg)
        tail = payload['url'].split('tenant=', 1)[1]
        assert '?' not in tail and '/' not in tail


# ============== description 执行时长（设计 §4.3，触发判定带入事件参数） ==============

class TestDurationDescription:
    def _render(self, parameters, event_type='task.completed'):
        with patch('src.config.settings.settings', fake_settings()):
            return make_adapter().render(message(event_type, parameters), {})

    def test_completed_description_carries_duration(self):
        params = {'session_title': '销售日报', 'event_kind': 'completed', 'duration_seconds': 332}
        payload = self._render(params)
        assert payload['description'] == '任务已执行 5分32秒，点击查看结果。'

    def test_failed_description_carries_duration(self):
        params = {'session_title': '销售日报', 'event_kind': 'failed', 'duration_seconds': 125}
        payload = self._render(params, 'task.failed')
        assert payload['description'] == '任务已执行 2分5秒 后失败，点击查看结果。'

    @pytest.mark.parametrize('seconds,expected', [
        (45, '45秒'), (60, '1分'), (332, '5分32秒'), (3600, '1小时'),
        (3661, '1小时1分1秒'), (7322, '2小时2分2秒'),
    ])
    def test_duration_label_formatting(self, seconds, expected):
        assert wecom_app._duration_label(seconds) == expected

    @pytest.mark.parametrize('value', [None, 0, '332', True, -5])
    def test_invalid_duration_falls_back_to_old_copy(self, value):
        """0=时长未知/类型异常/缺省：不虚构耗时，走旧文案兜底。"""
        params = {'session_title': '销售日报', 'event_kind': 'completed',
                  'duration_seconds': value}
        payload = self._render(params)
        assert payload['description'] == '任务已执行完成，点击查看结果。'

    def test_missing_duration_old_copy(self):
        """存量事件无时长字段（旧文案兜底）。"""
        payload = self._render({'session_title': '销售日报', 'event_kind': 'completed'})
        assert payload['description'] == '任务已执行完成，点击查看结果。'

    def test_failed_missing_duration_old_copy(self):
        payload = self._render({'session_title': '销售日报', 'event_kind': 'failed'},
                               'task.failed')
        assert payload['description'] == '任务执行失败，点击查看结果。'

    def test_duration_field_passes_terminal_parameters_model(self):
        """生产者带入的时长必须通过 task.completed 注册模型校验（extra=forbid）。"""
        from src.notifications.events import EVENTS
        parameters = {'session_title': '销售日报', 'event_kind': 'completed',
                      'duration_seconds': 332}
        _, validated = EVENTS.validate('task.completed', parameters,
                                       {'type': 'runner_result', 'id': 'runner_1'})
        assert validated['duration_seconds'] == 332


class TestOtherChannelsUnaffected:
    """duration_seconds 为可选新字段：其他渠道现有渲染不读取、输出不变。"""

    def test_inbox_render_ignores_duration(self):
        from src.notifications.events import render_inbox_text
        base = {'session_title': '日报', 'event_kind': 'completed'}
        assert (render_inbox_text('task.completed', base)
                == render_inbox_text('task.completed', dict(base, duration_seconds=332)))

    def test_web_push_payload_ignores_duration(self):
        from datetime import datetime

        from src.notifications.web_push import WebPushAdapter
        adapter = WebPushAdapter(MagicMock())
        target = {'installation_ref': 'inst1', 'binding_version': 1}
        base = {'notification_ref': 'ref_d', 'event_type': 'task.completed',
                'expires_at': datetime(2026, 11, 14),
                'parameters': {'session_title': '日报', 'event_kind': 'completed'}}
        without = adapter.render(dict(base), target)
        with_duration = adapter.render(dict(
            base, parameters=dict(base['parameters'], duration_seconds=332)), target)
        assert with_duration == without


# ============== errcode 分类（设计 §4.3 严格映射） ==============

class TestInterpret:
    @pytest.mark.parametrize('errcode,expected,reason', [
        (0, 'accepted', None),
        (40031, 'target_invalid', 'wecom_userid_invalid'),
        (43004, 'target_invalid', 'wecom_userid_invalid'),
        (81013, 'target_invalid', 'wecom_userid_invalid'),
        (42009, 'retryable', 'rate_limited'),
        (45009, 'retryable', 'rate_limited'),
        (40001, 'permanent_failure', 'provider_rejected'),
        (40058, 'permanent_failure', 'provider_rejected'),
    ])
    def test_errcode_mapping(self, errcode, expected, reason):
        result = make_adapter()._interpret(errcode, {'errcode': errcode})
        assert (result.outcome, result.error_class) == (expected, reason)

    def test_accepted_carries_msgid(self):
        result = make_adapter()._interpret(0, {'errcode': 0, 'msgid': 'msg_9'})
        assert result.provider_id == 'msg_9'
        assert make_adapter()._interpret(0, {'errcode': 0}).provider_id is None

    def test_unparseable_body_is_permanent(self):
        result = make_adapter()._interpret(None, None)
        assert (result.outcome, result.error_class) == ('permanent_failure', 'provider_rejected')


# ============== send（fake 连接全分支） ==============

class TestSend:
    def test_accepted_payload_shape(self, monkeypatch):
        calls, _ = make_api(monkeypatch, lambda call, state: (200, {'errcode': 0, 'msgid': 'm1'}))
        result = make_adapter(timeout_seconds=7).send(TARGET, TEXTCARD, 20)
        assert (result.outcome, result.provider_id) == ('accepted', 'm1')
        gettoken = next(call for call in calls if 'gettoken' in call[1])
        # secret 只进请求路径（不落日志由日志测试覆盖）；corpid/secret 已编码
        assert gettoken[1].startswith('/cgi-bin/gettoken?corpid=corp123&corpsecret=app-secret')
        send = calls[-1]
        assert send[1].startswith('/cgi-bin/message/send?access_token=token_')
        body = json.loads(send[2])
        # touser 单 userid、agentid int（设计决策 1/5）
        assert body == {'touser': 'zhangsan', 'msgtype': 'textcard', 'agentid': 1000001,
                        'textcard': {'title': TEXTCARD['title'], 'description': TEXTCARD['description'],
                                     'url': TEXTCARD['url'], 'btntxt': '点击查看'}}

    @pytest.mark.parametrize('errcode,expected,reason', [
        (40031, 'target_invalid', 'wecom_userid_invalid'),
        (42009, 'retryable', 'rate_limited'),
        (40001, 'permanent_failure', 'provider_rejected'),
    ])
    def test_errcode_outcomes(self, monkeypatch, errcode, expected, reason):
        make_api(monkeypatch, lambda call, state: (200, {'errcode': errcode}))
        result = make_adapter().send(TARGET, TEXTCARD, 20)
        assert (result.outcome, result.error_class) == (expected, reason)

    def test_http_5xx_retryable(self, monkeypatch):
        make_api(monkeypatch, lambda call, state: (500, None))
        result = make_adapter().send(TARGET, TEXTCARD, 20)
        assert (result.outcome, result.error_class) == ('retryable', 'provider_unavailable')

    def test_http_429_retryable(self, monkeypatch):
        make_api(monkeypatch, lambda call, state: (429, None))
        result = make_adapter().send(TARGET, TEXTCARD, 20)
        assert (result.outcome, result.error_class) == ('retryable', 'rate_limited')

    def test_http_4xx_permanent(self, monkeypatch):
        make_api(monkeypatch, lambda call, state: (403, None))
        result = make_adapter().send(TARGET, TEXTCARD, 20)
        assert (result.outcome, result.error_class) == ('permanent_failure', 'provider_rejected')

    def test_transport_error_retryable(self, monkeypatch):
        class BrokenConnection:
            def __init__(self, *args):
                raise OSError('unreachable')

            def close(self):
                pass

        monkeypatch.setattr(wecom_app, 'PinnedHTTPSConnection', BrokenConnection)
        monkeypatch.setattr(wecom_app, 'public_addresses', lambda host: ['93.184.216.34'])
        result = make_adapter().send(TARGET, TEXTCARD, 20)
        assert (result.outcome, result.error_class) == ('retryable', 'transport_unavailable')

    def test_gettoken_failure_is_retryable_without_send(self, monkeypatch):
        calls = install_fake_api(
            monkeypatch, lambda call: (200, {'errcode': 40013, 'errmsg': 'invalid corpid'}))
        result = make_adapter().send(TARGET, TEXTCARD, 20)
        assert (result.outcome, result.error_class) == ('retryable', 'provider_authentication')
        assert not [call for call in calls if 'message/send' in call[1]]

    def test_private_resolution_permanent(self, monkeypatch):
        def forbidden(host):
            raise NotificationError('NOTIFICATION_ENDPOINT_FORBIDDEN')

        monkeypatch.setattr(wecom_app, 'public_addresses', forbidden)
        result = make_adapter().send(TARGET, TEXTCARD, 20)
        assert (result.outcome, result.error_class) == ('permanent_failure', 'endpoint_forbidden')

    def test_bad_configuration_is_permanent(self, monkeypatch):
        install_fake_api(monkeypatch, lambda call: (200, {'errcode': 0}))
        result = make_adapter().send({'bound_user_name': '缺 userid'}, TEXTCARD, 20)
        assert (result.outcome, result.error_class) == ('permanent_failure', 'provider_configuration')

    def test_send_uses_tenant_credentials(self, monkeypatch):
        """五轮（租户级配置）：send 凭据按 target 配置的 tenant_id 解析——
        corp_id/agentid/secret 全部来自该租户的渠道配置。"""
        captured = {}
        credentials = WecomAppCredentials(tenant_id='tenant-9', corp_id='corp999',
                                          agentid=2000002, secret='tenant-secret')

        def source(tenant_id):
            captured['tenant_id'] = tenant_id
            return credentials if tenant_id == 'tenant-9' else None

        calls, _ = make_api(monkeypatch, lambda call, state: (200, {'errcode': 0}))
        target = dict(TARGET, tenant_id='tenant-9')
        result = WecomAppAdapter(credentials_source=source).send(target, TEXTCARD, 20)
        assert result.outcome == 'accepted'
        assert captured['tenant_id'] == 'tenant-9'
        gettoken = next(call for call in calls if 'gettoken' in call[1])
        assert gettoken[1].startswith('/cgi-bin/gettoken?corpid=corp999&corpsecret=tenant-secret')
        body = json.loads(calls[-1][2])
        assert body['agentid'] == 2000002 and body['touser'] == 'zhangsan'

    def test_send_without_tenant_credentials_permanent(self, monkeypatch):
        """租户未配置通知应用（凭据解析 None）：配置类失败不重试。"""
        make_api(monkeypatch, lambda call, state: (200, {'errcode': 0}))
        result = make_adapter(configured=False).send(TARGET, TEXTCARD, 20)
        assert (result.outcome, result.error_class) == ('permanent_failure', 'provider_configuration')

    def test_send_without_tenant_in_configuration_permanent(self, monkeypatch):
        """target 配置缺 tenant_id（无租户上下文）：fail-closed 不外呼。"""
        calls = install_fake_api(monkeypatch, lambda call: (200, {'errcode': 0}))
        result = make_adapter().send({'wecom_userid': 'zhangsan'}, TEXTCARD, 20)
        assert (result.outcome, result.error_class) == ('permanent_failure', 'provider_configuration')
        assert calls == []  # 未解析到凭据，连 gettoken 都不发

    def test_tenant_token_cached_across_sends(self, monkeypatch):
        """同一租户两次发送：token 按租户缓存复用（第二次发送不重新 gettoken）。"""
        calls, state = make_api(monkeypatch, lambda call, state: (200, {'errcode': 0}))
        adapter = make_adapter()
        assert adapter.send(TARGET, TEXTCARD, 20).outcome == 'accepted'
        assert adapter.send(TARGET, TEXTCARD, 20).outcome == 'accepted'
        assert state['count'] == 1


# ============== token 缓存与刷新重试（设计 §4.3） ==============

class TestTokenHandling:
    def test_token_cached_across_sends(self, monkeypatch):
        calls, state = make_api(monkeypatch, lambda call, state: (200, {'errcode': 0}))
        adapter = make_adapter()
        assert adapter.send(TARGET, TEXTCARD, 20).outcome == 'accepted'
        assert adapter.send(TARGET, TEXTCARD, 20).outcome == 'accepted'
        assert state['count'] == 1  # 第二次发送复用缓存 token

    def test_token_expired_refreshes_once_and_succeeds(self, monkeypatch):
        attempts = {'count': 0}

        def responder(call, state):
            attempts['count'] += 1
            return (200, {'errcode': 40014}) if attempts['count'] == 1 else (200, {'errcode': 0})

        calls, state = make_api(monkeypatch, responder)
        result = make_adapter().send(TARGET, TEXTCARD, 20)
        assert result.outcome == 'accepted'
        assert attempts['count'] == 2 and state['count'] == 2  # 刷新后用新 token 重试一次
        tokens = [call[1].split('access_token=', 1)[1] for call in calls if 'message/send' in call[1]]
        assert tokens == ['token_1', 'token_2']

    def test_token_expired_42001_refreshes_once(self, monkeypatch):
        attempts = {'count': 0}

        def responder(call, state):
            attempts['count'] += 1
            return (200, {'errcode': 42001}) if attempts['count'] == 1 else (200, {'errcode': 0})

        make_api(monkeypatch, responder)
        assert make_adapter().send(TARGET, TEXTCARD, 20).outcome == 'accepted'

    def test_persistent_token_expired_stays_retryable(self, monkeypatch):
        make_api(monkeypatch, lambda call, state: (200, {'errcode': 42001}))
        result = make_adapter().send(TARGET, TEXTCARD, 20)
        assert (result.outcome, result.error_class) == ('retryable', 'token_expired')


# ============== 凭据不落日志 ==============

def test_secret_and_token_never_logged(monkeypatch):
    records = []
    sink = logger.add(lambda msg: records.append(str(msg)), level='WARNING')
    try:
        install_fake_api(monkeypatch, lambda call: (200, {'errcode': 40013}))
        adapter = make_adapter()
        result = adapter.send(TARGET, TEXTCARD, 20)
        assert result.outcome == 'retryable'
    finally:
        logger.remove(sink)
    joined = ''.join(records)
    assert 'app-secret' not in joined and 'corpsecret' not in joined
    assert 'access_token=' not in joined


# ============== delivery 写入（通知核心既有机制） ==============

def _repo_with_adapter(configured=True):
    repo = NotificationRepository.__new__(NotificationRepository)
    repo.registry = AdapterRegistry([make_adapter(configured=configured)])
    return repo


class TestDeliveryWrite:
    TARGET_ROW = {'target_id': 'target_w1', 'channel': 'wecom_app', 'provider': 'wecom_app',
                  'tenant_id': 't1',
                  'binding_version': 2, 'installation_ref': 'wecom_bind_u1'}

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
        assert values[2] == 'target_w1' and values[3] == 2
        # 渲染产物是 textcard 结构（send 时再拼 touser/agentid）
        rendered = unprotect(values[4])
        assert rendered['title'] == '销售日报相关任务已完成'
        assert rendered['url'].endswith('/notification?ref=ref1')
        assert route == [{'target_id': 'target_w1', 'channel': 'wecom_app',
                          'provider': 'wecom_app', 'binding_version': 2}]

    def test_append_delivery_skips_unavailable_adapter(self):
        """target 租户未配置通知应用（凭据解析 None）→ 不产生 delivery。"""
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


# ============== expand 渠道事件白名单（评审回炉 P2-5） ==============

class StubWebPushAdapter:
    """expand 资格对照用：web_push 渠道桩（不参与白名单）。"""
    channel = provider = 'web_push'
    capabilities = AdapterCapabilities()

    def available(self):
        return True

    def render(self, message, target):
        return {'ref': message['notification_ref']}


def expand_message(event_type):
    now = datetime(2026, 11, 13, 10, 0, 0)
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
    return {'target_id': 'target_w1', 'scope_key': 'tenant:t1', 'tenant_id': 't1',
            'recipient_user_id': 'u1', 'channel': channel, 'provider': channel,
            'installation_ref': 'inst_1', 'binding_version': 1, 'state': 'active',
            'rule_id': None, 'enabled_since': datetime(2026, 1, 1),
            'preference_enabled': True, 'preference_since': datetime(2026, 1, 1),
            'event_types': list(TASK_EVENT_TYPES)}


class TestExpandProviderEventGate:
    """企微渠道仅终态通知（设计决策 3/§4.3）：waiting_input 等过程事件即使偏好
    启用也不产生 delivery；其他渠道与企微终态事件不受影响。五轮（租户级配置）：
    expand 投递可用性按 target 租户判定——租户未配置通知应用不投递。"""

    def _expand(self, event_type, channel='wecom_app', configured=True, target_tenant='t1'):
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

    def test_waiting_input_not_delivered_to_enabled_wecom_app_target(self):
        assert self._expand('task.waiting_input', 'wecom_app') == 0

    def test_waiting_input_still_delivered_to_web_push_target(self):
        assert self._expand('task.waiting_input', 'web_push') == 1

    def test_completed_still_delivered_to_wecom_app_target(self):
        assert self._expand('task.completed', 'wecom_app') == 1

    def test_completed_not_delivered_when_tenant_unconfigured(self):
        """五轮（租户级配置）：target 租户没有通知应用配置 → 不产生 delivery。"""
        assert self._expand('task.completed', 'wecom_app', configured=False) == 0

    def test_completed_not_delivered_when_target_tenant_unconfigured(self):
        """凭据按 target 租户隔离：t1 配置不给 t2 的 target 用。"""
        assert self._expand('task.completed', 'wecom_app', target_tenant='t2') == 0
