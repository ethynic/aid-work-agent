"""Composition root; legacy NotificationService callers retain their old contract."""
from .contracts import AdapterRegistry
from .dingtalk_app import DingTalkAppAdapter
from .feishu_app import FeishuAppAdapter
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
        # 飞书应用通知（2026-11-15 决策 7 追加，完全复刻企微租户级模式）：凭据
        # 租户自备（飞书渠道配置 notify_usage ∈ {both, notify_only}），可用性
        # gate 走 FeishuAppAdapter.available_for_tenant，全局 available 恒 False
        FeishuAppAdapter(config.timeout_seconds),
        # 钉钉应用通知（2026-11-16 决策 7 追加，同模式复刻）：凭据租户自备
        # （钉钉渠道配置 notify_usage ∈ {both, notify_only}，含 agent_id），可用性
        # gate 走 DingTalkAppAdapter.available_for_tenant，全局 available 恒 False
        DingTalkAppAdapter(config.timeout_seconds),
    ]))
