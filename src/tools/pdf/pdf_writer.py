"""
PDF 生成模块

Markdown 使用 fpdf2 生成 PDF。
HTML 优先使用 Playwright print-to-pdf，失败时回退 fpdf2。
"""

import os
import re
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from src.tools.pdf.pdf_lib import PdfFileHandler

# 蓝色主题色值
_BLUE_PRIMARY = (41, 98, 168)       # #2962A8 主蓝色
_BLUE_DARK = (25, 60, 110)          # #193C6E 深蓝（标题）
_BLUE_HEADER_BG = (41, 98, 168)     # 表头背景
_BLUE_HEADER_TEXT = (255, 255, 255) # 表头文字白色
_STRIPE_BG = (235, 242, 250)        # 斑马纹浅蓝
_BORDER_COLOR = (180, 200, 220)     # 边框浅蓝灰


class _HtmlTableParser(HTMLParser):
    """从 HTML 中提取 <table> 数据，返回非表格 HTML 片段和表格数据列表。"""

    def __init__(self):
        super().__init__()
        self.fragments: List[Dict] = []
        self._current_table: Optional[List[List[str]]] = None
        self._current_row: Optional[List[str]] = None
        self._current_cell: Optional[str] = None
        self._non_table_html: List[str] = []
        self._in_table = False
        self._in_thead = False

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._flush_non_table_html()
            self._in_table = True
            self._current_table = []
            return
        if tag == "thead":
            self._in_thead = True
            return
        if tag == "tbody":
            return
        if tag in ("tr",):
            self._current_row = []
            return
        if tag in ("td", "th"):
            self._current_cell = ""
            return
        # 非 table 内容中的标签
        if not self._in_table:
            attr_str = ""
            for k, v in attrs:
                attr_str += f' {k}="{v}"'
            self._non_table_html.append(f"<{tag}{attr_str}>")

    def handle_endtag(self, tag):
        if tag == "table":
            self._in_table = False
            self._in_thead = False
            if self._current_table:
                self.fragments.append({"type": "table", "data": self._current_table})
            self._current_table = None
            return
        if tag == "thead":
            self._in_thead = False
            return
        if tag == "tbody":
            return
        if tag == "tr":
            if self._current_row is not None:
                if self._current_table is not None:
                    self._current_table.append(self._current_row)
            self._current_row = None
            return
        if tag in ("td", "th"):
            if self._current_cell is not None and self._current_row is not None:
                self._current_row.append(self._current_cell.strip())
            self._current_cell = None
            return
        if not self._in_table:
            self._non_table_html.append(f"</{tag}>")

    def handle_data(self, data):
        if self._in_table:
            if self._current_cell is not None:
                self._current_cell += data
        else:
            self._non_table_html.append(data)

    def get_result(self) -> List[Dict]:
        """返回 [{type: "html", content: str}, {type: "table", data: [[...]]}, ...]"""
        self._flush_non_table_html()
        return list(self.fragments)

    def _flush_non_table_html(self) -> None:
        if not self._non_table_html:
            return
        html_chunk = "".join(self._non_table_html).strip()
        if html_chunk:
            self.fragments.append({"type": "html", "content": html_chunk})
        self._non_table_html = []


def _split_html_by_tables(html: str) -> List[Dict]:
    """将 HTML 拆分为交替的 HTML 片段和表格数据。"""
    parser = _HtmlTableParser()
    parser.feed(html)
    return parser.get_result()


def _strip_html_tags(text: str) -> str:
    """移除简单的 HTML 标签，保留文本。"""
    text = re.sub(r'<br\s*/?>', '\n', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'&amp;', '&', text)
    text = re.sub(r'&lt;', '<', text)
    text = re.sub(r'&gt;', '>', text)
    return text.strip()


def _preprocess_markdown(md_text: str) -> str:
    """规范化 LLM 生成的 Markdown 文本，修复常见格式问题。"""
    text = md_text
    text = _fix_literal_newlines(text)
    text = _ensure_block_spacing(text)
    text = _fix_tables(text)
    text = _close_code_fences(text)
    text = _clean_control_chars(text)
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
    prev_is_blank = True

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        is_block_start = bool(
            re.match(r'^#{1,6}\s', stripped)
            or re.match(r'^```', stripped)
            or re.match(r'^[-*_]{3,}$', stripped)
        )
        is_table_row = stripped.startswith('|')
        if is_table_row and not (result and result[-1].strip().startswith('|')):
            is_block_start = True

        if is_block_start and not prev_is_blank:
            result.append('')

        result.append(line)
        prev_is_blank = (stripped == '')

        if (re.match(r'^#{1,6}\s', stripped)
                or re.match(r'^```', stripped)
                or re.match(r'^[-*_]{3,}$', stripped)):
            if i + 1 < len(lines) and lines[i + 1].strip():
                result.append('')
                prev_is_blank = True

        i += 1

    return '\n'.join(result)


_SEPARATOR_RE = re.compile(r'^[\|\s\-:—–―]+$')


def _fix_tables(text: str) -> str:
    """修复表格：缺分隔行时自动补齐，列数不一致时补空单元格。"""
    lines = text.split('\n')
    result = []
    i = 0

    while i < len(lines):
        stripped = lines[i].strip()

        if stripped.startswith('|') and not _SEPARATOR_RE.match(stripped):
            table_lines = [stripped]
            j = i + 1

            while j < len(lines) and lines[j].strip().startswith('|'):
                table_lines.append(lines[j].strip())
                j += 1

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

    has_separator = (
        len(table_lines) > 1
        and _SEPARATOR_RE.match(table_lines[1].strip())
    )

    fixed = [table_lines[0]]
    fixed.append('|' + '|'.join([' --- '] * col_count) + '|')

    data_start = 2 if has_separator else 1
    for row in table_lines[data_start:]:
        if _SEPARATOR_RE.match(row.strip()):
            continue
        cells = [c.strip() for c in row.strip('|').split('|')]
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

    if fence_count % 2 == 1:
        lines.append('```')

    return '\n'.join(lines)


def _clean_control_chars(text: str) -> str:
    """移除控制字符（保留换行和制表符）。"""
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)


def _md_to_html(md_text: str) -> str:
    """Markdown → HTML，使用 markdown 库。"""
    try:
        import markdown

        extensions = [
            "tables",
            "fenced_code",
            "toc",
            "smarty",
            "sane_lists",
        ]
        return markdown.markdown(
            md_text,
            extensions=extensions,
        )
    except ImportError:
        return _basic_markdown_to_html(md_text)


def _basic_markdown_to_html(md_text: str) -> str:
    """markdown 包缺失时的轻量兜底转换，覆盖标题和普通段落。"""
    import html

    blocks = []
    for raw in md_text.split("\n"):
        line = raw.strip()
        if not line:
            continue
        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading:
            level = len(heading.group(1))
            blocks.append(f"<h{level}>{html.escape(heading.group(2))}</h{level}>")
        else:
            blocks.append(f"<p>{html.escape(line)}</p>")
    return "\n".join(blocks)


def _find_chinese_font() -> Optional[str]:
    """查找系统中可用的中文字体文件路径。"""
    import shutil

    # Windows 常见中文字体路径
    if os.name == 'nt':
        win_dir = os.environ.get('WINDIR', r'C:\Windows')
        candidates = [
            os.path.join(win_dir, 'Fonts', 'msyh.ttc'),      # 微软雅黑
            os.path.join(win_dir, 'Fonts', 'msyhbd.ttc'),     # 微软雅黑粗体
            os.path.join(win_dir, 'Fonts', 'simhei.ttf'),     # 黑体
            os.path.join(win_dir, 'Fonts', 'simsun.ttc'),     # 宋体
            os.path.join(win_dir, 'Fonts', 'simfang.ttf'),    # 仿宋
        ]
        for path in candidates:
            if os.path.exists(path):
                return path

    # Linux 常见路径
    linux_candidates = [
        '/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc',
        '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
        '/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc',
        '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc',
        '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',
        '/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf',
    ]
    for path in linux_candidates:
        if os.path.exists(path):
            return path

    # macOS
    mac_candidates = [
        '/System/Library/Fonts/PingFang.ttc',
        '/System/Library/Fonts/STHeiti Light.ttc',
        '/Library/Fonts/Arial Unicode.ttf',
    ]
    for path in mac_candidates:
        if os.path.exists(path):
            return path

    # 尝试 fc-list（Linux/macOS）
    try:
        result = shutil.which('fc-list')
        if result:
            import subprocess
            output = subprocess.check_output(
                ['fc-list', ':lang=zh', 'file'],
                stderr=subprocess.DEVNULL
            ).decode('utf-8', errors='ignore')
            for line in output.strip().split('\n'):
                line = line.strip().rstrip(':')
                if line and os.path.exists(line):
                    return line
    except Exception:
        pass

    return None


def _init_pdf(title: str = "") -> tuple:
    """初始化 FPDF 实例并注册中文字体，返回 (pdf, font_name)。"""
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=20)

    font_path = _find_chinese_font()
    font_name = 'Helvetica'
    if font_path:
        pdf.add_font('zh', '', font_path, uni=True)
        # 尝试注册粗体
        bold_variants = [
            font_path.replace('.ttc', 'bd.ttc').replace('.ttf', 'bd.ttf'),
            font_path.replace('-Regular', '-Bold'),
            font_path.replace('msyh.ttc', 'msyhbd.ttc'),
            font_path.replace('simsun.ttc', 'simhei.ttf'),
        ]
        bold_registered = False
        for bv in bold_variants:
            if os.path.exists(bv):
                pdf.add_font('zh', 'B', bv, uni=True)
                bold_registered = True
                break
        if not bold_registered:
            # 使用同字体模拟粗体
            pdf.add_font('zh', 'B', font_path, uni=True)
        font_name = 'zh'

    pdf.set_font(font_name, size=11)
    pdf.add_page()

    # 文档标题 — 蓝色主题
    if title:
        pdf.set_font(font_name, 'B', size=18)
        pdf.set_text_color(*_BLUE_DARK)
        pdf.cell(0, 14, title, new_x="LMARGIN", new_y="NEXT", align='C')
        # 蓝色分割线
        pdf.set_draw_color(*_BLUE_PRIMARY)
        pdf.set_line_width(0.8)
        pdf.line(pdf.l_margin, pdf.get_y() + 2, pdf.w - pdf.r_margin, pdf.get_y() + 2)
        pdf.ln(8)
        pdf.set_text_color(0, 0, 0)
        pdf.set_font(font_name, size=11)

    return pdf, font_name


class _OddRowFillMode:
    """自定义斑马纹：只填充奇数行（跳过表头行）。"""
    @staticmethod
    def should_fill_cell(i, j):
        return i > 0 and i % 2 == 0


def _render_table_native(pdf, font_name: str, table_data: List[List[str]]) -> None:
    """用 fpdf2 原生 table API 渲染带样式的表格。"""
    from fpdf.fonts import FontFace

    if not table_data:
        return

    # 清理 HTML 标签
    clean_data = []
    for row in table_data:
        clean_data.append([_strip_html_tags(cell) for cell in row])

    # 表头样式：蓝色背景 + 白色粗体文字
    headings_style = FontFace(
        emphasis='B',
        color=_BLUE_HEADER_TEXT,
        fill_color=_BLUE_HEADER_BG,
    )

    pdf.set_font(font_name, size=10)
    pdf.set_draw_color(*_BORDER_COLOR)
    pdf.set_line_width(0.3)

    num_cols = len(clean_data[0]) if clean_data else 0
    if num_cols == 0:
        return

    with pdf.table(
        headings_style=headings_style,
        cell_fill_color=_STRIPE_BG,
        cell_fill_mode=_OddRowFillMode(),
        borders_layout="HORIZONTAL_LINES",
        text_align="LEFT",
        padding=(4, 4, 3, 4),
        line_height=1.4 * pdf.font_size,
    ) as table:
        for i, row_data in enumerate(clean_data):
            row = table.row()
            for j, cell_text in enumerate(row_data):
                if j >= num_cols:
                    break
                row.cell(cell_text)

    pdf.ln(4)


def _render_html_content(pdf, font_name: str, html: str) -> None:
    """用 fpdf2 原生 API + write_html 渲染非表格 HTML 内容，蓝色主题。

    策略：提取 h1~h3 和 p 标签用原生 API 渲染（保证蓝色主题），
    其余内容（列表、代码块等）用 write_html 兜底。
    """
    # 按 block 级标签拆分 HTML
    blocks = re.split(r'(<(?:h[1-6]|p)\b[^>]*>.*?</(?:h[1-6]|p)>)', html, flags=re.DOTALL)

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        # 检测 h1~h6 标签
        h_match = re.match(r'<(h[1-6])\b[^>]*>(.*?)</\1>', block, re.DOTALL)
        if h_match:
            level = int(h_match.group(1)[1])
            text = _strip_html_tags(h_match.group(2))
            if not text:
                continue
            sizes = {1: 18, 2: 14, 3: 12, 4: 11, 5: 11, 6: 10}
            pdf.set_font(font_name, 'B', size=sizes.get(level, 11))
            pdf.set_text_color(*_BLUE_PRIMARY)
            pdf.multi_cell(0, sizes.get(level, 11) * 0.7, text, new_x="LMARGIN", new_y="NEXT")
            # h2 加底部蓝色线条
            if level == 2:
                pdf.set_draw_color(*_BLUE_PRIMARY)
                pdf.set_line_width(0.5)
                pdf.line(pdf.l_margin, pdf.get_y() + 1, pdf.w - pdf.r_margin, pdf.get_y() + 1)
                pdf.ln(3)
            else:
                pdf.ln(2)
            pdf.set_text_color(0, 0, 0)
            pdf.set_font(font_name, size=11)
            continue

        # 检测 p 标签
        p_match = re.match(r'<p\b[^>]*>(.*?)</p>', block, re.DOTALL)
        if p_match:
            inner = p_match.group(1)
            text = _strip_html_tags(inner)
            if not text:
                continue
            # 检查是否包含 <strong>（加粗关键词）
            if '<strong>' in inner:
                _render_rich_paragraph(pdf, font_name, inner)
            else:
                pdf.set_font(font_name, size=11)
                pdf.set_text_color(0, 0, 0)
                pdf.multi_cell(0, 6.5, text, new_x="LMARGIN", new_y="NEXT")
                pdf.ln(2)
            continue

        # 其余内容（ul/ol/li/pre/blockquote 等）用 write_html 兜底
        pdf.set_font(font_name, size=11)
        pdf.set_text_color(0, 0, 0)
        pdf.write_html(block, table_line_separators=False)
        pdf.ln(2)


def _render_rich_paragraph(pdf, font_name: str, html_inner: str) -> None:
    """渲染包含 <strong> 标签的段落，加粗部分用蓝色。"""
    # 按 <strong>...</strong> 拆分
    parts = re.split(r'(<strong>.*?</strong>)', html_inner, flags=re.DOTALL)
    pdf.set_text_color(0, 0, 0)
    pdf.set_font(font_name, size=11)

    for part in parts:
        strong_match = re.match(r'<strong>(.*?)</strong>', part, re.DOTALL)
        if strong_match:
            text = _strip_html_tags(strong_match.group(1))
            pdf.set_font(font_name, 'B', size=11)
            pdf.set_text_color(*_BLUE_DARK)
            pdf.write(6.5, text)
            pdf.set_font(font_name, size=11)
            pdf.set_text_color(0, 0, 0)
        else:
            text = _strip_html_tags(part)
            if text:
                pdf.write(6.5, text)

    pdf.ln(8)


def _create_pdf_with_html(html_body: str, output_path: str, title: str = "") -> None:
    """使用 fpdf2 从 HTML 内容生成 PDF，表格用原生 API 渲染。"""
    pdf, font_name = _init_pdf(title)

    # 将 HTML 拆分为：普通 HTML 片段 + 表格数据
    parts = _split_html_by_tables(html_body)

    for part in parts:
        if part["type"] == "html":
            _render_html_content(pdf, font_name, part["content"])
        elif part["type"] == "table":
            _render_table_native(pdf, font_name, part["data"])

    pdf.output(output_path)


def md_to_pdf(md_text: str, output_name: Optional[str] = None,
              css: Optional[str] = None, title: str = "") -> Dict[str, Any]:
    """Markdown → PDF，通过 markdown + fpdf2。

    Args:
        md_text: Markdown 文本内容
        output_name: 输出文件名
        css: 自定义 CSS 文件路径（保留参数兼容接口）
        title: 文档标题

    Returns:
        {"success": True, "file_path": str, "file_size": int}
    """
    try:
        # 预处理 Markdown
        cleaned_md = _preprocess_markdown(md_text)

        # Markdown → HTML
        body_html = _md_to_html(cleaned_md)

        # fpdf2 → PDF
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "output.pdf")
            _create_pdf_with_html(body_html, output_path, title=title)

            if not Path(output_path).exists():
                return {"success": False, "error": "PDF 生成失败：fpdf2 未输出文件"}

            save_result = PdfFileHandler.save_temp(
                source_path=output_path,
                file_name=output_name or "document.pdf",
            )
            save_result["success"] = True
            if css:
                save_result["warnings"] = ["当前 fpdf2 生成路径不支持自定义 CSS，已忽略 css 参数"]
            return save_result

    except Exception as e:
        logger.error(f"[PdfWriter] md_to_pdf 失败: {e}", exc_info=True)
        return {"success": False, "error": f"生成PDF失败: {e}"}


def html_to_pdf(html_text: str, output_name: Optional[str] = None,
                css: Optional[str] = None, engine: str = "auto") -> Dict[str, Any]:
    """HTML → PDF。默认使用 Playwright print-to-pdf，失败时回退 fpdf2。"""
    engine = (engine or "auto").lower().strip()
    if engine not in ("auto", "playwright", "fpdf2", "fpdf"):
        return {"success": False, "error": f"不支持的 HTML 转 PDF 引擎: {engine}"}

    if engine in ("playwright", "auto"):
        result = _html_to_pdf_via_playwright(html_text, output_name=output_name, css=css)
        if result.get("success") or engine == "playwright":
            return result

        fallback = _html_to_pdf_via_fpdf2(html_text, output_name=output_name, css=css)
        warnings = list(fallback.get("warnings", []))
        warnings.append(f"Playwright print-to-pdf 不可用，已回退 fpdf2: {result.get('error', '')}")
        fallback["warnings"] = warnings
        return fallback

    return _html_to_pdf_via_fpdf2(html_text, output_name=output_name, css=css)


def _html_to_pdf_via_playwright(html_text: str, output_name: Optional[str] = None,
                                css: Optional[str] = None) -> Dict[str, Any]:
    """通过 Playwright Chromium print-to-pdf 高保真生成 PDF。"""
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "output.pdf")
            html_doc = _prepare_print_html(html_text, css=css)

            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                try:
                    page = browser.new_page()
                    page.set_content(html_doc, wait_until="networkidle")
                    page.pdf(
                        path=output_path,
                        format="A4",
                        print_background=True,
                        prefer_css_page_size=True,
                        margin={"top": "16mm", "right": "14mm", "bottom": "16mm", "left": "14mm"},
                    )
                finally:
                    browser.close()

            if not Path(output_path).exists():
                return {"success": False, "error": "PDF 生成失败：Playwright 未输出文件"}

            save_result = PdfFileHandler.save_temp(
                source_path=output_path,
                file_name=output_name or "document.pdf",
            )
            save_result["success"] = True
            save_result["engine"] = "playwright"
            return save_result

    except Exception as e:
        logger.warning(f"[PdfWriter] Playwright HTML转PDF失败: {e}")
        return {"success": False, "error": f"Playwright HTML转PDF失败: {e}"}


def _prepare_print_html(html_text: str, css: Optional[str] = None) -> str:
    """准备用于浏览器打印的完整 HTML。"""
    css_text = _resolve_print_css(css)

    if re.search(r"<html[\s>]", html_text, flags=re.IGNORECASE):
        if not css_text:
            return html_text
        if "</head>" in html_text.lower():
            return re.sub(
                r"</head>",
                f"<style>{css_text}</style></head>",
                html_text,
                count=1,
                flags=re.IGNORECASE,
            )
        return re.sub(
            r"<html([^>]*)>",
            f"<html\\1><head><style>{css_text}</style></head>",
            html_text,
            count=1,
            flags=re.IGNORECASE,
        )

    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
@page {{ size: A4; margin: 16mm 14mm; }}
body {{
  font-family: "Microsoft YaHei", "Noto Sans CJK SC", Arial, sans-serif;
  font-size: 12px;
  line-height: 1.55;
}}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid #d0d7de; padding: 6px 8px; }}
thead {{ display: table-header-group; }}
tr {{ break-inside: avoid; }}
{css_text}
</style>
</head>
<body>
{html_text}
</body>
</html>"""


def _resolve_print_css(css: Optional[str]) -> str:
    """解析 Playwright 打印 CSS：支持 CSS 文件路径和内联 CSS。"""
    if not css:
        return ""

    css_value = str(css)
    try:
        css_path = Path(css_value)
        if css_path.exists() and css_path.is_file():
            return css_path.read_text(encoding="utf-8")
    except OSError:
        pass

    if any(marker in css_value for marker in ("{", "}", ";", "\n")):
        return css_value

    return ""


def _html_to_pdf_via_fpdf2(html_text: str, output_name: Optional[str] = None,
                           css: Optional[str] = None) -> Dict[str, Any]:
    """HTML → PDF，使用 fpdf2 简化渲染。"""
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "output.pdf")
            _create_pdf_with_html(html_text, output_path)

            if not Path(output_path).exists():
                return {"success": False, "error": "PDF 生成失败：fpdf2 未输出文件"}

            save_result = PdfFileHandler.save_temp(
                source_path=output_path,
                file_name=output_name or "document.pdf",
            )
            save_result["success"] = True
            save_result["engine"] = "fpdf2"
            if css:
                save_result["warnings"] = ["当前 fpdf2 生成路径不支持自定义 CSS，已忽略 css 参数"]
            return save_result

    except Exception as e:
        logger.error(f"[PdfWriter] fpdf2 HTML转PDF失败: {e}", exc_info=True)
        return {"success": False, "error": f"HTML转PDF失败: {e}"}


def docx_to_pdf(file_path: str, output_name: Optional[str] = None) -> Dict[str, Any]:
    """Word → PDF。

    优先使用 LibreOffice（效果最佳），回退到 Pandoc。
    """
    file_path = PdfFileHandler.resolve_path(file_path)
    if not Path(file_path).exists():
        return {"success": False, "error": f"文件不存在: {file_path}"}

    # 尝试 LibreOffice 路径
    result = _docx_to_pdf_via_libreoffice(file_path, output_name)
    if result.get("success"):
        return result

    # 回退到 Pandoc 路径
    result = _docx_to_pdf_via_pandoc(file_path, output_name)
    if result.get("success"):
        return result

    return {"success": False, "error": "Word转PDF失败：LibreOffice 和 Pandoc 均不可用或转换失败"}


def _docx_to_pdf_via_libreoffice(file_path: str, output_name: Optional[str] = None) -> Dict[str, Any]:
    """通过 LibreOffice 将 DOCX 转为 PDF。"""
    import shutil
    import subprocess

    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        return {"success": False, "error": "LibreOffice 未安装"}

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            cmd = [
                soffice, "--headless", "--convert-to", "pdf",
                "--outdir", tmpdir, file_path,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

            pdf_name = Path(file_path).stem + ".pdf"
            output_path = os.path.join(tmpdir, pdf_name)

            if not Path(output_path).exists():
                return {"success": False, "error": "LibreOffice 转换失败", "debug": result.stderr[:500]}

            save_result = PdfFileHandler.save_temp(
                source_path=output_path,
                file_name=output_name or pdf_name,
            )
            save_result["success"] = True
            return save_result

    except subprocess.TimeoutExpired:
        return {"success": False, "error": "LibreOffice 转换超时"}
    except Exception as e:
        logger.error(f"[PdfWriter] LibreOffice 转换失败: {e}", exc_info=True)
        return {"success": False, "error": f"LibreOffice 转换失败: {e}"}


def _docx_to_pdf_via_pandoc(file_path: str, output_name: Optional[str] = None) -> Dict[str, Any]:
    """通过 Pandoc 将 DOCX 转为 PDF（docx_to_pdf 的回退路径）。"""
    import shutil
    import subprocess

    pandoc = shutil.which("pandoc")
    if not pandoc:
        return {"success": False, "error": "Pandoc 未安装"}

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "output.pdf")

            cmd = [pandoc, file_path, "-o", output_path]

            try:
                import weasyprint  # noqa: F401
                cmd.append("--pdf-engine=weasyprint")
            except ImportError:
                pass

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

            if not Path(output_path).exists():
                return {"success": False, "error": "Pandoc 转换失败", "debug": result.stderr[:500]}

            save_result = PdfFileHandler.save_temp(
                source_path=output_path,
                file_name=output_name or "document.pdf",
            )
            save_result["success"] = True
            return save_result

    except Exception as e:
        logger.error(f"[PdfWriter] Pandoc DOCX→PDF 失败: {e}", exc_info=True)
        return {"success": False, "error": f"Pandoc 转换失败: {e}"}
