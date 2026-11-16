#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PDF 样版 -> 可编辑 DOCX（尽量保留原板式）。

优先使用 pdf2docx（版面还原度最高）；缺失时降级为 PyMuPDF 文本流 + python-docx
（仅保文字顺序，板式不保真，会在报告里明确标注）。

同时给出「文本覆盖率」：PDF 正文字符有多少出现在转换后的 docx 中。
覆盖率过低通常意味着原文在图片 / 文本框 / 形状里，后续无法按坐标回填，
此时应提示用户提供 Word 原件或先 OCR，而不是硬着头皮继续。

用法:
    python3 pdf_to_docx.py <source.pdf> <output.docx> [--pages 1-3] [--dpi 150] [--force]
输出: JSON 报告（不打印正文）
"""
import os
import re
import sys
import json
import hashlib
import argparse


def file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def pdf_text(path, pages=None):
    """用 PyMuPDF 提取纯文本，返回 (全文, 页数, 每页文本列表)。"""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return "", 0, []
    doc = fitz.open(path)
    total = len(doc)
    idxs = parse_pages(pages, total) if pages else list(range(total))
    per_page = []
    for i in idxs:
        if 0 <= i < total:
            per_page.append(doc[i].get_text("text"))
    doc.close()
    return "\n".join(per_page), total, per_page


def parse_pages(spec, total):
    """'1-3,5' -> [0,1,2,4]（1-based 输入，0-based 输出）。"""
    out = []
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^(\d+)\s*-\s*(\d+)$", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            out.extend(range(max(1, a) - 1, min(total, b)))
        elif part.isdigit():
            i = int(part) - 1
            if 0 <= i < total:
                out.append(i)
    return sorted(set(out))


def docx_text(path):
    try:
        from docx import Document
    except ImportError:
        return ""
    doc = Document(path)
    parts = [p.text for p in doc.paragraphs]
    for t in doc.tables:
        for r in t.rows:
            for c in r.cells:
                parts.append(c.text)
    for s in doc.sections:
        parts.extend(p.text for p in s.header.paragraphs)
        parts.extend(p.text for p in s.footer.paragraphs)
    return "\n".join(parts)


def _norm(s):
    return re.sub(r"\s+", "", s or "")


def coverage(pdf_raw, docx_raw):
    a, b = _norm(pdf_raw), _norm(docx_raw)
    if not a:
        return None
    # 以 12 字滑窗比对，避免短字符因OCR/排版细微差异被算作丢失
    chunks = [a[i:i + 12] for i in range(0, max(1, len(a) - 11), 6)]
    if not chunks:
        return 1.0
    hit = sum(1 for c in chunks if c in b)
    return round(hit / len(chunks), 3)


def convert_pdf2docx(src, dst, pages=None, dpi=150):
    from pdf2docx import Converter
    cv = Converter(src)
    try:
        kwargs = {"dpi": int(dpi)}
        if pages:
            kwargs["pages"] = parse_pages(pages, len(cv.pages)) if hasattr(cv, "pages") else None
            if not kwargs["pages"]:
                kwargs.pop("pages")
        cv.convert(dst, **kwargs)
    finally:
        cv.close()
    return "pdf2docx"


def convert_fallback(src, dst, pages=None):
    """降级路径：文本流写 docx，板式不保真。"""
    from docx import Document
    raw, _total, per_page = pdf_text(src, pages)
    doc = Document()
    for pi, text in enumerate(per_page):
        if pi:
            doc.add_page_break()
        for line in text.splitlines():
            line = line.strip()
            if line:
                doc.add_paragraph(line)
    doc.save(dst)
    return "fitz-text-fallback"


def convert(src, dst, pages=None, dpi=150, force=False):
    src = os.path.abspath(src)
    dst = os.path.abspath(dst)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    report = {"source": src.replace("\\", "/"), "output": dst.replace("\\", "/"),
              "source_hash": file_hash(src), "pages_arg": pages, "warnings": []}

    raw, total, _ = pdf_text(src, pages)
    report["pdf_pages"] = total
    report["pdf_chars"] = len(_norm(raw))
    if report["pdf_chars"] < 30:
        report["warnings"].append(
            "PDF 几乎无可提取文字（疑似扫描件/纯图片）。转换后文字可能不可编辑，"
            "建议提供 Word 原件或先做 OCR，否则无法按坐标回填。")

    if os.path.exists(dst) and not force:
        marker = dst + ".hash"
        if os.path.exists(marker):
            with open(marker, encoding="utf-8") as f:
                old = f.read().strip()
            if old == report["source_hash"]:
                report["reused"] = True
                report["converter"] = "cached"
                report["coverage"] = coverage(raw, docx_text(dst))
                return report

    try:
        converter = convert_pdf2docx(src, dst, pages, dpi)
    except Exception as e:
        report["warnings"].append("pdf2docx 转换失败，降级为文本流（板式不保真）: %s" % e)
        converter = convert_fallback(src, dst, pages)
    report["converter"] = converter
    report["reused"] = False

    cov = coverage(raw, docx_text(dst))
    report["coverage"] = cov
    if cov is not None and cov < 0.7:
        report["warnings"].append(
            "文本覆盖率仅 %.0f%%，部分内容可能在文本框/图片中，无法按坐标回填；"
            "建议改用 Word 原件。" % (cov * 100))
    with open(dst + ".hash", "w", encoding="utf-8") as f:
        f.write(report["source_hash"])
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("output")
    ap.add_argument("--pages", default=None, help="仅转换指定页，如 1-3,5")
    ap.add_argument("--dpi", type=int, default=150)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    print(json.dumps(convert(args.source, args.output, args.pages, args.dpi, args.force),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
