#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
第 4 步：按运行时分析缓存 analysis.json 把 answers.json 回填到样版 DOCX，
生成「板式与样版一致、内容按新要求改写」的新 Word 文件。

原则
- 样版不含占位符、不被修改；改什么/改在哪里全部来自 analysis.json。
- 文本替换在原有 run 上进行，保留字体/字号/加粗；清单行由样例行 XML 深拷贝，
  继承边框、底纹、行高、列宽等全部版式。
- 生成后对比「板式指纹」（页边距/纸张/段落样式/非清单表结构），发现走样即报告 fail。

用法:
    python3 fill_doc.py <template.docx> <analysis.json> <answers.json|-> <output.docx> \
        [--report report.json] [--cache-dir 目录] [--normalize-appearance]

analysis.json / answers.json 格式见 references/analysis_schema.md
"""
import os
import re
import sys
import copy
import json
import zipfile
import tempfile
import shutil
import hashlib
import argparse

from docx import Document
from docx.table import _Row
from docx.oxml.ns import qn

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dump_template import layout_signature  # noqa: E402


# --------------------------------------------------------------------------
# 文本与 run 处理：所有替换复用原 run 的字符格式
# --------------------------------------------------------------------------
def _runs(para):
    return list(para.runs)


def _char_rpr(runs):
    out = []
    for r in runs:
        rPr = r._element.find(qn("w:rPr"))
        out.extend([copy.deepcopy(rPr) if rPr is not None else None] * len(r.text))
    return out


def _rewrite(para, segments):
    for r in list(para.runs):
        r._element.getparent().remove(r._element)
    for text, rPr in segments:
        if text == "":
            continue
        nr = para.add_run(text)
        el = nr._element
        old = el.find(qn("w:rPr"))
        if old is not None:
            el.remove(old)
        if rPr is not None:
            el.insert(0, copy.deepcopy(rPr))


def _replace_spans(para, spans):
    runs = _runs(para)
    if not runs or not spans:
        return 0
    full = "".join(r.text for r in runs)
    rpr = _char_rpr(runs)
    segs, i, applied = [], 0, 0
    for s, e, new in spans:
        if s < i or s > len(full) or e > len(full) or e < s:
            continue
        if s > i:
            segs.append((full[i:s], rpr[i] if i < len(rpr) else None))
        segs.append((new, rpr[s] if s < len(rpr) else (rpr[-1] if rpr else None)))
        i, applied = e, applied + 1
    if i < len(full):
        segs.append((full[i:], rpr[i] if i < len(rpr) else None))
    _rewrite(para, segs)
    return applied


def _literal_spans(full, old, new, budget=None):
    spans, i = [], 0
    if not old:
        return spans
    while True:
        idx = full.find(old, i)
        if idx < 0 or (budget is not None and len(spans) >= budget):
            break
        spans.append((idx, idx + len(old), new))
        i = idx + len(old)
    return spans


def set_cell_text(cell, text):
    paras = cell.paragraphs
    if not paras:
        p = cell.add_paragraph()
        p.add_run(str(text))
        return
    p = paras[0]
    for extra in paras[1:]:
        extra._p.getparent().remove(extra._p)
    runs = _runs(p)
    if runs:
        for r in runs[1:]:
            r._element.getparent().remove(r._element)
        runs[0].text = str(text)
    else:
        p.add_run(str(text))


def replace_literal_in_cell(cell, old, new, sink=None):
    for p in cell.paragraphs:
        full = "".join(r.text for r in _runs(p))
        if old and old in full:
            spans = _literal_spans(full, old, "")
            spans = [(s, e, sink(new) if sink else new) for s, e, _ in spans]
            _replace_spans(p, spans)
            return True
    return False


# --------------------------------------------------------------------------
# 数值与表达式（通用文档也可能需要合计/日期等派生值）
# --------------------------------------------------------------------------
def to_number(v):
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").replace("，", "")
    for ch in ("¥", "￥", "元", "%", "％"):
        s = s.replace(ch, "")
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group(0)) if m else None


def fmt_number(v):
    s = str(v).strip() if v is not None else ""
    n = to_number(s)
    if n is None:
        return s
    return "{:,.0f}".format(n) if abs(n - round(n)) < 1e-9 else "{:,.2f}".format(n)


_DIGITS = "零壹贰叁肆伍陆柒捌玖"
_UNITS = ["", "拾", "佰", "仟"]
_BIG = ["", "万", "亿", "兆"]


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
    if jiao:
        res += _DIGITS[jiao] + "角"
    elif fen:
        res += "零"
    res += _DIGITS[fen] + "分" if fen else "整"
    return res


def render(value, template="{value}", item=None, index=None):
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    s = str(value) if value is not None else ""
    mapping = {"value": s, "value_fmt": fmt_number(s), "value_upper": cny_upper(s),
               "index": str(index) if index is not None else ""}
    if item:
        for k, v in item.items():
            mapping.setdefault(k, str(v) if v is not None else "")
    out = template
    for k, v in mapping.items():
        out = out.replace("{" + k + "}", str(v))
    return out


_EXPR_ALLOWED = re.compile(
    r"^[A-Za-z0-9_\s\.,\+\-\*/\(\)'\"=<>!&|%:×·\u4e00-\u9fff]*$")


def make_env(values, items):
    env = {}
    for k, v in values.items():
        n = to_number(v)
        plain = str(v).strip().replace(",", "").replace(".", "", 1)
        env[k] = n if (n is not None and plain.isdigit()) else v
    for it in (items or []):
        for k, v in it.items():
            n = to_number(v)
            env.setdefault(k, n if n is not None else v)
    env["sum"] = lambda col: sum(to_number(it.get(col)) or 0 for it in (items or []))
    env["sum_where"] = lambda key, val, col: sum(
        to_number(it.get(col)) or 0 for it in (items or []) if str(it.get(key)) == str(val))
    env["cny_upper"] = cny_upper
    env["fmt"] = fmt_number
    env["count"] = lambda: len(items or [])
    return env


def eval_expr(expr, env):
    if not isinstance(expr, str):
        return expr
    e = expr.strip()
    m = re.match(r"^sum:(\w+)$", e)
    if m:
        return env["sum"](m.group(1))
    m = re.match(r"^sum_where:(\w+)=([^:]+):(\w+)$", e)
    if m:
        return env["sum_where"](m.group(1), m.group(2), m.group(3))
    m = re.match(r"^cny_upper:(.+)$", e)
    if m:
        return cny_upper(env.get(m.group(1).strip(), m.group(1)))
    if not _EXPR_ALLOWED.match(e):
        raise ValueError("不允许的表达式: %s" % e)
    return eval(e, {"__builtins__": {}}, env)  # noqa: S307 - 表达式来自本地分析缓存


def eval_filter(expr, item):
    env = {k: (str(v) if v is not None else "") for k, v in item.items()}
    env["str"] = str
    if not _EXPR_ALLOWED.match(expr):
        raise ValueError("不允许的过滤表达式: %s" % expr)
    return bool(eval(expr, {"__builtins__": {}}, env))  # noqa: S307


# --------------------------------------------------------------------------
# 定位器
# --------------------------------------------------------------------------
def collect_contexts(doc):
    out = []
    for i, p in enumerate(doc.paragraphs):
        out.append({"where": "body", "p_index": i, "para": p})
    for ti, tbl in enumerate(doc.tables):
        for ri, row in enumerate(tbl.rows):
            seen = set()
            for ci, cell in enumerate(row.cells):
                if cell._tc in seen:
                    continue
                seen.add(cell._tc)
                for p in cell.paragraphs:
                    out.append({"where": "cell", "table": ti, "row": ri,
                                "col": ci, "para": p})
    for si, sect in enumerate(doc.sections):
        for p in list(sect.header.paragraphs) + list(sect.footer.paragraphs):
            out.append({"where": "headerfooter", "section": si, "para": p})
    return out


def _ctx_ok(ctx, loc):
    w = loc.get("where")
    if w == "body":
        if ctx["where"] != "body":
            return False
        if "paragraph_index" in loc and ctx["p_index"] != loc["paragraph_index"]:
            return False
    elif w == "cell" or loc.get("table") is not None:
        if ctx["where"] != "cell":
            return False
        for k in ("table", "row", "col"):
            if k in loc and ctx[k] != loc[k]:
                return False
    elif w == "headerfooter" and ctx["where"] != "headerfooter":
        return False
    if loc.get("contains") and loc["contains"] not in ctx["para"].text:
        return False
    return True


def make_sink(store):
    seq = [0]

    def sink(text):
        seq[0] += 1
        token = "\ue000LC%d\ue001" % seq[0]
        store[token] = str(text)
        return token
    return sink


def resolve_sentinels(doc, store):
    if not store:
        return 0
    tokens = sorted(store.items(), key=lambda kv: -len(kv[0]))
    total = 0
    for c in collect_contexts(doc):
        p = c["para"]
        full = "".join(r.text for r in _runs(p))
        if "\ue000" not in full:
            continue
        spans = []
        for token, val in tokens:
            for m in re.finditer(re.escape(token), full):
                spans.append((m.start(), m.end(), val))
        spans.sort(key=lambda x: x[0])
        if spans:
            total += _replace_spans(p, spans)
    return total


def apply_locator(doc, loc, value, values, sink=None):
    kind = loc.get("kind", "literal")
    new_text = render(value, loc.get("value_template", "{value}"))
    put = sink or (lambda t: t)
    scope = loc.get("scope", "all")
    budget = None if scope in (None, "all") else (1 if scope == "first" else int(scope))
    matched = 0
    ctxs = [c for c in collect_contexts(doc) if _ctx_ok(c, loc)]

    if kind == "literal":
        old = loc.get("text")
        if not old:
            return 0
        for c in ctxs:
            if budget is not None and matched >= budget:
                break
            full = "".join(r.text for r in _runs(c["para"]))
            spans = _literal_spans(full, old, "",
                                   None if budget is None else budget - matched)
            spans = [(s, e, put(new_text)) for s, e, _ in spans]
            if spans:
                matched += _replace_spans(c["para"], spans)

    elif kind == "regex":
        pat = re.compile(loc["pattern"])
        for c in ctxs:
            if budget is not None and matched >= budget:
                break
            full = "".join(r.text for r in _runs(c["para"]))
            spans = []
            for m in pat.finditer(full):
                rep = new_text.replace("{m0}", m.group(0))
                spans.append((m.start(), m.end(), put(rep)))
                if budget is not None and len(spans) + matched >= budget:
                    break
            if spans:
                matched += _replace_spans(c["para"], spans)

    elif kind == "after_label":
        label, stops = loc.get("label", ""), loc.get("stop", [])
        if isinstance(stops, str):
            stops = [stops]
        for c in ctxs:
            if budget is not None and matched >= budget:
                break
            full = "".join(r.text for r in _runs(c["para"]))
            if not label or label not in full:
                continue
            s, e = full.find(label) + len(label), len(full)
            for stp in stops:
                k = full.find(stp, s)
                if k >= 0:
                    e = min(e, k)
            matched += _replace_spans(c["para"], [(s, e, put(new_text))])

    elif kind == "cell":
        cell = doc.tables[loc["table"]].rows[loc["row"]].cells[loc["col"]]
        sample = loc.get("sample")
        if sample and replace_literal_in_cell(cell, sample, new_text, sink):
            matched = 1
        else:
            set_cell_text(cell, put(new_text))
            matched = 1

    elif kind == "paragraph":
        p = doc.paragraphs[loc["index"]]
        full = "".join(r.text for r in _runs(p))
        if full:
            matched = _replace_spans(p, [(0, len(full), put(new_text))])
        else:
            p.add_run(put(new_text))
            matched = 1
    return matched


# --------------------------------------------------------------------------
# 动态清单表（行数可多可少，样例行 XML 深拷贝，版式继承）
# --------------------------------------------------------------------------
def snapshot_tables(doc, tables_cfg):
    snaps = []
    for st in tables_cfg:
        tbl = doc.tables[st["table_index"]]
        header_rows = st.get("header_rows", 1)
        sample_rows = st.get("sample_rows")
        if sample_rows is None:
            sample_rows = list(range(header_rows, len(tbl.rows)))
        footer_rows = st.get("footer_rows", [])
        sample_els = [tbl.rows[i]._tr for i in sample_rows]
        footer_els = [tbl.rows[i]._tr for i in footer_rows]
        snaps.append({"cfg": st, "tbl": tbl, "sample_els": sample_els,
                      "footer_els": footer_els,
                      "tmpl": copy.deepcopy(sample_els[0]) if sample_els else None,
                      "rows_before": len(tbl.rows)})
    return snaps


def expand_list_table(snap, answers, values, report):
    st, tbl = snap["cfg"], snap["tbl"]
    src = st.get("rows_source", "items")
    items = list(answers.get(src) or [])
    if st.get("items_filter"):
        items = [it for it in items if eval_filter(st["items_filter"], it)]
    if snap["tmpl"] is None:
        report["warnings"].append("表%d 无样例行，无法动态扩展" % st["table_index"])
        return
    clones = [(copy.deepcopy(snap["tmpl"]), it, i)
              for i, it in enumerate(items, start=1)]
    anchor = snap["footer_els"][0] if snap["footer_els"] else None
    for tr, _, _ in clones:
        if anchor is not None:
            anchor.addprevious(tr)
        else:
            tbl._tbl.append(tr)
    for el in snap["sample_els"]:
        parent = el.getparent()
        if parent is not None:
            parent.remove(el)
    for tr, it, idx in clones:
        row = _Row(tr, tbl)
        for col in st.get("columns", []):
            ci = col["col"]
            if ci >= len(row.cells):
                continue
            val = it.get(col.get("key"), col.get("default", ""))
            if (val in (None, "")) and col.get("formula"):
                try:
                    val = eval_expr(col["formula"], make_env(dict(it), []))
                except Exception as e:
                    report["warnings"].append("表%d 列%s 公式失败: %s" %
                                              (st["table_index"], col.get("key"), e))
            text = render(val, col.get("value_template", "{value}"), it, idx)
            cell = row.cells[ci]
            sample = col.get("sample")
            if not (sample and replace_literal_in_cell(cell, sample, text)):
                set_cell_text(cell, text)
    for tc in st.get("total_cells", []):
        try:
            if "footer_index" in tc:
                row = _Row(snap["footer_els"][tc["footer_index"]], tbl)
            else:
                row = tbl.rows[tc["row"]]
            val = eval_expr(tc["formula"], make_env(values, items))
            text = render(val, tc.get("value_template", "{value}"))
            cell = row.cells[tc["col"]]
            sample = tc.get("sample")
            if not (sample and replace_literal_in_cell(cell, sample, text)):
                set_cell_text(cell, text)
            report["total_cells"].append({"table": st["table_index"],
                                          "col": tc["col"], "value": text})
        except Exception as e:
            report["warnings"].append("表%d 合计单元格计算失败: %s" % (st["table_index"], e))
    report["tables"].append({"table": st["table_index"],
                             "rows_before": snap["rows_before"],
                             "rows_after": len(tbl.rows), "items": len(items),
                             "filter": st.get("items_filter")})


# --------------------------------------------------------------------------
# 板式指纹对比（可选的外观归一化）
# --------------------------------------------------------------------------
def compare_layout(before, after, dynamic_tables):
    diffs = []
    if before.get("sections") != after.get("sections"):
        diffs.append("页面尺寸/页边距变化")
    if before.get("body_paragraphs") != after.get("body_paragraphs"):
        diffs.append("正文段落数变化: %s -> %s" %
                     (before.get("body_paragraphs"), after.get("body_paragraphs")))
    if before.get("styles") != after.get("styles"):
        diffs.append("段落样式分布变化")
    if before.get("header_footer") != after.get("header_footer"):
        diffs.append("页眉/页脚段落数变化")
    bt, at = before.get("tables", []), after.get("tables", [])
    if len(bt) != len(at):
        diffs.append("表格数量变化: %d -> %d" % (len(bt), len(at)))
    else:
        for i, (b, a) in enumerate(zip(bt, at)):
            if i in dynamic_tables:
                b, a = dict(b), dict(a)
                b.pop("rows", None)
                a.pop("rows", None)  # 动态行数变化属预期
            if b != a:
                diffs.append("表%d 结构变化: %s -> %s" % (i, b, a))
    return {"identical": not diffs, "differences": diffs,
            "dynamic_tables": sorted(dynamic_tables)}


def _is_red_hex(h):
    h = (h or "").strip().lower().replace("#", "")
    if len(h) != 6:
        return False
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return False
    return r >= 0x70 and (r + g + b) >= 0x80 and r > g * 1.5 and r > b * 1.5


def normalize_appearance(path):
    """可选：清除高亮背景、红字改黑。默认关闭——通用文档颜色可能是有意设计。"""
    tmp = tempfile.mkdtemp(prefix="lc_norm_")
    try:
        with zipfile.ZipFile(path, "r") as z:
            names = z.namelist()
            z.extractall(tmp)
        changed_any = False
        for name in names:
            if not name.lower().endswith(".xml"):
                continue
            fp = os.path.join(tmp, name)
            with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
            new = re.sub(
                r'<w:color[^>]*w:val="([0-9A-Fa-f]{6})"[^>]*>',
                lambda m: m.group(0).replace('w:val="%s"' % m.group(1), 'w:val="000000"')
                if _is_red_hex(m.group(1)) else m.group(0), text)
            new = re.sub(
                r'<w:highlight[^>]*w:val="([^"]*)"[^>]*>',
                lambda m: m.group(0).replace('w:val="%s"' % m.group(1), 'w:val="none"')
                if m.group(1).lower() != "none" else m.group(0), new)
            if new != text:
                with open(fp, "w", encoding="utf-8") as f:
                    f.write(new)
                changed_any = True
        if changed_any:
            backup = path + ".bak"
            if os.path.exists(backup):
                os.remove(backup)
            os.rename(path, backup)
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
                for root, _dirs, files in os.walk(tmp):
                    for fn in files:
                        full = os.path.join(root, fn)
                        z.write(full, os.path.relpath(full, tmp).replace(os.sep, "/"))
            os.remove(backup)
        return changed_any
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def fill(template_path, analysis_path, answers_path, out_path, normalize=False):
    with open(analysis_path, encoding="utf-8") as f:
        analysis = json.load(f)
    with open(answers_path, encoding="utf-8") as f:
        answers = json.load(f)

    report = {"template": template_path, "analysis": analysis_path,
              "answers": answers_path, "output": out_path,
              "fields_total": 0, "fields_filled": 0, "missing_required": [],
              "unmatched_locators": [], "derived": {}, "item_computed": [],
              "tables": [], "total_cells": [], "cell_updates": [],
              "warnings": [], "ok": True, "verification": {}}

    tables_cfg = analysis.get("list_tables")
    if tables_cfg is None:
        tables_cfg = analysis.get("service_tables", [])  # 兼容旧键
    items_all = []
    for st in tables_cfg:
        items_all.extend(answers.get(st.get("rows_source", "items")) or [])

    # 列公式先写入清单条目，供合计与派生字段使用
    for st in tables_cfg:
        src = st.get("rows_source", "items")
        for it in (answers.get(src) or []):
            for col in st.get("columns", []):
                key = col.get("key")
                if not key or str(it.get(key) or "").strip() or not col.get("formula"):
                    continue
                try:
                    it[key] = eval_expr(col["formula"], make_env(dict(it), []))
                    report["item_computed"].append({"list": src, "key": key,
                                                    "value": it[key],
                                                    "formula": col["formula"]})
                except Exception as e:
                    report["warnings"].append("清单项 %s 列公式失败: %s" %
                                              (it.get("name", "?"), e))

    values = {k: v for k, v in answers.items()
              if not str(k).startswith("_") and not isinstance(v, list)}
    for fld in analysis.get("fields", []):
        if fld.get("formula") and not str(values.get(fld["key"]) or "").strip():
            try:
                values[fld["key"]] = eval_expr(fld["formula"], make_env(values, items_all))
                report["derived"][fld["key"]] = values[fld["key"]]
            except Exception as e:
                report["warnings"].append("字段 %s 公式失败: %s" % (fld["key"], e))
    for key, expr in (analysis.get("derived_fields") or {}).items():
        if str(values.get(key) or "").strip():
            continue
        try:
            values[key] = eval_expr(expr, make_env(values, items_all))
            report["derived"][key] = values[key]
        except Exception as e:
            report["warnings"].append("派生字段 %s 失败: %s" % (key, e))

    doc = Document(template_path)
    before_sig = layout_signature(doc)
    snaps = snapshot_tables(doc, tables_cfg)
    sentinels = {}
    sink = make_sink(sentinels)

    for fld in analysis.get("fields", []):
        key = fld["key"]
        report["fields_total"] += 1
        val = values.get(key)
        if val in (None, ""):
            if fld.get("required"):
                report["missing_required"].append(key)
            continue
        report["fields_filled"] += 1
        for i, loc in enumerate(fld.get("locators", [])):
            n = apply_locator(doc, loc, val, values, sink)
            if n == 0:
                report["unmatched_locators"].append({"field": key, "index": i,
                                                     "locator": loc})

    for snap in snaps:
        expand_list_table(snap, answers, values, report)

    for cu in analysis.get("cell_updates", []):
        try:
            cell = doc.tables[cu["table"]].rows[cu["row"]].cells[cu["col"]]
            val = eval_expr(cu["formula"], make_env(values, items_all))
            text = render(val, cu.get("value_template", "{value}"))
            sample = cu.get("sample")
            if not (sample and replace_literal_in_cell(cell, sample, text)):
                set_cell_text(cell, text)
            report["cell_updates"].append({"table": cu["table"], "row": cu["row"],
                                           "col": cu["col"], "value": text})
        except Exception as e:
            report["warnings"].append("单元格更新 T%s R%s C%s 失败: %s" %
                                      (cu.get("table"), cu.get("row"), cu.get("col"), e))

    report["sentinels_resolved"] = resolve_sentinels(doc, sentinels)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    doc.save(out_path)
    if normalize:
        report["appearance_normalized"] = normalize_appearance(out_path)

    out = Document(out_path)
    after_sig = layout_signature(out)
    report["layout"] = compare_layout(before_sig, after_sig,
                                      {st["table_index"] for st in tables_cfg})
    blob = "\n".join(p.text for p in out.paragraphs)
    for t in out.tables:
        for r in t.rows:
            for c in r.cells:
                blob += "\n" + c.text
    for s in out.sections:
        for p in list(s.header.paragraphs) + list(s.footer.paragraphs):
            blob += "\n" + p.text

    sentinel_left = "\ue000" in blob
    placeholder_left = "{{" in blob
    if sentinel_left:
        report["warnings"].append("输出中残留内部标记，回填未完成")
    if placeholder_left:
        report["warnings"].append("输出中仍存在 {{...}} 文本，请检查")
    if report["layout"]["differences"]:
        report["warnings"].append("板式指纹变化: " + "; ".join(report["layout"]["differences"]))

    verify = {
        "fields": {k: str(v) for k, v in values.items()},
        "tables": report["tables"],
        "checks": {
            "unmatched_locators": len(report["unmatched_locators"]),
            "missing_required": len(report["missing_required"]),
            "sentinel_left": sentinel_left,
            "placeholder_left": placeholder_left,
            "layout_drift": not report["layout"]["identical"],
            "warnings": len(report["warnings"]),
        },
        "preview_lines": [p.text.strip() for p in out.paragraphs if p.text.strip()][:12],
    }
    report["verification"] = verify
    if (report["missing_required"] or report["unmatched_locators"] or
            sentinel_left or not report["layout"]["identical"]):
        report["ok"] = False
    report["summary"] = ("ok=%s | 字段 %d/%d | 清单 %d 项 | 未命中定位 %d | "
                         "板式一致 %s | 警告 %d" %
                         (report["ok"], report["fields_filled"], report["fields_total"],
                          sum(t.get("items", 0) for t in report["tables"]),
                          len(report["unmatched_locators"]),
                          report["layout"]["identical"], len(report["warnings"])))
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("template")
    ap.add_argument("analysis")
    ap.add_argument("answers", help="路径；传 '-' 表示从 stdin 读 JSON")
    ap.add_argument("output")
    ap.add_argument("--report")
    ap.add_argument("--cache-dir")
    ap.add_argument("--cache-key", default=None,
                    help="prep.py 输出的 selected.hash；与源样版 hash 一致，PDF 转换场景必传")
    ap.add_argument("--normalize-appearance", action="store_true")
    args = ap.parse_args()

    answers_path = args.answers
    if answers_path == "-":
        try:
            data = json.loads(sys.stdin.read())
        except Exception as e:
            print("ERROR: stdin 不是合法 JSON: %s" % e, file=sys.stderr)
            sys.exit(2)
        answers_path = os.path.join(os.path.dirname(os.path.abspath(args.output)),
                                    "answers.json")
        os.makedirs(os.path.dirname(answers_path), exist_ok=True)
        with open(answers_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    rep = fill(args.template, args.analysis, answers_path, args.output,
               args.normalize_appearance)

    if rep["ok"]:
        try:
            thash = args.cache_key
            if not thash:
                h = hashlib.sha256()
                with open(args.template, "rb") as f:
                    for chunk in iter(lambda: f.read(65536), b""):
                        h.update(chunk)
                thash = h.hexdigest()[:16]
            cache_dir = os.path.abspath(args.cache_dir or os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(args.output))),
                "_analysis_cache"))
            os.makedirs(cache_dir, exist_ok=True)
            shutil.copyfile(args.analysis, os.path.join(cache_dir,
                                                        "%s.analysis.json" % thash))
            with open(os.path.join(cache_dir, "%s.meta.json" % thash),
                      "w", encoding="utf-8") as f:
                json.dump({"template": os.path.basename(args.template),
                           "template_path": args.template.replace("\\", "/"),
                           "verified_ok": True}, f, ensure_ascii=False, indent=2)
            rep["analysis_cached"] = {"path": cache_dir, "template_hash": thash}
        except Exception as e:
            rep["warnings"].append("analysis 入缓存失败（不影响成品）: %s" % e)
    else:
        rep["analysis_cached"] = {"skipped": "报告 ok=false，未入缓存"}

    text = json.dumps(rep, ensure_ascii=False, indent=2)
    if args.report:
        os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
        with open(args.report, "w", encoding="utf-8") as f:
            f.write(text)
    print(text)
    if not rep["ok"]:
        sys.exit(2)


if __name__ == "__main__":
    main()
