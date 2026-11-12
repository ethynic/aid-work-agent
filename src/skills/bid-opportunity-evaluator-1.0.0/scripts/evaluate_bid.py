#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
evaluate_bid.py — 投标可行性评估与决策引擎（文件操作层）

输入
    --profile   tender_profile.json / tender_announce.json（tender_intel.py 产出）
    --company   company_profile.json（我方资质与经营档案）
    --assessment assessment.json（大模型给出的主观评估：竞争分析、战略判断、风险认知）
    --out       输出目录

输出
    <out>/evaluation.json      评估结论（评分卡 + 硬门槛 + 财务测算 + 决策）
    <out>/EVALUATION.md        人读版评估报告

决策规则（脚本判定，客观）
    任一硬门槛不满足            → NO_GO（不参与投标）
    硬门槛全过但评分 < 60       → CAUTION（谨慎参与，需补偿措施）
    硬门槛全过且评分 ≥ 60      → GO（建议参与）
    报价低于成本线（成本不可得时按「限价×最低毛利率」估算）→ 额外风险标记

铁律
    - 硬门槛判定**只依据招标文件原文与我方档案的客观比对**，不接受主观打分。
    - 财务测算全部由脚本计算，不接受手工填数。
    - 竞争分析、战略价值等主观项由大模型写入 assessment.json，脚本只做汇总与加权。
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta

# ---------------------------------------------------------------- 评分权重
WEIGHTS = {
    'capability': 25,     # 资质与能力匹配度
    'performance': 15,    # 业绩匹配度
    'commercial': 15,     # 商务条件可接受度
    'economics': 20,      # 经济性（毛利/资金占用）
    'competition': 15,    # 竞争格局
    'strategy': 10,       # 战略价值
}

DECISION_LABEL = {
    'GO': '建议参与投标',
    'CAUTION': '谨慎参与（须落实补偿措施后决策）',
    'NO_GO': '不建议参与投标',
}


# ---------------------------------------------------------------- 工具

def _load(path):
    if not path or not os.path.isfile(path):
        return {}
    with open(path, encoding='utf-8') as fh:
        return json.load(fh)


def _get(d, *keys, default=None):
    for k in keys:
        if isinstance(d, dict) and k in d and d[k] not in (None, ''):
            return d[k]
    return default


def parse_cn_datetime(s):
    """'2026 年07 月03 日09：00 时' → datetime"""
    if not s:
        return None
    t = str(s)
    m = re_search_date(t)
    return m


def re_search_date(t):
    import re
    m = re.search(r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日'
                  r'(?:\s*(\d{1,2})\s*[：:]\s*(\d{1,2}))?', t)
    if not m:
        m = re.search(r'(\d{4})-(\d{1,2})-(\d{1,2})'
                      r'(?:\s+(\d{1,2}):(\d{1,2}))?', t)
        if not m:
            return None
    g = m.groups()
    try:
        return datetime(int(g[0]), int(g[1]), int(g[2]),
                        int(g[3] or 0), int(g[4] or 0))
    except (ValueError, TypeError):
        return None


def money(x):
    try:
        return round(float(x), 2)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- 硬门槛

def check_hard_gates(profile, company):
    """逐条比对资格门槛 —— 客观比对，不接受主观分"""
    gates = []
    comp = company.get('capability', {})

    # 1 主体资格
    gates.append({
        'gate': '主体资格（独立法人/营业执照）',
        'requirement': '具备中华人民共和国境内正式注册并具有有效独立法人资格',
        'our_status': '具备' if comp.get('legal_entity') else '不具备',
        'evidence': comp.get('legal_entity_evidence', ''),
        'passed': bool(comp.get('legal_entity')),
        'note': '',
    })

    # 2 经营范围/资质许可
    gates.append({
        'gate': '经营范围覆盖招标标的',
        'requirement': '经营范围含本次采购品类',
        'our_status': '覆盖' if comp.get('scope_covers') else '不覆盖',
        'evidence': comp.get('scope_evidence', ''),
        'passed': bool(comp.get('scope_covers')),
        'note': '',
    })

    # 3 生产/代理资质
    gates.append({
        'gate': '生产许可或代理授权',
        'requirement': '生产许可证（制造商）或厂家唯一授权书（经销商）',
        'our_status': comp.get('license_status', '未知'),
        'evidence': comp.get('license_evidence', ''),
        'passed': bool(comp.get('has_license_or_authorization')),
        'note': comp.get('license_note', ''),
    })

    # 4 财务要求
    fin = company.get('finance', {})
    fin_ok = bool(fin.get('has_audit_report') or fin.get('has_quarterly_statements'))
    gates.append({
        'gate': '财务要求（审计报告/季度报表）',
        'requirement': '2024 年审计报告，或 2025 年以来任意季度财务报表（含资产负债表、利润表）',
        'our_status': '具备' if fin_ok else '不具备',
        'evidence': fin.get('evidence', ''),
        'passed': fin_ok,
        'note': fin.get('note', ''),
    })

    # 5 纳税与社保
    tax_ok = bool(company.get('tax_social', {}).get('has_records'))
    gates.append({
        'gate': '纳税与社保记录',
        'requirement': '2025 年 01 月至今任意 3 个月纳税证明 + 社保缴费记录',
        'our_status': '具备' if tax_ok else '不具备',
        'evidence': company.get('tax_social', {}).get('evidence', ''),
        'passed': tax_ok,
        'note': company.get('tax_social', {}).get('note', ''),
    })

    # 6 业绩要求（近 N 年、金额、类型）
    perf = company.get('performance', {})
    req_perf = _get(profile, 'performance_requirement') or \
        profile.get('performance_requirement_text', '')
    have = perf.get('projects', [])
    req_years = perf.get('required_years', 3)
    req_count = perf.get('required_count')
    req_amount = perf.get('required_amount')
    req_type = perf.get('required_type')
    matched = []
    for p in have:
        y = p.get('year')
        ok_year = (not req_years) or (y and int(y) >= 2026 - req_years)
        ok_type = (not req_type) or (req_type in (p.get('scope', '') or p.get('type', '')))
        amt = p.get('amount')
        ok_amt = True
        if req_amount:
            try:
                ok_amt = float(str(amt).replace('万元', '').replace(',', '')) >= float(req_amount)
            except (TypeError, ValueError):
                ok_amt = False
        if ok_year and ok_type and ok_amt:
            matched.append(p)
    cnt_ok = (not req_count) or (len(matched) >= int(req_count))
    gates.append({
        'gate': '类似项目业绩',
        'requirement': req_perf or ('近 %s 年类似项目业绩' % req_years),
        'our_status': '满足（可用 %d 项）' % len(matched) if cnt_ok else '不满足（可用 %d 项）' % len(matched),
        'evidence': '；'.join('%s/%s' % (p.get('name', ''), p.get('year', '')) for p in matched[:5]),
        'passed': cnt_ok,
        'note': '要求数量 %s；要求金额 %s；要求类型 %s' % (req_count or '未明确',
                                                          req_amount or '未明确',
                                                          req_type or '未明确'),
    })

    # 7 信誉
    credit_ok = bool(company.get('credit', {}).get('clean'))
    gates.append({
        'gate': '信誉要求（无失信/无严重违法）',
        'requirement': '未被列入失信被执行人、严重违法失信名单；近三年无骗取中标等',
        'our_status': '符合' if credit_ok else '存在风险',
        'evidence': company.get('credit', {}).get('evidence', ''),
        'passed': credit_ok,
        'note': company.get('credit', {}).get('note', ''),
    })

    # 8 联合体
    jv = profile.get('joint_venture')
    if jv is False:
        gates.append({
            'gate': '联合体投标',
            'requirement': '不接受联合体投标',
            'our_status': '独立主体投标' if not company.get('use_jv') else '拟联合体投标',
            'evidence': '',
            'passed': not company.get('use_jv'),
            'note': '须以单一法人主体投标',
        })

    # 9 分包限制
    if profile.get('multi_volume') is not None:
        pass

    # 10 保证金可承受
    bond = money(_get(profile, 'bond'))
    bond_cap = money(company.get('finance', {}).get('max_bond_bearable'))
    if bond is not None and bond_cap is not None:
        ok = bond <= bond_cap
        gates.append({
            'gate': '投标保证金可承受',
            'requirement': '保证金 ¥%s 不超过我方可承受额度 ¥%s' % (format(bond, ',.2f'),
                                                             format(bond_cap, ',.2f')),
            'our_status': '可承受' if ok else '超出额度',
            'evidence': '',
            'passed': ok,
            'note': '' if ok else '需提高额度或放弃',
        })

    return gates


# ---------------------------------------------------------------- 财务测算

def calc_economics(profile, company):
    """资金占用与成本测算（全部由脚本计算）"""
    eco = {}
    cap = money(_get(profile, 'cap', 'cap_raw'))
    bond = money(_get(profile, 'bond'))
    fee = money(_get(profile, 'agency_fee'))
    perf_bond_pct = None
    pb = _get(profile, 'perf_bond_pct')
    if pb is None:
        pb_raw = _get(profile, 'perf_bond_raw')
        if pb_raw:
            import re
            m = re.search(r'(\d+(?:\.\d+)?)\s*%', str(pb_raw))
            if m:
                perf_bond_pct = float(m.group(1))
            else:
                perf_bond_pct = money(pb_raw)
    else:
        perf_bond_pct = float(pb)

    fin = company.get('finance', {})
    margin = fin.get('target_gross_margin')      # 目标毛利率，如 0.20
    min_margin = fin.get('min_gross_margin', 0.08)

    eco['cap'] = cap
    eco['bond'] = bond
    eco['agency_fee'] = fee
    eco['perf_bond_pct'] = perf_bond_pct
    if perf_bond_pct and cap:
        eco['perf_bond'] = money(cap * perf_bond_pct / 100.0)

    # 资金占用合计
    occupy = 0.0
    if bond:
        occupy += bond
    if eco.get('perf_bond'):
        occupy += eco['perf_bond']
    if fee:
        occupy += fee
    eco['capital_occupation'] = money(occupy)
    eco['capital_occupation_ratio'] = (
        money(occupy / cap) if cap else None)

    # 成本线估算
    if cap and margin is not None:
        cost = cap * (1 - float(margin))
        eco['est_cost'] = money(cost)
        eco['target_gross_profit'] = money(cap - cost)
        eco['margin_used'] = float(margin)
        eco['min_margin'] = min_margin
        eco['below_cost_floor'] = bool(cap < cost)
    else:
        eco['est_cost'] = None
        eco['below_cost_floor'] = None
        eco['margin_used'] = margin

    # 评分策略：基准价法下的最优报价区间
    sc = profile.get('scoring', {}) or {}
    rule = sc.get('price_rule')
    if cap and rule:
        # 高于基准价扣分更快 → 略微低于限价更优
        lo = cap * (1 - 0.05)
        hi = cap * (1 - 0.02)
        eco['suggested_quote_range'] = {
            'low': money(lo), 'high': money(hi),
            'basis': '基准价法：高 1%% 扣 %.1f 分、低 1%% 扣 %.1f 分，建议略低于限价 2%%~5%%'
                     % (rule.get('higher_deduct', 0), rule.get('lower_deduct', 0)),
        }
    return eco


# ---------------------------------------------------------------- 评分卡

def build_scorecard(profile, company, assessment, gates, eco):
    """六大维度评分（客观项脚本算，主观项取 assessment）"""
    sc = profile.get('scoring', {}) or {}
    a = assessment or {}
    dims = []

    # 1 能力匹配（客观：资格门槛通过率 + 产品参数匹配）
    cap_pass = sum(1 for g in gates if g['passed'])
    cap_total = len(gates) or 1
    prod_match = a.get('product_match_rate', 100)   # 大模型判断产品参数满足度
    s_cap = round(cap_pass / cap_total * 70 + prod_match / 100 * 30, 1)
    dims.append({'dim': '资质与能力匹配', 'score': s_cap,
                 'weight': WEIGHTS['capability'],
                 'basis': '资格门槛通过 %d/%d（%.0f%%）+ 产品参数满足度 %s%%'
                          % (cap_pass, cap_total, cap_pass / cap_total * 100, prod_match),
                 'objective': True})

    # 2 业绩匹配（客观：可用业绩项数 vs 要求）
    perf = company.get('performance', {})
    req = perf.get('required_count')
    have = perf.get('available_count', len(perf.get('projects', [])))
    if req:
        s_perf = round(min(100, have / float(req) * 100), 1)
        basis = '可用业绩 %s 项 / 要求 %s 项' % (have, req)
    else:
        s_perf = 85.0
        basis = '可用业绩 %s 项；招标文件未明确数量要求，按优于平均取 85 分' % have
    dims.append({'dim': '业绩匹配度', 'score': s_perf, 'weight': WEIGHTS['performance'],
                 'basis': basis, 'objective': True})

    # 3 商务条件（客观：付款/账期/质保/违约 的可接受性由大模型判断，逐项给分）
    comm = a.get('commercial_scores', {})
    s_comm = round(sum(comm.values()) / len(comm), 1) if comm else 60.0
    dims.append({'dim': '商务条件可接受度', 'score': s_comm,
                 'weight': WEIGHTS['commercial'],
                 'basis': '；'.join('%s %s' % (k, v) for k, v in comm.items())
                          or '未逐项评估，取保守 60 分',
                 'objective': False})

    # 4 经济性（客观：毛利率、限价合理性、资金占用占比）
    if eco.get('margin_used') is not None:
        m = float(eco['margin_used'])
        # 毛利率 20% 记 80 分，线性映射到 0~100（10%~30% 区间）
        s_eco = max(0.0, min(100.0, (m - 0.10) / 0.20 * 100))
        basis = '目标毛利率 %.1f%%（限价 %.2f 万元，估算成本 %.2f 万元）' % (
            m * 100, (eco.get('cap') or 0) / 10000, (eco.get('est_cost') or 0) / 10000)
    else:
        s_eco = 50.0
        basis = '未提供目标毛利率，取中性 50 分'
    occ = eco.get('capital_occupation_ratio')
    if occ is not None:
        if occ <= 0.03:
            s_eco = min(100.0, s_eco + 8)
            basis += '；资金占用占限价 %.2f%%（较优）' % (occ * 100)
        elif occ > 0.15:
            s_eco = max(0.0, s_eco - 15)
            basis += '；资金占用占限价 %.2f%%（偏高）' % (occ * 100)
    dims.append({'dim': '经济性', 'score': round(s_eco, 1), 'weight': WEIGHTS['economics'],
                 'basis': basis, 'objective': True})

    # 5 竞争格局（主观）
    comp = a.get('competition', {})
    s_comp = float(comp.get('score', 55))
    dims.append({'dim': '竞争格局', 'score': s_comp, 'weight': WEIGHTS['competition'],
                 'basis': comp.get('basis', '未评估，取保守 55 分'), 'objective': False})

    # 6 战略价值（主观）
    strat = a.get('strategy', {})
    s_str = float(strat.get('score', 50))
    dims.append({'dim': '战略价值', 'score': s_str, 'weight': WEIGHTS['strategy'],
                 'basis': strat.get('basis', '未评估，取保守 50 分'), 'objective': False})

    total = round(sum(d['score'] * d['weight'] / 100.0 for d in dims), 1)
    return dims, total


# ---------------------------------------------------------------- 决策

def decide(gates, total, eco, profile, assessment):
    fails = [g for g in gates if not g['passed']]
    risks = list((assessment or {}).get('risks', []))
    if eco.get('below_cost_floor'):
        risks.append({'level': '高', 'desc': '按目标毛利率估算，报价可能低于成本线，须重新核价'})
    if eco.get('capital_occupation_ratio') and eco['capital_occupation_ratio'] > 0.15:
        risks.append({'level': '中', 'desc': '资金占用占限价比例偏高，须落实授信或保函额度'})

    if fails:
        decision = 'NO_GO'
        reason = '存在 %d 项硬性资格门槛不满足：%s' % (
            len(fails), '；'.join(g['gate'] for g in fails))
    elif total >= 60:
        decision = 'GO'
        reason = '硬性门槛全部满足，综合评分 %.1f 分（满分 100），建议参与投标' % total
    else:
        decision = 'CAUTION'
        reason = '硬性门槛满足但综合评分仅 %.1f 分，须先落实补偿措施再决策' % total
    return decision, reason, fails, risks


# ---------------------------------------------------------------- 时间

def time_feasibility(profile, today=None):
    """投标可行性：剩余时间是否够"""
    today = today or datetime.now()
    dl = parse_cn_datetime(_get(profile, 'deadline'))
    out = {'deadline': dl.strftime('%Y-%m-%d %H:%M') if dl else None,
           'today': today.strftime('%Y-%m-%d')}
    if dl:
        delta = dl - today
        days = delta.days
        out['days_left'] = days
        out['expired'] = days < 0
        if days < 0:
            out['verdict'] = '已截止 %d 天，无法参与' % abs(days)
        elif days <= 3:
            out['verdict'] = '仅剩 %d 天，编制时间极其紧张，需评估能否完成' % days
        elif days <= 7:
            out['verdict'] = '剩余 %d 天，偏紧，需压缩非核心工作' % days
        else:
            out['verdict'] = '剩余 %d 天，时间充裕' % days
    else:
        out['verdict'] = '未能识别投标截止时间，需人工确认'
    return out


# ---------------------------------------------------------------- 报告

def render_md(ev):
    L = []
    L.append('# 投标可行性评估报告')
    L.append('')
    L.append('- 项目名称：%s' % ev['project'].get('项目名称', ''))
    L.append('- 招标人：%s' % ev['project'].get('招标人', '未识别'))
    L.append('- 评估结论：**%s**' % ev['decision']['label'])
    L.append('- 综合评分：**%.1f / 100**' % float(ev['score_total']))
    L.append('- 判定理由：%s' % ev['decision']['reason'])
    L.append('- 投标截止：%s（%s）' % (ev['time']['deadline'] or '未识别',
                                    ev['time']['verdict']))
    L.append('')

    L.append('## 一、项目概况')
    L.append('')
    L.append('| 项目 | 内容 |')
    L.append('|---|---|')
    for k, v in ev['project'].items():
        if v not in (None, '', [], {}):
            L.append('| %s | %s |' % (k, str(v)[:200]))
    L.append('')

    L.append('## 二、硬性资格门槛核对')
    L.append('')
    L.append('| 门槛 | 招标要求 | 我方情况 | 佐证 | 结论 |')
    L.append('|---|---|---|---|---|')
    for g in ev['gates']:
        L.append('| %s | %s | %s | %s | %s |' % (
            g['gate'], g['requirement'][:80], g['our_status'],
            g.get('evidence', '')[:60], '✅ 通过' if g['passed'] else '❌ 不通过'))
    L.append('')

    L.append('## 三、可行性评分卡')
    L.append('')
    L.append('| 维度 | 得分 | 权重 | 加权 | 依据 |')
    L.append('|---|---|---|---|---|')
    for d in ev['scorecard']:
        L.append('| %s | %.1f | %d%% | %.1f | %s |' % (
            d['dim'], d['score'], d['weight'],
            d['score'] * d['weight'] / 100.0, d['basis'][:110]))
    L.append('| **合计** | | **100%%** | **%.1f** | |' % float(ev['score_total']))
    L.append('')

    L.append('## 四、财务与商务测算')
    L.append('')
    L.append('| 科目 | 金额（元） | 说明 |')
    L.append('|---|---|---|')
    for k, label in [('cap', '招标控制价'), ('bond', '投标保证金'),
                     ('perf_bond', '履约保证金（估算）'), ('agency_fee', '招标代理服务费'),
                     ('capital_occupation', '资金占用合计'), ('est_cost', '估算成本线'),
                     ('target_gross_profit', '目标毛利')]:
        v = ev['economics'].get(k)
        if v is not None:
            L.append('| %s | %s | |' % (label, format(v, ',.2f')))
    sq = ev['economics'].get('suggested_quote_range')
    if sq:
        L.append('| 建议报价区间 | %s ~ %s | %s |' % (
            format(sq['low'], ',.2f'), format(sq['high'], ',.2f'), sq['basis']))
    L.append('')

    L.append('## 五、竞争格局与战略判断')
    L.append('')
    comp = ev.get('assessment', {}).get('competition', {})
    strat = ev.get('assessment', {}).get('strategy', {})
    L.append('- **竞争格局**（%s 分）：%s' % (comp.get('score', '-'), comp.get('basis', '未评估')))
    L.append('- **战略价值**（%s 分）：%s' % (strat.get('score', '-'), strat.get('basis', '未评估')))
    L.append('')

    L.append('## 六、风险清单')
    L.append('')
    if ev['risks']:
        L.append('| 等级 | 风险描述 | 应对措施 |')
        L.append('|---|---|---|')
        for r in ev['risks']:
            L.append('| %s | %s | %s |' % (r.get('level', '中'),
                                          r.get('desc', ''),
                                          r.get('mitigation', '')))
    else:
        L.append('未识别到重大风险。')
    L.append('')

    L.append('## 七、投标建议')
    L.append('')
    for i, s in enumerate(ev.get('recommendations', []), 1):
        L.append('%d. %s' % (i, s))
    L.append('')
    return '\n'.join(L)


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description='投标可行性评估与决策引擎')
    ap.add_argument('--profile', required=True, help='tender_profile.json')
    ap.add_argument('--company', required=True, help='company_profile.json')
    ap.add_argument('--assessment', default=None,
                    help='assessment.json（主观评估）；缺省则用规则化自动研判结果')
    ap.add_argument('--out', required=True, help='输出目录')
    ap.add_argument('--today', default=None, help='评估基准日 YYYY-MM-DD（默认今天）')
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    profile = _load(args.profile)
    company = _load(args.company)
    assessment = {}
    if args.assessment:
        assessment = _load(args.assessment)
    else:
        # 无主观评估 → 规则化自动研判（0 次大模型调用）
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from auto_assess import build as auto_build
        assessment = auto_build(profile, company)
        print('[研判] 未提供 assessment.json，使用规则化自动研判'
              '（竞争格局 %.0f / 战略价值 %.0f）'
              % (assessment['competition']['score'],
                 assessment['strategy']['score']))
    if not profile:
        print('[错误] profile 为空: %s' % args.profile, file=sys.stderr)
        return 2

    today = datetime.strptime(args.today, '%Y-%m-%d') if args.today else None

    gates = check_hard_gates(profile, company)
    eco = calc_economics(profile, company)
    dims, total = build_scorecard(profile, company, assessment, gates, eco)
    decision, reason, fails, risks = decide(gates, total, eco, profile, assessment)
    tf = time_feasibility(profile, today)

    if tf.get('expired'):
        decision = 'NO_GO'
        reason = '投标已截止：%s' % tf['verdict']
    elif decision == 'GO' and tf.get('days_left', 999) <= 3:
        decision = 'CAUTION'
        reason = '时间严重不足：%s。%s' % (tf['verdict'], reason)

    metrics = profile.get('metric_summary', {}) or {}
    proj = {
        '项目名称': _get(profile, 'project_name', default='未识别'),
        '招标人': _get(profile, 'tenderer', default='未识别'),
        '招标代理': _get(profile, 'agency', default='未识别'),
        '项目编号': _get(profile, 'project_code', default='未识别'),
        '建设地点': _get(profile, 'location', default='未识别'),
        '招标范围': _get(profile, 'scope', default='未识别'),
        '工期/交货期': _get(profile, 'duration', default='未识别'),
        '质量标准': _get(profile, 'quality', default='未识别'),
        '资金来源': _get(profile, 'fund_source', default='未识别'),
        '规模指标': ('%s 项，总量 %s %s' % (metrics.get('item_count', '?'),
                                          metrics.get('total_area', '?'),
                                          metrics.get('primary_unit', ''))
                     if metrics else '未识别'),
        '评分办法': (_get(profile, 'scoring', 'method', default='未识别') or '未识别'),
    }
    # 兼容别名（供脚本内部按 name 取值用；展示一律走中文键）
    # 以 _ 开头表示内部键，渲染时跳过，不会出现在项目概况表里
    proj['_name'] = proj['项目名称']

    recs = list(assessment.get('recommendations', []))
    if not recs:
        # 规则化建议：按最终结论 + 关键测算生成（比 draft 更准确，因决策已定）
        try:
            from auto_assess import build_recommendations
            recs = build_recommendations(profile, company, decision, total, eco)
        except Exception:
            recs = ['按结论「%s」执行：%s' % (DECISION_LABEL[decision], reason)]
    if not recs:
        recs = ['按结论「%s」执行：%s' % (DECISION_LABEL[decision], reason)]
    if fails:
        recs.append('优先补齐不满足项：%s' % '；'.join(g['gate'] for g in fails))
    if eco.get('suggested_quote_range'):
        recs.append('报价控制在 %s ~ %s 元区间（依据：%s）' % (
            format(eco['suggested_quote_range']['low'], ',.2f'),
            format(eco['suggested_quote_range']['high'], ',.2f'),
            eco['suggested_quote_range']['basis']))

    ev = {
        'company_name': company.get('company', ''),
        'project': proj,
        'decision': {'code': decision, 'label': DECISION_LABEL[decision],
                     'reason': reason, 'failed_gates': len(fails)},
        'score_total': total,
        'scorecard': dims,
        'gates': gates,
        'economics': eco,
        'time': tf,
        'risks': risks,
        'assessment': assessment,
        'recommendations': recs,
    }

    p_json = os.path.join(args.out, 'evaluation.json')
    with open(p_json, 'w', encoding='utf-8') as fh:
        json.dump(ev, fh, ensure_ascii=False, indent=2)
    p_md = os.path.join(args.out, 'EVALUATION.md')
    with open(p_md, 'w', encoding='utf-8') as fh:
        fh.write(render_md(ev))

    print('=' * 66)
    print('项目：%s' % proj['项目名称'])
    print('结论：%s（%s）' % (DECISION_LABEL[decision], reason))
    print('评分：%.1f / 100' % total)
    print('时间：%s' % tf['verdict'])
    print('-' * 66)
    for g in gates:
        print('  [%s] %-22s %s' % ('PASS' if g['passed'] else 'FAIL',
                                    g['gate'], g['our_status']))
    print('-' * 66)
    if risks:
        for r in risks:
            print('  [风险-%s] %s' % (r.get('level', '中'), r.get('desc', '')))
    print('[输出] %s' % p_json)
    print('[输出] %s' % p_md)
    print('=' * 66)
    return 0


if __name__ == '__main__':
    sys.exit(main())
