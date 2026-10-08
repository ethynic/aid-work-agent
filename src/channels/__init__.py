"""
渠道模块

支持第三方平台（飞书、企业微信、钉钉）的机器人集成

子包（如 wecom_kf 的服务端仓储）被独立服务导入时不得拉起遗留渠道
管理栈；manager/session/callback 保持 `from src.channels import X` 的
兼容导入，但按需加载（PEP 562）。
"""

from src.channels.base import ChannelAdapter

__all__ = [
    "ChannelAdapter",
    "ChannelManager",
    "channel_manager",
    "ChannelSessionManager",
    "channel_session_manager",
    "callback",
]


def __getattr__(name):
    import importlib
    if name in ("ChannelManager", "channel_manager"):
        return getattr(importlib.import_module("src.channels.manager"), name)
    if name in ("ChannelSessionManager", "channel_session_manager"):
        return getattr(importlib.import_module("src.channels.session"), name)
    if name == "callback":
        return importlib.import_module("src.channels.callback")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
