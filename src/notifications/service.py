"""Composition root; legacy NotificationService callers retain their old contract."""
from .contracts import AdapterRegistry
from .im_webhook import DingtalkBotAdapter, FeishuBotAdapter, WecomBotAdapter
from .repository import NotificationRepository
from .smtp_email import SmtpEmailAdapter
from .wecom_app import WecomAppAdapter
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
        # 企微应用通知（wecom-app-task-notification-design §4.3）：enabled/凭据
        # gate 在 WecomAppAdapter.available，未启用时 registry 命中但 available
        # 为 False，repository 既有 gate 不投递
        WecomAppAdapter(config.wecom_app, config.timeout_seconds),
    ]))
