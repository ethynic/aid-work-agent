"""Tenant outbound email adapter; server-side unified SMTP, no per-tenant credentials."""
import hashlib
import re
import smtplib
from email.header import Header
from email.mime.text import MIMEText

from .contracts import AdapterCapabilities, NotificationError, SendResult

EMAIL_RE = re.compile(r'^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$')


class SmtpEmailAdapter:
    channel = 'email'
    provider = 'smtp'
    capabilities = AdapterCapabilities(payload_limit=65536)

    def __init__(self, smtp_config=None):
        self._smtp_config = smtp_config

    def _config(self):
        if self._smtp_config is not None:
            return self._smtp_config
        from src.config.settings import settings
        return settings.tools.email

    def available(self):
        config = self._config()
        return bool(config.smtp_server and config.smtp_user and config.smtp_password)

    def validate_target(self, configuration):
        from .contracts import EmailConfiguration
        dto = EmailConfiguration.model_validate(configuration)
        address = dto.email_address.strip().lower()
        if not EMAIL_RE.fullmatch(address):
            raise NotificationError('NOTIFICATION_INVALID_EMAIL')
        return hashlib.sha256(('smtp:'+address).encode()).hexdigest()

    def render(self, message, target):
        from .events import render_message_text
        title, body = render_message_text(message['event_type'], message['parameters'])
        return {'schema_version': 1, 'notification_ref': message['notification_ref'],
                'subject': title,
                'body': f'{body}\n\n（此邮件由平台自动发送，请勿回复）'}

    def _smtp_send(self, server, port, user, password, msg):
        if port == 465:
            with smtplib.SMTP_SSL(server, port, timeout=20) as smtp:
                smtp.login(user, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(server, port, timeout=20) as smtp:
                smtp.starttls()
                smtp.login(user, password)
                smtp.send_message(msg)

    def send(self, configuration, payload, ttl):
        # 与 validate_target 同口径小写化，避免注册成功但发送按原始大小写被判无效
        address = configuration['email_address'].strip().lower()
        if not EMAIL_RE.fullmatch(address):
            return SendResult('permanent_failure', 'invalid_recipient')
        config = self._config()
        if not (config.smtp_server and config.smtp_user and config.smtp_password):
            return SendResult('retryable', 'smtp_unconfigured')
        msg = MIMEText(payload['body'], 'plain', 'utf-8')
        msg['From'] = config.smtp_user
        msg['To'] = address
        msg['Subject'] = Header(payload['subject'], 'utf-8')
        try:
            self._smtp_send(config.smtp_server, config.smtp_port,
                            config.smtp_user, config.smtp_password, msg)
            return SendResult('accepted', provider_id=address)
        except smtplib.SMTPAuthenticationError:
            return SendResult('permanent_failure', 'smtp_authentication')
        except smtplib.SMTPRecipientsRefused:
            return SendResult('permanent_failure', 'recipient_refused')
        except (smtplib.SMTPException, OSError, TimeoutError):
            return SendResult('retryable', 'smtp_unavailable')
