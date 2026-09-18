"""LLM/Embedding 输入文本异常字符清洗

剔除会导致 API 报错或污染检索的异常字符（保留 \t \n \r）：
- 孤立代理 U+D800-DFFF：UTF-8 无法编码，SDK 序列化报 surrogates not allowed
- NUL 及 C0/C1 控制符：干扰分块与匹配，部分 API 拒收 NUL
- U+FFFE/U+FFFF noncharacter：部分服务端校验拒绝
- U+FEFF：BOM 残留，不可见垃圾字符

异常字符来源：PDF ToUnicode 映射缺陷（如孤立代理 \ud83c）、surrogateescape
解码的源文本等。命中时才做替换拷贝，正常文本零开销。
"""

import re
from collections import Counter
from typing import Any, Dict, List, Optional

from loguru import logger

_UNSAFE_CHARS_RE = re.compile(
    "[\ud800-\udfff\x00-\x08\x0b\x0c\x0e-\x1f\x7f\x80-\x9f\ufffe\uffff\ufeff]"
)


def _describe_unsafe(text: str) -> str:
    """统计命中的异常字符类别与首个码点，用于日志定位（surrogate / control / noncharacter / BOM）"""
    cats: Counter = Counter()
    first_cp = ""
    for ch in _UNSAFE_CHARS_RE.findall(text):
        cp = ord(ch)
        if not first_cp:
            first_cp = f" 首个=U+{cp:04X}"
        if 0xD800 <= cp <= 0xDFFF:
            cats["surrogate"] += 1
        elif cp in (0xFFFE, 0xFFFF):
            cats["noncharacter"] += 1
        elif cp == 0xFEFF:
            cats["BOM"] += 1
        else:
            cats["control"] += 1
    return " ".join(f"{k}x{v}" for k, v in sorted(cats.items())) + first_cp


def sanitize_text(text: str, source: str = "") -> str:
    """清洗单个文本；无异常字符时返回原对象（零拷贝）。

    source：调用方入口标识（如 llm_gateway / tool_result），仅用于日志定位脏数据源头。
    """
    if _UNSAFE_CHARS_RE.search(text):
        logger.info(
            f"后端日志：文本含异常字符，已剔除（source={source or '未标注'}, "
            f"原文长度={len(text)}, 命中={_describe_unsafe(text)}）"
        )
        return _UNSAFE_CHARS_RE.sub("", text)
    return text


def sanitize_value(value: Any, source: str = "") -> Any:
    """递归清洗任意结构（dict/list/tuple/str），供工具结果等复合数据出口使用。

    只清洗字符串值，键名与其他类型原样保留；无异常字符时返回原对象（零拷贝）。
    """
    if isinstance(value, str):
        return sanitize_text(value, source)
    if isinstance(value, dict):
        changed = False
        cleaned: Dict[Any, Any] = {}
        for k, v in value.items():
            nv = sanitize_value(v, source)
            if nv is not v:
                changed = True
            cleaned[k] = nv
        return cleaned if changed else value
    if isinstance(value, list):
        items = [sanitize_value(v, source) for v in value]
        return value if all(n is o for n, o in zip(items, value)) else items
    if isinstance(value, tuple):
        items = tuple(sanitize_value(v, source) for v in value)
        return value if all(n is o for n, o in zip(items, value)) else items
    return value


def sanitize_texts(texts: List[str], source: str = "") -> List[str]:
    """清洗文本列表"""
    return [sanitize_text(t, source) for t in texts]


def sanitize_messages(
    messages: Optional[List[Dict[str, Any]]],
    source: str = "",
) -> Optional[List[Dict[str, Any]]]:
    """清洗消息列表中的文本内容

    兼容两种 content 形态：纯字符串、多模态分段列表（清洗分段中的
    "text" 字段，图片等非文本分段原样保留）。未命中异常字符时返回
    原列表对象，不拷贝。
    """
    if not messages:
        return messages
    changed = False
    cleaned = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            new = sanitize_text(content, source)
            if new is not content:
                changed = True
                m = {**m, "content": new}
        elif isinstance(content, list):
            new_parts = []
            part_changed = False
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    t = sanitize_text(part["text"], source)
                    if t is not part["text"]:
                        part_changed = True
                        part = {**part, "text": t}
                new_parts.append(part)
            if part_changed:
                changed = True
                m = {**m, "content": new_parts}
        cleaned.append(m)
    return cleaned if changed else messages
