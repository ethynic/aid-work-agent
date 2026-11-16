#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把样版 DOCX 的内容按「段落索引 / 表格行列坐标」原样转储，供运行时分析
「哪些内容需要按新要求改写、每项改写位于何处」。样版本身不修改、不含占位符。

输出坐标（与 prep.py / fill_doc.py 一致）：
    P<n>            顶层正文段落下标（doc.paragraphs）
    T<t> R<r> C<c>  表格单元格（合并单元格会在多列重复出现）
    H<s>-<n>/F<s>-<n> 页眉/页脚段落

用法:
    python3 dump_template.py <template.docx> [--out dump.txt] [--json] [--no-hints]
"""
import os
import re
import sys
import json
import argparse
from docx import Document


def _clean(s):
    return (s or "").replace("\t", "\\t").strip()


# 通用「疑似可变内容」提示。仅辅助人工/模型判断，绝不为某类文档写死字段。
HINT_PATTERNS = [
    (r"\{\{.*?\}\}|\[\[.*?\]\]", "已有占位符(异常)"),
    (r"[¥￥]\s*[\d,]+(?:\.\d+)?", "金额"),
    (r"\d+(?:\.\d+)?\s*(?:元|万元|%|％|人|天|小时|个|套|件|kg|KG|米|km|㎡|m2)", "数值+单位"),
    (r"(?:19|20)\d{2}\s*[年\-/\.]\s*\d{1,2}\s*[月\-/\.]\s*\d{1,2}", "日期"),
    (r"1[3-9]\d{9}", "手机号"),
    (r"[\w.\-]+@[\w.\-]+\.\w+", "邮箱"),
    (r"\b\d{15,18}[0-9Xx]?\b", "证件/长编号"),
    (r"https?://\S+", "网址"),
    (r"_{3,}|＿{3,}|\.{4,}|[（(]\s*[)）]", "待填空位"),
    (r"编号|序号|名称|日期|地址|电话|联系人|负责人|金额|合计|总价|备注|版本|状态", "通用标签"),
]


def layout_signature(doc):
    """通用板式指纹：用于生成后对比「版式是否走样」，不依赖任何具体文档内容。"""
    sections = []
    for s in doc.sections:
        sections.append({
            "page_w": round(s.page_width.cm, 2) if s.page_width else None,
            "page_h": round(s.page_height.cm, 2) if s.page_height else None,
            "margin_l": round(s.left_margin.cm, 2) if s.left_margin else None,
            "margin_r": round(s.right_margin.cm, 2) if s.right_margin else None,
            "margin_t": round(s.top_margin.cm, 2) if s.top_margin else None,
            "margin_b": round(s.bottom_margin.cm, 2) if s.bottom_margin else None,
            "orientation": str(s.orientation),
        })
    styles = {}
    for p in doc.paragraphs:
        name = p.style.name if p.style else "(default)"
        styles[name] = styles.get(name, 0) + 1
    tables = []
    for t in doc.tables:
        try:
            style = t.style.name if t.style else None
        except Exception:
            style = None
        tables.append({"rows": len(t.rows), "cols": len(t.columns), "style": style})
    hf = []
    for s in doc.sections:
        hf.append({"header": len(s.header.paragraphs), "footer": len(s.footer.paragraphs)})
    return {"sections": sections, "body_paragraphs": len(doc.paragraphs),
            "styles": styles, "tables": tables, "header_footer": hf}


def dump(path, with_hints=True):
    doc = Document(path)
    lines = ["# 样版结构（运行时分析素材）", "文件: %s" % path, ""]
    rec = {"file": path, "paragraphs": [], "tables": [], "headers": [],
           "footers": [], "hints": [], "layout_signature": layout_signature(doc)}

    lines.append("=== 正文段落 (P) ===")
    for i, p in enumerate(doc.paragraphs):
        t = _clean(p.text)
        if t:
            lines.append("[P%03d] %s" % (i, t))
            rec["paragraphs"].append({"index": i, "text": t})

    lines.append("")
    lines.append("=== 表格 (T) ===")
    for ti, tbl in enumerate(doc.tables):
        lines.append("--- T%d: %d 行 x %d 列 ---" % (ti, len(tbl.rows), len(tbl.columns)))
        tinfo = {"index": ti, "rows": []}
        for ri, row in enumerate(tbl.rows):
            cells, seen = [], set()
            for ci, c in enumerate(row.cells):
                key = id(c._tc)
                mark = " (合并)" if key in seen else ""
                seen.add(key)
                txt = _clean(c.text)
                cells.append({"col": ci, "text": txt, "merged": bool(mark)})
                lines.append("[T%d R%02d C%02d]%s %s" % (ti, ri, ci, mark, txt))
            tinfo["rows"].append({"row": ri, "cells": cells})
        rec["tables"].append(tinfo)

    lines.append("")
    lines.append("=== 页眉/页脚 (H/F) ===")
    for si, sect in enumerate(doc.sections):
        for hi, p in enumerate(sect.header.paragraphs):
            t = _clean(p.text)
            if t:
                lines.append("[H%d-%d] %s" % (si, hi, t))
                rec["headers"].append({"section": si, "index": hi, "text": t})
        for fi, p in enumerate(sect.footer.paragraphs):
            t = _clean(p.text)
            if t:
                lines.append("[F%d-%d] %s" % (si, fi, t))
                rec["footers"].append({"section": si, "index": fi, "text": t})

    if with_hints:
        lines.append("")
        lines.append("=== 疑似需改写位置 (HINTS，仅辅助，最终以分析判断为准) ===")
        blocks = [(("P%03d" % x["index"]), x["text"]) for x in rec["paragraphs"]]
        for t in rec["tables"]:
            for r in t["rows"]:
                for c in r["cells"]:
                    if c["text"] and not c["merged"]:
                        blocks.append(("T%d R%02d C%02d" % (t["index"], r["row"], c["col"]), c["text"]))
        for coord, txt in blocks:
            tags = set()
            for pat, tag in HINT_PATTERNS:
                if re.search(pat, txt):
                    tags.add(tag)
            if tags:
                lines.append("%-14s [%s] %s" % (coord, ",".join(sorted(tags)), txt[:160]))
    return "\n".join(lines) + "\n", rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("template")
    ap.add_argument("--out", default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-hints", action="store_true")
    args = ap.parse_args()
    text, rec = dump(args.template, not args.no_hints)
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
