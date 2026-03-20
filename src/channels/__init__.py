"""
渠道模块

支持第三方平台（飞书、企业微信、钉钉）的机器人集成
"""

from src.channels.base import ChannelAdapter
from src.channels.manager import ChannelManager, channel_manager
from src.channels.session import ChannelSessionManager, channel_session_manager
from src.channels import callback

__all__ = [
    "ChannelAdapter",
    "ChannelManager",
    "channel_manager",
    "ChannelSessionManager",
    "channel_session_manager",
    "callback",
]
