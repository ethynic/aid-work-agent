#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""第 3 层全链路 LLM 测试驱动（A 部分：requirements / layout / matrix / price_input）

模拟 SKILL.md 六步法中的大模型判断环节：直接调用 LLMGateway（与产品同一网关配置），
按 references/ 提取模型重新生成 04-中间配置 的四个输入 JSON，落盘到 /tmp/lxy_bid_test/llm/。
生成后由外层用 scripts/ 校验并与包内标准答案对照。

用法：python run_steps.py --step requirements|layout|matrix|price_input
"""
import argparse
import asyncio
import json
import re
import sys

sys.path.insert(0, '/app')

PKG = '/app/ext_package/lxy-bid-testcase'
REF = '/app/src/skills/bid-doc-generator-1.0.0/references'
OUT = '/tmp/lxy_bid_test/llm'
SRC = f'{PKG}/02-我方源文件'


def read(p):
    with open(p, encoding='utf-8') as f:
        return f.read()


def write(p, s):
    with open(p, 'w', encoding='utf-8') as f:
        f.write(s)


def docx2text(path):
    import docx
    d = docx.Document(path)
    lines = []

    def walk(parent):
        from docx.table import Table
        from docx.text.paragraph import Paragraph
        from docx.oxml.ns import qn
        for child in parent.element.body.iterchildren():
            if child.tag == qn('w:p'):
                t = Paragraph(child, parent).text.strip()
                if t:
                    lines.append(t)
            elif child.tag == qn('w:tbl'):
                tb = Table(child, parent)
                for r in tb.rows:
                    lines.append(' | '.join(c.text.strip().replace('\n', ' ') for c in r.cells))

    walk(d)
    return '\n'.join(lines)


def xlsx2text(path):
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True)
    out = []
    for ws in wb.worksheets:
        out.append(f'===== Sheet: {ws.title} =====')
        for row in ws.iter_rows(values_only=True):
            vals = ['' if v is None else str(v) for v in row]
            if any(v.strip() for v in vals):
                out.append(' | '.join(vals))
    return '\n'.join(out)


def extract_json(text):
    m = re.search(r'```(?:json)?\s*(\{.*\})\s*```', text, re.S)
    raw = m.group(1) if m else text
    start = raw.find('{')
    end = raw.rfind('}')
    obj = json.loads(raw[start:end + 1])
    return obj


async def call_llm(system, user, temperature=0.2):
    from src.llm.gateway import LLMGateway
    gw = LLMGateway(provider_name='deepseek', model_codes={'deepseek': 'deepseek-flash'})
    resp = await gw.chat(
        messages=[
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user},
        ],
        temperature=temperature,
    )
    content = resp.get('content') or ''
    usage = resp.get('usage') or {}
    print(f"[LLM] usage={usage.get('prompt_tokens')}+{usage.get('completion_tokens')} tokens, content_len={len(content)}")
    return content


def save_result(name, obj, raw):
    write(f'{OUT}/{name}.json', json.dumps(obj, ensure_ascii=False, indent=1))
    write(f'{OUT}/{name}.raw.txt', raw)
    print(f"[saved] {OUT}/{name}.json")


# ---------------------------------------------------------------- steps

async def step_requirements():
    """Step 2：tender 全文 -> requirements.json"""
    system = read(f'{REF}/requirements-extraction.md') + (
        '\n\n你现在是投标专员。请严格执行上面的提取指令与 Schema，通读用户给出的招标文件全文，'
        '输出 requirements.json。只输出 JSON，不要任何其他文字。'
        '\n注意：1) 逐字摘录格式条款整句（尤其装订/份数/纸张/目录/页数等，注明出处条款号）；'
        '2) 否决条款逐条全收；3) 带★或「否决」「不予受理」的条款 mandatory=true；'
        '4) 评分办法的基准价算法与扣分规则必须完整写入 scoring.price_formula。'
    )
    user = f'以下是招标文件全文（机械解析产物）：\n\n{read(f"{PKG}/03-脚本解析产物/tender_text.md")}'
    raw = await call_llm(system, user)
    save_result('requirements', extract_json(raw), raw)


async def step_layout():
    """Step 3.5：requirements(新) + 历史标书 -> layout.json"""
    system = read(f'{REF}/layout-extraction.md') + (
        '\n\n你现在是标书排版专员。请按上文规则推导 layout.json：'
        '甲方格式条款优先（来自用户给出的 requirements.json 的 format_requirements 与原文出处），'
        '甲方未规定处沿用历史标书范式（用户给出的历史投标书文本），'
        '两者都未规定的字段留空/用默认并在 notes 标注。'
        '每个取值都要在 _source.notes 写明出处（条款号或「历史标书范式」）。'
        '只输出 JSON，不要任何其他文字。'
    )
    user = (
        '【甲方 requirements.json（新提取，权威依据）】\n'
        + read(f'{OUT}/requirements.json')
        + '\n\n【历史投标书全文（范式兜底来源）】\n'
        + docx2text(f'{SRC}/历史投标书-广东东鹏控股股份有限公司（历史范例）.docx')
    )
    raw = await call_llm(system, user)
    save_result('layout', extract_json(raw), raw)


async def step_matrix():
    """Step 4：requirements(新) + 我方资料 -> matrix.json"""
    system = read(f'{REF}/compliance-matrix.md') + (
        '\n\n你现在是投标合规专员。请把用户给出的甲方要求与我方资料逐条对齐，'
        '输出汇总 JSON（qualification / technical / commercial 三张表）。只输出 JSON。'
        '\n注意：1) technical 必须覆盖甲方清单每一项（同名不同规格的清单项按序号分行，不得合并）；'
        '2) 偏离结论只依据我方资料原文，禁止臆造参数；'
        '3) 资格项 evidence 必须来自我方资质文件原文；'
        '4) 商务项与我方报价说明/承诺冲突时如实标注偏离。'
    )
    user = (
        '【甲方 requirements.json（新提取）】\n' + read(f'{OUT}/requirements.json')
        + '\n\n【我方产品参数清单（含 6 型号「招标要求 vs 我方响应值」参数表）】\n'
        + xlsx2text(f'{SRC}/产品参数清单-广东东鹏控股股份有限公司.xlsx')
        + '\n\n【我方资质文件】\n'
        + docx2text(f'{SRC}/我方资质文件-广东东鹏控股股份有限公司.docx')
        + '\n\n【我方产品报价表（含报价说明/商务条件）】\n'
        + xlsx2text(f'{SRC}/产品报价表-兰香园 D04、D06 地块项目（暂估价：瓷砖）.xlsx')
    )
    raw = await call_llm(system, user, temperature=0.1)
    save_result('matrix', extract_json(raw), raw)


async def step_price_input():
    """Step 5 输入面：甲方清单 + 我方单价 -> price_input.json"""
    system = (
        '你现在是投标报价专员。请把「甲方采购清单（数量）」与「我方产品报价表（单价）」逐项对齐，'
        '输出 price_input.json，Schema 严格如下：\n'
        '{\n  "project_name": "…", "tax_rate": 0.13, "cap": <招标文件中的最高投标限价，含税，数字>,\n'
        '  "items": [{"no": 1, "area": "部位", "name": "名称", "model": "型号", "unit": "m2", '
        '"qty": <甲方清单数量>, "unit_price_pretax": <我方不含税单价>, "brand": "品牌", "note": ""}],\n'
        '  "freight_note": "报价口径说明"\n}\n'
        '只输出 JSON，不要任何其他文字。\n'
        '注意：1) 甲方清单中名称相同但型号/规格不同的项必须按序号分行，不得合并；\n'
        '2) 数量以甲方清单为准，单价以我方报价表「不含税单价」列为准，逐行核对型号匹配；\n'
        '3) 严禁自行计算合价或总价——合价/总价由脚本计算，你只填单价与数量；\n'
        '4) cap 只能来自招标文件原文（含税最高限价），找不到填 null。'
    )
    user = (
        '【甲方清单（来自招标文件参数要求 Excel，含数量）】\n'
        + xlsx2text(f'{PKG}/01-甲方招标文件/250626 兰香园D04、D06地块项目-瓷砖参数要求.xlsx')
        + '\n\n【招标文件全文中与限价/报价口径相关的内容请自行检索】\n'
        + read(f'{PKG}/03-脚本解析产物/tender_text.md')[:60000]
        + '\n\n【我方产品报价表】\n'
        + xlsx2text(f'{SRC}/产品报价表-兰香园 D04、D06 地块项目（暂估价：瓷砖）.xlsx')
    )
    raw = await call_llm(system, user, temperature=0.0)
    save_result('price_input', extract_json(raw), raw)


STEPS = {
    'requirements': step_requirements,
    'layout': step_layout,
    'matrix': step_matrix,
    'price_input': step_price_input,
}

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--step', required=True, choices=sorted(STEPS))
    args = ap.parse_args()
    import os
    os.makedirs(OUT, exist_ok=True)
    asyncio.run(STEPS[args.step]())
