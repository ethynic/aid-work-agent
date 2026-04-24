#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Word Processing Skill - CLI 入口

提供 4 个子命令用于 Word 文档操作：
- analyze: 分析文档结构（段落、表格、样式、属性）
- create-from-md: 从 Markdown 文件创建新文档
- modify: 基于操作列表修改已有文档（始终创建副本）
- format: 调整文档格式（字体、段落、页面设置、页眉页脚）

使用方式:
    python scripts/word_ops.py analyze --file-path doc.docx
    python scripts/word_ops.py create-from-md [--title "标题"]
    python scripts/word_ops.py modify --file-path doc.docx --instructions '<json>'
    python scripts/word_ops.py format --file-path doc.docx --instructions '<json>'
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

# 确保可以导入同目录的 word_lib
sys.path.insert(0, str(Path(__file__).parent))

from word_lib import (
    FileHandler,
    StyleManager,
    parse_instructions,
    document_to_dict,
    markdown_to_doc,
    _resolve_alignment,
    _replace_text_cross_run,
    get_template,
    list_templates,
    PAGE_SIZES,
)
from docx import Document
from docx.shared import Cm
from docx.oxml.ns import qn


# =============================================================================
# analyze 子命令
# =============================================================================

def cmd_analyze(args):
    """分析文档结构"""
    file_path = args.file_path
    if not Path(file_path).exists():
        _error_exit(f"文件不存在: {file_path}")

    try:
        doc = Document(file_path)
    except Exception as e:
        _error_exit(f"无法打开文档: {e}")

    result = document_to_dict(doc, detailed=args.detailed)
    result["file_path"] = str(Path(file_path).absolute())
    result["file_name"] = Path(file_path).name

    _output(result)


# =============================================================================
# （create 子命令已移除 — 创建新文档统一使用 create-from-md）
# =============================================================================


# =============================================================================
# list-templates 子命令
# =============================================================================

def cmd_list_templates(args):
    """列出所有可用模板"""
    templates = list_templates()
    _output({"success": True, "templates": templates})


# =============================================================================
# create-from-md 子命令
# =============================================================================

def cmd_create_from_md(args):
    """从 Markdown 创建 Word 文档（支持 stdin 或文件）"""
    md_text = ""

    # 优先从 stdin 读取（skill_execute 的 content 参数通过 stdin 传入）
    # 显式以 UTF-8 读取字节流，避免 Windows 上 sys.stdin 使用 GBK 等默认编码
    if not sys.stdin.isatty():
        raw = sys.stdin.buffer.read()
        md_text = raw.decode("utf-8", errors="replace")
    # 其次从文件读取
    elif args.md_file and Path(args.md_file).exists():
        try:
            md_text = Path(args.md_file).read_text(encoding="utf-8")
        except Exception as e:
            _error_exit(f"无法读取 Markdown 文件: {e}")
    else:
        _error_exit("未提供 Markdown 内容。请通过 stdin 管道传入，或使用 --md-file 指定文件")

    if not md_text.strip():
        _error_exit("Markdown 内容为空")

    # 清理 LLM 可能生成的 UTF-16 代理字符（surrogates）
    md_text = md_text.encode("utf-8", errors="ignore").decode("utf-8")

    title = args.title or ""
    author = args.author or ""

    # 加载模板
    template = None
    if args.template:
        try:
            template = get_template(args.template)
        except ValueError as e:
            _error_exit(str(e))

    doc = markdown_to_doc(md_text, title=title, author=author, template=template)

    # 保存到临时目录
    file_name = args.file_name or (f"{title}.docx" if title else "document.docx")
    result = FileHandler.save_temp(doc, file_name=file_name, output_dir=args.output_dir)
    result["success"] = True
    result["message"] = "从 Markdown 创建 Word 文档成功"
    if args.template:
        result["template"] = args.template

    _output(result)


# =============================================================================
# modify 子命令
# =============================================================================

def cmd_modify(args):
    """修改已有文档"""
    file_path = args.file_path
    if not Path(file_path).exists():
        _error_exit(f"文件不存在: {file_path}")

    try:
        instructions = _resolve_instructions(args)
    except ValueError as e:
        _error_exit(str(e))

    # 始终操作副本
    doc, info = FileHandler.copy_and_open(file_path)

    operations = instructions.get("operations", [])
    if not operations:
        _error_exit("指令中缺少 operations 列表。请提供至少一个操作。")

    results = []
    for op in operations:
        result = _execute_modify_op(doc, op)
        results.append(result)

    # 保存
    output = FileHandler.save_temp(
        doc,
        output_dir=args.output_dir,
        file_name=Path(file_path).stem + "_modified.docx",
    )
    output["success"] = True
    output["message"] = f"文档修改完成，共执行 {len(results)} 个操作"
    output["original_file"] = info
    output["operation_results"] = results

    _output(output)


def _execute_modify_op(doc: Document, op: Dict[str, Any]) -> Dict[str, Any]:
    """执行单个修改操作"""
    op_type = op.get("op", "")

    try:
        if op_type == "replace_text":
            return _op_replace_text(doc, op)
        elif op_type == "insert_paragraph":
            return _op_insert_paragraph(doc, op)
        elif op_type == "delete_paragraph":
            return _op_delete_paragraph(doc, op)
        elif op_type == "replace_paragraph":
            return _op_replace_paragraph(doc, op)
        elif op_type == "insert_table":
            return _op_insert_table(doc, op)
        elif op_type == "delete_table":
            return _op_delete_table(doc, op)
        elif op_type == "modify_table_cell":
            return _op_modify_table_cell(doc, op)
        elif op_type == "add_page_break":
            return _op_add_page_break(doc, op)
        else:
            return {"op": op_type, "success": False, "error": f"未知操作类型: {op_type}"}
    except Exception as e:
        return {"op": op_type, "success": False, "error": str(e)}


def _op_replace_text(doc: Document, op: Dict[str, Any]) -> Dict[str, Any]:
    """全局查找替换文本（支持跨 run 匹配）"""
    target = op.get("target", "")
    replacement = op.get("replacement", "")
    count = 0

    # 正文段落
    for para in doc.paragraphs:
        if target in para.text:
            count += _replace_text_cross_run(para.runs, target, replacement)

    # 表格
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for para in cell.paragraphs:
                    if target in para.text:
                        count += _replace_text_cross_run(para.runs, target, replacement)

    # 页眉页脚
    for section in doc.sections:
        for hf in [section.header, section.footer]:
            for para in hf.paragraphs:
                if target in para.text:
                    count += _replace_text_cross_run(para.runs, target, replacement)

    return {"op": "replace_text", "success": True, "replacements": count,
            "detail": f"替换 '{target}' → '{replacement}'，共 {count} 处"}


def _op_insert_paragraph(doc: Document, op: Dict[str, Any]) -> Dict[str, Any]:
    """在指定索引后插入段落"""
    after_index = int(op.get("after_index", -1))
    text = op.get("text", "")
    style = op.get("style", "Normal")

    if after_index < 0 or after_index >= len(doc.paragraphs):
        # 追加到末尾
        para = doc.add_paragraph(text, style=style)
    else:
        # 在指定段落后插入
        ref_para = doc.paragraphs[after_index]
        new_para = _insert_paragraph_after(ref_para, text, style)

    # 应用额外格式
    font_spec = {k: v for k, v in op.items() if k in
                 ("font_name", "font_size", "bold", "italic", "underline", "color")}
    para_to_format = new_para if after_index >= 0 and after_index < len(doc.paragraphs) else doc.paragraphs[-1]
    if font_spec and para_to_format.runs:
        StyleManager.apply_font(para_to_format.runs[0], font_spec)

    return {"op": "insert_paragraph", "success": True, "after_index": after_index}


def _insert_paragraph_after(ref_para, text: str, style: str):
    """在指定段落后插入新段落"""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.text.paragraph import Paragraph

    new_p_elem = OxmlElement("w:p")

    # 添加 run
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
    # 清除原有内容
    for run in para.runs:
        run.text = ""
    # 设置新内容
    if para.runs:
        para.runs[0].text = text
    else:
        para.text = text

    if style:
        try:
            para.style = style
        except KeyError:
            pass

    # 应用字体
    font_spec = {k: v for k, v in op.items() if k in
                 ("font_name", "font_size", "bold", "italic", "underline", "color")}
    if font_spec and para.runs:
        StyleManager.apply_font(para.runs[0], font_spec)

    return {"op": "replace_paragraph", "success": True, "index": index}


def _op_insert_table(doc: Document, op: Dict[str, Any]) -> Dict[str, Any]:
    """在指定段落后插入表格"""
    after_index = int(op.get("after_index", len(doc.paragraphs) - 1))
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
    """在指定段落后添加分页符"""
    after_index = int(op.get("after_index", -1))
    doc.add_page_break()
    return {"op": "add_page_break", "success": True}


# =============================================================================
# format 子命令
# =============================================================================

def cmd_format(args):
    """调整文档格式"""
    file_path = args.file_path
    if not Path(file_path).exists():
        _error_exit(f"文件不存在: {file_path}")

    try:
        instructions = _resolve_instructions(args)
    except ValueError as e:
        _error_exit(str(e))

    # 始终操作副本
    doc, info = FileHandler.copy_and_open(file_path)

    operations = instructions.get("operations", [])
    if not operations:
        _error_exit("指令中缺少 operations 列表。请提供至少一个格式操作。")

    results = []
    for op in operations:
        result = _execute_format_op(doc, op)
        results.append(result)

    # 保存
    output = FileHandler.save_temp(
        doc,
        output_dir=args.output_dir,
        file_name=Path(file_path).stem + "_formatted.docx",
    )
    output["success"] = True
    output["message"] = f"格式调整完成，共执行 {len(results)} 个操作"
    output["original_file"] = info
    output["operation_results"] = results

    _output(output)


def _execute_format_op(doc: Document, op: Dict[str, Any]) -> Dict[str, Any]:
    """执行单个格式操作"""
    op_type = op.get("op", "")

    try:
        if op_type == "set_font":
            return _fmt_set_font(doc, op)
        elif op_type == "set_paragraph_format":
            return _fmt_set_paragraph_format(doc, op)
        elif op_type == "set_page_margins":
            return _fmt_set_page_margins(doc, op)
        elif op_type == "set_page_size":
            return _fmt_set_page_size(doc, op)
        elif op_type == "set_header":
            return _fmt_set_header(doc, op)
        elif op_type == "set_footer":
            return _fmt_set_footer(doc, op)
        elif op_type == "set_table_style":
            return _fmt_set_table_style(doc, op)
        else:
            return {"op": op_type, "success": False, "error": f"未知格式操作: {op_type}"}
    except Exception as e:
        return {"op": op_type, "success": False, "error": str(e)}


def _get_scope_paragraphs(doc: Document, scope: str):
    """根据 scope 获取匹配的段落列表"""
    if scope in ("all", "paragraphs"):
        return doc.paragraphs
    elif scope == "heading1":
        return [p for p in doc.paragraphs if p.style and p.style.name == "Heading 1"]
    elif scope == "heading2":
        return [p for p in doc.paragraphs if p.style and p.style.name == "Heading 2"]
    elif scope == "heading3":
        return [p for p in doc.paragraphs if p.style and p.style.name == "Heading 3"]
    elif scope.startswith("heading"):
        try:
            level = int(scope.replace("heading", ""))
            return [p for p in doc.paragraphs if p.style and p.style.name == f"Heading {level}"]
        except ValueError:
            return []
    elif scope == "table":
        # 表格中的段落
        paras = []
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    paras.extend(cell.paragraphs)
        return paras
    else:
        # 按样式名匹配
        return [p for p in doc.paragraphs if p.style and p.style.name == scope]


def _fmt_set_font(doc: Document, op: Dict[str, Any]) -> Dict[str, Any]:
    """设置字体"""
    scope = op.get("scope", "all")
    paragraphs = _get_scope_paragraphs(doc, scope)
    count = 0

    font_spec = {k: v for k, v in op.items() if k in
                 ("font_name", "font_size", "bold", "italic", "underline", "color", "color_hex")}

    if not font_spec:
        return {"op": "set_font", "success": False, "error": "未指定任何字体属性"}

    for para in paragraphs:
        for run in para.runs:
            StyleManager.apply_font(run, font_spec)
            count += 1
        # 如果段落没有 run（纯文本），创建一个
        if not para.runs and para.text:
            run = para.add_run(para.text)
            para.text = ""
            StyleManager.apply_font(run, font_spec)
            count += 1

    return {"op": "set_font", "success": True, "scope": scope, "runs_affected": count}


def _fmt_set_paragraph_format(doc: Document, op: Dict[str, Any]) -> Dict[str, Any]:
    """设置段落格式"""
    scope = op.get("scope", "all")
    paragraphs = _get_scope_paragraphs(doc, scope)

    para_spec = {k: v for k, v in op.items() if k in
                 ("alignment", "line_spacing", "space_before", "space_after",
                  "first_line_indent")}

    if not para_spec:
        return {"op": "set_paragraph_format", "success": False, "error": "未指定任何段落格式属性"}

    count = 0
    for para in paragraphs:
        StyleManager.apply_paragraph_format(para, para_spec)
        count += 1

    return {"op": "set_paragraph_format", "success": True, "scope": scope, "paragraphs_affected": count}


def _fmt_set_page_margins(doc: Document, op: Dict[str, Any]) -> Dict[str, Any]:
    """设置页面边距"""
    for section in doc.sections:
        if "top" in op:
            section.top_margin = Cm(op["top"])
        if "bottom" in op:
            section.bottom_margin = Cm(op["bottom"])
        if "left" in op:
            section.left_margin = Cm(op["left"])
        if "right" in op:
            section.right_margin = Cm(op["right"])

    return {"op": "set_page_margins", "success": True, "sections_affected": len(doc.sections)}


def _fmt_set_page_size(doc: Document, op: Dict[str, Any]) -> Dict[str, Any]:
    """设置页面大小和方向"""
    size = op.get("size", "A4")
    orientation = op.get("orientation", "portrait")

    if size.upper() not in PAGE_SIZES:
        return {"op": "set_page_size", "success": False,
                "error": f"不支持的页面大小: {size}。支持: {', '.join(PAGE_SIZES.keys())}"}

    w, h = PAGE_SIZES[size.upper()]
    for section in doc.sections:
        if orientation.lower() == "landscape":
            section.page_width = Cm(h)
            section.page_height = Cm(w)
        else:
            section.page_width = Cm(w)
            section.page_height = Cm(h)

    return {"op": "set_page_size", "success": True, "size": size, "orientation": orientation}


def _fmt_set_header(doc: Document, op: Dict[str, Any]) -> Dict[str, Any]:
    """设置页眉"""
    if not doc.sections:
        return {"op": "set_header", "success": False, "error": "文档没有 section"}

    section = doc.sections[0]
    header = section.header
    header.is_linked_to_previous = False

    # 清除现有内容
    for para in header.paragraphs:
        para.text = ""

    if header.paragraphs:
        para = header.paragraphs[0]
    else:
        para = header.add_paragraph()

    para.text = op.get("text", "")

    # 字体
    font_spec = {k: v for k, v in op.items() if k in
                 ("font_name", "font_size", "bold", "italic", "color")}
    if font_spec and para.runs:
        StyleManager.apply_font(para.runs[0], font_spec)

    # 对齐
    align = op.get("alignment")
    if align:
        resolved = _resolve_alignment(align)
        if resolved is not None:
            para.paragraph_format.alignment = resolved

    return {"op": "set_header", "success": True}


def _fmt_set_footer(doc: Document, op: Dict[str, Any]) -> Dict[str, Any]:
    """设置页脚（支持页码）"""
    if not doc.sections:
        return {"op": "set_footer", "success": False, "error": "文档没有 section"}

    section = doc.sections[0]
    footer = section.footer
    footer.is_linked_to_previous = False

    # 清除现有内容
    for para in footer.paragraphs:
        para.text = ""

    if footer.paragraphs:
        para = footer.paragraphs[0]
    else:
        para = footer.add_paragraph()

    para.text = op.get("text", "")

    # 添加页码
    if op.get("include_page_number"):
        from docx.oxml import OxmlElement
        run = para.add_run()
        fldChar1 = OxmlElement("w:fldChar")
        fldChar1.set(qn("w:fldCharType"), "begin")
        run._element.append(fldChar1)

        instrText = OxmlElement("w:instrText")
        instrText.set(qn("xml:space"), "preserve")
        instrText.text = " PAGE "
        run._element.append(instrText)

        fldChar2 = OxmlElement("w:fldChar")
        fldChar2.set(qn("w:fldCharType"), "end")
        run._element.append(fldChar2)

    # 字体
    font_spec = {k: v for k, v in op.items() if k in
                 ("font_name", "font_size", "bold", "italic", "color")}
    if font_spec and para.runs:
        StyleManager.apply_font(para.runs[0], font_spec)

    # 对齐
    align = op.get("alignment")
    if align:
        resolved = _resolve_alignment(align)
        if resolved is not None:
            para.paragraph_format.alignment = resolved

    return {"op": "set_footer", "success": True}


def _fmt_set_table_style(doc: Document, op: Dict[str, Any]) -> Dict[str, Any]:
    """设置表格样式"""
    table_index = int(op.get("table_index", 0))
    style_name = op.get("style", "Table Grid")

    if table_index < 0 or table_index >= len(doc.tables):
        return {"op": "set_table_style", "success": False, "error": f"无效表格索引: {table_index}"}

    table = doc.tables[table_index]
    try:
        table.style = style_name
    except KeyError:
        return {"op": "set_table_style", "success": False, "error": f"无效表格样式: {style_name}"}

    # 表头行加粗
    if op.get("header_row_bold") and table.rows:
        for cell in table.rows[0].cells:
            for para in cell.paragraphs:
                for run in para.runs:
                    run.font.bold = True

    return {"op": "set_table_style", "success": True, "table_index": table_index, "style": style_name}


# =============================================================================
# 工具函数
# =============================================================================

def _output(data: Dict[str, Any]):
    """输出 JSON 到 stdout"""
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _error_exit(message: str):
    """输出错误并退出"""
    print(json.dumps({"success": False, "error": message}, ensure_ascii=False))
    sys.exit(1)


def _resolve_instructions(args) -> Dict[str, Any]:
    """从 --instructions 或 --instructions-file 解析 JSON 指令"""
    instructions_str = args.instructions
    instructions_file = getattr(args, 'instructions_file', None)

    if instructions_file:
        # 从文件读取
        file_path = Path(instructions_file)
        if not file_path.exists():
            # 尝试在工作目录中查找
            file_path = Path.cwd() / instructions_file
        if not file_path.exists():
            _error_exit(f"指令文件不存在: {instructions_file}")
        try:
            instructions_str = file_path.read_text(encoding="utf-8")
        except Exception as e:
            _error_exit(f"无法读取指令文件: {e}")

    if not instructions_str:
        _error_exit("请通过 --instructions 或 --instructions-file 提供 JSON 指令")

    return parse_instructions(instructions_str)


# =============================================================================
# 主入口
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Word 文档处理工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # analyze
    p_analyze = subparsers.add_parser("analyze", help="分析文档结构")
    p_analyze.add_argument("--file-path", required=True, help="文档文件路径")
    p_analyze.add_argument("--detailed", action="store_true", help="输出详细字体信息")

    # modify
    p_modify = subparsers.add_parser("modify", help="修改已有文档")
    p_modify.add_argument("--file-path", required=True, help="源文档路径")
    p_modify.add_argument("--instructions", default=None, help="JSON 格式的修改操作")
    p_modify.add_argument("--instructions-file", default=None, help="JSON 指令文件路径")
    p_modify.add_argument("--output-dir", default=None, help="输出目录")

    # format
    p_format = subparsers.add_parser("format", help="调整文档格式")
    p_format.add_argument("--file-path", required=True, help="源文档路径")
    p_format.add_argument("--instructions", default=None, help="JSON 格式的格式操作")
    p_format.add_argument("--instructions-file", default=None, help="JSON 指令文件路径")
    p_format.add_argument("--output-dir", default=None, help="输出目录")

    # create-from-md
    p_md = subparsers.add_parser("create-from-md", help="从 Markdown 创建 Word 文档（默认从 stdin 读取）")
    p_md.add_argument("--md-file", default=None, help="Markdown 文件路径（可选，默认从 stdin 读取）")
    p_md.add_argument("--title", default=None, help="文档标题")
    p_md.add_argument("--author", default=None, help="文档作者")
    p_md.add_argument("--file-name", default=None, help="输出文件名")
    p_md.add_argument("--output-dir", default=None, help="输出目录")
    p_md.add_argument("--template", default=None, help="模板名称 (default, formal, modern, report)")

    # list-templates
    subparsers.add_parser("list-templates", help="列出可用的文档模板")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "analyze":
        cmd_analyze(args)
    elif args.command == "modify":
        cmd_modify(args)
    elif args.command == "format":
        cmd_format(args)
    elif args.command == "create-from-md":
        cmd_create_from_md(args)
    elif args.command == "list-templates":
        cmd_list_templates(args)


if __name__ == "__main__":
    main()
