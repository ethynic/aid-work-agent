#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
check_layout.py — 版式与甲方格式要求一致性校验（文件操作层）

职责
    把「甲方招标书中的格式条款」与「本次实际使用的 layout.json」逐条比对，
    确认版式满足招标要求，且每个版式字段都有出处（甲方要求 / 历史标书）。

用法
    python check_layout.py --layout work/layout.json \
        --requirements work/requirements.json \
        [--history work/layout_history.json] \
        [--report work/LAYOUT_CHECK.md] [--json]

判定
    PASS  招标要求已满足
    FAIL  招标要求明确但 layout 未满足（必须修）
    WARN  招标书未规定、且无历史依据（用了兜底值，需人工确认）
"""
import argparse
import json
import os
import re
import sys

# 中文字号 → 磅值
SIZE_PT = {
    '初号': 42, '小初': 36, '一号': 26, '小一': 24, '二号': 22, '小二': 18,
    '三号': 16, '小三': 15, '四号': 14, '小四': 12, '五号': 10.5, '小五': 9,
    '六号': 7.5, '小六': 6.5,
}

# 招标书常见格式条款 → 校验规则
# 每条规则：pattern 命中 requirement 文本则执行 check
FONT_PATTERNS = [
    (r'["“]?宋体', '宋体'), (r'["“]?黑体', '黑体'), (r'["“]?楷体', '楷体'),
    (r'["“]?仿宋', '仿宋'), (r'["“]?Times\s*New\s*Roman', 'Times New Roman'),
    (r'["“]?Arial', 'Arial'),
]

# 中文数字 → 阿拉伯数字
CN_NUM = {'零': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5,
          '六': 6, '七': 7, '八': 8, '九': 9, '壹': 1, '贰': 2, '叁': 3,
          '肆': 4, '伍': 5, '陆': 6, '柒': 7, '捌': 8, '玖': 9, '拾': 10}


def cn_to_int(text):
    """中文数字串 → 整数（支持 十/拾、廿等常见写法）"""
    if not text:
        return None
    if text.isdigit():
        return int(text)
    t = text.replace('廿', '二十')
    total, section, number = 0, 0, 0
    for ch in t:
        if ch == '十' or ch == '拾':
            section += (number or 1) * 10
            number = 0
        elif ch in CN_NUM:
            number = CN_NUM[ch]
        else:
            return None
    total += section + number
    return total or None


def find_font(text):
    for pat, name in FONT_PATTERNS:
        if re.search(pat, text):
            return name
    return None


def find_size(text):
    for cn, pt in SIZE_PT.items():
        if cn in text:
            return cn, pt
    return None, None


def check_layout(layout, requirements, history=None):
    """返回 [{name, status, detail, source}]"""
    res = []
    fmt_reqs = [r for r in (requirements or [])
                if r.get('category') == 'format']
    alltext = ' '.join(r.get('requirement', '') for r in fmt_reqs)

    L = layout
    body = L.get('body', {})
    tbl = L.get('table', {})
    pg = L.get('page', {})
    cov = L.get('cover', {})
    toc = L.get('toc', {})
    sig = L.get('signature', {})
    copies = L.get('copies', {})

    # 1 字体
    want_font = find_font(alltext)
    if want_font:
        got = body.get('font')
        ok = (got == want_font)
        res.append({'name': '正文字体符合招标要求', 'status': 'PASS' if ok else 'FAIL',
                    'detail': '要求 %s；layout.body.font=%s' % (want_font, got)})
        if want_font in ('宋体', '黑体', '楷体', '仿宋'):
            got_t = tbl.get('font')
            ok2 = (got_t == want_font)
            res.append({'name': '表格字体符合招标要求', 'status': 'PASS' if ok2 else 'FAIL',
                        'detail': '要求 %s；layout.table.font=%s' % (want_font, got_t)})
    else:
        res.append({'name': '正文字体有出处', 'status': 'WARN',
                    'detail': '招标书未规定字体，layout=%s，来源需在 _source 标注'
                              % body.get('font')})

    # 2 字号
    want_cn, want_pt = find_size(alltext)
    if want_pt:
        got = body.get('size_pt')
        ok = (got is not None and abs(float(got) - want_pt) < 0.01)
        res.append({'name': '正文字号符合招标要求', 'status': 'PASS' if ok else 'FAIL',
                    'detail': '要求 %s(%gpt)；layout.body.size_pt=%s'
                              % (want_cn, want_pt, got)})
    else:
        res.append({'name': '正文字号有出处', 'status': 'WARN',
                    'detail': '招标书未规定字号，layout=%s' % body.get('size_pt')})

    # 3 纸张
    if re.search(r'A\s*4', alltext):
        ok = str(pg.get('size', '')).upper() == 'A4'
        res.append({'name': '纸张规格符合招标要求', 'status': 'PASS' if ok else 'FAIL',
                    'detail': '要求 A4；layout.page.size=%s' % pg.get('size')})
    else:
        res.append({'name': '纸张规格有出处', 'status': 'WARN',
                    'detail': '招标书未规定，layout=%s' % pg.get('size')})

    # 4 装订方式
    if re.search(r'胶装', alltext):
        b = pg.get('binding') or ''
        ok = '胶装' in b
        res.append({'name': '装订方式符合招标要求', 'status': 'PASS' if ok else 'FAIL',
                    'detail': '要求胶装；layout.page.binding=%s' % (b or '(空)')})
    if re.search(r'活页', alltext) and not re.search(r'不得采用活页|不得活页', alltext):
        res.append({'name': '装订方式符合招标要求', 'status': 'WARN',
                    'detail': '招标书提及活页，请确认 layout.page.binding 是否合规'})
    if re.search(r'不得采用活页|不得活页', alltext):
        b = pg.get('binding') or ''
        ok = '活页' not in b
        res.append({'name': '禁止活页装订', 'status': 'PASS' if ok else 'FAIL',
                    'detail': '招标书禁止活页；layout.page.binding=%s' % (b or '(空)')})

    # 5 正副本分开装订
    if re.search(r'正副本分开装订|正副本分别装订', alltext):
        ok = bool(pg.get('separate_copies'))
        res.append({'name': '正副本分开装订', 'status': 'PASS' if ok else 'FAIL',
                    'detail': 'layout.page.separate_copies=%s' % pg.get('separate_copies')})

    # 6 分册装订
    if re.search(r'不需要分册装订|不需分册', alltext):
        ok = not pg.get('multi_volume')
        res.append({'name': '分册装订设置', 'status': 'PASS' if ok else 'FAIL',
                    'detail': '招标书要求不分册；layout.page.multi_volume=%s'
                              % pg.get('multi_volume')})
    elif re.search(r'需要分册装订|需分册', alltext):
        ok = bool(pg.get('multi_volume'))
        res.append({'name': '分册装订设置', 'status': 'PASS' if ok else 'FAIL',
                    'detail': '招标书要求分册；layout.page.multi_volume=%s'
                              % pg.get('multi_volume')})

    # 7 份数（中英文数字均支持）
    m = re.search(r'纸质[^\d一二三四五六七八九十壹贰叁肆伍陆柒捌玖拾]{0,8}'
                  r'([\d一二三四五六七八九十壹贰叁肆伍陆柒捌玖拾]+)\s*份'
                  r'[^\d一二三四五六七八九十壹贰叁肆伍陆柒捌玖拾]{0,10}'
                  r'正本[^\d一二三四五六七八九十壹贰叁肆伍陆柒捌玖拾]{0,8}'
                  r'([\d一二三四五六七八九十壹贰叁肆伍陆柒捌玖拾]+)\s*份?'
                  r'[^\d一二三四五六七八九十壹贰叁肆伍陆柒捌玖拾]{0,10}'
                  r'副本[^\d一二三四五六七八九十壹贰叁肆伍陆柒捌玖拾]{0,8}'
                  r'([\d一二三四五六七八九十壹贰叁肆伍陆柒捌玖拾]+)\s*份', alltext)
    if m:
        orig = cn_to_int(m.group(2))
        cp = cn_to_int(m.group(3))
        got_o = copies.get('original')
        got_c = copies.get('copy')
        ok = (orig is not None and cp is not None
              and got_o == orig and got_c == cp)
        res.append({'name': '正副本份数符合招标要求', 'status': 'PASS' if ok else 'FAIL',
                    'detail': '要求 正本%s/副本%s；layout.copies=%s'
                              % (orig, cp, json.dumps(copies, ensure_ascii=False))})
    else:
        res.append({'name': '正副本份数有出处', 'status': 'WARN',
                    'detail': '未从招标书解析出份数；layout.copies=%s'
                              % json.dumps(copies, ensure_ascii=False)})

    # 8 封面正副本标注
    if re.search(r'正本.{0,10}副本|封面.{0,20}标注.{0,10}正本', alltext):
        ok = bool(cov.get('show_copy_mark'))
        res.append({'name': '封面标注正本/副本', 'status': 'PASS' if ok else 'FAIL',
                    'detail': 'layout.cover.show_copy_mark=%s（渲染时用 --copy-mark 加标注）'
                              % cov.get('show_copy_mark')})

    # 9 目录
    if re.search(r'编制目录|编目录|附目录', alltext):
        ok = bool(toc.get('enabled'))
        res.append({'name': '编制目录', 'status': 'PASS' if ok else 'FAIL',
                    'detail': 'layout.toc.enabled=%s' % toc.get('enabled')})

    # 10 页码
    if re.search(r'页码|页脚', alltext):
        ok = bool(L.get('footer', {}).get('page_number'))
        res.append({'name': '页码设置', 'status': 'PASS' if ok else 'FAIL',
                    'detail': 'layout.footer.page_number=%s'
                              % L.get('footer', {}).get('page_number')})

    # 11 签字盖章
    if re.search(r'签字.{0,10}盖章|签字并加盖|加盖.{0,6}公章', alltext):
        ok = bool(sig.get('need_sign_area'))
        res.append({'name': '签字盖章区设置', 'status': 'PASS' if ok else 'FAIL',
                    'detail': 'layout.signature.need_sign_area=%s' % ok})
        if re.search(r'逐页加盖', alltext):
            ok2 = bool(sig.get('per_page_seal'))
            res.append({'name': '逐页盖章要求', 'status': 'PASS' if ok2 else 'WARN',
                        'detail': '招标书要求逐页盖章；layout.signature.per_page_seal=%s'
                                  % sig.get('per_page_seal')})
        if re.search(r'骑缝章', alltext):
            ok3 = bool(sig.get('cross_page_seal'))
            res.append({'name': '骑缝章要求', 'status': 'PASS' if ok3 else 'WARN',
                        'detail': '招标书要求骑缝章；layout.signature.cross_page_seal=%s'
                                  % sig.get('cross_page_seal')})

    # 12 页数上限
    mp = pg.get('max_pages')
    if mp:
        res.append({'name': '页数上限已登记', 'status': 'PASS',
                    'detail': '上限 %s 页（%s）；须由 make_package.py 实测页数校验'
                              % (mp, pg.get('max_pages_scope') or '未注明范围')})
    else:
        res.append({'name': '页数上限有出处', 'status': 'WARN',
                    'detail': '招标书未规定页数上限'})

    # 13 版式来源标注
    src = L.get('_source', {})
    has_tender = bool(src.get('tender'))
    has_hist = bool(src.get('history'))
    ok = has_tender or has_hist
    res.append({'name': '版式来源已标注', 'status': 'PASS' if ok else 'FAIL',
                'detail': '甲方依据：%s；历史范式：%s' %
                          (src.get('tender') or '(未标注)',
                           src.get('history') or '(未标注)')})
    if has_tender and has_hist:
        res.append({'name': '版式同时参考甲方与历史标书', 'status': 'PASS',
                    'detail': '两个来源均已登记'})

    return res


def main():
    ap = argparse.ArgumentParser(description='版式合规校验')
    ap.add_argument('--layout', required=True)
    ap.add_argument('--requirements', default=None)
    ap.add_argument('--history', default=None,
                    help='历史标书版式依据（文本或 JSON），仅作记录')
    ap.add_argument('--report', default=None, help='输出 Markdown 报告路径')
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()

    if not os.path.isfile(args.layout):
        print('[错误] layout 文件不存在: %s' % args.layout, file=sys.stderr)
        return 2
    with open(args.layout, encoding='utf-8') as fh:
        layout = json.load(fh)

    reqs = []
    if args.requirements and os.path.isfile(args.requirements):
        with open(args.requirements, encoding='utf-8') as fh:
            rj = json.load(fh)
        reqs = rj.get('requirements', [])

    res = check_layout(layout, reqs)
    npass = sum(1 for r in res if r['status'] == 'PASS')
    nfail = sum(1 for r in res if r['status'] == 'FAIL')
    nwarn = sum(1 for r in res if r['status'] == 'WARN')

    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print('=' * 66)
        print('版式合规校验：%s' % os.path.basename(args.layout))
        print('-' * 66)
        for r in res:
            mark = {'PASS': '[PASS]', 'FAIL': '[FAIL]', 'WARN': '[WARN]'}[r['status']]
            print('%s %-28s %s' % (mark, r['name'], r['detail']))
        print('-' * 66)
        print('合计 %d 项：PASS %d / FAIL %d / WARN %d'
              % (len(res), npass, nfail, nwarn))
        print('=' * 66)

    if args.report:
        lines = ['# 版式合规校验报告', '',
                 '- 版式文件：`%s`' % os.path.basename(args.layout),
                 '- 甲方依据：%s' % (layout.get('_source', {}).get('tender') or '(未标注)'),
                 '- 历史范式：%s' % (layout.get('_source', {}).get('history') or '(未标注)'),
                 '- 说明：%s' % (layout.get('_source', {}).get('notes') or ''),
                 '', '| 校验项 | 结果 | 明细 |', '|---|---|---|']
        for r in res:
            lines.append('| %s | %s | %s |' % (r['name'], r['status'], r['detail']))
        lines += ['', '合计 %d 项：PASS %d / FAIL %d / WARN %d'
                  % (len(res), npass, nfail, nwarn)]
        with open(args.report, 'w', encoding='utf-8') as fh:
            fh.write('\n'.join(lines))
        print('[输出] %s' % os.path.abspath(args.report))

    return 1 if nfail else 0


if __name__ == '__main__':
    sys.exit(main())
