# -*- coding: utf-8 -*-
"""
探测 PDF 的图文布局，用于确定「哪个图片属于哪个产品」。

用法：
    python probe_pdf.py <pdf> <页码,页码,...>          # 指定页
    python probe_pdf.py <pdf> --all                    # 全部页（只列图片清单）
    python probe_pdf.py <pdf> 63 --search "Grey Carpet"  # 在指定页定位某标题的文字矩形

输出两类信息：
  * 每页图片：xref、尺寸、左上角坐标（便于按 x 排序或按 y 判断上下关系）
  * 每页文字：按行聚类的 (y, 起始x, 文本)，便于与图片 bbox 做「上下左右」配对
"""
import sys

import fitz


def page_inventory(doc, pno, search=None):
    pg = doc[pno - 1]
    imgs = [im for im in pg.get_image_info(xrefs=True) if im["bbox"][1] > 100]
    print("==== P%d  (page %d/%d)" % (pno, pno, doc.page_count))
    for im in sorted(imgs, key=lambda i: (round(i["bbox"][0]), round(i["bbox"][1]))):
        x0, y0, x1, y1 = im["bbox"]
        print("   IMG xref=%-5d %4dx%-4d  @(%4d,%4d)" %
              (im["xref"], round(x1 - x0), round(y1 - y0), round(x0), round(y0)))
    if search:
        for r in pg.search_for(search):
            print("   >> '%s' rect=(%.0f,%.0f,%.0f,%.0f)" % (search, r.x0, r.y0, r.x1, r.y1))
        return
    lines = {}
    for x0, y0, x1, y1, t, *_ in pg.get_text("words"):
        if y1 < 100 or t in ("•",):
            continue
        lines.setdefault(round(y1 / 9), []).append((round(x0), t))
    for k in sorted(lines):
        seg = sorted(lines[k])
        print("   TEXT y%3d x%3d | %s" % (k * 9, seg[0][0], " ".join(t for _, t in seg)[:60]))


def main():
    pdf = sys.argv[1]
    doc = fitz.open(pdf)
    if "--all" in sys.argv:
        for i in range(doc.page_count):
            imgs = [im for im in doc[i].get_image_info(xrefs=True) if im["bbox"][1] > 100]
            if imgs:
                print("P%-3d n=%d  %s" % (i + 1, len(imgs),
                      " ".join("%d(%dx%d)" % (im["xref"], round(im["bbox"][2] - im["bbox"][0]),
                                              round(im["bbox"][3] - im["bbox"][1])) for im in imgs)))
        return
    args = [a for a in sys.argv[2:] if not a.startswith("--")]
    search = None
    if "--search" in sys.argv:
        search = sys.argv[sys.argv.index("--search") + 1]
        args = [a for a in args if a != search]
    pages = []
    for a in args:
        if "-" in a:
            s, e = a.split("-")
            pages += list(range(int(s), int(e) + 1))
        else:
            pages.append(int(a))
    for p in pages or range(1, doc.page_count + 1):
        page_inventory(doc, p, search)


if __name__ == "__main__":
    main()
