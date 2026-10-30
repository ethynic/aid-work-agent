"""IM 群机器人适配器：目标校验（白名单 + SSRF）、渲染前缀、errcode 语义与出站映射。"""
import json
from unittest.mock import patch

import pytest

from src.notifications import im_webhook, web_push
from src.notifications.contracts import NotificationError
from src.notifications.im_webhook import (
    DingtalkBotAdapter,
    FeishuBotAdapter,
    ImWebhookAdapterBase,
    WecomBotAdapter,
)

FEISHU_URL = 'https://open.feishu.cn/open-apis/bot/v2/hook/abc123'


def _resolver(addresses):
    """构造 socket.getaddrinfo 替身，条目形如 (family, type, proto, canon, (addr, port))。"""
    return lambda *a, **k: [(0, 0, 0, '', (addr, 443)) for addr in addresses]


@pytest.fixture
def public_ok(monkeypatch):
    monkeypatch.setattr(im_webhook, 'public_addresses', lambda host: ['93.184.216.34'])


class TestValidateTarget:
    def test_valid_webhook_returns_stable_digest(self, public_ok):
        adapter = FeishuBotAdapter()
        first = adapter.validate_target({'webhook_url': FEISHU_URL})
        second = adapter.validate_target({'webhook_url': FEISHU_URL})
        assert first == second and len(first) == 64

    @pytest.mark.parametrize('url', [
        'http://open.feishu.cn/open-apis/bot/v2/hook/abc',      # 非 https
        'https://evil.example.com/open-apis/bot/v2/hook/abc',   # 域名不在白名单
        'https://user@open.feishu.cn/hook/abc',                 # 携带凭据
        'https://open.feishu.cn:8443/hook/abc',                 # 非 443 端口
        'https://192.168.1.10/hook/abc',                        # 裸 IP
        'https://open.feishu.cn/hook/abc#frag',                 # fragment
        'not a url',
    ])
    def test_invalid_webhook_rejected(self, url):
        with pytest.raises(NotificationError, match='NOTIFICATION_ENDPOINT_FORBIDDEN'):
            FeishuBotAdapter().validate_target({'webhook_url': url})

    def test_private_dns_resolution_rejected(self, monkeypatch):
        # 域名合法但解析到内网地址 -> 真实 public_addresses 守卫抛 SSRF 拦截
        monkeypatch.setattr(im_webhook, 'public_addresses',
                            lambda host: web_push.public_addresses(host, resolver=_resolver(['10.0.0.5'])))
        with pytest.raises(NotificationError):
            FeishuBotAdapter().validate_target({'webhook_url': FEISHU_URL})

    def test_cross_provider_host_rejected(self, public_ok):
        # 飞书适配器不接受企微域名
        with pytest.raises(NotificationError):
            FeishuBotAdapter().validate_target({'webhook_url': 'https://qyapi.weixin.qq.com/hook/abc'})


class TestRender:
    def test_render_prefixes_keyword_tag(self):
        message = {'notification_ref': 'ref1', 'event_type': 'billing.credit_low', 'parameters': {
            'tenant_name': '宏陶', 'daily_avg_credits': 5.0, 'estimated_days': 2,
            'remaining_credits': 10.0}}
        payload = FeishuBotAdapter().render(message, {})
        assert payload['text'].startswith('【AI通知】')
        assert '积分即将耗尽提醒' in payload['text']
        assert payload['notification_ref'] == 'ref1'

    def test_render_test_event(self):
        message = {'notification_ref': 'ref2', 'event_type': 'notification.test', 'parameters': {
            'session_title': '浏览器通知测试', 'event_kind': 'test'}}
        payload = DingtalkBotAdapter().render(message, {})
        assert '通知渠道测试' in payload['text']


class TestInterpret:
    @pytest.mark.parametrize('raw,expected', [
        (b'{"code":0}', 'accepted'),
        (b'{"StatusCode":0}', 'accepted'),
        (b'{"code":19021,"msg":"keyword not found"}', 'permanent_failure'),
        (b'{"err":"bad json"}', 'permanent_failure'),
        (b'not json', 'permanent_failure'),
    ])
    def test_feishu(self, raw, expected):
        result = FeishuBotAdapter()._interpret(raw)
        assert result.outcome == expected

    @pytest.mark.parametrize('raw,expected', [
        (b'{"errcode":0}', 'accepted'),
        (b'{"errcode":130101}', 'retryable'),
        (b'{"errcode":130102}', 'retryable'),
        (b'{"errcode":310000,"errmsg":"keywords not in content"}', 'permanent_failure'),
    ])
    def test_dingtalk(self, raw, expected):
        result = DingtalkBotAdapter()._interpret(raw)
        assert result.outcome == expected
        if expected == 'retryable':
            assert result.error_class == 'rate_limited'

    @pytest.mark.parametrize('raw,expected,reason', [
        (b'{"errcode":0,"errmsg":"ok"}', 'accepted', None),
        (b'{"errcode":93000}', 'target_invalid', 'webhook_invalid'),
        (b'{"errcode":45009}', 'retryable', 'rate_limited'),
        (b'{"errcode":40001}', 'permanent_failure', 'provider_rejected'),
    ])
    def test_wecom(self, raw, expected, reason):
        result = WecomBotAdapter()._interpret(raw)
        assert result.outcome == expected
        assert result.error_class == reason


class TestRequestBodies:
    def test_provider_body_shapes(self):
        assert FeishuBotAdapter()._request_body('hi') == {'msg_type': 'text', 'content': {'text': 'hi'}}
        assert DingtalkBotAdapter()._request_body('hi') == {'msgtype': 'text', 'text': {'content': 'hi'}}
        assert WecomBotAdapter()._request_body('hi') == {'msgtype': 'text', 'text': {'content': 'hi'}}


class FakeResponse:
    def __init__(self, status, raw):
        self.status = status
        self._raw = raw

    def read(self, size):
        return self._raw


class TestSend:
    def _run_send(self, monkeypatch, status, raw, addresses=None):
        """patch 连接与 DNS，返回 (SendResult, 捕获的请求参数)。"""
        created = {}

        class FakeConnection:
            def __init__(self, host, address, timeout):
                created.update(host=host, address=address, timeout=timeout)
                self.requested = {}

            def request(self, method, path, body, headers):
                created.update(path=path, body=body)

            def getresponse(self):
                return FakeResponse(status, raw)

            def close(self):
                pass

        monkeypatch.setattr(im_webhook, 'PinnedHTTPSConnection', FakeConnection)
        monkeypatch.setattr(im_webhook, 'public_addresses',
                            lambda host: list(addresses or ['93.184.216.34']))
        result = FeishuBotAdapter(timeout_seconds=7).send(
            {'webhook_url': FEISHU_URL}, {'text': '【AI通知】测试'}, 20)
        return result, created

    def test_accepted(self, monkeypatch):
        result, created = self._run_send(monkeypatch, 200, b'{"code":0}')
        assert result.outcome == 'accepted'
        assert created['host'] == 'open.feishu.cn'
        assert created['timeout'] == 7
        body = json.loads(created['body'])
        assert body['msg_type'] == 'text'

    def test_rate_limited_429(self, monkeypatch):
        result, _ = self._run_send(monkeypatch, 429, b'')
        assert (result.outcome, result.error_class) == ('retryable', 'rate_limited')

    def test_server_error_retryable(self, monkeypatch):
        result, _ = self._run_send(monkeypatch, 503, b'')
        assert (result.outcome, result.error_class) == ('retryable', 'provider_unavailable')

    def test_gone_target_invalid(self, monkeypatch):
        result, _ = self._run_send(monkeypatch, 404, b'')
        assert (result.outcome, result.error_class) == ('target_invalid', 'webhook_gone')

    def test_other_4xx_permanent(self, monkeypatch):
        result, _ = self._run_send(monkeypatch, 400, b'')
        assert result.outcome == 'permanent_failure'

    def test_private_resolution_forbidden(self, monkeypatch):
        # send 路径同样经真实 public_addresses 守卫，内网地址 -> endpoint_forbidden 永久失败
        monkeypatch.setattr(im_webhook, 'public_addresses',
                            lambda host: web_push.public_addresses(host, resolver=_resolver(['10.0.0.5'])))
        result = FeishuBotAdapter().send({'webhook_url': FEISHU_URL}, {'text': 'x'}, 20)
        assert (result.outcome, result.error_class) == ('permanent_failure', 'endpoint_forbidden')

    def test_transport_error_retryable(self, monkeypatch):
        class BrokenConnection:
            def __init__(self, *a, **k):
                raise OSError('unreachable')

            def close(self):
                pass

        monkeypatch.setattr(im_webhook, 'PinnedHTTPSConnection', BrokenConnection)
        monkeypatch.setattr(im_webhook, 'public_addresses', lambda host: ['93.184.216.34'])
        result = FeishuBotAdapter().send({'webhook_url': FEISHU_URL}, {'text': 'x'}, 20)
        assert (result.outcome, result.error_class) == ('retryable', 'transport_unavailable')

    def test_endpoint_forbidden_permanent(self, monkeypatch):
        # http 地址在 send 内 _parts 阶段被拦截（例如运行期配置被篡改）
        def forbidden(host):
            raise NotificationError('NOTIFICATION_ENDPOINT_FORBIDDEN')

        monkeypatch.setattr(im_webhook, 'public_addresses', forbidden)
        result = FeishuBotAdapter().send({'webhook_url': FEISHU_URL}, {'text': 'x'}, 20)
        assert (result.outcome, result.error_class) == ('permanent_failure', 'endpoint_forbidden')
