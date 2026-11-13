#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
run_pipeline.py — 投标可行性评估一键流水线

用途
    一次命令跑完：解析招标文件 → 规则化研判 → 评估决策 → 按模版填充 → Word 报告。
    **全程 0 次大模型调用**（大模型仅在需要深化主观判断时可选介入）。

用法
    # 最简：招标文件 + 公司档案 → 报告
    python run_pipeline.py --tender <招标文件.docx|pdf> \
        --company <company_profile.json> --out <输出目录> \
        [--announce <招标公告.pdf>] \
        [--template <模版.docx>] \
        [--assessment <assessment.json>] \
        [--today YYYY-MM-DD]

输出
    <输出目录>/
    ├── 01-画像/tender_profile.json      招标文件画像
    ├── 01-画像/tender_announce.json      公告要素（给了 --announce 时）
    ├── 02-研判/assessment_draft.json     规则化研判草稿
    ├── 03-评估/evaluation.json           评估结论
    ├── 03-评估/EVALUATION.md             评估摘要（人读）
    └── 04-报告/投标可行性评估报告-<项目>.docx   ★ 正式交付（Word）
"""
import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def run(cmd, desc):
    t0 = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                       errors='replace')
    dt = time.time() - t0
    if p.returncode != 0:
        print('[失败] %s' % desc, file=sys.stderr)
        sys.stderr.write((p.stderr or p.stdout or '')[-1500:])
        return None, dt
    out = (p.stdout or '').strip()
    if out:
        for ln in out.split('\n')[-6:]:
            print('    ' + ln)
    print('  ✓ %-28s %.1fs' % (desc, dt))
    return out, dt


def main():
    ap = argparse.ArgumentParser(description='投标可行性评估一键流水线')
    ap.add_argument('--tender', required=True, help='招标文件 docx/pdf')
    ap.add_argument('--company', required=True, help='company_profile.json')
    ap.add_argument('--out', required=True, help='输出目录')
    ap.add_argument('--announce', default=None, help='招标公告 pdf（可选）')
    ap.add_argument('--template', default=None,
                    help='评估报告模版 docx；缺省用技能自带模版')
    ap.add_argument('--assessment', default=None,
                    help='大模型研判 assessment.json（可选，缺省用规则化研判）')
    ap.add_argument('--competitors', default=None,
                    help='竞品清单 competitors.json（可选，由 competitor_intel '
                         'verify 产出；含真实企业名）')
    ap.add_argument('--today', default=None, help='评估基准日 YYYY-MM-DD')
    args = ap.parse_args()

    py = sys.executable
    out = os.path.abspath(args.out)
    d_profile = os.path.join(out, '01-画像')
    d_assess = os.path.join(out, '02-研判')
    d_eval = os.path.join(out, '03-评估')
    d_report = os.path.join(out, '04-报告')
    for d in (d_profile, d_assess, d_eval, d_report):
        os.makedirs(d, exist_ok=True)

    t0 = time.time()
    print('=' * 62)
    print('投标可行性评估流水线')
    print('=' * 62)

    # 1) 公告解析（可选）
    if args.announce:
        print('\n[1/5] 解析招标公告')
        cmd = [py, os.path.join(HERE, 'tender_intel.py'), 'announce',
               args.announce, '--out', d_profile]
        run(cmd, '公告要素解析')
    else:
        print('\n[1/5] 招标公告：跳过（未提供）')

    # 2) 招标文件画像
    print('\n[2/5] 解析招标文件')
    cmd = [py, os.path.join(HERE, 'tender_intel.py'), 'document',
           args.tender, '--out', d_profile]
    if run(cmd, '项目画像解析') is None:
        return 1
    profile_path = os.path.join(d_profile, 'tender_profile.json')

    # 2.5) 竞品检索任务书（供上层 Agent 检索真实竞品）
    if not args.competitors:
        brief = os.path.join(d_assess, 'competitor_brief.json')
        cmd = [py, os.path.join(HERE, 'competitor_intel.py'), 'probe',
               '--profile', profile_path, '--company', args.company,
               '--out', brief]
        run(cmd, '竞品检索任务书')
        print('    → 上层 Agent 按任务书检索真实竞品，填成 competitors_raw.json，')
        print('      再执行 competitor_intel.py verify 得到 competitors.json，')
        print('      用 --competitors 参数重跑本流水线。')

    # 3) 规则化研判
    print('\n[3/5] 规则化自动研判')
    cmd = [py, os.path.join(HERE, 'auto_assess.py'),
           '--profile', profile_path, '--company', args.company,
           '--out', os.path.join(d_assess, 'assessment_draft.json')]
    if args.competitors:
        cmd += ['--competitors', args.competitors]
    run(cmd, '规则化研判')
    draft_path = os.path.join(d_assess, 'assessment_draft.json')

    # 4) 评估决策
    print('\n[4/5] 评估与决策')
    cmd = [py, os.path.join(HERE, 'evaluate_bid.py'),
           '--profile', profile_path, '--company', args.company,
           '--out', d_eval]
    if args.assessment:
        cmd += ['--assessment', args.assessment]
    elif args.competitors:
        # 有竞品清单时，直接用含竞品的研判结果，避免二次研判丢掉竞品
        cmd += ['--assessment', os.path.join(d_assess, 'assessment_draft.json')]
    if args.today:
        cmd += ['--today', args.today]
    res, _ = run(cmd, '评估决策')
    if res is None:
        return 1
    eval_path = os.path.join(d_eval, 'evaluation.json')

    # 5) 按模版填充出 Word
    print('\n[5/5] 按模版填充 Word 报告')
    tpl = args.template or os.path.join(HERE, '..', 'assets',
                                        '投标可行性评估报告模板.docx')
    tpl = os.path.abspath(tpl)
    if not os.path.isfile(tpl):
        print('[错误] 模版不存在: %s' % tpl, file=sys.stderr)
        return 1
    cmd = [py, os.path.join(HERE, 'fill_template.py'),
           '--evaluation', eval_path, '--template', tpl, '--out', d_report]
    if run(cmd, 'Word 报告生成') is None:
        return 1

    # 汇总
    print('\n' + '=' * 62)
    print('流水线完成，用时 %.1f 秒，大模型调用 0 次'
          % (time.time() - t0))
    print('正式交付：')
    for f in sorted(os.listdir(d_report)):
        p = os.path.join(d_report, f)
        print('  %s  (%.1f KB)' % (f, os.path.getsize(p) / 1024.0))
    print('过程件：')
    for d in (d_profile, d_assess, d_eval):
        for f in sorted(os.listdir(d)):
            print('  %s/%s' % (os.path.basename(d), f))
    print('=' * 62)
    return 0


if __name__ == '__main__':
    sys.exit(main())
