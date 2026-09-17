# -*- coding: utf-8 -*-
"""报价清单生成后的自动校验：结构 / 表头 / 数量联动 / 图片 / 体积。

用法：
    python scripts/validate.py <输出目录或 xlsx ...> [--max-mb 20]

校验项：
  1. 工作簿 3 张表：主表(首位) / 参数 Parameters / Notes 说明（顺序固定）
  2. 第 6 行表头齐全（编号/描述/区域/数量/单位/品牌/图片/单价/合价/数量依据/依据）
  3. 数量列(E)：公式是否引用「参数 Parameters」页（算量联动）、是否为数值、是否留空
  4. 合价列(J)存在 =IF(...) 公式
  5. 图片列(H)嵌入图片数
  6. 文件体积（> --max-mb 告警，需降 dpi / 改 JPEG）

输出「已算量 / 总条目」与空量告警，便于如实告知用户哪些条目仍需补数据。
"""
import argparse
import glob
import math
import os
import re
import sys

import openpyxl

EXPECTED_SHEETS = ["参数 Parameters", "Notes 说明"]
QTY_SHEET_REF = re.compile(r"参数\s*Parameters", re.I)
AMOUNT_FORMULA = re.compile(r"=IF\(\s*OR\(\s*E\d+=\"\"", re.I)


def find_xlsx(paths):
    out = []
    for p in paths:
        if os.path.isdir(p):
            out += sorted(glob.glob(os.path.join(p, "*.xlsx")))
        elif p.endswith(".xlsx"):
            out.append(p)
    return out


def validate_file(fp, max_mb):
    wb = openpyxl.load_workbook(fp)
    sheets = wb.sheetnames
    issues = []

    # 1) 三表结构
    main = sheets[0] if sheets else None
    has_params = "参数 Parameters" in sheets
    has_notes = "Notes 说明" in sheets
    if len(sheets) != 3:
        issues.append("表数=%d（期望 3）" % len(sheets))
    if not has_params:
        issues.append("缺「参数 Parameters」页")
    if not has_notes:
        issues.append("缺「Notes 说明」页")
    if sheets[:3] != [main, "参数 Parameters", "Notes 说明"]:
        issues.append("表顺序异常：%s" % sheets)

    ws = wb[main] if main else None
    total = computed = linked = number = empty = img_rows = 0
    amount_ok = 0
    if ws is None:
        issues.append("无主表")
        return {"file": fp, "issues": issues, "size_mb": size_mb(fp, max_mb)}

    # 2) 表头
    hdr = {ws.cell(row=6, column=c).value for c in range(1, 13)}
    for need in ("Qty", "Price", "Amount", "Ref.", "Image"):
        if not any(need in (h or "") for h in hdr):
            issues.append("表头缺 %s" % need)

    # 分组标题行：A:G 合并的单行，跳过（不计为数据条目）
    group_rows = set()
    for rng in ws.merged_cells.ranges:
        if rng.min_col == 1 and rng.max_col >= 7 and rng.min_row == rng.max_row:
            group_rows.add(rng.min_row)

    # 3-5) 逐数据行
    r = 7
    images = len(ws._images)
    while r <= ws.max_row:
        a = ws.cell(row=r, column=1).value
        if r in group_rows:
            r += 1
            continue
        if a in ("Total 合计", "Total") or (isinstance(a, str) and a.startswith("Total")):
            break
        if a in (None, ""):
            r += 1
            continue
        total += 1
        q = ws.cell(row=r, column=5).value
        j = ws.cell(row=r, column=10).value
        if isinstance(q, str) and q.startswith("="):
            computed += 1
            if QTY_SHEET_REF.search(q):
                linked += 1
            else:
                issues.append("R%d 数量公式未引用参数页：%s" % (r, q[:40]))
        elif isinstance(q, (int, float)):
            computed += 1
            number += 1
        else:
            empty += 1
        if isinstance(j, str) and AMOUNT_FORMULA.search(j):
            amount_ok += 1
        r += 1

    if total and amount_ok < total:
        issues.append("合价公式缺失 %d/%d 行" % (total - amount_ok, total))
    if total and empty:
        issues.append("空数量 %d/%d 行（待补参数或依据）" % (empty, total))

    return {
        "file": fp,
        "main": main,
        "items": total,
        "computed": computed,
        "linked": linked,
        "number": number,
        "empty": empty,
        "images": images,
        "size_mb": size_mb(fp, max_mb),
        "issues": issues,
    }


def size_mb(fp, max_mb):
    mb = os.path.getsize(fp) / 1024 / 1024
    return round(mb, 2), mb > max_mb


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+", help="输出目录或若干 .xlsx 文件")
    ap.add_argument("--max-mb", type=float, default=20, help="体积告警阈值（默认 20MB）")
    args = ap.parse_args()

    files = find_xlsx(args.paths)
    if not files:
        print("未找到 xlsx 文件", file=sys.stderr)
        sys.exit(2)

    grand = {"items": 0, "computed": 0, "linked": 0, "empty": 0, "images": 0}
    bad = 0
    for fp in files:
        res = validate_file(fp, args.max_mb)
        grand["items"] += res["items"]
        grand["computed"] += res["computed"]
        grand["linked"] += res.get("linked", 0)
        grand["empty"] += res["empty"]
        grand["images"] += res["images"]
        name = os.path.basename(fp)
        flag = "⚠" if (res["issues"] or res["size_mb"][1]) else "✓"
        if res["issues"] or res["size_mb"][1]:
            bad += 1
        print("\n%s %s" % (flag, name))
        print("   主表:%-22s 条目:%-4d 已算量:%-4d (联动参数:%-4d, 数值:%-3d) 空量:%-3d 图片:%-3d 体积:%sMB%s"
              % (res.get("main", "?"), res["items"], res["computed"], res.get("linked", 0),
                 res.get("number", 0), res["empty"], res["images"], res["size_mb"][0],
                 " ⚠超限" if res["size_mb"][1] else ""))
        if res["size_mb"][1]:
            print("   - 体积超限：用 generate_quotation_lists.py --size-budget-mb %.0f 重新生成（自动降质重嵌）"
                  % args.max_mb)
        for it in res["issues"]:
            print("   - 问题:", it)

    pct = (100.0 * grand["computed"] / grand["items"]) if grand["items"] else 0
    print("\n" + "=" * 70)
    print("合计：%d 个文件 | 条目 %d | 已算量 %d (%.0f%%) | 空量 %d | 图片 %d"
          % (len(files), grand["items"], grand["computed"], pct, grand["empty"], grand["images"]))
    print("结论：", "全部通过 ✅" if bad == 0 else "%d 个文件有待处理项 ⚠" % bad)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
