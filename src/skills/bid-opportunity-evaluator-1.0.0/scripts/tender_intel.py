#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
tender_intel.py — 招标信息解析器（文件操作层，全部由脚本完成）

两个子命令
    announce   解析招标公告 → 公告要素（项目、招标人、时间节点、限价、保证金、资质要求、报名方式）
    document   解析招标文件 → 项目概况 / 资格要求 / 商务条款 / 评分办法 / 否决项 / 规模指标

设计要点
    - 规模指标**通用提取**：识别「数量+单位」组合，覆盖 台/套/个/辆/件/m²/㎡/延米/m/米/
      吨/kg/套组/批/项 等，适配设备、装修、总包等不同品类，不绑定单一行业。
    - 纯机械提取，**不做判断**。判断（是否投标、竞争分析）交给大模型。

用法
    python tender_intel.py announce <公告.pdf|docx> --out <目录>
    python tender_intel.py document <招标文件.docx|pdf> --out <目录>
    python tender_intel.py document <文件> --out <目录> --grep "技术规格"

输出
    <目录>/tender_announce.json   公告要素（announce 模式）
    <目录>/tender_profile.json    招标文件结构化画像（document 模式）
    <目录>/tender_text.md         纯文本版，便于人读
"""
import argparse
import json
import os
import re
import sys

# ---------------------------------------------------------------- 单位词典
# 规模指标单位 → (类别, 展示名)
# 明确不是「规模」的时间/场所单位，避免误判
NON_SCALE_UNITS = {'个工作日', '个月', '年', '日', '天', '室', '楼', '层', '号'}

UNIT_MAP = {
    '台': ('count', '台'), '套': ('count', '套'), '个': ('count', '个'),
    '部': ('count', '部'), '件': ('count', '件'), '辆': ('count', '辆'),
    '座': ('count', '座'), '栋': ('count', '栋'), '幢': ('count', '幢'),
    '组': ('count', '组'), '副': ('count', '副'), '支': ('count', '支'),
    '台套': ('count', '台套'), '面': ('count', '面'), '处': ('count', '处'),
    '项': ('count', '项'), '标段': ('count', '标段'), '标': ('count', '标段'),
    'm²': ('area', '㎡'), '㎡': ('area', '㎡'), 'm2': ('area', '㎡'),
    '平方米': ('area', '㎡'), '平方': ('area', '㎡'),
    'm³': ('volume', 'm³'), '立方米': ('volume', 'm³'),
    '延米': ('length', '延米'), '米': ('length', 'm'), 'm': ('length', 'm'),
    'km': ('length', 'km'), '公里': ('length', 'km'),
    '吨': ('weight', '吨'), 't': ('weight', '吨'), 'kg': ('weight', 'kg'),
    '批': ('batch', '批'), '批次': ('batch', '批次'), '台份': ('count', '台份'),
}

# 常见中文数字（用于「十二台」这类表述）
CN_NUM = {'零': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5,
          '六': 6, '七': 7, '八': 8, '九': 9, '十': 10, '百': 100, '千': 1000}

# ---------------------------------------------------------------- 词表

RE_PROJECT_NAME = [
    r'项目名称[：:]\s*([^\n，。；]+)',
    r'工程名称[：:]\s*([^\n，。；]+)',
    r'标段名称[：:]\s*([^\n，。；]+)',
]
RE_TENDERER = [
    r'招标人[：:]\s*([^\n，。；（(]+)',
    r'招标单位[：:]\s*([^\n，。；（(]+)',
    r'采购人[：:]\s*([^\n，。；（(]+)',
    r'建设单位[：:]\s*([^\n，。；（(]+)',
]
RE_AGENT = [
    r'招标代理[：:]\s*([^\n，。；（(]+)',
    r'代理机构[：:]\s*([^\n，。；（(]+)',
    r'招标代理机构[：:]\s*([^\n，。；（(]+)',
]
RE_CONTACT = [
    r'联系人[：:]\s*([^\n，。；（(]+)',
    r'联系电话[：:]\s*([^\n，。；（(]+)',
    r'电\s*话[：:]\s*([^\d（(]*[\d\-]{7,})',
]
RE_PROJECT_CODE = [
    r'项目代码[：:]\s*([\w\-]+)',
    r'招标编号[：:]\s*([^\n，。；]+)',
    r'项目编号[：:]\s*([^\n，。；]+)',
    r'备案号[：:]\s*([^\n，。；]+)',
]
RE_CAP = [
    r'招标控制价[：:]\s*([\d,，.]+\s*(?:万元|元|万|亿)?)',
    r'最高投标限价[：:]\s*([\d,，.]+\s*(?:万元|元|万|亿)?)',
    r'最高限价[：:]\s*([\d,，.]+\s*(?:万元|元|万|亿)?)',
    r'项目预算[：:]\s*([\d,，.]+\s*(?:万元|元|万|亿)?)',
    r'采购预算[：:]\s*([\d,，.]+\s*(?:万元|元|万|亿)?)',
    r'预算金额[：:]\s*([\d,，.]+\s*(?:万元|元|万|亿)?)',
    r'暂估价[：:]\s*([\d,，.]+\s*(?:万元|元|万|亿)?)',
]
RE_BOND = [
    r'投标保证金的金额[为是：:]?\s*([\d,，.]+\s*(?:万元|元|万)?)',
    r'投标保证金金额为[：:]\s*([\d,，.]+\s*(?:万元|元|万)?)',
    r'投标保证金金额[：:]\s*([\d,，.]+\s*(?:万元|元|万)?)',
    r'保证金金额[：:]\s*([\d,，.]+\s*(?:万元|元|万)?)',
]
RE_PERF_BOND = [
    # 「履约保证金金额：¥150000.00元」—— 绝对额形式
    r'履约保证金的?金额[：:]\s*[¥￥]?\s*([\d,，.]+\s*(?:万元|元|万)?)',
]
RE_PERF_BOND_PCT = [
    # 「履约保证金的金额：中标金额的10%」—— 百分比形式
    r'履约保证金的?金额[：:]\s*中标(?:合同|项目)?金额的\s*([\d.]+)\s*%',
    r'履约保证金[^\n]{0,20}?中标(?:合同|项目)?金额的\s*([\d.]+)\s*%',
]
RE_AGENCY_FEE = [
    # 「招标代理服务费金额：人民币陆万玖仟柒佰元整（¥69700.00元）」
    r'招标代理服务费金额[：:]\s*[^（(]{0,30}[（(]?\s*[¥￥]?\s*([\d,，.]+\s*(?:万元|元|万)?)',
    r'招标代理服务费[：:]?\s*金额?[：:]?\s*[¥￥]?\s*([\d,，.]+\s*(?:万元|元|万)?)',
    r'代理服务费金额[：:]\s*[^（(]{0,30}[（(]?\s*[¥￥]?\s*([\d,，.]+\s*(?:万元|元|万)?)',
]
RE_VALIDITY = [
    r'投标有效期[：:]?\s*(?:自[^，。]{0,20}起)?\s*(\d+)\s*日历天',
    r'投标有效期[：:]?\s*(\d+)\s*天',
]
RE_DEADLINE = [
    # 允许「投标文件递交的截止时间(投标截止时间，下同)2026 年07 月03 日09：00 时」
    r'投标(?:文件)?(?:递交)?(?:的)?截止时间[^\n]{0,20}?(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日[^\n]{0,30})',
    r'投标(?:文件)?(?:递交)?(?:的)?截止时间[：:]\s*([^\n]{6,50})',
    r'投标截止时间[：:]\s*([^\n]{6,50})',
    r'截止时间[：:]\s*([^\n]{6,50})',
    r'(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日\s*\d{1,2}\s*[：:]\s*\d{1,2}\s*时)',
]
RE_BID_OPEN = [
    r'开标时间[：:]?\s*(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日[^\n]{0,30})',
    r'开标时间[：:]\s*([^\n]{6,50})',
]
RE_BID_OPEN_PLACE = [
    r'开标地点[：:]\s*([^\n]{2,60})',
    r'地点[：:]\s*([^\n]{4,60}开标室[^\n]{0,30})',
    # 通用：任何「XX市/XX区公共资源交易中心」均可匹配，不绑定具体城市
    r'([^\n，。）)]{2,25}公共资源交易(?:中心|服务平台)[^\n，。）)]{0,40})',
]
RE_LOCATION = [
    r'建设地点[：:]\s*([^\n]{2,60})',
    r'交货地点[：:]\s*([^\n]{2,60})',
    r'项目地点[：:]\s*([^\n]{2,60})',
    r'工程地点[：:]\s*([^\n]{2,60})',
]
RE_DURATION = [
    r'(?:交货期|供货期|工期|服务期|履行期限|施工周期)[：:]\s*([^\n]{2,80})',
]
RE_SCOPE = [
    r'招标范围[：:]\s*([^\n]{2,200})',
    r'采购范围[：:]\s*([^\n]{2,200})',
    r'项目规模[：:]\s*([^\n]{2,300})',
]
RE_QUALITY = [
    r'质量标准[：:]\s*([^\n]{2,60})',
    r'质量要求[：:]\s*([^\n]{2,60})',
]
RE_FUND = [
    r'资金来源[：:及]?\s*([^\n]{2,50})',
    r'出资比例[：:]\s*([^\n]{2,30})',
]
RE_VENUE = [
    r'(?:递交|获取|购买)(?:招标文件|文件)[^\n]{0,30}?(?:平台|网站|系统)[：:（(]?\s*([^\n）)]{4,80})',
    # 通用：任何「XX市/XX区公共资源交易平台」与「全国公共资源交易平台」均可匹配
    r'([^\n，。）)]{2,25}公共资源交易(?:中心|服务平台|平台)[^\n，。）)]{0,30})',
    r'(全国公共资源交易平台[^\n，。）)]{0,30})',
]
RE_JOINT = [r'(是否接受|不接受|接受)\s*联合体']
RE_BOND_FORM = [
    r'(?:投标保证金|保证金)[^\n]{0,20}?的形式[：:]\s*([^\n]{2,80})',
]
RE_SIGN_METHOD = [
    # 签章要求常在表格单元格里（行内以 / 分隔），故允许跨分隔符取完整表述，
    # 避免只取到「骑缝章」而漏掉并列的「逐页盖章」选项
    r'((?:逐页加盖[^/\n]{0,24}公章|加盖[^/\n]{0,8}骑缝章)'
    r'(?:\s*[或和、]?\s*/?\s*(?:[^/\n]{0,12})?'
    r'(?:逐页加盖[^/\n]{0,24}公章|加盖[^/\n]{0,8}骑缝章))*)',
    r'(逐页加盖[^/\n]{0,24}公章|加盖[^/\n]{0,8}骑缝章)',
]


def _first(patterns, text, group=1):
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            try:
                v = m.group(group)
            except IndexError:
                v = m.group(0)
            if v:
                return v.strip()
    return None


def _all(patterns, text):
    out = []
    for pat in patterns:
        for m in re.finditer(pat, text):
            v = m.group(1) if m.groups() else m.group(0)
            if v:
                out.append(v.strip())
    return out


def cn_to_int(s):
    if not s:
        return None
    s = s.strip()
    if s.isdigit():
        return int(s)
    if not all(c in CN_NUM for c in s):
        return None
    total, num = 0, 0
    for c in s:
        n = CN_NUM[c]
        if n >= 10:
            total += (num or 1) * n
            num = 0
        else:
            num = n
    return total + num


def parse_amount(s, require_unit=True):
    """'13,438,990.93元' → 13438990.93；'6000万元' → 60000000

    require_unit=True 时，数值必须带货币单位才算有效 ——
    否则会把目录页码（如「投标保证金\t87」）误判成金额。
    """
    if not s:
        return None
    m = re.search(r'([\d,，.]+)', s)
    if not m:
        return None
    try:
        val = float(m.group(1).replace(',', '').replace('，', ''))
    except ValueError:
        return None
    unit = s[m.end():].strip()
    has_currency = ('元' in unit) or ('万' in unit) or ('亿' in unit)
    if require_unit and not has_currency:
        return None
    if '亿' in unit:
        val *= 1e8
    elif '万' in unit:
        val *= 1e4
    return round(val, 2)


# ---------------------------------------------------------------- 规模指标

METRIC_RE = re.compile(
    r'(?:约|计|共|合计|总计|不少于|不超过|不低于|不高于|暂估|暂定)?\s*'
    r'([\d,，.]+|[零一二两三四五六七八九十百千]+)\s*'
    r'(台套|台份|台|套|个|部|件|辆|座|栋|幢|组|副|支|面|处|项|标段|标|'
    r'm²|㎡|m2|平方米|m³|立方米|延米|公里|km|米|m|吨|t|kg|批|批次)'
)


# 噪声上下文：章节编号、日期、金额、比例、电话、证书号等
NOISE_CTX = [
    (r'^[\s\d.．、]*$', 1),                       # 前文全是编号
    (r'\d{4}\s*年', 1),                            # 年份
    (r'第[一二三四五六七八九十\d]+[章节条项部分款]', 1),
    (r'元|万元|%|％', 1),                           # 金额/比例
    (r'https?://|www\.', 1),
    (r'[：:]\s*$', 1),                             # 冒号结尾（栏目标题）
    (r'^[（(]?\d+[）)]?\s*[、．.]\s*\S{0,6}[：:]', 1),  # 「2.1 项目名称：」
]


def _is_noise(text, start):
    """判断匹配位置是否属于编号/日期/金额等噪声"""
    lo = max(0, start - 26)
    pre = text[lo:start]
    for pat, _ in NOISE_CTX:
        if re.search(pat, pre):
            return True
    return False


# 规模语义锚点：只有出现在这些词附近，才是真正的规模指标
SCALE_ANCHORS = [
    r'项目规模', r'建设规模', r'招标规模', r'采购规模', r'工程规模', r'供货规模',
    r'规模', r'数量[为及：:]', r'暂估数量', r'工程量', r'采购数量', r'主要工程量',
    r'建设内容', r'招标内容', r'采购内容', r'工作内容', r'服务内容', r'供货内容',
    r'计划数量', r'预计数量', r'约需', r'共计', r'总用量',
]
SCALE_WINDOW = 90     # 锚点后 90 字符内视为规模上下文
ANCHOR_RE = re.compile('|'.join(SCALE_ANCHORS))


def _in_scale_context(text, pos):
    """判断位置是否落在规模语义窗口内"""
    lo = max(0, pos - 30)
    win = text[lo:pos + SCALE_WINDOW]
    return bool(ANCHOR_RE.search(win))


def extract_metrics(text):
    """通用提取「数量+单位」规模指标。

    两条路径：
      1) 采购清单表（权威，见 extract_boq_metrics）
      2) 正文散句 —— **必须落在规模语义窗口内**（锚点词附近），
         否则会把「5 个工作日」「2 幢 11 室」「6 个月」等误当规模。
    """
    found = []
    for m in METRIC_RE.finditer(text):
        if _is_noise(text, m.start()):
            continue
        if not _in_scale_context(text, m.start()):
            continue
        num_s, unit = m.group(1), m.group(2)
        val = None
        if re.fullmatch(r'[\d,，.]+', num_s):
            try:
                val = float(num_s.replace(',', '').replace('，', ''))
            except ValueError:
                val = None
        else:
            val = cn_to_int(num_s)
        if val is None:
            continue
        # 组合单位「个工作日」「个月」等不是规模
        tail = text[m.end():m.end() + 3]
        if unit in NON_SCALE_UNITS or tail.startswith(('个工作日', '个月')):
            continue
        # 语义排除：「一套方案」「一批文件」「一个标段」等固定搭配不是规模
        # 注意数字可能用中文（「一套方案」），故前后都要查
        after = text[m.end():m.end() + 6]
        before = text[max(0, m.start() - 6):m.start()]
        if re.match(r'(方案|文件|资料|标段|形式|包|份|版本|复印件)', after) \
                or re.search(r'(方案|文件|资料|标段|形式|包|份|版本|复印件)$', before):
            continue
        cat, disp = UNIT_MAP.get(unit, ('count', unit))
        if cat == 'count' and val == int(val):
            val = int(val)
        found.append({
            'value': val, 'unit': disp, 'category': cat,
            'raw': m.group(0).strip(), 'context': _context(text, m.start())
        })
    # 同类同值去重
    seen, uniq = set(), []
    for f in found:
        k = (f['category'], f['value'], f['unit'])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(f)
    return uniq


def _context(text, pos, span=45):
    lo, hi = max(0, pos - span), min(len(text), pos + span)
    return text[lo:hi].replace('\n', ' ').strip()


def aggregate_metrics(metrics):
    """按类别汇总，给出主规模指标"""
    agg = {}
    for m in metrics:
        agg.setdefault(m['category'], []).append(m)
    summary = {}
    for cat, items in agg.items():
        # 面积类取合计
        if cat == 'area':
            total = sum(i['value'] for i in items if isinstance(i['value'], (int, float)))
            summary['area_total'] = round(total, 2)
        counts = [i['value'] for i in items if i['category'] == 'count']
        if counts:
            summary['count_max'] = max(counts)
    if 'count_max' in summary:
        summary['primary_count'] = summary['count_max']
        summary['primary_unit'] = next(
            (i['unit'] for i in metrics
             if i['category'] == 'count' and i['value'] == summary['count_max']), '')
    return summary


# ---------------------------------------------------------------- 文本抽取

def read_docx(path):
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    d = docx.Document(path)
    out = []
    for child in d.element.body.iterchildren():
        tag = child.tag.split('}')[-1]
        if tag == 'p':
            t = Paragraph(child, d).text.strip()
            if t:
                out.append(t)
        elif tag == 'tbl':
            tb = Table(child, d)
            out.append('[[TABLE]]')
            for r in tb.rows:
                cells = [c.text.strip().replace('\n', ' / ') for c in r.cells]
                out.append(' | '.join(cells))
            out.append('[[/TABLE]]')
    return '\n'.join(out)


def read_pdf(path):
    import fitz
    d = fitz.open(path)
    return '\n'.join(d.load_page(i).get_text('text') for i in range(d.page_count))


def read_xlsx(path):
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    out = []
    for ws in wb.worksheets:
        out.append('【工作表】%s' % ws.title)
        for r, row in enumerate(ws.iter_rows(values_only=True), 1):
            vals = ['' if v is None else str(v).replace('\n', ' / ') for v in row]
            while vals and vals[-1] == '':
                vals.pop()
            if vals:
                out.append(' | '.join(vals))
    wb.close()
    return '\n'.join(out)


def read_any(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == '.docx':
        return read_docx(path)
    if ext == '.pdf':
        return read_pdf(path)
    if ext in ('.xlsx', '.xls'):
        return read_xlsx(path)
    if ext in ('.txt', '.md'):
        with open(path, encoding='utf-8') as fh:
            return fh.read()
    raise ValueError('不支持的格式: %s' % ext)


def read_input(path):
    """读入公告/招标文件。

    支持三类输入：
      1) 文件路径（docx/pdf/xlsx/txt/md）
      2) 直接传入的公告正文文本（--from-text，落到临时 txt 再解析）
      3) 已经是纯文本的文件
    """
    if path and os.path.isfile(path):
        return read_any(path)
    # 可能是纯文本内容（上层 Agent 直接给正文）
    if path and len(path) > 200 and '\n' in path:
        return path
    raise ValueError('输入不存在或不是可识别格式: %s' % str(path)[:60])


# ---------------------------------------------------------------- 公告解析

def parse_announce(text):
    ann = {}
    ann['project_name'] = _first(RE_PROJECT_NAME, text)
    ann['project_code'] = _first(RE_PROJECT_CODE, text)
    ann['tenderer'] = _first(RE_TENDERER, text)
    ann['agency'] = _first(RE_AGENT, text)
    ann['contacts'] = _all(RE_CONTACT, text)[:4]
    ann['location'] = _first(RE_LOCATION, text)
    ann['scope'] = _first(RE_SCOPE, text)
    ann['duration'] = _first(RE_DURATION, text)
    ann['quality'] = _first(RE_QUALITY, text)
    ann['fund_source'] = _first(RE_FUND, text)
    ann['bid_open_time'] = _first(RE_BID_OPEN, text)
    ann['bid_open_venue'] = _first(RE_BID_OPEN_PLACE, text)
    ann['deadline'] = _first(RE_DEADLINE, text)
    ann['cap_raw'] = _first(RE_CAP, text)
    ann['cap'] = parse_amount(ann['cap_raw'])
    ann['bond_raw'] = _first(RE_BOND, text)
    ann['bond'] = parse_amount(ann['bond_raw'])
    ann['bond_form'] = _first(RE_BOND_FORM, text)
    ann['perf_bond_raw'] = _first(RE_PERF_BOND, text)
    ann['perf_bond'] = parse_amount(ann['perf_bond_raw'])
    _pct = _first(RE_PERF_BOND_PCT, text)
    ann['perf_bond_pct'] = float(_pct) if _pct else None
    ann['agency_fee_raw'] = _first(RE_AGENCY_FEE, text)
    ann['agency_fee'] = parse_amount(ann['agency_fee_raw'])
    ann['validity_raw'] = _first(RE_VALIDITY, text)
    ann['validity_days'] = int(re.search(r'(\d+)', ann['validity_raw']).group(1)) \
        if ann['validity_raw'] and re.search(r'(\d+)', ann['validity_raw']) else None
    ann['venues'] = _all(RE_VENUE, text)[:3]
    ann['joint_venture'] = bool(re.search(r'不接受\s*联合体', text))
    ann['sign_method'] = _collect_sign(text)
    ann['metrics'] = extract_metrics(text)
    ann['metric_summary'] = aggregate_metrics(ann['metrics'])
    # 资格要求：抽取"投标人资格要求"到下一章节之间的文本
    ann['qualification_text'] = _slice_section(text, '投标人资格要求', 2600)
    ann['documents_notice'] = _slice_section(text, '招标文件的获取', 1200, last=True)
    return ann


def _collect_sign(text):
    """收集全部签章方式表述并合并。

    招标文件的签章要求常写成并列的两条（骑缝章 / 逐页盖章），
    分别落在表格单元格内、彼此被说明性括号隔开，逐条正则只能取到其一。
    这里收集所有出现过的签章表述，去重后用「或」连接，保持原意完整。
    """
    pats = [
        r'逐页加盖[^/\n]{0,20}单位?公章',
        r'加盖[^/\n]{0,12}骑缝章',
    ]
    found = []
    for p in pats:
        for m in re.finditer(p, text):
            v = re.sub(r'\s+', '', m.group(0))
            v = v.replace('（指在骑缝页加盖单位公章）', '')
            if v and v not in found:
                found.append(v)
    if not found:
        return None
    if len(found) == 1:
        return found[0]
    return ' 或 '.join(found)


def _slice_section(text, start_kw, maxlen=2000, last=False):
    """截取以 start_kw 开头的小节。

    目录页与正文同名，必须跳过目录：目录行含制表符+页码，或后接大量章节标题。
    last=True 时取最后一次出现（正文通常在目录之后）。
    """
    idxs = [m.start() for m in re.finditer(re.escape(start_kw), text)]
    if not idxs:
        return ''
    if last:
        i = idxs[-1]
    else:
        # 取第一个不在目录区域的（目录行形如「xxx\t12」）
        i = None
        for cand in idxs:
            nxt = text[cand + len(start_kw):cand + len(start_kw) + 30]
            # 目录行特征：关键词后紧跟（制表符/空格）+页码，随后换行或又一行标题
            if re.match(r'[\s\t]{0,4}\d{1,3}(?=\s|$)', nxt):
                continue          # 目录行，跳过
            i = cand
            break
        if i is None:
            i = idxs[-1]
    seg = text[i:i + maxlen]
    # 遇到下一个同级编号章节则截断
    m = re.search(r'\n\s*(?:第[一二三四五六七八九十\d]+[章节]|[一二三四五六七八九十]+[、．]|\d+\s*[．.]\s*\S)', seg[12:])
    if m:
        seg = seg[:12 + m.start()]
    return seg.strip()


# ---------------------------------------------------------------- 招标文件解析

def extract_boq_metrics(file_path, text):
    """从采购清单表提取规模指标（表格里的数量列才准，正文散句不可靠）"""
    if not file_path.lower().endswith('.docx'):
        return []
    try:
        import docx
        from docx.table import Table
        d = docx.Document(file_path)
    except Exception:
        return []
    out = []
    for child in d.element.body.iterchildren():
        if child.tag.split('}')[-1] != 'tbl':
            continue
        tb = Table(child, d)
        rows = [[c.text.strip().replace('\n', ' ') for c in r.cells] for r in tb.rows]
        if len(rows) < 3:
            continue
        head = ' '.join(rows[0]) + ' ' + ' '.join(rows[1] if len(rows) > 1 else [])
        if not re.search(r'暂估数量|工程量|数量', head):
            continue
        # 定位名称列与数量列
        hr = ' '.join(rows[0])
        name_idx = None
        for i, h in enumerate(rows[0]):
            if '名称' in h:
                name_idx = i
                break
        qty_idx = None
        for i, h in enumerate(rows[0]):
            if re.search(r'暂估数量|工程量|数量', h):
                qty_idx = i
                break
        unit_idx = None
        for i, h in enumerate(rows[0]):
            if h.strip() in ('单位', '计量单位'):
                unit_idx = i
                break
        if qty_idx is None:
            continue
        last_unit = ''
        for r in rows[1:]:
            if qty_idx >= len(r):
                continue
            qraw = r[qty_idx]
            # 数量列只取数字，不跨列吸收（招标文件常有单位列空缺的情况）
            m = re.fullmatch(r'\s*([\d,，.]+)\s*([㎡m²m2平?方米|吨|t|台|套|个|件|延米|米|m|项|批])?\s*',
                             qraw)
            if not m:
                continue
            try:
                val = float(m.group(1).replace(',', '').replace('，', ''))
            except ValueError:
                continue
            # 单位优先取「单位列」；单位列为空时退回数量列自带单位；
            # 再空则沿用上一行的单位（同一清单内单位一致）
            unit = ''
            if unit_idx is not None and unit_idx < len(r):
                unit = r[unit_idx].strip()
            if not unit and m.group(2):
                unit = m.group(2).strip()
            if not unit and last_unit:
                unit = last_unit
            if unit:
                last_unit = unit
            unit = unit.replace('m²', '㎡').replace('m2', '㎡')
            if not unit:
                continue
            cat = UNIT_MAP.get(unit, ('area' if '㎡' in unit else 'count', unit))
            name = r[name_idx][:40] if name_idx is not None and name_idx < len(r) else ''
            out.append({'value': val, 'unit': cat[1], 'category': cat[0],
                        'raw': qraw.strip(), 'context': name, 'source': '采购清单'})
    return out


def parse_document(text, file_path=None):
    doc = {}
    doc['project_name'] = _first(RE_PROJECT_NAME, text)
    doc['project_code'] = _first(RE_PROJECT_CODE, text)
    doc['tenderer'] = _first(RE_TENDERER, text)
    doc['agency'] = _first(RE_AGENT, text)
    doc['location'] = _first(RE_LOCATION, text)
    doc['scope'] = _first(RE_SCOPE, text)
    doc['duration'] = _first(RE_DURATION, text)
    doc['quality'] = _first(RE_QUALITY, text)
    doc['fund_source'] = _first(RE_FUND, text)
    doc['cap_raw'] = _first(RE_CAP, text)
    doc['cap'] = parse_amount(doc['cap_raw'])
    doc['bond_raw'] = _first(RE_BOND, text)
    doc['bond'] = parse_amount(doc['bond_raw'])
    doc['bond_form'] = _first(RE_BOND_FORM, text)
    doc['agency_fee_raw'] = _first(RE_AGENCY_FEE, text)
    doc['agency_fee'] = parse_amount(doc['agency_fee_raw'])
    doc['perf_bond_raw'] = _first(RE_PERF_BOND, text)
    doc['perf_bond'] = parse_amount(doc['perf_bond_raw'])
    _pct = _first(RE_PERF_BOND_PCT, text)
    doc['perf_bond_pct'] = float(_pct) if _pct else None
    doc['validity_raw'] = _first(RE_VALIDITY, text)
    doc['validity_days'] = int(re.search(r'(\d+)', doc['validity_raw']).group(1)) \
        if doc['validity_raw'] and re.search(r'(\d+)', doc['validity_raw']) else None
    doc['deadline'] = _first(RE_DEADLINE, text)
    doc['bid_open_venue'] = _first(RE_BID_OPEN_PLACE, text)
    doc['joint_venture'] = bool(re.search(r'不接受\s*联合体', text))
    doc['sign_method'] = _collect_sign(text)
    doc['multi_volume'] = bool(re.search(r'需要分册装订', text)) and \
        not bool(re.search(r'不需要分册装订', text))

    # 项目概况（概况章节）
    doc['overview_text'] = _slice_section(text, '项目概况', 1800, last=True)
    doc['tech_spec_text'] = _slice_section(text, '技术规格', 1800, last=True) or \
        _slice_section(text, '供货要求', 1800, last=True)
    # 资格要求：第一章公告处（首个非目录出现）+ 前附表 1.4.1 条款
    q1 = _slice_section(text, '投标人资格要求', 2600)
    q2 = _slice_section(text, '投标人资质条件、能力和信誉', 2600)
    doc['qualification_text'] = q1
    doc['qualification_detail_text'] = q2

    # 规模指标：优先采购清单表（准确），辅以正文散句
    doc['boq_metrics'] = extract_boq_metrics(file_path or '', text)
    doc['metrics'] = doc['boq_metrics'] or extract_metrics(text)
    doc['metrics_source'] = '采购清单' if doc['boq_metrics'] else '正文'
    doc['metric_summary'] = aggregate_metrics(doc['metrics'])
    if doc['boq_metrics']:
        s = doc['metric_summary']
        s['total_area'] = round(sum(m['value'] for m in doc['boq_metrics']
                                  if m['category'] == 'area'), 2)
        s['item_count'] = len(doc['boq_metrics'])

    # 资格要求逐条
    doc['qualifications'] = _parse_qualification_items(doc['qualification_text'])

    # 评分办法
    doc['scoring'] = _parse_scoring(text)

    # 否决 / 实质性要求
    doc['veto_items'] = _parse_veto(text)

    # 商务条款
    doc['commercial_terms'] = _parse_commercial(text)

    return doc


def _parse_qualification_items(text):
    """从资格要求段落中拆出条目"""
    items = []
    if not text:
        return items
    body = re.sub(r'^.*?投标人资格要求', '', text, count=1, flags=re.S)
    # 按 "3.1 " "3.2 " 或 "（1）" "1." 切
    parts = re.split(r'\n\s*(?:\d+\.\d+\s*[．.]|\(\d+\)|（\d+）|\d+\s*[．.]\s*)',
                     body)
    for p in parts:
        p = p.strip()
        if len(p) < 8:
            continue
        if not re.search(r'资格|财务|信誉|纳税|社保|业绩|业绩|设备|技术能力|'
                         r'法人|联合体|授权|诉讼|仲裁|证书|承诺函|许可证', p):
            continue
        items.append(p[:300])
    return items


def _parse_scoring(text):
    """抽取评标办法：分值构成 + 评分因素 + 基准价算法"""
    sc = {'method': None, 'components': [], 'factors': [], 'base_price': None}
    m = re.search(r'本次评标采用([^\n。]{2,20})', text)
    if m:
        sc['method'] = m.group(1).strip()
    m = re.search(r'分值构成[^\n]{0,10}\(?总分\s*(\d+)\s*分\)?', text)
    if m:
        sc['total_score'] = int(m.group(1))
    # 商务/技术/报价分值
    for label in ['商务部分', '技术部分', '投标报价']:
        m = re.search(label + r'[^\n]{0,20}?(\d+)\s*分', text)
        if m:
            sc['components'].append({'name': label, 'score': int(m.group(1))})
    # 评分因素
    for m in re.finditer(r'([一-鿿]{2,12})\s*[（(](\d+)\s*分[）)]', text):
        sc['factors'].append({'name': m.group(1).strip(), 'score': int(m.group(2))})
    # 去重保序
    seen, uniq = set(), []
    for f in sc['factors']:
        if f['name'] in seen:
            continue
        seen.add(f['name'])
        uniq.append(f)
    sc['factors'] = uniq[:12]
    m = re.search(r'评标基准价[^。\n]{0,20}?(?:算术平均值|平均值)[^。\n]{0,60}', text)
    if m:
        sc['base_price'] = m.group(0).strip()[:120]
    m = re.search(r'每高于基准价的\s*1%\s*[（(]?含\s*1%\s*[）)]?\s*[^\n，,]{0,20}'
                  r'扣\s*([\d.]+)\s*分[^\n]{0,40}?每低于基准价\s*1%[^\n]{0,40}?扣\s*([\d.]+)\s*分',
                  text)
    if m:
        sc['price_rule'] = {'higher_deduct': float(m.group(1)),
                            'lower_deduct': float(m.group(2))}
    return sc


def _parse_veto(text):
    """抽取否决投标情形。

    向前补到最近的句读边界，避免只截到「否决其投标」这类残缺片段。
    """
    items = []
    pats = [
        r'否决(?:其|贵)(?:投标|公司)[^\n]{0,140}',
        r'作无效标处理[^\n]{0,80}',
        r'视为无效投标[^\n]{0,80}',
        r'投标(?:资格|文件)?(?:将被)?否决[^\n]{0,120}',
    ]
    for p in pats:
        for m in re.finditer(p, text):
            frag = m.group(0).strip()
            # 向前取到最近的句读/换行边界，形成完整语义单元
            lo = m.start()
            for _ in range(6):
                cand = max(text.rfind(c, max(0, lo - 60), lo)
                           for c in '。；\n')
                if cand < 0:
                    break
                lo = cand + 1
            s = text[lo:m.end()].strip(' 　/；')
            if not (8 < len(s) < 200):
                s = frag
            if s and s not in items:
                items.append(s)
    return items[:15]


def _parse_commercial(text):
    """抽取关键商务条款"""
    terms = {}
    terms['payment'] = _slice_section(text, '支付', 700)
    terms['settlement'] = _slice_section(text, '结算', 700)
    terms['warranty'] = re.findall(r'质量(?:保修|保证)期[^\n。]{0,40}', text)[:2]
    terms['after_sale'] = re.findall(r'\d+\s*小时内[^\n。]{0,60}', text)[:3]
    terms['delivery'] = re.findall(r'(?:交货期|供货期)[^\n。]{0,60}', text)[:3]
    terms['brand'] = re.findall(r'[△★☆][^\n。]{0,80}', text)[:8]
    return terms


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description='招标信息解析器')
    sub = ap.add_subparsers(dest='cmd', required=True)

    a = sub.add_parser('announce', help='解析招标公告')
    a.add_argument('file', help='公告文件路径；也可用 --from-text 传入公告正文')
    a.add_argument('--out', required=True)
    a.add_argument('--from-text', default=None,
                   help='直接传入公告正文文本（上层 Agent 检索后提供）')

    d = sub.add_parser('document', help='解析招标文件')
    d.add_argument('file', help='招标文件路径；也可用 --from-text 传入正文')
    d.add_argument('--out', required=True)
    d.add_argument('--grep', default=None)
    d.add_argument('--from-text', default=None,
                   help='直接传入招标文件正文文本（上层 Agent 检索后提供）')

    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    src_label = args.file
    if getattr(args, 'from_text', None):
        text = args.from_text
        src_label = '直接传入的正文文本'
    else:
        try:
            text = read_input(args.file)
        except Exception as e:
            print('[错误] 解析失败 %s: %s' % (str(args.file)[:60], e), file=sys.stderr)
            return 2

    base = '输入正文' if getattr(args, 'from_text', None) \
        else os.path.splitext(os.path.basename(args.file))[0]
    with open(os.path.join(args.out, 'tender_text.md'), 'w', encoding='utf-8') as fh:
        fh.write('# 来源：%s\n\n%s' % (src_label, text))

    if args.cmd == 'announce':
        data = parse_announce(text)
        path = os.path.join(args.out, 'tender_announce.json')
    else:
        data = parse_document(text, None if getattr(args, 'from_text', None) else args.file)
        path = os.path.join(args.out, 'tender_profile.json')

    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)

    print('[输出] %s' % path)
    if args.cmd == 'announce':
        print('  项目：%s' % (data.get('project_name') or '(未识别)'))
        print('  招标人：%s' % (data.get('tenderer') or '(未识别)'))
        print('  截止：%s' % (data.get('deadline') or '(未识别)'))
        print('  控制价：%s' % (data.get('cap_raw') or '(未识别)'))
        print('  保证金：%s' % (data.get('bond_raw') or '(未识别)'))
        print('  规模指标：%d 项' % len(data.get('metrics', [])))
    else:
        print('  项目：%s' % (data.get('project_name') or '(未识别)'))
        print('  地点：%s' % (data.get('location') or '(未识别)'))
        print('  工期：%s' % (data.get('duration') or '(未识别)'))
        print('  控制价：%s' % (data.get('cap_raw') or '(未识别)'))
        print('  规模指标：%d 项，主指标 %s'
              % (len(data.get('metrics', [])), data.get('metric_summary')))
        print('  资格条目：%d；评分因素：%d；否决项：%d'
              % (len(data.get('qualifications', [])), len(data.get('scoring', {}).get('factors', [])),
                 len(data.get('veto_items', []))))

    if getattr(args, 'grep', None):
        print('\n===== grep: %s =====' % args.grep)
        lines = text.split('\n')
        for i, ln in enumerate(lines):
            if args.grep in ln:
                for j in range(max(0, i - 1), min(len(lines), i + 3)):
                    print(('  > ' if j == i else '    ') + lines[j][:220])
    return 0


if __name__ == '__main__':
    sys.exit(main())
