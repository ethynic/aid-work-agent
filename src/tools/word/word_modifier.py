"""
Word 文档内容修改

从 word_ops.py 迁移 modify 操作逻辑。
"""

from typing import Any, Dict

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph

from src.tools.word.word_lib import replace_text_cross_run, StyleManager


def batch_modify(doc: Document, operations: list) -> Dict[str, Any]:
    """批量执行多个修改操作"""
    results = []
    for op in operations:
        result = execute_modify_op(doc, op)
        results.append(result)
    return {
        "success": True,
        "operations_applied": len(results),
        "results": results,
    }


def execute_modify_op(doc: Document, op: Dict[str, Any]) -> Dict[str, Any]:
    """执行单个修改操作"""
    op_type = op.get("type", "") or op.get("op", "")

    try:
        handlers = {
            "replace_text": _op_replace_text,
            "insert_paragraph": _op_insert_paragraph,
            "delete_paragraph": _op_delete_paragraph,
            "replace_paragraph": _op_replace_paragraph,
            "insert_table": _op_insert_table,
            "delete_table": _op_delete_table,
            "modify_table_cell": _op_modify_table_cell,
            "add_page_break": _op_add_page_break,
        }
        handler = handlers.get(op_type)
        if not handler:
            return {"op": op_type, "success": False, "error": f"未知操作类型: {op_type}"}
        return handler(doc, op)
    except Exception as e:
        return {"op": op_type, "success": False, "error": str(e)}


def _op_replace_text(doc: Document, op: Dict[str, Any]) -> Dict[str, Any]:
    """全局查找替换文本（支持跨 run 匹配）"""
    target = op.get("target", "")
    replacement = op.get("replacement", "")
    count = 0

    for para in doc.paragraphs:
        if target in para.text:
            count += replace_text_cross_run(para.runs, target, replacement)

    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for para in cell.paragraphs:
                    if target in para.text:
                        count += replace_text_cross_run(para.runs, target, replacement)

    for section in doc.sections:
        for hf in [section.header, section.footer]:
            for para in hf.paragraphs:
                if target in para.text:
                    count += replace_text_cross_run(para.runs, target, replacement)

    return {"op": "replace_text", "success": True, "replacements": count}


def _op_insert_paragraph(doc: Document, op: Dict[str, Any]) -> Dict[str, Any]:
    """在指定索引后插入段落"""
    after_index = int(op.get("after_index", -1))
    text = op.get("text", "")
    style = op.get("style", "Normal")

    if after_index < 0 or after_index >= len(doc.paragraphs):
        doc.add_paragraph(text, style=style)
        new_para = doc.paragraphs[-1]
    else:
        ref_para = doc.paragraphs[after_index]
        new_para = _insert_paragraph_after(ref_para, text, style)

    font_spec = {k: v for k, v in op.items() if k in
                 ("font_name", "font_size", "bold", "italic", "underline", "color")}
    if font_spec and new_para.runs:
        StyleManager.apply_font(new_para.runs[0], font_spec)

    return {"op": "insert_paragraph", "success": True, "after_index": after_index}


def _insert_paragraph_after(ref_para, text: str, style: str):
    new_p_elem = OxmlElement("w:p")
    r_elem = OxmlElement("w:r")
    t_elem = OxmlElement("w:t")
    t_elem.text = text
    t_elem.set(qn("xml:space"), "preserve")
    r_elem.append(t_elem)
    new_p_elem.append(r_elem)
    ref_para._element.addnext(new_p_elem)
    return Paragraph(new_p_elem, ref_para._element.getparent())


def _op_delete_paragraph(doc: Document, op: Dict[str, Any]) -> Dict[str, Any]:
    """删除指定索引的段落"""
    index = int(op.get("index", -1))
    if index < 0 or index >= len(doc.paragraphs):
        return {"op": "delete_paragraph", "success": False, "error": f"无效段落索引: {index}"}

    para = doc.paragraphs[index]
    para._element.getparent().remove(para._element)
    return {"op": "delete_paragraph", "success": True, "deleted_index": index}


def _op_replace_paragraph(doc: Document, op: Dict[str, Any]) -> Dict[str, Any]:
    """替换指定段落的内容"""
    index = int(op.get("index", -1))
    text = op.get("text", "")
    style = op.get("style")

    if index < 0 or index >= len(doc.paragraphs):
        return {"op": "replace_paragraph", "success": False, "error": f"无效段落索引: {index}"}

    para = doc.paragraphs[index]
    for run in para.runs:
        run.text = ""
    if para.runs:
        para.runs[0].text = text
    else:
        para.text = text

    if style:
        try:
            para.style = style
        except KeyError:
            pass

    font_spec = {k: v for k, v in op.items() if k in
                 ("font_name", "font_size", "bold", "italic", "underline", "color")}
    if font_spec and para.runs:
        StyleManager.apply_font(para.runs[0], font_spec)

    return {"op": "replace_paragraph", "success": True, "index": index}


def _op_insert_table(doc: Document, op: Dict[str, Any]) -> Dict[str, Any]:
    """在指定段落后插入表格"""
    headers = op.get("headers", [])
    rows = op.get("rows", [])

    if not headers and not rows:
        return {"op": "insert_table", "success": False, "error": "表格需要 headers 或 rows"}

    col_count = len(headers) if headers else (len(rows[0]) if rows else 0)
    row_count = (1 if headers else 0) + len(rows)

    table = doc.add_table(rows=row_count, cols=col_count)
    style_name = op.get("style", "Table Grid")
    try:
        table.style = style_name
    except KeyError:
        pass

    start_row = 0
    if headers:
        for col_idx, h in enumerate(headers):
            cell = table.rows[0].cells[col_idx]
            cell.text = str(h)
            for p in cell.paragraphs:
                for r in p.runs:
                    r.font.bold = True
        start_row = 1

    for row_idx, row_data in enumerate(rows):
        for col_idx, cell_text in enumerate(row_data):
            if col_idx < col_count:
                table.rows[start_row + row_idx].cells[col_idx].text = str(cell_text)

    return {"op": "insert_table", "success": True, "rows": row_count, "cols": col_count}


def _op_delete_table(doc: Document, op: Dict[str, Any]) -> Dict[str, Any]:
    """删除指定索引的表格"""
    index = int(op.get("index", -1))
    if index < 0 or index >= len(doc.tables):
        return {"op": "delete_table", "success": False, "error": f"无效表格索引: {index}"}

    table = doc.tables[index]
    table._element.getparent().remove(table._element)
    return {"op": "delete_table", "success": True, "deleted_index": index}


def _op_modify_table_cell(doc: Document, op: Dict[str, Any]) -> Dict[str, Any]:
    """修改表格单元格内容"""
    table_index = int(op.get("table_index", 0))
    row = int(op.get("row", 0))
    col = int(op.get("col", 0))
    text = op.get("text", "")

    if table_index < 0 or table_index >= len(doc.tables):
        return {"op": "modify_table_cell", "success": False, "error": f"无效表格索引: {table_index}"}

    table = doc.tables[table_index]
    if row < 0 or row >= len(table.rows):
        return {"op": "modify_table_cell", "success": False, "error": f"无效行索引: {row}"}
    if col < 0 or col >= len(table.columns):
        return {"op": "modify_table_cell", "success": False, "error": f"无效列索引: {col}"}

    table.rows[row].cells[col].text = text
    return {"op": "modify_table_cell", "success": True, "table": table_index, "cell": f"({row},{col})"}


def _op_add_page_break(doc: Document, op: Dict[str, Any]) -> Dict[str, Any]:
    """添加分页符"""
    doc.add_page_break()
    return {"op": "add_page_break", "success": True}
