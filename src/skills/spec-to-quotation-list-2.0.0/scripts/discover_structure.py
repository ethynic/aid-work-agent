# -*- coding: utf-8 -*-
"""
discover_structure.py —— 扫描原始文档，提出「空间」与「大类」候选清单（供用户确认/调整）。

这是 spec-to-quotation-list 技能**分阶段交互工作流的第 1、2 阶段**的引擎：
  - 先扫描 PDF，动态发现文档里有哪些「空间」（房间/区域），并给出证据（目录层级 + 页码）；
  - 一级章节作为「主空间」候选，其下二级/三级条目作为「可拆分细项」，方便用户先确认粗粒度、
    再按需把某个主空间拆成更细的真实房间；
  - 再结合文档关键词与已有 items.json，提出「大类」（外立面/硬装材料/家具软装/机电设备/艺术品…）候选；
  - 输出 structure_proposal.json + 人类可读提案，交给用户确认或调整（重命名/合并/拆分/增删）。

注意：本脚本**只做发现与提案，不生成任何 Excel**。确认后的结构由 generate_quotation_lists.py --structure 落实。

用法：
  python discover_structure.py --pdf <spec.pdf> [--items items.json] [--json structure_proposal.json]

输出 JSON 结构：
  {
    "project_name": "...",
    "doc_type": "spec_book|schedule_driven|drawing_set",
    "spaces": [ {"name": "...", "evidence": "...", "likely_space": true/false, "hint": "...",
                 "item_count": N, "real_count": M, "subspaces": ["细项1", "细项2", ...]}, ... ],
    "themes": [ {"name": "...", "keep": true/false, "evidence": "..."}, ... ],
    "matrix": [ {"space": "...", "theme": "...", "item_count": N}, ... ]
  }
"""
import argparse
import importlib.util
import json
import os
import re

import fitz

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("ap", os.path.join(HERE, "analyze_pdf.py"))
ap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ap)

# 明显「非空间」的章节（品牌概述/安全/技术/供应商/标准/附录/要求等，通常只进 Notes，不产生报价文件）
NON_SPACE_PAT = re.compile(
    r"brand|overview|introduction|safety|security|technology|supplier|standard|material\b|"
    r"finish|appendix|addendum|requirement|specification|environment|sustainab|quality|"
    r"warranty|maintenance|glossary|index|term|acronym|"
    r"品牌|概述|简介|安全|安防|技术|供应商|标准|材料|饰面|附录|补充|要求|环境|可持续|质量|"
    r"保修|维护|索引|术语|缩略", re.I)

# 大类调色板：name -> 文档关键词（命中即建议保留）
THEME_PALETTE = [
    ("外立面与室外",
     r"facade|façade|exterior|landscape|parking|entrance|signage|curtain\s*wall|"
     r"立面|室外|景观|停车|入口|标识|幕墙"),
    ("硬装材料",
     r"\bfinish|material|wall|floor|ceiling|tile|stone|paint|plaster|"
     r"硬装|饰面|材料|墙|地 ?面|天花|瓷砖|石材|涂料"),
    ("家具软装",
     r"furniture|fixture|soft|decor|upholster|"
     r"家具|软装|陈设|布艺"),
    ("机电与设备",
     r"\bmep\b|mechanical|electrical|plumbing|hvac|generator|equipment|lift|escalator|"
     r"机电|设备|空调|发电机|电梯|给排水|弱电|强电"),
    ("艺术品",
     r"art\s*work|art\s*piece|painting|sculpture|mural|gallery|"
     r"艺术品|挂画|画作|雕塑|艺术陈设"),
]


def clean(title):
    return re.sub(r"^\d+(\.\d+)*[\s\.]+", "", title).strip()


def classify_space(title):
    if NON_SPACE_PAT.search(title):
        return False, "非空间（通常只进 Notes 说明）"
    return True, "候选空间"


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--pdf", required=True)
    a.add_argument("--items", help="可选：已有 items.json，用于补充真实条目数与 (空间×大类) 矩阵")
    a.add_argument("--json", dest="json_out", help="提案 JSON 输出路径")
    args = a.parse_args()

    doc = fitz.open(args.pdf)
    name, _ = ap.guess_project_name(doc)
    toc, toc_src = ap.get_toc(doc)
    schedule = ap.detect_schedule(doc)
    doc_type, _ = ap.detect_doc_type(doc, schedule)

    # ---------- 1) 构建目录树：一级章节 = 主空间候选，其下二/三级 = 可拆分细项 ----------
    spaces_map = {}      # name -> dict
    order = []
    cur_top = None
    for t in toc:
        nm = clean(t["title"])
        if not nm:
            continue
        if t["level"] == 1:
            cur_top = nm
            if nm not in spaces_map:
                likely, hint = classify_space(t["title"])
                spaces_map[nm] = {
                    "name": nm,
                    "evidence": "TOC L1 «%s» P%d" % (t["title"], t["page"]),
                    "likely_space": likely,
                    "hint": hint,
                    "subspaces": [],
                }
                order.append(nm)
        else:
            if cur_top and nm not in spaces_map.get(cur_top, {}).get("subspaces", []):
                spaces_map[cur_top].setdefault("subspaces", []).append(nm)

    # ---------- 2) 若给了 items.json，用真实空间 enrich / 补空间 ----------
    item_spaces = {}
    item_themes = {}
    matrix = {}
    data = None
    if args.items and os.path.exists(args.items):
        data = json.load(open(args.items, encoding="utf-8"))
        for it in data.get("items", []):
            sp = it.get("space")
            th = it.get("theme")
            if sp:
                d = item_spaces.setdefault(sp, {"count": 0, "real": 0})
                d["count"] += 1
                if not it.get("tbc"):
                    d["real"] += 1
                if sp not in spaces_map:
                    spaces_map[sp] = {"name": sp, "evidence": "来自 items.json",
                                      "likely_space": True, "hint": "候选空间", "subspaces": []}
                    order.append(sp)
            if th:
                item_themes[th] = item_themes.get(th, 0) + 1
            if sp and th:
                matrix[(sp, th)] = matrix.get((sp, th), 0) + 1
        for nm, c in spaces_map.items():
            if nm in item_spaces:
                c["item_count"] = item_spaces[nm]["count"]
                c["real_count"] = item_spaces[nm]["real"]

    spaces = [spaces_map[nm] for nm in order]

    # ---------- 3) 候选大类：调色板 + 关键词/items 命中 ----------
    text_pool = json.dumps(data, ensure_ascii=False) if data else ""
    if not text_pool:
        for p in range(1, min(31, doc.page_count + 1)):
            text_pool += doc[p - 1].get_text()
    themes = []
    for th_name, pat in THEME_PALETTE:
        present = bool(re.search(pat, text_pool, re.I))
        in_items = th_name in item_themes
        keep = bool(present or in_items)
        if present:
            ev = "文档关键词命中"
        elif in_items:
            ev = "items.json 已含此大类（%d 条）" % item_themes[th_name]
        else:
            ev = "文档未明显提及，建议暂不包含"
        themes.append({"name": th_name, "keep": keep, "evidence": ev})

    matrix_list = [{"space": k[0], "theme": k[1], "item_count": v}
                   for k, v in sorted(matrix.items())]

    result = {
        "project_name": name,
        "doc_type": doc_type,
        "toc_source": toc_src,
        "spaces": spaces,
        "themes": themes,
        "matrix": matrix_list,
    }

    # ---------- 打印可读提案 ----------
    print("=" * 78)
    print("文档：%s  （%d 页，类型 %s）" % (os.path.basename(args.pdf), doc.page_count, doc_type))
    print("项目名（候选）：%s" % name)
    print("=" * 78)
    print("\n【阶段1】候选「主空间」（先确认粗粒度；每个下面是可拆分细项）—— 请用户确认/调整：")
    idx = 0
    for c in spaces:
        idx += 1
        tag = "空间" if c["likely_space"] else "非空间"
        cnt = ("  条目%d/%d" % (c.get("real_count", 0), c.get("item_count", 0))) if "item_count" in c else ""
        print("  %2d. %-30s  [%s]  %s%s" % (idx, c["name"], tag, c["evidence"], cnt))
        if c.get("subspaces"):
            print("        └ 可拆分细项: " + " / ".join(c["subspaces"]))
    non = [c["name"] for c in spaces if not c["likely_space"]]
    if non:
        print("\n  （以上标注「非空间」的通常只进 Notes 说明，不生成文件；如确要报价可保留）")
    print("\n【阶段2】候选大类（请用户确认或调整，✓=建议保留 ✗=建议暂不含）：")
    for th in themes:
        print("  %s %-14s  %s" % ("✓" if th["keep"] else "✗", th["name"], th["evidence"]))
    if matrix_list:
        print("\n【参考】已有 items.json 的空间×大类 矩阵：")
        for m in matrix_list:
            print("      %-24s × %-12s  %d 条" % (m["space"], m["theme"], m["item_count"]))
    print("\n>>> 用户调整方式：重命名 / 合并（细项并入主空间）/ 拆分（主空间展开为细项）/ 增删。")
    print(">>> 两个确认门都通过后，写 confirmed_structure.json，再编/修 items.json 并运行")
    print(">>> generate_quotation_lists.py --structure confirmed_structure.json。")
    print("=" * 78)

    if args.json_out:
        json.dump(result, open(args.json_out, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print("\n已写入提案：%s" % args.json_out)


if __name__ == "__main__":
    main()
