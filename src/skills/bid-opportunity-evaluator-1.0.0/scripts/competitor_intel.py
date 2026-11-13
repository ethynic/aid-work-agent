#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
competitor_intel.py — 竞品情报采集与校验

背景
    投标可行性评估报告里的「竞品分析」必须落到**可指认的真实企业**，
    而不是「竞品A / 竞品B」这类代号 —— 代号在正式报告里没有决策价值。

    但技能本身**不得内置任何品牌名**（要服务于不同企业、不同品类）。
    因此本脚本承担两件事：

    1) probe  —— 生成「竞品检索任务书」
       依据招标文件画像（品类、区域、金额量级、价格分权重、业绩门槛、
       品牌门槛、质保与交付要求），推导出**该查什么**：候选竞品的类型
       划分、必须核实的属性、建议检索关键词、每类应给出的证据口径。
       任务书里不含任何真实品牌名，由上层 Agent 用其检索能力去填。

    2) verify —— 校验上层 Agent / 用户回传的竞品清单
       检测并剔除「占位代号」（竞品A、竞品B、某某公司、XX企业、待补充…），
       校验必要属性齐备，输出规范化 competitors.json 供研判引擎使用。

用法
    # 生成检索任务书
    python competitor_intel.py probe --profile tender_profile.json \
        --company company_profile.json --out competitor_brief.json

    # 校验回传的竞品清单
    python competitor_intel.py verify --input competitors_raw.json \
        --out competitors.json

输入格式（verify）
    {
      "source": "公开渠道检索 + 我方历史投标记录",
      "competitors": [
        {"name": "某真实品牌名", "type": "品牌型",
         "positioning": "…", "region": "…", "price_band": "高档",
         "overlap": "高", "in_project": null,
         "strength": "…", "weakness": "…",
         "evidence": "来源说明（官网/公开报道/我方投标记录）"}
      ]
    }

输出（verify）
    {
      "source": "…",
      "verified_at": "…",
      "competitors": [ … 规范化后 … ],
      "rejected": [ {"name": "竞品A", "reason": "占位代号"} ]
    }
"""
import argparse
import json
import os
import re
import sys

# ---------------------------------------------------------------- 占位代号识别

# 中文占位代号：竞品A / 竞品1 / 对手A / 品牌B / 某某公司 / XX企业 …
PLACEHOLDER_PATTERNS = [
    (r'^竞品\s*[A-Za-z0-9一二三四五六七八九十甲乙丙丁]+$', '占位代号'),
    (r'^(对手|竞争者|品牌|厂商|企业|公司)\s*[A-Za-z0-9一二三四五六七八九十甲乙丙丁]+$',
     '占位代号'),
    (r'^[XxＸ]{1,3}\s*(公司|企业|品牌|集团|竞品|对手)', '占位代号'),
    (r'^(某|某某|某甲|某乙|某丙)\s*(公司|企业|品牌|集团|厂商)?$', '占位代号'),
    (r'^[A-Za-z]\s*(公司|企业|品牌|集团)$', '占位代号'),
    (r'^(待补充|待定|待确认|TBD|TODO|N/?A|无|未知名|未知品牌|示例|样例)$',
     '占位代号'),
    (r'^(竞品|对手|品牌|厂商|企业|公司)\s*[A-Za-z0-9]+\s*[\(（].*[\)）]$', '占位代号'),
]

# 疑似占位（不直接剔除，但需人工确认）
SUSPECT_PATTERNS = [
    (r'^.{0,4}(示例|样例|测试|test|sample|example)', '疑似示例名称'),
    (r'^(某|某某)', '疑似泛指名称'),
]


def classify_name(name):
    """返回 (是否占位代号, 说明)。非占位返回 (False, '')。"""
    s = (name or '').strip()
    if not s:
        return True, '名称为空'
    for pat, reason in PLACEHOLDER_PATTERNS:
        if re.search(pat, s):
            return True, reason
    for pat, reason in SUSPECT_PATTERNS:
        if re.search(pat, s):
            return False, reason      # 不剔除，但提示
    return False, ''


# ---------------------------------------------------------------- 检索任务书

TYPE_PROFILES = [
    {
        'type': '头部品牌型',
        'trigger': '招标文件设品牌门槛（十大品牌/知名品牌/同等档次），或评标含品牌加分',
        'traits': '全国性品牌，渠道与品牌溢价明显，产能与全国服务网络完备',
        'check': ['是否在招标文件认可的品牌名单内',
                  '本项目所在区域是否有直营网点或授权经销商',
                  '同类工程集采项目的中标记录'],
        'brand_hunt': [
            '{品类} 十大品牌 排行',
            '{品类} 一线品牌 名单 官网',
            '{品类} 品牌 全国工程集采 中标',
            '{区域} {品类} 品牌 代理商 授权',
        ],
    },
    {
        'type': '区域深耕型',
        'trigger': '项目所在地有强势区域品牌，或清单量大、交付半径敏感',
        'traits': '本地渠道深、报价灵活、账期谈判意愿强，跨区服务能力弱',
        'check': ['在项目所在省份/城市的同类项目中标记录',
                  '本地仓配与售后响应半径',
                  '价格带与我方的重合程度'],
        'brand_hunt': [
            '{区域} {品类} 品牌 代理商 门店',
            '{区域} {品类} 工程 中标公示 供应商',
            '{区域} 建材市场 {品类} 经销商 品牌',
        ],
    },
    {
        'type': '价格导向型',
        'trigger': '价格分权重高（≥30 分）或评标含低价优先条款',
        'traits': '成本控制强、报价激进，以性价比切入，品质与售后稳定性可能不足',
        'check': ['低端产品线是否满足招标技术指标',
                  '历史报价水平与让利空间',
                  '在质量/工期敏感项目中的表现'],
        'brand_hunt': [
            '{品类} 性价比 品牌 排行',
            '{品类} 工程 厂家 直供 低价',
            '{品类} 贴牌 代工 供应 商标',
        ],
    },
    {
        'type': '工程集采型',
        'trigger': '招标人为总承包/地产商，采购集中度高、账期长',
        'traits': '专做工程集采，报价贴近成本，接受长账期，换量效率优先',
        'check': ['是否有地产/总包集采的供货业绩',
                  '能否接受本项目的付款与结算条件',
                  '产能与集中供货能力'],
        'brand_hunt': [
            '{品类} 工程集采 中标 供应商 公示',
            '{品类} 战略集采 签约 供应商',
            '{区域} {品类} 集采 中标 公告',
            '{品类} 建材 供应商 招标 中标公示',
        ],
    },
    {
        'type': '新进入者',
        'trigger': '项目品类或区域有新品牌跨界进入，或招标文件降低业绩门槛',
        'traits': '以低价或新概念搅局，正面冲突意愿强但根基浅',
        'check': ['是否为新设主体或跨品类进入',
                  '低价背后的现金流与产能支撑',
                  '履约与售后承诺的可信度'],
        'brand_hunt': [
            '{品类} 新品牌 跨界 进军',
            '{品类} 新锐品牌 2025 2026',
            '{区域} {品类} 新进入 供应商',
        ],
    },
]

REQUIRED_ATTRS = [
    ('name', '企业/品牌名称（真实可指认，不得使用代号）'),
    ('type', '竞争者类型，取自本任务书的类型划分'),
    ('positioning', '品牌定位简述'),
    ('region', '主要经营区域'),
    ('price_band', '价格带：高档 / 中档 / 经济'),
    ('overlap', '与我方重合度：高 / 中 / 低'),
    ('strength', '相对我方的优势'),
    ('weakness', '相对我方的劣势'),
    ('evidence', '信息来源：官网 / 公开报道 / 我方投标或供货记录'),
]


def _region_of(location):
    """从完整地址提取省/市级粒度，用于检索词。

    「海口市江东新区顺达路与仙乐路交叉路口处」→「海口市」
    """
    s = (location or '').strip()
    if not s:
        return ''
    # 优先「XX市」「XX自治州」
    m = re.search(r'([^，,。；;\s]{2,10}?(?:市|自治州|地区))', s)
    if m:
        return m.group(1)
    # 再取「XX区/XX县/XX新区」等次级行政单位
    m = re.search(r'([^，,。；;\s]{2,12}?(?:新区|开发区|区|县))', s)
    if m:
        return m.group(1)
    # 再取「XX省/XX自治区」
    m = re.search(r'([^，,。；;\s]{2,6}?(?:省|自治区))', s)
    if m:
        return m.group(1)
    return s[:8]


def build_brief(profile, company):
    """生成竞品检索任务书（不含任何真实品牌名）"""
    sc = profile.get('scoring', {}) or {}
    comps = {c['name']: c['score'] for c in (sc.get('components') or [])}
    price_w = comps.get('投标报价', 30)
    perf_w = comps.get('商务部分', 0) or comps.get('技术部分', 0)
    factors = ' '.join(f.get('name', '') for f in (sc.get('factors') or []))
    texts = ' '.join(str(v) for v in profile.values() if isinstance(v, str))
    terms = profile.get('commercial_terms', {}) or {}
    metrics = profile.get('metric_summary', {}) or {}
    cap = profile.get('cap') or 0

    scope = (profile.get('scope') or '').strip()
    location = (profile.get('location') or '').strip()
    qty = metrics.get('total_area') or metrics.get('count_max')

    # 触发哪些类型
    active_types = []
    for tp in TYPE_PROFILES:
        t = tp['trigger']
        hit = False
        if '品牌门槛' in t and re.search(
                r'十大品牌|知名品牌|同等档次|品牌加分|品牌影响力|品牌档次|'
                r'品牌受众度|品牌口碑|唯一授权书', texts):
            hit = True
        elif '价格分权重' in t and price_w >= 30:
            hit = True
        elif '业绩门槛' in t and re.search(r'业绩', factors):
            hit = True
        elif '总承包' in t and re.search(
                r'总承包|房地产开发|置业|建设集团|建筑工程|工程局|建工集团|'
                r'施工企业', texts):
            hit = True
        elif '新品牌' in t:
            hit = False     # 新进入者恒为候选类型，不靠特征触发
        if hit or tp['type'] == '新进入者':
            active_types.append(tp)

    # 检索关键词（通用，不含品牌名）
    kw = []
    cat = ''
    m = re.search(r'(瓷砖|陶瓷|地板|电梯|空调|防水|涂料|石材|管材|阀门|'
                  r'门窗|幕墙|五金|机电|智能|消防|电梯设备|铝合金|石膏板)', texts)
    if m:
        cat = m.group(1)
        kw.append('%s 品牌' % cat)
        kw.append('%s 工程集采 中标' % cat)
    region = _region_of(location)
    if region:
        kw.append('%s %s 代理商' % (region, cat or '建材'))
        kw.append('%s %s 项目 中标公示' % (region, cat or '建材'))
    kw.append('%s 十大品牌' % (cat or '行业'))
    kw.append('%s 厂家 实力' % (cat or '行业'))
    kw.append('%s 工程 项目 供货商' % (cat or '行业'))
    if re.search(r'业绩', factors):
        kw.append('%s 工程业绩 供应商 名单' % (cat or '行业'))

    # 每个候选类型 → 具体品牌检索指令（供上层 Agent 直接照着搜）
    brand_hunt = []
    for t in active_types:
        brand_hunt.append({
            'type': t['type'],
            'brand_features': t['traits'],
            'min_brands': 2,
            'search_queries': [
                q.format(品类=cat or '建材', 区域=region or '项目所在地')
                for q in (t.get('brand_hunt') or [])
            ],
            'verify_items': t['check'],
        })

    brief = {
        'generated_for': profile.get('project_name') or '（未识别项目）',
        'location': location,
        'region': region,
        'scope': scope,
        'cap': cap,
        'scale': ('%.2f %s' % (qty, metrics.get('primary_unit', '')))
                 if qty else '未识别',
        'price_weight': price_w,
        'performance_weight': perf_w,
        'scoring_factors': factors,
        'candidate_types': [
            {'type': t['type'], 'trigger': t['trigger'], 'traits': t['traits'],
             'must_verify': t['check']}
            for t in active_types
        ],
        'required_attributes': [
            {'key': k, 'desc': v} for k, v in REQUIRED_ATTRS
        ],
        'brand_hunt': brand_hunt,
        'search_keywords': kw,
        'data_source_expectation':
            '每家竞品须给出可追溯来源（企业官网 / 公开中标公示 / 我方历史投标记录）。'
            '不得填写「竞品A」「某品牌」等代号。',
        'deliverable':
            '按 brand_hunt 逐类检索，每类至少给出 2 个具体品牌名，'
            '填入 competitors_raw.json 后执行 competitor_intel.py verify。'
            '报告中「竞争者类型 → 该类型下的具体品牌」一栏由该清单填充。',
        '_note': '本任务书由脚本依据招标文件客观特征生成，不含任何企业名称；'
                 '真实品牌名由上层 Agent 检索后填入 competitors.json。',
    }
    return brief


# ---------------------------------------------------------------- 清单校验

def verify(raw):
    """校验回传的竞品清单，剔除占位代号"""
    if isinstance(raw, list):
        comps = raw
        source = '未标注'
    else:
        comps = raw.get('competitors') or []
        source = raw.get('source') or '未标注'

    ok, rejected = [], []
    seen = set()
    for c in comps:
        if isinstance(c, str):
            c = {'name': c}
        if not isinstance(c, dict):
            continue
        name = (c.get('name') or '').strip()
        is_ph, reason = classify_name(name)
        if is_ph:
            rejected.append({'name': name, 'reason': reason})
            continue
        if name in seen:
            rejected.append({'name': name, 'reason': '重复项'})
            continue
        seen.add(name)

        item = {
            'name': name,
            'type': (c.get('type') or '').strip(),
            'positioning': (c.get('positioning') or '').strip(),
            'region': (c.get('region') or '').strip(),
            'price_band': (c.get('price_band') or '').strip(),
            'overlap': (c.get('overlap') or '').strip(),
            'in_project': c.get('in_project'),
            'strength': (c.get('strength') or '').strip(),
            'weakness': (c.get('weakness') or '').strip(),
            'evidence': (c.get('evidence') or '').strip(),
            'our_counter': (c.get('our_counter') or '').strip(),
        }
        missing = [k for k, _ in REQUIRED_ATTRS
                   if k != 'in_project' and not item.get(k)]
        item['_missing'] = missing
        ok.append(item)

    return {
        'source': source,
        'competitors': ok,
        'rejected': rejected,
        'count': len(ok),
        'complete': [c['name'] for c in ok if not c['_missing']],
        'incomplete': [c['name'] for c in ok if c['_missing']],
    }


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description='竞品情报采集与校验')
    sub = ap.add_subparsers(dest='cmd')

    p = sub.add_parser('probe', help='生成竞品检索任务书')
    p.add_argument('--profile', required=True, help='tender_profile.json')
    p.add_argument('--company', default=None, help='company_profile.json（可选）')
    p.add_argument('--out', required=True)

    v = sub.add_parser('verify', help='校验回传的竞品清单')
    v.add_argument('--input', required=True, help='竞品清单 JSON')
    v.add_argument('--out', required=True)

    args = ap.parse_args()

    if args.cmd == 'probe':
        profile = {}
        with open(args.profile, encoding='utf-8') as fh:
            profile = json.load(fh)
        company = {}
        if args.company and os.path.isfile(args.company):
            with open(args.company, encoding='utf-8') as fh:
                company = json.load(fh)
        brief = build_brief(profile, company)
        _write(args.out, brief)
        print('[输出] %s' % os.path.abspath(args.out))
        print('  候选竞争者类型 %d 类：%s'
              % (len(brief['candidate_types']),
                 '、'.join(t['type'] for t in brief['candidate_types'])))
        print('  检索关键词 %d 组：%s'
              % (len(brief['search_keywords']),
                 '；'.join(brief['search_keywords'][:4])))
        print('  品牌检索指令 %d 类（每类至少 2 个具体品牌）：'
              % len(brief['brand_hunt']))
        for bh in brief['brand_hunt']:
            print('    · %s：%s'
                  % (bh['type'], ' ｜ '.join(bh['search_queries'][:2])))
        print('  任务书不含任何企业名称，须由上层 Agent 检索后回填')
        return 0

    if args.cmd == 'verify':
        raw = {}
        with open(args.input, encoding='utf-8') as fh:
            raw = json.load(fh)
        res = verify(raw)
        _write(args.out, res)
        print('[输出] %s' % os.path.abspath(args.out))
        print('  有效竞品 %d 家：%s'
              % (res['count'],
                 '、'.join(c['name'] for c in res['competitors']) or '无'))
        if res['rejected']:
            print('  剔除占位/无效项 %d 个：%s'
                  % (len(res['rejected']),
                     '；'.join('%s（%s）' % (r['name'], r['reason'])
                               for r in res['rejected'])))
        if res['incomplete']:
            print('  属性不全（不影响使用，建议补齐）：%s'
                  % '、'.join(res['incomplete']))
        return 0

    ap.print_help()
    return 2


def _write(path, data):
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


if __name__ == '__main__':
    sys.exit(main())
