#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第 4 步：按 analysis.json 把 answers.json 回填到 XLSX 样版，生成新文件并验收。

用法:
  python3 fill_xlsx.py <template.xlsx> <analysis.json> <answers.json|-> <output.xlsx> \
      [--report fill_report.json] [--cache-dir 目录] [--cache-key 源样版hash]
"""
import os
import re
import sys
import ast
import json
import shutil
import argparse
import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from xlsx_engine import XlsxEditor, split_ref, join_ref, col_to_num, num_to_col  # noqa: E402

_DIGITS = "零壹贰叁肆伍陆柒捌玖"
_UNITS = ["", "拾", "佰", "仟"]
_BIG = ["", "万", "亿", "兆"]


def to_number(v):
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").replace("，", "")
    for ch in ("¥", "￥", "元", "%", "％"):
        s = s.replace(ch, "")
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group(0)) if m else None


def cny_upper(v):
    n = to_number(v)
    if n is None:
        return str(v)
    n = round(abs(n), 2)
    neg = to_number(v) < 0
    integer, frac = int(n), int(round((n - int(n)) * 100))
    jiao, fen = divmod(frac, 10)
    parts = []
    if integer == 0:
        int_str = "零元"
    else:
        groups, x = [], integer
        while x > 0:
            groups.append(x % 10000)
            x //= 10000
        for gi in range(len(groups) - 1, -1, -1):
            g = groups[gi]
            if g == 0:
                if parts and parts[-1] != "零":
                    parts.append("零")
                continue
            seg, zero = "", False
            for pos in range(3, -1, -1):
                d = (g // (10 ** pos)) % 10
                if d == 0:
                    if seg:
                        zero = True
                else:
                    if zero:
                        seg += "零"
                        zero = False
                    seg += _DIGITS[d] + _UNITS[pos]
            parts.append(seg + _BIG[gi])
        int_str = "".join(parts).strip("零")
        while "零零" in int_str:
            int_str = int_str.replace("零零", "零")
        int_str += "元"
    if frac == 0:
        return ("负" if neg else "") + int_str + "整"
    res = ("负" if neg else "") + int_str
    res += _DIGITS[jiao] + "角" if jiao else ("零" if fen else "")
    res += _DIGITS[fen] + "分" if fen else "整"
    return res


def fmt_number(v):
    n = to_number(v)
    if n is None:
        return str(v)
    return "{:,.0f}".format(n) if abs(n - round(n)) < 1e-9 else "{:,.2f}".format(n)


def render(template, mapping):
    out = str(template)
    for k, v in mapping.items():
        out = out.replace("{" + k + "}", str(v) if v is not None else "")
    return out


_FUNCS = {
    "sum": lambda *xs: sum(float(to_number(x) or 0) for x in xs),
    "min": lambda *xs: min(float(to_number(x) or 0) for x in xs),
    "max": lambda *xs: max(float(to_number(x) or 0) for x in xs),
    "count": lambda *xs: len(xs),
    "round": lambda x, n=0: round(float(to_number(x) or 0), int(n)),
    "abs": lambda x: abs(float(to_number(x) or 0)),
    "cny_upper": cny_upper,
    "fmt": fmt_number,
    "len": len,
    "float": float,
    "int": int,
    "str": str,
    "col": lambda seq, key: [d.get(key) for d in seq if isinstance(d, dict)],
    "sum_where": lambda seq, key, val, target: sum(
        float(to_number(d.get(target)) or 0) for d in seq
        if isinstance(d, dict) and str(d.get(key)) == str(val)),
}


def _eval_node(node, ctx):
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, ctx)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in ctx:
            raise KeyError(node.id)
        return ctx[node.id]
    if isinstance(node, ast.UnaryOp):
        v = _eval_node(node.operand, ctx)
        if isinstance(node.op, ast.USub):
            return -float(to_number(v) or 0)
        if isinstance(node.op, ast.UAdd):
            return float(to_number(v) or 0)
    if isinstance(node, ast.BinOp):
        a = _eval_node(node.left, ctx)
        b = _eval_node(node.right, ctx)
        if isinstance(node.op, ast.Mult):
            return float(to_number(a) or 0) * float(to_number(b) or 0)
        if isinstance(node.op, ast.Add):
            try:
                return float(to_number(a) or 0) + float(to_number(b) or 0)
            except Exception:
                return str(a) + str(b)
        if isinstance(node.op, ast.Sub):
            return float(to_number(a) or 0) - float(to_number(b) or 0)
        if isinstance(node.op, ast.Div):
            return float(to_number(a) or 0) / float(to_number(b) or 1)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        fn = _FUNCS.get(node.func.id)
        if fn is None:
            raise ValueError("不支持函数: %s" % node.func.id)
        return fn(*[_eval_node(a, ctx) for a in node.args])
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
        seq = _eval_node(node.value, ctx)
        key = _eval_node(node.slice, ctx)
        return seq[int(key)] if isinstance(key, (int, float)) else seq[key]
    raise ValueError("不支持表达式节点: %s" % type(node).__name__)


def eval_expr(expr, ctx):
    e = str(expr).strip()
    m = re.match(r"^(sum|count|min|max|avg):([A-Za-z_]\w*)(?:\.([A-Za-z_]\w*))?$", e)
    if m:
        op, name, key = m.groups()
        seq = ctx.get(name, [])
        if op == "count":
            return len(seq)
        if not key:
            return sum(float(to_number(x) or 0) for x in seq)
        vals = [float(to_number(d.get(key)) or 0) for d in seq if isinstance(d, dict)]
        if op == "avg":
            return sum(vals) / len(vals) if vals else 0
        return {"sum": sum, "min": min, "max": max}[op](vals)
    return _eval_node(ast.parse(e, mode="eval"), ctx)


def filter_items(items, expr):
    if not expr:
        return list(items)
    m = re.match(r"^\s*([A-Za-z_]\w*)\s*(==|!=|>=|<=|>|<)\s*(.+?)\s*$", str(expr))
    if not m:
        raise ValueError("不支持 items_filter: %s" % expr)
    key, op, raw = m.groups()
    raw = raw.strip().strip("'\"")
    out = []
    for d in items:
        a, b = str(d.get(key, "")), raw
        ok = {"==": a == b, "!=": a != b,
              ">": a > b, "<": a < b, ">=": a >= b, "<=": a <= b}[op]
        if ok:
            out.append(d)
    return out


# --------------------------------------------------------------------------
def resolve_target(editor, loc, sheet_maps, default_sheet=None):
    kind = loc.get("kind", "cell")
    sheet = loc.get("sheet") or default_sheet
    if not sheet:
        return None, None
    if kind == "cell":
        ref = loc.get("ref")
        if not ref:
            col = loc.get("col", "A")
            col = col if isinstance(col, str) and col.isalpha() else num_to_col(int(col))
            ref = "%s%d" % (col, int(loc.get("row", 1)))
        sh, c, r, dc, dr = split_ref(ref)
        sh = sh or sheet
        m = sheet_maps.get(sh, {})
        if r is not None and r in m:
            r = m[r]
        return sh, join_ref(None, c, r, dc, dr)
    if kind in ("label", "value", "formula"):
        kw = {}
        if kind == "label":
            kw["contains"] = str(loc.get("label", ""))
            kw["exact"] = bool(loc.get("exact", False))
        elif kind == "value":
            kw["contains"] = str(loc.get("text", ""))
            kw["exact"] = bool(loc.get("exact", False))
        else:
            kw["formula_contains"] = str(loc.get("contains", ""))
        kw["occurrence"] = int(loc.get("occurrence", 0))
        hit = editor.find_cell(sheet, **kw)
        if not hit:
            return None, None
        _, c, r, _, _ = split_ref(hit)
        c = col_to_num(c) + int(loc.get("offset_col", 0))
        r = r + int(loc.get("offset_row", 0))
        if c < 1 or r < 1:
            return None, None
        return sheet, join_ref(None, num_to_col(c), r)
    return None, None


def write_field_value(editor, sheet, ref, value, spec, row_hint=None):
    kind = spec.get("kind", "auto")
    if kind not in ("formula", "keep_formula", "number", "date", "bool", "blank"):
        tpl = spec.get("value_template")
        if tpl:
            mapping = {"value": value, "index": spec.get("index", ""),
                       "value_fmt": fmt_number(value), "value_upper": cny_upper(value)}
            for k, v in (spec.get("item") or {}).items():
                mapping.setdefault(k, v)
            value = render(tpl, mapping)
    if kind in ("formula", "keep_formula"):
        row = row_hint or split_ref(ref)[2] or 1
        formula = render(str(value or ""), {"row": row})
        editor.write_cell(sheet, ref, formula, kind=kind)
    else:
        editor.write_cell(sheet, ref, value, kind=kind,
                          template_ref=spec.get("template_ref"))


def preview(editor, max_rows=8, max_cols=8):
    out = {}
    for sheet in editor.sheet_order:
        root = editor.sheet_root(sheet)
        sd = root.find("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}sheetData")
        grid = []
        for row in sd.findall("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}row")[:max_rows]:
            cells = []
            for c in row.findall("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}c")[:max_cols]:
                info = editor.cell_value(sheet, c.get("r"))
                cells.append(("=" + info["formula"]) if info["formula"] else info["value"])
            grid.append(cells)
        out[sheet] = grid
    return out


def compare_signature(before, after, dynamic_sheets):
    diffs, expected = [], []

    def add(group, key, a, b, hard=True):
        if a == b:
            return
        (diffs if hard else expected).append("%s | %s: %s -> %s" % (group, key, a, b))

    add("workbook", "sheet 列表", [s["name"] for s in before["sheets"]],
        [s["name"] for s in after["sheets"]])
    add("workbook", "sheet 状态", [s["state"] for s in before["sheets"]],
        [s["state"] for s in after["sheets"]])
    add("styles", "sha256", before.get("styles_sha256"), after.get("styles_sha256"))
    add("workbook", "定义名称", before.get("defined_names"), after.get("defined_names"))
    for key in ("tables", "drawings", "charts", "pivotTables", "slicerCaches", "vba"):
        add("elements", key, before.get(key), after.get(key))
    bmap = {s["name"]: s for s in before["sheets"]}
    amap = {s["name"]: s for s in after["sheets"]}
    for name in bmap:
        if name not in amap:
            continue
        b, a = bmap[name], amap[name]
        for key in ("merged_count", "dv_count", "cf_count", "hyperlinks", "freeze"):
            add(name, key, b.get(key), a.get(key))
        dyn = name in dynamic_sheets
        for key in ("max_row", "dimension", "autofilter"):
            add(name, key, b.get(key), a.get(key), hard=not dyn)
        add(name, "formula_count", b.get("formula_count"), a.get("formula_count"), hard=False)
    return {"identical": not diffs, "differences": diffs, "expected_changes": expected}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("template")
    ap.add_argument("analysis")
    ap.add_argument("answers")
    ap.add_argument("output")
    ap.add_argument("--report", default=None)
    ap.add_argument("--cache-dir", default=None)
    ap.add_argument("--cache-key", default=None)
    args = ap.parse_args()

    work = os.path.dirname(os.path.abspath(args.analysis))
    with open(args.analysis, encoding="utf-8") as f:
        analysis = json.load(f)
    if args.answers == "-":
        raw = sys.stdin.read()
        answers_path = os.path.join(work, "answers.json")
        with open(answers_path, "w", encoding="utf-8") as f:
            f.write(raw)
        answers = json.loads(raw)
    else:
        with open(args.answers, encoding="utf-8") as f:
            answers = json.load(f)

    ed = XlsxEditor(args.template)
    before = ed.signature()
    report = {"template": os.path.basename(args.template), "ok": False,
              "fields_filled": 0, "fields_total": 0, "missing_required": [],
              "unmatched_locators": [], "derived": {}, "item_computed": {},
              "tables": [], "total_cells": [], "cell_updates": [],
              "warnings": list(ed.warnings), "changed_parts": []}

    # 1) 动态清单行：先扩展，后续所有模板坐标都按行映射平移
    sheet_maps, table_info = {}, {}
    for t in analysis.get("list_tables", []):
        sheet = t["sheet"]
        rows_all = answers.get(t.get("rows_source", "items"), [])
        rows = filter_items(rows_all, t.get("items_filter"))
        if sheet in sheet_maps:
            report["warnings"].append("sheet %s 有多个 list_table，第 2 张起按扩展后的坐标解释" % sheet)
        expanded = ed.expand_rows(sheet, t["sample_rows"], len(rows), t.get("template_row"))
        sheet_maps[sheet] = expanded["row_map"]
        table_info[sheet] = expanded
        report["tables"].append({"sheet": sheet, "items": len(rows),
                                 "rows_before": len(t["sample_rows"]),
                                 "rows_after": expanded["count"],
                                 "final_rows": expanded["final_rows"]})
        for i, item in enumerate(rows):
            for colspec in t.get("columns", []):
                key = colspec.get("key")
                if not key:
                    continue
                val = item.get(key, "")
                formula = colspec.get("formula")
                spec = dict(colspec)
                spec["item"] = item
                spec["index"] = i + 1
                final_row = expanded["final_rows"][i]
                ref = "%s%d" % (colspec["col"], final_row)
                if formula and (str(formula).startswith("=") or spec.get("kind") == "formula"):
                    write_field_value(ed, sheet, ref, formula, spec, row_hint=final_row)
                    report["item_computed"].setdefault(key, []).append("=" + str(formula))
                elif formula:
                    try:
                        local_ctx = dict(answers)
                        local_ctx.update(item)
                        val = eval_expr(formula, local_ctx)
                    except Exception as exc:
                        report["warnings"].append("列公式失败 %s[%s]: %s" % (sheet, key, exc))
                    write_field_value(ed, sheet, ref, val, spec, row_hint=final_row)
                    report["item_computed"].setdefault(key, []).append(val)
                else:
                    tref = "%s%d" % (colspec["col"], t.get("template_row") or t["sample_rows"][0])
                    spec["template_ref"] = tref
                    write_field_value(ed, sheet, ref, val, spec, row_hint=final_row)

        footers = t.get("footer_rows", [table_info[sheet]["last"] + 1])
        for tc in t.get("total_cells", []):
            if tc.get("row") is not None:
                r0 = int(tc["row"])
            else:
                fi = int(tc.get("footer_index", 0))
                r0 = int(footers[fi]) if fi < len(footers) else table_info[sheet]["last"] + 1
            r = sheet_maps[sheet].get(r0, r0)
            ref = "%s%d" % (tc["col"], r)
            fr = table_info[sheet]["final_rows"]
            mapping = {"start_row": min(fr) if fr else r,
                       "end_row": max(fr) if fr else r, "row": r}
            formula = tc.get("formula")
            if formula and str(formula).startswith("="):
                value = render(formula, mapping)
                ed.write_cell(sheet, ref, value, kind="formula")
                report["total_cells"].append({"sheet": sheet, "ref": ref, "formula": value})
            elif formula:
                try:
                    val = eval_expr(formula, dict(answers))
                    ed.write_cell(sheet, ref, val, kind=tc.get("kind", "auto"))
                    report["total_cells"].append({"sheet": sheet, "ref": ref, "value": val})
                except Exception as exc:
                    report["warnings"].append("合计公式失败 %s!%s: %s" % (sheet, ref, exc))

    # 2) 派生字段
    ctx = dict(answers)
    for key, expr in (analysis.get("derived_fields") or {}).items():
        try:
            ctx[key] = eval_expr(expr, ctx)
            report["derived"][key] = ctx[key]
        except Exception as exc:
            report["warnings"].append("派生字段 %s 失败: %s" % (key, exc))

    # 3) 普通字段回填
    fields = analysis.get("fields", [])
    report["fields_total"] = len(fields)
    for field in fields:
        key = field["key"]
        if key in answers and answers[key] is not None:
            value = answers[key]
        elif field.get("derived"):
            try:
                value = eval_expr(field["derived"], ctx)
                report["derived"][key] = value
            except Exception as exc:
                report["warnings"].append("字段 %s 派生失败: %s" % (key, exc))
                continue
        elif field.get("default") is not None:
            value = field["default"]
        elif field.get("required", False):
            report["missing_required"].append(key)
            continue
        else:
            continue
        ctx[key] = value
        filled = 0
        for loc in field.get("locators", []):
            sheet, ref = resolve_target(ed, loc, sheet_maps)
            if not sheet or not ref:
                report["unmatched_locators"].append("%s | %s" % (key, json.dumps(loc, ensure_ascii=False)))
                continue
            spec = dict(field)
            spec["item"] = field.get("item") or {}
            write_field_value(ed, sheet, ref, value, spec,
                              row_hint=split_ref(ref)[2])
            if loc.get("check_absent"):
                old = str(loc.get("text", ""))
                if old and old in str(ed.cell_value(sheet, ref).get("value", "")):
                    report["warnings"].append("样例值仍残留: %s!%s %s" % (sheet, ref, old))
            filled += 1
        if filled:
            report["fields_filled"] += 1
        report.setdefault("verification", {}).setdefault("fields", {})[key] = value

    # 4) 固定单元格更新
    for cu in analysis.get("cell_updates", []):
        sheet = cu["sheet"]
        _, col, r, _, _ = split_ref(cu["ref"])
        r = sheet_maps.get(sheet, {}).get(r, r)
        ref = join_ref(None, col, r)
        fr = []
        for shp, inf in table_info.items():
            fr.extend(inf["final_rows"])
        mapping = {"start_row": min(fr) if fr else r, "end_row": max(fr) if fr else r, "row": r}
        formula = cu.get("formula")
        if formula and str(formula).startswith("="):
            v = render(formula, mapping)
            ed.write_cell(sheet, ref, v, kind="formula")
            report["cell_updates"].append({"sheet": sheet, "ref": ref, "formula": v})
        elif formula:
            try:
                v = eval_expr(formula, ctx)
                ed.write_cell(sheet, ref, v, kind=cu.get("kind", "auto"))
                report["cell_updates"].append({"sheet": sheet, "ref": ref, "value": v})
            except Exception as exc:
                report["warnings"].append("单元格公式失败 %s!%s: %s" % (sheet, ref, exc))

    ed.set_full_calc()
    changed = ed.save(args.output)
    ed.close()
    report["changed_parts"] = changed

    after_ed = XlsxEditor(args.output)
    after = after_ed.signature()
    report["verification"] = report.get("verification", {})
    report["verification"]["preview"] = preview(after_ed)
    after_ed.close()

    dynamic_sheets = set(table_info)
    layout = compare_signature(before, after, dynamic_sheets)
    bad_parts = [p for p in changed if not (
        p == "xl/workbook.xml" or p.startswith(("xl/worksheets/", "xl/tables/",
                                                "xl/charts/", "xl/comments",
                                                "xl/drawings/", "xl/pivotCache/")))]
    if bad_parts:
        layout["differences"].append("意外改动的部件: " + ", ".join(bad_parts))
        layout["identical"] = False
    report["layout"] = layout
    report["verification"]["checks"] = {
        "missing_required": len(report["missing_required"]),
        "unmatched_locators": len(report["unmatched_locators"]),
        "layout_differences": len(layout["differences"]),
        "unexpected_changed_parts": len(bad_parts),
        "warnings": len(report["warnings"]),
    }

    report["ok"] = bool(not report["missing_required"] and not report["unmatched_locators"]
                        and layout["identical"] and not bad_parts)
    report["summary"] = ("ok=%s | 字段 %d/%d | 清单 %s | 未命中定位 %d | 板式一致 %s | 警告 %d" %
                         (report["ok"], report["fields_filled"], report["fields_total"],
                          ", ".join("%s:%d项" % (t["sheet"], t["items"]) for t in report["tables"]) or "无",
                          len(report["unmatched_locators"]), layout["identical"],
                          len(report["warnings"])))

    report["analysis_cached"] = False
    if report["ok"] and args.cache_dir and args.cache_key:
        cache = os.path.abspath(args.cache_dir)
        os.makedirs(cache, exist_ok=True)
        dst = os.path.join(cache, "%s.analysis.json" % args.cache_key)
        shutil.copyfile(args.analysis, dst)
        with open(os.path.join(cache, "%s.meta.json" % args.cache_key), "w",
                  encoding="utf-8") as f:
            json.dump({"cached_at": datetime.datetime.now().isoformat(timespec="seconds"),
                       "output": os.path.basename(args.output),
                       "fields": report["fields_filled"],
                       "tables": report["tables"]}, f, ensure_ascii=False, indent=2)
        report["analysis_cached"] = True

    report_path = args.report or os.path.join(work, "fill_report.json")
    os.makedirs(os.path.dirname(os.path.abspath(report_path)), exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
