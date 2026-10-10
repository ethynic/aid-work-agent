"""Tenant outbound group-robot webhook adapters (feishu/dingtalk/wecom).

共用基类：pinned HTTPS（域名白名单 + 公网地址解析校验，复用 web_push 出站防护）、
SendResult 规范化（限流 -> retryable，URL 失效 -> target_invalid）。
安全约定：机器人安全设置使用「自定义关键词」，外发文本统一带【AI工作台通知】前缀命中。
"""
import hashlib
import http.client
import json

from .contracts import AdapterCapabilities, NotificationError, SendResult
from .web_push import PinnedHTTPSConnection, endpoint_parts, public_addresses


def _json_object(raw):
    try:
        data = json.loads(raw or b'{}')
    except (ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


class ImWebhookAdapterBase:
    channel = 'im_webhook'
    capabilities = AdapterCapabilities(payload_limit=4096)
    provider_hosts: tuple = ()

    def __init__(self, timeout_seconds=10):
        self.timeout_seconds = timeout_seconds

    def available(self):
        return bool(self.provider_hosts)

    def validate_target(self, configuration):
        from .contracts import ImWebhookConfiguration
        dto = ImWebhookConfiguration.model_validate(configuration)
        self._parts(dto.webhook_url)
        public_addresses(self._parts(dto.webhook_url).hostname)
        return hashlib.sha256(dto.webhook_url.encode()).hexdigest()

    def _parts(self, url):
        return endpoint_parts(url, list(self.provider_hosts))

    def render(self, message, target):
        from .events import render_message_text
        title, body = render_message_text(message['event_type'], message['parameters'])
        return {'schema_version': 1, 'notification_ref': message['notification_ref'],
                'text': f'【AI工作台通知】{title}\n{body}'}

    def _request_body(self, text):
        raise NotImplementedError

    def _interpret(self, raw):
        raise NotImplementedError

    def send(self, configuration, payload, ttl):
        connection = None
        try:
            parts = self._parts(configuration['webhook_url'])
            address = public_addresses(parts.hostname)[0]
            body = json.dumps(self._request_body(payload['text']), ensure_ascii=False).encode()
            connection = PinnedHTTPSConnection(parts.hostname, address, self.timeout_seconds)
            path = parts.path + ('?' + parts.query if parts.query else '')
            connection.request('POST', path, body, {'Content-Type': 'application/json'})
            response = connection.getresponse()
            status = response.status
            raw = response.read(65536)
            if status in (200, 201, 202):
                return self._interpret(raw)
            if status in (404, 410):
                return SendResult('target_invalid', 'webhook_gone')
            if status == 429 or status >= 500:
                return SendResult('retryable', 'rate_limited' if status == 429 else 'provider_unavailable')
            return SendResult('permanent_failure', 'provider_rejected')
        except NotificationError:
            return SendResult('permanent_failure', 'endpoint_forbidden')
        except (OSError, TimeoutError, http.client.HTTPException):
            return SendResult('retryable', 'transport_unavailable')
        except Exception:
            return SendResult('permanent_failure', 'provider_configuration')
        finally:
            if connection is not None:
                connection.close()


class FeishuBotAdapter(ImWebhookAdapterBase):
    provider = 'feishu_bot'
    provider_hosts = ('open.feishu.cn',)

    def _request_body(self, text):
        return {'msg_type': 'text', 'content': {'text': text}}

    def _interpret(self, raw):
        data = _json_object(raw)
        code = data.get('code', data.get('StatusCode')) if data else None
        if code == 0:
            return SendResult('accepted')
        # 关键词不命中、机器人被移除等均为配置类失败，不重试。
        return SendResult('permanent_failure', 'provider_rejected')


class DingtalkBotAdapter(ImWebhookAdapterBase):
    provider = 'dingtalk_bot'
    provider_hosts = ('oapi.dingtalk.com',)

    def _request_body(self, text):
        return {'msgtype': 'text', 'text': {'content': text}}

    def _interpret(self, raw):
        data = _json_object(raw)
        errcode = data.get('errcode') if data else None
        if errcode == 0:
            return SendResult('accepted')
        if errcode in (130101, 130102):
            return SendResult('retryable', 'rate_limited')
        return SendResult('permanent_failure', 'provider_rejected')


class WecomBotAdapter(ImWebhookAdapterBase):
    provider = 'wecom_bot'
    provider_hosts = ('qyapi.weixin.qq.com',)

    def _request_body(self, text):
        return {'msgtype': 'text', 'text': {'content': text}}

    def _interpret(self, raw):
        data = _json_object(raw)
        errcode = data.get('errcode') if data else None
        if errcode == 0:
            return SendResult('accepted')
        if errcode == 93000:
            return SendResult('target_invalid', 'webhook_invalid')
        if errcode == 45009:
            return SendResult('retryable', 'rate_limited')
        return SendResult('permanent_failure', 'provider_rejected')
