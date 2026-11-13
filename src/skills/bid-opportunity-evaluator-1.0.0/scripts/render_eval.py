#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
render_eval.py — 投标可行性评估报告渲染（文件操作层）

把 evaluation.json 渲染为规范的 Word 评估报告。
版式参数从 layout.json 读取（与 bid-doc-generator 同样的外部化原则）：
未提供则使用内置兜底版式并告警。

用法
    python render_eval.py --evaluation evaluation.json --out <目录> \
        [--layout layout.json] [--title "投标可行性评估报告"]
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from docx_kit import DocBuilder  # noqa: E402

# 兜底版式（甲方/历史标书未规定时使用，渲染时会告警）
FALLBACK_LAYOUT = {
    'body': {'font': '宋体', 'size_pt': 12, 'line_spacing': 1.5,
             'first_line_indent_chars': 2, 'align': 'justify'},
    'heading': {
        '1': {'font': '黑体', 'size_pt': 16, 'bold': True, 'align': 'center'},
        '2': {'font': '黑体', 'size_pt': 14, 'bold': True, 'align': 'left'},
        '3': {'font': '楷体', 'size_pt': 12, 'bold': True, 'align': 'left'},
    },
    'table': {'font': '宋体', 'size_pt': 9.5, 'header_shading': 'D9D9D9'},
    'note': {'font': '楷体', 'size_pt': 9.5},
}

DECISION_STYLE = {
    'GO': ('建议参与投标', 'E2EFDA'),
    'CAUTION': ('谨慎参与（须落实补偿措施后决策）', 'FFF2CC'),
    'NO_GO': ('不建议参与投标', 'FCE4E4'),
}


def load_layout(path):
    if path and os.path.isfile(path):
        with open(path, encoding='utf-8') as fh:
            return json.load(fh), False
    return FALLBACK_LAYOUT, True


def build_report(ev, out_path, title, layout):
    proj = ev['project']
    dec = ev['decision']
    label, shade = DECISION_STYLE.get(dec['code'], (dec['label'], 'FFFFFF'))

    b = DocBuilder(header_text=proj.get('项目名称', ''),
                   footer_text='投标可行性评估报告')

    # 封面
    b.cover(proj.get('项目名称', ''), title,
            ev.get('assessment', {}).get('company', '') or ev.get('company_name', ''),
            ev.get('evaluated_at', ''),
            extra_lines=['评估结论：%s' % label,
                         '综合评分：%.1f / 100' % ev['score_total']])

    b.toc()

    # 一、评估结论
    b.h1('一、评估结论')
    b.table(['评估项', '结论'], [
        ['评估结论', label],
        ['综合评分', '%.1f / 100' % ev['score_total']],
        ['判定理由', dec['reason']],
        ['投标截止时间', ev['time'].get('deadline') or '未识别'],
        ['时间可行性', ev['time'].get('verdict', '')],
        ['硬性门槛', '共 %d 项，不通过 %d 项'
                     % (len(ev['gates']), dec.get('failed_gates', 0))],
    ], widths=[3.6, 11.4], aligns=['center', 'left'],
       caption='表 1-1　评估结论摘要')

    # 二、项目概况
    b.h1('二、项目概况')
    b.table(['项目要素', '内容'],
            [[k, str(v)] for k, v in proj.items()
             if not k.startswith('_') and v not in (None, '', [], {})],
            widths=[3.6, 11.4], aligns=['center', 'left'],
           caption='表 2-1　项目基本情况')

    # 三、硬性资格门槛核对
    b.h1('三、硬性资格门槛核对')
    b.h2('3.1　逐项核对表')
    rows = []
    for g in ev['gates']:
        rows.append([g['gate'], g['requirement'], g['our_status'],
                     '符合' if g['passed'] else '不符合'])
    b.table(['资格门槛', '招标要求', '我方情况', '结论'], rows,
            widths=[3.2, 5.4, 4.6, 1.8],
            aligns=['center', 'left', 'left', 'center'],
            caption='表 3-1　硬性资格门槛核对表')
    b.h2('3.2　不满足项')
    fails = [g for g in ev['gates'] if not g['passed']]
    if fails:
        for g in fails:
            b.para('● %s：要求 %s；我方 %s。%s'
                   % (g['gate'], g['requirement'], g['our_status'], g.get('note', '')),
                   bold=True)
    else:
        b.para('本次评估未发现不满足的硬性资格门槛。')

    # 四、可行性评分卡
    b.h1('四、可行性评分卡')
    rows = []
    for d in ev['scorecard']:
        rows.append([d['dim'], '%.1f' % d['score'], '%d%%' % d['weight'],
                     '%.1f' % (d['score'] * d['weight'] / 100.0),
                     d['basis']])
    rows.append(['合计', '', '100%', '%.1f' % ev['score_total'], ''])
    b.table(['评估维度', '得分', '权重', '加权分', '评分依据'], rows,
            widths=[2.8, 1.4, 1.4, 1.6, 7.8],
            aligns=['center', 'center', 'center', 'center', 'left'],
            caption='表 4-1　六维可行性评分卡',
            note='注：资质与能力匹配、业绩匹配度、经济性三项为客观项，由脚本依据招标文件与我方档案直接计算；'
                 '商务条件可接受度、竞争格局、战略价值三项含主观判断，由评估人给出并须说明依据。')

    # 五、财务与商务测算
    b.h1('五、财务与商务测算')
    eco = ev['economics']
    rows = []
    labels = [('cap', '招标控制价'), ('bond', '投标保证金'),
              ('perf_bond', '履约保证金（估算）'), ('agency_fee', '招标代理服务费'),
              ('capital_occupation', '资金占用合计'),
              ('est_cost', '估算成本线'), ('target_gross_profit', '目标毛利')]
    for k, label in labels:
        v = eco.get(k)
        if v is not None:
            rows.append([label, format(v, ',.2f'), '元'])
    occ = eco.get('capital_occupation_ratio')
    if occ is not None:
        rows.append(['资金占用占限价比', '%.2f%%' % (occ * 100), ''])
    sq = eco.get('suggested_quote_range')
    if sq:
        rows.append(['建议报价区间',
                     '%s ~ %s' % (format(sq['low'], ',.2f'), format(sq['high'], ',.2f')),
                     '元'])
    b.table(['科目', '金额 / 比例', '单位'], rows,
            widths=[5.0, 6.4, 1.6], aligns=['center', 'right', 'center'],
            caption='表 5-1　财务测算表')
    if sq:
        b.para('报价建议依据：%s' % sq['basis'])

    # 六、竞争格局与战略判断
    b.h1('六、竞争格局与战略判断')
    comp = ev.get('assessment', {}).get('competition', {})
    strat = ev.get('assessment', {}).get('strategy', {})
    b.h2('6.1　竞争格局（%s 分）' % comp.get('score', '-'))
    b.para(comp.get('basis', '未评估'))
    b.h2('6.2　战略价值（%s 分）' % strat.get('score', '-'))
    b.para(strat.get('basis', '未评估'))

    # 七、风险清单
    b.h1('七、风险清单')
    if ev.get('risks'):
        b.table(['等级', '风险描述', '应对措施'],
                [[r.get('level', '中'), r.get('desc', ''), r.get('mitigation', '')]
                 for r in ev['risks']],
                widths=[1.4, 6.6, 7.0], aligns=['center', 'left', 'left'],
                caption='表 7-1　投标风险清单')
    else:
        b.para('未识别到重大风险。')

    # 八、投标建议
    b.h1('八、投标建议')
    for i, s in enumerate(ev.get('recommendations', []), 1):
        b.para('%d. %s' % (i, s), bold=(i == 1))

    b.h1('九、评估说明与免责')
    b.para('1．本报告的硬性资格核对、财务测算与评分计算均由脚本依据招标文件原文与我方资质档案自动完成；'
           '竞争格局与战略价值两项含评估人主观判断，其依据已在第四章列明。')
    b.para('2．我方资质、财务与业绩数据以所附证照及审计报告为准；缺失项已标注 [待补充]，未作任何推定。')
    b.para('3．本报告用于投标决策内部研判，不构成对外承诺；最终以招标文件正式澄清、补充文件为准。')

    b.sign(['评估人：（签字）', '评估日期：' + str(ev.get('evaluated_at', ''))])

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    b.doc.save(out_path)
    return out_path


def main():
    ap = argparse.ArgumentParser(description='评估报告渲染')
    ap.add_argument('--evaluation', required=True)
    ap.add_argument('--out', required=True, help='输出目录')
    ap.add_argument('--layout', default=None)
    ap.add_argument('--title', default='投标可行性评估报告')
    args = ap.parse_args()

    if not os.path.isfile(args.evaluation):
        print('[错误] 评估结果不存在: %s' % args.evaluation, file=sys.stderr)
        return 2
    with open(args.evaluation, encoding='utf-8') as fh:
        ev = json.load(fh)

    layout, is_fallback = load_layout(args.layout)
    if is_fallback:
        print('[版式] 未提供 layout.json，使用内置兜底版式（宋体小四/黑体标题/灰底表头）',
              file=sys.stderr)

    name = ev['project'].get('项目名称', '项目').replace('/', '_').replace('\\', '_')
    out_path = os.path.join(args.out, '投标可行性评估报告-%s.docx' % name)
    build_report(ev, out_path, args.title, layout)
    print('[渲染] %s  (%.1f KB)' % (os.path.basename(out_path),
                                   os.path.getsize(out_path) / 1024.0))
    print('[输出] %s' % os.path.abspath(out_path))
    return 0


if __name__ == '__main__':
    sys.exit(main())
