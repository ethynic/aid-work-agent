"""
Word 工具核心库

从 skill/word-processing/scripts/word_lib.py 迁移，提供共享的基础功能：
- 常量映射（字体、对齐、页面大小）
- 样式操作（StyleManager）
- 文件操作（WordFileHandler）
- 跨 run 文本替换算法
- 全文档段落迭代（iter_all_paragraphs / paragraph_text_runs，覆盖嵌套表格/文本框/超链接）
"""

import json
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph
from docx.text.run import Run

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


def resolve_font_name(name: str) -> str:
    """解析字体名称，支持中文名映射"""
    if not name:
        return name
    return CHINESE_FONTS.get(name, name)


def resolve_alignment(align: str):
    """解析对齐方式字符串为 python-docx 枚举值"""
    if isinstance(align, str):
        return ALIGNMENT_MAP.get(align, None)
    return align


def parse_color(color_str: str) -> Optional[RGBColor]:
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


def replace_text_cross_run(runs, target: str, replacement: str) -> int:
    """
    替换可能跨越多个 run 的文本。

    Word 文档中一个逻辑句子可能被拆分为多个 run（编辑、格式变化等导致），
    此函数拼接相邻 run 文本进行匹配，然后从后向前修改以保持索引稳定。
    """
    if not runs or not target:
        return 0

    concat = ""
    char_map = []
    for ri, run in enumerate(runs):
        for ci, ch in enumerate(run.text):
            char_map.append((ri, ci))
            concat += ch

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

    for occ_idx in reversed(occurrences):
        end_idx = occ_idx + len(target) - 1
        run_start = char_map[occ_idx][0]
        run_end = char_map[end_idx][0]
        char_start = char_map[occ_idx][1]
        char_end = char_map[end_idx][1]

        if run_start == run_end:
            run_text = runs[run_start].text
            runs[run_start].text = (
                run_text[:char_start] + replacement + run_text[char_end + 1:]
            )
        else:
            runs[run_start].text = runs[run_start].text[:char_start] + replacement
            for ri in range(run_start + 1, run_end):
                runs[ri].text = ""
            runs[run_end].text = runs[run_end].text[char_end + 1:]

    return len(occurrences)


def iter_all_paragraphs(doc: Document) -> List[Paragraph]:
    """
    全文档段落迭代器（文档序），修复只遍历顶层段落/表格时的扫描盲区。

    覆盖范围：
    - 正文所有 w:p：用 body.iter(qn('w:p')) 递归一把抓，
      天然包含嵌套表格、内容控件（w:sdt）内的段落、文本框（w:txbxContent）内的段落
    - 各 section 的页眉/页脚（含 first_page / even_page 变体）中的所有 w:p，
      同样覆盖其中的表格和文本框段落

    已知限制（明确不支持）：
    - 脚注/尾注位于独立的 XML part，python-docx 不解析，不在遍历范围内

    Returns:
        Paragraph 列表（每个 w:p 用 doc 作 parent 包装，本场景只改 run 文本）
    """
    paragraphs: List[Paragraph] = []

    # 正文：body 递归迭代覆盖嵌套表格 / sdt / txbxContent 内的所有段落
    for p_el in doc.element.body.iter(qn('w:p')):
        paragraphs.append(Paragraph(p_el, doc))

    # 页眉页脚：仅访问有自定义定义的（is_linked_to_previous=False），
    # 避免触发 python-docx 懒创建空的 header/footer part；个别属性访问可能抛异常，逐个 try
    for section in doc.sections:
        for hf in (section.header, section.footer,
                   section.first_page_header, section.first_page_footer,
                   section.even_page_header, section.even_page_footer):
            try:
                if hf.is_linked_to_previous:
                    continue
                for p_el in hf._element.iter(qn('w:p')):
                    paragraphs.append(Paragraph(p_el, doc))
            except Exception:
                continue

    return paragraphs


def paragraph_text_runs(para: Paragraph) -> List[Run]:
    """
    返回该段落文档序的全部含文本 run，供 replace_text_cross_run 使用。

    与 paragraph.runs 的区别：paragraph.runs 只取 w:p 直接子级的 w:r，
    本函数额外覆盖 w:hyperlink（超链接）、w:ins（修订插入）等容器内的 run，
    修复超链接内占位符漏替换的问题。

    注意：run 自身不再下钻（文本框嵌套在 run 的 drawing 内，其段落由
    iter_all_paragraphs 单独覆盖，避免重复处理）；嵌套 w:p 同样跳过。
    """
    runs: List[Run] = []
    for r_el in _iter_runs_excluding_nested(para._element):
        run = Run(r_el, para)
        if run.text:
            runs.append(run)
    return runs


def _iter_runs_excluding_nested(el):
    """递归收集元素内文档序的 w:r，不进入 w:r 内部（避免文本框 run 重复），跳过嵌套 w:p"""
    for child in el.iterchildren():
        tag = child.tag
        if tag == qn('w:r'):
            yield child
        elif tag == qn('w:p'):
            # 嵌套段落（文本框内）：由 iter_all_paragraphs 单独覆盖
            continue
        elif isinstance(tag, str):
            # 常规容器（w:hyperlink、w:ins、w:sdt 等）继续下钻；跳过注释等非元素节点
            yield from _iter_runs_excluding_nested(child)


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
            resolved = resolve_font_name(font_name)
            font.name = resolved
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
            rgb = parse_color(color)
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
            resolved = resolve_alignment(alignment)
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
            for s in styles:
                if s.name == style_name:
                    return s

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


class WordFileHandler:
    """Word 文件操作管理"""

    @staticmethod
    def save_temp(doc: Document, file_name: Optional[str] = None,
                  output_dir: Optional[str] = None) -> Dict[str, Any]:
        """将文档保存到指定目录或用户会话目录。"""
        if output_dir:
            save_dir = Path(output_dir)
        else:
            save_dir = WordFileHandler.get_session_dir()

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

    @staticmethod
    def get_session_dir() -> Path:
        """获取当前用户会话的文件存储目录。

        目录结构: storage/tenants/{tenant_id}/conversation/
        无租户时: storage/tenants/_anonymous/conversation/

        与用户上传文件共用同一目录，生成的文件天然支持下载和预览。
        """
        try:
            from src.main import _get_tenant_upload_dir
            return _get_tenant_upload_dir()
        except ImportError:
            import tempfile
            return Path(tempfile.mkdtemp(prefix="word_"))

    @staticmethod
    def copy_and_open(file_path: str) -> Tuple[Document, Dict[str, Any]]:
        """打开文件（永不修改原文件）。"""
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

    @staticmethod
    def resolve_path(file_path: str) -> str:
        """解析文件路径（支持相对路径或 file_id）

        查找顺序：
        1. 原路径直接命中（含绝对路径）
        2. Redis 元数据命中（file_id -> uploaded_file:{file_id}.path，最可靠）
        3. 新路径 storage/tenants/{tenant}/conversation/{file}（含 _anonymous 兜底）
        """
        p = Path(file_path)
        # 防路径穿越：含 .. 的相对路径不得进行 exists 检查或路径拼接
        # （Path.exists() 和 Path()/.. 都会自动 resolve 后命中项目外系统文件）
        if not p.is_absolute() and ".." in p.parts:
            return str(p.absolute())
        if p.exists():
            return str(p.absolute())

        # Redis 元数据命中：file_id 上传时写了 uploaded_file:{file_id} 永久元数据
        # 适用于所有走 cp/upload/subagent_template_file 上传的文件，不依赖目录扫描
        try:
            from src.core.storage import resolve_path_via_redis
            redis_path = resolve_path_via_redis(file_path)
            if redis_path:
                return redis_path
        except ImportError:
            pass

        # 优先在新路径下查找
        try:
            from src.core.storage import _TENANTS_ROOT
            project_root = Path(__file__).resolve().parents[3]
            tenants_root = project_root / _TENANTS_ROOT
            if tenants_root.exists():
                # 防路径穿越：file_path 含 .. 或绝对路径时跳过新路径扫描
                fp_obj = Path(file_path)
                if not fp_obj.is_absolute() and ".." not in fp_obj.parts:
                    for d1 in tenants_root.iterdir():
                        if d1.is_dir():
                            candidate = d1 / "conversation" / file_path
                            if candidate.exists():
                                return str(candidate.absolute())
        except (ImportError, AttributeError):
            pass
        return str(p.absolute())
