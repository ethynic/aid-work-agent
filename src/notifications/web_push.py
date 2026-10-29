"""Web Push encryption via pywebpush; pinned public TLS transport, no redirects."""
import base64
import hashlib
import http.client
import ipaddress
import json
import math
import os
import socket
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from .contracts import AdapterCapabilities, NotificationError, SendResult, WebConfiguration


def public_addresses(host, resolver=socket.getaddrinfo):
    addresses = resolver(host, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise NotificationError('NOTIFICATION_ENDPOINT_FORBIDDEN')
    return list(dict.fromkeys(item[4][0] for item in addresses))


def endpoint_parts(endpoint, allowed_hosts):
    try:
        parts = urlsplit(endpoint)
        host = parts.hostname or ''
        allowed = any(host == item or (item.startswith('*.') and host.endswith(item[1:])
                                      and host != item[2:]) for item in allowed_hosts)
        if (parts.scheme != 'https' or parts.username is not None or parts.password is not None
                or parts.port not in (None, 443) or parts.fragment or not allowed
                or not parts.path or any(ord(c) < 33 or ord(c) > 126 for c in endpoint)):
            raise ValueError()
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError()
        return parts
    except ValueError:
        raise NotificationError('NOTIFICATION_ENDPOINT_FORBIDDEN') from None


def decode_key(value, length):
    try:
        raw = base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True)
        if len(raw) != length:
            raise ValueError()
        return raw
    except (ValueError, TypeError):
        raise NotificationError('NOTIFICATION_INVALID_SUBSCRIPTION') from None


def retry_after_seconds(value, now=None):
    if not value:
        return None
    try:
        delay = int(value) if value.isdigit() else math.ceil(
            (parsedate_to_datetime(value) - (now or datetime.now(timezone.utc))).total_seconds())
        return max(1, min(delay, 86400))
    except (ValueError, TypeError, OverflowError):
        return None


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, address, timeout):
        super().__init__(host, port=443, timeout=timeout)
        self.address = address

    def connect(self):
        # The socket destination uses the SAME checked resolution; TLS still
        # verifies the original provider hostname. No ambient proxy or redirect.
        sock = socket.create_connection((self.address, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


class WebPushAdapter:
    channel = provider = 'web_push'
    capabilities = AdapterCapabilities()

    def __init__(self, config):
        self.config = config

    def available(self):
        if not (self.config.enabled and self.config.public_key and self.config.private_key.get_secret_value()
                and self.config.subject and self.config.allowed_hosts):
            return False
        try:
            from py_vapid import Vapid
            import pywebpush  # Availability includes the installed encryption dependency.
            private = self.config.private_key.get_secret_value()
            key = Vapid.from_file(private) if os.path.isfile(private) else Vapid.from_string(private)
            public = key.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
            contact = urlsplit(self.config.subject)
            return (decode_key(self.config.public_key, 65) == public
                    and contact.scheme in ('mailto', 'https') and bool(contact.path or contact.hostname))
        except Exception:
            return False

    def _private_key(self, key_id):
        if key_id == self.config.key_id:
            return self.config.private_key.get_secret_value()
        old = self.config.previous_keys.get(key_id)
        if old and old.accept_until and old.accept_until.tzinfo and old.accept_until > datetime.now(timezone.utc):
            return old.private_key.get_secret_value()
        raise NotificationError('NOTIFICATION_VAPID_KEY_UNAVAILABLE', 503)

    def validate_target(self, configuration):
        dto = WebConfiguration.model_validate(configuration)
        endpoint_parts(dto.endpoint, self.config.allowed_hosts)
        public_addresses(urlsplit(dto.endpoint).hostname)
        raw = decode_key(dto.keys.p256dh, 65)
        try:
            ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
        except ValueError:
            raise NotificationError('NOTIFICATION_INVALID_SUBSCRIPTION') from None
        decode_key(dto.keys.auth, 16)
        if dto.key_id != self.config.key_id:
            raise NotificationError('NOTIFICATION_VAPID_KEY_CHANGED', 409)
        if dto.expirationTime is not None and dto.expirationTime <= time.time() * 1000:
            raise NotificationError('NOTIFICATION_SUBSCRIPTION_EXPIRED')
        return hashlib.sha256(dto.endpoint.encode()).hexdigest()

    def render(self, message, target):
        # task.paused/task.needs_action 参数以 task_name 映射标题（paused 无
        # session_title 字段；needs_action 的 session_title 即任务名），event_kind
        # 取 'paused'/'needs_action'，其余 task.* 事件沿用参数原字段（Phase 3，
        # 设计 §8.1 全渠道渲染）。
        if message['event_type'] == 'task.paused':
            session_title = message['parameters']['task_name']
            event_kind = 'paused'
        elif message['event_type'] == 'task.needs_action':
            session_title = message['parameters']['task_name']
            event_kind = 'needs_action'
        else:
            session_title = message['parameters']['session_title']
            event_kind = message['parameters']['event_kind']
        return {'schema_version':1, 'notification_ref':message['notification_ref'],
                'session_title':session_title,
                'event_kind':event_kind,
                'installation_ref':target['installation_ref'],
                'binding_version':target['binding_version'],
                'expiry':int(message['expires_at'].timestamp())}

    def send(self, configuration, payload, ttl):
        connection = None
        try:
            from pywebpush import WebPusher
            from py_vapid import Vapid
            if configuration.get('expirationTime') is not None and configuration['expirationTime'] <= time.time()*1000:
                return SendResult('target_invalid', 'subscription_expired')
            parts = endpoint_parts(configuration['endpoint'], self.config.allowed_hosts)
            address = public_addresses(parts.hostname)[0]
            private = self._private_key(configuration['key_id'])
            key = Vapid.from_file(private) if os.path.isfile(private) else Vapid.from_string(private)
            claims = {'aud':'https://' + parts.netloc, 'sub':self.config.subject, 'exp':int(time.time())+3600}
            headers = {**key.sign(claims), 'TTL':str(max(1, ttl)), 'Content-Encoding':'aes128gcm',
                       'Content-Type':'application/octet-stream', 'Urgency':'normal'}
            data = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()
            if len(data) > self.capabilities.payload_limit:
                return SendResult('permanent_failure', 'payload_too_large')
            encrypted = WebPusher(configuration).encode(data)['body']
            connection = PinnedHTTPSConnection(parts.hostname, address, self.config.timeout_seconds)
            connection.request('POST', parts.path + ('?' + parts.query if parts.query else ''), encrypted, headers)
            response = connection.getresponse()
            status = response.status
            if status in (200, 201, 202):
                return SendResult('accepted')
            if status in (404, 410):
                return SendResult('target_invalid', 'subscription_gone')
            if status == 429 or status >= 500:
                return SendResult('retryable', 'rate_limited' if status == 429 else 'provider_unavailable',
                                  retry_after_seconds(response.getheader('Retry-After')))
            if status in (401, 403):
                return SendResult('retryable', 'provider_authentication')
            return SendResult('permanent_failure', 'provider_rejected')
        except NotificationError as error:
            return SendResult('permanent_failure', 'key_unavailable' if error.status == 503 else 'endpoint_forbidden')
        except (OSError, TimeoutError, http.client.HTTPException):
            return SendResult('retryable', 'transport_unavailable')
        except Exception:
            # pywebpush exceptions may contain endpoints, keys or response bodies.
            return SendResult('permanent_failure', 'provider_configuration')
        finally:
            if connection is not None:
                connection.close()
