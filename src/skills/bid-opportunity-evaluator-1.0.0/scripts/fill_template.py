#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
fill_template.py — 评估报告模版填充引擎（文件操作层）

用途
    把 evaluation.json 的内容填进**指定模版**，产出最终 Word 报告。
    模版来自三处之一（按优先级）：
        ① 用户在会话中提供的文件
        ② 资料库中检索到的评估报告模版
        ③ 技能自带的 assets/投标可行性评估报告模板.docx

填充策略（两条并行，先精确后模糊）
    A. 占位符替换：模版中的 {{文本}} / {{表格:键}} / {{段落:键}}
       → 精确定位替换，是首选路径。
    B. 章节语义匹配：模版无占位符时，按章节标题关键词匹配内容块，
       在该标题后插入对应段落与表格。
    未被匹配的内容块追加到报告末尾「补充说明」节，避免丢失。

用法
    python fill_template.py --evaluation evaluation.json \
        --template <模版.docx> --out <目录> [--layout layout.json] \
        [--title "投标可行性评估报告"] [--mapping mapping.json]
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from docx_kit import DocBuilder  # noqa: E402

PLACEHOLDER_RE = re.compile(r'\{\{\s*([^{}]+?)\s*\}\}')

# ---------------------------------------------------------------- 内容块定义
# 每个块：key（占位符用）、章节关键词（模糊匹配用）、生成函数
# 生成函数返回 {'paragraphs': [...], 'tables': [{'header':[], 'rows':[], 'widths':[], 'aligns':[]}]}


def _fmt_money(v):
    try:
        return format(float(v), ',.2f')
    except (TypeError, ValueError):
        return str(v)


def _gates_rows(ev):
    rows = []
    for g in ev.get('gates', []):
        rows.append([g['gate'], g['requirement'], g['our_status'],
                     '符合' if g['passed'] else '不符合'])
    return rows


def build_blocks(ev):
    """从 evaluation.json 生成全部内容块"""
    proj = ev.get('project', {})
    dec = ev.get('decision', {})
    eco = ev.get('economics', {})
    asmt = ev.get('assessment', {}) or {}
    B = {}

    # 结论
    B['结论摘要'] = {
        'paragraphs': [],
        'tables': [{
            'header': ['评估项', '结论'],
            'rows': [
                ['评估结论', dec.get('label', '')],
                ['综合评分', '%.1f / 100' % float(ev.get('score_total', 0))],
                ['判定理由', dec.get('reason', '')],
                ['投标截止时间', (ev.get('time', {}) or {}).get('deadline') or '未识别'],
                ['时间可行性', (ev.get('time', {}) or {}).get('verdict', '')],
                ['硬性门槛', '共 %d 项，不通过 %d 项' % (
                    len(ev.get('gates', [])), dec.get('failed_gates', 0))],
            ],
            'widths': [3.6, 11.4], 'aligns': ['center', 'left'],
        }],
    }
    B['结论说明'] = {
        'paragraphs': ['%s' % dec.get('reason', '')],
        'tables': [],
    }

    # 项目概况
    B['项目概况'] = {
        'paragraphs': [],
        'tables': [{
            'header': ['项目要素', '内容'],
            'rows': [[k, str(v)] for k, v in proj.items()
                     if not k.startswith('_') and v not in (None, '', [], {})],
            'widths': [3.6, 11.4], 'aligns': ['center', 'left'],
        }],
    }

    # 资格门槛
    B['资格门槛核对'] = {
        'paragraphs': [],
        'tables': [{
            'header': ['资格门槛', '招标要求', '我方情况', '结论'],
            'rows': _gates_rows(ev),
            'widths': [3.2, 5.4, 4.6, 1.8],
            'aligns': ['center', 'left', 'left', 'center'],
        }],
    }
    fails = [g for g in ev.get('gates', []) if not g['passed']]
    if fails:
        paras = ['本次评估发现 %d 项不满足的硬性资格门槛：' % len(fails)]
        for g in fails:
            paras.append('● %s：要求 %s；我方 %s。%s'
                         % (g['gate'], g['requirement'], g['our_status'],
                            g.get('note', '')))
    else:
        paras = ['本次评估未发现不满足的硬性资格门槛。']
    B['不满足项说明'] = {'paragraphs': paras, 'tables': []}

    # 评分卡
    rows = []
    for d in ev.get('scorecard', []):
        rows.append([d['dim'], '%.1f' % d['score'], '%d%%' % d['weight'],
                     '%.1f' % (d['score'] * d['weight'] / 100.0), d['basis']])
    rows.append(['合计', '', '100%', '%.1f' % float(ev.get('score_total', 0)), ''])
    B['评分卡'] = {
        'paragraphs': [],
        'tables': [{
            'header': ['评估维度', '得分', '权重', '加权分', '评分依据'],
            'rows': rows,
            'widths': [2.8, 1.4, 1.4, 1.6, 7.8],
            'aligns': ['center', 'center', 'center', 'center', 'left'],
        }],
    }

    # 财务测算
    labels = [('cap', '招标控制价'), ('bond', '投标保证金'),
              ('perf_bond', '履约保证金（估算）'), ('agency_fee', '招标代理服务费'),
              ('capital_occupation', '资金占用合计'),
              ('est_cost', '估算成本线'), ('target_gross_profit', '目标毛利')]
    frows = []
    for k, label in labels:
        v = eco.get(k)
        if v is not None:
            frows.append([label, _fmt_money(v), '元'])
    occ = eco.get('capital_occupation_ratio')
    if occ is not None:
        frows.append(['资金占用占限价比', '%.2f%%' % (occ * 100), ''])
    sq = eco.get('suggested_quote_range')
    if sq:
        frows.append(['建议报价区间',
                      '%s ~ %s' % (_fmt_money(sq['low']), _fmt_money(sq['high'])),
                      '元'])
    B['财务测算'] = {
        'paragraphs': [],
        'tables': [{
            'header': ['科目', '金额 / 比例', '单位'], 'rows': frows,
            'widths': [5.0, 6.4, 1.6], 'aligns': ['center', 'right', 'center'],
        }],
    }
    B['报价建议'] = {
        'paragraphs': (['报价建议依据：%s' % sq['basis']] if sq else []),
        'tables': [],
    }

    # 竞争与战略
    comp = asmt.get('competition', {})
    strat = asmt.get('strategy', {})
    B['竞争格局'] = {
        'paragraphs': ['竞争格局评估（%s 分）：%s'
                       % (comp.get('score', '-'), comp.get('basis', '未评估'))],
        'tables': [],
    }
    # 竞品分析：按竞争者类型分组，组下列出具体品牌
    competitors = asmt.get('competitors') or []
    comp_source = asmt.get('competitor_source') or ''
    comp_profiles = asmt.get('competitor_profiles') or []
    by_type = asmt.get('competitors_by_type') or {}
    paras = []
    tables = []

    # --- 表1：竞争者类型 → 该类型下的具体品牌（主表）
    # 类型取「实证竞品类型 ∪ 画像类型」，保证每种类型都有一行，
    # 有品牌的填品牌，没品牌的给出检索指向而不是留空。
    type_order = []
    for pr in comp_profiles:
        if pr.get('type') and pr['type'] not in type_order:
            type_order.append(pr['type'])
    for c in competitors:
        t = c.get('type') or '未分类'
        if t not in type_order:
            type_order.append(t)
    if not type_order:
        type_order = ['（无）']

    def _brands_of(t):
        items = by_type.get(t) or [c for c in competitors
                                   if (c.get('type') or '未分类') == t]
        return items

    rows = []
    for t in type_order:
        items = _brands_of(t)
        pr = None
        for x in comp_profiles:
            if x.get('type') == t:
                pr = x
                break
        if items:
            # 品牌单元格只列品牌名与威胁度，价带/区域在逐家分析表里已有
            brand_cell = '；'.join(
                '%s（威胁度 %.0f）' % (x.get('name', ''), x.get('threat', 0))
                for x in items)
            n = len(items)
            threat = max(x.get('threat', 0) for x in items)
            cols = [t, brand_cell, '%d 家' % n, '%.0f' % threat]
        else:
            # 该类型尚无实证品牌：给出该类型的品牌特征与检索指向，不留空
            traits = (pr or {}).get('traits', '')
            brand_cell = ('该类型尚未识别到具体品牌 —— 应为具备「%s」特征的企业'
                          % traits) if traits else '该类型尚未识别到具体品牌'
            cols = [t, brand_cell, '待补充', '—']
        cols.append((pr or {}).get('threat_point', '') or
                    '（待补充该类型威胁点）')
        cols.append((pr or {}).get('our_counter', '') or
                    '（待补充应对策略）')
        rows.append(cols)

    if comp_profiles or competitors:
        tables.append({
            'header': ['竞争者类型', '该类型下的具体品牌', '家数', '最高威胁度',
                       '对本项目的威胁点', '我方应对策略'],
            'rows': rows,
            'widths': [1.9, 4.6, 0.8, 1.1, 3.4, 3.8],
            'aligns': ['center', 'left', 'center', 'center', 'left', 'left'],
        })

    if competitors:
        paras.append('依据招标文件的资格门槛、评分权重与交付要求，'
                     '本项目的潜在竞争者分为 %d 类；已识别 %d 家可指认的'
                     '具体品牌，按类型归属与威胁度如下表。'
                     % (len(type_order), len(competitors)))
        if comp_source:
            paras.append('竞品信息来源：%s。' % comp_source)
    else:
        paras.append('尚未取得可指认的实证竞品名单（不以代号代替真实企业名）。'
                     '下表按招标文件客观特征列出本项目可能吸引的竞争者类型、'
                     '各类型应具备的品牌特征、威胁点与应对策略；'
                     '其中「具体品牌」一列需经检索补齐后重评。')

    # --- 表2：逐家分析（实证竞品）
    if competitors:
        tables.append({
            'header': ['竞争者（具体品牌）', '类型', '品牌定位', '主要区域',
                       '价格带', '重合度', '威胁度', '相对优势', '相对劣势',
                       '我方应对'],
            'rows': [[c.get('name', ''), c.get('type', ''),
                      c.get('positioning', ''), c.get('region', ''),
                      c.get('price_band', ''), c.get('overlap', ''),
                      '%.0f' % c.get('threat', 0),
                      c.get('strength', ''), c.get('weakness', ''),
                      c.get('our_counter', '')]
                     for c in competitors],
            'widths': [1.9, 1.1, 1.7, 1.3, 0.9, 0.9, 0.9, 2.0, 2.0, 3.0],
            'aligns': ['center', 'center', 'left', 'center', 'center',
                       'center', 'center', 'left', 'left', 'left'],
        })

    B['竞品分析'] = {
        'paragraphs': paras,
        'tables': tables,
    }
    B['战略价值'] = {
        'paragraphs': ['战略价值评估（%s 分）：%s'
                       % (strat.get('score', '-'), strat.get('basis', '未评估'))],
        'tables': [],
    }

    # 风险清单
    B['风险清单'] = {
        'paragraphs': [],
        'tables': [{
            'header': ['等级', '风险描述', '应对措施'],
            'rows': [[r.get('level', '中'), r.get('desc', ''),
                      r.get('mitigation', '')] for r in ev.get('risks', [])],
            'widths': [1.4, 6.6, 7.0], 'aligns': ['center', 'left', 'left'],
        }],
    }

    # 建议
    B['投标建议'] = {
        'paragraphs': ['%d. %s' % (i, s)
                       for i, s in enumerate(ev.get('recommendations', []), 1)],
        'tables': [],
    }

    # 说明与免责
    B['评估说明'] = {
        'paragraphs': [
            '1．本报告的硬性资格核对、财务测算与评分计算均由脚本依据招标文件原文与'
            '我方资质档案自动完成；竞争格局与战略价值两项含评估人主观判断，'
            '其依据已在第四章列明。',
            '2．我方资质、财务与业绩数据以所附证照及审计报告为准；'
            '缺失项已标注 [待补充]，未作任何推定。',
            '3．本报告用于投标决策内部研判，不构成对外承诺；'
            '最终以招标文件正式澄清、补充文件为准。',
        ],
        'tables': [],
    }
    return B


# ---------------------------------------------------------------- 章节匹配

DEFAULT_KEYWORDS = {
    '结论摘要': ['结论', '决策', '研判结论', '是否参与', '投标决策', '最终意见',
                 '最终结论', '研判意见', '总体意见', '意见', '建议结论'],
    '结论说明': ['结论说明', '判定理由', '理由', '结论依据'],
    '项目概况': ['概况', '项目情况', '项目基本信息', '情况介绍', '项目简介',
                 '基本信息', '项目情况', '项目信息', '项目内容', '研判基本信息',
                 '工程概况', '概况信息'],
    '资格门槛核对': ['资格', '门槛', '符合性', '资质条件', '资格核对', '资格条件',
                     '投标资格', '资格要求', '是否符合', '资质要求'],
    '不满足项说明': ['不满足', '不符合项', '差距', '差距分析'],
    '评分卡': ['评分', '打分', '可行性评价', '评价', '权重', '评分结果',
               '得分', '综合评价', '可行性得分'],
    '财务测算': ['财务', '经济', '测算', '成本', '资金', '报价', '经济性',
                 '财务测算', '资金测算'],
    '报价建议': ['报价建议', '价格建议'],
    '竞争格局': ['竞争', '对手', '市场格局', '竞争分析', '竞争对手'],
    '竞品分析': ['竞品', '竞对', '竞争对手分析', '竞品分析', '竞争态势',
                 '主要竞争者', '对手分析', '竞争者', '竞争格局', '竞争分析',
                 '竞争对手', '市场格局', '对手'],
    '战略价值': ['战略', '长期价值', '战略意义', '战略判断'],
    '风险清单': ['风险', '隐患', '风险与对策', '风险应对', '风险及对策'],
    '投标建议': ['投标建议', '行动建议', '实施建议', '结论与建议', '应对措施',
                 '建议', '工作建议'],
    '评估说明': ['说明', '免责', '声明', '备注', '编制说明'],
}


def _norm(t):
    return re.sub(r'[\s　]', '', t or '')


def match_sections(sections, blocks, keywords):
    """按章节标题语义匹配内容块。

    策略：对每个 (章节, 内容块) 计算命中关键词数，得分高者优先占位。
    得分相同按内容块在 keywords 中的顺序（先重要后次要）优先。
    这样「一、研判基本信息」能匹配「项目概况」，「五、最终意见」能匹配「结论摘要」。
    """
    pairs = []
    for si, sec in enumerate(sections):
        key = _norm(sec.get('key', ''))
        title = _norm(sec.get('title', ''))
        hay = key + '|' + title
        for order, (bk, kws) in enumerate(keywords.items()):
            score = 0
            for kw in kws:
                if not kw:
                    continue
                if kw in key:
                    score += 3          # 出现在归一化标题里，权重更高
                elif kw in title:
                    score += 2
                elif kw in hay:
                    score += 1
            if score:
                pairs.append((score, -order, si, bk))
    pairs.sort(reverse=True)

    mapping = {}
    used_blocks = set()
    used_sections = set()
    for score, _n, si, bk in pairs:
        if bk in used_blocks or si in used_sections:
            continue
        mapping.setdefault(si, []).append(bk)
        used_blocks.add(bk)
        used_sections.add(si)
    return mapping, used_blocks


# ---------------------------------------------------------------- 填充

def _make_content_elements(blocks, keys):
    """把内容块渲染成可搬运的 XML 元素列表（段落 + 表格）"""
    tmp = DocBuilder()
    for bk in keys:
        blk = blocks.get(bk) or {}
        for p in blk.get('paragraphs', []):
            tmp.para(p, size=11, align='justify', first_line=False, space_after=4)
        for t in blk.get('tables', []):
            tmp.table(t.get('header'), t.get('rows', []),
                      widths=t.get('widths'), aligns=t.get('aligns'),
                      size=9)
    # 取 body 下除 sectPr 外的元素
    body = tmp.doc.element.body
    els = []
    for child in body.iterchildren():
        tag = child.tag.split('}')[-1]
        if tag in ('p', 'tbl'):
            els.append(child)
    return els


def _insert_after(anchor, elements):
    """在 anchor 元素之后依次插入 elements"""
    prev = anchor
    for el in elements:
        prev.addnext(el)
        prev = el


def fill(evaluation, template_path, out_path, title, layout, mapping=None):
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = docx.Document(template_path)
    blocks = build_blocks(evaluation)
    keywords = mapping or DEFAULT_KEYWORDS

    # --- 文本占位符全局替换（先做，避免影响结构识别）
    ev = evaluation
    text_map = {
        '项目名称': ev.get('project', {}).get('项目名称', ''),
        '报告标题': title,
        '投标人名称': ev.get('company_name', ''),
        '评估日期': ev.get('evaluated_at', ''),
        '结论': ev.get('decision', {}).get('label', ''),
        '综合评分': '%.1f / 100' % float(ev.get('score_total', 0)),
    }
    replaced = set()
    for p in doc.paragraphs:
        txt = p.text
        if '{{' not in txt:
            continue
        new = txt
        for m in PLACEHOLDER_RE.finditer(txt):
            key = m.group(1)
            if key in text_map:
                new = new.replace(m.group(0), str(text_map[key]))
                replaced.add(key)
        if new != txt:
            # 保留下一个 run 的格式
            runs = p.runs
            if runs:
                runs[0].text = new
                for r in runs[1:]:
                    r.text = ''
            else:
                p.text = new

    # --- 遍历 body，识别章节与待替换占位符
    body = doc.element.body
    sections = []
    pending_ph = []      # (占位符段落元素, 键)
    cur = None
    for child in list(body.iterchildren()):
        tag = child.tag.split('}')[-1]
        if tag == 'p':
            p = Paragraph(child, doc)
            t = p.text.strip()
            m = PLACEHOLDER_RE.fullmatch(t)
            if m and not replaced.__contains__(m.group(1)):
                pending_ph.append((child, m.group(1)))
                continue
            lvl = _heading_level(p)
            if lvl:
                cur = {'title': t, 'level': lvl, 'key': _head_key(t), 'el': child}
                sections.append(cur)
        elif tag == 'tbl':
            pass

    # --- A. 占位符替换（表格/段落块）
    used_blocks = set()
    for anchor, key in pending_ph:
        if key.startswith('表格:') or key.startswith('段落:'):
            bk = key.split(':', 1)[1]
            blk = blocks.get(bk)
            if blk:
                els = _make_content_elements(blocks, [bk])
                _insert_after(anchor, els)
                used_blocks.add(bk)
            # 占位符段落本身清空（内容已插到其后），避免残留重复文字
            _set_text(anchor, '')
        else:
            if key in text_map:
                _set_text(anchor, str(text_map[key]))

    # --- B. 章节语义匹配（填未被占位符覆盖的块）
    mapping_sec, _ = match_sections(sections, blocks, keywords)
    for idx, bks in mapping_sec.items():
        todo = [b for b in bks if b not in used_blocks]
        if not todo:
            continue
        els = _make_content_elements(blocks, todo)
        _insert_after(sections[idx]['el'], els)
        used_blocks.update(todo)

    # --- C. 未被章节占用的块：就近合并到最相关章节，而非全部堆到末尾
    leftover = [bk for bk in blocks if bk not in used_blocks]
    merged = {}
    if leftover:
        # 依据内容块自身语义，找出已匹配章节中「最相关」的一节
        merged = {}
        for bk in leftover:
            best, best_score = None, 0
            for idx, sec in enumerate(sections):
                hay = _norm(sec.get('key', '')) + '|' + _norm(sec.get('title', ''))
                score = 0
                for kw in keywords.get(bk, []):
                    if kw and kw in hay:
                        score += 2
                if score > best_score:
                    best, best_score = idx, score
            if best is None:
                best = list(mapping_sec.keys())[-1] if mapping_sec else None
            if best is not None:
                merged.setdefault(best, []).append(bk)
        for idx, bks in merged.items():
            els = _make_content_elements(blocks, bks)
            _insert_after(sections[idx]['el'], els)
        # 仍有无法归位的（模版完全无对应章节）才追加到末尾
        rest = [bk for bk in leftover
                if not any(bk in v for v in merged.values())]
        if rest:
            h = doc.add_paragraph()
            r = h.add_run('补充说明')
            r.bold = True
            els = _make_content_elements(blocks, rest)
            prev = h._p
            for el in els:
                prev.addnext(el)
                prev = el

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    doc.save(out_path)
    # leftover 中已被就近合并的，不算「追加到末尾」
    truly_appended = [bk for bk in leftover
                      if not any(bk in v for v in merged.values())] \
        if leftover else []
    return out_path, used_blocks, truly_appended


def _set_text(p_el, text):
    from docx.text.paragraph import Paragraph
    p = Paragraph(p_el, None)
    runs = p.runs
    if runs:
        runs[0].text = text
        for r in runs[1:]:
            r.text = ''
    else:
        p.add_run(text)


def _heading_level(p):
    style = (p.style.name or '') if p.style is not None else ''
    if style.lower().startswith('heading'):
        m = re.search(r'heading\s*(\d+)', style.lower())
        return int(m.group(1)) if m else 1
    if style in ('Title', '标题'):
        return 1
    t = p.text.strip()
    if not t or len(t) > 60:
        return None
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
    t = (title or '').strip()
    t = re.sub(r'^第[一二三四五六七八九十百零〇\d]+[章篇部分]\s*', '', t)
    t = re.sub(r'^[一二三四五六七八九十\d]+\s*[、．.]\s*', '', t)
    t = re.sub(r'^[（(][一二三四五六七八九十\d]+[）)]\s*', '', t)
    t = re.sub(r'^\d+(\.\d+)*\s*[、．.]?\s*', '', t)
    return re.sub(r'[\s　]', '', t)


def main():
    ap = argparse.ArgumentParser(description='评估报告模版填充引擎')
    ap.add_argument('--evaluation', required=True)
    ap.add_argument('--template', required=True, help='模版 docx 路径')
    ap.add_argument('--out', required=True, help='输出目录')
    ap.add_argument('--layout', default=None)
    ap.add_argument('--title', default='投标可行性评估报告')
    ap.add_argument('--mapping', default=None, help='章节关键词映射 JSON')
    args = ap.parse_args()

    if not os.path.isfile(args.evaluation):
        print('[错误] 评估结果不存在: %s' % args.evaluation, file=sys.stderr)
        return 2
    if not os.path.isfile(args.template):
        print('[错误] 模版不存在: %s' % args.template, file=sys.stderr)
        return 2

    with open(args.evaluation, encoding='utf-8') as fh:
        ev = json.load(fh)
    mapping = None
    if args.mapping and os.path.isfile(args.mapping):
        with open(args.mapping, encoding='utf-8') as fh:
            mapping = json.load(fh).get('keywords')

    name = ev.get('project', {}).get('项目名称', '项目').replace('/', '_').replace('\\', '_')
    out_path = os.path.join(args.out, '投标可行性评估报告-%s.docx' % name)

    path, used, leftover = fill(ev, args.template, out_path, args.title,
                                args.layout, mapping)
    print('[填充] %s  (%.1f KB)' % (os.path.basename(path),
                                  os.path.getsize(path) / 1024.0))
    print('       模版：%s' % os.path.basename(args.template))
    total_blocks = len(build_blocks(ev))
    print('       已落位内容块 %d/%d 个；末尾补充 %d 个'
          % (total_blocks - len(leftover), total_blocks, len(leftover)))
    if leftover:
        print('       末尾补充项：%s' % '、'.join(leftover))
    print('[输出] %s' % os.path.abspath(path))
    return 0


if __name__ == '__main__':
    sys.exit(main())
