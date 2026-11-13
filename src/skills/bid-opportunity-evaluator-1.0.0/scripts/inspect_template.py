#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
inspect_template.py — 评估报告模版解析器（文件操作层）

用途
    解析用户/资料库提供的 Word 模版，提取其**结构骨架**，
    使填充引擎可以「按模版的章节与表格来写」，而不是自造章节。

提取内容
    - 章节序列：标题文本 + 层级（依�� Heading 样式或编号形态判定）
    - 每章附属表格：表头 + 行数（用于按表头语义匹配并填行）
    - 占位符：形如 {{key}} 的文本块（可直接替换）
    - 封面/页眉页脚信息

用法
    python inspect_template.py <模版.docx> --out <目录>
    python inspect_template.py <模版.docx> --out <目录> --json

输出
    <目录>/template_structure.json   模版结构描述
"""
import argparse
import json
import os
import re
import sys

# 中文编号形态 → 层级
CN_NUM_MAP = {
    '一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8,
    '九': 9, '十': 10,
}
PLACEHOLDER_RE = re.compile(r'\{\{\s*([^{}]+?)\s*\}\}')


def _is_heading(p):
    """判定段落是否为标题"""
    style = (p.style.name or '') if p.style is not None else ''
    if style.lower().startswith('heading'):
        m = re.search(r'heading\s*(\d+)', style.lower())
        return int(m.group(1)) if m else 1
    if style in ('Title', '标题'):
        return 1
    t = p.text.strip()
    if not t or len(t) > 60:
        return None
    # 「一、」「二、」「（一）」「1.」「1.1」「第一章」
    if re.match(r'^第[一二三四五六七八九十百零〇\d]+[章篇部分]', t):
        return 1
    if re.match(r'^[一二三四五六七八九十]+\s*[、．.]', t):
        return 1
    if re.match(r'^[（(][一二三四五六七八九十\d]+[）)]', t):
        return 2
    if re.match(r'^\d+\.\d+\s*[、．.]?\s*\S', t):
        return 2
    if re.match(r'^\d+\s*[、．.]\s*\S', t):
        return 3
    return None


def _head_key(title):
    """章节标题归一化：用于与内容块做语义匹配。

    去掉编号前缀与标点，保留核心词，便于「资格条件核对」匹配「资格门槛」这类近义。
    """
    t = title.strip()
    t = re.sub(r'^第[一二三四五六七八九十百零〇\d]+[章篇部分]\s*', '', t)
    t = re.sub(r'^[一二三四五六七八九十\d]+\s*[、．.]\s*', '', t)
    t = re.sub(r'^[（(][一二三四五六七八九十\d]+[）)]\s*', '', t)
    t = re.sub(r'^\d+(\.\d+)*\s*[、．.]?\s*', '', t)
    t = re.sub(r'[\s　]', '', t)
    return t


def inspect(path):
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    d = docx.Document(path)
    sections = []
    cur = None
    placeholders = []
    body = d.element.body

    for child in body.iterchildren():
        tag = child.tag.split('}')[-1]
        if tag == 'p':
            p = Paragraph(child, d)
            t = p.text.strip()
            for m in PLACEHOLDER_RE.finditer(t):
                placeholders.append({'key': m.group(1), 'context': t[:120]})
            lvl = _is_heading(p)
            if lvl:
                cur = {'title': t, 'level': lvl, 'key': _head_key(t),
                       'paragraphs': [], 'tables': []}
                sections.append(cur)
            elif cur is not None and t:
                cur['paragraphs'].append(t)
        elif tag == 'tbl':
            tb = Table(child, d)
            rows = [[c.text.strip().replace('\n', ' / ') for c in r.cells]
                    for r in tb.rows]
            if not rows:
                continue
            tbl = {'header': rows[0], 'rows': len(rows) - 1,
                   'ncols': max(len(r) for r in rows),
                   'sample_row': rows[1] if len(rows) > 1 else []}
            if cur is not None:
                cur['tables'].append(tbl)
            else:
                sections.append({'title': '(正文前表格)', 'level': 0,
                                 'key': '', 'paragraphs': [], 'tables': [tbl]})

    # 页眉页脚
    hdr = ''
    ftr = ''
    try:
        if d.sections:
            hdr = d.sections[0].header.paragraphs[0].text.strip()
            ftr = d.sections[0].footer.paragraphs[0].text.strip()
    except Exception:
        pass

    # 页面设置
    page = {}
    try:
        s = d.sections[0]
        page = {'width_cm': round(s.page_width.cm, 2),
                'height_cm': round(s.page_height.cm, 2),
                'margin_top_cm': round(s.top_margin.cm, 2),
                'margin_bottom_cm': round(s.bottom_margin.cm, 2),
                'margin_left_cm': round(s.left_margin.cm, 2),
                'margin_right_cm': round(s.right_margin.cm, 2)}
    except Exception:
        pass

    return {
        'template_path': os.path.abspath(path),
        'template_name': os.path.basename(path),
        'page': page,
        'header_text': hdr,
        'footer_text': ftr,
        'sections': sections,
        'section_count': len(sections),
        'placeholders': placeholders,
        'has_placeholders': bool(placeholders),
    }


def summarize(st):
    L = []
    L.append('模版：%s' % st['template_name'])
    L.append('页面：%s' % (st['page'] or '未识别'))
    L.append('章节数：%d；占位符：%d 个' % (st['section_count'],
                                          len(st['placeholders'])))
    L.append('')
    L.append('章节骨架：')
    for s in st['sections']:
        L.append('  L%d  %s%s' % (s['level'], s['title'],
                                   '  [表×%d]' % len(s['tables']) if s['tables'] else ''))
        for t in s['tables']:
            L.append('        表头: %s' % ' | '.join(t['header'])[:110])
    if st['placeholders']:
        L.append('')
        L.append('占位符：')
        for p in st['placeholders']:
            L.append('  {{%s}}  ← %s' % (p['key'], p['context'][:70]))
    return '\n'.join(L)


def main():
    ap = argparse.ArgumentParser(description='评估报告模版解析器')
    ap.add_argument('template', help='模版 docx 路径')
    ap.add_argument('--out', required=True, help='输出目录')
    ap.add_argument('--json', action='store_true', help='只输出 JSON 到 stdout')
    args = ap.parse_args()

    if not os.path.isfile(args.template):
        print('[错误] 模版不存在: %s' % args.template, file=sys.stderr)
        return 2
    if not args.template.lower().endswith('.docx'):
        print('[错误] 仅支持 .docx 模版（不支持 .doc/.pdf）', file=sys.stderr)
        return 2

    try:
        st = inspect(args.template)
    except Exception as e:
        print('[错误] 模版解析失败: %s' % e, file=sys.stderr)
        return 2

    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, 'template_structure.json')
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(st, fh, ensure_ascii=False, indent=2)

    if args.json:
        print(json.dumps(st, ensure_ascii=False, indent=2))
    else:
        print(summarize(st))
    print('[输出] %s' % path)
    return 0


if __name__ == '__main__':
    sys.exit(main())
