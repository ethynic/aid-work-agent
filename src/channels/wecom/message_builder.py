"""
企业微信消息构建器

支持构建多种 WeCom 消息类型:
- text: 纯文本
- markdown: Markdown 格式
- textcard: 文本卡片（带链接）
- image: 图片消息
- file: 文件消息
- news: 图文链接消息

以及长消息自动拆分。
"""

import re
from typing import Any, Dict, List, Optional


class WeComMessageBuilder:
    """企业微信消息构建器"""

    @staticmethod
    def build_text(content: str, agent_id: str) -> Dict[str, Any]:
        """构建文本消息"""
        return {
            "touser": "",
            "msgtype": "text",
            "agentid": agent_id,
            "text": {"content": content},
        }

    @staticmethod
    def normalize_markdown_headings(content: str) -> str:
        """
        将 Markdown # 标题转换为粗体文本，避免企业微信中标题字体过大

        企业微信的 markdown 消息类型中 # 标题渲染字体偏大（约 20px），
        不适合正文阅读。此方法将各层级标题转换为 **粗体** 文本：

        - # 标题  → **标题**
        - ## 标题 → **标题**
        - ### 标题 → **标题**
        - 以此类推

        转换前会在标题前保留一个空行（如果前面有内容），保持段落间距。
        """
        def replace_heading(match: re.Match) -> str:
            prefix = match.group(1)   # 空行前缀
            heading_content = match.group(3).strip()
            return f"{prefix}**{heading_content}**"

        # 匹配行首的 # 标题（行首可能有多个 # 和一个空格）
        # 分组: (空行前缀)(#号+空格)(标题内容)
        return re.sub(
            r"(\n*)^(#{1,6})\s+(.+)",
            replace_heading,
            content,
            flags=re.MULTILINE,
        )

    @staticmethod
    def build_markdown(content: str, agent_id: str) -> Dict[str, Any]:
        """
        构建 Markdown 消息

        注意: WeCom 的 markdown 类型仅支持有限的 markdown 子集，
        不支持表格、代码块等复杂格式。发送前会自动将 # 标题转换为粗体，
        避免企业微信中标题字体过大。
        """
        # 预处理：将 # 标题转换为粗体，避免企业微信中标题字体过大
        content = WeComMessageBuilder.normalize_markdown_headings(content)
        return {
            "touser": "",
            "msgtype": "markdown",
            "agentid": agent_id,
            "markdown": {"content": content},
        }

    @staticmethod
    def build_textcard(
        title: str,
        description: str,
        url: str,
        agent_id: str,
        btntxt: str = "详情",
    ) -> Dict[str, Any]:
        """构建文本卡片消息"""
        return {
            "touser": "",
            "msgtype": "textcard",
            "agentid": agent_id,
            "textcard": {
                "title": title,
                "description": description,
                "url": url,
                "btntxt": btntxt,
            },
        }

    @staticmethod
    def build_image(media_id: str, agent_id: str) -> Dict[str, Any]:
        """构建图片消息"""
        return {
            "touser": "",
            "msgtype": "image",
            "agentid": agent_id,
            "image": {"media_id": media_id},
        }

    @staticmethod
    def build_file(media_id: str, agent_id: str) -> Dict[str, Any]:
        """构建文件消息"""
        return {
            "touser": "",
            "msgtype": "file",
            "agentid": agent_id,
            "file": {"media_id": media_id},
        }

    @staticmethod
    def build_news(
        articles: List[Dict[str, str]], agent_id: str
    ) -> Dict[str, Any]:
        """
        构建图文消息

        articles 格式: [{"title": "...", "description": "...", "url": "...", "picurl": "..."}]
        最多 8 条
        """
        return {
            "touser": "",
            "msgtype": "news",
            "agentid": agent_id,
            "news": {"articles": articles[:8]},
        }

    @staticmethod
    def build_msg_data(
        content: str,
        agent_id: str,
        msg_type: str = "markdown",
    ) -> Dict[str, Any]:
        """
        根据消息类型构建消息体

        Args:
            content: 消息内容
            agent_id: 应用 ID
            msg_type: 消息类型 (text | markdown)

        Returns:
            WeCom API 消息体
        """
        if msg_type == "markdown":
            return WeComMessageBuilder.build_markdown(content, agent_id)
        return WeComMessageBuilder.build_text(content, agent_id)

    @staticmethod
    def detect_message_type(text: str, default_type: str = "markdown") -> str:
        """
        检测文本是否包含 markdown 格式，决定消息类型

        Args:
            text: 消息文本
            default_type: 默认消息类型

        Returns:
            推荐的消息类型
        """
        # 检测常见 markdown 模式
        markdown_patterns = [
            r"^#{1,6}\s",           # 标题
            r"\*\*.*?\*\*",         # 粗体
            r"\*.*?\*",             # 斜体
            r"^\s*[-*+]\s",         # 无序列表
            r"^\s*\d+\.\s",         # 有序列表
            r"\[.*?\]\(.*?\)",      # 链接
            r"`[^`]+`",             # 行内代码
            r"^>\s",                # 引用
        ]
        for pattern in markdown_patterns:
            if re.search(pattern, text, re.MULTILINE):
                return "markdown"
        return default_type

    @staticmethod
    def split_long_message(
        text: str,
        max_bytes: int = 2048,
        split_on_paragraph: bool = True,
    ) -> List[str]:
        """
        将长消息拆分为多条消息，确保每条不超过字节数限制

        拆分策略:
        1. 优先按段落边界（\\n\\n）拆分
        2. 段落过长时按行边界（\\n）拆分
        3. 单行过长时按字符拆分

        Args:
            text: 原始消息文本
            max_bytes: 每条消息的最大字节数
            split_on_paragraph: 是否按段落拆分

        Returns:
            拆分后的消息列表
        """
        if not text:
            return []

        # 检查整体是否在限制内
        if len(text.encode("utf-8")) <= max_bytes:
            return [text]

        parts: List[str] = []

        if split_on_paragraph:
            # 按段落拆分
            paragraphs = text.split("\n\n")
            current_part = ""

            for para in paragraphs:
                para_bytes = para.encode("utf-8")

                # 单个段落就超限，需要进一步拆分
                if len(para_bytes) > max_bytes:
                    # 先保存当前累积的部分
                    if current_part:
                        parts.append(current_part.strip())
                        current_part = ""

                    # 按行拆分这个段落
                    lines = para.split("\n")
                    for line in lines:
                        candidate = current_part + "\n" + line if current_part else line
                        if len(candidate.encode("utf-8")) <= max_bytes:
                            current_part = candidate
                        else:
                            if current_part:
                                parts.append(current_part.strip())
                            # 单行超限，强制截断
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
            # 按行拆分
            lines = text.split("\n")
            current_part = ""
            for line in lines:
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


def _split_by_bytes(text: str, max_bytes: int) -> List[str]:
    """
    按字节长度强制拆分字符串，确保不截断 UTF-8 字符

    Args:
        text: 原始文本
        max_bytes: 每段最大字节数

    Returns:
        拆分后的字符串列表
    """
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
