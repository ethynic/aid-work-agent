# -*- coding: utf-8 -*-
"""
分析任意设计规范 PDF，输出「项目名 / 章节结构 / 可配图资源 / 建议拆分方案」。

用法：
    python analyze_pdf.py <pdf> [--json out.json] [--min-images 1]

输出内容：
  * project_name  —— 从文档元信息、封面文字、以及全书最常出现的页眉行推断
  * toc           —— 目录（优先用 PDF 书签；无书签时从目录页抓「标题 + 页码」行）
  * sections      —— 顶层章节：起始页、结束页、图片数、可提炼的空间业态
  * suggested_files —— 建议拆成的文件（顶层章节 → 一个文件；附录合并为一个「材料标准」文件）
"""
import argparse
import json
import os
import re

import fitz


def guess_project_name(doc):
    """优先取 PDF 元信息标题，其次封面大字，最后取全书最常见的页眉行"""
    meta_title = (doc.metadata or {}).get("title", "").strip()
    if meta_title and len(meta_title) > 3:
        return meta_title, "metadata"

    # 封面：第 1-2 页里最长的那几行
    cover_lines = []
    for i in range(min(2, doc.page_count)):
        for line in doc[i].get_text().split("\n"):
            line = line.strip()
            if len(line) > 8:
                cover_lines.append(line)
    if cover_lines:
        cover_lines.sort(key=len, reverse=True)
        return cover_lines[0], "cover"

    # 页眉：统计每页首行出现次数
    heads = {}
    for i in range(doc.page_count):
        txt = doc[i].get_text().strip().split("\n")
        if txt:
            h = txt[0].strip()
            if len(h) > 10:
                heads[h] = heads.get(h, 0) + 1
    if heads:
        best = max(heads.items(), key=lambda kv: kv[1])
        if best[1] >= max(3, doc.page_count * 0.2):
            return best[0], "running-header"
    return os.path.splitext(os.path.basename(doc.name))[0], "filename"


PLACEHOLDER = re.compile(r"^(slide|page|sheet|第\s*\d+\s*页|untitled)\s*\d*$", re.I)
HEADING = re.compile(r"^(\d{1,2})(?:\.(\d{1,2}))?[\s\.]+(\S.*)$")


def get_toc(doc):
    """章节识别三级回退：PDF 书签 → 目录页「标题+页码」→ 正文页眉里反复出现的章节名"""
    toc = doc.get_toc()
    if toc:
        clean = [(lv, t, p) for lv, t, p in toc if not PLACEHOLDER.match(t.strip())]
        if clean:
            return [{"level": lv, "title": t, "page": p} for lv, t, p in clean], "bookmarks"

    # 目录页：形如 "01 Brand Overview – 6" / "4.3 Business Center ... 38" / "Title.....12"
    entries = []
    pat = re.compile(r"^(.{3,70}?)\s*[.·•]{2,}|–|—|-|\s+(\d{1,3})\s*$")
    for i in range(min(12, doc.page_count)):
        for line in doc[i].get_text().split("\n"):
            s = line.strip()
            m = re.match(r"^(.{3,70}?)[\s\.·]{2,}(\d{1,3})$", s) or \
                re.match(r"^(.{3,70}?)\s*[–—-]\s*(\d{1,3}(?:[-,–]\d{1,3})?)\s*$", s)
            if m:
                page = re.match(r"\d+", m.group(2).replace(",", ""))
                if page:
                    entries.append({"level": 1, "title": m.group(1).strip(" .-"),
                                    "page": int(page.group(0))})
    for e in entries:
        e["level"] = 2 if re.match(r"^\d{1,2}\.\d{1,2}", e["title"]) else 1
    if len(entries) >= 3:
        return dedup(entries), "toc-page"

    # 正文页眉：统计每个「数字 + 标题」行出现在哪些页，反复出现者即章节名
    freq = {}
    for p in range(1, doc.page_count + 1):
        seen = set()
        for line in doc[p - 1].get_text().split("\n"):
            s = line.strip()
            if not (4 <= len(s) <= 60):
                continue
            m = HEADING.match(s)
            if not m:
                continue
            if m.group(3) and re.match(r"^\d", m.group(3)):
                continue          # 排除 "2026 6" 之类
            if s in seen:
                continue
            seen.add(s)
            freq.setdefault(s, []).append(p)
    need = 2 if doc.page_count > 12 else 1
    heads = [{"level": 1, "title": t, "page": min(pages)}
             for t, pages in freq.items() if len(pages) >= need]
    # 只保留顶层（编号为 X 而非 X.Y）
    top = [h for h in heads if not HEADING.match(h["title"]).group(2)]
    top = dedup(top) or dedup(heads)
    if top:
        return top, "running-headings"

    # 兜底：按字号识别标题——每页最大字号的那行通常就是该页标题
    out = []
    for p in range(1, doc.page_count + 1):
        try:
            spans = [s for b in doc[p - 1].get_text("dict")["blocks"]
                     if b.get("type") == 0 for l in b["lines"] for s in l["spans"]]
        except Exception:
            continue
        spans = [s for s in spans if s["text"].strip()]
        if not spans:
            continue
        mx = max(s["size"] for s in spans)
        for s in spans:
            t = s["text"].strip()
            if len(t) < 2 or len(t) > 60:
                continue
            if s["size"] >= mx * 0.92:
                out.append({"level": 1, "title": t, "page": p})
                break
    return dedup(out), "font-size"


def dedup(entries):
    out, seen = [], set()
    for e in sorted(entries, key=lambda e: e["page"]):
        if e["title"] in seen:
            continue
        seen.add(e["title"])
        out.append(e)
    return out


ROLE_RULES = [
    ("01", "客房", r"guest\s*room|happyroom|bed\s*room|bedroom|bathroom|room\s*amenit|suite|客房|卧室|卫浴|浴室|卫生间|标准层"),
    ("02", "公共区域", r"interior\s*design|lobb|recept|corridor|circulation|elevator|lift|meeting|function|business|restroom|toilet|gym|fitness|laundry|conference|marketing|work\s*space|office|公共|大堂|走廊|电梯|会议|多功能|健身|洗衣|公卫|室内设计|办公"),
    ("03", "餐饮", r"dining|restaurant|bar\b|cafe|café|kitchen|f&b|banquet|餐饮|餐厅|酒吧|厨房|宴会"),
    ("04", "外立面及室外", r"facade|façade|exterior|landscape|parking|pool|outdoor|entrance|signage|curtain\s*wall|立面|室外|景观|停车|泳池|入口|标识|幕墙"),
    ("05", "后勤与机电", r"back\s*of\s*house|\bboh\b|mep|mechanical|electrical|plumbing|technology|safety|security|housekeeping|后勤|机电|给排水|弱电|安防|消x"),
    ("06", "材料标准与供应商", r"appendix|material|finish|supplier|standard|specification|附录|材料|饰面|供应商|标准"),
    ("10", "住宅户型", r"apartment|studio|residential|unit\s*type|typology|户型|公寓|套房|住宅|condo"),
    ("90", "品牌概述", r"brand|overview|introduction|品牌|概述|简介"),
]


def classify(title):
    for code, name, pat in ROLE_RULES:
        if re.search(pat, title, re.I):
            return (code, name)
    return ("50", "其他空间")


def section_image_stats(doc, start, end):
    n = 0
    pages = []
    for p in range(start, end + 1):
        imgs = [im for im in doc[p - 1].get_image_info(xrefs=True) if im["bbox"][1] > 100]
        if imgs:
            n += len(imgs)
            pages.append(p)
    return n, pages


def detect_doc_type(doc, schedule=None):
    """区分文档处理范式，决定后续拆分与算量策略：

    - schedule_driven（套数表驱动）：存在「公寓套数一览表 / 户型清单」页，产品是少量图例，
      数量来自套数 × 每套件数
    - drawing_set   （图纸集）：文字稀疏、大量矢量绘制，无栅格产品图、也无套数表
    - spec_book      （FF&E 规范书）：文字多、含产品图与品牌表，数量可从正文读出
    """
    if schedule:
        return "schedule_driven", {"schedule_pages": [c["page"] for c in schedule[:5]]}
    total_chars = 0
    vector_pages = 0
    for p in range(1, doc.page_count + 1):
        pg = doc[p - 1]
        txt = pg.get_text().strip()
        total_chars += len(txt)
        draws = pg.get_drawings()
        imgs = [im for im in pg.get_image_info(xrefs=True) if im["bbox"][1] > 100]
        if draws and len(imgs) <= 2 and len(txt) < 300:
            vector_pages += 1
    avg = total_chars / max(1, doc.page_count)
    if vector_pages >= max(3, doc.page_count * 0.3) and avg < 400:
        return "drawing_set", {"avg_chars_per_page": round(avg, 1), "vector_pages": vector_pages}
    return "spec_book", {"avg_chars_per_page": round(avg, 1), "vector_pages": vector_pages}


UNITCODE_PAT = re.compile(r"\d{2}[A-Za-z]?\d?_\d+")   # 公寓单元编码，如 02A1_1
RESIDENTIAL_PAT = re.compile(r"(studio|\d[-\s]?bedroom|\d[-\s]?bed\b|apartment|typology|户型|公寓|套房|residential|unit\s*type)", re.I)
PIECE_PAT = re.compile(r"(pcs|\bks\b|\d+\s*pcs)", re.I)   # 件套数（非面积，面积在规范书里也常见）


def detect_schedule(doc):
    """定位「公寓套数一览表 / 户型清单」页（套数表驱动型文档的核心）。

    强信号：单页出现 ≥5 个单元编码（如公寓一览表，每页数十个单元号）；
    弱信号：同时出现住宅户型关键词（studio / N-bed / 公寓 / 户型 / 套房…）与
            **件套数**（pcs/ks，非面积）且为表格页。
    规范书类文档单元编码为 0、且不会同时出现住宅户型词 + 件套数，不会误报。
    返回命中页候选（按单元编码出现次数排序），含前几行预览，便于直接打开核对。
    """
    cand = []
    for p in range(1, doc.page_count + 1):
        txt = doc[p - 1].get_text()
        codes = UNITCODE_PAT.findall(txt)
        strong = len(codes) >= 5
        weak = RESIDENTIAL_PAT.search(txt) and PIECE_PAT.search(txt) and txt.count("\n") > 10
        if strong or weak:
            lines = [l.strip() for l in txt.split("\n") if l.strip()]
            cand.append({"page": p, "unitcode_hits": len(codes),
                         "preview": " | ".join(lines[:8])[:300]})
    cand.sort(key=lambda c: -c["unitcode_hits"])
    return cand[:10]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--json", dest="json_out")
    ap.add_argument("--min-images", type=int, default=1)
    args = ap.parse_args()

    doc = fitz.open(args.pdf)
    name, src = guess_project_name(doc)
    toc, toc_src = get_toc(doc)
    schedule = detect_schedule(doc)            # 无条件执行：定位公寓套数一览表/图例
    doc_type, doc_meta = detect_doc_type(doc, schedule)

    top = [t for t in toc if t["level"] == 1] or toc
    if not top:   # 完全识别不出章节时，整本作为一个区间
        top = [{"level": 1, "title": "Full document / 全部内容", "page": 1}]
    sections = []
    for i, t in enumerate(top):
        start = min(max(1, t["page"]), doc.page_count)
        end = (top[i + 1]["page"] - 1) if i + 1 < len(top) else doc.page_count
        end = min(max(end, start), doc.page_count)
        n_img, pages = section_image_stats(doc, start, end)
        sections.append({
            "title": t["title"],
            "start_page": start,
            "end_page": end,
            "images": n_img,
            "pages_with_images": pages[:20],
        })

    # 建议拆分：按业态归类顶层章节，相同业态合并为一个文件
    files, by_role = [], {}
    for s in sections:
        role = classify(s["title"])
        by_role.setdefault(role, []).append(s)
    for role, secs in by_role.items():
        pages = [min(x["start_page"] for x in secs), max(x["end_page"] for x in secs)]
        files.append({
            "suggested_filename": "%s_%s报价清单.xlsx" % (role[0], role[1]),
            "role": role[1],
            "from_sections": [x["title"] for x in secs],
            "pages": pages,
            "images": sum(x["images"] for x in secs),
            "has_images": any(x["images"] > 0 for x in secs),
        })
    files.sort(key=lambda f: f["pages"][0])

    # 套数表驱动 / 图纸集：按业态的章节拆分无效，改提示「按户型/区域拆分」
    if doc_type == "schedule_driven":
        sp = schedule[0]["page"] if schedule else None
        files = [{
            "suggested_filename": "按户型_单元拆分_报价清单.xlsx",
            "role": "住宅户型/单元（套数表驱动）",
            "from_sections": [s["title"] for s in sections],
            "pages": [1, doc.page_count],
            "images": sum(x["images"] for x in sections),
            "has_images": any(x["images"] > 0 for x in sections),
            "schedule_page": sp,
            "note": ("此文档为套数表驱动型：产品多为少量图例，数量由「套数一览表」"
                     "（疑似 P.%s）驱动。请读取该页套数，按户型/单元拆分文件，"
                     "套数写入参数页，条目数量用 expr 公式 = 套数 × 每套件数；"
                     "矢量平面图用 img=[\"r\",页号] 栅格化，并设 disable_auto_image=true。"
                     % sp) if sp else "请读取套数一览表后按户型拆分。",
        }]
    elif doc_type == "drawing_set":
        files = [{
            "suggested_filename": "按区域_楼层拆分_报价清单.xlsx",
            "role": "区域/单元（图纸集驱动）",
            "from_sections": [s["title"] for s in sections],
            "pages": [1, doc.page_count],
            "images": sum(x["images"] for x in sections),
            "has_images": any(x["images"] > 0 for x in sections),
            "note": ("此文档为纯矢量图纸集：文字稀疏、无套数表，产品多为图纸标注。"
                     "请按区域/楼层/单元拆分（可独立报价的交付包），数量多来自面积/比例/固定参数；"
                     "矢量平面图用 img=[\"r\",页号] 栅格化，并设 disable_auto_image=true。"),
        }]

    result = {
        "pdf": args.pdf,
        "page_count": doc.page_count,
        "project_name": name,
        "project_name_source": src,
        "doc_type": doc_type,
        "doc_type_meta": doc_meta,
        "schedule_hint": schedule,
        "toc_source": toc_src,
        "sections": sections,
        "suggested_files": files,
    }

    print("PDF      :", os.path.basename(args.pdf), "(%d 页)" % doc.page_count)
    print("项目名   :", name, "[来源:%s]" % src)
    print("文档类型 :", doc_type, doc_meta,
          "  → 处理范式:", "套数表驱动（按户型拆分，套数进参数页）" if doc_type == "schedule_driven"
          else "纯矢量图纸集（按区域/楼层拆分，栅格化取图）" if doc_type == "drawing_set"
          else "FF&E 规范书（按业态拆分，数量从正文读）")
    if schedule:
        print("套数一览表候选页:")
        for c in schedule[:5]:
            print("    P%-3d  单元编码命中 %d  | %s" % (c["page"], c["unitcode_hits"], c["preview"][:80]))
    print("目录来源 :", toc_src, " 顶层章节 %d 个" % len(sections))
    print("-" * 78)
    for s in sections:
        print("  P%-3d - P%-3d  图%-3d  %s" % (s["start_page"], s["end_page"], s["images"], s["title"][:52]))
    print("-" * 78)
    print("建议拆分：")
    for f in files:
        print("  %-46s %s  图:%s" % (f["suggested_filename"], f["role"], "有" if f["has_images"] else "无"))
        if f.get("note"):
            print("      ↳", f["note"][:150])

    if args.json_out:
        json.dump(result, open(args.json_out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("\n已写入", args.json_out)


if __name__ == "__main__":
    main()
