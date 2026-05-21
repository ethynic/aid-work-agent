"""微信客服消息格式转换

包含：
- parse_kf_message: sync_msg 响应 → UnifiedMessage
- markdown_to_plain_text: markdown → 纯文本（微信客服 text 消息不支持格式）
"""
import re
from datetime import datetime
from typing import Any, Dict

from src.models.message import (
    ChannelType,
    MessageType,
    UnifiedMessage,
)


def parse_kf_message(msg: Dict[str, Any]) -> UnifiedMessage:
    """
    将微信客服 sync_msg 返回的单条消息转换为 UnifiedMessage。

    Args:
        msg: sync_msg msg_list 中的一条消息，格式参考：
            {
                "msgid": "xxx",
                "open_kfid": "wkAAAA",
                "external_userid": "wmXXXXXXXX",
                "send_time": 1234567890,
                "origin": 3,  # 3=客户发送, 4=接待人员发送
                "servicer_userid": "",
                "msgtype": "text",
                "text": {"content": "你好"}
            }

    Returns:
        UnifiedMessage 实例
    """
    msg_type = msg.get("msgtype", "text")
    content: Dict[str, Any] = {}
    message_type = MessageType.TEXT

    if msg_type == "text":
        content["text"] = msg.get("text", {}).get("content", "")
    elif msg_type == "image":
        message_type = MessageType.IMAGE
        content["media_id"] = msg.get("image", {}).get("media_id", "")
    elif msg_type == "voice":
        # 优先使用识别文本
        recognition = msg.get("voice", {}).get("Recognition", "")
        content["text"] = recognition or "[语音消息]"
        content["media_id"] = msg.get("voice", {}).get("media_id", "")
    elif msg_type == "file":
        message_type = MessageType.FILE
        content["media_id"] = msg.get("file", {}).get("media_id", "")
        content["file_name"] = msg.get("file", {}).get("file_name", "")
    elif msg_type == "video":
        message_type = MessageType.FILE
        content["media_id"] = msg.get("video", {}).get("media_id", "")
    elif msg_type == "link":
        link = msg.get("link", {})
        content["text"] = f"[链接] {link.get('title', '')} {link.get('url', '')}"
        content["title"] = link.get("title", "")
        content["url"] = link.get("url", "")
    elif msg_type == "emoji":
        content["text"] = "[表情]"
    else:
        content["text"] = f"[{msg_type}消息]"

    send_time = msg.get("send_time", 0)
    timestamp = datetime.fromtimestamp(send_time) if send_time else datetime.now()

    return UnifiedMessage(
        message_id=msg.get("msgid", ""),
        channel_type=ChannelType.WECOM_KF,
        user_id=msg.get("external_userid", ""),
        message_type=message_type,
        content=content,
        timestamp=timestamp,
        raw_message=msg,
    )


def markdown_to_plain_text(md: str) -> str:
    """
    将 markdown 转为纯文本，适配微信客服 text 消息。

    微信客服 API 不支持 markdown 格式，需要剥离格式标记。
    """
    if not md:
        return ""

    text = md

    # 代码块 ```code``` → 保留代码内容，添加缩进标记
    text = re.sub(r"```(\w*)\n(.*?)```", r"\2", text, flags=re.DOTALL)

    # 行内代码 `code` → code
    text = re.sub(r"`([^`]+)`", r"\1", text)

    # 加粗 **bold** / __bold__ → bold
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)

    # 斜体 *italic* / _italic_ → italic
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"\1", text)

    # 删除线 ~~strikethrough~~ → strikethrough
    text = re.sub(r"~~(.+?)~~", r"\1", text)

    # 标题 # Heading → Heading
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)

    # 链接 [text](url) → text(url)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1(\2)", text)

    # 图片 ![alt](url) → alt
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)

    # 无序列表标记 - / * / + 开头 → 保留
    text = re.sub(r"^\s*[-*+]\s+", "- ", text, flags=re.MULTILINE)

    # 有序列表标记 1. / 2. 等 → 保留
    text = re.sub(r"^\s*\d+\.\s+", "- ", text, flags=re.MULTILINE)

    # 引用块 > quote → quote
    text = re.sub(r"^\s*>\s*", "", text, flags=re.MULTILINE)

    # 水平线 --- / *** / ___ → 分隔线
    text = re.sub(r"^\s*[-*_]{3,}\s*$", "\n---\n", text, flags=re.MULTILINE)

    # 表格 → 保留文本内容
    # 去除纯分隔行（如 |---|---|）
    text = re.sub(r"^\s*\|[\s\-:|]+\|\s*$", "", text, flags=re.MULTILINE)

    # 清理 HTML 标签
    text = re.sub(r"<[^>]+>", "", text)

    # 清理多余空行（3 个以上换行 → 2 个）
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()
