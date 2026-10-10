"""Composition root; legacy NotificationService callers retain their old contract."""
from .contracts import AdapterRegistry
from .im_webhook import DingtalkBotAdapter, FeishuBotAdapter, WecomBotAdapter
from .repository import NotificationRepository
from .smtp_email import SmtpEmailAdapter
from .web_push import WebPushAdapter


def notification_repository(config=None):
    if config is None:
        from src.config.settings import settings
        config = settings.notifications
    return NotificationRepository(config, AdapterRegistry([
        WebPushAdapter(config.web_push),
        FeishuBotAdapter(config.timeout_seconds),
        DingtalkBotAdapter(config.timeout_seconds),
        WecomBotAdapter(config.timeout_seconds),
        SmtpEmailAdapter(),
    ]))
