#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
第 4 步：按「第 2 步分析并缓存的字段清单 analysis.json」把 answers.json 回填到
原始合同模板，生成版式与模板完全一致的新合同。

核心原则
- 模板不含任何占位符、也不被修改；填什么、填在哪里，全部来自运行时生成的
  analysis.json（字段清单 + locators）。
- 服务/产品清单支持动态增删行：深拷贝模板样例行 XML，按列映射写入任意行数。
- 所有文本替换都在原有 run 上进行，保留字体/字号/加粗；动态行由原行 XML 深拷贝，
  保留边框、底纹、行高、对齐等全部版式。

用法:
    python3 fill_contract.py <template.docx> <analysis.json> <answers.json> <output.docx> \
        [--prices prices.csv] [--report report.json]

analysis.json / answers.json 的完整格式见 references/analysis_schema.md
"""
import os
import re
import sys
import copy
import json
import argparse

from docx import Document
from docx.table import _Row
from docx.oxml.ns import qn

try:
    from lookup_price import best_price
except ImportError:  # 允许从其它目录直接调用
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from lookup_price import best_price


# --------------------------------------------------------------------------
# 基础工具
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
    """用 (text, rPr) 列表重建段落的 runs，保留字符级格式。"""
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
    """spans: [(start, end, new_text)]，按原字符的 run 格式重建。"""
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
    """整格写入，保留首 run 的字符格式与单元格格式。"""
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
    """数字金额 -> 人民币中文大写。"""
    n = to_number(v)
    if n is None:
        return str(v)
    n = round(abs(n), 2)
    neg = to_number(v) < 0
    integer = int(n)
    frac = int(round((n - integer) * 100))
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
    if fen:
        res += _DIGITS[fen] + "分"
    else:
        res += "整"
    return res


def render(value, template="{value}", item=None, index=None):
    """模板变量：{value} {value_fmt} {value_upper} {index} 及清单条目字段 {key}。"""
    if isinstance(value, float) and value.is_integer():
        value = int(value)  # 避免 50.0% / 1359700.0 这类多余小数
    s = str(value) if value is not None else ""
    mapping = {
        "value": s,
        "value_fmt": fmt_number(s),
        "value_upper": cny_upper(s),
        "index": str(index) if index is not None else "",
    }
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
        env[k] = n if (n is not None and str(v).strip().replace(",", "").replace(".", "", 1).isdigit()) else v
    for it in (items or []):
        for k, v in it.items():
            n = to_number(v)
            env.setdefault(k, n if n is not None else v)
    env["sum"] = lambda col: sum(to_number(it.get(col)) or 0 for it in (items or []))
    env["sum_where"] = lambda key, val, col: sum(
        to_number(it.get(col)) or 0 for it in (items or []) if str(it.get(key)) == str(val))
    env["cny_upper"] = lambda x: cny_upper(x)
    env["fmt"] = lambda x: fmt_number(x)
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
    """带坐标的段落列表，坐标与 dump_template.py 输出一致。"""
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
                    out.append({"where": "cell", "table": ti, "row": ri, "col": ci, "para": p})
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
    """生成唯一临时标记，避免「A 字段填进去的值恰好是 B 字段的锚点」被二次替换。"""
    seq = [0]

    def sink(text):
        seq[0] += 1
        token = "\ue000CG%d\ue001" % seq[0]
        store[token] = str(text)
        return token

    return sink


def resolve_sentinels(doc, store):
    """所有定位完成后，把临时标记统一解析为最终值（保留标记处原有的字符格式）。"""
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
    """sink: 把最终值换成唯一临时标记的函数；为 None 时直接写入最终值。"""
    kind = loc.get("kind", "literal")
    template = loc.get("value_template", "{value}")
    new_text = render(value, template)
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
        label = loc.get("label", "")
        stops = loc.get("stop", [])
        if isinstance(stops, str):
            stops = [stops]
        for c in ctxs:
            if budget is not None and matched >= budget:
                break
            full = "".join(r.text for r in _runs(c["para"]))
            if not label or label not in full:
                continue
            s = full.find(label) + len(label)
            e = len(full)
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
# 价格 / 派生字段
# --------------------------------------------------------------------------
def auto_price(analysis, answers, prices_path, report):
    if not prices_path or not os.path.exists(prices_path):
        return
    mode = analysis.get("deploy_mode")
    items = answers.get("service_items") or []
    price_cols = set()
    for st in analysis.get("service_tables", []):
        for c in st.get("columns", []):
            if c.get("price_lookup"):
                price_cols.add((c["key"], c.get("query_key", "name")))
    for it in items:
        for key, qk in sorted(price_cols):
            if str(it.get(key) or "").strip():
                continue
            q = str(it.get(qk) or "").strip()
            if not q:
                continue
            p = best_price(prices_path, q, mode)
            if p:
                it[key] = p
                report["price_lookups"].append({"item": q, "field": key, "price": p, "mode": mode})
            else:
                report["warnings"].append("清单项「%s」的 %s 未在价格知识库命中，且用户未指定" % (q, key))
    for fld in analysis.get("fields", []):
        pl = fld.get("price_lookup")
        if not pl or str(answers.get(fld["key"]) or "").strip():
            continue
        q = str(answers.get(pl.get("query_key", ""), "") or pl.get("query", "")).strip()
        if not q:
            continue
        p = best_price(prices_path, q, pl.get("mode") or mode)
        if p:
            answers[fld["key"]] = p
            report["price_lookups"].append({"field": fld["key"], "query": q, "price": p,
                                            "mode": pl.get("mode") or mode})
        else:
            report["warnings"].append("字段「%s」查价未命中：%s" % (fld["key"], q))


def compute_item_columns(analysis, answers, report):
    """把列公式（如 总价=单价×数量）的结果写回清单条目，供派生字段/汇总取用。"""
    by_source = {}
    for st in analysis.get("service_tables", []):
        by_source.setdefault(st.get("rows_source", "service_items"), []).append(st)
    for src, tables in by_source.items():
        for it in (answers.get(src) or []):
            for st in tables:
                for col in st.get("columns", []):
                    key = col.get("key")
                    if not key or str(it.get(key) or "").strip() or not col.get("formula"):
                        continue
                    try:
                        it[key] = eval_expr(col["formula"], make_env(dict(it), []))
                        report["item_computed"].append({"list": src, "key": key,
                                                        "value": it[key], "formula": col["formula"]})
                    except Exception as e:
                        report["warnings"].append("清单项 %s 列公式失败: %s" % (it.get("name", "?"), e))


def compute_derived(analysis, answers, items, report):
    values = {k: v for k, v in answers.items()
              if k != "service_items" and not str(k).startswith("_")}
    for fld in analysis.get("fields", []):
        if fld.get("formula") and not str(values.get(fld["key"]) or "").strip():
            try:
                values[fld["key"]] = eval_expr(fld["formula"], make_env(values, items))
                report["derived"][fld["key"]] = values[fld["key"]]
            except Exception as e:
                report["warnings"].append("字段 %s 公式计算失败: %s" % (fld["key"], e))
    for key, expr in (analysis.get("derived_fields") or {}).items():
        if str(values.get(key) or "").strip():
            continue
        try:
            values[key] = eval_expr(expr, make_env(values, items))
            report["derived"][key] = values[key]
        except Exception as e:
            report["warnings"].append("派生字段 %s 计算失败: %s" % (key, e))
    return values


# --------------------------------------------------------------------------
# 服务/产品清单动态行
# --------------------------------------------------------------------------
def snapshot_tables(doc, analysis):
    snaps = []
    for st in analysis.get("service_tables", []):
        tbl = doc.tables[st["table_index"]]
        header_rows = st.get("header_rows", 1)
        sample_rows = st.get("sample_rows")
        if sample_rows is None:
            sample_rows = list(range(header_rows, len(tbl.rows)))
        footer_rows = st.get("footer_rows", [])
        sample_els = [tbl.rows[i]._tr for i in sample_rows]
        footer_els = [tbl.rows[i]._tr for i in footer_rows]
        snaps.append({
            "cfg": st, "tbl": tbl,
            "sample_els": sample_els, "footer_els": footer_els,
            "tmpl": copy.deepcopy(sample_els[0]) if sample_els else None,
            "rows_before": len(tbl.rows),
        })
    return snaps


def expand_service_table(snap, answers, values, report):
    st, tbl = snap["cfg"], snap["tbl"]
    src = st.get("rows_source", "service_items")
    items = list(answers.get(src) or [])
    if st.get("items_filter"):
        items = [it for it in items if eval_filter(st["items_filter"], it)]
    if snap["tmpl"] is None:
        report["warnings"].append("表%d 无样例行，无法动态扩展" % st["table_index"])
        return

    clones = []
    for idx, it in enumerate(items, start=1):
        clones.append((copy.deepcopy(snap["tmpl"]), it, idx))

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
                    report["warnings"].append("表%d 列%s 公式失败: %s" % (st["table_index"], col.get("key"), e))
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
            report["total_cells"].append({"table": st["table_index"], "col": tc["col"], "value": text})
        except Exception as e:
            report["warnings"].append("表%d 合计单元格计算失败: %s" % (st["table_index"], e))

    report["service_tables"].append({
        "table": st["table_index"], "rows_before": snap["rows_before"],
        "rows_after": len(tbl.rows), "items": len(items),
        "filter": st.get("items_filter"),
    })


# --------------------------------------------------------------------------
# 生成后外观归一化（通用算法，不依赖任何模板的具体位置）
# --------------------------------------------------------------------------
def _is_red_hex(h):
    """判断一个 6 位十六进制 RGB 是否为「红系」。

    规则：R 分量明显主导（R > G*1.5 且 R > B*1.5），且足够明亮（R>=0x70）。
    这样能稳定命中模板遗留的红字（如 C0504D / CF7B79），同时：
      - 蓝系（b 主导，如 4F81BD 蓝、theme 蓝）不会误判；
      - 黑/灰/深褐等中性色不会误判。
    主题色（themeColor）不在 hex 范围内，本判定返回 False，不处理。
    """
    h = (h or "").strip().lower().replace("#", "")
    if len(h) != 6:
        return False
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return False
    if r < 0x70 or (r + g + b) < 0x80:
        return False
    return r > g * 1.5 and r > b * 1.5


def _normalize_xml_text(text):
    """对单个 XML 部件文本做最小改动（不重排结构，零侵入）：
      1) 所有红系 <w:color w:val="RRGGBB"/> 改写为黑色 000000
         —— 覆盖 run 直设红字与表格样式红字；
      2) 所有非 none 的 <w:highlight w:val="..."/> 改写为 none
         —— 清除黄色「待填区」背景等。
     注意：OOXML 里 color / highlight 是「子元素 + w:val 属性」，并非属性形式，
     因此必须匹配 <w:color ... w:val="..."> 这种子元素写法（早期版本误写成
     w:color="..." 属性形式，导致完全未命中）。
    返回 (text, changed)。"""
    changed = False

    def repl_color(m):
        nonlocal changed
        if _is_red_hex(m.group(1)):
            changed = True
            return m.group(0).replace('w:val="%s"' % m.group(1), 'w:val="000000"')
        return m.group(0)

    text = re.sub(r'<w:color[^>]*w:val="([0-9A-Fa-f]{6})"[^>]*>', repl_color, text)

    def repl_hl(m):
        nonlocal changed
        if m.group(1).lower() not in ("none",):
            changed = True
            return m.group(0).replace('w:val="%s"' % m.group(1), 'w:val="none"')
        return m.group(0)

    text = re.sub(r'<w:highlight[^>]*w:val="([^"]*)"[^>]*>', repl_hl, text)
    return text, changed


def normalize_docx_appearance(path):
    """解包 docx，对所有 XML 部件（document / styles / header / footer / theme …）
    执行外观归一化，再无感重打包。把模板遗留的「高亮背景」和「红字样式」统一清理为
    白底黑字，保证交付合同外观中性，与具体模板、具体文字位置解耦。

    返回是否发生改动。"""
    import zipfile
    import tempfile
    import shutil
    tmp = tempfile.mkdtemp(prefix="cgnorm_")
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
            new_text, changed = _normalize_xml_text(text)
            if changed:
                with open(fp, "w", encoding="utf-8") as f:
                    f.write(new_text)
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
                        arc = os.path.relpath(full, tmp).replace(os.sep, "/")
                        z.write(full, arc)
            os.remove(backup)
        return changed_any
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _fix_template_typos(doc):
    """修复模板已知的表头错字：单元格文本「适用场适景」因多出一个『适』而成错字，
    修正为「适用场景」。仅当单元格文本精确命中时才删除其中孤立的『适』字 run，
    不影响其它任何内容，也不修改模板源文件（只作用于生成副本）。"""
    fixed = 0
    for t in doc.tables:
        for r in t.rows:
            for c in r.cells:
                if c.text == "适用场适景":
                    for p in c.paragraphs:
                        for run in list(p.runs):
                            if run.text == "适":
                                run._element.getparent().remove(run._element)
                                fixed += 1
    return fixed


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def fill(template_path, analysis_path, answers_path, out_path, prices_path=None,
         normalize=True):
    with open(analysis_path, encoding="utf-8") as f:
        analysis = json.load(f)
    with open(answers_path, encoding="utf-8") as f:
        answers = json.load(f)

    report = {
        "template": template_path, "analysis": analysis_path, "answers": answers_path,
        "output": out_path, "fields_total": 0, "fields_filled": 0,
        "missing_required": [], "locators": [], "unmatched_locators": [],
        "price_lookups": [], "derived": {}, "item_computed": [], "service_tables": [],
        "total_cells": [], "cell_updates": [], "warnings": [], "ok": True,
        "verification": {},
    }

    prices_path = prices_path or analysis.get("price_table")
    if prices_path and not os.path.isabs(prices_path):
        cand = os.path.join(os.path.dirname(os.path.abspath(analysis_path)), prices_path)
        if os.path.exists(cand):
            prices_path = cand
        else:
            cand = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", prices_path)
            if os.path.exists(cand):
                prices_path = cand
    auto_price(analysis, answers, prices_path, report)
    compute_item_columns(analysis, answers, report)

    items = answers.get("service_items") or []
    values = compute_derived(analysis, answers, items, report)

    doc = Document(template_path)
    snaps = snapshot_tables(doc, analysis)  # 在任何文本替换前取样例行版式
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
            report["locators"].append({"field": key, "index": i,
                                       "kind": loc.get("kind", "literal"), "matched": n})
            if n == 0:
                report["unmatched_locators"].append({"field": key, "index": i, "locator": loc})

    for snap in snaps:
        expand_service_table(snap, answers, values, report)

    for cu in analysis.get("cell_updates", []):
        try:
            tbl = doc.tables[cu["table"]]
            cell = tbl.rows[cu["row"]].cells[cu["col"]]
            val = eval_expr(cu["formula"], make_env(values, items))
            text = render(val, cu.get("value_template", "{value}"))
            sample = cu.get("sample")
            if not (sample and replace_literal_in_cell(cell, sample, text)):
                set_cell_text(cell, text)
            report["cell_updates"].append({"table": cu["table"], "row": cu["row"],
                                           "col": cu["col"], "value": text})
        except Exception as e:
            report["warnings"].append("单元格更新 T%d R%d C%d 失败: %s" %
                                      (cu.get("table", -1), cu.get("row", -1), cu.get("col", -1), e))

    report["sentinels_resolved"] = resolve_sentinels(doc, sentinels)
    report["typos_fixed"] = _fix_template_typos(doc)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    doc.save(out_path)

    # 生成后外观归一化：清除高亮背景、把红系字体色改为黑色（白底黑字）
    if normalize:
        report["appearance_normalized"] = normalize_docx_appearance(out_path)

    # 生成后复核
    out = Document(out_path)
    blob = "\n".join(p.text for p in out.paragraphs)
    for t in out.tables:
        for r in t.rows:
            for c in r.cells:
                blob += "\n" + c.text
    for sect in out.sections:
        for p in list(sect.header.paragraphs) + list(sect.footer.paragraphs):
            blob += "\n" + p.text
    if "{{" in blob:
        report["warnings"].append("输出中仍存在 {{...}} 文本，请检查")
    for fld in analysis.get("fields", []):
        for loc in fld.get("locators", []):
            if loc.get("check_absent") and loc.get("text") and loc["text"] in blob:
                report["warnings"].append("字段 %s 的原样例值「%s」仍残留在输出中" %
                                          (fld["key"], loc["text"]))
    # 人民币大写重复自检（历史缺陷：只替换「叁仟」留下模板自带的「元整」，出现「陆仟元整  元整」）
    dup = re.findall(r"[元圓圆]整[\s　]*[元圓圆]整", blob)
    if dup:
        report["warnings"].append("疑似大写金额重复「元整…元整」（%d 处），请检查大写 locator 是否覆盖完整原文" % len(dup))

    # 验收摘要：让调用方看这一份报告即可复核，无需再打开 docx / 再跑一次核对脚本
    verify = {
        "fields": {k: str(v) for k, v in values.items() if not str(k).startswith("_")},
        "service_items": len(items),
        "tables": [{"table": s["table"], "rows": s["rows_after"], "items": s["items"]}
                   for s in report.get("service_tables", [])],
        "paragraph_count": len(out.paragraphs),
        "checks": {
            "placeholder_left": "{{" in blob,
            "uppercase_duplicated": bool(dup),
            "unmatched_locators": len(report["unmatched_locators"]),
            "missing_required": len(report["missing_required"]),
            "warnings": len(report["warnings"]),
        },
        "amount_lines": [p.text.strip() for p in out.paragraphs
                         if re.search(r"(金额|费用|价款|总额|合计|大写)", p.text)][:12],
    }
    report["verification"] = verify

    # 自检失败同样判 ok=false：避免有缺陷的 analysis 被自动入缓存，也避免「生成→读回→改→重生成」的往返
    if report["missing_required"] or report["unmatched_locators"]:
        report["ok"] = False
    if dup or "{{" in blob:
        report["ok"] = False
        verify["checks"]["self_check_failed"] = True
    report["summary"] = ("ok=%s | 字段 %d/%d | 清单 %d 项 | 未命中定位 %d | 警告 %d | 大写重复 %s"
                         % (report["ok"], report["fields_filled"], report["fields_total"],
                            len(items), len(report["unmatched_locators"]),
                            len(report["warnings"]), "是" if dup else "否"))
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("template")
    ap.add_argument("analysis")
    ap.add_argument("answers", help="answers.json 路径；传 '-' 表示从 stdin 读 JSON（省一次落盘往返）")
    ap.add_argument("output")
    ap.add_argument("--prices")
    ap.add_argument("--report")
    ap.add_argument("--cache-dir", default=None,
                    help="analysis.json 缓存目录；生成成功时自动入缓存，供同模板下次复用")
    ap.add_argument("--no-normalize", action="store_true",
                    help="跳过生成后外观归一化（保留模板原有高亮/红字样式）")
    ap.add_argument("--force", action="store_true",
                    help="即使报告 ok=false 也强制入缓存（一般不建议使用）")
    args = ap.parse_args()

    answers_path = args.answers
    if answers_path == "-":
        # 一次工具调用即可完成「写 answers + 生成 + 验收」，无需先单独写文件
        raw = sys.stdin.read()
        try:
            data = json.loads(raw)
        except Exception as e:
            print("ERROR: stdin 不是合法 JSON: %s" % e, file=sys.stderr)
            sys.exit(2)
        answers_path = os.path.join(os.path.dirname(os.path.abspath(args.output)), "answers.json")
        os.makedirs(os.path.dirname(os.path.abspath(answers_path)), exist_ok=True)
        with open(answers_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    rep = fill(args.template, args.analysis, answers_path, args.output, args.prices,
               normalize=not args.no_normalize)

    # 生成成功后把 analysis.json 按模板内容 hash 入缓存：下一次同模板可直接跳过第 2 步
    if rep["ok"] or args.force:
        try:
            import hashlib
            import shutil
            h = hashlib.sha256()
            with open(args.template, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
            thash = h.hexdigest()[:16]
            cache_dir = os.path.abspath(
                args.cache_dir or os.path.join(
                    os.path.dirname(os.path.dirname(os.path.abspath(args.output))),
                    "_analysis_cache"))
            os.makedirs(cache_dir, exist_ok=True)
            dst = os.path.join(cache_dir, "%s.analysis.json" % thash)
            shutil.copyfile(args.analysis, dst)
            with open(os.path.join(cache_dir, "%s.meta.json" % thash),
                      "w", encoding="utf-8") as f:
                json.dump({"template": os.path.basename(args.template),
                           "template_path": args.template.replace("\\", "/"),
                           "analysis": args.analysis.replace("\\", "/"),
                           "verified_ok": rep["ok"]}, f, ensure_ascii=False, indent=2)
            rep["analysis_cached"] = {"path": dst, "template_hash": thash}
        except Exception as e:
            rep["warnings"].append("analysis 入缓存失败（不影响成品）: %s" % e)
    else:
        rep["analysis_cached"] = {"skipped": "报告 ok=false，未入缓存；修正后重新生成"}

    text = json.dumps(rep, ensure_ascii=False, indent=2)
    if args.report:
        os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
        with open(args.report, "w", encoding="utf-8") as f:
            f.write(text)
    print(text)
    print("\n%s -> %s" % ("OK" if rep["ok"] else "CHECK WARNINGS", args.output))
    if not rep["ok"]:
        sys.exit(2)


if __name__ == "__main__":
    main()
