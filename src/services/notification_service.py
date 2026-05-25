"""
通用通知服务。

复用场景：
- 投诉升级通知人工客服
- 合同审核完成通知申请人
- 审批流节点变更通知
- 系统告警通知管理员
"""

import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional

from loguru import logger


class NotificationChannel(str, Enum):
    EMAIL = "email"
    WECHAT = "wechat"
    DINGTALK = "dingtalk"
    WEBHOOK = "webhook"


@dataclass
class NotificationMessage:
    """通知消息"""
    title: str
    content: str
    urgency: str  # low / medium / high / critical
    recipient: str
    channel: NotificationChannel
    metadata: Dict[str, Any] = field(default_factory=dict)


class NotificationService:
    """
    通用通知服务。
    """

    def __init__(self):
        self._channel_senders = {
            NotificationChannel.EMAIL: self._send_email,
            NotificationChannel.WEBHOOK: self._send_webhook,
        }

    async def send(self, message: NotificationMessage) -> bool:
        """发送通知"""
        sender = self._channel_senders.get(message.channel)
        if not sender:
            logger.warning(f"通知渠道 {message.channel} 未注册发送器")
            return False

        try:
            result = await sender(message)
            if result:
                logger.info(f"通知发送成功: channel={message.channel}, recipient={message.recipient}, title={message.title}")
            return result
        except Exception as e:
            logger.error(f"通知发送失败: channel={message.channel}, recipient={message.recipient}, error={e}", exc_info=True)
            return False

    async def send_batch(self, messages: List[NotificationMessage]) -> List[bool]:
        """批量发送"""
        return [await self.send(msg) for msg in messages]

    def register_channel(self, channel: NotificationChannel, sender):
        """注册通知渠道发送器"""
        self._channel_senders[channel] = sender
        logger.info(f"注册通知渠道: {channel}")

    async def _send_email(self, message: NotificationMessage) -> bool:
        """通过 SMTP 发送邮件通知"""
        try:
            from src.config.settings import settings

            smtp_server = getattr(settings.tools.email, "smtp_server", None)
            smtp_port = getattr(settings.tools.email, "smtp_port", 465)
            smtp_user = getattr(settings.tools.email, "smtp_user", None)
            smtp_password = getattr(settings.tools.email, "smtp_password", None)

            if not all([smtp_server, smtp_user, smtp_password]):
                logger.warning("邮件通知: SMTP 配置不完整，跳过发送")
                return False

            msg = MIMEMultipart()
            msg["From"] = smtp_user
            msg["To"] = message.recipient
            msg["Subject"] = f"[{message.urgency.upper()}] {message.title}"

            urgency_colors = {
                "low": "#666666",
                "medium": "#f0ad4e",
                "high": "#d9534f",
                "critical": "#d9534f",
            }
            color = urgency_colors.get(message.urgency, "#666666")

            html_body = f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
                <div style="background: {color}; color: white; padding: 12px 20px; border-radius: 4px 4px 0 0;">
                    <h3 style="margin: 0;">{message.title}</h3>
                </div>
                <div style="border: 1px solid #ddd; border-top: none; padding: 20px;">
                    <p>{message.content}</p>
                    <hr style="border: none; border-top: 1px solid #eee; margin: 15px 0;">
                    <p style="color: #999; font-size: 12px;">
                        紧急程度: {message.urgency.upper()}
                        {" | 关联ID: " + message.metadata.get("complaint_id", "") if message.metadata.get("complaint_id") else ""}
                    </p>
                </div>
            </div>
            """
            msg.attach(MIMEText(html_body, "html", "utf-8"))

            import asyncio
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._smtp_send, smtp_server, smtp_port, smtp_user, smtp_password, msg, message.recipient)
            return True
        except Exception as e:
            logger.error(f"邮件发送失败: {e}", exc_info=True)
            return False

    def _smtp_send(self, server, port, user, password, msg, recipient):
        """同步 SMTP 发送"""
        if port == 465:
            with smtplib.SMTP_SSL(server, port) as smtp:
                smtp.login(user, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(server, port) as smtp:
                smtp.starttls()
                smtp.login(user, password)
                smtp.send_message(msg)

    async def _send_webhook(self, message: NotificationMessage) -> bool:
        """通过 Webhook 发送通知"""
        try:
            from src.config.settings import settings

            webhook_url = ""
            notification_cfg = getattr(settings, "notification", None)
            if notification_cfg:
                webhook_cfg = getattr(notification_cfg, "channels", None)
                if webhook_cfg:
                    webhook_url = getattr(webhook_cfg.get("webhook", {}), "default_url", "")

            if not webhook_url:
                logger.warning("Webhook 通知: 未配置 default_url，跳过发送")
                return False

            import httpx
            payload = {
                "title": message.title,
                "content": message.content,
                "urgency": message.urgency,
                "metadata": message.metadata,
            }
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(webhook_url, json=payload)
                return resp.status_code == 200
        except Exception as e:
            logger.error(f"Webhook 发送失败: {e}", exc_info=True)
            return False


notification_service = NotificationService()
