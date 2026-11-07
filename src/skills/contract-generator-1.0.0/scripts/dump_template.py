#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
第 2 步：把一份合同模板的完整内容按「段落索引 / 表格行列坐标」原样吐出，
供技能运行时分析：哪些信息需要填写、每项信息在文档中的定位是什么。

模板本身不做任何修改、不含占位符。输出的坐标可直接写进 analysis.json 的
locators（cell / paragraph / literal / regex / after_label）。

用法:
    python3 dump_template.py <template.docx> [--out dump.txt] [--json] [--no-hints]
输出坐标约定：
    P<n>        顶层正文段落（doc.paragraphs 的下标）
    T<t> R<r> C<c>  表格单元格；同一合并单元格会在多列重复出现
    H<n>/F<n>   页眉 / 页脚段落
"""
import os
import re
import sys
import json
import argparse
from docx import Document


def _clean(s):
    return (s or "").replace("\t", "\\t").strip()


HINT_PATTERNS = [
    (r"\{\{.*?\}\}", "已有占位符(异常)"),
    (r"[¥￥]\s*[\d,]+(?:\.\d+)?", "金额"),
    (r"\d+(?:\.\d+)?\s*(?:元|万元|%|％)", "金额/比例"),
    (r"(?:19|20)\d{2}\s*[年\-/\.]\s*\d{1,2}\s*[月\-/\.]\s*\d{1,2}", "日期"),
    (r"[\u4e00-\u9fa5]{2,}(?:有限公司|股份公司|集团|公司)", "公司名"),
    (r"_{3,}|＿{3,}|\.{4,}|＿+", "待填空位"),
    (r"统一社会信用代码|税号|开户行|账号|电话|联系人|地址", "主体信息标签"),
    (r"第[一二三四五六七八九十\d]+期|首付|尾款|验收|质保", "付款/交付条款"),
]


def dump(path, with_hints=True):
    doc = Document(path)
    lines = ["# 合同模板结构（运行时分析素材）", "文件: %s" % path, ""]
    rec = {"file": path, "paragraphs": [], "tables": [], "headers": [], "footers": [], "hints": []}

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
            cells = []
            seen = set()
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
        lines.append("=== 疑似需填写位置 (HINTS，仅辅助，最终以分析判断为准) ===")
        allblocks = [(("P%03d" % x["index"]), x["text"]) for x in rec["paragraphs"]]
        for t in rec["tables"]:
            for r in t["rows"]:
                for c in r["cells"]:
                    if c["text"] and not c["merged"]:
                        allblocks.append(("T%d R%02d C%02d" % (t["index"], r["row"], c["col"]), c["text"]))
        for coord, txt in allblocks:
            tags = set()
            for pat, tag in HINT_PATTERNS:
                if re.search(pat, txt):
                    tags.add(tag)
                    for m in re.finditer(pat, txt):
                        rec["hints"].append({"coord": coord, "tag": tag, "match": m.group(0)})
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
