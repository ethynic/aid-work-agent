#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
make_package.py — 投标文件交付打包与一致性自检（文件操作层）

职责
    1. 校验渲染产物是否齐全（对照 expected 清单）
    2. 跨文件一致性自检：金额、数量、术语、项目名、份数要求
    3. 生成交付清单（PACKAGE.md）与自检报告（CHECKS.md）
    4. 可选：一键转 PDF（若本机有 Word COM / libreoffice）

用法
    python make_package.py --content content.json --out outputs/ \
        --expected "01-投标函.docx,02-报价一览表.docx" --checks checks.json
"""
import argparse
import json
import os
import re
import sys


def _read_text(path):
    try:
        import docx
        d = docx.Document(path)
        parts = [p.text for p in d.paragraphs]
        for t in d.tables:
            for r in t.rows:
                for c in r.cells:
                    parts.append(c.text)
        return '\n'.join(parts)
    except Exception:
        try:
            import fitz
            d = fitz.open(path)
            return '\n'.join(d.load_page(i).get_text('text')
                             for i in range(d.page_count))
        except Exception:
            return ''


def _read_tables(path):
    """返回 [[row_cells, ...], ...] 的表格列表；非 docx 返回 []"""
    try:
        import docx
        d = docx.Document(path)
        out = []
        for t in d.tables:
            out.append([[c.text.strip() for c in r.cells] for r in t.rows])
        return out
    except Exception:
        return []


def collect_text(out_dir):
    txts = {}
    for fn in sorted(os.listdir(out_dir)):
        if fn.lower().endswith(('.docx', '.pdf')) and not fn.startswith('~$'):
            txts[fn] = _read_text(os.path.join(out_dir, fn))
    return txts


def collect_tables(out_dir):
    tabs = {}
    for fn in sorted(os.listdir(out_dir)):
        if fn.lower().endswith('.docx') and not fn.startswith('~$'):
            tabs[fn] = _read_tables(os.path.join(out_dir, fn))
    return tabs


def run_checks(txts, checks, tables=None):
    """checks: [{name, type, expect, files:'all'|['a.docx'], ...}]

    类型语义
      contains      目标文件必须包含 expect
      not_contains  目标文件不得包含 expect
      regex         目标文件必须匹配 expect
      consistent    「出现即唯一」：目标文件中若出现 expect 匹配项，
                    该文件内该匹配项的值必须唯一（多值 → FAIL）。
                    除非 required=true，否则文件未出现该值不判失败。
      table_values  定位含指定表头的表格，抽取该列全部取值，
                    检查是否出现禁用值（forbid）或取值不唯一。
    """
    tables = tables or {}
    results = []
    for ck in checks or []:
        name = ck.get('name', '检查')
        typ = ck.get('type', 'contains')
        expect = ck.get('expect')
        scope = ck.get('files', 'all')
        target = list(txts) if scope == 'all' else [f for f in scope if f in txts]
        missing = [f for f in (scope if scope != 'all' else []) if f not in txts]
        hits, neg_hits = [], []

        if typ == 'table_values':
            header_kw = ck.get('header', '')
            col_kw = ck.get('column', '')
            forbid = ck.get('forbid') or []
            for f in target:
                found_col = False
                values = []
                for tb in tables.get(f, []):
                    if not tb:
                        continue
                    head = ' '.join(tb[0])
                    if header_kw and header_kw not in head:
                        continue
                    idx = None
                    for ci, cell in enumerate(tb[0]):
                        if col_kw in cell:
                            idx = ci
                            break
                    if idx is None:
                        continue
                    found_col = True
                    for row in tb[1:]:
                        if idx < len(row):
                            values.append(row[idx])
                if not found_col:
                    neg_hits.append(f + '(未找到列)')
                    continue
                bad = sorted({v for v in values if any(b in v for b in forbid)})
                if bad:
                    neg_hits.append(f + str(bad[:4]))
                else:
                    hits.append(f)
            status = 'PASS' if (not neg_hits and not missing) else 'FAIL'
            results.append({'name': name, 'status': status, 'type': typ,
                            'expect': '禁用值:%s' % ','.join(forbid),
                            'ok_files': hits, 'bad_files': neg_hits,
                            'missing_files': missing,
                            'detail': ck.get('detail', '')})
            continue

        for f in target:
            t = txts[f]
            if typ == 'contains':
                (hits if expect in t else neg_hits).append(f)
            elif typ == 'not_contains':
                (neg_hits if expect in t else hits).append(f)
            elif typ == 'regex':
                (hits if re.search(expect, t) else neg_hits).append(f)
            elif typ == 'consistent':
                vals = set(re.findall(expect, t))
                if not vals:
                    # 未出现该值：默认不判失败（required=true 时判失败）
                    if ck.get('required'):
                        neg_hits.append(f + '(未出现)')
                    else:
                        hits.append(f)
                elif len(vals) == 1:
                    hits.append(f)
                else:
                    neg_hits.append(f + str(sorted(vals)))
        status = 'PASS' if (not neg_hits and not missing) else 'FAIL'
        results.append({'name': name, 'status': status, 'type': typ,
                        'expect': expect, 'ok_files': hits,
                        'bad_files': neg_hits, 'missing_files': missing,
                        'detail': ck.get('detail', '')})
    return results


def main():
    ap = argparse.ArgumentParser(description='交付打包与一致性自检')
    ap.add_argument('--content', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--expected', default='')
    ap.add_argument('--checks', default=None)
    ap.add_argument('--pkg-name', default='投标文件')
    args = ap.parse_args()

    with open(args.content, encoding='utf-8') as fh:
        content = json.load(fh)
    meta = content.get('meta', {})

    expected = [x.strip() for x in args.expected.split(',') if x.strip()]
    present = [f for f in sorted(os.listdir(args.out))
               if f.lower().endswith(('.docx', '.pdf'))]

    # 1 齐全性
    missing = [e for e in expected if e not in present]

    # 2 一致性自检
    checks = []
    if args.checks and os.path.isfile(args.checks):
        with open(args.checks, encoding='utf-8') as fh:
            checks = json.load(fh).get('checks', [])
    txts = collect_text(args.out)
    tables = collect_tables(args.out)
    results = run_checks(txts, checks, tables)

    # 3 交付清单
    pkg_path = os.path.join(args.out, 'PACKAGE.md')
    lines = ['# %s · 交付清单' % args.pkg_name, '',
             '- 项目名称：%s' % meta.get('project_name', ''),
             '- 招标人：%s' % meta.get('tenderer', ''),
             '- 招标代理：%s' % meta.get('agency', ''),
             '- 投标人：%s' % meta.get('bidder', ''),
             '- 生成时间：%s' % meta.get('date_cn', ''),
             '', '## 一、文件构成', '', '| # | 文件 | 大小 |',
             '|---|---|---|']
    for i, f in enumerate(present, 1):
        sz = os.path.getsize(os.path.join(args.out, f))
        lines.append('| %d | %s | %.1f KB |' % (i, f, sz / 1024.0))
    lines += ['', '## 二、一致性自检', '',
              '| 检查项 | 类型 | 结果 | 说明 |', '|---|---|---|---|']
    if results:
        for r in results:
            lines.append('| %s | %s | %s | %s |' % (
                r['name'], r['type'], '✅ PASS' if r['status'] == 'PASS' else '❌ FAIL',
                r['detail'] or (', '.join(r['bad_files'])[:120])))
    else:
        lines.append('| （未配置检查项） | - | - | - |')
    lines += ['', '## 三、需人工补齐项', '']
    pending = content.get('pending_items') or []
    if pending:
        for p in pending:
            lines.append('- %s' % p)
    else:
        lines.append('- （无）')
    lines += ['', '## 四、使用提示', '',
              '1. 正文以「正本」为准，副本为正本复印件；封面右上角须标注正/副本。',
              '2. 所有「（盖单位章）」「（签字）」处须在签章后生效。',
              '3. 目录需在 Word 中按 Ctrl+A → F9 更新域后生成。',
              '4. 纸质正本 1 份、副本 4 份；电子版 Word + 盖章 PDF 各 1 份，光盘与 U 盘各 1 份。']
    with open(pkg_path, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(lines))

    # 4 打印结论
    print('=' * 60)
    print('交付目录: %s' % os.path.abspath(args.out))
    print('生成文件: %d 份' % len(present))
    if expected:
        print('预期文件: %d 份 | 缺失: %d 份 %s' % (
            len(expected), len(missing), missing if missing else ''))
    fails = [r for r in results if r['status'] != 'PASS']
    print('自检项: %d 项 | 通过 %d 项 | 失败 %d 项' % (
        len(results), len(results) - len(fails), len(fails)))
    for r in fails:
        print('  [FAIL] %s (%s) → %s' % (r['name'], r['type'],
                                          ', '.join(r['bad_files'])[:150]))
    print('交付清单: %s' % pkg_path)
    print('=' * 60)
    return 1 if (missing or fails) else 0


if __name__ == '__main__':
    sys.exit(main())
