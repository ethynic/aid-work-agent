#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
render_docs.py — 投标文件渲染引擎（文件操作层，全部由脚本完成）

用途
    把「大模型输出的内容 JSON」渲染成一份或多份专业排版的 DOCX 投标文件。
    大模型只负责产出内容 JSON，**不碰 Word 文件**。

内容 JSON 结构（详见 references/content-spec.md）
{
  "meta": {
    "project_name": "兰香园 D04、D06 地块项目（暂估价：瓷砖）",
    "bidder": "广东东鹏控股股份有限公司",
    "tenderer": "中建二局第三建筑工程有限公司",
    "agency": "海南双达项目管理有限公司",
    "date_cn": "2026 年 6 月"
  },
  "docs": [
    {
      "file": "01-投标函.docx",
      "title": "投标函",
      "volume": "第一册",            # 可选，封面显示
      "cover": {"subtitle": "投标文件", "show_bidder": true},   # 可选
      "sections": [
        {
          "heading": "一、投标函",
          "level": 1,
          "blocks": [
            {"type": "p", "text": "……", "bold": false, "align": "left", "indent": 0},
            {"type": "table", "header": [...], "rows": [[...]], "widths": [2,3],
             "caption": "表1 ……", "note": "注：……"},
            {"type": "kv", "rows": [["字段", "值"], ...]},         # 两列信息表
            {"type": "sign", "lines": ["投标人：（盖单位章）", "…"]},
            {"type": "toc"},                                       # 目录占位
            {"type": "pagebreak"},
            {"type": "list", "items": ["…"], "ordered": true},
            {"type": "note", "text": "注：…"}
          ]
        }
      ]
    }
  ]
}

用法
    python render_docs.py <content.json> --out <输出目录> [--no-footer-check]
"""
import argparse
import json
import os
import sys

# ---------------------------------------------------------------- 版式配置
#
# **版式不固化在技能里。** 渲染器只执行 layout.json，
# 其中每个字段都必须来自「甲方招标要求」或「历史标书范式」（见
# references/layout-extraction.md）。
# 下列 DEFAULTS 仅在 layout.json 缺字段时兜底，并会在渲染报告中提示。

DEFAULTS = {
    'page': {
        'size': 'A4', 'orientation': 'portrait',
        'margin_cm': {'top': 2.8, 'bottom': 2.5, 'left': 3.0, 'right': 3.0},
        'header_distance_cm': 1.5, 'footer_distance_cm': 1.5,
        'binding': None, 'separate_copies': False, 'multi_volume': False,
        'max_pages': None, 'max_pages_scope': None,
    },
    'body': {
        'font': '宋体', 'size_pt': 12.0, 'line_spacing': 1.5,
        'first_line_indent_chars': 2, 'align': 'justify', 'space_after_pt': 6,
    },
    'heading': {
        '1': {'font': '黑体', 'size_pt': 16.0, 'bold': True, 'align': 'center',
              'space_before_pt': 6, 'space_after_pt': 10},
        '2': {'font': '黑体', 'size_pt': 14.0, 'bold': True, 'align': 'left',
              'space_before_pt': 12, 'space_after_pt': 8},
        '3': {'font': '楷体', 'size_pt': 12.0, 'bold': True, 'align': 'left',
              'space_before_pt': 8, 'space_after_pt': 6},
    },
    'table': {
        'font': '宋体', 'size_pt': 9.0, 'header_bold': True,
        'header_shading': 'D9D9D9', 'border': True, 'border_sz': 4,
        'border_color': '000000', 'header_repeat': True, 'cell_v_align': 'center',
        'header_align': 'center', 'align': 'left', 'numeric_align': 'right',
    },
    'caption': {'font': '黑体', 'size_pt': 10.5, 'bold': True, 'align': 'center',
                'space_before_pt': 8},
    'note': {'font': '楷体', 'size_pt': 9.5, 'align': 'left', 'space_after_pt': 8},
    'sign': {'font': '宋体', 'size_pt': 12.0, 'align': 'right', 'space_after_pt': 2},
    'list': {'font': '宋体', 'size_pt': 12.0, 'space_after_pt': 3},
    'cover': {
        'enabled': True, 'title_size_pt': 22.0, 'subtitle_size_pt': 18.0,
        'volume_size_pt': 14.0, 'info_size_pt': 14.0, 'align': 'center',
        'blank_lines_before': 3, 'show_bidder': True, 'show_copy_mark': False,
        'copy_mark_position': 'top_right',
    },
    'toc': {'enabled': True, 'title': '目　录', 'title_font': '黑体',
            'title_size_pt': 16.0, 'levels': '1-3'},
    'header': {'enabled': True, 'text': '', 'font': '宋体', 'size_pt': 9.0,
               'align': 'center'},
    'footer': {'enabled': True, 'text': '', 'font': '宋体', 'size_pt': 9.0,
               'align': 'center', 'page_number': True,
               'page_format': '第 {page} 页 共 {total} 页', 'separator': '    '},
    'signature': {'need_sign_area': True, 'per_page_seal': False,
                  'cross_page_seal': False},
    'copies': {'original': 1, 'copy': 4},
}

PAPER_SIZES = {
    'A4': (21.0, 29.7), 'A3': (29.7, 42.0), 'B5': (18.2, 25.7),
    'LETTER': (21.59, 27.94), 'LEGAL': (21.59, 35.56),
}


def _deep_merge(base, over):
    """over 覆盖 base；dict 递归合并，标量直接覆盖"""
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_layout(path=None):
    """加载 layout.json；未提供则用内置 DEFAULTS（并明确标注为兜底）"""
    if path and os.path.isfile(path):
        with open(path, encoding='utf-8') as fh:
            user = json.load(fh)
        merged = _deep_merge(DEFAULTS, user)
        merged['_layout_path'] = os.path.abspath(path)
        merged['_is_fallback'] = False
        return merged
    merged = _deep_merge(DEFAULTS, {})
    merged['_layout_path'] = None
    merged['_is_fallback'] = True
    return merged


def _set_run_font(run, name, pt, bold=False):
    run.font.name = name
    run.font.size = _Pt(pt)
    run.bold = bool(bold)
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(_qn('w:rFonts'))
    if rfonts is None:
        rfonts = OxmlElement('w:rFonts')
        rpr.append(rfonts)
    rfonts.set(_qn('w:ascii'), name)
    rfonts.set(_qn('w:hAnsi'), name)
    rfonts.set(_qn('w:eastAsia'), name)


def _init():
    global _Pt, _qn, OxmlElement, Document, WD_ALIGN_PARAGRAPH, Pt, RGBColor, _Cm
    from docx.shared import Pt as _P, RGBColor as _RGB, Cm as _C
    from docx.enum.text import WD_ALIGN_PARAGRAPH as _WA
    from docx.oxml.ns import qn as _q
    from docx.oxml import OxmlElement as _OE
    from docx import Document as _D
    _Pt, _qn, OxmlElement, Document = _P, _q, _OE, _D
    WD_ALIGN_PARAGRAPH, Pt, RGBColor, _Cm = _WA, _P, _RGB, _C


def _shade(cell, hexcolor):
    tcpr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement('w:shd')
    shd.set(_qn('w:val'), 'clear')
    shd.set(_qn('w:color'), 'auto')
    shd.set(_qn('w:fill'), hexcolor)
    tcpr.append(shd)


def _cell_borders(table, sz=4, color='000000'):
    tbl = table._tbl
    tblPr = tbl.tblPr
    borders = OxmlElement('w:tblBorders')
    for edge in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
        el = OxmlElement('w:' + edge)
        el.set(_qn('w:val'), 'single')
        el.set(_qn('w:sz'), str(sz))
        el.set(_qn('w:space'), '0')
        el.set(_qn('w:color'), color)
        borders.append(el)
    tblPr.append(borders)


def _repeat_header(row):
    trPr = row._tr.get_or_add_trPr()
    el = OxmlElement('w:tblHeader')
    el.set(_qn('w:val'), 'true')
    trPr.append(el)


def _vcenter(cell):
    tcPr = cell._tc.get_or_add_tcPr()
    va = OxmlElement('w:vAlign')
    va.set(_qn('w:val'), 'center')
    tcPr.append(va)


def _cell_width(cell, cm):
    tcPr = cell._tc.get_or_add_tcPr()
    w = OxmlElement('w:tcW')
    w.set(_qn('w:w'), str(int(cm * 567)))
    w.set(_qn('w:type'), 'dxa')
    tcPr.append(w)


def _footer_pagenum(section, left_text, cfg):
    """页脚：按 layout.footer 配置渲染文字 + 页码域"""
    f = cfg['footer']
    p = section.footer.paragraphs[0]
    p.text = ''
    al = f.get('align', 'center')
    if al == 'center':
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    elif al == 'right':
        p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    elif al == 'left':
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    base = (f.get('text') or '').strip()
    if base:
        r = p.add_run(base)
        _set_run_font(r, f.get('font', '宋体'), f.get('size_pt', 9))
    if f.get('page_number'):
        if base:
            r = p.add_run(f.get('separator', '    '))
            _set_run_font(r, f.get('font', '宋体'), f.get('size_pt', 9))
        fmt = f.get('page_format', '第 {page} 页 共 {total} 页')
        head, _, tail = fmt.partition('{page}')
        if head:
            r = p.add_run(head)
            _set_run_font(r, f.get('font', '宋体'), f.get('size_pt', 9))
        _add_field(p, 'PAGE', f.get('font', '宋体'), f.get('size_pt', 9))
        if tail:
            tail = tail.replace('{total}', '')
            r = p.add_run(tail)
            _set_run_font(r, f.get('font', '宋体'), f.get('size_pt', 9))
        # {total} 单独处理
        if '{total}' in fmt:
            r = p.add_run(' 共 ')
            _set_run_font(r, f.get('font', '宋体'), f.get('size_pt', 9))
            _add_field(p, 'NUMPAGES', f.get('font', '宋体'), f.get('size_pt', 9))
            r = p.add_run(' 页')
            _set_run_font(r, f.get('font', '宋体'), f.get('size_pt', 9))


def _add_field(paragraph, instr, font='宋体', size=9):
    r = paragraph.add_run()
    _set_run_font(r, font, size)
    fc = OxmlElement('w:fldChar'); fc.set(_qn('w:fldCharType'), 'begin')
    it = OxmlElement('w:instrText'); it.set(_qn('xml:space'), 'preserve')
    it.text = ' %s ' % instr
    fc2 = OxmlElement('w:fldChar'); fc2.set(_qn('w:fldCharType'), 'end')
    r._r.append(fc); r._r.append(it); r._r.append(fc2)


def _header_text(section, text, cfg):
    h = cfg['header']
    p = section.header.paragraphs[0]
    p.text = ''
    al = h.get('align', 'center')
    if al == 'center':
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    elif al == 'right':
        p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    elif al == 'left':
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    if text:
        r = p.add_run(text)
        _set_run_font(r, h.get('font', '宋体'), h.get('size_pt', 9))


def _para(doc, text='', size=None, bold=False, font=None, align=None,
          indent_chars=0, first_line=False, space_before=None, space_after=None,
          line_spacing=None, cfg=None):
    """段落渲染；size/font/align/行距/间距 均取自 cfg（layout.json）"""
    b = (cfg or {}).get('body', {})
    size = size if size is not None else b.get('size_pt', 12)
    font = font if font is not None else b.get('font', '宋体')
    align = align if align is not None else b.get('align', 'justify')
    space_after = space_after if space_after is not None else b.get('space_after_pt', 6)
    space_before = space_before if space_before is not None else 0
    line_spacing = line_spacing if line_spacing is not None else b.get('line_spacing', 1.5)
    indent_chars = indent_chars or b.get('first_line_indent_chars', 2) if first_line else indent_chars

    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.line_spacing = line_spacing
    pf.space_before = _Pt(space_before)
    pf.space_after = _Pt(space_after)
    if align == 'center':
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    elif align == 'right':
        p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    elif align == 'justify':
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    if indent_chars:
        pf.first_line_indent = _Pt(size * indent_chars)
    if text:
        r = p.add_run(text)
        _set_run_font(r, font, size, bold)
    return p


def _rich_para(doc, segments, size=None, align=None, first_line=False,
               space_after=None, line_spacing=None, cfg=None):
    """segments: [{'text':..,'bold':..,'font':..,'size':..}]"""
    b = (cfg or {}).get('body', {})
    size = size if size is not None else b.get('size_pt', 12)
    align = align if align is not None else b.get('align', 'justify')
    space_after = space_after if space_after is not None else b.get('space_after_pt', 6)
    line_spacing = line_spacing if line_spacing is not None else b.get('line_spacing', 1.5)
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.line_spacing = line_spacing
    pf.space_after = _Pt(space_after)
    if align == 'center':
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    elif align == 'justify':
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    if first_line:
        pf.first_line_indent = _Pt(size * b.get('first_line_indent_chars', 2))
    for seg in segments:
        r = p.add_run(seg.get('text', ''))
        _set_run_font(r, seg.get('font') or b.get('font', '宋体'),
                      seg.get('size') or size, seg.get('bold', False))
    return p


# ---------------------------------------------------------------- 渲染器

class Renderer(object):
    def __init__(self, meta, layout=None):
        self.meta = meta
        self.L = layout or load_layout(None)
        self.project = meta.get('project_name', '')
        self.doc = Document()
        self._setup_page()
        self._setup_styles()

    def _setup_page(self):
        pg = self.L['page']
        size = str(pg.get('size', 'A4')).upper()
        w, h = PAPER_SIZES.get(size, PAPER_SIZES['A4'])
        if pg.get('orientation') == 'landscape':
            w, h = h, w
        m = pg.get('margin_cm', {})
        for s in self.doc.sections:
            s.page_width = _Cm(w)
            s.page_height = _Cm(h)
            s.left_margin = _Cm(m.get('left', 3.0))
            s.right_margin = _Cm(m.get('right', 3.0))
            s.top_margin = _Cm(m.get('top', 2.8))
            s.bottom_margin = _Cm(m.get('bottom', 2.5))
            s.header_distance = _Cm(pg.get('header_distance_cm', 1.5))
            s.footer_distance = _Cm(pg.get('footer_distance_cm', 1.5))
            if self.L['header'].get('enabled'):
                _header_text(s, self.L['header'].get('text') or self.project,
                             self.L)
            else:
                s.header.paragraphs[0].text = ''
            if self.L['footer'].get('enabled'):
                _footer_pagenum(s, self.L['footer'].get('text') or self.project,
                                self.L)
            else:
                s.footer.paragraphs[0].text = ''

    def _setup_styles(self):
        b = self.L['body']
        st = self.doc.styles['Normal']
        st.font.name = b.get('font', '\u5b8b\u4f53')
        st.font.size = _Pt(b.get('size_pt', 12))
        st.element.rPr.rFonts.set(_qn('w:eastAsia'), b.get('font', '\u5b8b\u4f53'))

    # ---- \u5c01\u9762
    def add_cover(self, spec):
        c = self.L['cover']
        if not spec or not c.get('enabled', True):
            return
        title = spec.get('title') or self.project
        subtitle = spec.get('subtitle', '')
        volume = spec.get('volume', '')
        for _ in range(int(c.get('blank_lines_before', 3))):
            _para(self.doc, '', space_after=0, cfg=self.L)
        _para(self.doc, title, size=c.get('title_size_pt', 22), bold=True,
              font=c.get('font', '\u9ed1\u4f53'), align=c.get('align', 'center'),
              space_after=18, cfg=self.L)
        if subtitle:
            _para(self.doc, subtitle, size=c.get('subtitle_size_pt', 18),
                  bold=True, font=c.get('font', '\u9ed1\u4f53'),
                  align=c.get('align', 'center'), space_after=12, cfg=self.L)
        if volume:
            _para(self.doc, volume, size=c.get('volume_size_pt', 14),
                  font=c.get('volume_font', '\u6977\u4f53'),
                  align=c.get('align', 'center'), space_after=36, cfg=self.L)
        for _ in range(2):
            _para(self.doc, '', space_after=0, cfg=self.L)
        m = self.meta
        isz = c.get('info_size_pt', 14)
        if spec.get('show_bidder', c.get('show_bidder', True)):
            _para(self.doc, '\u6295\u6807\u4eba\uff1a%s\uff08\u76d6\u5355\u4f4d\u7ae0\uff09' % m.get('bidder', ''),
                  size=isz, align=c.get('align', 'center'), space_after=10, cfg=self.L)
            _para(self.doc, '\u6cd5\u5b9a\u4ee3\u8868\u4eba\u6216\u5176\u59d4\u6258\u4ee3\u7406\u4eba\uff1a\uff08\u7b7e\u5b57\uff09',
                  size=isz, align=c.get('align', 'center'), space_after=10, cfg=self.L)
        if m.get('date_cn'):
            _para(self.doc, m['date_cn'], size=isz, align=c.get('align', 'center'),
                  space_after=0, cfg=self.L)
        self.doc.add_page_break()

    # ---- \u76ee\u5f55
    def add_toc(self):
        t = self.L['toc']
        if not t.get('enabled', True):
            return
        _para(self.doc, t.get('title', '\u76ee\u3000\u5f55'),
              size=t.get('title_size_pt', 16), bold=True,
              font=t.get('title_font', '\u9ed1\u4f53'), align='center',
              space_after=18, cfg=self.L)
        b = self.L['body']
        p = self.doc.add_paragraph()
        r = p.add_run()
        _set_run_font(r, b.get('font', '\u5b8b\u4f53'), b.get('size_pt', 12))
        fc = OxmlElement('w:fldChar'); fc.set(_qn('w:fldCharType'), 'begin')
        it = OxmlElement('w:instrText'); it.set(_qn('xml:space'), 'preserve')
        it.text = r' TOC \o "%s" \h \z \u ' % t.get('levels', '1-3')
        fs = OxmlElement('w:fldChar'); fs.set(_qn('w:fldCharType'), 'separate')
        tx = OxmlElement('w:t')
        tx.text = '\uff08\u5728 Word \u4e2d\u6309 F9 \u6216\u53f3\u952e\u201c\u66f4\u65b0\u57df\u201d\u751f\u6210\u76ee\u5f55\uff09'
        fe = OxmlElement('w:fldChar'); fe.set(_qn('w:fldCharType'), 'end')
        r._r.append(fc); r._r.append(it); r._r.append(fs); r._r.append(tx); r._r.append(fe)
        self.doc.add_page_break()

    # ---- \u6807\u9898
    def add_heading(self, text, level=1):
        h = self.L['heading'].get(str(level), self.L['heading'].get('1'))
        p = _para(self.doc, text, size=h.get('size_pt', 14),
                  bold=h.get('bold', True), font=h.get('font', '\u9ed1\u4f53'),
                  align=h.get('align', 'left'),
                  space_before=h.get('space_before_pt', 10),
                  space_after=h.get('space_after_pt', 8), cfg=self.L)
        try:
            p.style = self.doc.styles['Heading %d' % min(level, 3)]
        except Exception:
            pass
        for r in p.runs:
            _set_run_font(r, h.get('font', '\u9ed1\u4f53'), h.get('size_pt', 14),
                          h.get('bold', True))
        return p

    # ---- 段落
    def add_p(self, blk):
        segs = blk.get('segments')
        if segs:
            _rich_para(self.doc, segs, size=blk.get('size'),
                       align=blk.get('align'),
                       first_line=blk.get('first_line', False),
                       space_after=blk.get('space_after'), cfg=self.L)
            return
        _para(self.doc, blk.get('text', ''),
              size=blk.get('size'),
              bold=blk.get('bold', False),
              font=blk.get('font'),
              align=blk.get('align'),
              first_line=blk.get('first_line', False),
              indent_chars=blk.get('indent', 0),
              space_before=blk.get('space_before', 0),
              space_after=blk.get('space_after'), cfg=self.L)

    # ---- 表格
    def add_table(self, blk):
        tb = self.L['table']
        rows = blk.get('rows') or []
        header = blk.get('header')
        ncols = max([len(r) for r in rows] + ([len(header)] if header else [0]) or [1])
        if not ncols:
            return
        cap = blk.get('caption')
        if cap:
            c = self.L['caption']
            _para(self.doc, cap, size=c.get('size_pt', 10.5),
                  bold=c.get('bold', True), font=c.get('font', '黑体'),
                  align=c.get('align', 'center'),
                  space_before=c.get('space_before_pt', 8), cfg=self.L)
        t = self.doc.add_table(rows=0, cols=ncols)
        t.style = 'Table Grid'
        if tb.get('border', True):
            _cell_borders(t, sz=tb.get('border_sz', 4),
                          color=tb.get('border_color', '000000'))
        widths = blk.get('widths')
        aligns = blk.get('aligns') or []
        data = ([header] if header else []) + rows
        for ri, row in enumerate(data):
            cells = t.add_row().cells
            is_head = bool(ri == 0 and header)
            for ci in range(ncols):
                val = row[ci] if ci < len(row) else ''
                cell = cells[ci]
                cell.text = ''
                if ci < len(aligns):
                    al = aligns[ci]
                else:
                    al = tb.get('header_align', 'center') if is_head \
                        else tb.get('align', 'left')
                p = cell.paragraphs[0]
                p.paragraph_format.line_spacing = 1.15
                p.paragraph_format.space_before = _Pt(2)
                p.paragraph_format.space_after = _Pt(2)
                if al == 'center':
                    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                elif al == 'right':
                    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                r = p.add_run(str(val))
                _set_run_font(r, tb.get('font', '宋体'), tb.get('size_pt', 9),
                              tb.get('header_bold', True) if is_head else False)
                _vcenter(cell)
                if is_head and tb.get('header_shading'):
                    _shade(cell, tb['header_shading'])
                if widths and ci < len(widths):
                    _cell_width(cell, widths[ci])
            if is_head and tb.get('header_repeat', True):
                _repeat_header(t.rows[0])
        note = blk.get('note')
        nt = self.L['note']
        if note:
            _para(self.doc, note, size=nt.get('size_pt', 9.5),
                  font=nt.get('font', '楷体'), align=nt.get('align', 'left'),
                  space_before=2, space_after=nt.get('space_after_pt', 8),
                  cfg=self.L)
        else:
            _para(self.doc, '', size=6, space_after=0, cfg=self.L)

    # ---- 偏差表（直接读 matrix.json，杜绝手抄偏离结论）
    def add_matrix_table(self, blk):
        path = blk.get('source')
        if not path or not os.path.isfile(path):
            _para(self.doc, '【偏差表数据缺失：%s】' % path, size=None,
                  bold=True, align='center', cfg=self.L)
            return
        with open(path, encoding='utf-8') as fh:
            mx = json.load(fh)

        which = blk.get('matrix', 'technical')   # technical | commercial | qualification
        cap = blk.get('caption')

        if which == 'technical':
            header = ['序号', '货物/服务名称', '招标文件中的材料及设备技术性能指标/商务等要求',
                      '投标文件响应的材料及设备技术性能指标/商务等内容', '偏离情况（＋/-/=）',
                      '佐证材料索引页码（如有）']
            rows = []
            for r in mx.get('technical', []):
                rows.append([r['spec_id'],
                             r.get('name', ''),
                             r['requirement'],
                             r['our_parameter'],
                             r['deviation'],
                             r.get('evidence', '')])
            widths = [1.0, 2.2, 4.4, 4.4, 1.5, 2.0]
            aligns = ['center', 'left', 'left', 'left', 'center', 'left']
        elif which == 'commercial':
            header = ['序号', '条款名称', '招标文件要求', '投标文件响应内容', '偏离情况（＋/-/=）', '说明']
            rows = []
            for r in mx.get('commercial', []):
                rows.append([r['term_id'], r['term'], r['requirement'],
                             r['our_commitment'], r['deviation'], r.get('note', '')])
            widths = [1.0, 2.2, 4.2, 4.4, 1.5, 2.2]
            aligns = ['center', 'left', 'left', 'left', 'center', 'left']
        else:
            header = ['序号', '资格审查项', '招标文件要求', '我方证明材料', '符合情况', '说明']
            rows = []
            for r in mx.get('qualification', []):
                rows.append([r['req_id'], r.get('name', ''), r['requirement'],
                             r['our_evidence'], r['status'], r.get('note', '')])
            widths = [1.0, 2.4, 4.4, 4.4, 1.5, 1.8]
            aligns = ['center', 'left', 'left', 'left', 'center', 'left']

        tb = self.L['table']
        if cap:
            c = self.L['caption']
            _para(self.doc, cap, size=c.get('size_pt', 10.5),
                  bold=c.get('bold', True), font=c.get('font', '黑体'),
                  align=c.get('align', 'center'),
                  space_before=c.get('space_before_pt', 8), cfg=self.L)
        t = self.doc.add_table(rows=0, cols=len(header))
        t.style = 'Table Grid'
        if tb.get('border', True):
            _cell_borders(t, sz=tb.get('border_sz', 4),
                          color=tb.get('border_color', '000000'))
        for ri, row in enumerate([header] + rows):
            cells = t.add_row().cells
            is_head = (ri == 0)
            for ci in range(len(header)):
                val = row[ci] if ci < len(row) else ''
                cell = cells[ci]
                cell.text = ''
                p = cell.paragraphs[0]
                p.paragraph_format.line_spacing = 1.1
                p.paragraph_format.space_before = _Pt(1.5)
                p.paragraph_format.space_after = _Pt(1.5)
                al = aligns[ci]
                if al == 'center':
                    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                r = p.add_run(str(val))
                bold = is_head or (ci == 4 and str(val) not in ('', '无偏离'))
                _set_run_font(r, tb.get('font', '宋体'), tb.get('size_pt', 9),
                              bold)
                _vcenter(cell)
                if is_head and tb.get('header_shading'):
                    _shade(cell, tb['header_shading'])
                if ci == 4 and not is_head:
                    if str(val).startswith('正偏离'):
                        _shade(cell, 'E2EFDA')
                    elif str(val).startswith('负偏离') or str(val) == '不满足':
                        _shade(cell, 'FCE4E4')
                    elif str(val) == '部分满足':
                        _shade(cell, 'FFF2CC')
                if ci < len(widths):
                    _cell_width(cell, widths[ci])
            if is_head and tb.get('header_repeat', True):
                _repeat_header(t.rows[0])
        _para(self.doc, '', size=6, space_after=0, cfg=self.L)

        note = blk.get('note')
        if note:
            nt = self.L['note']
            _para(self.doc, note, size=nt.get('size_pt', 9.5),
                  font=nt.get('font', '楷体'), align=nt.get('align', 'justify'),
                  space_before=2, space_after=nt.get('space_after_pt', 8),
                  cfg=self.L)

    # ---- 报价表（直接读 price_result.json，杜绝手抄数字）
    def add_price_table(self, blk):
        path = blk.get('source')
        if not path or not os.path.isfile(path):
            _para(self.doc, '【报价表数据缺失：%s】' % path, size=None,
                  bold=True, align='center', cfg=self.L)
            return
        with open(path, encoding='utf-8') as fh:
            pr = json.load(fh)

        style = blk.get('style', 'tender')   # tender=招标书12列 / simple=简表
        tb = self.L['table']
        cap = blk.get('caption') or ('%s 报价一览表' % pr.get('project_name', ''))
        c = self.L['caption']
        _para(self.doc, cap, size=c.get('size_pt', 10.5), bold=c.get('bold', True),
              font=c.get('font', '黑体'), align=c.get('align', 'center'),
              space_before=c.get('space_before_pt', 8), cfg=self.L)

        if style == 'tender':
            header = ['序号', '部位', '名称', '单位', '工程量',
                      '不含税单价（元）', '不含税合价（元）', '税率（%）',
                      '含税单价（元）', '含税合价（元）', '报价品牌', '备注']
            rows = []
            for r in pr['rows']:
                rows.append([r['no'], r['area'], r['name'], r['unit'], r['qty'],
                             r['unit_price_pretax'], r['amount_pretax'], r['tax_rate'],
                             r['unit_price_taxed'], r['amount_taxed'],
                             r['brand'], r['note']])
            rows.append(['合计（元）', '', '', '', '', '', '%.2f' % pr['total_pretax'],
                         '', '', '%.2f' % pr['total_taxed'], '', ''])
            widths = [1.0, 2.0, 2.6, 0.9, 1.6, 1.8, 1.9, 1.1, 1.8, 1.9, 1.4, 1.4]
            aligns = ['center', 'center', 'left', 'center', 'right', 'right',
                      'right', 'center', 'right', 'right', 'center', 'left']
        else:
            header = ['序号', '材料名称', '规格型号', '单位', '数量',
                      '不含税单价（元）', '含税合价（元）', '备注']
            rows = []
            for r in pr['rows']:
                rows.append([r['no'], r['name'], r.get('model', ''), r['unit'],
                             r['qty'], r['unit_price_pretax'], r['amount_taxed'],
                             r['note']])
            rows.append(['合计', '', '', '', '', '', '%.2f' % pr['total_taxed'], ''])
            widths = [1.2, 3.4, 2.0, 1.0, 2.0, 2.4, 2.4, 2.0]
            aligns = ['center', 'left', 'center', 'center', 'right', 'right',
                      'right', 'left']

        t = self.doc.add_table(rows=0, cols=len(header))
        t.style = 'Table Grid'
        if tb.get('border', True):
            _cell_borders(t, sz=tb.get('border_sz', 4),
                          color=tb.get('border_color', '000000'))
        for ri, row in enumerate([header] + rows):
            cells = t.add_row().cells
            is_head = (ri == 0)
            is_total = (ri == len(rows))
            for ci in range(len(header)):
                val = row[ci] if ci < len(row) else ''
                cell = cells[ci]
                cell.text = ''
                p = cell.paragraphs[0]
                p.paragraph_format.line_spacing = 1.1
                p.paragraph_format.space_before = _Pt(1.5)
                p.paragraph_format.space_after = _Pt(1.5)
                al = aligns[ci]
                if al == 'center':
                    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                elif al == 'right':
                    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                r = p.add_run(str(val))
                _set_run_font(r, tb.get('font', '宋体'), tb.get('size_pt', 9),
                              is_head or is_total)
                _vcenter(cell)
                if is_head and tb.get('header_shading'):
                    _shade(cell, tb['header_shading'])
                elif is_total and tb.get('header_shading'):
                    _shade(cell, tb['header_shading'])
                if ci < len(widths):
                    _cell_width(cell, widths[ci])
            if is_head and tb.get('header_repeat', True):
                _repeat_header(t.rows[0])
        _para(self.doc, '', size=6, space_after=0, cfg=self.L)

        # 报价说明
        notes = []
        if pr.get('freight_note'):
            notes.append(pr['freight_note'])
        notes.append('投标总报价（含税）：¥%.2f 元（大写：%s）' % (
            pr['total_taxed'], pr['total_upper']))
        if pr.get('cap') is not None:
            notes.append('本报价低于最高投标限价 ¥%.2f 元，差额 ¥%.2f 元。' % (
                float(pr['cap']), float(pr['cap']) - pr['total_taxed']))
        notes.append('本报价单价为固定综合单价，包干不调整；最终以实际验收合格数量结算。')
        for n in notes:
            _para(self.doc, n, size=self.L['body'].get('size_pt'),
                  align='justify', space_after=3, cfg=self.L)
        for n in (pr.get('price_notes') or []):
            _para(self.doc, n, size=self.L['body'].get('size_pt'),
                  align='justify', space_after=3, cfg=self.L)

    # ---- 信息表（两列）
    def add_kv(self, blk):
        tb = self.L['table']
        rows = blk.get('rows') or []
        if not rows:
            return
        ncols = max(len(r) for r in rows)
        t = self.doc.add_table(rows=0, cols=ncols)
        t.style = 'Table Grid'
        if tb.get('border', True):
            _cell_borders(t, sz=tb.get('border_sz', 4),
                          color=tb.get('border_color', '000000'))
        widths = blk.get('widths') or ([4.5, 11.0] if ncols == 2 else None)
        for ri, row in enumerate(rows):
            cells = t.add_row().cells
            for ci in range(ncols):
                val = row[ci] if ci < len(row) else ''
                cell = cells[ci]
                cell.text = ''
                p = cell.paragraphs[0]
                p.paragraph_format.line_spacing = 1.3
                p.paragraph_format.space_before = _Pt(2)
                p.paragraph_format.space_after = _Pt(2)
                r = p.add_run(str(val))
                is_label = (ci % 2 == 0) and ncols == 2
                _set_run_font(r, tb.get('font', '宋体'), tb.get('size_pt', 9),
                              is_label)
                _vcenter(cell)
                if is_label and tb.get('header_shading'):
                    _shade(cell, tb['header_shading'])
                if widths and ci < len(widths):
                    _cell_width(cell, widths[ci])
        _para(self.doc, '', size=6, space_after=0, cfg=self.L)

    # ---- 签字区
    def add_sign(self, blk):
        sg = self.L['sign']
        lines = blk.get('lines') or []
        align = blk.get('align', sg.get('align', 'right'))
        for ln in lines:
            _para(self.doc, ln, size=blk.get('size', sg.get('size_pt', 12)),
                  font=blk.get('font', sg.get('font', '宋体')), align=align,
                  space_after=sg.get('space_after_pt', 2), cfg=self.L)

    # ---- 清单
    def add_list(self, blk):
        ls = self.L['list']
        b = self.L['body']
        items = blk.get('items') or []
        ordered = blk.get('ordered', True)
        for i, it in enumerate(items, 1):
            if isinstance(it, dict):
                txt = it.get('text', '')
                lvl = it.get('level', 1)
                bold = it.get('bold', False)
            else:
                txt, lvl, bold = it, 1, False
            prefix = ('%d. ' % i) if ordered else ''
            p = _para(self.doc, prefix + txt, size=ls.get('size_pt'),
                      bold=bold, font=ls.get('font'),
                      space_after=ls.get('space_after_pt', 3), cfg=self.L)
            if lvl > 1:
                p.paragraph_format.left_indent = _Pt(
                    ls.get('size_pt', b.get('size_pt', 12)) * (lvl - 1) * 2)

    def add_note(self, blk):
        nt = self.L['note']
        _para(self.doc, blk.get('text', ''), size=nt.get('size_pt', 9.5),
              font=nt.get('font', '楷体'), align=nt.get('align', 'justify'),
              space_before=4, space_after=nt.get('space_after_pt', 8), cfg=self.L)

    # ---- 渲染一个 doc
    def render_doc(self, spec):
        self.add_cover(spec.get('cover'))
        for sec in spec.get('sections') or []:
            if sec.get('heading'):
                self.add_heading(sec['heading'], sec.get('level', 1))
            for blk in sec.get('blocks') or []:
                t = blk.get('type')
                if t == 'p':
                    self.add_p(blk)
                elif t == 'table':
                    self.add_table(blk)
                elif t == 'price_table':
                    self.add_price_table(blk)
                elif t == 'matrix_table':
                    self.add_matrix_table(blk)
                elif t == 'kv':
                    self.add_kv(blk)
                elif t == 'sign':
                    self.add_sign(blk)
                elif t == 'list':
                    self.add_list(blk)
                elif t == 'note':
                    self.add_note(blk)
                elif t == 'toc':
                    self.add_toc()
                elif t == 'pagebreak':
                    self.doc.add_page_break()
                elif t == 'heading':
                    self.add_heading(blk.get('text', ''), blk.get('level', 2))


def _mark_copy(doc, mark, L):
    """在封面右上角标注「正本」/「副本」（招标文件常要求）"""
    from docx.enum.text import WD_ALIGN_PARAGRAPH as WA
    pos = L['cover'].get('copy_mark_position', 'top_right')
    if pos == 'top_right':
        p = doc.paragraphs[0] if doc.paragraphs else doc.add_paragraph()
        p.alignment = WA.RIGHT
        r = p.add_run(mark)
        _set_run_font(r, L['body'].get('font', '宋体'),
                      L['body'].get('size_pt', 12), True)
    else:
        p = doc.add_paragraph()
        p.alignment = WA.CENTER
        r = p.add_run(mark)
        _set_run_font(r, L['body'].get('font', '宋体'),
                      L['body'].get('size_pt', 12), True)

def main():
    ap = argparse.ArgumentParser(
        description='投标文件 DOCX 渲染引擎（版式由 layout.json 决定）')
    ap.add_argument('content', help='内容 JSON 文件')
    ap.add_argument('--out', required=True, help='输出目录')
    ap.add_argument('--layout', default=None,
                    help='版式配置 layout.json；由甲方招标要求 + 历史标书推导而来')
    ap.add_argument('--copy-mark', default=None,
                    choices=['正本', '副本'],
                    help='封面右上角标注正本/副本（须与 layout.cover.show_copy_mark 配合）')
    args = ap.parse_args()

    _init()
    if not os.path.isfile(args.content):
        print('[错误] 内容文件不存在: %s' % args.content, file=sys.stderr)
        return 2
    with open(args.content, encoding='utf-8') as fh:
        data = json.load(fh)

    meta = data.get('meta', {})
    docs = data.get('docs') or []
    if not docs:
        print('[错误] 内容 JSON 中没有 docs', file=sys.stderr)
        return 2

    # 版式：优先 --layout，其次 content.json 内的 layout 字段，最后兜底
    layout_path = args.layout
    if not layout_path and isinstance(data.get('layout'), dict):
        layout_path = os.path.join(os.path.dirname(os.path.abspath(args.content)),
                                   'layout.json')
        if not os.path.isfile(layout_path):
            layout_path = None
    L = load_layout(layout_path)
    if L.get('_is_fallback'):
        print('[版式] 未提供 layout.json，使用内置兜底版式'
              '（正文宋体小四/A4/表头灰底）——请按甲方要求与历史标书推导后重跑',
              file=sys.stderr)
    else:
        src = L.get('_source', {})
        print('[版式] %s' % (L.get('_layout_path')))
        if src.get('tender'):
            print('       甲方依据：%s' % src['tender'])
        if src.get('history'):
            print('       历史范式：%s' % src['history'])

    os.makedirs(args.out, exist_ok=True)
    ok = 0
    for spec in docs:
        fn = spec.get('file') or (spec.get('title', 'document') + '.docx')
        if not fn.lower().endswith('.docx'):
            fn += '.docx'
        r = Renderer(meta, L)
        r.render_doc(spec)
        # 封面正/副本标记
        if args.copy_mark and L['cover'].get('show_copy_mark'):
            _mark_copy(r.doc, args.copy_mark, L)
        path = os.path.join(args.out, fn)
        r.doc.save(path)
        size = os.path.getsize(path)
        print('[渲染] %s  (%d 字节)  ← %s' % (fn, size, spec.get('title', '')))
        ok += 1
    print('[完成] 共生成 %d 份文件 → %s' % (ok, os.path.abspath(args.out)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
