#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按 marks.json 给「源报价单副本」上色：偏贵=红，偏便宜=绿。原件永不修改。

支持 xlsx/xlsm（单元格填充+批注）、csv（转成带色 xlsx）、docx（文字高亮）、
pdf（高亮注释）、图片（按 bbox 画框）。无法定位的条目写入 unmarked.md，不静默丢弃。

用法：
  python mark_sources.py --marks out/marks.json --out-dir out/
  python mark_sources.py --marks out/marks.json --out-dir out/ --suffix _已标记
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qc_common import (  # noqa: E402
    GREEN_FILL, GREEN_FONT, RED_FILL, RED_FONT, dump_json, dump_text,
    load_json, to_float,
)


def _same_price(cell_text, price_text):
    a, b = to_float(cell_text), to_float(price_text)
    if a is None or b is None:
        return str(cell_text).strip() == str(price_text).strip()
    return abs(a - b) < 1e-6


def _target_path(src, out_dir, suffix, force_ext=None):
    stem, ext = os.path.splitext(os.path.basename(src))
    ext = force_ext or ext
    return os.path.join(out_dir, f"{stem}{suffix}{ext}")


def mark_xlsx(src, dst, marks, colors):
    from openpyxl import load_workbook
    from openpyxl.comments import Comment
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = load_workbook(dst)
    ok, failed = 0, []
    for m in marks:
        loc, fl = m.get("locator", {}), m["flag"]
        ws = wb[loc["sheet"]] if loc.get("sheet") in wb.sheetnames else wb.active
        r, c = loc.get("row"), loc.get("price_col")
        cell = None
        if loc.get("cell"):
            try:
                cell = ws[loc["cell"]]
            except Exception:
                pass
        if cell is None and r:
            if c:
                cell = ws.cell(r, c)
            if cell is None or not _same_price(cell.value, loc.get("price_text", "")):
                # 兜底：在该行内按价格数值/文本反查单元格
                for cc in range(1, ws.max_column + 1):
                    cand = ws.cell(r, cc)
                    if _same_price(cand.value, loc.get("price_text", "")) or (
                        to_float(cand.value) is not None and to_float(cand.value) == to_float(m.get("price"))
                    ):
                        cell = cand
                        break
        if cell is None:
            failed.append(f"{m['name']} / {m['supplier']}：无法定位价格单元格（sheet={loc.get('sheet')} row={r} col={c}）")
            continue
        fill, font_color = colors[fl]
        cell.fill = PatternFill("solid", fgColor=fill)
        cell.font = Font(color=font_color, bold=True)
        cell.comment = Comment(m.get("comment", ""), "比价助手")
        ok += 1
    wb.save(dst)
    return ok, failed


def mark_csv(src, dst_xlsx, marks, colors):
    import csv
    from openpyxl import Workbook
    from openpyxl.comments import Comment
    from openpyxl.styles import Font, PatternFill

    rows = None
    for enc in ("utf-8-sig", "gbk", "utf-8"):
        try:
            with open(src, "r", encoding=enc, newline="") as f:
                rows = list(csv.reader(f))
            break
        except UnicodeDecodeError:
            continue
    if rows is None:
        return 0, [f"{os.path.basename(src)}：CSV 编码无法识别"]
    wb = Workbook()
    ws = wb.active
    ws.title = "报价单"
    for row in rows:
        ws.append([to_float(v) if to_float(v) is not None and _looks_numeric(v) else v for v in row])
    ok, failed = 0, []
    for m in marks:
        loc = m.get("locator", {})
        r, c = loc.get("row"), loc.get("price_col")
        cell = ws.cell(r, c) if r and c else None
        if cell is None:
            failed.append(f"{m['name']}：CSV 无法定位 row={r} col={c}")
            continue
        fill, font_color = colors[m["flag"]]
        cell.fill = PatternFill("solid", fgColor=fill)
        cell.font = Font(color=font_color, bold=True)
        cell.comment = Comment(m.get("comment", ""), "比价助手")
        ok += 1
    wb.save(dst_xlsx)
    return ok, failed


def _looks_numeric(v):
    t = str(v).strip()
    return bool(t) and sum(ch.isdigit() for ch in t) >= max(1, len(t) - 3) and any(ch.isdigit() for ch in t)


def mark_docx(src, dst, marks, colors):
    try:
        from docx import Document
        from docx.enum.text import WD_COLOR_INDEX
    except ImportError:
        return 0, [f"{os.path.basename(src)}：缺少 python-docx"]
    doc = Document(dst)
    hl = {"expensive": WD_COLOR_INDEX.RED, "cheap": WD_COLOR_INDEX.BRIGHT_GREEN}
    ok, failed = 0, []
    for m in marks:
        loc = m.get("locator", {})
        price_text = str(loc.get("price_text", ""))
        hit = False
        ti, r, c = loc.get("table"), loc.get("row"), loc.get("price_col")
        if ti is not None and r and c and ti < len(doc.tables):
            tbl = doc.tables[ti]
            if r - 1 < len(tbl.rows):
                cells = tbl.rows[r - 1].cells
                if c - 1 < len(cells):
                    for p in cells[c - 1].paragraphs:
                        for run in p.runs:
                            if _same_price(run.text, price_text) or price_text in run.text:
                                run.font.highlight_color = hl[m["flag"]]
                                hit = True
        if not hit:
            # 兜底：全文按价格文本反查，只染第一处匹配，避免同价不同品项被误染
            found = False
            for p in doc.paragraphs:
                if found:
                    break
                for run in p.runs:
                    if price_text and (price_text in run.text or _same_price(run.text, price_text)):
                        run.font.highlight_color = hl[m["flag"]]
                        hit = found = True
                        break
            for tbl in doc.tables:
                if found:
                    break
                for row in tbl.rows:
                    if found:
                        break
                    for cell in row.cells:
                        if found:
                            break
                        for p in cell.paragraphs:
                            for run in p.runs:
                                if price_text and (price_text in run.text or _same_price(run.text, price_text)):
                                    run.font.highlight_color = hl[m["flag"]]
                                    hit = found = True
                                    break
        if hit:
            ok += 1
        else:
            failed.append(f"{m['name']} / {m['supplier']}：DOCX 中未找到价格文本『{price_text}』")
    doc.save(dst)
    return ok, failed


def mark_pdf(src, dst, marks, colors):
    try:
        import fitz
    except ImportError:
        return 0, [f"{os.path.basename(src)}：缺少 PyMuPDF（pip install pymupdf）"]
    doc = fitz.open(dst)
    ok, failed = 0, []
    rgb = {"expensive": (1, 0.15, 0.15), "cheap": (0.1, 0.7, 0.25)}
    for m in marks:
        loc = m.get("locator", {})
        pi = max(0, int(loc.get("page", 1)) - 1)
        if pi >= len(doc):
            failed.append(f"{m['name']}：页码 {pi+1} 超出范围")
            continue
        page = doc[pi]
        rects = []
        if loc.get("bbox"):
            rects = [fitz.Rect(loc["bbox"])]
        elif loc.get("price_text"):
            rects = page.search_for(str(loc["price_text"]))
        if not rects:
            failed.append(f"{m['name']} / {m['supplier']}：PDF 中未找到价格文本『{loc.get('price_text')}』")
            continue
        for rect in rects[:2]:
            annot = page.add_highlight_annot(rect)
            annot.set_colors(stroke=rgb[m["flag"]])
            annot.set_opacity(0.35)
            annot.set_info(content=m.get("comment", ""))
            annot.update()
        ok += 1
    doc.saveIncr()
    doc.close()
    return ok, failed


def mark_image(src, dst, marks, colors):
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return 0, [f"{os.path.basename(src)}：缺少 Pillow"]
    img = Image.open(dst).convert("RGBA")
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    rgb = {"expensive": (220, 30, 30, 90), "cheap": (20, 160, 60, 90)}
    ok, failed = 0, []
    for m in marks:
        bbox = (m.get("locator") or {}).get("bbox")
        if not bbox or len(bbox) != 4:
            failed.append(f"{m['name']}：图片缺少 bbox，无法画框（请在手工 JSON 中补 locator.bbox）")
            continue
        fill = rgb[m["flag"]]
        draw.rectangle(bbox, fill=fill, outline=fill[:3] + (255,), width=3)
        ok += 1
    Image.alpha_composite(img, layer).convert("RGB").save(dst)
    return ok, failed


def main():
    ap = argparse.ArgumentParser(description="给源报价单副本标红/标绿（原件不动）")
    ap.add_argument("--marks", default="comparison_output/marks.json")
    ap.add_argument("--out-dir", default="comparison_output")
    ap.add_argument("--suffix", default="_marked")
    args = ap.parse_args()

    data = load_json(args.marks)
    marks = data.get("items", [])
    colors = {
        "expensive": (data.get("red_fill", RED_FILL), data.get("red_font", RED_FONT)),
        "cheap": (data.get("green_fill", GREEN_FILL), data.get("green_font", GREEN_FONT)),
    }
    os.makedirs(args.out_dir, exist_ok=True)
    by_file = defaultdict(list)
    for m in marks:
        path = (m.get("locator") or {}).get("file") or ""
        if not path and m.get("source_file"):
            cand = os.path.join(args.out_dir, m["source_file"])
            path = cand if os.path.isfile(cand) else ""
        by_file[path].append(m)

    report, outputs, unmarked = [], [], []
    for src, ms in by_file.items():
        if not src or not os.path.isfile(src):
            unmarked += [f"{m['name']} / {m['supplier']}：源文件不可达（{src or '未提供 locator.file'}）" for m in ms]
            continue
        ext = os.path.splitext(src)[1].lower()
        kind = (ms[0].get("locator") or {}).get("kind", "")
        force_ext = ".xlsx" if ext in (".csv", ".txt") else None
        dst = _target_path(src, args.out_dir, args.suffix, force_ext)
        if os.path.abspath(src) == os.path.abspath(dst):
            dst = _target_path(src, args.out_dir, args.suffix + "_copy", force_ext)
        # csv/txt 不复制（直接生成新的带色 xlsx），其余一律先复制再改副本
        if not force_ext:
            shutil.copy2(src, dst)
        try:
            if ext in (".xlsx", ".xlsm") or kind == "xlsx":
                ok, failed = mark_xlsx(src, dst, ms, colors)
            elif ext == ".csv" or kind == "csv":
                ok, failed = mark_csv(src, dst, ms, colors)
            elif ext == ".docx" or kind == "docx":
                ok, failed = mark_docx(src, dst, ms, colors)
            elif ext == ".pdf" or kind == "pdf":
                ok, failed = mark_pdf(src, dst, ms, colors)
            elif ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"):
                ok, failed = mark_image(src, dst, ms, colors)
            else:
                ok, failed = 0, [f"{os.path.basename(src)}：格式 {ext or kind} 不支持标色"]
        except Exception as e:
            ok, failed = 0, [f"{os.path.basename(src)}：标色异常 {type(e).__name__}: {e}"]
        outputs.append(dst)
        report.append({"source": src, "output": dst, "marked": ok, "failed": failed})
        print(f"  {os.path.basename(src)} -> {os.path.basename(dst)}  成功 {ok}/{len(ms)}")
        unmarked += failed

    current = {os.path.abspath(x) for x in outputs}
    stale = [os.path.join(args.out_dir, fn) for fn in sorted(os.listdir(args.out_dir))
             if args.suffix in fn and os.path.isfile(os.path.join(args.out_dir, fn))
             and os.path.abspath(os.path.join(args.out_dir, fn)) not in current]
    dump_json(os.path.join(args.out_dir, "mark_report.json"), {
        "generated_at": data.get("generated_at", ""), "total_marks": len(marks),
        "marked": sum(r["marked"] for r in report), "outputs": outputs,
        "stale_outputs": stale, "files": report,
    })
    dump_text(os.path.join(args.out_dir, "unmarked.md"),
              "# 未能标色的条目\n\n" + ("\n".join(f"- {x}" for x in unmarked) if unmarked else "- 无，全部标色成功。") + "\n")
    print(f"[OK] 标色完成 {sum(r['marked'] for r in report)}/{len(marks)} 处，副本 {len(outputs)} 份，输出于 {os.path.abspath(args.out_dir)}")
    if unmarked:
        print(f"[warn] {len(unmarked)} 处未能标色，详见 unmarked.md")
    if stale:
        print(f"[warn] 输出目录还有 {len(stale)} 个历史标色副本本次未更新（重跑前建议清理）：")
        for p in stale[:8]:
            print("   -", os.path.basename(p))


if __name__ == "__main__":
    main()
