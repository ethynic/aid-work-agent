#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
docx_kit.py — Word/Excel 源文件排版基础库（被 build_source_docs.py 调用）

提供投标资料编制常用的排版能力：
    - 中文文档样式（A4、宋体正文、黑体标题、灰底表头、全框线、页眉页脚页码）
    - 封面、目录域、章节标题、正文、KV 信息表、表格、签字区
    - Excel 表格样式（标题行冻结、筛选、列宽、金额格式、边框）
"""
import os

FONT_HEI = '黑体'
FONT_SONG = '宋体'
FONT_KAI = '楷体'


def _init():
    global Document, Pt, Cm, RGBColor, WD_ALIGN_PARAGRAPH, WD_TABLE_ALIGNMENT
    global _qn, OxmlElement
    from docx import Document as _D
    from docx.shared import Pt as _P, Cm as _C, RGBColor as _RGB
    from docx.enum.text import WD_ALIGN_PARAGRAPH as _WA
    from docx.enum.table import WD_TABLE_ALIGNMENT as _WT
    from docx.oxml.ns import qn as _q
    from docx.oxml import OxmlElement as _OE
    Document, Pt, Cm, RGBColor = _D, _P, _C, _RGB
    WD_ALIGN_PARAGRAPH, WD_TABLE_ALIGNMENT = _WA, _WT
    _qn, OxmlElement = _q, _OE


def _set_run(run, name=FONT_SONG, pt=12.0, bold=False):
    run.font.name = name
    run.font.size = Pt(pt)
    run.bold = bool(bold)
    rpr = run._element.get_or_add_rPr()
    rf = rpr.find(_qn('w:rFonts'))
    if rf is None:
        rf = OxmlElement('w:rFonts')
        rpr.append(rf)
    for k in ('w:ascii', 'w:hAnsi', 'w:eastAsia'):
        rf.set(_qn(k), name)


def _shade(cell, hexcolor):
    tcpr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement('w:shd')
    shd.set(_qn('w:val'), 'clear')
    shd.set(_qn('w:color'), 'auto')
    shd.set(_qn('w:fill'), hexcolor)
    tcpr.append(shd)


def _borders(table, sz=4, color='000000'):
    tblPr = table._tbl.tblPr
    b = OxmlElement('w:tblBorders')
    for edge in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
        el = OxmlElement('w:' + edge)
        el.set(_qn('w:val'), 'single')
        el.set(_qn('w:sz'), str(sz))
        el.set(_qn('w:space'), '0')
        el.set(_qn('w:color'), color)
        b.append(el)
    tblPr.append(b)


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


def _cellw(cell, cm):
    tcPr = cell._tc.get_or_add_tcPr()
    w = OxmlElement('w:tcW')
    w.set(_qn('w:w'), str(int(cm * 567)))
    w.set(_qn('w:type'), 'dxa')
    tcPr.append(w)


def _field(p, instr):
    r = p.add_run()
    _set_run(r, FONT_SONG, 9)
    fc = OxmlElement('w:fldChar'); fc.set(_qn('w:fldCharType'), 'begin')
    it = OxmlElement('w:instrText'); it.set(_qn('xml:space'), 'preserve')
    it.text = ' %s ' % instr
    fe = OxmlElement('w:fldChar'); fe.set(_qn('w:fldCharType'), 'end')
    r._r.append(fc); r._r.append(it); r._r.append(fe)


class DocBuilder(object):
    """按招标资料惯例排版的 Word 文档构建器"""

    def __init__(self, header_text='', footer_text='',
                 margin=(2.8, 2.5, 3.0, 3.0)):
        _init()
        self.doc = Document()
        self.header_text = header_text
        self.footer_text = footer_text
        self._page(margin)

    def _page(self, margin):
        top, bottom, left, right = margin
        for s in self.doc.sections:
            s.page_width = Cm(21.0)
            s.page_height = Cm(29.7)
            s.top_margin = Cm(top)
            s.bottom_margin = Cm(bottom)
            s.left_margin = Cm(left)
            s.right_margin = Cm(right)
            s.header_distance = Cm(1.5)
            s.footer_distance = Cm(1.5)
            # 页眉
            hp = s.header.paragraphs[0]
            hp.text = ''
            hp.alignment = WD_ALIGN_PARAGRAPH.CENTER
            if self.header_text:
                r = hp.add_run(self.header_text)
                _set_run(r, FONT_SONG, 9)
            # 页脚：项目名 + 第X页 共Y页
            fp = s.footer.paragraphs[0]
            fp.text = ''
            fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
            if self.footer_text:
                r = fp.add_run(self.footer_text + '    第 ')
                _set_run(r, FONT_SONG, 9)
                _field(fp, 'PAGE')
                r = fp.add_run(' 页  共 ')
                _set_run(r, FONT_SONG, 9)
                _field(fp, 'NUMPAGES')
                r = fp.add_run(' 页')
                _set_run(r, FONT_SONG, 9)
        st = self.doc.styles['Normal']
        st.font.name = FONT_SONG
        st.font.size = Pt(12)
        st.element.rPr.rFonts.set(_qn('w:eastAsia'), FONT_SONG)

    # ---------- 元素 ----------
    def para(self, text='', size=12, bold=False, font=FONT_SONG, align='justify',
             first_line=True, space_before=0, space_after=6, line_spacing=1.5):
        p = self.doc.add_paragraph()
        pf = p.paragraph_format
        pf.line_spacing = line_spacing
        pf.space_before = Pt(space_before)
        pf.space_after = Pt(space_after)
        if align == 'center':
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        elif align == 'right':
            p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        elif align == 'justify':
            p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        if first_line and text:
            pf.first_line_indent = Pt(size * 2)
        if text:
            r = p.add_run(text)
            _set_run(r, font, size, bold)
        return p

    def h1(self, text):
        p = self.para(text, size=16, bold=True, font=FONT_HEI, align='center',
                      first_line=False, space_before=6, space_after=12)
        try:
            p.style = self.doc.styles['Heading 1']
        except Exception:
            pass
        for r in p.runs:
            _set_run(r, FONT_HEI, 16, True)
        return p

    def h2(self, text):
        p = self.para(text, size=14, bold=True, font=FONT_HEI, align='left',
                      first_line=False, space_before=12, space_after=8)
        try:
            p.style = self.doc.styles['Heading 2']
        except Exception:
            pass
        for r in p.runs:
            _set_run(r, FONT_HEI, 14, True)
        return p

    def h3(self, text):
        p = self.para(text, size=12, bold=True, font=FONT_KAI, align='left',
                      first_line=False, space_before=8, space_after=6)
        try:
            p.style = self.doc.styles['Heading 3']
        except Exception:
            pass
        for r in p.runs:
            _set_run(r, FONT_KAI, 12, True)
        return p

    def kv(self, rows, widths=(4.5, 10.5), label_shade='F2F2F2'):
        t = self.doc.add_table(rows=0, cols=len(widths))
        t.style = 'Table Grid'
        _borders(t)
        for row in rows:
            cells = t.add_row().cells
            for ci in range(len(widths)):
                val = row[ci] if ci < len(row) else ''
                cell = cells[ci]
                cell.text = ''
                p = cell.paragraphs[0]
                p.paragraph_format.line_spacing = 1.3
                p.paragraph_format.space_before = Pt(2)
                p.paragraph_format.space_after = Pt(2)
                r = p.add_run(str(val))
                is_label = (ci == 0)
                _set_run(r, FONT_SONG, 10.5, is_label)
                _vcenter(cell)
                if is_label and label_shade:
                    _shade(cell, label_shade)
                _cellw(cell, widths[ci])
        self.para('', size=6, space_after=0, first_line=False)
        return t

    def table(self, header, rows, widths=None, aligns=None, caption=None,
              note=None, size=9, header_shade='D9D9D9'):
        if caption:
            self.para(caption, size=10.5, bold=True, font=FONT_HEI,
                      align='center', first_line=False, space_before=8,
                      space_after=3)
        ncols = len(header) if header else (max(len(r) for r in rows) if rows else 0)
        t = self.doc.add_table(rows=0, cols=ncols)
        t.style = 'Table Grid'
        _borders(t)
        aligns = aligns or []
        data = ([header] if header else []) + list(rows)
        for ri, row in enumerate(data):
            cells = t.add_row().cells
            is_head = bool(header and ri == 0)
            for ci in range(ncols):
                val = row[ci] if ci < len(row) else ''
                cell = cells[ci]
                cell.text = ''
                al = aligns[ci] if ci < len(aligns) else ('center' if is_head else 'left')
                p = cell.paragraphs[0]
                p.paragraph_format.line_spacing = 1.15
                p.paragraph_format.space_before = Pt(2)
                p.paragraph_format.space_after = Pt(2)
                if al == 'center':
                    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                elif al == 'right':
                    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                r = p.add_run(str(val))
                _set_run(r, FONT_SONG, size, is_head)
                _vcenter(cell)
                if is_head and header_shade:
                    _shade(cell, header_shade)
                if widths and ci < len(widths):
                    _cellw(cell, widths[ci])
            if is_head:
                _repeat_header(t.rows[0])
        if note:
            self.para(note, size=9.5, font=FONT_KAI, align='left',
                      first_line=False, space_before=2, space_after=8)
        else:
            self.para('', size=6, space_after=0, first_line=False)
        return t

    def cover(self, title, subtitle, bidder, date_cn, extra_lines=None,
              mark=None):
        if mark:
            p = self.doc.paragraphs[0] if self.doc.paragraphs else self.doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            r = p.add_run(mark)
            _set_run(r, FONT_SONG, 12, True)
        for _ in range(4):
            self.para('', space_after=0, first_line=False)
        self.para(title, size=22, bold=True, font=FONT_HEI, align='center',
                  first_line=False, space_after=18)
        self.para(subtitle, size=18, bold=True, font=FONT_HEI, align='center',
                  first_line=False, space_after=40)
        for ln in (extra_lines or []):
            self.para(ln, size=14, align='center', first_line=False, space_after=8)
        for _ in range(2):
            self.para('', space_after=0, first_line=False)
        self.para('投标人：%s（盖单位章）' % bidder, size=14, align='center',
                  first_line=False, space_after=10)
        self.para('法定代表人或其委托代理人：（签字）', size=14, align='center',
                  first_line=False, space_after=10)
        self.para(date_cn, size=14, align='center', first_line=False, space_after=0)
        self.doc.add_page_break()

    def toc(self, title='目　录'):
        self.para(title, size=16, bold=True, font=FONT_HEI, align='center',
                  first_line=False, space_after=18)
        p = self.doc.add_paragraph()
        r = p.add_run()
        _set_run(r, FONT_SONG, 12)
        fc = OxmlElement('w:fldChar'); fc.set(_qn('w:fldCharType'), 'begin')
        it = OxmlElement('w:instrText'); it.set(_qn('xml:space'), 'preserve')
        it.text = r' TOC \o "1-3" \h \z \u '
        fs = OxmlElement('w:fldChar'); fs.set(_qn('w:fldCharType'), 'separate')
        tx = OxmlElement('w:t')
        tx.text = '（在 Word 中按 Ctrl+A 后按 F9 更新目录）'
        fe = OxmlElement('w:fldChar'); fe.set(_qn('w:fldCharType'), 'end')
        r._r.append(fc); r._r.append(it); r._r.append(fs); r._r.append(tx); r._r.append(fe)
        self.doc.add_page_break()

    def sign(self, lines):
        for ln in lines:
            self.para(ln, size=12, align='right', first_line=False, space_after=2)

    def pagebreak(self):
        self.doc.add_page_break()

    def save(self, path):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.doc.save(path)
        return path


# ---------------------------------------------------------------- Excel

def style_sheet(ws, header_row=1, widths=None, freeze=True, autofilter=True,
                header_fill='D9D9D9', align_center_cols=None, num_formats=None):
    """统一 Excel 表格样式：标题行加粗灰底、冻结、筛选、列宽、边框"""
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    thin = Side(style='thin', color='000000')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    fill = PatternFill('solid', fgColor=header_fill)

    max_col = ws.max_column
    max_row = ws.max_row

    # 边框 + 对齐
    for r in range(1, max_row + 1):
        for c in range(1, max_col + 1):
            cell = ws.cell(row=r, column=c)
            cell.border = border
            if r == header_row:
                cell.font = Font(name='宋体', size=10, bold=True)
                cell.fill = fill
                cell.alignment = Alignment(horizontal='center', vertical='center',
                                           wrap_text=True)
            else:
                cell.font = Font(name='宋体', size=10)
                al = 'center' if (align_center_cols and c in align_center_cols) else 'left'
                if num_formats and c in num_formats:
                    al = 'right'
                cell.alignment = Alignment(horizontal=al, vertical='center',
                                           wrap_text=True)
                if num_formats and c in num_formats:
                    cell.number_format = num_formats[c]

    if widths:
        for c, w in widths.items():
            ws.column_dimensions[get_column_letter(c)].width = w

    if freeze and max_row > header_row:
        ws.freeze_panes = ws.cell(row=header_row + 1, column=1)
    if autofilter and max_row >= header_row:
        ws.auto_filter.ref = (get_column_letter(1) + str(header_row) + ':' +
                              get_column_letter(max_col) + str(max_row))
    ws.row_dimensions[header_row].height = 26
