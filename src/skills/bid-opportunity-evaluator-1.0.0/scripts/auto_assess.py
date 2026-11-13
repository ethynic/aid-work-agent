#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
auto_assess.py — 规则化自动研判（把主观项从「必须大模型写」降级为「可选增强」）

用途
    依据招标文件画像与公司档案，**用规则推导**出 assessment.json 的全部字段，
    使整个评估流程在 **0 次大模型调用**下即可产出完整评估报告。
    大模型若要介入，可只修订其中的主观字段。

推导依据（全部来自招标文件的客观特征 + 我方档案的可比对数据）
    竞争格局：价格分权重、业绩分权重、标段划分、联合体限制、品牌门槛、
              质保与履约要求 → 参与门槛与价格战强度
    战略价值：项目金额相对我方规模、品类匹配度、业绩客户重合度、产能利用、
              是否首次进入的地域 → 长尾价值
    商务可接受度：预付款比例、账期、结算周期、质保期、违约金、卸货责任
    产品参数满足度：清单条目数 vs 我方可供型号数
    风险清单：否决项、格式条款、页数上限、保证金、清单同名项
    投标建议：由决策结论 + 关键测算模板化生成

用法
    python auto_assess.py --profile tender_profile.json \
        --company company_profile.json [--out assessment_draft.json]

输出
    assessment_draft.json   规则化研判结果（结构与 assessment.json 完全一致，
                            可直接作为 evaluate_bid.py 的 --assessment 输入，
                            也可由大模型修订后覆盖）
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from competitor_intel import classify_name as _classify_comp_name
except Exception:            # 脚本被单独拷走时的兜底
    def _classify_comp_name(name):
        return (not (name or '').strip()), '名称为空'

# ---------------------------------------------------------------- 工具


def _load(path):
    if not path or not os.path.isfile(path):
        return {}
    with open(path, encoding='utf-8') as fh:
        return json.load(fh)


def _num(text, default=None):
    """从文本中取数值。

    需处理中文数量级表述：
        「超 2 亿平方米」 → 2e8
        「1.5 万吨」     → 15000
        「500 万套」     → 5000000
    否则会把「2 亿」读成 2，导致占比算成天文数字。
    """
    if text is None:
        return default
    if isinstance(text, (int, float)):
        return float(text)
    t = str(text)
    # 千分位逗号：先剥离，避免「2,000 万」被读成 2
    t = t.replace(',', '')
    m = re.search(r'([\d.]+)', t)
    if not m:
        return default
    try:
        val = float(m.group(1))
    except ValueError:
        return default
    tail = t[m.end():m.end() + 4]
    if '亿' in tail:
        val *= 1e8
    elif '万' in tail:
        val *= 1e4
    elif '千' in tail:
        val *= 1e3
    return val


def _days(text):
    """'730日' → 730；'2年' → 730"""
    if not text:
        return None
    m = re.search(r'(\d+)\s*日', str(text))
    if m:
        return int(m.group(1))
    m = re.search(r'(\d+(?:\.\d+)?)\s*年', str(text))
    if m:
        return int(float(m.group(1)) * 365)
    return None


def _clip(v, lo=0.0, hi=100.0):
    return max(lo, min(hi, v))



# ---------------------------------------------------------------- 竞品分析

def analyze_competitors(profile, company):
    """逐个竞品分析。

    竞品数据来源：公司档案的 `competitors` 字段（我方掌握的市场情报）。
    每个竞品可选字段：
        name        竞品名称（必填）
        type        类型：品牌型/区域型/价格型/工程型/新进入者
        positioning 品牌定位
        region      主要经营区域
        strength    相对优势
        weakness    相对劣势
        price_band  价格带（高档/中档/经济）
        overlap     与我方重合度（高/中/低）
        in_project  是否已知参与本项目（true/false/null）
    无竞品数据时返回空列表，并在 competition basis 中说明「未提供竞品名单」。
    """
    comps = company.get('competitors') or []
    if not comps:
        return []

    out = []
    for c in comps:
        name = (c.get('name') or '').strip()
        # 代号型占位名（如「竞品A」「某某公司」）不得进入正式报告
        is_ph, _reason = _classify_comp_name(name)
        if is_ph:
            continue
        ctype = c.get('type') or '未分类'
        price_band = c.get('price_band') or '未标注'
        overlap = c.get('overlap') or '未评估'
        region = c.get('region') or '未标注'

        # 威胁度：价格分权重越高，价格型竞品威胁越大；
        # 业绩门槛存在时，有业绩者威胁更大
        sc = profile.get('scoring', {}) or {}
        comps_w = {x['name']: x['score'] for x in (sc.get('components') or [])}
        price_w = comps_w.get('投标报价', 30)
        threat = 50.0
        if price_band in ('经济', '低价') or ctype == '价格型':
            threat += (price_w - 20) * 0.6 if price_w > 20 else 0
        if ctype == '品牌型':
            threat += 12
        if ctype == '区域型':
            threat += 8
        if ctype == '新进入者':
            threat -= 10
        if overlap == '高':
            threat += 10
        elif overlap == '低':
            threat -= 8
        if c.get('in_project') is False:
            threat -= 15
        threat = max(5.0, min(100.0, threat))

        # 应对建议
        actions = []
        if price_band in ('经济', '低价') or ctype == '价格型':
            actions.append('不宜正面低价跟进；以交付能力与质量稳定性差异化，'
                           '并用基准价法下的最优报价区间守住得分')
        if ctype == '品牌型':
            actions.append('以我方品牌在目标客户体系的既有业绩与服务网络形成差异，'
                           '避免纯品牌溢价被比价')
        if ctype == '区域型':
            actions.append('发挥本地化服务与响应速度优势，突出到货时效与售后半径')
        if ctype == '新进入者':
            actions.append('警惕其低价搅局，必要时以履约承诺函与样板服务稳固客户预期')
        if overlap == '高':
            actions.append('重合度高，重点在方案差异化与评分点全覆盖（业绩、方案、服务三项）')
        if not actions:
            actions.append('以方案完整度与评分点覆盖取胜')
        if c.get('weakness'):
            actions.append('针对其弱项「%s」强化我方表述' % c['weakness'])

        out.append({
            'name': name,
            'type': ctype,
            'positioning': c.get('positioning') or '',
            'region': region,
            'price_band': price_band,
            'overlap': overlap,
            'threat': round(threat, 1),
            'strength': c.get('strength') or '',
            'weakness': c.get('weakness') or '',
            'in_project': c.get('in_project'),
            'evidence': c.get('evidence') or '',
            'our_counter': '；'.join(actions),
        })

    out.sort(key=lambda x: -x['threat'])
    return out


# ---------------------------------------------------------------- 竞争者画像

def build_competitor_profiles(profile, company):
    """依据招标文件的客观特征，推导**竞争者类型画像**。

    与「实证竞品」互补：
      · 实证竞品 = 真实可指认的企业名（由上层 Agent 检索或我方情报提供）
      · 竞争者画像 = 不出现任何企业名，只刻画「本项目会吸引哪几类对手、
                     它们凭什么是、威胁在哪、我方怎么应对」

    在尚未拿到实证竞品名单时，画像本身即是可执行的竞争分析，
    不会出现「竞品A」这类无决策价值的代号。
    """
    sc = profile.get('scoring', {}) or {}
    comps = {c['name']: c['score'] for c in (sc.get('components') or [])}
    price_w = comps.get('投标报价', 30)
    factors = ' '.join(f.get('name', '') for f in (sc.get('factors') or []))
    texts = ' '.join(str(v) for v in profile.values() if isinstance(v, str))
    terms = profile.get('commercial_terms', {}) or {}
    cap = profile.get('cap') or 0

    profiles = []

    # 头部品牌型
    if re.search(r'十大品牌|知名品牌|同等档次|品牌影响力|品牌档次|品牌受众度|'
                 r'品牌口碑|唯一授权书', texts):
        profiles.append({
            'type': '头部品牌型',
            'presence': '高',
            'traits': '全国性品牌，凭品牌影响力与集采经验获取品牌类评分，'
                      '报价中含品牌溢价',
            'threat_point': '在「品牌影响力/档次/口碑」评分项上直接领先，'
                            '并在价格上以规模摊薄成本',
            'our_counter': '以我方在同类型工程项目的业绩实证与质量体系文件正面对标；'
                           '价格锚定基准价法下的最优区间，避免品牌溢价被放大对比',
        })

    # 价格导向型
    if price_w >= 30:
        profiles.append({
            'type': '价格导向型',
            'presence': '高' if price_w >= 40 else '中',
            'traits': '以成本控制和低价切入，产品同质化程度高时冲击最大',
            'threat_point': '价格分占 %d 分，若我方报价高于其 %s%% 将直接失分'
                            % (price_w, '5~8'),
            'our_counter': '锁定基准价法下的最优报价区间并做分项成本精算；'
                           '把价格劣势用质量、业绩、服务三项评分补回',
        })

    # 区域深耕型
    profiles.append({
        'type': '区域深耕型',
        'presence': '中',
        'traits': '本地渠道与经销商网络深，报价灵活、账期谈判意愿强，'
                  '响应速度快但跨区服务能力弱',
        'threat_point': '在本地项目熟悉度与临时响应上占优，'
                        '并可能以延长账期换取价格让利',
        'our_counter': '以到货时效承诺与售后响应半径量化对比其服务半径；'
                       '在付款条件上争取对等账期，抵消其账期让利优势',
    })

    # 工程集采型
    if re.search(r'总承包|房地产开发|置业|建设集团|建筑工程|工程局|建工集团', texts):
        profiles.append({
            'type': '工程集采型',
            'presence': '中',
            'traits': '专做地产与总包集采，报价贴近成本，接受较长账期，'
                      '以量换利、对单项目利润不敏感',
            'threat_point': '在长账期项目上现金流成本更低，'
                            '可能接受对我方不利的付款条件来压价',
            'our_counter': '把账期成本折算进报价（资金占用测算），'
                           '在商务偏离中主张缩短账期或提高预付比例；'
                           '不与其比现金流，只比质量与服务确定性',
        })

    # 业绩壁垒型（有业绩门槛时）
    if re.search(r'业绩', factors):
        req = company.get('performance', {}).get('required_count')
        profiles.append({
            'type': '业绩壁垒型',
            'presence': '中',
            'traits': '具备同类项目业绩的施工方或其长期供应商，'
                      '熟悉甲方验收与资料口径',
            'threat_point': '业绩评分项上与我方直接竞争，'
                            '且熟悉评标口径可精准踩点得分',
            'our_counter': '业绩按「足额、可核验、类型对口」三项准备：'
                           '合同或中标通知书加盖公章、金额与类型匹配、'
                           '覆盖近 %s 年' % (req or '规定年限'),
        })

    # 新进入者
    profiles.append({
        'type': '新进入者/跨品类进入',
        'presence': '低',
        'traits': '以低价或新概念切入，短期不追求利润，'
                  '根基与履约能力通常未经检验',
        'threat_point': '在价格敏感型评标中制造扰动，压缩我方合理利润空间',
        'our_counter': '以履约承诺函、样板与质保条款建立客户预期；'
                       '不主动跟价，以综合评分优势守住位次',
    })

    # 品牌门槛会筛掉部分参与者
    if re.search(r'十大品牌|知名品牌|同等档次', texts) and cap:
        profiles.append({
            'type': '参与主体收敛效应',
            'presence': '提示',
            'traits': '品牌门槛与业绩门槛共同抬高准入，'
                      '实际参与家数通常少于表面预期',
            'threat_point': '若我方不满足品牌/业绩门槛则直接失去参与资格',
            'our_counter': '提前核对自身资质与业绩是否满足准入，'
                           '必要时通过合规的联合体或授权代理补足',
        })

    return profiles


def competition_summary(competitors, comp_profiles=None):
    """把竞品分析汇总成一段可读文字。

    **按竞争者类型聚合**并直接点名品牌 —— 评审看的是「这一类对手里有谁」，
    而不是「有几家对手」。没有实证品牌时，改述每个类型应有的品牌特征，
    提示需补充检索。
    """
    if not competitors:
        # 无实证品牌：按类型说明「该找什么样的品牌」，并给出可操作指向
        parts = []
        for pr in (comp_profiles or []):
            parts.append('%s：%s' % (pr.get('type', ''),
                                     pr.get('traits', '')))
        if parts:
            return ('尚未取得实证竞品名单。按招标文件特征，本项目可能吸引：'
                    + '；'.join(parts))
        return ''

    # 按类型分组
    groups = {}
    for c in competitors:
        groups.setdefault(c.get('type') or '未分类', []).append(c)

    segs = []
    for ctype in sorted(groups, key=lambda t: -max(
            x['threat'] for x in groups[t])):
        items = sorted(groups[ctype], key=lambda x: -x['threat'])
        names = '、'.join('%s（威胁度 %.0f）' % (x['name'], x['threat'])
                          for x in items)
        segs.append('%s %d 家：%s' % (ctype, len(items), names))

    out = '已识别可指认的竞争者 %d 家，按类型分布为 —— %s' % (
        len(competitors), '；'.join(segs))

    high = [c['name'] for c in competitors if c['threat'] >= 60]
    if high:
        out += '。威胁度 ≥60 需重点应对：%s' % '、'.join(high)

    # 类型覆盖缺口：画像里有、实证里没有的类型 → 提示补充
    if comp_profiles:
        have = set(groups.keys())
        missing = [pr['type'] for pr in comp_profiles
                   if pr['type'] not in have]
        if missing:
            out += '。尚缺该类竞争者的具体品牌：%s，建议补充检索后重评' % '、'.join(missing)
    return out


def _group_by_type(competitors):
    """把实证竞品按类型分组，供报告按「类型 → 该类型下的品牌」呈现。

    返回 {类型: [竞品…]}，组内按威胁度降序。
    """
    g = {}
    for c in competitors or []:
        g.setdefault(c.get('type') or '未分类', []).append(c)
    for k in g:
        g[k] = sorted(g[k], key=lambda x: -x.get('threat', 0))
    return g


# ---------------------------------------------------------------- 竞争格局

def score_competition(profile, company):
    """按招标特征推导竞争激烈程度与我方相对优势"""
    sc = profile.get('scoring', {}) or {}
    comps = {c['name']: c['score'] for c in (sc.get('components') or [])}
    price_w = comps.get('投标报价', 30)
    perf_w = comps.get('商务部分', 30)
    factors = ' '.join(f.get('name', '') for f in (sc.get('factors') or []))
    texts = ' '.join(str(v) for v in profile.values() if isinstance(v, str))
    terms = profile.get('commercial_terms', {}) or {}

    score = 62.0
    reasons = []

    # 价格权重高 → 价格战强 → 对我方不利
    if price_w >= 30:
        score -= 8
        reasons.append('价格分占 %d 分，价格竞争激烈' % price_w)
    elif price_w >= 20:
        score -= 3
        reasons.append('价格分占 %d 分' % price_w)

    # 业绩分权重高 → 有业绩者占优
    if '类似项目业绩' in factors or '业绩' in factors:
        avail = company.get('performance', {}).get('available_count', 0)
        req = company.get('performance', {}).get('required_count') or 1
        if avail >= req:
            score += 10
            reasons.append('设类似项目业绩评分，我方可用业绩 %d 项可支撑' % avail)
        else:
            score -= 10
            reasons.append('设类似项目业绩评分，我方可用业绩 %d 项不足要求 %s 项'
                           % (avail, req))
    else:
        reasons.append('未设类似项目业绩评分项')

    # 联合体限制：接受联合体 → 参与门槛低 → 竞争者多
    if profile.get('joint_venture') is False:
        score -= 3
        reasons.append('不接受联合体投标，参与主体受限')

    # 标段划分
    if re.search(r'不分标段|不分段', texts):
        score -= 2
        reasons.append('本项目不分标段，同台竞争')
    elif re.search(r'\d+\s*个标段', texts):
        score += 3
        reasons.append('划分多个标段，可分散竞争压力')

    # 品牌门槛
    if re.search(r'十大品牌|同等档次品牌|知名品牌', texts):
        score -= 4
        reasons.append('设品牌门槛（十大品牌或同等档次），头部品牌占优')

    # 金额规模：项目越大竞争者越多
    cap = profile.get('cap') or 0
    if cap >= 5e7:
        score -= 5
        reasons.append('项目金额较大（%.0f 万元），参与企业数量通常较多'
                       % (cap / 1e4))
    elif cap and cap < 3e6:
        score += 3
        reasons.append('项目金额较小（%.0f 万元），头部企业参与意愿低'
                       % (cap / 1e4))

    # 质保/履约要求高 → 抬高门槛
    w = terms.get('warranty') or []
    days = _days(w[0]) if w else None
    if days and days >= 730:
        score -= 2
        reasons.append('质保期 %d 日，服务能力门槛偏高' % days)

    return _clip(score), '；'.join(reasons)


# ---------------------------------------------------------------- 战略价值

def score_strategy(profile, company):
    """项目对我方的长尾价值"""
    score = 55.0
    reasons = []
    cap = profile.get('cap') or 0
    metrics = profile.get('metric_summary', {}) or {}
    total_qty = metrics.get('total_area') or metrics.get('count_max') or 0

    # 金额量级：相对我方体量的重要度
    if cap >= 3e7:
        score += 12
        reasons.append('项目金额 %.0f 万元，属于重点机会' % (cap / 1e4))
    elif cap >= 1e7:
        score += 8
        reasons.append('项目金额 %.0f 万元，具备规模意义' % (cap / 1e4))
    elif cap >= 3e6:
        score += 4
        reasons.append('项目金额 %.0f 万元，中等体量' % (cap / 1e4))
    else:
        reasons.append('项目金额 %.0f 万元，体量偏小' % (cap / 1e4))

    # 业绩客户重合：已在该招标人体系供货 → 复投机会
    buyer = (profile.get('tenderer') or '').strip()
    same_buyer = []
    for p in company.get('performance', {}).get('projects', []):
        if buyer and buyer in str(p.get('buyer', '')):
            same_buyer.append(p)
        elif buyer and buyer[:2] and buyer[:2] in str(p.get('buyer', '')):
            same_buyer.append(p)
    if same_buyer:
        score += 12
        reasons.append('招标人体系已有 %d 项供货记录，具备复投基础'
                       % len(same_buyer))
    else:
        score -= 3
        reasons.append('招标人体系无既有供货记录，需新进入')

    # 地域覆盖：项目地点是否在我方服务网络内
    loc = profile.get('location') or ''
    regions = company.get('capacity', {}).get('regions') or []
    net = ' '.join(str(r) for r in regions) + ' ' + \
        str(company.get('capacity', {}).get('service_network', ''))
    hit = [r for r in regions if r and r in loc]
    if hit:
        score += 8
        reasons.append('建设地点在既有服务网络覆盖范围（%s）' % '、'.join(hit))
    elif '海南' in loc or '海南' in net:
        score += 5
        reasons.append('属海南区域项目，与我方海南前置仓布局方向一致')

    # 品类匹配：清单条目数与我方可供型号/品类数
    item_count = metrics.get('item_count') or 0
    if item_count and item_count <= 8:
        score += 4
        reasons.append('清单仅 %d 项，品类集中，交付与备货压力小' % item_count)
    elif item_count > 20:
        score -= 6
        reasons.append('清单 %d 项，品类分散，备货与质量管理压力大' % item_count)

    # 产能利用：项目量相对年产能
    out = company.get('capacity', {}).get('annual_output', '')
    out_num = _num(out)
    if out_num and total_qty and out_num > 0:
        ratio = total_qty / out_num
        if 0 <= ratio < 0.001:
            score += 3
            reasons.append('项目量相对年产能占比极低，产能无压力')
        elif 0.001 <= ratio <= 0.05:
            reasons.append('项目量相对年产能占比约 %.2f%%，产能占用可控'
                           % (ratio * 100))
        elif ratio > 0.05:
            score -= 4
            reasons.append('项目量相对年产能占比约 %.1f%%，占用产能较大'
                           % (ratio * 100))

    return _clip(score), '；'.join(reasons)


# ---------------------------------------------------------------- 商务可接受度

def score_commercial(profile, company):
    """按商务条款客观特征逐项打分"""
    terms = profile.get('commercial_terms', {}) or {}
    text = ' '.join(str(v) for v in terms.values() if isinstance(v, str))
    payment = terms.get('payment') or ''
    settle = terms.get('settlement') or ''
    warranty = terms.get('warranty') or []
    after = terms.get('after_sale') or []
    scores = {}
    bases = {}

    # 付款条件：预付款比例 + 账期
    s = 60.0
    b = []
    m = re.search(r'预付款[^\d]{0,10}(\d+)\s*%', text)
    if m:
        pct = int(m.group(1))
        s += min(15, pct * 1.5)
        b.append('预付款 %d%%' % pct)
    else:
        s -= 5
        b.append('未见预付款约定')
    m = re.search(r'(\d+)\s*日历天', payment) or re.search(r'(\d+)\s*日内付', payment)
    if m:
        days = int(m.group(1))
        if days <= 60:
            s += 10
            b.append('账期约 %d 日' % days)
        elif days <= 120:
            s += 3
            b.append('账期约 %d 日' % days)
        else:
            s -= 8
            b.append('账期约 %d 日，占用资金较久' % days)
    else:
        b.append('付款账期以合同约定，未见明确天数')
    scores['付款条件'] = _clip(s)
    bases['付款条件'] = '，'.join(b)

    # 结算方式：结算周期
    s = 60.0
    b = []
    if re.search(r'按月度结算|按月结算|月度结算', text):
        s += 12
        b.append('按月度结算')
    elif re.search(r'按节点结算|分段结算|竣工结算', text):
        s += 4
        b.append('按节点/竣工结算')
    else:
        b.append('结算方式未明确')
    m = re.search(r'(\d+)\s*日内.*审核', text) or re.search(r'甲方在收到[^\n]{0,20}(\d+)\s*日内', text)
    if m:
        b.append('甲方审核期 %s 日' % m.group(1))
    scores['结算方式'] = _clip(s)
    bases['结算方式'] = '，'.join(b)

    # 质量保修期：与行业常见 730 日对比
    days = _days(warranty[0]) if warranty else None
    if days is None:
        scores['质量保修期'] = 60.0
        bases['质量保修期'] = '招标文件未明确质量保修期，按常规取中值'
    elif days >= 730:
        scores['质量保修期'] = min(100.0, 70 + (days - 730) / 365.0 * 20)
        bases['质量保修期'] = ('质保期 %d 日（%s 年），达到或优于行业惯例'
                               % (days, round(days / 365.0, 1)))
    else:
        scores['质量保修期'] = max(30.0, 70 - (730 - days) / 365.0 * 25)
        bases['质量保修期'] = '质保期 %d 日，短于行业惯例 730 日' % days

    # 违约责任：违约金/滞纳金/赔偿条款
    s = 60.0
    b = []
    m = re.search(r'([\d.]+)\s*万元/天', text)
    if m:
        s -= 8
        b.append('含 %s 万元/天类罚则' % m.group(1))
    if re.search(r'承担.{0,20}一切损失|全部损失', text):
        s -= 8
        b.append('含承担全部损失的赔偿条款')
    if re.search(r'没收投标保证金|无权.{0,6}扣除', text):
        s -= 6
        b.append('含保证金没收/扣款条款')
    if not b:
        b.append('未识别到显著罚则条款')
    scores['违约责任'] = _clip(s)
    bases['违约责任'] = '，'.join(b)

    # 交货方式与责任划分
    s = 60.0
    b = []
    if re.search(r'车板交货', text):
        b.append('车板交货')
    if re.search(r'由中标人负责卸货|负责卸货|中标人负责.{0,10}卸', text):
        s -= 10
        b.append('卸货责任在乙方，人工与机械成本需计入')
    if re.search(r'不雇佣甲方在班人员|不雇佣', text):
        s -= 4
        b.append('须自行组织装卸，不得使用甲方人员设备')
    if re.search(r'运输.{0,15}安全事故责任.{0,10}自行承担', text):
        s -= 5
        b.append('运输装卸安全事故责任自担')
    if re.search(r'48\s*小时|24\s*小时内.{0,6}送达|响应', text):
        s += 8
        b.append('存在明确响应时限要求（须核实我方能否满足）')
    scores['交货方式'] = _clip(s)
    bases['交货方式'] = '，'.join(b)

    return scores, bases


# ---------------------------------------------------------------- 产品匹配

def score_product_match(profile, company):
    """清单条目数 vs 我方可供型号数（若公司档案提供）"""
    metrics = profile.get('metric_summary', {}) or {}
    item_count = metrics.get('item_count') or 0
    models = company.get('product_models') or company.get('available_models')
    if models and item_count:
        if isinstance(models, int):
            ratio = min(100.0, models / item_count * 100)
        else:
            ratio = min(100.0, len(models) / item_count * 100)
    else:
        ratio = 100.0
    basis = ('我方可供型号 %s 项，清单 %s 项，覆盖率约 %.0f%%'
             % (len(models) if models and not isinstance(models, int) else (models or '未限定'),
                item_count or '未识别', ratio))
    return round(ratio, 1), basis


# ---------------------------------------------------------------- 风险

def build_risks(profile, company, eco_hint=None):
    """从招标客观条款自动抽取风险"""
    risks = []
    veto = profile.get('veto_items') or []
    if veto:
        risks.append({
            'level': '高',
            'desc': '招标文件设有 %d 项否决投标情形，任一不满足即被否决：%s'
                    % (len(veto), '；'.join(v[:40] for v in veto[:3])),
            'mitigation': '编标与递交前逐条对照否决项自查，确认签字盖章、'
                          '保证金到账、报价不超限、文件份数与格式全部合规',
        })
    # 签章
    sm = profile.get('sign_method')
    if sm:
        risks.append({
            'level': '中',
            'desc': '签章要求：%s；未签字并加盖单位公章的投标文件将被否决' % sm,
            'mitigation': '按招标文件要求采用逐页盖章或骑缝章（以原文约定为准），'
                          '打印后逐页检查签字与盖章位置，避免漏页',
        })
    # 保证金
    bond = profile.get('bond')
    cap = profile.get('cap')
    if bond and cap:
        ratio = bond / cap
        risks.append({
            'level': '中' if ratio > 0.01 else '低',
            'desc': '投标保证金 %.2f 元（占最高限价 %.2f%%），须从基本账户转出并在截止前到账'
                    % (bond, ratio * 100),
            'mitigation': '提前办理转账并确认系统子账号；提交基本存款账户证明与转账凭证',
        })
    # 页数上限
    page_note = None
    for t in (profile.get('commercial_terms', {}) or {}).values():
        if isinstance(t, list):
            for x in t:
                if x and ('页' in str(x) and ('以内' in str(x) or '不超过' in str(x))):
                    page_note = str(x)[:80]
    if page_note:
        risks.append({
            'level': '中',
            'desc': '技术方案存在页数限制：%s，超限将在技术评分中扣分' % page_note,
            'mitigation': '技术方案按评分点组织，宋体小四排版，控制在限页数内',
        })
    # 清单同名项
    metrics = profile.get('metrics') or []
    names = {}
    for m in metrics:
        ctx = (m.get('context') or '').strip()
        if ctx:
            names.setdefault(ctx, []).append(m.get('raw', ''))
    dup = {k: v for k, v in names.items() if len(v) > 1}
    if dup:
        risks.append({
            'level': '中',
            'desc': '采购清单存在名称相同但规格不同的条目（%s），易报价串行'
                    % '、'.join(list(dup)[:3]),
            'mitigation': '报价表按招标清单原序号分行，备注列注明规格与图纸型号，逐行复核',
        })
    # 业绩门槛
    perf = company.get('performance', {})
    req = perf.get('required_count')
    avail = perf.get('available_count', len(perf.get('projects', [])))
    if req and avail < req:
        risks.append({
            'level': '高',
            'desc': '类似项目业绩可用 %d 项，低于要求 %s 项' % (avail, req),
            'mitigation': '补充可用业绩证明材料；确无法满足的须评估是否放弃投标',
        })
    # 资金占用
    if eco_hint and eco_hint.get('capital_occupation_ratio'):
        r = eco_hint['capital_occupation_ratio']
        if r > 0.15:
            risks.append({
                'level': '中',
                'desc': '资金占用占最高限价 %.2f%%，偏高' % (r * 100),
                'mitigation': '落实授信额度或以保函替代现金保证金，降低资金占用',
            })
    return risks


# ---------------------------------------------------------------- 建议

def build_recommendations(profile, company, decision_hint, score_hint, eco=None):
    """由结论与关键测算模板化生成建议"""
    recs = []
    dec = decision_hint
    if dec == 'GO':
        recs.append('参与投标。硬性资格门槛全部满足，综合评分处于可参与区间。')
    elif dec == 'CAUTION':
        recs.append('谨慎参与。综合评分 %.1f 分，须先落实下列补偿措施后再决策。'
                    % score_hint)
    else:
        recs.append('不建议参与。存在不满足的硬性门槛或关键条件不具备。')

    # 报价建议
    sq = (eco or {}).get('suggested_quote_range')
    if sq:
        rule = sq.get('basis', '')
        recs.append('报价控制在 %s ~ %s 元区间（%s），既不超最高限价，'
                    '也避免基准价法下因报价过低失分。'
                    % (format(sq['low'], ',.2f'), format(sq['high'], ',.2f'), rule))
    # 保证金
    bond = profile.get('bond')
    if bond:
        recs.append('投标保证金 %.2f 元须在投标截止前从基本账户到账，'
                    '并附基本存款账户证明与转账凭证。' % bond)
    # 业绩材料
    perf = company.get('performance', {})
    req = perf.get('required_count')
    avail = perf.get('available_count', len(perf.get('projects', [])))
    if req:
        recs.append('按要求准备 %s 项近三年类似业绩证明材料（合同或中标通知书复印件加盖公章），'
                    '我方可用 %d 项。' % (req, avail))
    # 截止前动作
    recs.append('投标截止前派员核对密封、签章、份数与电子版一致性；'
                '按招标文件要求携带法定代表人授权委托书及代理人身份证原件到场。')
    return recs


# ---------------------------------------------------------------- main

def build(profile, company):
    cap = profile.get('cap') or 0
    bond = profile.get('bond') or 0
    fee = profile.get('agency_fee') or 0
    perf_pct = profile.get('perf_bond_pct')
    perf_abs = profile.get('perf_bond')
    occupy = bond + fee + (cap * perf_pct / 100.0 if perf_pct else (perf_abs or 0))

    eco_hint = {
        'capital_occupation': round(occupy, 2),
        'capital_occupation_ratio': (occupy / cap) if cap else None,
    }

    pm_rate, pm_basis = score_product_match(profile, company)
    competitors = analyze_competitors(profile, company)
    comp_profiles = build_competitor_profiles(profile, company)
    comp_score, comp_basis = score_competition(profile, company)
    csum = competition_summary(competitors, comp_profiles)
    if csum:
        comp_basis = csum + '。' + comp_basis
    else:
        comp_basis = ('未取得可指认的实证竞品名单，以下为依据招标文件特征推导的'
                      '竞争格局判断。' + comp_basis)
    strat_score, strat_basis = score_strategy(profile, company)
    comm_scores, comm_bases = score_commercial(profile, company)
    risks = build_risks(profile, company, eco_hint)

    out = {
        '_note': '规则化自动研判结果（auto_assess.py 产出）。'
                 '可直接作为 evaluate_bid.py 的 --assessment 使用；'
                 '大模型如需深化判断，可在此基础上修订 competition/strategy/'
                 'commercial_scores/risks/recommendations 后覆盖。',
        'product_match_rate': pm_rate,
        'product_match_basis': pm_basis,
        'commercial_scores': comm_scores,
        'commercial_basis': '；'.join('%s %s（%s）' % (k, v, b)
                                     for k, v, b in
                                     zip(comm_scores.keys(), comm_scores.values(),
                                         comm_bases.values())),
        'competition': {'score': comp_score, 'basis': comp_basis},
        'competitors': competitors,
        'competitors_by_type': _group_by_type(competitors),
        'competitor_source': company.get('competitors_source')
                           or company.get('competitor_source') or '',
        'competitor_profiles': comp_profiles,
        'strategy': {'score': strat_score, 'basis': strat_basis},
        'risks': risks,
        'recommendations': [],   # 由 evaluate_bid 阶段按最终结论回填更准确
        'auto_generated': True,
        'eco_hint': eco_hint,
    }
    return out


def main():
    ap = argparse.ArgumentParser(description='规则化自动研判')
    ap.add_argument('--profile', required=True)
    ap.add_argument('--company', required=True)
    ap.add_argument('--competitors', default=None,
                    help='竞品清单 competitors.json（可选，由 competitor_intel '
                         'verify 产出）')
    ap.add_argument('--out', required=True)
    args = ap.parse_args()

    profile = _load(args.profile)
    company = _load(args.company)
    if args.competitors:
        cl = _load(args.competitors)
        items = cl.get('competitors') if isinstance(cl, dict) else cl
        company['competitors'] = items or []
        if isinstance(cl, dict) and cl.get('source'):
            company['competitors_source'] = cl['source']
    if not profile:
        print('[错误] 画像为空: %s' % args.profile, file=sys.stderr)
        return 2

    d = build(profile, company)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as fh:
        json.dump(d, fh, ensure_ascii=False, indent=2)

    print('[输出] %s' % os.path.abspath(args.out))
    print('  产品参数满足度 %.0f%%' % d['product_match_rate'])
    print('  竞争格局 %.1f 分 | 战略价值 %.1f 分' %
          (d['competition']['score'], d['strategy']['score']))
    if d.get('competitors'):
        print('  实证竞品 %d 家：%s' % (
            len(d['competitors']),
            '、'.join('%s(%.0f)' % (c['name'], c['threat'])
                      for c in d['competitors'][:5])))
        if d.get('competitor_source'):
            print('    竞品来源：%s' % d['competitor_source'])
    else:
        print('  实证竞品：未取得可指认的竞品名单（不输出代号占位名）')
    print('  竞争者类型画像 %d 类：%s' % (
        len(d.get('competitor_profiles') or []),
        '、'.join(p['type'] for p in (d.get('competitor_profiles') or []))))
    print('  商务可接受度：%s' % '、'.join('%s %.0f' % (k, v)
                                     for k, v in d['commercial_scores'].items()))
    print('  自动抽取风险 %d 条' % len(d['risks']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
