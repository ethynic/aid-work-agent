"""邮件适配器：地址校验、渲染后缀、SMTP 异常到 SendResult 的映射。"""
import hashlib
import smtplib
from unittest.mock import MagicMock

import pytest

from src.notifications.contracts import NotificationError, SendResult
from src.notifications.smtp_email import EMAIL_RE, SmtpEmailAdapter


def smtp_config(server='smtp.example.com', port=465, user='noreply@example.com', password='secret'):
    config = MagicMock()
    config.smtp_server = server
    config.smtp_port = port
    config.smtp_user = user
    config.smtp_password = password
    return config


class TestValidateTarget:
    @pytest.mark.parametrize('address', [
        'user@example.com',
        'Name.Surname+tag@Sub.Example.COM',
        'a_b%1@mail-server.co.uk',
    ])
    def test_valid_address_returns_digest(self, address):
        digest = SmtpEmailAdapter().validate_target({'email_address': address})
        expected = hashlib.sha256(('smtp:' + address.strip().lower()).encode()).hexdigest()
        assert digest == expected and len(digest) == 64

    @pytest.mark.parametrize('address', [
        'not-an-email',
        'missing-at@example',
        '@example.com',
        'user@',
        'user name@example.com',
        '用户@example.com',
    ])
    def test_invalid_address_rejected(self, address):
        with pytest.raises(NotificationError, match='NOTIFICATION_INVALID_EMAIL'):
            SmtpEmailAdapter().validate_target({'email_address': address})


class TestRegex:
    def test_email_re_requires_tld(self):
        assert EMAIL_RE.fullmatch('a@b.co')
        assert not EMAIL_RE.fullmatch('a@b')


class TestRender:
    def test_render_has_subject_and_no_reply_suffix(self):
        message = {'notification_ref': 'ref1', 'event_type': 'billing.tenant_expiry', 'parameters': {
            'tenant_name': '宏陶', 'expire_date': '2026-11-09', 'days_left': 30}}
        payload = SmtpEmailAdapter().render(message, {})
        assert payload['subject'] == '租户有效期即将到期'
        assert payload['body'].endswith('（此邮件由平台自动发送，请勿回复）')
        assert '2026-11-09' in payload['body']


class TestSend:
    def _adapter(self, monkeypatch, config=None):
        adapter = SmtpEmailAdapter(smtp_config=config or smtp_config())
        adapter._smtp_send = MagicMock()
        return adapter

    def test_success(self, monkeypatch):
        adapter = self._adapter(monkeypatch)
        result = adapter.send({'email_address': 'admin@example.com'},
                              {'subject': '标题', 'body': '正文'}, 30)
        assert result == SendResult('accepted', provider_id='admin@example.com')
        adapter._smtp_send.assert_called_once()

    def test_unconfigured_is_retryable(self, monkeypatch):
        adapter = self._adapter(monkeypatch, smtp_config(server='', user='', password=''))
        result = adapter.send({'email_address': 'admin@example.com'},
                              {'subject': 's', 'body': 'b'}, 30)
        assert (result.outcome, result.error_class) == ('retryable', 'smtp_unconfigured')
        adapter._smtp_send.assert_not_called()

    def test_invalid_recipient_permanent(self, monkeypatch):
        adapter = self._adapter(monkeypatch)
        result = adapter.send({'email_address': 'bad-address'}, {'subject': 's', 'body': 'b'}, 30)
        assert (result.outcome, result.error_class) == ('permanent_failure', 'invalid_recipient')

    def test_uppercase_recipient_lowered_consistently(self, monkeypatch):
        # validate_target 接受大小写混合（内部小写化），send 同口径，不得按原始大小写拒发
        adapter = self._adapter(monkeypatch)
        result = adapter.send({'email_address': 'Admin@Example.COM'},
                              {'subject': 's', 'body': 'b'}, 30)
        assert result.outcome == 'accepted'
        assert result.provider_id == 'admin@example.com'

    @pytest.mark.parametrize('error,expected', [
        (smtplib.SMTPAuthenticationError(535, b'bad credentials'), 'smtp_authentication'),
        (smtplib.SMTPRecipientsRefused({'admin@example.com': (550, b'no mailbox')}), 'recipient_refused'),
        (smtplib.SMTPServerDisconnected('gone'), 'smtp_unavailable'),
        (OSError('network down'), 'smtp_unavailable'),
        (TimeoutError('timed out'), 'smtp_unavailable'),
    ])
    def test_error_mapping(self, monkeypatch, error, expected):
        adapter = self._adapter(monkeypatch)
        adapter._smtp_send.side_effect = error
        result = adapter.send({'email_address': 'admin@example.com'},
                              {'subject': 's', 'body': 'b'}, 30)
        if expected in ('smtp_authentication', 'recipient_refused'):
            assert result.outcome == 'permanent_failure'
        else:
            assert result.outcome == 'retryable'
        assert result.error_class == expected

    def test_port_465_uses_ssl(self):
        sent = {}

        class FakeSMTPSSL:
            def __init__(self, host, port, timeout=None):
                sent['cls'] = 'ssl'

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def login(self, user, password):
                pass

            def send_message(self, msg):
                pass

        original = smtplib.SMTP_SSL
        smtplib.SMTP_SSL = FakeSMTPSSL
        try:
            SmtpEmailAdapter(smtp_config(port=465))._smtp_send(
                'smtp.example.com', 465, 'u', 'p', 'msg-object')
        finally:
            smtplib.SMTP_SSL = original
        assert sent['cls'] == 'ssl'

    def test_non_465_uses_starttls(self, monkeypatch):
        sent = {}

        class FakeSMTP:
            def __init__(self, host, port, timeout=None):
                sent['cls'] = 'plain'

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def starttls(self):
                sent['tls'] = True

            def login(self, user, password):
                pass

            def send_message(self, msg):
                pass

        original = smtplib.SMTP
        smtplib.SMTP = FakeSMTP
        try:
            adapter = SmtpEmailAdapter(smtp_config(port=587))
            adapter._smtp_send('smtp.example.com', 587, 'u', 'p', 'msg-object')
        finally:
            smtplib.SMTP = original
        assert sent['cls'] == 'plain' and sent.get('tls') is True

