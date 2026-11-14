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
        # 企微应用通知（wecom-app-task-notification-design §4.3；五轮：租户级
        # 配置改造）：凭据租户自备（连接中心渠道配置 wecom_app_notify），可用性
        # gate 走 WecomAppAdapter.available_for_tenant（repository 租户感知点），
        # 全局 available 恒 False；未配置租户 registry 命中但不投递
        WecomAppAdapter(config.timeout_seconds),
    ]))
