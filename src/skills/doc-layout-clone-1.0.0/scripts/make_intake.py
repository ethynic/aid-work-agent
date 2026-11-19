#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通用「待补充信息采集表」生成器（doc-layout-clone 技能配套）。

设计红线：脚本不含任何业务常量。字段清单由本次读样版分析得出，写在外部 fields.json。

两种模式：
  1) 生成（默认）：fields.json -> intake.docx
     - 示例值以【黄色高亮】呈现，提示用户替换为真实值；确认沿用示例可保留高亮。
  2) 回收（--extract）：从用户填写后的 intake.docx 读回 key -> value，
     供回填 answers.json。按 fields.json 的行列结构定位，不依赖用户是否保留高亮。

用法：
  python make_intake.py <fields.json> <intake.docx> [--title "..."] [--note "..."]
  python make_intake.py --extract <filled.docx> <fields.json> [--out <values.json>]

fields.json 结构见 references/intake_form_schema.md。
"""
import sys
import json
import argparse

from docx import Document
from docx.shared import Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_COLOR_INDEX
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

HIGHLIGHT = WD_COLOR_INDEX.YELLOW          # 示例数据统一黄色高亮
NAVY = (0x1F, 0x38, 0x64)
GREY = (0x59, 0x59, 0x59)
CN_FONT = '微软雅黑'

HEADERS = ['序号', '信息项', '请填写（黄色=示例，请替换为真实值）', '口径 / 说明']


def set_cn_font(run, name=CN_FONT, size=None, bold=None, color=None):
    run.font.name = name
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn('w:rFonts'))
    if rfonts is None:
        rfonts = OxmlElement('w:rFonts')
        rpr.append(rfonts)
    rfonts.set(qn('w:eastAsia'), name)
    if size is not None:
        run.font.size = Pt(size)
    if bold is not None:
        run.font.bold = bold
    if color is not None:
        run.font.color.rgb = RGBColor(*color)


def shade_cell(cell, hex_fill):
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement('w:shd')
    shd.set(qn('w:val'), 'clear')
    shd.set(qn('w:fill'), hex_fill)
    tcPr.append(shd)


def write_cell(cell, text, size=9, bold=False, color=None, highlight=False):
    cell.text = ''
    p = cell.paragraphs[0]
    r = p.add_run('' if text is None else str(text))
    set_cn_font(r, size=size, bold=bold, color=color)
    if highlight:
        r.font.highlight_color = HIGHLIGHT
    return p


def build(fields, out, title=None, note=None):
    doc = Document()
    normal = doc.styles['Normal']
    normal.font.name = CN_FONT
    normal.font.size = Pt(10)
    normal._element.rPr.rFonts.set(qn('w:eastAsia'), CN_FONT)

    # 标题
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_cn_font(p.add_run(title or fields.get('title', '待补充信息采集表')),
                size=17, bold=True, color=NAVY)

    # 说明（强调高亮含义）
    note_text = note or fields.get('note', '')
    if note_text:
        np = doc.add_paragraph()
        set_cn_font(np.add_run(note_text), size=10, color=GREY)
    doc.add_paragraph()

    for gi, group in enumerate(fields.get('groups', []), start=1):
        hp = doc.add_paragraph()
        set_cn_font(hp.add_run('【%s】' % group.get('name', '第%d组' % gi)),
                    size=12, bold=True, color=NAVY)

        table = doc.add_table(rows=1, cols=len(HEADERS))
        table.style = 'Table Grid'
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        for i, h in enumerate(HEADERS):
            write_cell(table.rows[0].cells[i], h, size=9, bold=True)
            shade_cell(table.rows[0].cells[i], 'D9E2F3')

        for fi, f in enumerate(group.get('fields', []), start=1):
            cells = table.add_row().cells
            write_cell(cells[0], fi, size=9)
            label = str(f.get('label', '')) + (' *' if f.get('required') else '')
            write_cell(cells[1], label, size=9, bold=bool(f.get('required')))

            sample = f.get('sample', '')
            has_sample = sample not in ('', None)
            write_cell(cells[2], sample if has_sample else '　',
                       size=9, highlight=has_sample)

            hint = str(f.get('hint', ''))
            if f.get('sample_from'):
                hint = (hint + ' ｜示例来源：' + str(f['sample_from'])).strip(' ｜')
            write_cell(cells[3], hint, size=8, color=GREY)
        doc.add_paragraph()

    # 确认区
    cp = doc.add_paragraph()
    set_cn_font(cp.add_run('填写确认：本人 / 本企业确认以上信息真实、准确、完整。'),
                size=10, bold=True)
    sp = doc.add_paragraph()
    set_cn_font(sp.add_run('签字 / 盖章：____________________　　　日期：________年____月____日'),
                size=10)

    doc.save(out)
    return {
        'out': out,
        'groups': len(fields.get('groups', [])),
        'fields': sum(len(g.get('fields', [])) for g in fields.get('groups', [])),
        'highlight': 'yellow=示例数据',
    }


def extract(fields_path, docx_path, out_path=None):
    """按 fields.json 结构从填好的采集表读回值；高亮与否不影响读取。"""
    fields = json.load(open(fields_path, encoding='utf-8'))
    doc = Document(docx_path)
    values = {}
    for gi, group in enumerate(fields.get('groups', [])):
        if gi >= len(doc.tables):
            break
        table = doc.tables[gi]
        for fi, f in enumerate(group.get('fields', []), start=1):
            if fi >= len(table.rows):
                continue
            cells = table.rows[fi].cells
            if len(cells) < 3:
                continue
            key = f.get('key') or f.get('label')
            values[key] = cells[2].text.strip().lstrip('\u3000').strip()
    if out_path:
        json.dump(values, open(out_path, 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=2)
    return {'count': len(values), 'out': out_path, 'values': values}


def main():
    ap = argparse.ArgumentParser(description='生成 / 回收信息采集表')
    ap.add_argument('a', help='生成: fields.json；回收: 填好的 intake.docx')
    ap.add_argument('b', help='生成: 输出 intake.docx；回收: fields.json')
    ap.add_argument('--title')
    ap.add_argument('--note')
    ap.add_argument('--extract', action='store_true', help='从填好的采集表读回值')
    ap.add_argument('--out', help='回收模式输出 values.json 的路径')
    args = ap.parse_args()

    if args.extract:
        res = extract(args.b, args.a, args.out)
    else:
        fields = json.load(open(args.a, encoding='utf-8'))
        res = build(fields, args.b, args.title, args.note)
    print(json.dumps(res, ensure_ascii=False))


if __name__ == '__main__':
    main()
