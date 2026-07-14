# -*- coding: utf-8 -*-
"""渠道端文本+图片占位符渲染（所有第三方渠道共用）

设计背景：所有第三方渠道（feishu / dingtalk / wecom / wecom_kf）的
单条消息只能是单一类型（text / image / file 互斥），无法图文混排。
渠道适配器需要把 UnifiedResponse 拆成「文本消息 + 多条图片消息」顺序发送。

placement 字段在渠道端的降级语义（与 Web 端不同）：

    | placement    | Web 端渲染       | 渠道端文本处理                          |
    |--------------|------------------|-----------------------------------------|
    | after_text   | 文本下方画廊     | 文本不加占位符，图在文本之后发送        |
    | before_text  | 文本上方         | 文本开头插入 [图片：{name}]\\n          |
    | inline       | 文本流中行内渲染 | Markdown ![](file_id:xxx) → [图片：alt] |

after_text 不插占位符：图自然在文本之后发送，加占位符反而冗余。
before_text / inline 插占位符：让用户感知「这里有图」。

调用约定：各渠道 adapter 在调本函数之前先 markdown_to_plain_text
把 Markdown 转成纯文本，本函数只负责占位符处理。

Phase 2 P2.9.0
"""
import re
from typing import Any, Dict, List


# 匹配 Markdown 图片语法：![alt](file_id:file_xxx)
# file_id 由 ImageRegistry 生成（file_{uuid.uuid4().hex[:12]}），字符集是 [a-z0-9_]
_INLINE_FILE_ID_PATTERN = re.compile(r'!\[([^\]]*)\]\(file_id:([a-z0-9_]+)\)')


def render_text_with_image_placeholders(
    text: str,
    images: List[Dict[str, Any]],
) -> str:
    """根据 images 的 placement 在文本中插入占位符。

    Args:
        text: 原始 Markdown 文本（渠道端调用前通常已转 plain text，但保留 Markdown 图片语法也无妨）
        images: ImageRef 列表（dict 形式，包含 file_id / display_name / placement 等字段）

    Returns:
        处理后的文本（含占位符）。

    安全性：
        - display_name 来自后端（受信任），但仍做最小转义（去换行）防止跨行注入
        - inline 占位符的 alt 文本来自 Markdown，含中文/标点都安全（不参与 HTML 渲染）
    """
    if not text or not images:
        return text

    # 1. before_text 图片：文本开头插入占位符
    before_imgs = [
        img for img in images
        if isinstance(img, dict) and img.get("placement") == "before_text"
    ]
    if before_imgs:
        prefix_parts = []
        for img in before_imgs:
            name = _safe_name(img.get("display_name"))
            prefix_parts.append(f"[图片：{name}]")
        # 占位符行后接换行，与原文本分隔
        text = "\n".join(prefix_parts) + "\n" + text

    # 2. inline 图片：替换 Markdown ![](file_id:xxx) 为占位符
    # 用 Markdown 中的 alt 文本作为图片名；alt 为空时回退到 display_name
    # 但本函数无法在正则替换里拿到 images 列表，所以 alt 优先；alt 空时用 file_id 短哈希
    def _replace_inline(m: re.Match) -> str:
        alt = m.group(1).strip()
        file_id = m.group(2)
        name = alt or _name_from_file_id(file_id, images)
        return f"[图片：{name}]"

    text = _INLINE_FILE_ID_PATTERN.sub(_replace_inline, text)

    # 3. after_text 图片：不加占位符（图自然在文本之后发送）
    return text


def _safe_name(name: Any) -> str:
    """安全化展示名：去换行/制表符，截断超长名（防 UI 错位）"""
    if not isinstance(name, str):
        return "未命名"
    cleaned = re.sub(r"[\r\n\t]+", " ", name).strip()
    if not cleaned:
        return "未命名"
    if len(cleaned) > 40:
        cleaned = cleaned[:40] + "..."
    return cleaned


def _name_from_file_id(file_id: str, images: List[Dict[str, Any]]) -> str:
    """inline 占位符无 alt 时，根据 file_id 从 images 列表查 display_name。"""
    for img in images:
        if img.get("file_id") == file_id:
            return _safe_name(img.get("display_name"))
    return file_id
