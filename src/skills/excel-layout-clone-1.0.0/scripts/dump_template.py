#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""运行时转储 XLSX 样版结构，供分析「改什么、改在哪里」。

坐标约定：S[Sheet名] A1。样版本身绝不修改；本脚本只读。
用法: python3 dump_template.py <template.xlsx> [--out dump.txt] [--json]
"""
import os
import re
import sys
import json
import warnings
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from xlsx_engine import XlsxEditor  # noqa: E402

try:
    from openpyxl import load_workbook
    from openpyxl.utils import get_column_letter
except ImportError:
    load_workbook = None
    get_column_letter = None

HINT_PATTERNS = [
    (r"\{\{.*?\}\}|\[\[.*?\]\]|\$\{.*?\}", "已有占位符(异常)"),
    (r"[¥￥$]\s*[\d,]+(?:\.\d+)?", "金额"),
    (r"\d+(?:\.\d+)?\s*(?:元|万元|%|％|人|天|小时|个|套|件|kg|米|km|㎡)", "数值+单位"),
    (r"(?:19|20)\d{2}[-/.年]\d{1,2}[-/.月]?\d{0,2}", "日期"),
    (r"1[3-9]\d{9}", "手机号"),
    (r"[\w.\-]+@[\w.\-]+\.\w+", "邮箱"),
    (r"https?://\S+", "网址"),
    (r"_{3,}|＿{3,}|\.{4,}|[（(]\s*[)）]", "待填空位"),
    (r"名称|编号|日期|地址|电话|联系人|负责人|金额|合计|小计|总价|备注|版本|状态|客户|公司|项目|数量|单价", "通用标签"),
]

TOTAL_WORDS = ("合计", "总计", "小计", "总额", "total", "sum", "共计")


def _text(v):
    if v is None:
        return ""
    return str(v).replace("\n", "\\n").replace("\t", "\\t")


def _is_total_row(values, formulas):
    joined = " ".join(_text(v) for v in values if v is not None)
    if any(w in joined.lower() for w in TOTAL_WORDS):
        return True
    return any(re.match(r"^=(SUM|AVERAGE|COUNT)\s*\(", str(f or ""), re.I)
               for f in formulas if f)


def _normalize_body_formula(formula, row):
    if not formula:
        return ""
    def repl(m):
        return m.group(1) + ("{row}" if int(m.group(2)) == row else m.group(2))
    return re.sub(r"(?<![A-Za-z0-9_])([A-Z]{1,3})(\d+)", repl, str(formula))


def _candidate_tables(ws, max_scan=12):
    rows = {}
    for row in ws.iter_rows():
        vals = [_text(c.value) for c in row]
        if any(vals):
            rows[row[0].row] = (vals, [c.value if isinstance(c.value, str) and
                                       c.value.startswith("=") else "" for c in row])
    out = []
    keys = sorted(rows)
    for hi, r in enumerate(keys):
        vals, formulas = rows[r]
        text_cols = [i for i, v in enumerate(vals) if v and re.search(r"[\u4e00-\u9fa5A-Za-z]", v)]
        if len(text_cols) < 2 or r >= keys[-1]:
            continue
        follow = [x for x in keys[hi + 1: hi + 1 + max_scan]]
        if not follow:
            continue
        body, total_row = [], None
        for br in follow:
            bv, bf = rows[br]
            if _is_total_row(bv, bf):
                total_row = br
                break
            filled = sum(1 for i in text_cols if i < len(bv) and bv[i])
            if filled < max(1, len(text_cols) // 2):
                break
            body.append(br)
        if not body:
            continue
        ncols = max(text_cols) + 1
        span = (max(body) if body else r) - r + 1
        cols = []
        for ci in range(ncols):
            cvi = get_column_letter(ci + 1) if get_column_letter else str(ci + 1)
            sample = rows[body[0]][0][ci] if ci < len(rows[body[0]][0]) else ""
            pats = set()
            for br in body:
                f = rows[br][1][ci] if ci < len(rows[br][1]) else ""
                if f:
                    pats.add(_normalize_body_formula(f, br))
            cols.append({"col": cvi, "header": vals[ci] if ci < len(vals) else "",
                         "sample": sample, "formula_patterns": sorted(pats)[:3]})
        out.append({"sheet": ws.title, "header_row": r,
                    "sample_rows": body[:8], "total_row": total_row,
                    "cols": get_column_letter(ncols) if get_column_letter else str(ncols),
                    "score": len(body) * ncols, "columns": cols})
    out.sort(key=lambda x: -x["score"])
    picked = []
    for cand in out:
        a, b = cand["header_row"], cand.get("total_row") or max(cand["sample_rows"])
        if any(not (b < x["header_row"] or a > (x.get("total_row") or max(x["sample_rows"])))
               for x in picked):
            continue
        picked.append(cand)
        if len(picked) >= 8:
            break
    return picked


def _dump_sheet(ws, rec, lines):
    s = {"name": ws.title, "state": ws.sheet_state,
         "max_row": ws.max_row, "max_col": ws.max_column,
         "dimension": ws.calculate_dimension(), "cells": [], "merged": [],
         "tables": [], "charts": [], "images": 0, "data_validations": [],
         "conditional_formattings": [], "freeze": str(ws.freeze_panes or ""),
         "autofilter": str(ws.auto_filter.ref or ""),
         "print_area": str(ws.print_area or ""),
         "print_titles": str(ws.print_title_rows or ws.print_title_cols or ""),
         "defined_names": [], "hyperlinks": [], "comments": [],
         "candidate_tables": []}
    lines.append("--- S[%s] %s (%s) ---" % (ws.title, ws.sheet_state, ws.calculate_dimension()))

    for row in ws.iter_rows():
        for c in row:
            if c.value is None:
                continue
            v = _text(c.value)
            f = ""
            if isinstance(c.value, str) and c.value.startswith("="):
                f = c.value
            item = {"ref": c.coordinate, "value": v, "formula": f,
                    "data_type": str(c.data_type)}
            s["cells"].append(item)
            if len(lines) < 12000:
                lines.append("S[%s] %s|%s" % (ws.title, c.coordinate, f or v))

    s["merged"] = [str(x) for x in ws.merged_cells.ranges]
    for m in s["merged"]:
        lines.append("S[%s] MERGE|%s" % (ws.title, m))
    try:
        for name, ref in ws.tables.items():
            s["tables"].append({"name": name, "ref": str(ref)})
            lines.append("S[%s] TABLE|%s|%s" % (ws.title, name, ref))
    except Exception:
        pass
    try:
        for ch in ws._charts:
            s["charts"].append({"type": type(ch).__name__, "anchor": str(getattr(ch, "anchor", ""))[:80]})
            lines.append("S[%s] CHART|%s" % (ws.title, type(ch).__name__))
    except Exception:
        pass
    try:
        s["images"] = len(ws._images)
        if s["images"]:
            lines.append("S[%s] IMAGES|%d" % (ws.title, s["images"]))
    except Exception:
        pass
    try:
        for dv in ws.data_validations.dataValidation:
            s["data_validations"].append({"sqref": str(dv.sqref), "type": dv.type,
                                          "formula1": str(dv.formula1 or "")})
            lines.append("S[%s] DV|%s|%s" % (ws.title, dv.sqref, dv.type))
    except Exception:
        pass
    try:
        for cf in ws.conditional_formatting:
            for rule in cf.rules:
                s["conditional_formattings"].append({"sqref": str(cf.sqref),
                                                     "type": str(rule.type)})
            lines.append("S[%s] CF|%s" % (ws.title, cf.sqref))
    except Exception:
        pass
    for key in ("freeze", "autofilter", "print_area", "print_titles"):
        if s[key]:
            lines.append("S[%s] %s|%s" % (ws.title, key.upper(), s[key]))
    try:
        for key, dn in ws.defined_names.items():
            s["defined_names"].append({"name": key, "value": str(dn.attr_text)})
    except Exception:
        pass
    try:
        for row in ws.iter_rows():
            for c in row:
                if c.hyperlink:
                    s["hyperlinks"].append({"ref": c.coordinate, "target": str(c.hyperlink.target or "")})
                if c.comment:
                    s["comments"].append({"ref": c.coordinate, "text": str(c.comment.text)[:80]})
    except Exception:
        pass
    s["candidate_tables"] = _candidate_tables(ws)
    for t in s["candidate_tables"]:
        lines.append("S[%s] CANDIDATE_TABLE|header=%d|rows=%s|total=%s|%s" %
                     (ws.title, t["header_row"], t["sample_rows"], t["total_row"], t["cols"]))
    rec["sheets"].append(s)
    lines.append("")


def dump(path):
    if load_workbook is None:
        raise RuntimeError("需要 openpyxl：pip install openpyxl")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        wb = load_workbook(path, data_only=False, keep_vba=True)
    rec = {"file": os.path.abspath(path), "sheets": [], "warnings": [],
           "layout_signature": {}, "features": {}}
    rec["warnings"] = sorted({str(w.message)[:200] for w in caught})[:12]
    lines = ["# XLSX 样版结构（运行时分析素材）", "文件: %s" % path, ""]
    for ws in wb.worksheets:
        _dump_sheet(ws, rec, lines)
    try:
        ed = XlsxEditor(path)
        rec["layout_signature"] = ed.signature()
        # 我方补丁（上游 bug）：signature 的部件计数键带 xl/ 前缀，上游按无前缀键
        # 过滤导致 features 恒为空；改为同时接受两种键名
        _feat_keys = ("tables", "drawings", "charts", "pivotTables", "slicerCaches",
                      "xl/tables", "xl/drawings", "xl/charts", "xl/pivotTables",
                      "xl/slicerCaches")
        rec["features"] = {k: v for k, v in rec["layout_signature"].items()
                           if k in _feat_keys and v}
        ed.close()
    except Exception as exc:
        rec["warnings"].append("读取包级结构失败: %s" % exc)
    rec["anchors"] = build_anchors(rec)
    return "\n".join(lines) + "\n", rec


def build_anchors(rec, limit=160):
    out = []
    for s in rec["sheets"]:
        for c in s["cells"]:
            txt = c["formula"] or c["value"]
            tags = sorted({tag for pat, tag in HINT_PATTERNS if re.search(pat, txt)})
            if tags:
                out.append({"sheet": s["name"], "ref": c["ref"],
                            "value": c["value"][:120], "formula": c["formula"][:160],
                            "tags": tags})
    return out[:limit]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("template")
    ap.add_argument("--out", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    text, rec = dump(args.template)
    payload = json.dumps(rec, ensure_ascii=False, indent=2) if args.json else text
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(payload)
        print("WROTE " + args.out)
    else:
        print(payload)


if __name__ == "__main__":
    main()
