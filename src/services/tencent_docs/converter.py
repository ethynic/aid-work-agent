"""doc v3 属性树 -> Markdown 纯函数转换器

树结构（官方文档 §2.5）：Document -> MainStory -> Section -> Paragraph -> Run -> Text，
表格类节点 Table / TableRow / TableCell。仅 Text 叶子节点携带文字。

设计要点：
- 纯函数、无 IO，容错优先：children 中的 None / 非法节点直接跳过
  （2026-10-20 实测响应中确实出现过 None 子节点）。
- 段落在 Markdown 中以空行分隔；TableCell 以 `|` 拼接成表格行（尽力还原，
  无对齐行——知识库分块只关心文本内容，语法完整性非目标）。
- 图片 / 未识别节点类型：递归进 children；叶子层未知节点输出占位符，
  不丢上下文。
"""

from typing import List, Optional

# 未知叶子节点类型的占位文案（type -> 占位）
_LEAF_PLACEHOLDERS = {
    "Image": "（图片）",
    "Drawing": "（图元）",
}


def document_tree_to_markdown(doc: dict) -> str:
    """把 doc v3 响应中的 document 树转为 Markdown 文本。"""
    if not isinstance(doc, dict):
        return ""
    md = _node_to_markdown(doc)
    # 压缩 3 个以上连续换行为 2 个（段落间标准空行）
    lines = [ln.rstrip() for ln in md.splitlines()]
    out: List[str] = []
    blank = 0
    for ln in lines:
        if ln == "":
            blank += 1
            if blank <= 1:
                out.append(ln)
        else:
            blank = 0
            out.append(ln)
    joined = "\n".join(out).strip()
    return joined + "\n" if joined else ""


def _children(node: Optional[dict]) -> List[dict]:
    if not isinstance(node, dict):
        return []
    return [c for c in (node.get("children") or []) if isinstance(c, dict)]


def _node_to_markdown(node: dict) -> str:
    ntype = node.get("type")

    if ntype == "Text":
        return node.get("text") or ""

    if ntype == "Paragraph":
        inner = "".join(_node_to_markdown(c) for c in _children(node)).strip()
        if not inner:
            return ""
        return _paragraph_markdown(node, inner) + "\n\n"

    if ntype == "TableRow":
        cells = [_cell_to_markdown(c) for c in _children(node)]
        if not cells:
            return ""
        return "| " + " | ".join(cells) + " |\n"

    if ntype == "Table":
        rows_md = "".join(_node_to_markdown(c) for c in _children(node))
        if not rows_md:
            return ""
        # 在首个表格行后补分隔行，保证 Markdown 表格语法基本成立
        lines = rows_md.splitlines(keepends=True)
        if len(lines) >= 1:
            # 首行形如 "| a | b |"，竖线数-1 即列数
            cols = max(1, lines[0].count("|") - 1)
            lines.insert(1, "|" + "---|" * cols + "\n")
        return "\n" + "".join(lines) + "\n"

    if ntype in _LEAF_PLACEHOLDERS and not _children(node):
        return _LEAF_PLACEHOLDERS[ntype]

    # Document / MainStory / Section / Run 及一切未识别类型：递归 children
    return "".join(_node_to_markdown(c) for c in _children(node))


def _paragraph_markdown(node: dict, inner: str) -> str:
    """段落级 Markdown 映射：标题/列表尽力识别，识别不了按普通段落。

    paragraphProperty 的标题字段名官方快照未完整记载，预留基于
    nestingLevel（有序/无序列表层级，实测响应可见）的列表映射；
    标题映射等拿到含标题样式的真实属性树样本后补充。
    """
    prop = node.get("property") or {}
    pprop = prop.get("paragraphProperty") or {} if isinstance(prop, dict) else {}
    if not isinstance(pprop, dict):
        return inner
    number = pprop.get("numberProperty") or {}
    nesting = number.get("nestingLevel") or 0
    if number.get("numberId"):
        indent = "  " * int(nesting or 0)
        return f"{indent}- {inner}"
    return inner


def _cell_to_markdown(cell: dict) -> str:
    """TableCell -> 单元格文本（内嵌段落压成一行，换行替换为空格）。"""
    inner = "".join(_node_to_markdown(c) for c in _children(cell))
    return " ".join(inner.split())
