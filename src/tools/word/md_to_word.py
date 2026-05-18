"""
Markdown 转 Word 文档（基于 Pandoc 引擎）

将 Markdown 文本通过 Pandoc 转换为 python-docx Document。
内置 LLM 输出规范化处理，自动修复常见 Markdown 格式问题。
"""

import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from loguru import logger


def _find_pandoc() -> str:
    """查找 pandoc 可执行文件路径。

    依次检查：PATH 环境变量 → Windows 常见安装路径。
    找不到时返回 "pandoc"（让 subprocess 自行报错）。
    """
    import shutil
    pandoc = shutil.which("pandoc")
    if pandoc:
        return pandoc

    # Windows 常见安装路径
    windows_paths = [
        Path.home() / "AppData" / "Local" / "Pandoc" / "pandoc.exe",
        Path("C:/Program Files/Pandoc/pandoc.exe"),
        Path("C:/Program Files (x86)/Pandoc/pandoc.exe"),
    ]
    for p in windows_paths:
        if p.exists():
            return str(p)

    return "pandoc"


# ============== 公共 API ==============

# 默认 Pandoc 参考文档路径
_DEFAULT_REFERENCE_DOC = Path(__file__).parent / "assets" / "reference" / "default.docx"


def convert(md_text: str, template: Optional[str] = None,
            title: str = "", author: str = "") -> Document:
    """将 Markdown 文本转换为 python-docx Document。

    Args:
        md_text: Markdown 文本内容
        template: 可选 .docx 模板文件路径（传给 Pandoc --reference-doc），
                  传入非路径字符串时使用内置默认参考文档
        title: 文档标题
        author: 文档作者
    """
    normalized = normalize_markdown(md_text)

    # 确定 reference-doc：优先用指定的，否则用内置默认
    reference_doc = None
    if template and template.endswith(".docx") and Path(template).exists():
        reference_doc = template
    elif _DEFAULT_REFERENCE_DOC.exists():
        reference_doc = str(_DEFAULT_REFERENCE_DOC)

    doc = _pandoc_convert(normalized, reference_doc=reference_doc,
                          title=title, author=author)
    _ensure_cjk_fonts(doc)
    # 从 reference doc 模板复制表格内联格式（边框、底色、字体）
    if reference_doc:
        _apply_template_table_style(doc, reference_doc)
    return doc


def convert_file(md_path: str, template: Optional[str] = None,
                 title: str = "", author: str = "") -> Document:
    """从 Markdown 文件创建 Word Document"""
    md_text = Path(md_path).read_text(encoding="utf-8")
    return convert(md_text, template=template, title=title, author=author)


def save_as(doc: Document, file_name: Optional[str] = None,
            output_dir: Optional[str] = None) -> Dict[str, Any]:
    """保存 Document 到文件"""
    from src.tools.word.word_lib import WordFileHandler
    return WordFileHandler.save_temp(doc, file_name=file_name, output_dir=output_dir)


# ============== Markdown 规范化 ==============


def normalize_markdown(md_text: str) -> str:
    """规范化 LLM 生成的 Markdown 文本，修复常见格式问题。"""
    text = md_text
    text = _fix_literal_newlines(text)
    text = _ensure_block_spacing(text)
    text = _fix_tables(text)
    text = _close_code_fences(text)
    text = _preprocess_for_pandoc(text)
    return text


def _fix_literal_newlines(text: str) -> str:
    """将字面量 \\n（反斜杠+n）替换为真正的换行符，跳过代码块内部。"""
    parts = re.split(r'(```.*?```)', text, flags=re.DOTALL)
    for i in range(0, len(parts), 2):
        parts[i] = parts[i].replace('\\n', '\n')
    return ''.join(parts)


def _ensure_block_spacing(text: str) -> str:
    """确保标题、代码块、表格、分隔线前后有空行。"""
    lines = text.split('\n')
    result = []
    prev_is_blank = True  # 文件开头视为已有空行

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        is_block_start = bool(
            re.match(r'^#{1,6}\s', stripped)
            or re.match(r'^```', stripped)
            or re.match(r'^[-*_]{3,}$', stripped)
        )
        # 表格行：仅在不紧跟前一个表格行时视为块开始
        is_table_row = stripped.startswith('|')
        if is_table_row and not (result and result[-1].strip().startswith('|')):
            is_block_start = True

        # 块元素前需要空行
        if is_block_start and not prev_is_blank:
            result.append('')

        result.append(line)
        prev_is_blank = (stripped == '')

        # 标题后、代码块开闭后、分隔线后也需要空行
        if (re.match(r'^#{1,6}\s', stripped)
                or re.match(r'^```', stripped)
                or re.match(r'^[-*_]{3,}$', stripped)):
            if i + 1 < len(lines) and lines[i + 1].strip():
                result.append('')
                prev_is_blank = True

        i += 1

    return '\n'.join(result)


# 匹配 Markdown 表格分隔行，支持 ASCII 连字符(-)和 Unicode 破折号(—–―)
_SEPARATOR_RE = re.compile(r'^[\|\s\-:—–―]+$')


def _fix_tables(text: str) -> str:
    """修复表格：缺分隔行时自动补齐，列数不一致时补空单元格。"""
    lines = text.split('\n')
    result = []
    i = 0

    while i < len(lines):
        stripped = lines[i].strip()

        # 检测表格块开始（行首有 | 且不是分隔行）
        if stripped.startswith('|') and not _SEPARATOR_RE.match(stripped):
            table_lines = [stripped]
            j = i + 1

            # 收集连续的表格行
            while j < len(lines) and lines[j].strip().startswith('|'):
                table_lines.append(lines[j].strip())
                j += 1

            # 处理表格
            table_lines = _repair_table(table_lines)
            result.extend(table_lines)
            i = j
        else:
            result.append(lines[i])
            i += 1

    return '\n'.join(result)


def _repair_table(table_lines: list) -> list:
    """修复单个表格块。"""
    if not table_lines:
        return table_lines

    header = table_lines[0]
    header_cells = [c.strip() for c in header.strip('|').split('|')]
    col_count = len(header_cells)

    # 检查第二行是否为分隔行
    has_separator = (
        len(table_lines) > 1
        and _SEPARATOR_RE.match(table_lines[1].strip())
    )

    if not has_separator:
        # 补齐分隔行
        separator = '|' + '|'.join([' --- '] * col_count) + '|'
        table_lines.insert(1, separator)

    # 统一所有数据行的列数
    fixed = [table_lines[0]]  # header
    # 始终使用标准 ASCII 分隔行，确保 Pandoc 能正确解析
    fixed.append('|' + '|'.join([' --- '] * col_count) + '|')

    data_start = 1 if has_separator else 1  # 数据从第2行开始（已插入分隔行）
    for row in table_lines[data_start + (0 if has_separator else 0):]:
        if _SEPARATOR_RE.match(row.strip()):
            continue  # 跳过分隔行
        cells = [c.strip() for c in row.strip('|').split('|')]
        # 补齐或截断
        if len(cells) < col_count:
            cells.extend([''] * (col_count - len(cells)))
        elif len(cells) > col_count:
            cells = cells[:col_count]
        fixed.append('|' + '|'.join(cells) + '|')

    return fixed


def _close_code_fences(text: str) -> str:
    """自动闭合未关闭的代码块。"""
    lines = text.split('\n')
    fence_count = 0
    for line in lines:
        if re.match(r'^```', line.strip()):
            fence_count += 1

    # 开闭数量不等时补齐
    if fence_count % 2 == 1:
        lines.append('```')

    return '\n'.join(lines)


def _preprocess_for_pandoc(text: str) -> str:
    """Pandoc 专用的预处理：分隔线转分页、清理控制字符。"""
    lines = text.split('\n')
    result = []

    for line in lines:
        stripped = line.strip()
        # Markdown 分隔线 → Pandoc 分页符
        if re.match(r'^[-*_]{3,}$', stripped) and not stripped.startswith('|'):
            result.append('\\newpage')
        else:
            result.append(line)

    text = '\n'.join(result)

    # 移除控制字符（保留换行和制表符）
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)

    return text


# ============== Pandoc 转换 ==============


def _pandoc_convert(md_text: str, reference_doc: Optional[str] = None,
                    title: str = "", author: str = "") -> Document:
    """通过 Pandoc 将 Markdown 转换为 python-docx Document。"""
    with tempfile.TemporaryDirectory(prefix="pandoc_") as tmpdir:
        md_path = Path(tmpdir) / "input.md"
        docx_path = Path(tmpdir) / "output.docx"

        md_path.write_text(md_text, encoding="utf-8")

        cmd = [
            _find_pandoc(),
            "-f", "markdown+pipe_tables+raw_html+autolink_bare_uris",
            "-t", "docx",
            "--wrap=none",
        ]

        if reference_doc and Path(reference_doc).exists():
            cmd.extend(["--reference-doc", str(reference_doc)])

        cmd.extend(["-o", str(docx_path), str(md_path)])

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError("Pandoc conversion timed out (30s)")

        if result.returncode != 0:
            raise RuntimeError(
                f"Pandoc conversion failed (exit {result.returncode}): "
                f"{result.stderr[:500]}"
            )

        if not docx_path.exists():
            raise RuntimeError("Pandoc produced no output file")

        doc = Document(str(docx_path))

    if title:
        doc.core_properties.title = title
    if author:
        doc.core_properties.author = author

    return doc


# ============== CJK 字体后处理 ==============


def _ensure_cjk_fonts(doc: Document, cjk_font: str = "SimSun") -> None:
    """确保文档中所有 run 都设置了 eastAsia 字体。

    Pandoc 不设置 w:rFonts w:eastAsia 属性，
    导致中文字符可能回退到非预期字体。
    """
    def _fix_runs(paragraphs):
        for para in paragraphs:
            for run in para.runs:
                rPr = run._element.get_or_add_rPr()
                rFonts = rPr.find(qn('w:rFonts'))
                if rFonts is None:
                    rFonts = OxmlElement('w:rFonts')
                    rPr.insert(0, rFonts)
                if rFonts.get(qn('w:eastAsia')) is None:
                    rFonts.set(qn('w:eastAsia'), cjk_font)

    _fix_runs(doc.paragraphs)

    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                _fix_runs(cell.paragraphs)


def _apply_template_table_style(doc: Document, reference_doc_path: str) -> None:
    """从 reference doc 模板中提取表格格式，应用到输出文档的所有表格。

    提取的内容包括：表格边框、表头底色/字体、数据行格式。
    Pandoc --reference-doc 只复制样式定义，不复制内联的边框和单元格格式，
    因此需要此后处理步骤。
    """
    from copy import deepcopy
    from lxml import etree

    if not Path(reference_doc_path).exists():
        logger.warning(f"[TableStyle] Reference doc not found: {reference_doc_path}")
        return

    try:
        ref_doc = Document(reference_doc_path)
    except Exception as e:
        logger.warning(f"[TableStyle] Failed to load reference doc: {e}")
        return

    # 从模板中找第一个有多行数据的表格作为格式模板
    ref_table = None
    for t in ref_doc.tables:
        if len(t.rows) >= 2:
            ref_table = t
            break

    if ref_table is None:
        logger.warning("[TableStyle] No table with >= 2 rows found in reference doc")
        return

    ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}

    # 提取模板表格级别的格式（边框、对齐等）
    ref_tblPr = ref_table._tbl.tblPr
    ref_borders = ref_tblPr.find('w:tblBorders', ns) if ref_tblPr is not None else None

    # 提取表头行格式
    ref_header_row = ref_table.rows[0]
    ref_header_cells = ref_header_row.cells

    # 提取数据行格式（用第2行，因为第1行是表头）
    ref_data_row = ref_table.rows[1] if len(ref_table.rows) > 1 else None

    for table in doc.tables:
        tbl = table._tbl
        tblPr = tbl.tblPr
        if tblPr is None:
            tblPr = OxmlElement('w:tblPr')
            tbl.insert(0, tblPr)

        # 应用表格级边框
        if ref_borders is not None:
            existing = tblPr.find(qn('w:tblBorders'))
            if existing is not None:
                tblPr.remove(existing)
            tblPr.append(deepcopy(ref_borders))

        # 逐行应用格式
        for ri, row in enumerate(table.rows):
            is_header = (ri == 0)
            ref_cells = ref_header_cells if is_header else (ref_data_row.cells if ref_data_row else None)
            if ref_cells is None:
                continue

            for ci, cell in enumerate(row.cells):
                # 取模板中对应列的单元格，列不够时取最后一列
                ref_ci = min(ci, len(ref_cells) - 1)
                ref_tcPr = ref_cells[ref_ci]._tc.tcPr
                if ref_tcPr is None:
                    continue

                # 复制单元格属性（底色、宽度等），保留原有内容
                new_tcPr = deepcopy(ref_tcPr)
                old_tcPr = cell._tc.tcPr
                if old_tcPr is not None:
                    cell._tc.remove(old_tcPr)
                # tcPr 必须在第一个段落之前
                cell._tc.insert(0, new_tcPr)

                # 表头行：应用字体样式
                if is_header:
                    ref_paras = ref_cells[ref_ci].paragraphs
                    if ref_paras:
                        ref_para_format = ref_paras[0].paragraph_format
                        for para in cell.paragraphs:
                            if ref_para_format.alignment is not None:
                                para.paragraph_format.alignment = ref_para_format.alignment
                        # 从模板表头的 run 中提取字体属性
                        ref_runs = ref_paras[0].runs
                        if ref_runs:
                            ref_run = ref_runs[0]
                            for para in cell.paragraphs:
                                for run in para.runs:
                                    if ref_run.font.name:
                                        run.font.name = ref_run.font.name
                                    if ref_run.font.size:
                                        run.font.size = ref_run.font.size
                                    if ref_run.font.bold:
                                        run.font.bold = ref_run.font.bold
                                    if ref_run.font.color and ref_run.font.color.rgb:
                                        run.font.color.rgb = ref_run.font.color.rgb

    logger.info(f"[TableStyle] Applied template table style from {reference_doc_path}")
