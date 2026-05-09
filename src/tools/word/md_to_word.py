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


def convert(md_text: str, template: Optional[str] = None,
            title: str = "", author: str = "") -> Document:
    """将 Markdown 文本转换为 python-docx Document。

    Args:
        md_text: Markdown 文本内容
        template: 可选 .docx 模板文件路径（传给 Pandoc --reference-doc），
                  传入非路径字符串时忽略
        title: 文档标题
        author: 文档作者
    """
    normalized = normalize_markdown(md_text)

    # 判断 template 是否为 .docx 文件路径
    reference_doc = None
    if template and template.endswith(".docx") and Path(template).exists():
        reference_doc = template

    doc = _pandoc_convert(normalized, reference_doc=reference_doc,
                          title=title, author=author)
    _ensure_cjk_fonts(doc)
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
            or re.match(r'^\|', stripped)
            or re.match(r'^[-*_]{3,}$', stripped)
        )

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


def _fix_tables(text: str) -> str:
    """修复表格：缺分隔行时自动补齐，列数不一致时补空单元格。"""
    lines = text.split('\n')
    result = []
    i = 0

    while i < len(lines):
        stripped = lines[i].strip()

        # 检测表格块开始（行首有 | 且不是分隔行）
        if stripped.startswith('|') and not re.match(r'^[\|\s\-:]+$', stripped):
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
        and re.match(r'^[\|\s\-:]+$', table_lines[1].strip())
    )

    if not has_separator:
        # 补齐分隔行
        separator = '|' + '|'.join([' --- '] * col_count) + '|'
        table_lines.insert(1, separator)

    # 统一所有数据行的列数
    fixed = [table_lines[0]]  # header
    if has_separator:
        fixed.append(table_lines[1])
    else:
        fixed.append('|' + '|'.join([' --- '] * col_count) + '|')

    data_start = 1 if has_separator else 1  # 数据从第2行开始（已插入分隔行）
    for row in table_lines[data_start + (0 if has_separator else 0):]:
        if re.match(r'^[\|\s\-:]+$', row.strip()):
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
