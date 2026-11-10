#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
extract_tender.py — 甲方招标书解析器（文件操作层，全部由脚本完成）

用途
    把甲方招标文件（.docx / .pdf / .xlsx）解析成结构化 JSON 考题清单，
    供大模型做「判断与内容输出」时使用。大模型不直接读原始文档。

支持
    - .docx  正文段落 + 表格（按文档流顺序），识别章节标题
    - .pdf   PyMuPDF 逐页文本
    - .xlsx  逐 sheet 单元格（非空行）

用法
    python extract_tender.py <文件路径> [<文件路径> ...] --out <输出目录>
    python extract_tender.py <文件> --out out/ --format json|md
    python extract_tender.py <文件> --out out/ --grep "第五章"   # 只看命中上下文

输出
    <输出目录>/tender_extracted.json   结构化全文（含章节树、表格）
    <输出目录>/tender_text.md           便于人读的纯文本版
    stdout                             摘要统计 + 可选 grep 命中

设计原则
    解析是纯机械的，**不做任何判断**。判断交给大模型。
"""
import argparse
import json
import os
import re
import sys

# ---------------------------------------------------------------- 章节识别

# 招标文件常见章节标题（中英文数字混排、点号、括号）
CHAPTER_PATTERNS = [
    r'^第[一二三四五六七八九十百零〇0-9]+[章篇]\s*[、.]?\s*\S+',
    r'^[一二三四五六七八九十]+\s*[、.．]\s*\S+',
    r'^\d+(\.\d+)*\s*[、.．]?\s*\S{2,}$',
    r'^（[一二三四五六七八九十]+）\s*\S+',
    r'^\([一二三四五六七八九十]+\)\s*\S+',
]
# 明确不值得进正文、但要单独抽取的字段型目录/表格
SKIP_TOC = re.compile(r'^\s*(目\s*录|目\s*录\s*)$')

# 采购清单表头关键词（用于识别清单表）
BOQ_HEADER_KEYS = ['材料（或设备）名称', '暂估数量', '技术参数', '规格要求', '单位', '数量']


def _is_heading(text):
    t = text.strip()
    if not t or SKIP_TOC.match(t):
        return False
    # 目录行（含连续页码）不算标题
    if re.search(r'\.{3,}\s*\d+\s*$', t) or re.search(r'\t\d+$', t):
        return False
    if len(t) > 60:
        return False
    for pat in CHAPTER_PATTERNS:
        if re.match(pat, t):
            # 「1.1.2 | 条款号 | ...」这类表格内文本不在段落流中，此处只处理段落
            return True
    return False


def _clean(s):
    if s is None:
        return ''
    s = str(s).replace('\r', '').replace('\xa0', ' ').strip()
    return s


# ---------------------------------------------------------------- 各格式解析

def parse_docx(path):
    """DOCX → {'blocks': [...]}，block 为 {'type':'h'|'p'|'table', ...}"""
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    d = docx.Document(path)
    blocks = []
    for child in d.element.body.iterchildren():
        tag = child.tag.split('}')[-1]
        if tag == 'p':
            t = _clean(Paragraph(child, d).text)
            if not t:
                continue
            if _is_heading(t):
                blocks.append({'type': 'h', 'level': _heading_level(t), 'text': t})
            else:
                blocks.append({'type': 'p', 'text': t})
        elif tag == 'tbl':
            tb = Table(child, d)
            rows = []
            for r in tb.rows:
                cells = []
                for c in r.cells:
                    cells.append(_clean(c.text.replace('\n', ' / ')))
                rows.append(cells)
            if not rows:
                continue
            # 合并掉全空行
            rows = [r for r in rows if any(x for x in r)]
            if rows:
                blocks.append({'type': 'table', 'rows': rows,
                               'nrows': len(rows), 'ncols': max(len(r) for r in rows)})
    return {'blocks': blocks}


def _heading_level(t):
    if re.match(r'^第[一二三四五六七八九十百零〇0-9]+[章篇]', t):
        return 1
    if re.match(r'^[一二三四五六七八九十]+\s*[、.．]', t):
        return 2
    if re.match(r'^（[一二三四五六七八九十]+）', t):
        return 3
    return 3


def parse_pdf(path):
    """PDF → {'blocks': [...]}（逐页文本，段落按空行切分）"""
    import fitz  # PyMuPDF
    doc = fitz.open(path)
    blocks = []
    for pno in range(doc.page_count):
        page = doc.load_page(pno)
        txt = page.get_text('text')
        for para in re.split(r'\n\s*\n', txt):
            t = _clean(para.replace('\n', ' '))
            if not t:
                continue
            if _is_heading(t):
                blocks.append({'type': 'h', 'level': _heading_level(t), 'text': t,
                               'page': pno + 1})
            else:
                blocks.append({'type': 'p', 'text': t, 'page': pno + 1})
    doc.close()
    return {'blocks': blocks}


def parse_xlsx(path):
    """XLSX → {'blocks': [...]}（逐 sheet 非空行）"""
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    blocks = []
    for ws in wb.worksheets:
        blocks.append({'type': 'h', 'level': 1,
                       'text': '【工作表】%s' % ws.title})
        for r, row in enumerate(ws.iter_rows(values_only=True), start=1):
            vals = [_clean(v) for v in row]
            while vals and vals[-1] == '':
                vals.pop()
            if not any(vals):
                continue
            blocks.append({'type': 'row', 'sheet': ws.title, 'rownum': r,
                           'text': ' | '.join(vals)})
    wb.close()
    return {'blocks': blocks}


PARSERS = {'.docx': parse_docx, '.pdf': parse_pdf, '.xlsx': parse_xlsx,
           '.xls': parse_xlsx}


# ---------------------------------------------------------------- 识别采购清单

def find_boq_tables(blocks):
    """在 docx blocks 中定位采购清单表（表头含 暂估数量/材料名称 等关键词）"""
    hits = []
    for i, b in enumerate(blocks):
        if b.get('type') != 'table':
            continue
        head = ' '.join(b['rows'][0])
        for r in b['rows'][:3]:
            head += ' ' + ' '.join(r)
        score = sum(1 for k in BOQ_HEADER_KEYS if k in head)
        if score >= 3:
            hits.append({'block_index': i, 'score': score, 'rows': b['rows']})
    return hits


def find_tables_by_keywords(blocks, keywords, min_score=1):
    """按关键词定位表格，返回 [{block_index, matched:[kw], rows}]"""
    out = []
    for i, b in enumerate(blocks):
        if b.get('type') != 'table':
            continue
        flat = ' '.join(' '.join(r) for r in b['rows'])
        matched = [k for k in keywords if k in flat]
        if len(matched) >= min_score:
            out.append({'block_index': i, 'matched': matched, 'rows': b['rows']})
    return out


# ---------------------------------------------------------------- 章节树

def build_section_tree(blocks):
    """由 h 块构建章节树 [{title, level, start, end}]"""
    marks = [(i, b) for i, b in enumerate(blocks) if b.get('type') == 'h']
    tree = []
    for k, (i, b) in enumerate(marks):
        end = marks[k + 1][0] if k + 1 < len(marks) else len(blocks)
        tree.append({'title': b['text'], 'level': b.get('level', 3),
                     'start_block': i, 'end_block': end})
    return tree


def slice_section(blocks, tree, title_kw):
    """按标题关键词截取章节正文（返回该章节下的 blocks）"""
    for s in tree:
        if title_kw in s['title']:
            return blocks[s['start_block'] + 1:s['end_block']]
    return []


# ---------------------------------------------------------------- 输出

def blocks_to_md(blocks):
    out = []
    for b in blocks:
        if b['type'] == 'h':
            out.append('\n' + '#' * min(b.get('level', 3), 6) + ' ' + b['text'] + '\n')
        elif b['type'] == 'p':
            out.append(b['text'])
        elif b['type'] == 'row':
            out.append('[%s!R%d] %s' % (b.get('sheet', ''), b.get('rownum', 0), b['text']))
        elif b['type'] == 'table':
            rows = b['rows']
            out.append('\n【表格 %d行×%d列】' % (len(rows), b.get('ncols', 0)))
            for r in rows:
                out.append('| ' + ' | '.join(r) + ' |')
            out.append('')
        out.append('')
    return '\n'.join(out)


def summarize(doc, path):
    blocks = doc['blocks']
    n_p = sum(1 for b in blocks if b['type'] == 'p')
    n_t = sum(1 for b in blocks if b['type'] == 'table')
    n_h = sum(1 for b in blocks if b['type'] == 'h')
    n_r = sum(1 for b in blocks if b['type'] == 'row')
    return {
        'file': os.path.basename(path),
        'path': os.path.abspath(path),
        'ext': os.path.splitext(path)[1].lower(),
        'blocks_total': len(blocks),
        'paragraphs': n_p,
        'tables': n_t,
        'headings': n_h,
        'xlsx_rows': n_r,
        'chars': sum(len(b.get('text', '')) for b in blocks)
                  + sum(len(' '.join(r)) for b in blocks if b['type'] == 'table'
                        for r in b['rows']),
    }


def main():
    ap = argparse.ArgumentParser(description='甲方招标书解析器')
    ap.add_argument('files', nargs='+', help='招标文件路径（docx/pdf/xlsx）')
    ap.add_argument('--out', required=True, help='输出目录')
    ap.add_argument('--format', default='json', choices=['json', 'md', 'both'])
    ap.add_argument('--grep', default=None,
                    help='仅输出包含该关键词的段落及其前后文（调试用）')
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    all_docs = []
    md_parts = []

    for f in args.files:
        if not os.path.isfile(f):
            print('[跳过] 文件不存在: %s' % f, file=sys.stderr)
            continue
        ext = os.path.splitext(f)[1].lower()
        parser = PARSERS.get(ext)
        if not parser:
            print('[跳过] 不支持格式: %s' % f, file=sys.stderr)
            continue
        try:
            doc = parser(f)
        except Exception as e:
            print('[失败] %s -> %s' % (f, e), file=sys.stderr)
            continue
        blocks = doc['blocks']
        tree = build_section_tree(blocks)
        entry = summarize(doc, f)
        entry['section_tree'] = tree
        entry['boq_tables'] = find_boq_tables(blocks)
        entry['key_tables'] = find_tables_by_keywords(
            blocks,
            ['条款号', '评审因素', '评分标准', '要求', '应答', '偏离',
             '投标人资格要求', '招标范围', '投标文件组成'])
        all_docs.append(entry)
        md_parts.append('# ===== %s =====\n\n%s' % (os.path.basename(f),
                                                    blocks_to_md(blocks)))
        print('[解析] %s → %d 块 / %d 表 / %d 标题 / %d 字符' % (
            os.path.basename(f), entry['blocks_total'], entry['tables'],
            entry['headings'], entry['chars']))

    if not all_docs:
        print('[错误] 无任何文件解析成功', file=sys.stderr)
        return 2

    out_json = os.path.join(args.out, 'tender_extracted.json')
    with open(out_json, 'w', encoding='utf-8') as fh:
        json.dump({'documents': all_docs}, fh, ensure_ascii=False, indent=2)

    out_md = os.path.join(args.out, 'tender_text.md')
    with open(out_md, 'w', encoding='utf-8') as fh:
        fh.write('\n\n'.join(md_parts))

    print('[输出] %s' % out_json)
    print('[输出] %s' % out_md)

    # 采购清单定位提示
    for entry in all_docs:
        for hit in entry['boq_tables']:
            print('[清单] %s → 块#%d，%d 行 × %d 列' % (
                entry['file'], hit['block_index'], len(hit['rows']),
                max(len(r) for r in hit['rows'])))

    if args.grep:
        print('\n===== grep: %s =====' % args.grep)
        for entry in all_docs:
            for i, b in enumerate(entry.get('_blocks_cache', [])):
                pass
        # 重新解析以支持 grep（避免重复 IO 缓存问题）
        for f in args.files:
            if not os.path.isfile(f):
                continue
            ext = os.path.splitext(f)[1].lower()
            p = PARSERS.get(ext)
            if not p:
                continue
            blocks = p(f)['blocks']
            for i, b in enumerate(blocks):
                blob = b.get('text', '') if b['type'] != 'table' \
                    else ' '.join(' '.join(r) for r in b['rows'])
                if args.grep in blob:
                    lo, hi = max(0, i - 1), min(len(blocks), i + 2)
                    print('\n[%s] 块#%d' % (os.path.basename(f), i))
                    for j in range(lo, hi):
                        bb = blocks[j]
                        if bb['type'] == 'table':
                            print('  [表] %d行' % len(bb['rows']))
                            for r in bb['rows'][:6]:
                                print('       ' + ' | '.join(r)[:200])
                        else:
                            print('  [%s] %s' % (bb['type'], bb.get('text', '')[:400]))
    return 0


if __name__ == '__main__':
    sys.exit(main())
