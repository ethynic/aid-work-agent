"""微信客服消息格式转换

包含：
- parse_kf_message: sync_msg 响应 → UnifiedMessage
- markdown_to_plain_text: markdown → 纯文本（微信客服 text 消息不支持格式）
- segment_markdown: markdown → ContentBlock 列表（按表格/链接/文本分段）
- table_to_plain_text: markdown 表格 → 可读纯文本（降级方案）
"""
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

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
        # 微信客服语音不自带识别文字（Recognition 字段永远为空）
        # 语音转文字由渠道层 channel_routes.py 调用 ASR 完成
        content["text"] = "[语音消息]"
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


# ==================== 内容分段 ====================

# 表格分隔行正则（复用 pdf_writer 逻辑）
_TABLE_SEPARATOR_RE = re.compile(r'^[\|\s\-:—–―]+$')


# Markdown 图片引用正则
# 1. file_id: scheme - 内部图片资产引用
_FILE_ID_IMAGE_RE = re.compile(r'!\[[^\]]*\]\(file_id:[a-z0-9_]+\)')
# 2. 远程 URL - http/https 图片
_REMOTE_URL_IMAGE_RE = re.compile(r'!\[[^\]]*\]\(https?://[^\s)]+\)')


def contains_table_or_image(md: str) -> bool:
    """检测 markdown 文本是否包含 md 表格 或 图片引用。

    用于 wecom_kf 渠道决定是否将整段 md 渲染为长图一次性发送：
    - md 表格：以 | 开头且非分隔行的行
    - 图片：![alt](file_id:xxx) 或 ![alt](http(s)://...)

    Args:
        md: 原始 markdown 文本

    Returns:
        True 如果包含表格或图片，False 否则
    """
    if not md:
        return False

    # 1. 检测图片引用（file_id: scheme 或 远程 URL）
    if _FILE_ID_IMAGE_RE.search(md) or _REMOTE_URL_IMAGE_RE.search(md):
        return True

    # 2. 检测 md 表格：任一行以 | 开头且不是分隔行
    for line in md.split('\n'):
        stripped = line.strip()
        if stripped.startswith('|') and not _TABLE_SEPARATOR_RE.match(stripped):
            return True

    return False


@dataclass
class ContentBlock:
    """Markdown 内容分段块"""
    type: Literal["text", "table", "link"]
    content: str
    meta: Dict[str, Any] = field(default_factory=dict)


def segment_markdown(md: str) -> List[ContentBlock]:
    """
    将 markdown 拆分为 text / table / link 块，保持原始顺序。

    分段策略：
    1. 如果整个消息仅包含一个链接（无其他内容），产生 link 块
    2. 识别表格区域（以 | 开头 + 分隔行），表格之间为 text 块
    3. text 块中的链接由微信 text 消息自动识别为可点击

    Args:
        md: 原始 markdown 文本

    Returns:
        ContentBlock 列表，按原始顺序排列
    """
    if not md:
        return []

    # 检查是否为独立链接消息
    link_block = _try_extract_standalone_link(md)
    if link_block:
        return [link_block]

    lines = md.split('\n')
    blocks: List[ContentBlock] = []
    current_text_lines: List[str] = []
    i = 0

    while i < len(lines):
        stripped = lines[i].strip()

        # 检测表格起始行（以 | 开头且不是分隔行）
        if stripped.startswith('|') and not _TABLE_SEPARATOR_RE.match(stripped):
            # 收集表格行
            table_lines = [stripped]
            j = i + 1
            while j < len(lines) and lines[j].strip().startswith('|'):
                table_lines.append(lines[j].strip())
                j += 1

            # 刷新之前累积的文本
            if current_text_lines:
                text_content = '\n'.join(current_text_lines).strip()
                if text_content:
                    blocks.append(ContentBlock(type="text", content=text_content))
                current_text_lines = []

            # 修复表格（复用 pdf_writer 的修复逻辑）
            fixed_table = _repair_markdown_table(table_lines)
            blocks.append(ContentBlock(
                type="table",
                content='\n'.join(fixed_table),
                meta={"rows": len(fixed_table) - 2, "cols": len(fixed_table[0].strip('|').split('|')) if fixed_table else 0},
            ))
            i = j
        else:
            current_text_lines.append(lines[i])
            i += 1

    # 刷新末尾文本
    if current_text_lines:
        text_content = '\n'.join(current_text_lines).strip()
        if text_content:
            blocks.append(ContentBlock(type="text", content=text_content))

    return blocks


def _try_extract_standalone_link(md: str) -> Optional[ContentBlock]:
    """检查是否为独立链接消息（仅包含一个链接，无其他实质内容）。"""
    stripped = md.strip()

    # 匹配纯 markdown 链接 [text](url)
    link_match = re.match(r'^\[([^\]]+)\]\(([^)]+)\)\s*$', stripped)
    if link_match:
        return ContentBlock(
            type="link",
            content=link_match.group(2),
            meta={"title": link_match.group(1), "url": link_match.group(2)},
        )

    # 匹配纯 URL（可选的标题前缀）
    lines = stripped.split('\n')
    non_empty = [l for l in lines if l.strip()]
    if len(non_empty) == 1:
        url_match = re.match(r'^(https?://[^\s]+)\s*$', non_empty[0])
        if url_match:
            url = url_match.group(1)
            return ContentBlock(
                type="link",
                content=url,
                meta={"title": url, "url": url},
            )

    return None


def _repair_markdown_table(table_lines: List[str]) -> List[str]:
    """修复 markdown 表格：补分隔行、补空单元格。"""
    if not table_lines:
        return table_lines

    header = table_lines[0]
    header_cells = [c.strip() for c in header.strip('|').split('|')]
    col_count = len(header_cells)

    has_separator = (
        len(table_lines) > 1
        and _TABLE_SEPARATOR_RE.match(table_lines[1].strip())
    )

    fixed = [table_lines[0]]
    fixed.append('|' + '|'.join([' --- '] * col_count) + '|')

    data_start = 2 if has_separator else 1
    for row in table_lines[data_start:]:
        if _TABLE_SEPARATOR_RE.match(row.strip()):
            continue
        cells = [c.strip() for c in row.strip('|').split('|')]
        if len(cells) < col_count:
            cells.extend([''] * (col_count - len(cells)))
        elif len(cells) > col_count:
            cells = cells[:col_count]
        fixed.append('|' + '|'.join(cells) + '|')

    return fixed


def table_to_plain_text(markdown_table: str) -> str:
    """
    将 markdown 表格转为可读纯文本（降级方案）。

    输入:
        | Name | Age | City |
        |------|-----|------|
        | Alice | 25 | Beijing |

    输出:
        [表格]
        Name | Age | City
        Alice | 25 | Beijing
    """
    lines = markdown_table.strip().split('\n')
    result = ['[表格]']
    for line in lines:
        stripped = line.strip()
        if _TABLE_SEPARATOR_RE.match(stripped):
            continue
        cells = [c.strip() for c in stripped.strip('|').split('|')]
        result.append(' | '.join(cells))
    return '\n'.join(result)
