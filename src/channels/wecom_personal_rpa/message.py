"""企业微信个人账号 RPA 回调消息解析

将客户端上报的 ``RpaCallbackEnvelope``（event_type=message / status / action_result）
转换为服务端内部使用的 ``UnifiedMessage`` 与对应 payload 模型。

签名契约：docs/system/wecom-personal-rpa-protocol.md §B.3。
权威 Pydantic 模型：src/channels/wecom_personal_rpa/schemas.py。
"""
from typing import Any, Dict

from src.channels.wecom_personal_rpa.schemas import (
    RpaActionResultPayload,
    RpaMessagePayload,
    RpaStatusPayload,
)
from src.models.message import (
    Attachment,
    ChannelType,
    MessageType,
    UnifiedMessage,
)

# InboundMessageType → MessageType 映射表
# text → TEXT；image → IMAGE；file → FILE；voice/video/link/未知 → EVENT
_MESSAGE_TYPE_MAP: Dict[str, MessageType] = {
    "text": MessageType.TEXT,
    "image": MessageType.IMAGE,
    "file": MessageType.FILE,
}


def _map_message_type(message_type: str) -> MessageType:
    """将入站 InboundMessageType 映射为统一 MessageType。

    text/image/file 有直接对应；voice/video/link 及未识别类型降级为 EVENT。
    """
    return _MESSAGE_TYPE_MAP.get(message_type, MessageType.EVENT)


def _convert_attachment(att: Any) -> Attachment:
    """将 RpaAttachment（dict 或 pydantic 实例）转换为统一 Attachment。"""
    if isinstance(att, dict):
        type_ = att.get("type", "")
        url = att.get("url", "")
        name = att.get("name")
        size = att.get("size")
        mime_type = att.get("mime_type")
    else:
        # RpaAttachment pydantic 实例
        type_ = getattr(att, "type", "") or ""
        url = getattr(att, "url", "") or ""
        name = getattr(att, "name", None)
        size = getattr(att, "size", None)
        mime_type = getattr(att, "mime_type", None)

    return Attachment(
        type=type_,
        url=url,
        name=name,
        size=size,
        mime_type=mime_type,
    )


def parse_rpa_message(raw: dict) -> UnifiedMessage:
    """将 ``event_type=message`` 的 ``RpaCallbackEnvelope`` 转换为 UnifiedMessage。

    Args:
        raw: 已通过 ``RpaCallbackEnvelope`` 校验的回调信封 dict（顶层含
            event_id / account_id / event_type / occurred_at / payload 等）。

    Returns:
        ``UnifiedMessage`` 实例：
        - ``message_id`` = envelope.event_id
        - ``channel_type`` = ``ChannelType.WECOM_PERSONAL_RPA``
        - ``user_id`` = payload.sender_stable_id or payload.conversation_id
        - ``user_name`` = payload.sender_display_name
        - ``message_type`` 由 InboundMessageType 映射
        - ``content`` 含 text / conversation_id / conversation_type / account_id
        - ``attachments`` 由 payload.attachments 转换
        - ``timestamp`` = envelope.occurred_at
        - ``raw_message`` = 原始 raw dict

    Raises:
        ValueError: payload 缺失、或 text 消息缺 text 字段。
        pydantic.ValidationError: payload 不符合 RpaMessagePayload。
    """
    if not isinstance(raw, dict):
        raise ValueError(f"raw 必须为 dict，收到 {type(raw).__name__}")

    payload_data = raw.get("payload")
    if payload_data is None:
        raise ValueError("回调信封缺少 payload 字段")

    # 用 schemas 模型校验 payload，拿到强类型字段
    payload = RpaMessagePayload.model_validate(payload_data)

    message_type_str = payload.message_type
    message_type = _map_message_type(message_type_str)

    # 缺字段给明确 ValueError
    if message_type_str == "text" and not payload.text:
        raise ValueError("message_type=text 时 payload.text 不得为空")

    # content 聚合
    content: Dict[str, Any] = {
        "text": payload.text or "",
        "conversation_id": payload.conversation_id,
        "conversation_type": payload.conversation_type,
        "account_id": raw.get("account_id", ""),
    }
    # 非文本类型补充原始 message_type，便于下游路由/审计
    if message_type_str != "text":
        content["message_type"] = message_type_str

    # user_id：sender_stable_id 优先，缺失则回退到 conversation_id（保证非空）
    user_id = payload.sender_stable_id or payload.conversation_id
    if not user_id:
        raise ValueError("payload.sender_stable_id 与 conversation_id 均为空，无法确定 user_id")

    # 附件转换
    attachments = [_convert_attachment(a) for a in payload.attachments]

    return UnifiedMessage(
        message_id=raw.get("event_id", ""),
        channel_type=ChannelType.WECOM_PERSONAL_RPA,
        user_id=user_id,
        user_name=payload.sender_display_name,
        message_type=message_type,
        content=content,
        attachments=attachments,
        timestamp=raw.get("occurred_at"),
        raw_message=raw,
    )


def parse_status_event(raw: dict) -> RpaStatusPayload:
    """从 ``event_type=status`` 的回调信封中校验并返回 ``RpaStatusPayload``。

    Args:
        raw: 已通过 ``RpaCallbackEnvelope`` 校验的回调信封 dict。

    Returns:
        ``RpaStatusPayload`` 实例。

    Raises:
        ValueError: payload 缺失。
        pydantic.ValidationError: payload 不符合 RpaStatusPayload。
    """
    if not isinstance(raw, dict):
        raise ValueError(f"raw 必须为 dict，收到 {type(raw).__name__}")

    payload_data = raw.get("payload")
    if payload_data is None:
        raise ValueError("回调信封缺少 payload 字段")

    return RpaStatusPayload.model_validate(payload_data)


def parse_action_result(raw: dict) -> RpaActionResultPayload:
    """从 ``event_type=action_result`` 的回调信封中校验并返回 ``RpaActionResultPayload``。

    Args:
        raw: 已通过 ``RpaCallbackEnvelope`` 校验的回调信封 dict。

    Returns:
        ``RpaActionResultPayload`` 实例。

    Raises:
        ValueError: payload 缺失。
        pydantic.ValidationError: payload 不符合 RpaActionResultPayload。
    """
    if not isinstance(raw, dict):
        raise ValueError(f"raw 必须为 dict，收到 {type(raw).__name__}")

    payload_data = raw.get("payload")
    if payload_data is None:
        raise ValueError("回调信封缺少 payload 字段")

    return RpaActionResultPayload.model_validate(payload_data)
