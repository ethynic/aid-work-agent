#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""归一化 -> 品项匹配 -> 基准价 -> 红绿判定 -> 横向对比 Excel + 摘要 + 标色指令。

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
  summary.md        可直接贴给用户的对比结果摘要
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qc_common import (  # noqa: E402
    FLAG_LABEL, GREEN_FILL, GREEN_FONT, RED_FILL, RED_FONT,
    dump_json, dump_text, load_json, norm_text, similarity, today,
)


# ---------- 归一化 ----------
def normalize_items(items, cfg, notes):
    fx = cfg["fx"]
    for it in items:
        p = float(it.get("unit_price") or 0)
        cur = (it.get("currency") or cfg["target_currency"]).upper()
        reasons = []
        if cur != cfg["target_currency"]:
            rate = fx.get(cur)
            if rate:
                p *= float(rate)
                reasons.append(f"{cur}→{cfg['target_currency']} @{rate}")
            else:
                it["price_norm"] = None
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
        mult = float(it.get("unit_multiplier") or 1.0)
        if mult != 1.0:
            p *= mult
            reasons.append(f"单位换算 ×{mult}")
        it["price_norm"] = round(p, 4)
        it["norm_reasons"] = reasons
        if not it.get("standard_uom"):
            it["standard_uom"] = it.get("uom") or ""
    return items


# ---------- 品项匹配（并查集合并模糊同款） ----------
def build_groups(items, cfg, notes):
    exact = defaultdict(list)
    for it in items:
        key = it.get("match_key") or (norm_text(it.get("name")) + "|" + norm_text(it.get("spec")) + "|" + norm_text(it.get("standard_uom")))
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
            uom_a, uom_b = ra.get("standard_uom", ""), rb.get("standard_uom", "")
            if norm_text(uom_a) and norm_text(uom_b) and norm_text(uom_a) != norm_text(uom_b):
                continue  # 单位不同绝不自动合并
            if sim >= cfg["match_threshold"] and find(ka) != find(kb):
                union(ka, kb)
                notes.append(f"模糊合并：『{na} / {sa}』≈『{nb} / {sb}』 相似度 {sim:.2f}")
            elif 0.60 <= sim < cfg["match_threshold"]:
                suspicious.append(f"疑似同款未合并（相似度 {sim:.2f}）：『{na} {sa}』 vs 『{nb} {sb}』")
    merged = defaultdict(list)
    for k in keys:
        merged[find(k)] += exact[k]
    return list(merged.values()), suspicious


# ---------- 判定 ----------
def baseline_of(vals, method):
    vals = [v for v in vals if v is not None and math.isfinite(v) and v > 0]
    if not vals:
        return None
    if method == "min":
        return min(vals)
    if method == "mean":
        return round(statistics.mean(vals), 4)
    return round(statistics.median(vals), 4)


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
        uoms = {norm_text(i.get("standard_uom", "")) for i in items if i.get("standard_uom")}
        uom_conflict = len(uoms) > 1
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

        for it in chosen:
            it["_dup"] = False
        for it in duplicates:
            it["_dup"] = True

        base_pool = [
            i["price_norm"] for i in chosen
            if not i.get("_dup") and i.get("source_type") in bs_sources
            and i.get("price_norm") is not None and not uom_conflict
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

        qty = cfg["target_qty"] or max([i.get("qty") or 0 for i in chosen] or [0]) or 1
        rows = []
        for it in chosen:
            p = it.get("price_norm")
            is_web = it.get("source_type") == "web"
            if uom_conflict:
                fl, reason = "uncomparable", "单位口径不一致：" + "/".join(sorted(uoms))
            elif p is None:
                fl, reason = "uncomparable", "；".join(it.get("norm_reasons") or ["价格缺失"])
            elif baseline is None:
                fl, reason = "no_baseline", "该品项只有单一来源，无横向基准"
            elif is_web and "web" not in bs_sources:
                fl, reason = "reference", "网络零售/挂牌价，仅作参考，不参与红绿判定"
            else:
                fl, reason = flag_of(p, baseline, cfg["tolerance"], is_web), ""
            dev = round((p - baseline) / baseline, 6) if (p is not None and baseline) else None
            it.update({"group_id": gid, "flag": fl, "deviation": dev, "flag_reason": reason,
                       "baseline": baseline, "baseline_note": basis_note})
            rows.append(it)
            if fl in ("expensive", "cheap") and it.get("source_type") == "upload":
                marks.append({
                    "group_id": gid, "flag": fl, "name": it.get("name"), "spec": it.get("spec"),
                    "supplier": it.get("supplier"), "price": p, "baseline": baseline,
                    "deviation": dev, "source_file": it.get("source_file"),
                    "locator": it.get("locator") or {"kind": "none"},
                    "comment": (f"{FLAG_LABEL[fl]}｜基准 {baseline:.2f} {cfg['target_currency']}"
                                f"（{basis_note}）｜本项 {p:.2f}｜偏差 {dev:+.1%}｜容差 ±{cfg['tolerance']:.0%}"),
                })
            if fl == "uncomparable":
                anomalies.append(f"{gid} {it.get('name')} / {it.get('supplier')}：{reason}")

        valid = [i["price_norm"] for i in rows if i["price_norm"] is not None and i["flag"] != "uncomparable"]
        prices = {i.get("supplier"): i["price_norm"] for i in rows if i["price_norm"] is not None and not i.get("_dup")}
        non_web = {k: v for k, v in prices.items() if any(i.get("supplier") == k and i.get("source_type") != "web" for i in rows)}
        results.append({
            "group_id": gid, "name": rep.get("name"), "spec": rep.get("spec"),
            "uom": rep.get("standard_uom") or rep.get("uom"), "qty": qty,
            "baseline": baseline, "baseline_note": basis_note,
            "min_price": min(valid) if valid else None,
            "max_price": max(valid) if valid else None,
            "gap": round(max(valid) - min(valid), 2) if valid else None,
            "gap_pct": round((max(valid) - min(valid)) / baseline, 4) if valid and baseline else None,
            "best_source": min(non_web, key=non_web.get) if non_web else None,
            "prices": prices, "rows": rows,
            "source_count": len(prices), "uom_conflict": uom_conflict,
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
                sources.append((i.get("supplier"), i.get("source_type")))
    sources.sort(key=lambda x: ({"upload": 0, "library": 1, "web": 2}.get(x[1], 3), x[0]))
    src_labels = [f"{n}{'（资料库）' if t == 'library' else '（网络参考）' if t == 'web' else ''}" for n, t in sources]

    ws = wb.active
    ws.title = "横向对比"
    fixed = ["组号", "品名", "规格/型号", "单位", "数量", "基准价", "最低价", "最高价", "最大价差", "价差率", "最优来源", "可比来源数", "口径/异常说明"]
    ws.append(fixed + src_labels + ["说明"])
    for r in results:
        line = [
            r["group_id"], r["name"], r["spec"], r["uom"], r["qty"],
            r["baseline"], r["min_price"], r["max_price"], r["gap"], r["gap_pct"],
            r["best_source"], r["source_count"],
            r["baseline_note"] + ("；单位口径不一致" if r["uom_conflict"] else ""),
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
    autowidth(ws, [9, 26, 30, 9, 9, 12, 12, 12, 12, 10, 18, 11, 30] + [16] * len(sources) + [36])
    ws.cell(ws.max_row + 2, 1, "图例：红色=高于基准（偏贵）；绿色=低于基准（偏便宜）；灰色说明见「口径与图例」").font = Font(italic=True, color="FF666666")

    ws2 = wb.create_sheet("明细数据")
    cols = ["组号", "来源类型", "供应商/来源", "来源文件", "来源依据", "报价/采集日期", "编号", "品名", "规格", "品牌",
            "单位", "标准单位", "数量", "原始单价", "币种", "含税", "归一化单价", "基准价", "偏差", "判定", "归一化说明", "原因/备注", "源文件定位"]
    ws2.append(cols)
    for r in results:
        for i in r["rows"]:
            loc = i.get("locator") or {}
            ws2.append([
                r["group_id"], i.get("source_type"), i.get("supplier"), i.get("source_file"),
                i.get("source_ref"), i.get("source_date"), i.get("code"), i.get("name"), i.get("spec"),
                i.get("brand"), i.get("uom"), i.get("standard_uom"), i.get("qty"), i.get("unit_price"),
                i.get("currency"), "是" if i.get("tax_included") else "否", i.get("price_norm"),
                i.get("baseline"), i.get("deviation"), FLAG_LABEL.get(i.get("flag"), i.get("flag")),
                "；".join(i.get("norm_reasons") or []), i.get("flag_reason") or i.get("note"),
                f"{loc.get('sheet', '')}!{get_column_letter(loc['price_col']) if loc.get('price_col') else ''}{loc.get('row', '')}".strip("!"),
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
            elif i.get("flag") in ("reference", "uncomparable", "no_baseline"):
                fcell.fill = grey
            if i.get("deviation") is not None:
                ws2.cell(rr, 19).number_format = "+0.0%;-0.0%;0.0%"
            for c in (14, 17, 18):
                ws2.cell(rr, c).number_format = "#,##0.00"
    style_header(ws2, len(cols))
    ws2.freeze_panes = "H2"
    ws2.auto_filter.ref = f"A1:{get_column_letter(len(cols))}{ws2.max_row}"
    autowidth(ws2, [9, 11, 18, 22, 26, 13, 12, 24, 26, 12, 9, 11, 9, 13, 9, 7, 14, 12, 10, 14, 24, 34, 16])

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
        ["单位口径", "仅做别名归一（个/件/pcs→件 等）；不同量纲不做隐式换算，需显式填写 unit_multiplier，否则标不可比"],
        ["品项匹配", f"编号/品名+规格+单位精确分组，模糊相似度 ≥{cfg['match_threshold']:.2f} 自动合并，0.60~阈值为疑似同款（不合并）"],
        ["网络价格", "仅作参考列，默认不参与基准；零售/挂牌价与 B2B 成交价口径不同（含运费、税、最小起订量差异），不得直接当成交依据"],
        ["源单标色", "只改副本（*_marked.*），原件不动；仅对上传报价单中判定为偏贵/偏便宜的价格单元格着色并加批注"],
        ["数量口径", f"目标采购量 {cfg['target_qty'] or '按各组最大数量'}"],
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


# ---------- 摘要 ----------
def write_summary(path, results, cfg, quotes_meta, anomalies, suspicious, marks):
    uploads = [i for r in results for i in r["rows"] if i.get("source_type") == "upload"]
    libs = [i for r in results for i in r["rows"] if i.get("source_type") == "library"]
    webs = [i for r in results for i in r["rows"] if i.get("source_type") == "web"]
    n_red = sum(1 for i in uploads if i.get("flag") == "expensive")
    n_green = sum(1 for i in uploads if i.get("flag") == "cheap")
    n_uncomparable = sum(1 for r in results for i in r["rows"] if i.get("flag") in ("uncomparable", "no_baseline"))

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

    lines = [
        "# 报价横向比价结果摘要", "",
        f"- 生成日期：{today()}",
        f"- 数据范围：上传报价单 {len(set(i.get('source_file') for i in uploads))} 份 / {len(up_sources)} 家供应商；"
        f"资料库来源 {len(set(i.get('supplier') for i in libs))} 个；网络价格 {len(webs)} 条",
        f"- 条目归并：{sum(len(r['rows']) for r in results)} 条报价 → {len(results)} 个品项组；"
        f"不可比/无基准 {n_uncomparable} 条",
        f"- 判定口径：基准 = {'+'.join(sorted(set(cfg['baseline_sources'])))} 归一化单价的 {cfg['baseline']}；容差 ±{cfg['tolerance']:.0%}；"
        f"统一为{'含税' if cfg['tax_basis'] == 'incl' else '未税' if cfg['tax_basis'] == 'excl' else '原口径'} {cfg['target_currency']}",
        "", "## 核心结论", "",
    ]
    if totals:
        best = min(totals, key=totals.get)
        worst = max(totals, key=totals.get)
        save = totals[worst] - totals[best]
        save_pct = save / totals[worst] if totals[worst] else 0
        lines += [
            f"- **综合最优：{best}**，在 {len(complete)} 个各家均有报价的可比品项上合计 "
            f"**{totals[best]:,.2f} {cfg['target_currency']}**；比最高的 {worst} 低 {save:,.2f}"
            f"（{save_pct:.1%}）。",
            "- 各来源可比合计：" + "；".join(f"{k} {v:,.2f}" for k, v in sorted(totals.items(), key=lambda x: x[1])),
        ]
    else:
        lines.append("- 上传来源之间没有足够「共同可比品项」，无法给出综合总价排名；已按单项逐一判定红绿。")
    lines += [
        f"- 源报价单标记：**偏贵 {n_red} 处标红、偏便宜 {n_green} 处标绿**（共 {len(marks)} 处），均写入 `*_marked.*` 副本，原件未改动。",
        "",
        "## 价差最大的品项", "",
        "| 组号 | 品名 | 规格 | 基准价 | 最低价 | 最高价 | 最大价差 | 最优来源 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in sorted([r for r in results if r["gap"]], key=lambda x: -x["gap"])[:8]:
        lines.append(f"| {r['group_id']} | {r['name']} | {(r['spec'] or '')[:30]} | "
                     f"{r['baseline'] or 0:,.2f} | {r['min_price'] or 0:,.2f} | {r['max_price'] or 0:,.2f} | "
                     f"{r['gap']:,.2f} | {r['best_source'] or '-'} |")
    if webs:
        lines += ["", "## 网络参考价", "",
                  f"- 共 {len(webs)} 条网络价格，仅作参考列展示，**不参与红绿判定**（零售/挂牌价与采购成交价口径不同）。",
                  "- 明细及 URL 见 `comparison.xlsx` →「明细数据」的「来源依据」列。"]
    lines += ["", "## 需要注意", ""]
    notes = list(dict.fromkeys(anomalies + suspicious + quotes_meta.get("warnings", [])))
    lines += [f"- {a}" for a in notes[:15]] if notes else ["- 本次未发现口径异常。"]
    if len(notes) > 15:
        lines.append(f"- 另有 {len(notes) - 15} 条提示，见 Excel「异常与不可比」sheet。")
    lines += ["", "## 交付物", "",
              "- `comparison.xlsx`：横向对比 / 明细数据 / 异常与不可比 / 口径与图例",
              "- `*_marked.*`：带红绿标记与批注的源报价单副本",
              "- `comparison.json`、`marks.json`：结构化结果与标色指令",
              "- `summary.md`：本摘要", ""]
    return dump_text(path, "\n".join(lines))


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
    write_summary(os.path.join(args.out_dir, "summary.md"), results, cfg, meta, anomalies, suspicious, marks)

    n_red = sum(1 for m in marks if m["flag"] == "expensive")
    n_green = sum(1 for m in marks if m["flag"] == "cheap")
    print(f"[OK] {len(items)} 条报价 → {len(results)} 个品项组")
    print(f"     红(偏贵) {n_red} 处 / 绿(偏便宜) {n_green} 处，共 {len(marks)} 处待标色")
    print(f"     异常/提示 {len(anomalies) + len(suspicious) + len(meta.get('warnings', []))} 条")
    print(f"     输出目录：{os.path.abspath(args.out_dir)}")


if __name__ == "__main__":
    main()
