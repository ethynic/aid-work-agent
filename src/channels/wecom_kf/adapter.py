"""企业微信 SCRM（微信客服）适配器

通过微信客服 API 实现 AI Agent 与外部微信用户的对话。

核心流程：
1. 企业微信回调 → WeComKfCallback → 创建 adapter
2. adapter 通过 sync_msg 拉取消息 → 转为 UnifiedMessage
3. Agent 处理 → 生成回复 → 通过 send_msg API 推送
4. 支持人工转接、欢迎语、多客服账号
"""
import asyncio
import hashlib
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger

from src.channels.base import ChannelAdapter
from src.channels.wecom.crypto import WeComCrypto
from src.channels.wecom.message_builder import WeComMessageBuilder
from src.channels.wecom_kf.api_client import WeComKfApiClient
from src.channels.wecom_kf.cursor import CursorManager
from src.channels.wecom_kf.message import markdown_to_plain_text, parse_kf_message
from src.core.redis_client import redis_client
from src.models.message import MessageType, UnifiedMessage, UnifiedResponse


# 人工转接关键词默认值（租户配置未设置时使用）
_DEFAULT_HUMAN_KEYWORDS = ["人工服务", "转人工", "人工客服", "找真人"]


class WeComKfAdapter(ChannelAdapter):
    """微信客服适配器"""

    def __init__(
        self,
        corp_id: str,
        secret: str,
        token: str = "",
        encoding_aes_key: str = "",
        kf_account: Optional[List[Dict[str, Any]]] = None,
        *,
        max_bytes: int = 2048,
        media_upload_dir: str = "./storage/uploads/wecom_kf",
        **kwargs,
    ):
        self.corp_id = corp_id
        self.secret = secret
        self.token = token
        self.encoding_aes_key = encoding_aes_key
        self._max_bytes = max_bytes

        # 客服账号配置列表
        self.kf_accounts: List[Dict[str, Any]] = kf_account or []

        # 加解密模块（复用现有 WeComCrypto）
        self.crypto: Optional[WeComCrypto] = None
        if token and encoding_aes_key and corp_id:
            self.crypto = WeComCrypto(token, encoding_aes_key, corp_id)
            logger.info("微信客服加解密模块已初始化")

        # API 客户端
        self.api_client = WeComKfApiClient(corp_id, secret)

        # 游标管理器
        self.cursor_manager = CursorManager(redis_client)

        # HTTP 连接池（延迟初始化，供 adapter 自身关闭使用）
        self._http_client: Optional[httpx.AsyncClient] = None

        # 当前回调上下文中的客服账号 ID（由回调 handler 设置）
        self.current_open_kfid: str = ""

    @property
    def channel_type(self) -> str:
        return "wecom_kf"

    # ==================== 客服配置查询 ====================

    def get_kf_config(self, open_kfid: str) -> Optional[Dict[str, Any]]:
        """根据 open_kfid 查找对应的客服账号配置"""
        for kf in self.kf_accounts:
            if kf.get("open_kfid") == open_kfid:
                return kf
        return None

    def should_transfer_to_human(self, text: str, kf_config: Dict[str, Any]) -> bool:
        """判断消息是否匹配人工转接关键词"""
        if not text:
            return False
        keywords = kf_config.get("human_transfer_keywords") or _DEFAULT_HUMAN_KEYWORDS
        return any(kw in text for kw in keywords)

    # ==================== 消息解析 ====================

    async def parse_message(self, raw_message: Dict[str, Any]) -> UnifiedMessage:
        """
        解析微信客服消息。

        输入为 sync_msg 返回的 msg_list 中的一条消息 dict。
        """
        return parse_kf_message(raw_message)

    # ==================== 消息发送 ====================

    async def send_message(self, message: UnifiedResponse) -> bool:
        """
        发送统一响应消息。

        流程：markdown → 纯文本 → 拆分 → 逐条发送
        """
        text = message.text
        if not text:
            return True

        plain_text = markdown_to_plain_text(text)
        parts = self._split_message(plain_text)

        all_success = True
        for part in parts:
            result = await self.api_client.send_msg(
                touser=message.reply_to,
                open_kfid=self.current_open_kfid,
                msgtype="text",
                content={"content": part},
            )
            if result.get("errcode", 0) != 0:
                all_success = False
        return all_success

    async def send_welcome_message(self, code: str, welcome_text: str) -> bool:
        """
        发送欢迎语（enter_session 事件中使用）。

        Args:
            code: enter_session 回调中的 Code 字段，用于 send_msg_on_event
            welcome_text: 欢迎语文本
        """
        result = await self.api_client.send_welcome(
            code=code,
            msgtype="text",
            content={"content": welcome_text},
        )
        return result.get("errcode", 0) == 0

    async def send_text(self, text: str, user_id: str) -> bool:
        """发送纯文本消息（供回调流程直接调用）"""
        result = await self.api_client.send_msg(
            touser=user_id,
            open_kfid=self.current_open_kfid,
            msgtype="text",
            content={"content": text},
        )
        return result.get("errcode", 0) == 0

    async def send_waiting_indicator(self, user_id: str, message: str) -> bool:
        """发送等待提示消息"""
        return await self.send_text(message, user_id)

    async def send_long_message(self, text: str, user_id: str) -> bool:
        """发送长消息（自动拆分）"""
        plain_text = markdown_to_plain_text(text)
        parts = self._split_message(plain_text)
        all_success = True
        for part in parts:
            result = await self.api_client.send_msg(
                touser=user_id,
                open_kfid=self.current_open_kfid,
                msgtype="text",
                content={"content": part},
            )
            if result.get("errcode", 0) != 0:
                all_success = False
        return all_success

    # ==================== 人工转接 ====================

    async def transfer_to_human(
        self, open_kfid: str, external_userid: str, servicer_userid: str
    ) -> bool:
        """将会话转接给人工客服"""
        result = await self.api_client.trans_service_state(
            open_kfid=open_kfid,
            external_userid=external_userid,
            service_state=3,  # 人工接待
            servicer_userid=servicer_userid,
        )
        return result.get("errcode", 0) == 0

    # ==================== 用户信息 ====================

    async def get_user_info(self, user_id: str) -> Dict[str, Any]:
        """获取外部微信用户信息（需要 open_kfid 上下文）"""
        if not self.current_open_kfid:
            return {}
        result = await self.api_client.get_customer_info(
            open_kfid=self.current_open_kfid,
            external_userid=user_id,
        )
        if result.get("errcode", 0) != 0:
            return {}
        return {
            "user_id": result.get("external_userid", ""),
            "name": result.get("nickname", ""),
            "avatar": result.get("avatar", ""),
            "gender": result.get("gender", 0),
        }

    # ==================== 签名验证 ====================

    async def verify_signature(
        self, signature: str, timestamp: str, nonce: str, body: str
    ) -> bool:
        """验证回调签名"""
        if not self.token:
            return True
        if self.crypto:
            return self.crypto.verify_signature(signature, timestamp, nonce, body)
        # 降级为 3 元素验证
        items = [self.token, timestamp, nonce]
        items.sort()
        combined = "".join(items)
        calculated = hashlib.sha1(combined.encode()).hexdigest()
        return calculated == signature

    # ==================== 内部方法 ====================

    def _split_message(self, text: str) -> List[str]:
        """拆分长消息（复用 WeComMessageBuilder）"""
        return WeComMessageBuilder.split_long_message(text, self._max_bytes)

    async def close(self):
        """关闭 HTTP 连接池"""
        await self.api_client.close()
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
