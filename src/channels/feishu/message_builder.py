"""
飞书消息构建器

构建飞书机器人发送消息的请求体。

关键设计：
- 所有 build_*() 返回的 `content` 字段都是 JSON 字符串（不是对象）
  这是为了避免踩 P1 坑：飞书 API 要求 content 字段是 JSON 字符串，
  否则报 `content is invalid`
- 调用方拿到返回值后可直接放入请求体，无需再 json.dumps

支持的消息类型：
- text: 纯文本
- post: 富文本（支持加粗/链接/图片/@）
- interactive: 卡片消息（复杂排版）
- image: 图片
- file: 文件

长消息拆分采用与企微相同的三级策略（段落 → 行 → 字节），
默认 max_bytes=4000（飞书文本消息上限约 4000 字符）。
"""

import json
import re
from typing import Any, Dict, List, Optional


class FeishuMessageBuilder:
    """飞书消息构建器"""

    @staticmethod
    def build_text(text: str) -> Dict[str, Any]:
        """
        构建文本消息。

        返回：{"msg_type": "text", "content": "<JSON string>"}
        其中 content 字符串解析后为 {"text": text}

        Args:
            text: 文本内容

        Returns:
            可直接放入请求体的 dict
        """
        return {
            "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False),
        }

    @staticmethod
    def build_post(title: str, paragraphs: List[List[Dict[str, Any]]]) -> Dict[str, Any]:
        """
        构建富文本消息（post）。

        paragraphs 是二维数组，每个段落是 tag 元素列表：
        [
          [
            {"tag": "text", "text": "普通文字"},
            {"tag": "text", "text": "加粗文字", "style": ["bold"]},
            {"tag": "a", "text": "链接", "href": "https://..."},
            {"tag": "at", "user_id": "ou_xxx", "user_name": "名字"},
            {"tag": "img", "image_key": "img_xxx", "width": 300, "height": 200},
          ],
          [...]  # 下一段
        ]

        Args:
            title: 富文本标题（显示在消息卡片顶部）
            paragraphs: 富文本段落列表

        Returns:
            可直接放入请求体的 dict
        """
        post_content = {
            "zh_cn": {
                "title": title,
                "content": paragraphs,
            }
        }
        return {
            "msg_type": "post",
            "content": json.dumps(post_content, ensure_ascii=False),
        }

    @staticmethod
    def build_interactive(
        elements: List[Dict[str, Any]], header: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        构建卡片消息（interactive）。

        用于复杂排版场景（按钮、分割线、多列布局等）。

        Args:
            elements: 卡片元素列表（div / action / hr / img 等）
            header: 卡片头部 {"title": {"tag": "plain_text", "content": "..."}, "template": "blue"}

        Returns:
            可直接放入请求体的 dict
        """
        card: Dict[str, Any] = {"elements": elements}
        if header:
            card["header"] = header
        return {
            "msg_type": "interactive",
            "content": json.dumps(card, ensure_ascii=False),
        }

    @staticmethod
    def build_image(image_key: str) -> Dict[str, Any]:
        """
        构建图片消息。

        Args:
            image_key: 飞书 image_key（通过 upload_image 获得）

        Returns:
            可直接放入请求体的 dict
        """
        return {
            "msg_type": "image",
            "content": json.dumps({"image_key": image_key}, ensure_ascii=False),
        }

    @staticmethod
    def build_file(file_key: str, file_name: str) -> Dict[str, Any]:
        """
        构建文件消息。

        Args:
            file_key: 飞书 file_key（通过 upload_file 获得）
            file_name: 文件名

        Returns:
            可直接放入请求体的 dict
        """
        return {
            "msg_type": "file",
            "content": json.dumps(
                {"file_key": file_key, "file_name": file_name}, ensure_ascii=False
            ),
        }

    @staticmethod
    def detect_message_type(text: str, downloadable_files: Optional[list] = None) -> str:
        """
        根据内容智能选择消息类型。

        判定顺序：
        1. 有可下载文件 → "file"（由调用方处理文件消息）
        2. 包含 markdown 格式 → "post"（富文本展示更佳）
        3. 其他 → "text"

        Args:
            text: 文本内容
            downloadable_files: DownloadableFileInfo 列表（可选）

        Returns:
            推荐的消息类型：text / post / file
        """
        if downloadable_files:
            return "file"

        if _has_markdown_pattern(text):
            return "post"

        return "text"

    @staticmethod
    def markdown_to_post_paragraphs(text: str) -> List[List[Dict[str, Any]]]:
        """
        将 markdown 文本转换为 post 消息的 paragraphs 结构。

        支持的 markdown 元素：
        - **bold** → {"tag": "text", "text": "...", "style": ["bold"]}
        - [text](url) → {"tag": "a", "text": "...", "href": "..."}
        - `code` → {"tag": "text", "text": "...", "style": ["bold"]}  (飞书 post 无 inline code，用粗体替代)
        - 普通文本 → {"tag": "text", "text": "..."}

        不支持的元素（表格、代码块等）会降级为纯文本。

        Args:
            text: markdown 文本

        Returns:
            post paragraphs 二维数组
        """
        paragraphs: List[List[Dict[str, Any]]] = []
        for raw_line in text.split("\n"):
            line = raw_line.rstrip()
            if not line:
                # 空行不加入段落，但如果上一段有内容则作为段落分隔
                continue
            elements = _parse_inline_elements(line)
            if elements:
                paragraphs.append(elements)
        return paragraphs

    @staticmethod
    def split_long_message(
        text: str,
        max_bytes: int = 4000,
        split_on_paragraph: bool = True,
    ) -> List[str]:
        """
        将长消息拆分为多条，确保每条不超过字节数限制。

        拆分策略（三级，与企微相同）：
        1. 优先按段落边界（\\n\\n）拆分
        2. 段落过长时按行边界（\\n）拆分
        3. 单行过长时按字节截断（不截断 UTF-8 字符）

        Args:
            text: 原始消息文本
            max_bytes: 每条消息的最大字节数（飞书默认 4000）
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


def _parse_inline_elements(line: str) -> List[Dict[str, Any]]:
    """
    解析一行的内联元素，返回 post tag 元素列表。

    处理顺序：先识别 **bold** 和 [text](url)，剩余部分作为纯文本。
    """
    elements: List[Dict[str, Any]] = []
    # 正则匹配 **bold** 或 [text](url) 或 `code`
    pattern = re.compile(
        r"(\*\*(.+?)\*\*)"       # group 1-2: bold
        r"|(\[([^\]]+)\]\(([^)]+)\))"  # group 3-5: link [text](url)
        r"|(`([^`]+)`)"          # group 6-7: inline code
    )
    pos = 0
    for m in pattern.finditer(line):
        start = m.start()
        if start > pos:
            plain = line[pos:start]
            if plain:
                elements.append({"tag": "text", "text": plain})
        if m.group(1):  # bold
            elements.append({"tag": "text", "text": m.group(2), "style": ["bold"]})
        elif m.group(3):  # link
            elements.append({"tag": "a", "text": m.group(4), "href": m.group(5)})
        elif m.group(6):  # inline code → 用粗体替代
            elements.append({"tag": "text", "text": m.group(7), "style": ["bold"]})
        pos = m.end()

    remaining = line[pos:]
    if remaining:
        elements.append({"tag": "text", "text": remaining})

    return elements


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
