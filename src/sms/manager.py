"""
短信管理器

管理所有短信发送器，根据配置选择当前使用的通道
"""

from typing import Dict, Optional

from loguru import logger

from .base import SmsSender
from .zhutong import ZhuTongSmsSender


class SmsManager:
    """
    短信管理器

    管理：
    - 短信发送器注册
    - 根据配置获取当前通道发送器
    """

    def __init__(self):
        """初始化"""
        self._senders: Dict[str, SmsSender] = {}

    def register(self, sender: SmsSender) -> None:
        """
        注册短信发送器

        Args:
            sender: 发送器实例
        """
        name = sender.channel_name
        self._senders[name] = sender
        logger.info(f"注册短信发送器: {name}")

    def unregister(self, channel_name: str) -> None:
        """
        注销短信发送器

        Args:
            channel_name: 通道名称
        """
        if channel_name in self._senders:
            del self._senders[channel_name]
            logger.info(f"注销短信发送器: {channel_name}")

    def get_sender(self, channel_name: Optional[str] = None) -> Optional[SmsSender]:
        """
        获取短信发送器

        Args:
            channel_name: 通道名称，为None则返回配置中的通道

        Returns:
            发送器实例或None
        """
        from src.config.settings import settings
        if channel_name is None:
            channel_name = settings.sms.channel
        if not channel_name:
            return None
        return self._senders.get(channel_name)

    def has_sender(self, channel_name: str) -> bool:
        """
        检查发送器是否存在

        Args:
            channel_name: 通道名称

        Returns:
            是否存在
        """
        return channel_name in self._senders

    def list_channels(self) -> list:
        """
        列出所有已注册通道

        Returns:
            通道名称列表
        """
        return list(self._senders.keys())

    def send(
        self,
        mobile: str,
        template_id: Optional[str] = None,
        template_params: Optional[Dict[str, str]] = None,
        channel_name: Optional[str] = None,
    ) -> Optional[Dict]:
        """
        发送短信

        Args:
            mobile: 手机号
            template_id: 模板ID
            template_params: 模板参数
            channel_name: 指定通道，None使用配置

        Returns:
            发送结果，None表示没有可用通道
        """
        sender = self.get_sender(channel_name)
        if sender is None:
            logger.error(f"未找到短信发送器: {channel_name}")
            return None
        if not sender.is_available():
            logger.error(f"短信发送器不可用（配置不完整）: {sender.channel_name}")
            return None
        return sender.send(mobile, template_id, template_params)


# 全局短信管理器
sms_manager = SmsManager()

# 注册默认发送器
sms_manager.register(ZhuTongSmsSender())
