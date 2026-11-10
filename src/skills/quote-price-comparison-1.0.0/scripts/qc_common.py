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

UOM_ALIASES = {
    "件": "件", "个": "件", "只": "件", "pcs": "件", "pc": "件", "piece": "件", "pieces": "件", "ea": "件",
    "套": "套", "set": "套", "sets": "套",
    "台": "台", "unit": "台",
    "吨": "吨", "t": "吨", "ton": "吨", "tonne": "吨", "tons": "吨",
    "千克": "kg", "公斤": "kg", "kg": "kg", "kgs": "kg",
    "克": "g", "g": "g",
    "米": "米", "m": "米", "延米": "米", "meter": "米", "metre": "米",
    "平方米": "㎡", "㎡": "㎡", "m2": "㎡", "m²": "㎡", "sqm": "㎡",
    "立方米": "m³", "m3": "m³", "cbm": "m³",
    "升": "L", "l": "L", "liter": "L", "litre": "L",
    "箱": "箱", "box": "箱", "carton": "箱",
    "包": "包", "pack": "包", "bag": "包",
    "卷": "卷", "roll": "卷",
    "桶": "桶", "drum": "桶",
    "批": "批", "lot": "批",
    "项": "项", "item": "项",
    "小时": "小时", "h": "小时", "hour": "小时",
    "次": "次", "time": "次",
}

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
    if t in UOM_ALIASES:
        return UOM_ALIASES[t], True
    for k, val in UOM_ALIASES.items():
        if t.startswith(norm_text(k)) and len(norm_text(k)) >= 2:
            return val, True
    return s(v), False


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
