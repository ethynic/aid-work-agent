#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""quote-price-comparison 共享工具：文本/数字/单位归一化、相似度、配色常量与 IO。

被 extract_quotes.py / compare_prices.py / mark_sources.py 共同 import。
只依赖标准库，保证技能可移植。
"""
from __future__ import annotations

import difflib
import json
import os
import re
import sys
import unicodedata
from datetime import date

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Excel / Word / PDF 标记配色（红=贵，绿=便宜，遵循用户口径，不套用股市涨红跌绿）
RED_FILL, RED_FONT = "FFFFC7CE", "FF9C0006"
GREEN_FILL, GREEN_FONT = "FFC6EFCE", "FF006100"
FLAG_LABEL = {
    "expensive": "偏贵(红)",
    "cheap": "偏便宜(绿)",
    "neutral": "接近基准",
    "no_baseline": "无可比基准",
    "uncomparable": "不可比",
    "reference": "仅参考(网络价)",
}

# ---------------------------------------------------------------------------
# 单位体系（单一数据源，避免别名表与量纲表两处维护而失配）
#
# UOM_SPEC[原始写法] = (规范名, 量纲, 该单位相对量纲基准单位的倍率)
#
# 换算关系明确、无歧义的单位放同一量纲，可自动换算：
#   count  件/个/只/pcs=1, 打=12, 令=144, 千件/千只=1000, 万件=10000
#   weight 吨=1, kg=0.001, g=1e-6, mg=1e-9, 斤=0.0005, 磅≈0.00045359237, 盎司≈2.83495e-5
#   length 米=1, cm=0.01, mm=0.001, km=1000, 英寸=0.0254, 英尺=0.3048
#   area   ㎡=1, cm²=1e-4, km²=1e6, 亩≈666.667
#   volume m³=1, L=0.001, mL=1e-6, 加仑≈3.785412
#   time   小时=1, 分钟=1/60, 秒=1/3600, 天=24, 月=720
#
# 语义不同、不可互换的计数单位（支/根/条/张/片/块/把/副/双/瓶/袋/罐/盒/托/盘/卷/桶/捆…）
# 各自独立成量纲（dim 名唯一），同组遇到不同 dim 时判「不可比」而不是硬凑。
# ---------------------------------------------------------------------------
UOM_SPEC = {
    # ---- 计数 ----
    "件": ("件", "count", 1), "个": ("件", "count", 1), "只": ("件", "count", 1),
    "pcs": ("件", "count", 1), "pc": ("件", "count", 1), "piece": ("件", "count", 1),
    "pieces": ("件", "count", 1), "ea": ("件", "count", 1),
    "打": ("打", "count", 12), "令": ("令", "count", 144),
    "千件": ("千件", "count", 1000), "千只": ("千件", "count", 1000), "万件": ("万件", "count", 10000),
    # ---- 重量 ----
    "吨": ("吨", "weight", 1), "t": ("吨", "weight", 1), "ton": ("吨", "weight", 1),
    "tonne": ("吨", "weight", 1), "tons": ("吨", "weight", 1),
    "千克": ("kg", "weight", 0.001), "公斤": ("kg", "weight", 0.001),
    "kg": ("kg", "weight", 0.001), "kgs": ("kg", "weight", 0.001),
    "克": ("g", "weight", 0.000001), "g": ("g", "weight", 0.000001),
    "毫克": ("mg", "weight", 1e-9), "mg": ("mg", "weight", 1e-9),
    "斤": ("斤", "weight", 0.0005), "磅": ("磅", "weight", 0.00045359237),
    "盎司": ("盎司", "weight", 2.83495231e-5), "oz": ("盎司", "weight", 2.83495231e-5),
    # ---- 长度 ----
    "米": ("米", "length", 1), "m": ("米", "length", 1), "延米": ("延米", "length", 1),
    "meter": ("米", "length", 1), "metre": ("米", "length", 1),
    "厘米": ("cm", "length", 0.01), "cm": ("cm", "length", 0.01),
    "毫米": ("mm", "length", 0.001), "mm": ("mm", "length", 0.001),
    "千米": ("km", "length", 1000), "km": ("km", "length", 1000),
    "英寸": ("英寸", "length", 0.0254), "inch": ("英寸", "length", 0.0254),
    "英尺": ("英尺", "length", 0.3048), "ft": ("英尺", "length", 0.3048),
    # ---- 面积 ----
    "平方米": ("㎡", "area", 1), "㎡": ("㎡", "area", 1), "m2": ("㎡", "area", 1),
    "m²": ("㎡", "area", 1), "sqm": ("㎡", "area", 1),
    "平方厘米": ("cm²", "area", 0.0001), "cm²": ("cm²", "area", 0.0001),
    "平方千米": ("km²", "area", 1000000), "km²": ("km²", "area", 1000000),
    "亩": ("亩", "area", 2000.0 / 3.0),
    # ---- 体积 ----
    "立方米": ("m³", "volume", 1), "m3": ("m³", "volume", 1), "cbm": ("m³", "volume", 1),
    "m³": ("m³", "volume", 1),
    "升": ("L", "volume", 0.001), "l": ("L", "volume", 0.001),
    "liter": ("L", "volume", 0.001), "litre": ("L", "volume", 0.001),
    "毫升": ("mL", "volume", 0.000001), "ml": ("mL", "volume", 0.000001),
    "加仑": ("加仑", "volume", 3.785411784), "gal": ("加仑", "volume", 3.785411784),
    # ---- 时间 ----
    "小时": ("小时", "hour", 1), "h": ("小时", "hour", 1), "hour": ("小时", "hour", 1),
    "分钟": ("分钟", "minute", 1.0 / 60.0), "min": ("分钟", "minute", 1.0 / 60.0),
    "秒": ("秒", "second", 1.0 / 3600.0), "s": ("秒", "second", 1.0 / 3600.0),
    "天": ("天", "day", 24), "日": ("天", "day", 24), "工作日": ("天", "day", 24),
    "月": ("月", "month", 720),
    # ---- 独立计数单位（各自成量纲，语义不同不可互换） ----
    "套": ("套", "set", 1), "set": ("套", "set", 1), "sets": ("套", "set", 1),
    "台": ("台", "machine", 1), "unit": ("台", "machine", 1),
    "支": ("支", "stick", 1), "根": ("根", "stick", 1),
    "条": ("条", "strip", 1), "张": ("张", "sheet", 1), "片": ("片", "slice", 1),
    "块": ("块", "chunk", 1), "颗": ("颗", "grain", 1), "枚": ("枚", "piece2", 1),
    "把": ("把", "bunch", 1), "副": ("副", "pair", 1), "双": ("双", "pair", 1),
    "份": ("份", "portion", 1), "瓶": ("瓶", "bottle", 1), "袋": ("袋", "bag2", 1),
    "罐": ("罐", "can", 1), "盒": ("盒", "box2", 1), "托": ("托", "pallet", 1),
    "盘": ("盘", "tray", 1), "卷": ("卷", "roll", 1), "roll": ("卷", "roll", 1),
    "桶": ("桶", "drum", 1), "drum": ("桶", "drum", 1),
    "箱": ("箱", "carton", 1), "box": ("箱", "carton", 1), "carton": ("箱", "carton", 1),
    "包": ("包", "pack", 1), "pack": ("包", "pack", 1), "bag": ("包", "pack", 1),
    "捆": ("捆", "bundle", 1), "扎": ("扎", "bundle", 1),
    "批": ("批", "lot", 1), "lot": ("批", "lot", 1),
    "项": ("项", "item", 1), "item": ("项", "item", 1),
}

# 每个量纲的基准单位（归一化目标；用于并列时择一、以及展示口径）
DIMENSION_BASE_UNIT = {
    "count": "件", "weight": "吨", "length": "米", "area": "㎡", "volume": "m³",
    "hour": "小时", "minute": "分钟", "second": "秒", "day": "天", "month": "月",
    "set": "套", "machine": "台", "stick": "支", "strip": "条", "sheet": "张",
    "slice": "片", "chunk": "块", "grain": "颗", "piece2": "枚", "bunch": "把",
    "pair": "副", "portion": "份", "bottle": "瓶", "bag2": "袋", "can": "罐",
    "box2": "盒", "pallet": "托", "tray": "盘", "roll": "卷", "drum": "桶",
    "carton": "箱", "pack": "包", "bundle": "捆", "lot": "批", "item": "项",
}

# 兼容旧名（外部引用）
UOM_ALIASES = {k: v[0] for k, v in UOM_SPEC.items()}
UOM_DIMENSION = {v[0]: (v[1], v[2]) for v in UOM_SPEC.values()}



PUNCT_RE = re.compile(r"[\s\u3000,，.。;；:：/\\()（）\[\]【】<>《》\-_'\"“”‘’+*&#%$￥¥@!！?？~]+")
TOKEN_RE = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]")


def s(v) -> str:
    """任意单元格值 -> 干净字符串。"""
    if v is None:
        return ""
    return str(v).strip()


def to_float(v):
    """从 '¥4,200.00' / '4200元/吨' / 4200 / '1 250,50' 中解析数值，失败返回 None。"""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    t = s(v)
    if not t:
        return None
    t = t.replace("，", ",").replace(" ", "").replace("\u00a0", "")
    nums = re.findall(r"-?\d[\d,]*(?:\.\d+)?", t)
    if not nums:
        return None
    # 价格通常在文本末尾（如 "规格1.0 4200元"）；无明确货币符号时取最后一个数字
    cand = nums[-1] if len(nums) > 1 and not re.search(r"[¥￥$€£]|元|usd|cny|rmb", t.lower()) else nums[0]
    try:
        return float(cand.replace(",", ""))
    except ValueError:
        return None


def norm_text(v) -> str:
    """全角转半角 + 小写 + 去标点空白，用于匹配（不做语义同义替换，避免误配）。"""
    t = unicodedata.normalize("NFKC", s(v)).lower()
    return PUNCT_RE.sub("", t)


def norm_uom(v):
    """单位别名归一。返回 (标准单位, 是否识别)。未识别的原样返回，由比价脚本判定不可比。"""
    t = norm_text(v)
    if not t:
        return "", False
    if t in UOM_SPEC:
        return UOM_SPEC[t][0], True
    # 前缀匹配：单位带修饰前缀时（如 "每箱"、"箱装"）取最长命中
    best = None
    for k, val in UOM_SPEC.items():
        kt = norm_text(k)
        if len(kt) >= 2 and t.startswith(kt):
            if best is None or len(kt) > len(norm_text(best)):
                best = k
    if best:
        return UOM_SPEC[best][0], True
    return s(v), False


def uom_dimension(uom):
    """返回 (量纲, 该单位相对量纲基准单位的倍率)。量纲未知时返回 (None, None)。"""
    canon, known = norm_uom(uom)
    if not known:
        return None, None
    for k, (c, dim, mult) in UOM_SPEC.items():
        if c == canon:
            return dim, mult
    return None, None


def uom_base_unit(uom):
    """该单位所属量纲的基准单位（如 千件 → 件）。量纲未知返回 ''。"""
    dim, _ = uom_dimension(uom)
    if not dim:
        return ""
    return DIMENSION_BASE_UNIT.get(dim, "")


def uom_convert_multiplier(from_uom, to_uom):
    """同量纲单位换算倍率：price_to_base = price_from * mult。

    返回 (multiplier, ok)：
      multiplier = 从 from_uom 换算到 to_uom 需要的乘数（对价格）；
                   对数量而言方向相反（数量用 ÷）。
      例：from=千件, to=件 → 价格 ×0.001，数量 ÷1000。
    量纲不同或未知时返回 (None, False)。
    """
    dim_f, mult_f = uom_dimension(from_uom)
    dim_t, mult_t = uom_dimension(to_uom)
    if not dim_f or not dim_t or dim_f != dim_t:
        return None, False
    if mult_f == mult_t:
        return 1.0, True
    return mult_f / mult_t, True


def uom_pair_compatible(uom_price, uom_qty):
    """判断「价格单位」与「数量单位」是否属于同一量纲。

    同一量纲 → 可自动换算（例如 单价按千件、数量按件）。
    不同量纲 → 语义不同（如 单价按吨、数量按套），必须人工确认或显式给换算系数。
    """
    dim_p, _ = uom_dimension(uom_price)
    dim_q, _ = uom_dimension(uom_qty)
    if not dim_p or not dim_q:
        return False, None
    return dim_p == dim_q, dim_p




def detect_currency(v, default="CNY") -> str:
    t = s(v).upper()
    if not t:
        return default
    if any(k in t for k in ("¥", "￥", "CNY", "RMB", "人民币", "元")):
        return "CNY"
    if "USD" in t or "$" in t or "US$" in t:
        return "USD"
    if "EUR" in t or "€" in t:
        return "EUR"
    if "GBP" in t or "£" in t:
        return "GBP"
    if "JPY" in t or "日元" in t:
        return "JPY"
    if "HKD" in t:
        return "HKD"
    return default


def similarity(a: str, b: str) -> float:
    """品名/规格模糊相似度 0~1：字符集 Jaccard + 序列比对的加权。"""
    na, nb = norm_text(a), norm_text(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ta, tb = set(TOKEN_RE.findall(na)), set(TOKEN_RE.findall(nb))
    jac = len(ta & tb) / max(1, len(ta | tb))
    seq = difflib.SequenceMatcher(None, na, nb).ratio()
    return round(0.45 * jac + 0.55 * seq, 4)


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def dump_json(path, obj):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    return path


def dump_text(path, text):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def today() -> str:
    return date.today().isoformat()


def rel(p) -> str:
    try:
        return os.path.relpath(p, os.getcwd())
    except ValueError:
        return p
