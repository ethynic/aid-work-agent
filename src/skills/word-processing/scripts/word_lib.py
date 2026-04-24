#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Word Processing Skill - 共享库

提供 Word 文档分析、创建、修改、格式化所需的核心功能。

核心组件：
- StyleManager: 样式操作（字体、段落格式、自定义样式）
- FileHandler: 文件输出管理（保存到 uploads 目录，生成 file_id）
- parse_instructions(): 校验 LLM 生成的 JSON 指令
- document_to_dict(): 将 python-docx Document 转为结构化字典
"""

import json
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from docx import Document
from docx.shared import Pt, Cm, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn

# 常见中文字体映射
CHINESE_FONTS = {
    "宋体": "SimSun",
    "黑体": "SimHei",
    "仿宋": "FangSong",
    "楷体": "KaiTi",
    "SimSun": "SimSun",
    "SimHei": "SimHei",
    "FangSong": "FangSong",
    "KaiTi": "KaiTi",
    "微软雅黑": "Microsoft YaHei",
    "Microsoft YaHei": "Microsoft YaHei",
}

# 对齐方式映射
ALIGNMENT_MAP = {
    "LEFT": WD_ALIGN_PARAGRAPH.LEFT,
    "CENTER": WD_ALIGN_PARAGRAPH.CENTER,
    "RIGHT": WD_ALIGN_PARAGRAPH.RIGHT,
    "JUSTIFY": WD_ALIGN_PARAGRAPH.JUSTIFY,
    "left": WD_ALIGN_PARAGRAPH.LEFT,
    "center": WD_ALIGN_PARAGRAPH.CENTER,
    "right": WD_ALIGN_PARAGRAPH.RIGHT,
    "justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
}

# 页面大小映射（宽 x 高，单位 Cm）
PAGE_SIZES = {
    "A4": (21.0, 29.7),
    "A3": (29.7, 42.0),
    "Letter": (21.59, 27.94),
    "B5": (17.6, 25.0),
}

# 默认 uploads 目录
DEFAULT_UPLOAD_DIR = Path("./uploads")


def _resolve_font_name(name: str) -> str:
    """解析字体名称，支持中文名映射"""
    if not name:
        return name
    return CHINESE_FONTS.get(name, name)


def _resolve_alignment(align: str):
    """解析对齐方式字符串为 python-docx 枚举值"""
    if isinstance(align, str):
        return ALIGNMENT_MAP.get(align, None)
    return align


def _parse_color(color_str: str) -> Optional[RGBColor]:
    """解析颜色字符串（支持 #RRGGBB 和纯 RRGGBB 格式）"""
    if not color_str:
        return None
    color_str = color_str.lstrip("#")
    if len(color_str) == 6:
        try:
            r, g, b = int(color_str[:2], 16), int(color_str[2:4], 16), int(color_str[4:6], 16)
            return RGBColor(r, g, b)
        except ValueError:
            return None
    return None


class StyleManager:
    """样式操作封装"""

    @staticmethod
    def apply_font(run, font_spec: Dict[str, Any]):
        """对 run 应用字体规格"""
        if not font_spec:
            return

        font = run.font

        font_name = font_spec.get("font_name") or font_spec.get("fontName")
        if font_name:
            resolved = _resolve_font_name(font_name)
            font.name = resolved
            # 设置东亚字体（中文字符使用相同字体）
            run._element.rPr.rFonts.set(qn("w:eastAsia"), resolved)

        font_size = font_spec.get("font_size") or font_spec.get("fontSize") or font_spec.get("size")
        if font_size is not None:
            font.size = Pt(float(font_size))

        bold = font_spec.get("bold")
        if bold is not None:
            font.bold = bool(bold)

        italic = font_spec.get("italic")
        if italic is not None:
            font.italic = bool(italic)

        underline = font_spec.get("underline")
        if underline is not None:
            font.underline = bool(underline)

        color = font_spec.get("color") or font_spec.get("color_hex")
        if color:
            rgb = _parse_color(color)
            if rgb:
                font.color.rgb = rgb

    @staticmethod
    def apply_paragraph_format(paragraph, para_spec: Dict[str, Any]):
        """对段落应用格式规格"""
        if not para_spec:
            return

        pf = paragraph.paragraph_format

        alignment = para_spec.get("alignment")
        if alignment:
            resolved = _resolve_alignment(alignment)
            if resolved is not None:
                pf.alignment = resolved

        line_spacing = para_spec.get("line_spacing") or para_spec.get("lineSpacing")
        if line_spacing is not None:
            pf.line_spacing = float(line_spacing)

        space_before = para_spec.get("space_before") or para_spec.get("spaceBefore")
        if space_before is not None:
            pf.space_before = Pt(float(space_before))

        space_after = para_spec.get("space_after") or para_spec.get("spaceAfter")
        if space_after is not None:
            pf.space_after = Pt(float(space_after))

        first_line_indent = para_spec.get("first_line_indent") or para_spec.get("firstLineIndent")
        if first_line_indent is not None:
            pf.first_line_indent = Cm(float(first_line_indent))

    @staticmethod
    def ensure_style(doc: Document, style_name: str, base_style: str = "Normal",
                     font_props: Optional[Dict] = None,
                     para_props: Optional[Dict] = None):
        """确保文档中存在指定样式，不存在则创建"""
        try:
            styles = doc.styles
            # 检查样式是否已存在
            for s in styles:
                if s.name == style_name:
                    return s

            # 创建新样式
            style = styles.add_style(style_name, 1)  # WD_STYLE_TYPE.PARAGRAPH = 1
            if base_style:
                try:
                    style.base_style = styles[base_style]
                except KeyError:
                    pass

            if font_props:
                StyleManager.apply_font(style.font, font_props)
            if para_props:
                StyleManager.apply_paragraph_format(style.paragraph_format, para_props)

            return style
        except Exception:
            return None


class FileHandler:
    """文件输出管理"""

    @staticmethod
    def save_temp(doc: Document, file_name: Optional[str] = None,
                  output_dir: Optional[str] = None) -> Dict[str, Any]:
        """
        将文档保存到临时目录或指定目录。

        不再自行注册到下载系统——由 LLM 调用 register_download_file 工具完成注册。

        Returns:
            {"file_path": str, "file_size": int}
        """
        import tempfile
        save_dir = Path(output_dir) if output_dir else Path(tempfile.mkdtemp(prefix="word_"))
        save_dir.mkdir(parents=True, exist_ok=True)

        if not file_name:
            file_name = f"document_{uuid.uuid4().hex[:8]}.docx"
        elif not file_name.endswith(".docx"):
            file_name += ".docx"

        output_path = save_dir / file_name
        doc.save(str(output_path))

        file_size = output_path.stat().st_size
        return {
            "file_path": str(output_path.absolute()),
            "file_size": file_size,
        }

    # 保留 save_to_uploads 向后兼容
    @staticmethod
    def save_to_uploads(doc: Document, output_dir: Optional[str] = None,
                        file_name: Optional[str] = None) -> Dict[str, Any]:
        """兼容旧调用方式，内部调用 save_temp"""
        return FileHandler.save_temp(doc, file_name=file_name, output_dir=output_dir)

    @staticmethod
    def copy_and_open(file_path: str) -> Tuple[Document, Dict[str, Any]]:
        """
        打开文件并创建副本（永不修改原文件）。

        Returns:
            (doc, info) - doc 为 python-docx Document，info 包含原始文件信息
        """
        src = Path(file_path)
        if not src.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        doc = Document(str(src))
        info = {
            "original_path": str(src.absolute()),
            "original_name": src.name,
            "original_size": src.stat().st_size,
        }
        return doc, info


def parse_instructions(json_str: str) -> Dict[str, Any]:
    """
    解析并校验 LLM 生成的 JSON 指令。

    Returns:
        解析后的字典

    Raises:
        ValueError: JSON 格式错误或缺少必要字段，错误信息清晰便于 LLM 自纠正
    """
    if not json_str or not json_str.strip():
        raise ValueError("指令不能为空。请提供 JSON 格式的操作指令。")

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"JSON 格式错误: {e}。"
            f"请确保 JSON 字符串格式正确，特别注意引号和转义字符。"
            f"提示：命令行中的 JSON 需要用单引号包裹，内部字符串用双引号。"
        )

    if not isinstance(data, dict):
        raise ValueError(f"指令必须是 JSON 对象，当前类型为: {type(data).__name__}")

    return data


def document_to_dict(doc: Document, detailed: bool = False) -> Dict[str, Any]:
    """
    将 python-docx Document 转为结构化字典，供 LLM 分析文档结构。

    输出包含段落（含样式和字体信息）、表格、页眉页脚、文档属性。

    Args:
        doc: python-docx Document 对象
        detailed: 是否包含每个 run 的详细字体信息
    """
    result = {
        "success": True,
        "document_properties": _extract_properties(doc),
        "styles_found": _extract_style_names(doc),
        "sections": [],
        "summary": {
            "paragraph_count": 0,
            "table_count": 0,
            "section_count": len(doc.sections),
        },
    }

    total_chars = 0

    for section_idx, section in enumerate(doc.sections):
        section_data = {
            "section_index": section_idx,
            "paragraphs": [],
            "tables": [],
            "page_setup": _extract_page_setup(section),
        }

        # 页面尺寸
        page_setup = section_data["page_setup"]

        # 页眉页脚
        section_data["header"] = _extract_header_footer(section, "header")
        section_data["footer"] = _extract_header_footer(section, "footer")

        # 段落 — 遍历文档 body 的直接子元素，以正确区分段落和表格
        body = doc.element.body
        para_idx = 0
        table_idx = 0

        for element in body:
            if element.tag == qn("w:p"):
                # 这是一个段落
                if para_idx < len(doc.paragraphs):
                    para = doc.paragraphs[para_idx]
                    text = para.text
                    if text.strip() or detailed:
                        para_data = _extract_paragraph(para, para_idx, detailed)
                        section_data["paragraphs"].append(para_data)
                        total_chars += len(text)
                    else:
                        # 空段落也记录索引，方便 modify 定位
                        section_data["paragraphs"].append({
                            "index": para_idx,
                            "text": "",
                            "style": para.style.name if para.style else "Normal",
                        })
                    para_idx += 1
            elif element.tag == qn("w:tbl"):
                # 这是一个表格
                if table_idx < len(doc.tables):
                    table = doc.tables[table_idx]
                    table_data = _extract_table(table, table_idx)
                    section_data["tables"].append(table_data)
                    total_chars += len(table_data.get("text_content", ""))
                    table_idx += 1

        result["sections"].append(section_data)
        result["summary"]["paragraph_count"] += para_idx
        result["summary"]["table_count"] += table_idx

    result["summary"]["total_chars"] = total_chars

    return result


def _extract_properties(doc: Document) -> Dict[str, Any]:
    """提取文档属性"""
    props = doc.core_properties
    return {
        "title": props.title or "",
        "author": props.author or "",
        "subject": props.subject or "",
        "keywords": props.keywords or "",
        "created": str(props.created) if props.created else "",
        "modified": str(props.modified) if props.modified else "",
        "last_modified_by": props.last_modified_by or "",
        "revision": props.revision or 0,
        "category": props.category or "",
    }


def _extract_style_names(doc: Document) -> List[str]:
    """提取文档中使用的样式名称"""
    names = set()
    for para in doc.paragraphs:
        if para.style:
            names.add(para.style.name)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for para in cell.paragraphs:
                    if para.style:
                        names.add(para.style.name)
    return sorted(names)


def _extract_page_setup(section) -> Dict[str, Any]:
    """提取页面设置"""
    try:
        return {
            "page_width_cm": round(section.page_width.cm, 2) if section.page_width else None,
            "page_height_cm": round(section.page_height.cm, 2) if section.page_height else None,
            "top_margin_cm": round(section.top_margin.cm, 2) if section.top_margin else None,
            "bottom_margin_cm": round(section.bottom_margin.cm, 2) if section.bottom_margin else None,
            "left_margin_cm": round(section.left_margin.cm, 2) if section.left_margin else None,
            "right_margin_cm": round(section.right_margin.cm, 2) if section.right_margin else None,
            "orientation": str(section.orientation) if section.orientation else "PORTRAIT",
        }
    except Exception:
        return {}


def _extract_header_footer(section, which: str) -> Dict[str, Any]:
    """提取页眉或页脚内容"""
    try:
        if which == "header":
            hf = section.header
        else:
            hf = section.footer

        if hf.is_linked_to_previous:
            return {"linked_to_previous": True, "text": ""}

        paragraphs = []
        for para in hf.paragraphs:
            if para.text.strip():
                paragraphs.append(para.text.strip())

        return {
            "linked_to_previous": False,
            "text": "\n".join(paragraphs),
            "paragraph_count": len(paragraphs),
        }
    except Exception:
        return {"text": ""}


def _extract_paragraph(para, index: int, detailed: bool = False) -> Dict[str, Any]:
    """提取段落详细信息"""
    data = {
        "index": index,
        "text": para.text,
        "style": para.style.name if para.style else "Normal",
    }

    # 段落格式
    pf = para.paragraph_format
    if pf.alignment is not None:
        data["alignment"] = str(pf.alignment).replace("WD_ALIGN_PARAGRAPH.", "")
    if pf.line_spacing is not None:
        data["line_spacing"] = pf.line_spacing
    if pf.space_before is not None:
        data["space_before_pt"] = pf.space_before.pt
    if pf.space_after is not None:
        data["space_after_pt"] = pf.space_after.pt

    # 段落级字体信息（从第一个 run 获取）
    if para.runs:
        first_run = para.runs[0]
        font = first_run.font
        data["font_name"] = font.name or ""
        data["font_size"] = font.size.pt if font.size else None
        data["bold"] = font.bold
        data["italic"] = font.italic

        if detailed:
            data["runs"] = []
            for run in para.runs:
                run_data = {"text": run.text}
                rf = run.font
                if rf.name:
                    run_data["font_name"] = rf.name
                if rf.size:
                    run_data["font_size"] = rf.size.pt
                if rf.bold is not None:
                    run_data["bold"] = rf.bold
                if rf.italic is not None:
                    run_data["italic"] = rf.italic
                if rf.underline is not None:
                    run_data["underline"] = rf.underline
                if rf.color and rf.color.rgb:
                    run_data["color"] = str(rf.color.rgb)
                data["runs"].append(run_data)

    return data


def _extract_table(table, index: int) -> Dict[str, Any]:
    """提取表格信息"""
    rows_data = []
    for row in table.rows:
        row_data = [cell.text.strip() for cell in row.cells]
        rows_data.append(row_data)

    # 表格文本内容
    text_content = " | ".join(" | ".join(row) for row in rows_data)

    return {
        "index": index,
        "rows": len(table.rows),
        "cols": len(table.columns) if table.columns else 0,
        "data": rows_data,
        "text_content": text_content,
        "style": table.style.name if table.style else "",
    }


# =============================================================================
# 跨 Run 文本替换
# =============================================================================

def _replace_text_cross_run(runs, target: str, replacement: str) -> int:
    """
    替换可能跨越多个 run 的文本。

    Word 文档中一个逻辑句子可能被拆分为多个 run（编辑、格式变化等导致），
    此函数拼接相邻 run 文本进行匹配，然后从后向前修改以保持索引稳定。

    Args:
        runs: 段落的 run 对象列表
        target: 要查找的文本
        replacement: 替换为的文本

    Returns:
        替换次数
    """
    if not runs or not target:
        return 0

    # 拼接所有 run 文本，构建字符到 (run_index, char_index_in_run) 映射
    concat = ""
    char_map = []
    for ri, run in enumerate(runs):
        for ci, ch in enumerate(run.text):
            char_map.append((ri, ci))
            concat += ch

    # 查找所有出现位置
    occurrences = []
    pos = 0
    while True:
        idx = concat.find(target, pos)
        if idx == -1:
            break
        occurrences.append(idx)
        pos = idx + len(target)

    if not occurrences:
        return 0

    # 从后向前处理，避免修改影响后续索引
    for occ_idx in reversed(occurrences):
        end_idx = occ_idx + len(target) - 1
        run_start = char_map[occ_idx][0]
        run_end = char_map[end_idx][0]
        char_start = char_map[occ_idx][1]
        char_end = char_map[end_idx][1]

        if run_start == run_end:
            # 单 run 情况：直接替换
            run_text = runs[run_start].text
            runs[run_start].text = (
                run_text[:char_start] + replacement + run_text[char_end + 1:]
            )
        else:
            # 跨 run 情况
            runs[run_start].text = runs[run_start].text[:char_start] + replacement
            for ri in range(run_start + 1, run_end):
                runs[ri].text = ""
            runs[run_end].text = runs[run_end].text[char_end + 1:]

    return len(occurrences)


# =============================================================================
# 模板加载
# =============================================================================

def get_template(template_name: str) -> Dict[str, Any]:
    """
    加载命名模板。

    模板文件位于 skills/word-processing/assets/templates/<name>.json。
    """
    templates_dir = Path(__file__).parent.parent / "assets" / "templates"
    template_path = templates_dir / f"{template_name}.json"
    if not template_path.exists():
        available = [p.stem for p in sorted(templates_dir.glob("*.json"))] if templates_dir.exists() else []
        raise ValueError(
            f"未知模板 '{template_name}'。可用模板: {available}"
        )
    return json.loads(template_path.read_text(encoding="utf-8"))


def list_templates() -> List[Dict[str, str]]:
    """列出所有可用模板的名称和描述。"""
    templates_dir = Path(__file__).parent.parent / "assets" / "templates"
    if not templates_dir.exists():
        return []
    result = []
    for p in sorted(templates_dir.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            result.append({
                "name": data.get("name", p.stem),
                "display_name": data.get("display_name", p.stem),
                "description": data.get("description", ""),
            })
        except (json.JSONDecodeError, KeyError):
            result.append({"name": p.stem, "display_name": p.stem, "description": ""})
    return result


# =============================================================================
# Markdown → Word 转换
# =============================================================================

def markdown_to_doc(md_text: str, title: str = "", author: str = "",
                    template: Optional[Dict[str, Any]] = None) -> Document:
    """
    将 Markdown 文本转换为 python-docx Document。

    支持的 Markdown 元素：
    - 标题（# ~ ###### → Heading 1-6）
    - 段落（普通文本 + **粗体** / *斜体* / `行内代码`）
    - 表格（| col1 | col2 |）
    - 无序列表（- / * / +）
    - 有序列表（1. 2. 3.）
    - 分隔线（--- → 分页符）
    - 代码块（```）
    - 引用（>）
    - 链接（[text](url) → 显示文本）

    不支持：图片、HTML 标签、脚注。
    """
    doc = Document()

    # 设置文档属性
    if title:
        doc.core_properties.title = title
    if author:
        doc.core_properties.author = author

    # 加载模板（未指定时使用 default）
    if template is None:
        template = get_template("default")

    # 应用模板：正文样式
    body_spec = template.get("body", {})
    body_font = body_spec.get("font_name", "SimSun")
    body_ea = body_spec.get("east_asia_font", body_font)
    body_size = body_spec.get("font_size", 12)

    style = doc.styles['Normal']
    font = style.font
    font.name = body_font
    font.size = Pt(body_size)
    style.element.rPr.rFonts.set(qn("w:eastAsia"), body_ea)

    if "alignment" in body_spec:
        resolved = _resolve_alignment(body_spec["alignment"])
        if resolved is not None:
            style.paragraph_format.alignment = resolved
    if "line_spacing" in body_spec:
        style.paragraph_format.line_spacing = float(body_spec["line_spacing"])
    if "first_line_indent" in body_spec:
        style.paragraph_format.first_line_indent = Cm(float(body_spec["first_line_indent"]))
    if "space_before" in body_spec:
        style.paragraph_format.space_before = Pt(float(body_spec["space_before"]))
    if "space_after" in body_spec:
        style.paragraph_format.space_after = Pt(float(body_spec["space_after"]))

    # 应用模板：标题样式
    headings_spec = template.get("headings", {})
    for level in range(1, 7):
        style_name = f'Heading {level}'
        h_spec = headings_spec.get(str(level), {})
        if not h_spec:
            h_spec = {"font_name": "SimHei", "east_asia_font": "SimHei"}
        try:
            h_style = doc.styles[style_name]
            h_fn = h_spec.get("font_name", "SimHei")
            h_ea = h_spec.get("east_asia_font", h_fn)
            h_style.font.name = h_fn
            h_style.element.rPr.rFonts.set(qn("w:eastAsia"), h_ea)
            if "font_size" in h_spec:
                h_style.font.size = Pt(float(h_spec["font_size"]))
            if "color" in h_spec:
                rgb = _parse_color(h_spec["color"])
                if rgb:
                    h_style.font.color.rgb = rgb
            if "alignment" in h_spec:
                resolved = _resolve_alignment(h_spec["alignment"])
                if resolved is not None:
                    h_style.paragraph_format.alignment = resolved
            if "line_spacing" in h_spec:
                h_style.paragraph_format.line_spacing = float(h_spec["line_spacing"])
            if "space_before" in h_spec:
                h_style.paragraph_format.space_before = Pt(float(h_spec["space_before"]))
            if "space_after" in h_spec:
                h_style.paragraph_format.space_after = Pt(float(h_spec["space_after"]))
        except KeyError:
            pass

    # 应用模板：页面设置
    page_spec = template.get("page", {})
    if page_spec:
        size_name = page_spec.get("size", "A4")
        orientation = page_spec.get("orientation", "portrait")
        if size_name.upper() in PAGE_SIZES:
            w, h = PAGE_SIZES[size_name.upper()]
            for section in doc.sections:
                if orientation.lower() == "landscape":
                    section.page_width = Cm(h)
                    section.page_height = Cm(w)
                else:
                    section.page_width = Cm(w)
                    section.page_height = Cm(h)
        margins = page_spec.get("margins", {})
        if margins:
            for section in doc.sections:
                if "top" in margins:
                    section.top_margin = Cm(margins["top"])
                if "bottom" in margins:
                    section.bottom_margin = Cm(margins["bottom"])
                if "left" in margins:
                    section.left_margin = Cm(margins["left"])
                if "right" in margins:
                    section.right_margin = Cm(margins["right"])

    lines = md_text.split('\n')
    i = 0
    in_code_block = False
    code_buffer = []

    while i < len(lines):
        line = lines[i]

        # 代码块处理
        if line.strip().startswith('```'):
            if in_code_block:
                code_text = '\n'.join(code_buffer)
                _add_code_paragraph(doc, code_text)
                code_buffer = []
                in_code_block = False
            else:
                in_code_block = True
                code_buffer = []
            i += 1
            continue

        if in_code_block:
            code_buffer.append(line)
            i += 1
            continue

        # 空行跳过
        if not line.strip():
            i += 1
            continue

        # 分隔线 → 分页符
        if re.match(r'^-{3,}$|^\*{3,}$|^_{3,}$', line.strip()):
            doc.add_page_break()
            i += 1
            continue

        # 标题
        heading_match = re.match(r'^(#{1,6})\s+(.+)$', line)
        if heading_match:
            level = len(heading_match.group(1))
            text = heading_match.group(2).strip()
            _add_heading(doc, text, level, template)
            i += 1
            continue

        # 表格
        if '|' in line and i + 1 < len(lines):
            table_lines, consumed = _extract_table(lines, i)
            if table_lines:
                _add_table(doc, table_lines, template)
                i += consumed
                continue

        # 引用
        if line.strip().startswith('>'):
            quote_text = line.strip().lstrip('> ').strip()
            para = doc.add_paragraph(quote_text)
            para.style = doc.styles['Normal']
            for run in para.runs:
                run.font.italic = True
                run.font.color.rgb = RGBColor(0x66, 0x66, 0x66)
            i += 1
            continue

        # 无序列表
        ul_match = re.match(r'^(\s*)[-*+]\s+(.+)$', line)
        if ul_match:
            text = ul_match.group(2)
            _add_list_item(doc, text, ordered=False)
            i += 1
            continue

        # 有序列表
        ol_match = re.match(r'^(\s*)\d+[.)]\s+(.+)$', line)
        if ol_match:
            text = ol_match.group(2)
            _add_list_item(doc, text, ordered=True)
            i += 1
            continue

        # 普通段落
        _add_rich_paragraph(doc, line.strip())
        i += 1

    return doc


def _add_heading(doc: Document, text: str, level: int,
                 template: Optional[Dict[str, Any]] = None):
    """添加标题，应用模板指定的字体"""
    para = doc.add_heading(text, level=min(level, 6))
    headings_spec = (template or {}).get("headings", {})
    h_spec = headings_spec.get(str(level), {"font_name": "SimHei", "east_asia_font": "SimHei"})
    h_fn = h_spec.get("font_name", "SimHei")
    h_ea = h_spec.get("east_asia_font", h_fn)
    for run in para.runs:
        run.font.name = h_fn
        run._element.rPr.rFonts.set(qn("w:eastAsia"), h_ea)


def _add_rich_paragraph(doc: Document, text: str, style: str = "Normal"):
    """添加段落，支持 **粗体** *斜体* `代码` [链接](url)"""
    para = doc.add_paragraph(style=style)

    # 用正则分割出格式片段
    pattern = r'(\*\*(.+?)\*\*|\*(.+?)\*|`(.+?)`|\[([^\]]+)\]\(([^)]+)\))'
    last_end = 0

    for match in re.finditer(pattern, text):
        # 添加匹配前的普通文本
        if match.start() > last_end:
            plain = text[last_end:match.start()]
            if plain:
                para.add_run(plain)

        full = match.group(0)
        if full.startswith('**'):
            run = para.add_run(match.group(2))
            run.bold = True
        elif full.startswith('*') and not full.startswith('**'):
            run = para.add_run(match.group(3))
            run.italic = True
        elif full.startswith('`'):
            run = para.add_run(match.group(4))
            run.font.name = 'Consolas'
            run.font.size = Pt(10)
            run.font.color.rgb = RGBColor(0xC7, 0x25, 0x4E)
        elif full.startswith('['):
            link_text = match.group(5)
            run = para.add_run(link_text)
            run.font.color.rgb = RGBColor(0x05, 0x63, 0xC1)
            run.underline = True

        last_end = match.end()

    # 添加剩余文本
    if last_end < len(text):
        remaining = text[last_end:]
        if remaining:
            para.add_run(remaining)

    # 如果段落没有 run（空文本），添加空 run
    if not para.runs and text:
        para.add_run(text)


def _add_list_item(doc: Document, text: str, ordered: bool = False):
    """添加列表项"""
    style = "List Number" if ordered else "List Bullet"
    _add_rich_paragraph(doc, text, style=style)


def _add_code_paragraph(doc: Document, code: str):
    """添加代码块"""
    para = doc.add_paragraph()
    run = para.add_run(code)
    run.font.name = 'Consolas'
    run.font.size = Pt(9)
    # 设置背景色（通过底纹）
    from docx.oxml import OxmlElement
    shd = OxmlElement('w:shd')
    shd.set(qn('w:val'), 'clear')
    shd.set(qn('w:color'), 'auto')
    shd.set(qn('w:fill'), 'F5F5F5')
    run._element.rPr.append(shd)


def _extract_table(lines: list, start: int) -> tuple:
    """
    提取 Markdown 表格行。

    返回 (table_lines, consumed_count)，如果不是表格返回 ([], 0)。
    """
    table_lines = []
    i = start

    while i < len(lines):
        line = lines[i].strip()
        if '|' not in line:
            break
        # 分隔行（|---|---|）也要包含
        if re.match(r'^[\|\s\-:]+$', line):
            table_lines.append(line)
        else:
            table_lines.append(line)
        i += 1

    # 至少需要表头 + 分隔行 + 一行数据 = 3 行
    if len(table_lines) < 2:
        return [], 0

    # 检查第二行是否是分隔行
    if not re.match(r'^[\|\s\-:]+$', table_lines[1] if len(table_lines) > 1 else ''):
        return [], 0

    return table_lines, len(table_lines)


def _add_table(doc: Document, table_lines: list,
               template: Optional[Dict[str, Any]] = None):
    """将 Markdown 表格转为 Word 表格"""
    # 解析单元格
    rows_data = []
    for line in table_lines:
        if re.match(r'^[\|\s\-:]+$', line.strip()):
            continue  # 跳过分隔行
        cells = [c.strip() for c in line.strip().strip('|').split('|')]
        rows_data.append(cells)

    if not rows_data:
        return

    # 确定列数
    col_count = max(len(row) for row in rows_data)

    # 创建表格
    table = doc.add_table(rows=len(rows_data), cols=col_count)
    try:
        table.style = 'Table Grid'
    except KeyError:
        pass

    # 模板中的表格字体配置
    table_spec = (template or {}).get("table", {})
    header_fn = table_spec.get("header_font_name", "SimHei")
    header_ea = table_spec.get("header_east_asia_font", header_fn)

    # 填充数据
    for row_idx, row_data in enumerate(rows_data):
        for col_idx, cell_text in enumerate(row_data):
            if col_idx < col_count:
                cell = table.rows[row_idx].cells[col_idx]
                # 表头行加粗
                if row_idx == 0:
                    cell.text = ""
                    run = cell.paragraphs[0].add_run(cell_text)
                    run.bold = True
                    run.font.name = header_fn
                    run._element.rPr.rFonts.set(qn("w:eastAsia"), header_ea)
                else:
                    cell.text = cell_text

    doc.add_paragraph("")  # 表格后空行
