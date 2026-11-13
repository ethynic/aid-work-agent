#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
build_builtin_template.py — 生成技能自带的评估报告模版（.docx）

用途
    产出 `assets/投标可行性评估报告模板.docx`，作为「既无资料库模版、
    也无用户提供的模版」时的默认模版。

模版占位符协议
    {{文本键}}          → 替换为纯文本
    {{表格:键}}         → 在该段落位置插入一张表格
    {{段落:键}}         → 在该段落位置插入若干段落
    未被替换的占位符保留原样，便于人工补填

用法
    python build_builtin_template.py --out <assets目录>
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from docx_kit import DocBuilder  # noqa: E402

# 章节骨架：(级别, 标题, 紧随其后的占位符段落列表)
SKELETON = [
    (1, '一、评估结论', [
        '{{表格:结论摘要}}',
        '{{段落:结论说明}}',
    ]),
    (1, '二、项目概况', [
        '{{表格:项目概况}}',
    ]),
    (1, '三、资格条件核对', [
        '{{表格:资格门槛核对}}',
        '{{段落:不满足项说明}}',
    ]),
    (1, '四、可行性评分', [
        '{{表格:评分卡}}',
    ]),
    (1, '五、财务与经济性测算', [
        '{{表格:财务测算}}',
        '{{段落:报价建议}}',
    ]),
    (1, '六、竞争格局与战略判断', [
        '{{段落:竞争格局}}',
        '{{表格:竞品分析}}',
        '{{段落:战略价值}}',
    ]),
    (1, '七、风险清单', [
        '{{表格:风险清单}}',
    ]),
    (1, '八、投标建议', [
        '{{段落:投标建议}}',
    ]),
    (1, '九、评估说明与免责', [
        '{{段落:评估说明}}',
    ]),
]


def build(out_path):
    b = DocBuilder(header_text='', footer_text='')

    # 封面（占位符由填充引擎替换）
    for _ in range(4):
        b.para('', space_after=0, first_line=False)
    b.para('{{项目名称}}', size=22, bold=True, font='黑体', align='center',
           first_line=False, space_after=18)
    b.para('{{报告标题}}', size=18, bold=True, font='黑体', align='center',
           first_line=False, space_after=40)
    b.para('评估结论：{{结论}}', size=14, align='center', first_line=False,
           space_after=8)
    b.para('综合评分：{{综合评分}}', size=14, align='center', first_line=False,
           space_after=8)
    for _ in range(2):
        b.para('', space_after=0, first_line=False)
    b.para('投标人：{{投标人名称}}（盖单位章）', size=14, align='center',
           first_line=False, space_after=10)
    b.para('法定代表人或其委托代理人：（签字）', size=14, align='center',
           first_line=False, space_after=10)
    b.para('{{评估日期}}', size=14, align='center', first_line=False, space_after=0)
    b.doc.add_page_break()

    b.para('目　录', size=16, bold=True, font='黑体', align='center',
           first_line=False, space_after=18)
    b.para('（在 Word 中按 Ctrl+A 后按 F9 更新目录）', size=10.5, font='楷体',
           align='center', first_line=False, space_after=0)
    b.doc.add_page_break()

    for lvl, title, placeholders in SKELETON:
        if lvl == 1:
            b.h1(title)
        else:
            b.h2(title)
        for ph in placeholders:
            b.para(ph, size=11, font='楷体', align='left', first_line=False,
                   space_after=6)

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    b.doc.save(out_path)
    return out_path


def main():
    ap = argparse.ArgumentParser(description='生成技能自带评估模版')
    ap.add_argument('--out', required=True, help='assets 目录')
    args = ap.parse_args()
    path = build(os.path.join(args.out, '投标可行性评估报告模板.docx'))
    print('[生成] %s  (%.1f KB)' % (os.path.basename(path),
                                   os.path.getsize(path) / 1024.0))
    print('[输出] %s' % os.path.abspath(path))
    return 0


if __name__ == '__main__':
    sys.exit(main())
