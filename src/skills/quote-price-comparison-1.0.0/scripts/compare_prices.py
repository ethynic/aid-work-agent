#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""归一化 -> 品项匹配 -> 基准价 -> 红绿判定 -> 横向对比 Excel + 摘要 + 标色指令。

核心不变量（务必保持）：
  1. 同一品项组内，所有来源的「单价×数量」必须折算到同一口径后保持相等（金额守恒）。
     因此单位换算时，**单价与数量必须同步换算**：单价 ×f，数量 ÷f。
     只换单价不换数量 → 合计金额随"选哪个标准单位"漂移（典型 1000 倍）。
  2. 基准价、红绿判定一律在「组标准单位」下计算，与组标准单位的选取无关（只差显示尺度）。
  3. 网络价只进参考列，不参与 min/max/价差/来源数，避免"搜到几条网价"改变结论数字。

用法：
  python compare_prices.py --quotes quotes.json --out-dir out/ \
      [--baseline median|min|mean|web|library] [--baseline-sources upload,library] \
      [--tolerance 0.05] [--tax-basis incl|excl|asis] [--target-currency CNY] \
      [--fx fx.json] [--target-qty 100] [--match-threshold 0.82] \
      [--include-web-in-baseline]

产出（均在 --out-dir 下）：
  comparison.xlsx   横向对比 + 明细数据 + 异常与不可比 + 口径与图例
  comparison.json   结构化结果
  marks.json        源报价单标色指令（交给 mark_sources.py）
  summary.docx      可直接交付给用户的对比结果摘要（Word 格式）
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qc_common import (  # noqa: E402
    DIMENSION_BASE_UNIT, FLAG_LABEL, GREEN_FILL, GREEN_FONT, RED_FILL, RED_FONT,
    dump_json, dump_text, load_json, norm_text, similarity, today, uom_dimension,
)


# ---------- 归一化（币种 / 税务；单位换算在分组后按组统一处理） ----------
def normalize_items(items, cfg, notes):
    """先做币种与税务折算，得到 price_fx_tax（仍是源报价单位的单价）。"""
    fx = cfg["fx"]
    for it in items:
        reasons = []
        if it.get("quote_status") == "no_quote" or it.get("unit_price") in (None, ""):
            it["price_fx_tax"] = None
            it["norm_reasons"] = [str(it.get("quote_note") or "源单未报出价格")]
            continue
        p = float(it.get("unit_price") or 0)
        cur = (it.get("currency") or cfg["target_currency"]).upper()
        if cur != cfg["target_currency"]:
            rate = fx.get(cur)
            if rate:
                p *= float(rate)
                reasons.append(f"{cur}→{cfg['target_currency']} @{rate}")
            else:
                it["price_fx_tax"] = None
                it["norm_reasons"] = [f"币种 {cur} 未提供汇率，无法折算"]
                continue
        rate = float(it.get("tax_rate") or 0)
        incl = bool(it.get("tax_included", True))
        if cfg["tax_basis"] == "incl" and not incl:
            p *= (1 + rate)
            reasons.append(f"未税→含税 @{rate:.0%}")
        elif cfg["tax_basis"] == "excl" and incl:
            p /= (1 + rate) if rate > -1 else 1
            reasons.append(f"含税→未税 @{rate:.0%}")
        it["price_fx_tax"] = p
        it["norm_reasons"] = reasons
        if not it.get("standard_uom"):
            it["standard_uom"] = it.get("uom") or ""
    return items


# ---------- 品项匹配（并查集合并模糊同款；按量纲分组） ----------
def build_groups(items, cfg, notes):
    """分组键 = match_key 或 品名|规格|数量单位量纲。

    注意：分组键用「数量单位的量纲」而不是「价格单位」。
    - 千件 与 个 同属 count 量纲 → 归入同一组，组内再统一换算到组标准单位。
      若把具体单位写进键，两者会天然分裂成两组，只能靠人工强行对齐 unit_multiplier，
      两次执行口径必然不同。
    - 单价按「元/吨」、数量按「千克」时，按数量口径（重量量纲）分组才是可比口径；
      单价按「元/箱」、数量按「包」时，箱与包是不同量纲，需显式给 qty_to_price_uom，
      此时按数量单位量纲（pack）分组。
    """
    exact = defaultdict(list)
    for it in items:
        q_uom = it.get("qty_uom") or it.get("uom") or ""
        dim, _ = uom_dimension(q_uom)
        if not dim:
            dim, _ = uom_dimension(it.get("uom") or "")
        key = it.get("match_key") or (norm_text(it.get("name")) + "|" + norm_text(it.get("spec")) + "|" + (dim or ""))
        it["_group_seed"] = key
        exact[key].append(it)
    keys = list(exact)
    parent = {k: k for k in keys}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    suspicious = []
    for i, ka in enumerate(keys):
        ra = exact[ka][0]
        for kb in keys[i + 1:]:
            rb = exact[kb][0]
            na, nb = ra.get("name", ""), rb.get("name", "")
            sa, sb = ra.get("spec", ""), rb.get("spec", "")
            sim_name = similarity(na, nb)
            sim = sim_name if not (sa and sb) else 0.7 * sim_name + 0.3 * similarity(sa, sb)
            dim_a, _ = uom_dimension(ra.get("qty_uom") or ra.get("uom") or "")
            dim_b, _ = uom_dimension(rb.get("qty_uom") or rb.get("uom") or "")
            if dim_a and dim_b and dim_a != dim_b:
                continue  # 数量量纲不同绝不自动合并
            if sim >= cfg["match_threshold"] and find(ka) != find(kb):
                union(ka, kb)
                notes.append(f"模糊合并：『{na} / {sa}』≈『{nb} / {sb}』 相似度 {sim:.2f}")
            elif 0.60 <= sim < cfg["match_threshold"]:
                suspicious.append(f"疑似同款未合并（相似度 {sim:.2f}）：『{na} {sa}』 vs 『{nb} {sb}』")
    merged = defaultdict(list)
    for k in keys:
        merged[find(k)] += exact[k]
    return list(merged.values()), suspicious


# ---------- 单位统一（组标准单位 + 金额守恒） ----------
def rep_name(group):
    """组代表品名，用于异常提示。"""
    return (group[0].get("name") or "") if group else ""


def resolve_group_units(group, cfg, notes, anomalies):
    """为组内所有条目选定统一的标准单位，单价与数量同步换算，保证金额守恒。

    规则（确定性，不依赖执行者）：
      组标准单位 = 组内「价格单位」出现次数最多的单位；并列时取该量纲基准单位。
      单价_std = 单价_src × (倍率_std / 倍率_价格单位)
      数量_std = 数量_src × (倍率_数量单位 / 倍率_std)

      价格单位与数量单位可以不同（如 单价 5280 元/吨、数量 12000 千克）：
        两者同量纲时按倍率自动换算；不同量纲时必须显式给 `qty_to_price_uom`
        （1 个价格单位 = 多少个数量单位），否则该条标不可比，绝不臆测。
      => 单价_std × 数量_std == 单价_src × 数量_src × (倍率_数量单位 / 倍率_价格单位)
         当数量与价格同单位时即为金额守恒（最常见情形）。
    """
    quoted = [i for i in group if i.get("quote_status") != "no_quote" and i.get("price_fx_tax") is not None]
    for it in group:
        it.setdefault("standard_uom", it.get("uom") or "")
        it["price_norm"] = None
        it["qty_std"] = None
        it["amount_std"] = None
        it["unit_factor_price"] = 1.0
        it["unit_factor_qty"] = 1.0
    if not quoted:
        return "", None

    # 量纲口径：以「数量单位」的量纲为可比口径（数量才是采购口径）
    qdims = {uom_dimension(i.get("qty_uom") or i.get("uom") or "")[0] for i in quoted}
    qdims.discard(None)
    dim = next(iter(qdims)) if len(qdims) == 1 else None
    if dim is None:
        for it in quoted:
            it["norm_reasons"] = list(it.get("norm_reasons") or []) + [
                "同组内数量单位量纲不一致，无法统一比较口径"]
        return "", None
    pdims = {uom_dimension(i.get("uom") or "")[0] for i in quoted}
    pdims.discard(None)
    if len(pdims) > 1:
        anomalies.append(
            f"{rep_name(group)}：组内价格单位量纲不一致（{', '.join(sorted(pdims))}），"
            f"已按数量单位口径（{DIMENSION_BASE_UNIT.get(dim, dim)}）统一，请复核各条单价含义")

    # 标准单位：优先按「数量单位」选取（数量是比价的采购口径），
    # 其次按价格单位。并列时取该量纲基准单位。
    def _pick(counter, dim):
        top = max(counter.values()) if counter else 0
        if not top:
            return ""
        cands = sorted([u for u, n in counter.items() if n == top])
        if len(cands) > 1:
            base = DIMENSION_BASE_UNIT.get(dim, "")
            return base if base in cands else cands[0]
        return cands[0]

    q_counts = Counter(i.get("qty_uom") or i.get("uom") or "" for i in quoted)
    p_counts = Counter(i.get("uom") or "" for i in quoted)
    target = _pick(q_counts, dim) or _pick(p_counts, dim)
    if not target:
        for it in quoted:
            it["norm_reasons"] = list(it.get("norm_reasons") or []) + ["无法确定组标准单位"]
        return "", None
    _, mult_t = uom_dimension(target)

    for it in quoted:
        uom_p = it.get("uom") or ""
        _, mult_p = uom_dimension(uom_p)
        if not mult_p:
            it["norm_reasons"] = list(it.get("norm_reasons") or []) + ["源单位未识别，无法换算"]
            continue
        uom_q = it.get("qty_uom") or uom_p
        dim_p, _ = uom_dimension(uom_p)
        dim_q, mult_q = uom_dimension(uom_q)
        if not mult_q:
            it["norm_reasons"] = list(it.get("norm_reasons") or []) + ["数量单位未识别，无法换算"]
            continue

        # ratio_eff = 1 个数量单位(qty_uom) 等于多少个价格单位(uom)
        #   同量纲：按倍率自动得出（1 个 = 1/1000 千件 → ratio_eff=0.001）
        #   跨量纲：人工给 qty_to_price_uom（1 箱 = 20 包 → 1 包 = 0.05 箱 → ratio_eff=0.05）
        explicit = it.get("qty_to_price_uom")
        if explicit not in (None, ""):
            try:
                ratio_eff = float(explicit)
            except (TypeError, ValueError):
                ratio_eff = None
            if ratio_eff is None or ratio_eff <= 0:
                it["norm_reasons"] = list(it.get("norm_reasons") or []) + [
                    f"qty_to_price_uom 取值无效（{explicit!r}），该条不可比"]
                continue
            uom_note = (f"价格单位 {uom_p} 与数量单位 {uom_q} 跨量纲，"
                        f"按人工给定 1{uom_q}={ratio_eff:g}{uom_p} 换算")
        elif dim_p != dim_q:
            it["norm_reasons"] = list(it.get("norm_reasons") or []) + [
                f"价格单位({uom_p})与数量单位({uom_q})量纲不同，无法自动换算；"
                "请确认换算关系后在条目里显式填 qty_to_price_uom（1 数量单位 = 多少价格单位）"]
            continue
        else:
            ratio_eff = mult_q / mult_p
            uom_note = ""

        # 统一到组标准单位（标准单位取自数量单位口径）
        #   ratio_eff = 1 个数量单位(qty_uom) 等于多少个价格单位(uom)
        #   price_t = price_src × ratio_eff × (mult_target / mult_q)   ← 换到「每标准单位」
        #   qty_t   = qty_src   × (mult_q / mult_target)                ← 换到「标准单位数」
        #   金额    = price_src × qty_src × ratio_eff
        #   同量纲时 ratio_eff = mult_q/mult_p 且 mult_target = mult_q
        #   ⇒ 金额 = price_src × qty_src × (mult_q/mult_p)
        #   当数量与价格同单位时 mult_q = mult_p ⇒ 金额守恒（最常见情形）。
        mq_t = mult_q / mult_t
        mt_q = mult_t / mult_q
        it["unit_factor_price"] = ratio_eff * mt_q
        it["unit_factor_qty"] = mq_t
        it["price_norm"] = it["price_fx_tax"] * ratio_eff * mt_q
        it["qty_std"] = (it.get("qty") or 0) * mq_t
        it["amount_std"] = it["price_norm"] * it["qty_std"]
        rs = list(it.get("norm_reasons") or [])
        if abs(it["unit_factor_price"] - 1.0) > 1e-9 or uom_note:
            rs.append(f"单位 {uom_p}→{target}：单价×{it['unit_factor_price']:g}，数量×{mq_t:g}"
                       + (f"；{uom_note}" if uom_note else ""))
        # 与源单自带金额列交叉核对（源单内部一致性）
        # 关键：源单金额列与 unit_price 必须同税基。多数报价单金额列与价格列一致
        # （含税价配含税金额）；若金额列是未税而价格列含税（反之亦然），按税率折算后再比。
        amt = it.get("amount_reported")
        if amt:
            try:
                amt_f = float(amt)
            except (TypeError, ValueError):
                amt_f = None
            if amt_f:
                price_basis_incl = bool(it.get("tax_included", True))
                amount_basis_incl = it.get("amount_tax_included")
                if amount_basis_incl is None:
                    amount_basis_incl = price_basis_incl  # 默认同口径
                rate = float(it.get("tax_rate") or 0)
                src_amount = float(it.get("unit_price") or 0) * (it.get("qty") or 0) * ratio_eff
                if bool(amount_basis_incl) != price_basis_incl and rate > -1:
                    src_amount = src_amount * (1 + rate) if not price_basis_incl else src_amount / (1 + rate)
                if src_amount > 0 and abs(src_amount - amt_f) / amt_f > 0.01:
                    rs.append(f"源单自检：单价×数量={src_amount:,.2f} 与源单金额列 {amt_f:,.2f} 不符")
                    it["amount_check"] = "不符"
                else:
                    it["amount_check"] = "一致"
        it["norm_reasons"] = rs

    # 组采购数量：取上传来源（用户实单采购量），并核对各来源是否一致
    up_qty = [i["qty_std"] for i in quoted
              if i.get("source_type") == "upload" and i.get("qty_std") is not None and (i.get("qty") or 0) > 0]
    if not up_qty:
        up_qty = [i["qty_std"] for i in quoted if i.get("qty_std") is not None]
    target_qty = None
    if up_qty:
        vals = sorted(set(round(v, 6) for v in up_qty))
        target_qty = max(vals)
        if len(vals) > 1:
            anomalies.append(
                f"组内上传来源采购数量不一致（换算后 {', '.join(f'{v:,.2f}' for v in vals)}）："
                "合计按最大数量口径计算，请复核各家报价数量")
    return target, target_qty


def check_qty_unit_consistency(items, anomalies):
    """校验「数量单位」与「数量值」是否自洽。

    典型错误：源单写「80,000 个」，执行者把数量单位改成「千件」却忘了把数量改成 80，
    于是数量凭空缩小 1000 倍。表现为组内各来源采购数量对不上。

    判定：以组标准单位折算后的数量，与同组其他来源比较；差异超过 1% 即记异常。
    """
    by_group = defaultdict(list)
    for it in items:
        if it.get("group_id"):
            by_group[it["group_id"]].append(it)
    for gid, rows in by_group.items():
        ups = [r for r in rows if r.get("source_type") == "upload" and r.get("qty_std")]
        if len(ups) < 2:
            continue
        vals = [r["qty_std"] for r in ups]
        # 取中位数作为"合理数量"的参照
        med = statistics.median(vals)
        if not med:
            continue
        for r in ups:
            if med and abs(r["qty_std"] - med) / med > 0.01:
                anomalies.append(
                    f"{gid} {r.get('name')} / {r.get('supplier')}：采购数量换算后为 {r['qty_std']:,.2f}，"
                    f"与同组其他来源（{med:,.2f}）不一致——请检查「数量单位」与「数量值」是否同步修改"
                    f"（原值 {r.get('qty'):,} {r.get('qty_uom') or r.get('uom')}）")


# ---------- 判定 ----------
def baseline_of(vals, method):
    vals = [v for v in vals if v is not None and math.isfinite(v) and v > 0]
    if not vals:
        return None
    if method == "min":
        return min(vals)
    if method == "mean":
        return statistics.mean(vals)
    return statistics.median(vals)


def flag_of(price, baseline, tol, is_web):
    if price is None or baseline is None:
        return "uncomparable" if price is None else "no_baseline"
    if is_web and not baseline:
        return "reference"
    d = (price - baseline) / baseline
    if d > tol:
        return "expensive"
    if d < -tol:
        return "cheap"
    return "neutral"


def analyze(groups, cfg, notes):
    results, anomalies, marks = [], [], []
    bs_sources = set(cfg["baseline_sources"])
    if cfg["include_web_in_baseline"]:
        bs_sources.add("web")
    for gi, items in enumerate(groups, 1):
        rep = items[0]
        gid = f"G{gi:03d}"
        std_uom, group_qty = resolve_group_units(items, cfg, notes, anomalies)
        for it in items:
            it["_dup"] = False

        # 同一来源多条报价：取单价最低档或最接近目标数量的一条，其余记为异常
        by_source = defaultdict(list)
        for i in items:
            by_source[(i.get("source_type"), i.get("supplier"))].append(i)
        chosen, duplicates = [], []
        for key, lst in by_source.items():
            if len(lst) > 1:
                lst = sorted(lst, key=lambda x: (abs((x.get("qty") or 0) - (cfg["target_qty"] or 0)), x.get("price_norm") or 1e18))
                duplicates += lst[1:]
                anomalies.append(f"{gid} {rep.get('name')}：来源 {key[1]} 有 {len(lst)} 条重复报价，已取最接近目标数量的一条，其余见明细")
            chosen.append(lst[0])
        for it in duplicates:
            it["_dup"] = True

        base_pool = [
            i["price_norm"] for i in chosen
            if not i.get("_dup") and i.get("source_type") in bs_sources
            and i.get("price_norm") is not None
        ]
        if cfg["baseline"] in ("web", "library"):
            base_pool = [
                i["price_norm"] for i in chosen
                if i.get("source_type") == cfg["baseline"] and i.get("price_norm") is not None
            ]
        web_pool = [i["price_norm"] for i in chosen if i.get("source_type") == "web" and i.get("price_norm") is not None]
        baseline = baseline_of(base_pool, cfg["baseline"])
        basis_note = f"{'+'.join(sorted(bs_sources))} 的{cfg['baseline']}价"
        if len(base_pool) == 1:
            # 单一可比报价不存在「横向」基准，不得据自身判红/绿
            baseline, basis_note = None, "仅 1 条可比报价，无横向基准"
        elif baseline is None and web_pool:
            baseline = baseline_of(web_pool, "median")
            basis_note = "仅网络参考价中位数（非成交口径）"
        elif baseline is None:
            basis_note = "无可比基准"

        qty = cfg["target_qty"] or group_qty or 1
        rows = []
        for it in chosen:
            p = it.get("price_norm")
            is_web = it.get("source_type") == "web"
            no_quote = it.get("quote_status") == "no_quote"
            if no_quote:
                fl, reason = "no_quote", str(it.get("quote_note") or "源单未报出价格")
            elif p is None:
                fl, reason = "uncomparable", "；".join(it.get("norm_reasons") or ["价格缺失或单位不可比"])
            elif baseline is None:
                fl, reason = "no_baseline", "该品项只有单一来源，无横向基准"
            elif is_web and "web" not in bs_sources:
                fl, reason = "reference", "网络零售/挂牌价，仅作参考，不参与红绿判定与价差统计"
            else:
                fl, reason = flag_of(p, baseline, cfg["tolerance"], is_web), ""
            dev = (p - baseline) / baseline if (p is not None and baseline) else None
            it.update({"group_id": gid, "flag": fl, "deviation": dev, "flag_reason": reason,
                       "baseline": baseline, "baseline_note": basis_note})
            rows.append(it)
            if fl in ("expensive", "cheap") and it.get("source_type") == "upload":
                marks.append({
                    "group_id": gid, "flag": fl, "name": it.get("name"), "spec": it.get("spec"),
                    "supplier": it.get("supplier"), "price": p, "baseline": baseline,
                    "deviation": dev, "source_file": it.get("source_file"),
                    "locator": it.get("locator") or {"kind": "none"},
                    "comment": (f"{FLAG_LABEL[fl]}｜基准 {baseline:.2f} {cfg['target_currency']}/{std_uom or '原单位'}"
                                f"（{basis_note}）｜本项 {p:.2f}｜偏差 {dev:+.1%}｜容差 ±{cfg['tolerance']:.0%}"),
                })
            if fl == "uncomparable":
                anomalies.append(f"{gid} {it.get('name')} / {it.get('supplier')}：{reason}")
            if fl == "no_quote":
                anomalies.append(f"{gid} {it.get('name')} / {it.get('supplier')}：{reason}（未纳入比较）")

        # 价差与来源数只统计可比（上传 + 资料库）来源；网络价仅进参考列
        comparable = [i["price_norm"] for i in rows
                      if i["price_norm"] is not None and i["flag"] != "uncomparable"
                      and i.get("source_type") != "web"]
        prices = {i.get("supplier"): i["price_norm"] for i in rows
                  if i["price_norm"] is not None and not i.get("_dup") and i.get("source_type") != "web"}
        non_web = {k: v for k, v in prices.items()
                   if any(i.get("supplier") == k and i.get("source_type") != "web" for i in rows)}
        results.append({
            "group_id": gid, "name": rep.get("name"), "spec": rep.get("spec"),
            "uom": std_uom or rep.get("uom"), "qty": qty,
            "baseline": baseline, "baseline_note": basis_note,
            "min_price": min(comparable) if comparable else None,
            "max_price": max(comparable) if comparable else None,
            "gap": (max(comparable) - min(comparable)) if comparable else None,
            "gap_pct": ((max(comparable) - min(comparable)) / baseline) if comparable and baseline else None,
            "best_source": min(non_web, key=non_web.get) if non_web else None,
            "prices": prices, "rows": rows,
            "source_count": len(prices), "uom_conflict": False,
        })
        if len(base_pool) == 1:
            anomalies.append(f"{gid} {rep.get('name')}：仅 1 条可比报价，红绿判定无实际意义，建议补充资料库/网络价")
    return results, anomalies, marks


# ---------- Excel ----------
def write_excel(path, results, cfg, quotes_meta, anomalies, suspicious):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    thin = Side(style="thin", color="FFD9D9D9")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    head_fill = PatternFill("solid", fgColor="FF1F4E79")
    head_font = Font(color="FFFFFFFF", bold=True, size=10)
    red = PatternFill("solid", fgColor=RED_FILL)
    green = PatternFill("solid", fgColor=GREEN_FILL)
    red_font = Font(color=RED_FONT, bold=True)
    green_font = Font(color=GREEN_FONT, bold=True)
    grey = PatternFill("solid", fgColor="FFF2F2F2")

    def style_header(ws, ncol, row=1):
        for c in range(1, ncol + 1):
            cell = ws.cell(row, c)
            cell.fill, cell.font = head_fill, head_font
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.border = border
        ws.row_dimensions[row].height = 30

    def autowidth(ws, widths):
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w

    # 来源列：上传 + 资料库 + 网络（网络后缀标注）
    sources = []
    for r in results:
        for i in r["rows"]:
            key = (i.get("supplier"), i.get("source_type"))
            if key not in [(x[0], x[1]) for x in sources]:
                sources.append(key)
    sources.sort(key=lambda x: ({"upload": 0, "library": 1, "web": 2}.get(x[1], 3), x[0]))
    src_labels = [f"{n}{'（资料库）' if t == 'library' else '（网络参考）' if t == 'web' else ''}" for n, t in sources]

    ws = wb.active
    ws.title = "横向对比"
    fixed = ["组号", "品名", "规格/型号", "标准单位", "采购数量", "基准价", "最低价", "最高价", "最大价差", "价差率", "最优来源", "可比来源数", "口径/异常说明"]
    ws.append(fixed + src_labels + ["说明"])
    for r in results:
        line = [
            r["group_id"], r["name"], r["spec"], r["uom"], r["qty"],
            r["baseline"], r["min_price"], r["max_price"], r["gap"], r["gap_pct"],
            r["best_source"], r["source_count"], r["baseline_note"],
        ]
        for (sn, st) in sources:
            hit = next((i for i in r["rows"] if i.get("supplier") == sn and i.get("source_type") == st and not i.get("_dup")), None)
            line.append(hit["price_norm"] if hit else None)
        line.append("；".join(sorted({i["flag_reason"] for i in r["rows"] if i.get("flag_reason")}))[:200])
        ws.append(line)
        rr = ws.max_row
        for c in range(1, ws.max_column + 1):
            cell = ws.cell(rr, c)
            cell.border = border
            if c >= 6 and isinstance(cell.value, (int, float)) and c not in (10, 12):
                cell.number_format = "#,##0.00"
        ws.cell(rr, 10).number_format = "0.0%"
        for idx, (sn, st) in enumerate(sources):
            cell = ws.cell(rr, len(fixed) + 1 + idx)
            hit = next((i for i in r["rows"] if i.get("supplier") == sn and i.get("source_type") == st and not i.get("_dup")), None)
            if not hit or hit.get("flag") not in ("expensive", "cheap"):
                continue
            cell.fill = red if hit["flag"] == "expensive" else green
            cell.font = red_font if hit["flag"] == "expensive" else green_font
    style_header(ws, ws.max_column)
    ws.freeze_panes = "F2"
    ws.auto_filter.ref = f"A1:{get_column_letter(ws.max_column)}{ws.max_row}"
    autowidth(ws, [9, 26, 30, 10, 12, 12, 12, 12, 12, 10, 18, 11, 30] + [16] * len(sources) + [36])
    ws.cell(ws.max_row + 2, 1, "图例：红色=高于基准（偏贵）；绿色=低于基准（偏便宜）；灰色说明见「口径与图例」").font = Font(italic=True, color="FF666666")

    ws2 = wb.create_sheet("明细数据")
    cols = ["组号", "来源类型", "供应商/来源", "来源文件", "来源依据", "报价/采集日期", "编号", "品名", "规格", "品牌",
            "单位", "标准单位", "数量", "原始单价", "币种", "含税", "归一化单价", "基准价", "偏差", "判定", "归一化说明", "原因/备注", "源文件定位",
            "归一化数量", "归一化金额", "源单金额(原币)", "金额自检"]
    ws2.append(cols)
    for r in results:
        for i in r["rows"]:
            loc = i.get("locator") or {}
            ws2.append([
                r["group_id"], i.get("source_type"), i.get("supplier"), i.get("source_file"),
                i.get("source_ref"), i.get("source_date"), i.get("code"), i.get("name"), i.get("spec"),
                i.get("brand"), i.get("uom"), r["uom"], i.get("qty"), i.get("unit_price"),
                i.get("currency"), "是" if i.get("tax_included") else "否", i.get("price_norm"),
                i.get("baseline"), i.get("deviation"), FLAG_LABEL.get(i.get("flag"), i.get("flag")),
                "；".join(i.get("norm_reasons") or []), i.get("flag_reason") or i.get("note"),
                f"{loc.get('sheet', '')}!{get_column_letter(loc['price_col']) if loc.get('price_col') else ''}{loc.get('row', '')}".strip("!"),
                i.get("qty_std"), i.get("amount_std"), i.get("amount_reported"), i.get("amount_check"),
            ])
            rr = ws2.max_row
            for c in range(1, len(cols) + 1):
                ws2.cell(rr, c).border = border
            fcell = ws2.cell(rr, 20)
            pcell = ws2.cell(rr, 17)
            if i.get("flag") == "expensive":
                pcell.fill, fcell.fill, fcell.font = red, red, red_font
            elif i.get("flag") == "cheap":
                pcell.fill, fcell.fill, fcell.font = green, green, green_font
            elif i.get("flag") in ("reference", "uncomparable", "no_baseline", "no_quote"):
                fcell.fill = grey
            if i.get("deviation") is not None:
                ws2.cell(rr, 19).number_format = "+0.0%;-0.0%;0.0%"
            for c in (14, 17, 18, 24, 25, 26):
                ws2.cell(rr, c).number_format = "#,##0.00"
    style_header(ws2, len(cols))
    ws2.freeze_panes = "H2"
    ws2.auto_filter.ref = f"A1:{get_column_letter(len(cols))}{ws2.max_row}"
    autowidth(ws2, [9, 11, 18, 22, 26, 13, 12, 24, 26, 12, 9, 11, 9, 13, 9, 7, 14, 12, 10, 14, 30, 34, 16,
                     14, 16, 14, 11])

    ws3 = wb.create_sheet("异常与不可比")
    ws3.append(["#", "类型", "说明"])
    n = 0
    for a in list(dict.fromkeys(anomalies + suspicious + quotes_meta.get("warnings", []))):
        n += 1
        typ = "疑似同款" if a.startswith("疑似") else ("模糊合并" if a.startswith("模糊") else "口径/数据提示")
        ws3.append([n, typ, a])
    if n == 0:
        ws3.append([1, "无", "本次未发现异常"])
    style_header(ws3, 3)
    autowidth(ws3, [6, 16, 120])
    for row in ws3.iter_rows(min_row=2):
        row[2].alignment = Alignment(wrap_text=True, vertical="top")

    ws4 = wb.create_sheet("口径与图例")
    info = [
        ["项目", "本次取值 / 说明"],
        ["生成日期", today()],
        ["基准价算法", f"{cfg['baseline']}（参与来源：{', '.join(sorted(set(cfg['baseline_sources']) | ({'web'} if cfg['include_web_in_baseline'] else set())))}）"],
        ["红绿容差", f"±{cfg['tolerance']:.0%}：高于基准×(1+容差)=偏贵(红)；低于基准×(1-容差)=偏便宜(绿)；区间内=接近基准(不标色)"],
        ["颜色", f"偏贵填充 {RED_FILL} / 字色 {RED_FONT}；偏便宜填充 {GREEN_FILL} / 字色 {GREEN_FONT}"],
        ["税务口径", f"统一折算为 {'含税' if cfg['tax_basis'] == 'incl' else '未税' if cfg['tax_basis'] == 'excl' else '原样(不折算)'}单价；税率取各来源自带值"],
        ["目标币种", cfg["target_currency"]],
        ["汇率表", json.dumps(cfg["fx"], ensure_ascii=False) if cfg["fx"] else "未提供（跨币种条目将标为不可比）"],
        ["单位口径", "同量纲单位自动换算到组标准单位（千件↔件、kg↔吨等）；单价与数量同步换算，保证「单价×数量」金额守恒；"
                    "不同量纲（件 vs 套 vs 吨）不合并，标为不可比"],
        ["标准单位选取", "组内报价单位出现次数最多者；并列时取该量纲基准单位。该选取由脚本判定，不依赖人工填写"],
        ["品项匹配", f"编号/品名+规格+量纲精确分组，模糊相似度 ≥{cfg['match_threshold']:.2f} 自动合并，0.60~阈值为疑似同款（不合并）"],
        ["网络价格", "仅作参考列展示，不参与基准、红绿判定、最低/最高价与价差统计；零售/挂牌价与 B2B 成交价口径不同，不得直接当成交依据"],
        ["源单标色", "只改副本（*_marked.*），原件不动；仅对上传报价单中判定为偏贵/偏便宜的价格单元格着色并加批注"],
        ["数量口径", f"采购数量取组内上传来源换算后数量（默认 {cfg['target_qty'] or '组内上传来源换算后数量'}）；各来源数量不一致时按最大数量口径并记异常"],
        ["金额自检", "与源报价单金额列交叉核对（单价×数量 vs 源单金额），偏差>1% 记入「异常与不可比」"],
        ["来源清单", "；".join(sorted({f"{i.get('supplier')}({i.get('source_type')})" for r in results for i in r["rows"]}))],
        ["免责", "本表为价格横向对比工具输出，规格一致性、含税/运费/账期/最小起订量等商务条款需以原始报价单为准"],
    ]
    for row in info:
        ws4.append(row)
    style_header(ws4, 2)
    autowidth(ws4, [16, 126])
    for row in ws4.iter_rows(min_row=2):
        row[1].alignment = Alignment(wrap_text=True, vertical="top")
        ws4.row_dimensions[row[0].row].height = 30

    wb.save(path)
    return path


# ---------- 摘要（Word .docx 格式，不用 Markdown） ----------
def write_summary(path, results, cfg, quotes_meta, anomalies, suspicious, marks):
    """生成 Word 摘要（summary.docx）。

    与旧版 Markdown 摘要信息完全等价，仅输出格式改为 .docx，
    便于直接作为正式交付物给用户。python-docx 在函数内 import，
    未安装时给出明确错误提示。
    """
    from docx import Document
    from docx.shared import Pt

    uploads = [i for r in results for i in r["rows"] if i.get("source_type") == "upload"]
    libs = [i for r in results for i in r["rows"] if i.get("source_type") == "library"]
    webs = [i for r in results for i in r["rows"] if i.get("source_type") == "web"]
    n_red = sum(1 for i in uploads if i.get("flag") == "expensive")
    n_green = sum(1 for i in uploads if i.get("flag") == "cheap")
    n_uncomparable = sum(1 for r in results for i in r["rows"] if i.get("flag") in ("uncomparable", "no_baseline"))
    n_noquote = sum(1 for r in results for i in r["rows"] if i.get("flag") == "no_quote")
    n_amt_bad = sum(1 for r in results for i in r["rows"] if i.get("amount_check") == "不符")

    # 只在「所有上传来源都有报价且可比」的品项上算综合总价，避免覆盖品项不同造成误比
    up_sources = sorted({i.get("supplier") for i in uploads})
    complete = []
    for r in results:
        ok = True
        per = {}
        for sn in up_sources:
            hit = next((i for i in r["rows"] if i.get("supplier") == sn and i.get("source_type") == "upload"
                        and i.get("price_norm") is not None and i.get("flag") != "uncomparable"), None)
            if not hit:
                ok = False
                break
            per[sn] = hit["price_norm"] * (r["qty"] or 1)
        if ok and per:
            complete.append((r, per))
    totals = {sn: sum(per[sn] for _, per in complete) for sn in up_sources} if complete else {}

    def bullet(text, bold_prefix=""):
        p = doc.add_paragraph(style="List Bullet")
        if bold_prefix:
            p.add_run(bold_prefix).bold = True
        p.add_run(text)
        return p

    doc = Document()
    doc.add_heading("报价横向比价结果摘要", level=0)

    # 概览（标签加粗，便于扫读）
    p = doc.add_paragraph()
    p.add_run(f"生成日期：{today()}").bold = True
    doc.add_paragraph(
        f"数据范围：上传报价单 {len(set(i.get('source_file') for i in uploads))} 份 / {len(up_sources)} 家供应商；"
        f"资料库来源 {len(set(i.get('supplier') for i in libs))} 个；网络价格 {len(webs)} 条")
    doc.add_paragraph(
        f"条目归并：{sum(len(r['rows']) for r in results)} 条报价 → {len(results)} 个品项组；"
        f"不可比/无基准 {n_uncomparable} 条；未报出价格 {n_noquote} 条")
    doc.add_paragraph(
        f"判定口径：基准 = {'+'.join(sorted(set(cfg['baseline_sources'])))} 归一化单价的 {cfg['baseline']}；"
        f"容差 ±{cfg['tolerance']:.0%}；统一为{'含税' if cfg['tax_basis'] == 'incl' else '未税' if cfg['tax_basis'] == 'excl' else '原口径'} {cfg['target_currency']}；"
        "同量纲单位自动换算且单价×数量金额守恒")

    # 核心结论
    doc.add_heading("核心结论", level=1)
    if totals:
        best = min(totals, key=totals.get)
        worst = max(totals, key=totals.get)
        save = totals[worst] - totals[best]
        save_pct = save / totals[worst] if totals[worst] else 0
        pb = doc.add_paragraph(style="List Bullet")
        pb.add_run(f"综合最优：{best}").bold = True
        pb.add_run(f"，在 {len(complete)} 个各家均有报价的可比品项上合计 "
                   f"{totals[best]:,.2f} {cfg['target_currency']}；比最高的 {worst} 低 {save:,.2f}（{save_pct:.1%}）。")
        bullet("各来源可比合计：" + "；".join(f"{k} {v:,.2f}" for k, v in sorted(totals.items(), key=lambda x: x[1])))
    else:
        bullet("上传来源之间没有足够「共同可比品项」，无法给出综合总价排名；已按单项逐一判定红绿。")
    bullet(f"源报价单标记：偏贵 {n_red} 处标红、偏便宜 {n_green} 处标绿（共 {len(marks)} 处），"
           "均写入 *_marked.* 副本，原件未改动。")

    # 价差最大的品项（表格）
    doc.add_heading("价差最大的品项", level=1)
    table = doc.add_table(rows=1, cols=9)
    table.style = "Table Grid"
    hdrs = ["组号", "品名", "规格", "标准单位", "基准价", "最低价", "最高价", "最大价差", "最优来源"]
    for c, h in zip(table.rows[0].cells, hdrs):
        c.text = h
        if c.paragraphs[0].runs:
            c.paragraphs[0].runs[0].bold = True
    for r in sorted([r for r in results if r["gap"]], key=lambda x: -x["gap"])[:8]:
        vals = [str(r['group_id']), r['name'], (r['spec'] or '')[:30], r['uom'],
                f"{r['baseline'] or 0:,.2f}", f"{r['min_price'] or 0:,.2f}",
                f"{r['max_price'] or 0:,.2f}", f"{r['gap']:,.2f}", r['best_source'] or '-']
        for c, v in zip(table.add_row().cells, vals):
            c.text = str(v)

    # 网络参考价
    if webs:
        doc.add_heading("网络参考价", level=1)
        doc.add_paragraph(
            f"共 {len(webs)} 条网络价格，仅作参考列展示，不参与基准、红绿判定与价差统计"
            "（零售/挂牌价与采购成交价口径不同）。")
        doc.add_paragraph("明细及 URL 见 comparison.xlsx →「明细数据」的「来源依据」列。")

    # 需要注意
    doc.add_heading("需要注意", level=1)
    notes = list(dict.fromkeys(anomalies + suspicious + quotes_meta.get("warnings", [])))
    if n_amt_bad:
        notes.insert(0, f"金额自检：{n_amt_bad} 条「单价×数量」与源报价单金额列不符，请复核数量与单位口径。")
    if notes:
        for a in notes[:15]:
            bullet(a)
        if len(notes) > 15:
            bullet(f"另有 {len(notes) - 15} 条提示，见 Excel「异常与不可比」sheet。")
    else:
        bullet("本次未发现口径异常。")

    # 交付物
    doc.add_heading("交付物", level=1)
    bullet("comparison.xlsx：横向对比 / 明细数据 / 异常与不可比 / 口径与图例", "")
    bullet("*_marked.*：带红绿标记与批注的源报价单副本", "")
    bullet("comparison.json、marks.json：结构化结果与标色指令", "")
    bullet("summary.docx：本摘要（Word 格式）", "")

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    doc.save(path)
    print(f"[OK] 已生成 Word 摘要：{path}")
    return path


def main():
    ap = argparse.ArgumentParser(description="报价归一化、横向对比、红绿判定")
    ap.add_argument("--quotes", default="quotes.json")
    ap.add_argument("--out-dir", default="comparison_output")
    ap.add_argument("--baseline", default="median", choices=["median", "min", "mean", "web", "library"])
    ap.add_argument("--baseline-sources", default="upload,library")
    ap.add_argument("--tolerance", type=float, default=0.05)
    ap.add_argument("--tax-basis", default="incl", choices=["incl", "excl", "asis"])
    ap.add_argument("--target-currency", default="CNY")
    ap.add_argument("--fx", default="", help='汇率 JSON：{"USD":7.15}，键=原币种，值=目标币种金额')
    ap.add_argument("--target-qty", type=float, default=None)
    ap.add_argument("--match-threshold", type=float, default=0.82)
    ap.add_argument("--include-web-in-baseline", action="store_true")
    args = ap.parse_args()

    meta = load_json(args.quotes)
    items = meta.get("items", [])
    if not items:
        print("[ERROR] quotes.json 无条目，先运行 extract_quotes.py")
        sys.exit(2)
    fx = load_json(args.fx) if args.fx and os.path.isfile(args.fx) else (json.loads(args.fx) if args.fx else {})
    cfg = {
        "baseline": args.baseline, "baseline_sources": [x.strip() for x in args.baseline_sources.split(",") if x.strip()],
        "tolerance": args.tolerance, "tax_basis": args.tax_basis, "target_currency": args.target_currency,
        "fx": fx, "target_qty": args.target_qty, "match_threshold": args.match_threshold,
        "include_web_in_baseline": args.include_web_in_baseline,
    }
    os.makedirs(args.out_dir, exist_ok=True)
    notes = []
    items = normalize_items(items, cfg, notes)
    groups, suspicious = build_groups(items, cfg, notes)
    results, anomalies, marks = analyze(groups, cfg, notes)
    check_qty_unit_consistency([it for r in results for it in r["rows"]], anomalies)

    xlsx = os.path.join(args.out_dir, "comparison.xlsx")
    write_excel(xlsx, results, cfg, meta, anomalies, suspicious)
    dump_json(os.path.join(args.out_dir, "comparison.json"), {
        "generated_at": today(), "config": cfg,
        "warnings": meta.get("warnings", []), "anomalies": anomalies, "suspicious": suspicious,
        "groups": [{
            k: v for k, v in r.items() if k != "rows"
        } | {"rows": [{kk: vv for kk, vv in i.items() if not kk.startswith("_")} for i in r["rows"]]}
            for r in results],
    })
    dump_json(os.path.join(args.out_dir, "marks.json"), {
        "generated_at": today(), "red_fill": RED_FILL, "red_font": RED_FONT,
        "green_fill": GREEN_FILL, "green_font": GREEN_FONT,
        "count": len(marks), "items": marks,
    })
    write_summary(os.path.join(args.out_dir, "summary.docx"), results, cfg, meta, anomalies, suspicious, marks)

    n_red = sum(1 for m in marks if m["flag"] == "expensive")
    n_green = sum(1 for m in marks if m["flag"] == "cheap")
    print(f"[OK] {len(items)} 条报价 → {len(results)} 个品项组")
    print(f"     红(偏贵) {n_red} 处 / 绿(偏便宜) {n_green} 处，共 {len(marks)} 处待标色")
    print(f"     异常/提示 {len(anomalies) + len(suspicious) + len(meta.get('warnings', []))} 条")
    print(f"     输出目录：{os.path.abspath(args.out_dir)}")


if __name__ == "__main__":
    main()
