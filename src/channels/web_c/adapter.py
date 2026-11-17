"""官网客服（web_c）渠道：薄壳适配器。

无第三方回调、无加解密；回复经 C 端 API 层 SSE 透传与落库投递，
不经 adapter 推送（send_message 为 no-op）。渠道差异（greeting、
channel_secret、identity_mode）全部在 tenant_channel_configs.config JSON 内。
设计见 docs/system/web-c-channel-design.md §2/§4.1。
"""

from typing import Any, Dict

from loguru import logger

from src.models.message import ChannelType, UnifiedMessage, UnifiedResponse
from src.channels.base import ChannelAdapter


class WebCAdapter(ChannelAdapter):
    """官网客服渠道适配器（薄壳）。

    构造参数来自 tenant_channel_configs.config JSON（ChannelFactory 以
    kwargs 注入：enabled/greeting/channel_secret/identity_mode 等），
    全部可选、未知键忽略，避免配置演进引发构造 TypeError。
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        greeting: str = "",
        channel_secret: str = "",
        identity_mode: str = "both",
        **kwargs,
    ):
        self.enabled = bool(enabled)
        self.greeting = greeting or ""
        self.channel_secret = channel_secret or ""
        self.identity_mode = identity_mode or "both"

    @property
    def channel_type(self) -> str:
        return "web_c"

    async def parse_message(self, raw_message: Dict[str, Any]) -> UnifiedMessage:
        """C 端消息已在 API 层组装落库，此处仅满足基类契约（不参与执行链路）。"""
        return UnifiedMessage.from_text(
            text=str(raw_message.get("text") or ""),
            user_id=str(raw_message.get("user_id") or ""),
            channel_type=ChannelType.WEB_C,
        )

    async def send_message(self, message: UnifiedResponse) -> bool:
        """回复经 SSE 透传与代理层落库投递，adapter 不承担推送职责。"""
        logger.debug("[web_c] send_message no-op（回复经 C 端 API SSE 透传）")
        return True

    async def get_user_info(self, user_id: str) -> Dict[str, Any]:
        """web_c 无第三方用户信息可拉，返回空（身份由签名 name / 匿名 uid 构建）。"""
        return {}
