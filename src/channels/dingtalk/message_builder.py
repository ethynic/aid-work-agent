"""
钉钉消息构建器

构建钉钉机器人发送消息的请求体。

关键设计：
- 所有 build_*() 返回的 msgParam 字段都是 JSON 字符串（不是对象）
  这是钉钉 API 的要求
- msgKey 标识消息类型，msgParam 包含消息内容
- 长消息拆分采用与飞书相同的三级策略（段落 → 行 → 字节）
  默认 max_bytes=4000（钉钉单条消息上限约 4000 字符）

官方文档：
https://open.dingtalk.com/document/orgapp/send-normal-messages
"""

import json
import re
from typing import Any, Dict, List, Optional


class DingTalkMessageBuilder:
    """钉钉消息构建器"""

    @staticmethod
    def build_text(text: str) -> Dict[str, Any]:
        """
        构建文本消息

        返回：{"msgKey": "sampleText", "msgParam": "<JSON string>"}
        其中 msgParam 字符串解析后为 {"content": text}

        Args:
            text: 文本内容

        Returns:
            可直接放入请求体的 dict
        """
        return {
            "msgKey": "sampleText",
            "msgParam": json.dumps({"content": text}, ensure_ascii=False),
        }

    @staticmethod
    def build_markdown(title: str, text: str) -> Dict[str, Any]:
        """
        构建 Markdown 消息

        返回：{"msgKey": "sampleMarkdown", "msgParam": "<JSON string>"}
        其中 msgParam 字符串解析后为 {"title": title, "text": text}

        Args:
            title: Markdown 标题
            text: Markdown 内容

        Returns:
            可直接放入请求体的 dict
        """
        return {
            "msgKey": "sampleMarkdown",
            "msgParam": json.dumps({"title": title, "text": text}, ensure_ascii=False),
        }

    @staticmethod
    def build_image(photo_url: str) -> Dict[str, Any]:
        """
        构建图片消息

        返回：{"msgKey": "sampleImageMsg", "msgParam": "<JSON string>"}
        其中 msgParam 字符串解析后为 {"photoURL": photo_url}

        Args:
            photo_url: 图片 URL（需要先上传获取）

        Returns:
            可直接放入请求体的 dict
        """
        return {
            "msgKey": "sampleImageMsg",
            "msgParam": json.dumps({"photoURL": photo_url}, ensure_ascii=False),
        }

    @staticmethod
    def build_file(media_id: str, file_name: str, file_type: str) -> Dict[str, Any]:
        """
        构建文件消息

        返回：{"msgKey": "sampleFile", "msgParam": "<JSON string>"}
        其中 msgParam 字符串解析后为 {"mediaId": media_id, "fileName": file_name, "fileType": file_type}

        Args:
            media_id: 媒体文件 ID（通过上传接口获取）
            file_name: 文件名
            file_type: 文件类型（如 "pdf", "doc" 等）

        Returns:
            可直接放入请求体的 dict
        """
        return {
            "msgKey": "sampleFile",
            "msgParam": json.dumps(
                {"mediaId": media_id, "fileName": file_name, "fileType": file_type},
                ensure_ascii=False,
            ),
        }

    @staticmethod
    def build_link(title: str, text: str, message_url: str, pic_url: str = "") -> Dict[str, Any]:
        """
        构建链接消息

        返回：{"msgKey": "sampleLink", "msgParam": "<JSON string>"}

        Args:
            title: 链接标题
            text: 链接描述
            message_url: 链接 URL
            pic_url: 图片 URL（可选）

        Returns:
            可直接放入请求体的 dict
        """
        return {
            "msgKey": "sampleLink",
            "msgParam": json.dumps(
                {
                    "title": title,
                    "text": text,
                    "messageUrl": message_url,
                    "picUrl": pic_url,
                },
                ensure_ascii=False,
            ),
        }

    @staticmethod
    def detect_message_type(text: str, downloadable_files: Optional[list] = None) -> str:
        """
        根据内容智能选择消息类型

        判定顺序：
        1. 有可下载文件 → "file"（由调用方处理文件消息）
        2. 包含 markdown 格式 → "markdown"（富文本展示更佳）
        3. 其他 → "text"

        Args:
            text: 文本内容
            downloadable_files: DownloadableFileInfo 列表（可选）

        Returns:
            推荐的消息类型：text / markdown / file
        """
        if downloadable_files:
            return "file"

        if _has_markdown_pattern(text):
            return "markdown"

        return "text"

    @staticmethod
    def split_long_message(
        text: str,
        max_bytes: int = 4000,
        split_on_paragraph: bool = True,
    ) -> List[str]:
        """
        将长消息拆分为多条，确保每条不超过字节数限制

        拆分策略（三级，与飞书相同）：
        1. 优先按段落边界（\\n\\n）拆分
        2. 段落过长时按行边界（\\n）拆分
        3. 单行过长时按字节截断（不截断 UTF-8 字符）

        Args:
            text: 原始消息文本
            max_bytes: 每条消息的最大字节数（钉钉默认 4000）
            split_on_paragraph: 是否按段落拆分

        Returns:
            拆分后的消息列表
        """
        if not text:
            return []

        if len(text.encode("utf-8")) <= max_bytes:
            return [text]

        parts: List[str] = []

        if split_on_paragraph:
            paragraphs = text.split("\n\n")
            current_part = ""

            for para in paragraphs:
                para_bytes = para.encode("utf-8")

                if len(para_bytes) > max_bytes:
                    if current_part:
                        parts.append(current_part.strip())
                        current_part = ""

                    lines = para.split("\n")
                    for line in lines:
                        candidate = current_part + "\n" + line if current_part else line
                        if len(candidate.encode("utf-8")) <= max_bytes:
                            current_part = candidate
                        else:
                            if current_part:
                                parts.append(current_part.strip())
                            if len(line.encode("utf-8")) > max_bytes:
                                chunks = _split_by_bytes(line, max_bytes)
                                parts.extend(chunks[:-1])
                                current_part = chunks[-1]
                            else:
                                current_part = line
                else:
                    candidate = (
                        current_part + "\n\n" + para if current_part else para
                    )
                    if len(candidate.encode("utf-8")) <= max_bytes:
                        current_part = candidate
                    else:
                        if current_part:
                            parts.append(current_part.strip())
                        current_part = para

            if current_part:
                parts.append(current_part.strip())
        else:
            lines = text.split("\n")
            current_part = ""
            for line in lines:
                if len(line.encode("utf-8")) > max_bytes:
                    if current_part:
                        parts.append(current_part.strip())
                        current_part = ""
                    chunks = _split_by_bytes(line, max_bytes)
                    parts.extend(chunks[:-1])
                    current_part = chunks[-1]
                else:
                    candidate = current_part + "\n" + line if current_part else line
                    if len(candidate.encode("utf-8")) <= max_bytes:
                        current_part = candidate
                    else:
                        if current_part:
                            parts.append(current_part.strip())
                        current_part = line
            if current_part:
                parts.append(current_part.strip())

        return [p for p in parts if p]


def _has_markdown_pattern(text: str) -> bool:
    """检测文本是否包含 markdown 格式"""
    patterns = [
        r"^#{1,6}\s",
        r"\*\*.*?\*\*",
        r"\*.*?\*",
        r"^\s*[-*+]\s",
        r"^\s*\d+\.\s",
        r"\[.*?\]\(.*?\)",
        r"`[^`]+`",
        r"^>\s",
    ]
    for pattern in patterns:
        if re.search(pattern, text, re.MULTILINE):
            return True
    return False


def _split_by_bytes(text: str, max_bytes: int) -> List[str]:
    """按字节长度强制拆分字符串，确保不截断 UTF-8 字符"""
    if not text:
        return []

    result: List[str] = []
    current = ""

    for char in text:
        candidate = current + char
        if len(candidate.encode("utf-8")) <= max_bytes:
            current = candidate
        else:
            if current:
                result.append(current)
            current = char

    if current:
        result.append(current)

    return result
