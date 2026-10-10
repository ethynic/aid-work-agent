import base64
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from src.config.settings import WebPushConfig
from src.notifications.contracts import NotificationError, session_title
from src.notifications import web_push
from src.notifications.web_push import WebPushAdapter, endpoint_parts, public_addresses, retry_after_seconds


def subscription():
    key = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(Encoding.X962,PublicFormat.UncompressedPoint)
    encode = lambda b: base64.urlsafe_b64encode(b).decode().rstrip('=')
    return {'endpoint':'https://fcm.googleapis.com/push/test','key_id':'v1',
            'keys':{'p256dh':encode(key),'auth':encode(b'a'*16)},'expirationTime':None}


@pytest.mark.parametrize('endpoint', [
    'http://fcm.googleapis.com/path','https://fcm.googleapis.com.evil.test/path',
    'https://user:password@fcm.googleapis.com/path','https://fcm.googleapis.com:444/path',
    'https://127.0.0.1/path','https://fcm.googleapis.com/path#frag',
    'https://fcm.googleapis.com/path\r\nX: bad'])
def test_endpoint_rejects_ssrf_and_header_injection(endpoint):
    with pytest.raises(NotificationError):
        endpoint_parts(endpoint,['fcm.googleapis.com'])


def test_resolution_rejects_mixed_public_and_private_addresses():
    resolver=lambda *a,**kw:[(2,1,6,'',('8.8.8.8',443)),(2,1,6,'',('127.0.0.1',443))]
    with pytest.raises(NotificationError):
        public_addresses('fcm.googleapis.com',resolver)


def test_subscription_keys_and_rotation_are_strict(monkeypatch):
    monkeypatch.setattr(web_push,'public_addresses',lambda _: ['8.8.8.8'])
    adapter=WebPushAdapter(WebPushConfig())
    data=subscription()
    assert len(adapter.validate_target(data))==64
    data['keys']['auth']='bad'
    with pytest.raises(NotificationError,match='INVALID_SUBSCRIPTION'):
        adapter.validate_target(data)
    data=subscription();data['key_id']='old'
    with pytest.raises(NotificationError,match='VAPID_KEY_CHANGED'):
        adapter.validate_target(data)


@pytest.mark.parametrize('status,outcome,error', [
    (201,'accepted',None),(404,'target_invalid','subscription_gone'),
    (410,'target_invalid','subscription_gone'),(429,'retryable','rate_limited'),
    (503,'retryable','provider_unavailable'),(401,'retryable','provider_authentication'),
    (403,'retryable','provider_authentication'),(302,'permanent_failure','provider_rejected')])
def test_real_library_encryption_normalizes_provider_results_without_redirect(monkeypatch,status,outcome,error):
    from py_vapid import Vapid
    key=Vapid();key.generate_keys()
    monkeypatch.setattr(web_push,'public_addresses',lambda _: ['8.8.8.8'])
    monkeypatch.setattr(web_push.WebPushAdapter,'_private_key',lambda *a:'unused')
    monkeypatch.setattr(Vapid,'from_string',lambda *a,**kw:key)
    connection=MagicMock()
    connection.getresponse.return_value.status=status
    connection.getresponse.return_value.getheader.return_value='17'
    monkeypatch.setattr(web_push,'PinnedHTTPSConnection',lambda host,address,timeout:connection)
    config=WebPushConfig(subject='mailto:notifications@example.com')
    result=WebPushAdapter(config).send(subscription(),{'version':1},120)
    assert (result.outcome,result.error_class)==(outcome,error)
    assert result.retry_after==17 if status in (429,503) else result.retry_after is None
    assert connection.request.call_count==1
    args=connection.request.call_args.args
    assert args[0:2]==('POST','/push/test')
    assert isinstance(args[2],bytes) and b'version' not in args[2]
    assert args[3]['TTL']=='120' and args[3]['Content-Encoding']=='aes128gcm'
    connection.close.assert_called_once()


def test_retry_after_dates_and_title_snapshot_sanitization():
    now=datetime(2026,10,10,8,tzinfo=timezone.utc)
    assert retry_after_seconds('Sat, 10 Oct 2026 08:00:30 GMT',now)==30
    assert retry_after_seconds('bad',now) is None
    assert session_title('\n\x00\u202e   ')=='未命名会话'
    assert session_title('a'*100)=='a'*80


def test_pinned_socket_uses_validated_ip_and_original_tls_hostname(monkeypatch):
    sock=MagicMock()
    connect=MagicMock(return_value=sock)
    monkeypatch.setattr(web_push.socket,'create_connection',connect)
    conn=web_push.PinnedHTTPSConnection('fcm.googleapis.com','8.8.8.8',10)
    conn._context=MagicMock()
    conn.connect()
    connect.assert_called_once_with(('8.8.8.8',443),10)
    conn._context.wrap_socket.assert_called_once_with(sock,server_hostname='fcm.googleapis.com')
