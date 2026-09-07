# -*- coding: utf-8 -*-
"""docx 页码后处理：移除空 pgNumType；按节序给页脚 PAGE 域补 ROMAN/arabic 开关（WPS 兼容）。"""
import re, shutil, sys, zipfile, os

DOCX = sys.argv[1]
TMP = DOCX + ".tmp.zip"

with zipfile.ZipFile(DOCX, "r") as zin:
    names = zin.namelist()
    data = {n: zin.read(n) for n in names}

doc_xml = data["word/document.xml"].decode("utf-8")

# 1) 移除空 pgNumType（docx-js 在未设置 pageNumbers 的节也会输出）
doc_xml, n_removed = re.subn(r"<w:pgNumType/>", "", doc_xml)

# 2) 按文档节顺序解析 footerReference r:id
sect_prs = re.findall(r"<w:sectPr[ >].*?</w:sectPr>", doc_xml, flags=re.S)
rels_xml = data["word/_rels/document.xml.rels"].decode("utf-8")
rid_to_target = dict(re.findall(r'Id="([^"]+)"[^>]*Target="([^"]+)"', rels_xml))

footer_fmt = {}  # footer filename -> "ROMAN" | "arabic"
for sp in sect_prs:
    m_fmt = re.search(r'<w:pgNumType[^>]*w:fmt="([^"]+)"', sp)
    fmt = m_fmt.group(1) if m_fmt else None
    for rid in re.findall(r'<w:footerReference[^>]*r:id="([^"]+)"', sp):
        target = rid_to_target.get(rid, "")
        fname = "word/" + target.lstrip("/") if not target.startswith("word/") else target
        if fmt == "upperRoman":
            footer_fmt[fname] = "ROMAN"
        elif fmt in ("decimal", None):
            footer_fmt.setdefault(fname, "arabic")

patched = []
for fname, fmt in footer_fmt.items():
    if fname not in data:
        continue
    xml = data[fname].decode("utf-8")
    switch = "ROMAN" if fmt == "ROMAN" else "arabic"
    new_xml, n = re.subn(
        r"(<w:instrText[^>]*>)\s*PAGE\s*(</w:instrText>)",
        r"\1 PAGE \\* " + switch + r" \\* MERGEFORMAT \2",
        xml,
    )
    if n:
        data[fname] = new_xml.encode("utf-8")
        patched.append((fname, switch, n))

data["word/document.xml"] = doc_xml.encode("utf-8")

with zipfile.ZipFile(TMP, "w", zipfile.ZIP_DEFLATED) as zout:
    for n in names:
        zout.writestr(n, data[n])
shutil.move(TMP, DOCX)
print(f"pgNumType removed: {n_removed}; footers patched: {patched}")
