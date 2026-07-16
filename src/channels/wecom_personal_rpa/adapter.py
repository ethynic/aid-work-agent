"""企业微信个人账号 RPA 渠道适配器

将统一 ``UnifiedResponse`` 转换为 ``RpaAction`` 列表，经 ``action_client.deliver_actions``
投递给在线客户端或离线 outbox。

**关键约定（Wire 阶段路由在 send_message 前调用）**：
    ``set_reply_context(account_id, conversation_id, session_id, request_id, tenant_id='')``
    必须在 ``send_message`` 之前由回调路由层调用，把当前回调上下文（账号 / 会话 /
    服务端会话 ID / 请求 ID）注入适配器。``send_message`` 依赖该上下文决定投递目标，
    未设置上下文时记 ``logger.error`` 并返回 False。

签名契约：docs/system/wecom-personal-rpa-protocol.md §B.5。
"""
import uuid
from typing import Any, Callable, Dict, List, Optional

from loguru import logger

from src.channels.base import ChannelAdapter, build_public_url
from src.channels.wecom_personal_rpa.action_client import deliver_actions
from src.channels.wecom_personal_rpa.message import parse_rpa_message
from src.channels.wecom_personal_rpa.schemas import (
    NoopAction,
    SendFileAction,
    SendImageAction,
    SendTextAction,
)
from src.models.message import UnifiedMessage, UnifiedResponse

# 文本消息单段最大字符数（企微单条文本上限 2000 字符）
_TEXT_SEGMENT_MAX = 2000


class WeComPersonalRpaAdapter(ChannelAdapter):
    """企业微信个人账号 RPA 渠道适配器。"""

    def __init__(
        self,
        client_id: str,
        encrypted_secret: Optional[str] = None,
        secret_resolver: Optional[Callable[[str], Optional[bytes]]] = None,
        get_secret: Optional[Callable[[str], Optional[bytes]]] = None,
        tenant_id: str = "",
        account_id: str = "",
        **kwargs: Any,
    ):
        self.client_id = client_id
        self.encrypted_secret = encrypted_secret
        # secret_resolver 保留给需要解密 encrypted_secret 的场景（首版未使用，占位）
        self._secret_resolver = secret_resolver
        # get_secret 供 verify_request 使用
        self._get_secret = get_secret
        self.tenant_id = tenant_id
        self.account_id = account_id

        # 回复上下文（由 set_reply_context 注入）
        self._reply: Optional[Dict[str, str]] = None

    # ==================== 回复上下文 ====================

    def set_reply_context(
        self,
        account_id: str,
        conversation_id: str,
        session_id: str,
        request_id: str,
        tenant_id: str = "",
        sender_display_name: Optional[str] = None,
        sender_stable_id: Optional[str] = None,
        conversation_search_name: Optional[str] = None,
        inbound_text: Optional[str] = None,
    ) -> None:
        """注入当前回调上下文，供 send_message 决定投递目标。

        Wire 阶段路由在收到入站回调后、调用 send_message 前必须调用本方法。

        Args:
            account_id: 入站事件归属的个人企微账号 ID。
            conversation_id: 客户端侧会话标识（payload.conversation_id）。
            session_id: 服务端会话 ID（``wecom_personal_rpa:{account_id}:{route_key}``）。
            request_id: 本次回复的请求 ID（用于回执去重）。
            tenant_id: 租户 ID（可选，若提供则覆盖构造时的 tenant_id）。
        """
        self._reply = {
            "account_id": account_id,
            "conversation_id": conversation_id,
            "session_id": session_id,
            "request_id": request_id,
            "sender_display_name": sender_display_name,
            "sender_stable_id": sender_stable_id,
            "conversation_search_name": conversation_search_name,
            "inbound_text": inbound_text,
        }
        if tenant_id:
            self.tenant_id = tenant_id

    # ==================== 渠道标识 ====================

    @property
    def channel_type(self) -> str:
        return "wecom_personal_rpa"

    # ==================== 入站解析（委托 message 模块） ====================

    async def parse_message(self, raw_message: Dict[str, Any]) -> UnifiedMessage:
        """委托 ``message.parse_rpa_message`` 完成 event_type=message 的解析。"""
        return parse_rpa_message(raw_message)

    # ==================== 出站发送 ====================

    @staticmethod
    def _segment_text(text: str) -> List[str]:
        """按 ``_TEXT_SEGMENT_MAX`` 切分长文本，保证每段不超过上限。

        逐字切片（不按词/行），确保总长度严格受控；空字符串返回空列表。
        """
        if not text:
            return []
        return [text[i : i + _TEXT_SEGMENT_MAX] for i in range(0, len(text), _TEXT_SEGMENT_MAX)]

    def _build_actions(self, message: UnifiedResponse) -> List[Any]:
        """把 UnifiedResponse 的 text 与 downloadable_files 转换为 RpaAction 列表。

        - text：每段一个 SendTextAction（超 2000 字分段，产生多个 action）。
        - attachments / downloadable_files：图片 → SendImageAction，否则 SendFileAction。
        - 空 text 且无附件：返回 [NoopAction()]，保证信封非空。
        """
        actions: List[Any] = []

        for segment in self._segment_text(message.text or ""):
            actions.append(SendTextAction(text=segment))

        for f in message.downloadable_files or []:
            public_url = build_public_url(f.download_url)
            mime = (f.mime_type or "").lower()
            filename = f.file_name or "file"
            if "image" in mime:
                actions.append(SendImageAction(file_url=public_url, filename=filename))
            else:
                actions.append(SendFileAction(file_url=public_url, filename=filename))

        # 部分 Agent/工具直接返回 UnifiedResponse.attachments，而不是 downloadable_files。
        # 这里只处理可下载 URL；入站会话存档的 sdkfileid 不会进入该出站模型，二者不可混用。
        for attachment in message.attachments or []:
            if not attachment.url:
                logger.warning("RPA 忽略缺少 URL 的 Agent 出站附件 type={}", attachment.type)
                continue
            public_url = build_public_url(attachment.url)
            mime = (attachment.mime_type or "").lower()
            filename = attachment.name or "file"
            if attachment.type.lower() == "image" or mime.startswith("image/"):
                actions.append(SendImageAction(file_url=public_url, filename=filename))
            else:
                actions.append(SendFileAction(file_url=public_url, filename=filename))

        if not actions:
            actions.append(NoopAction())

        return actions

    async def send_message(self, message: UnifiedResponse) -> bool:
        """把 UnifiedResponse 转换为 actions 并经 ``deliver_actions`` 投递。

        依赖 ``set_reply_context`` 预先注入的上下文；未注入时记 error 并返回 False。
        """
        if not self._reply:
            logger.error(
                "RPA send_message 失败：未调用 set_reply_context 注入回复上下文"
            )
            return False

        search_name = str(self._reply.get("conversation_search_name") or "").strip()
        sender_stable_id = str(self._reply.get("sender_stable_id") or "").strip()
        if (
            not search_name
            or search_name.lower() == "unknown"
            or (sender_stable_id and search_name == sender_stable_id)
        ):
            # external_userid 无法在企微桌面端搜索。宁可拒发，也不能让客户端
            # 误搜/误发给同名或其他联系人；等待姓名解析或管理员人工维护后重试。
            logger.error(
                "RPA send_message 拒绝投递：缺少可靠会话搜索名 account_id={}",
                self._reply.get("account_id"),
            )
            return False

        actions = self._build_actions(message)
        request_id = self._reply["request_id"] or f"req_{uuid.uuid4().hex[:16]}"

        try:
            delivered = await deliver_actions(
                tenant_id=self.tenant_id,
                account_id=self._reply["account_id"],
                conversation_id=self._reply["conversation_id"],
                request_id=request_id,
                session_id=self._reply["session_id"],
                actions=actions,
                reply_context={
                    "sender_display_name": self._reply.get("sender_display_name"),
                    "sender_stable_id": self._reply.get("sender_stable_id"),
                    "conversation_search_name": self._reply.get("conversation_search_name"),
                    "inbound_text": self._reply.get("inbound_text"),
                    "agent_reply_text": message.text or "",
                },
            )
        except Exception as e:
            # deliver_actions 内部已吞异常，此处兜底防御性记录
            logger.error(
                f"RPA send_message 投递异常 request_id={request_id}: {e}"
            )
            return False

        return bool(delivered)

    # ==================== 用户信息 ====================

    async def get_user_info(self, user_id: str) -> Dict[str, Any]:
        """RPA 渠道无统一用户信息接口，返回最小占位（保证 user_id 可回传）。"""
        return {"user_id": user_id}

    # ==================== 签名验证 ====================

    async def verify_signature(
        self, signature: str, timestamp: str, nonce: str, body: str
    ) -> bool:
        """若有 get_secret 则委托 ``auth.verify_request``，否则放行。"""
        if not self._get_secret:
            return True
        try:
            from src.channels.wecom_personal_rpa import auth

            headers = {
                "X-Client-Id": self.client_id,
                "X-Timestamp": timestamp,
                "X-Nonce": nonce,
                "X-Signature": signature,
            }
            result = auth.verify_request(
                headers=headers,
                raw_body=body,
                get_secret=self._get_secret,
            )
            return bool(getattr(result, "ok", False))
        except Exception as e:
            logger.error(f"RPA verify_signature 异常: {e}")
            return False
