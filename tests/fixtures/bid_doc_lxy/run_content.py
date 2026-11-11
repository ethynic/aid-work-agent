#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""第 3 层全链路 LLM 测试驱动（B 部分：matrix 修订 + 十册 content 生成）

用法：python run_content.py --step revise_matrix|content_a|content_b|content_c
"""
import argparse
import asyncio
import json
import sys

sys.path.insert(0, '/app')

from run_steps import (PKG, REF, OUT, SRC, read, write, docx2text, xlsx2text,
                       extract_json, call_llm, save_result)

CSPEC = read(f'{REF}/content-spec.md')
BSTRUCT = read(f'{REF}/bid-structure.md')
QUAL_TEXT = docx2text(f'{SRC}/我方资质文件-广东东鹏控股股份有限公司.docx')
PARAM_TEXT = xlsx2text(f'{SRC}/产品参数清单-广东东鹏控股股份有限公司.xlsx')
PRICE_TEXT = xlsx2text(f'{SRC}/产品报价表-兰香园 D04、D06 地块项目（暂估价：瓷砖）.xlsx')
HIST_TEXT = docx2text(f'{SRC}/历史投标书-广东东鹏控股股份有限公司（历史范例）.docx')
TENDER_TEXT = read(f'{PKG}/03-脚本解析产物/tender_text.md')


async def step_revise_matrix():
    """真实迭代流程：把「承诺/响应文本缺项」类负偏离补齐为我方承诺，客观不满足项保留"""
    system = (
        '你现在是投标合规专员，正在修订偏离矩阵。规则：\n'
        '1) 逐条检查 deviation=负偏离 的条目：若缺口属于「投标文件尚未载明承诺/响应文本」'
        '（如样品提交承诺、服务费承诺书、保证金缴纳安排、报价口径承诺等——投标文件本身就是要作出这些承诺的载体），'
        '则把我方承诺的具体文本写进 our_commitment/our_parameter（明确、可执行、与报价口径一致），deviation 改为 无偏离，note 说明「承诺将在 X 文件中载明」。\n'
        '2) 若属于客观事实无法满足（产品参数低于招标要求、资质不达标），保持负偏离，如实标注。\n'
        '3) qualification 表同理：status=部分/不满足 且缺口是「文件/承诺未载明」的，补 evidence 说明并改满足；客观不达标保持。\n'
        '4) 金额、型号、数量等事实数字不得改动。\n'
        '输出修订后的完整 matrix JSON（三张表全量），只输出 JSON。'
    )
    user = '【当前 matrix.json】\n' + read(f'{OUT}/matrix.json') + '\n\n【我方资料（用于撰写承诺文本）】\n' + PRICE_TEXT[:8000] + '\n' + QUAL_TEXT[:6000]
    raw = await call_llm(system, user, temperature=0.1)
    save_result('matrix_rev', extract_json(raw), raw)


DOC_GROUPS = {
    # 按甲方第六章投标文件格式目录分组
    'content_a': {
        'docs': ['01-投标函.docx', '02-报价一览表.docx',
                 '03-法定代表人身份证明或授权委托书.docx', '04-投标保证金.docx'],
        'extra': ('【报价结果（脚本算出，price_table 区块将自动引用 /tmp/lxy_bid_test/llm/price_result.json）】\n'
                  '02-报价一览表.docx 中报价表必须用 price_table 区块（source 指向上述路径，style=tender），不得手抄金额。\n'),
        'src_hint': '投标函按第六章投标文件格式原文条款撰写；保证金册按甲方格式。',
    },
    'content_b': {
        'docs': ['05-资格审查资料.docx', '06-近年承接的类似项目情况表.docx', '07-商务和技术偏差表.docx'],
        'extra': ('07-商务和技术偏差表.docx：技术偏离用 matrix_table（matrix=technical），商务偏离用 matrix_table（matrix=commercial），'
                  '资格符合性用表格直接呈现 qualification 结论；均引用 /tmp/lxy_bid_test/llm/matrix_rev.json，不得手抄偏离结论。\n'),
        'src_hint': '05 册按第六章资格审查资料格式 + 我方资质文件逐表填充。',
    },
    'content_c': {
        'docs': ['08-投标材料及设备技术性能指标的详细描述.docx', '09-实施方案.docx', '10-其他资料.docx'],
        'extra': ('08 册：把 6 个型号「招标要求 vs 我方响应值」逐项做成表格，型号必须用我方参数清单里的东鹏型号；'
                  '09 实施方案按评分办法逐项针对性撰写（供货、运输、验收、质保、售后、应急）；'
                  '10-其他资料册按第六章格式收录各类承诺函（诚信/质量/售后/保密/不转包/代理服务费承诺书等，逐份独立标题，不得合并）。\n'),
        'src_hint': '承诺函文字参考历史标书承诺章话术与 commitment-letters.md。',
    },
}


async def step_content(group):
    g = DOC_GROUPS[group]
    system = (
        '你现在是标书撰写专员。按下方 Content Spec 契约，撰写用户指定各册的完整内容，输出 content JSON 片段：\n'
        '{"docs": [ {"file": "...", "title": "...", "cover": {...}, "sections": [{"heading": ..., "level": 1, "blocks": [...]}]} ]}\n'
        '只输出 JSON（含 docs 数组），不要任何其他文字。\n'
        '铁律：1) 事实零虚构，资质/业绩/证件号缺失写「[待补充：…]」并记入 pending_items；\n'
        '2) 报价与偏离表必须用 price_table / matrix_table 区块引用脚本产物，禁止手抄金额与偏离符号；\n'
        '3) 每份文件末尾放 sign 签字盖章区；4) 语体、章节话术对齐历史标书范式；\n'
        '5) 招标文件要求的承诺逐份独立成节，不得一揽子带过。'
    )
    user = (
        f'【本次要撰写的文件】{json.dumps(g["docs"], ensure_ascii=False)}\n'
        f'{g["extra"]}\n{g["src_hint"]}\n\n'
        '【Content Spec（输出契约，严格遵守）】\n' + CSPEC
        + '\n【甲方 requirements.json（本组相关条款自查）】\n' + read(f'{OUT}/requirements.json')
        + '\n【偏离矩阵（修订后，含我方承诺文本）】\n' + read(f'{OUT}/matrix_rev.json')
        + '\n【我方资质文件】\n' + QUAL_TEXT
        + '\n【我方产品参数清单】\n' + PARAM_TEXT
        + '\n【招标文件全文（第六章投标文件格式为各册格式依据）】\n' + TENDER_TEXT
        + '\n【历史标书（语体与承诺话术范式）】\n' + HIST_TEXT[:20000]
        + '\n【多文件拆分方案参考】\n' + BSTRUCT
    )
    raw = await call_llm(system, user, temperature=0.4)
    write(f'{OUT}/{group}.raw.txt', raw)
    try:
        obj = extract_json(raw)
    except Exception as e:
        print(f"[parse-error] {group}: {e}; 原始输出已存 {OUT}/{group}.raw.txt")
        raise
    write(f'{OUT}/{group}.json', json.dumps(obj, ensure_ascii=False, indent=1))
    write(f'{OUT}/{group}.raw.txt', raw)
    print(f"[saved] {OUT}/{group}.json docs={len(obj.get('docs', []))}")


STEPS = {
    'revise_matrix': step_revise_matrix,
    'content_a': lambda: step_content('content_a'),
    'content_b': lambda: step_content('content_b'),
    'content_c': lambda: step_content('content_c'),
}

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--step', required=True, choices=sorted(STEPS))
    args = ap.parse_args()
    asyncio.run(STEPS[args.step]())
