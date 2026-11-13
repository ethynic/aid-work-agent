#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把任意格式报价单抽取为统一结构 quotes.json（含源单元格定位，供回写标色）。

支持：
  .xlsx/.xlsm  多 sheet、表头语义识别（列序/表头文案不限）、合并单元格左上角值
  .csv         utf-8-sig / gbk 自动回退，同上表头识别
  .docx        表格优先，段落行兜底
  .pdf         文本层行兜底（扫描件请由 agent 视觉读取后手工写 JSON）
  .json/.json  已由 agent 手工构造的条目，直接合并（支持 {"items":[...]} 或 [...]）
  .txt         纯文本行兜底
  图片(.png/.jpg) 无法由脚本解析：打印提示，由 agent 用多模态读取后写手工 JSON

用法：
  python extract_quotes.py <报价单或目录> [<更多路径>] --out quotes.json \
      [--supplier-map '{"A.xlsx":"甲公司"}'] [--supplier 统一供应商名] \
      [--source-type upload|library|web] [--default-currency CNY] \
      [--default-tax-included true] [--default-tax-rate 0.13] [--merge]
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qc_common import (  # noqa: E402
    detect_currency, dump_json, load_json, norm_text, norm_uom, s, to_float, today,
)

# 表头角色 -> 候选关键词（按最长匹配取胜，避免"报价日期"被误判为"单价"）
ROLE_KEYWORDS = {
    "code": ["物料编码", "物料编号", "商品编码", "产品编号", "项目编号", "编号", "序号", "项次", "料号", "品号",
             "sku", "item no", "item code", "item", "code", "no."],
    "name": ["产品名称", "商品名称", "物料名称", "设备名称", "品名", "名称", "产品", "物料", "项目描述", "描述",
             "description", "product name", "product", "goods", "item name"],
    "spec": ["规格型号", "技术参数", "规格及型号", "规格", "型号", "参数", "specification", "spec", "model"],
    "brand": ["品牌", "生产厂家", "制造商", "生产厂商", "厂家", "brand", "manufacturer"],
    "qty": ["采购数量", "数量", "工程量", "用量", "qty", "quantity", "qty."],
    "uom": ["计量单位", "单位", "unit", "uom"],
    "price": ["含税单价", "未税单价", "不含税单价", "综合单价", "单价(元)", "单价", "报价", "unit price", "price", "rate"],
    "total": ["价税合计", "含税金额", "合计金额", "总金额", "金额", "总价", "小计", "total amount", "amount", "total"],
    "currency": ["币种", "货币", "currency"],
    "tax": ["税率", "含税", "税", "tax", "vat"],
    "note": ["备注", "说明", "注释", "note", "remark"],
    "date": ["报价日期", "日期", "date"],
}
_ROLE_PAIRS = sorted(
    [(role, kw) for role, kws in ROLE_KEYWORDS.items() for kw in kws],
    key=lambda x: -len(x[1]),
)
SKIP_ROW_RE = re.compile(
    r"(合计|小计|总计|总价|subtotal|grand total|备注|说明|报价单位|供应商|联系人|电话|开户行|税号|盖章|签字)",
    re.I,
)
SUPPORTED = (".xlsx", ".xlsm", ".csv", ".docx", ".pdf", ".json", ".txt")


def _best_role(header: str):
    """返回 (角色, 命中关键词长度)。长度用于全局择优，避免同角色被先出现的弱关键词抢走。"""
    from qc_common import norm_text
    h = norm_text(header)
    if not h:
        return None, 0
    best_role, best_len = None, 0
    for role, kw in _ROLE_PAIRS:
        k = norm_text(kw)
        if k and k in h and len(k) > best_len:
            best_role, best_len = role, len(k)
    return best_role, best_len


def detect_header(rows, max_scan=40):
    """返回 (表头行索引 0-based, {role: 列索引 0-based})，选命中角色最多的一行。

    同一角色的多个候选列取「命中关键词最长」的那个（而非列序先到先得），
    否则「序号」会抢走 code 角色、「金额」会抢走 price 角色。
    """
    best, best_score = None, 0
    for i, row in enumerate(rows[:max_scan]):
        role_col = {}
        role_len = {}
        for c, val in enumerate(row):
            role, klen = _best_role(s(val))
            if role and klen > role_len.get(role, 0):
                role_col[role], role_len[role] = c, klen
        has_price = "price" in role_col or "total" in role_col
        has_name = "name" in role_col or "code" in role_col
        score = len(role_col)
        if has_price and has_name and score > best_score:
            best, best_score = (i, role_col), score
    return best


def _cell(row, col):
    return s(row[col]) if col is not None and col < len(row) else ""


def _tax_included(price_header, tax_cell, default=True):
    t = (price_header + " " + tax_cell).lower()
    if any(k in t for k in ("未税", "不含税", "税前", "excl", "net price")):
        return False
    if any(k in t for k in ("含税", "税价", "incl", "gross")):
        return True
    return default


def _tax_rate(tax_cell, default=0.13):
    v = to_float(tax_cell)
    if v is None:
        return default
    return v / 100 if v > 1 else v


def make_item(*, ctx, name, spec, code, brand, qty, uom, price, price_raw,
              currency, tax_included, tax_rate, note, source_date, locator, warnings,
              amount_reported=None, amount_tax_included=None, quote_status="quoted", quote_note=""):
    """构造一条报价行。price 为 None 时产出 quote_status=no_quote 的占位行。

    「未报出价格」也是报价单的重要信息（图纸未确认、面议、暂不报价），
    必须保留成条目，否则该品项会静默消失，两次执行的品项集合就不一致。
    """
    std_uom, uom_known = norm_uom(uom)
    item = {
        "item_id": "",
        "supplier": ctx["supplier"],
        "source_type": ctx["source_type"],
        "source_file": os.path.basename(ctx["file"]),
        "source_ref": ctx.get("source_ref", ""),
        "source_date": source_date or ctx.get("source_date", "") or today(),
        "code": code,
        "name": name or code,
        "spec": spec,
        "brand": brand,
        "qty": qty,
        "uom": uom,
        "qty_uom": uom,
        "standard_uom": std_uom,
        "uom_known": uom_known,
        "unit_price": round(price, 4) if price is not None else None,
        "price_raw": price_raw,
        "currency": currency,
        "tax_included": tax_included,
        "tax_rate": tax_rate,
        "unit_multiplier": 1.0,
        "amount_reported": amount_reported,
        "amount_tax_included": amount_tax_included,
        "quote_status": quote_status,
        "quote_note": quote_note,
        "note": note,
        "match_key": "",
        "match_confidence": 1.0,
        "locator": locator,
    }
    if quote_status == "no_quote":
        return item if (name or code) else None
    if price is None or price <= 0:
        return None
    if not name:
        warnings.append(f"{ctx['source_file']}: 有价格但品名为空，已用编号兜底，请复核")
    return item


def parse_table(rows, ctx, args, warnings, sheet="", table=None):
    """表格式解析（xlsx / csv / docx / pdf 表格共用）。"""
    det = detect_header(rows)
    if not det:
        return []
    hrow, role = det
    items = []
    for r in range(hrow + 1, len(rows)):
        row = rows[r]
        name = _cell(row, role.get("name"))
        code = _cell(row, role.get("code"))
        price_raw = _cell(row, role.get("price")) if "price" in role else ""
        price = to_float(price_raw)
        total_raw = _cell(row, role.get("total")) if "total" in role else ""
        amount_reported = to_float(total_raw)
        qty = to_float(_cell(row, role.get("qty")))
        note = _cell(row, role.get("note"))
        if price is None and amount_reported and qty:
            price = round(amount_reported / qty, 4)
            warnings.append(f"{ctx['file']} 第{r+1}行无单价，已由金额/数量推算")
        if not name and not code:
            continue
        if SKIP_ROW_RE.search(name) or SKIP_ROW_RE.search(code):
            continue
        price_header = _cell(rows[hrow], role.get("price")) if "price" in role else ""
        total_header = _cell(rows[hrow], role.get("total")) if "total" in role else ""
        tax_cell = _cell(row, role.get("tax"))
        cur = detect_currency(_cell(row, role.get("currency")) or price_raw, args.default_currency)
        price_col = role.get("price")
        if price_col is None:
            price_col = role.get("total")
        loc = {
            "kind": ctx["kind"], "file": ctx["file"], "sheet": sheet,
            "row": r + 1,
            "price_col": price_col + 1 if price_col is not None else None,
            "price_text": price_raw or total_raw,
        }
        if table is not None:
            loc["table"] = table
        # 无价但有品名的行 → 未报出价格（如「图纸未确认」「面议」），保留为占位条目
        no_quote = price is None or price <= 0
        if no_quote and not (name or code):
            continue
        it = make_item(
            ctx=ctx, name=name, spec=_cell(row, role.get("spec")), code=code,
            brand=_cell(row, role.get("brand")), qty=qty, uom=_cell(row, role.get("uom")),
            price=price, price_raw=price_raw or total_raw, currency=cur,
            tax_included=_tax_included(price_header, tax_cell, args.default_tax_included),
            tax_rate=_tax_rate(tax_cell, args.default_tax_rate),
            note=note, source_date=_cell(row, role.get("date")), locator=loc, warnings=warnings,
            amount_reported=amount_reported,
            amount_tax_included=_tax_included(total_header, tax_cell, args.default_tax_included)
            if total_header else None,
            quote_status="no_quote" if no_quote else "quoted",
            quote_note=(note or price_raw or "源单未报出价格") if no_quote else "",
        )
        if it:
            items.append(it)
    return items


def parse_xlsx(path, ctx, args, warnings):
    from openpyxl import load_workbook
    ctx = dict(ctx, kind="xlsx")
    wb = load_workbook(path, data_only=True)
    out = []
    for ws in wb.worksheets:
        rows = [[c.value for c in row] for row in ws.iter_rows()]
        out += parse_table(rows, ctx, args, warnings, sheet=ws.title)
    # 报价单编号（表头信息区）作为来源依据，便于回溯与留痕
    if not ctx.get("source_ref"):
        ref = ""
        for ws in wb.worksheets:
            for row in ws.iter_rows(max_row=14, max_col=14):
                cells = list(row)
                for i, c in enumerate(cells):
                    if c.value and "报价单编号" in str(c.value):
                        # 编号可能在同行右侧，也可能在下一行（合并单元格纵向布局）
                        for nc in cells[i + 1:i + 3]:
                            if nc.value and str(nc.value).strip():
                                ref = str(nc.value).strip()
                                break
                        if not ref and i + 1 < len(cells) and cells[i + 1].value:
                            ref = str(cells[i + 1].value).strip()
                        break
                if ref:
                    break
            if ref:
                break
        if ref:
            for it in out:
                it["source_ref"] = it.get("source_ref") or ref
    wb.close()
    return out


def parse_csv(path, ctx, args, warnings):
    ctx = dict(ctx, kind="csv")
    raw = None
    for enc in ("utf-8-sig", "gbk", "utf-8"):
        try:
            with open(path, "r", encoding=enc, newline="") as f:
                raw = list(csv.reader(f))
            break
        except UnicodeDecodeError:
            continue
    if raw is None:
        warnings.append(f"{path}: 编码无法识别")
        return []
    return parse_table(raw, ctx, args, warnings, sheet="csv")


def parse_docx(path, ctx, args, warnings):
    try:
        from docx import Document
    except ImportError:
        warnings.append(f"{path}: 缺少 python-docx，跳过 DOCX 解析（pip install python-docx）")
        return []
    ctx = dict(ctx, kind="docx")
    doc = Document(path)
    out = []
    for ti, tbl in enumerate(doc.tables):
        rows = [[c.text for c in row.cells] for row in tbl.rows]
        got = parse_table(rows, ctx, args, warnings, table=ti)
        if got:
            out += got
        else:
            # 报价单通常含「表头信息表 / 报价明细表 / 合计汇总表」多张表。
            # 未识别到表头是信息表或汇总表的正常现象，不应作为告警噪音：
            # 只有当一张表同时出现「物料/规格」和「单价」字样（=像报价明细）时才提示人工介入。
            head_txt = norm_text(" ".join(s(v) for r in rows[:2] for v in r))
            looks_like_quote = (
                any(k in head_txt for k in ("物料", "规格", "品名"))
                and any(k in head_txt for k in ("单价", "报价"))
            )
            if looks_like_quote:
                warnings.append(
                    f"{os.path.basename(path)} [表{ti}] 疑似报价明细表但未识别到表头，"
                    "请手工补 spec/uom 或改用 JSON 构造")
    if not out:
        out = parse_text_lines("\n".join(p.text for p in doc.paragraphs), ctx, args, warnings)
    return out


def parse_text_lines(text, ctx, args, warnings, pages=None):
    """无表格结构的 PDF/文本兜底：抽「文字 + 末尾数字」行，规格留空并提示人工补全。

    仅用于真正的纯文本报价单。文本层 PDF 报价单应优先走 find_tables 表格识别，
    否则每个单元格会被当成独立条目。
    """
    out = []
    for lineno, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or SKIP_ROW_RE.search(line) or len(line) < 4:
            continue
        nums = re.findall(r"-?\d[\d,]*(?:\.\d+)?", line)
        if not nums:
            continue
        # 末尾常为金额，倒数第二个常为单价；只有 1 个数字则视为单价
        price = to_float(nums[-2] if len(nums) >= 2 else nums[0])
        if price is None or price <= 0:
            continue
        name = re.sub(r"-?\d[\d,]*(?:\.\d+)?", " ", line)
        name = re.sub(r"\s+", " ", name).strip(" |:：元¥￥")
        if len(name) < 2:
            continue
        loc = {
            "kind": ctx["kind"], "file": ctx["file"],
            "price_text": nums[-2] if len(nums) >= 2 else nums[0],
            "line": lineno,
        }
        if pages:
            loc["page"] = pages.get(lineno, 1)
        it = make_item(
            ctx=ctx, name=name[:60], spec="", code="", brand="", qty=None, uom="",
            price=price, price_raw=loc["price_text"], currency=args.default_currency,
            tax_included=args.default_tax_included, tax_rate=args.default_tax_rate,
            note="文本行兜底抽取，规格/单位需人工补全", source_date="", locator=loc,
            warnings=warnings,
        )
        if it:
            out.append(it)
    if out:
        warnings.append(f"{ctx['file']}: {len(out)} 条为文本行兜底抽取，规格与单位需人工补全后再比价")
    return out


def parse_pdf(path, ctx, args, warnings):
    """PDF：优先用 PyMuPDF 表格识别（find_tables），失败再退回文本行兜底。

    文本层 PDF 的报价单几乎都是真表格，用文本行兜底会把单元格逐行当成条目
    （价格变成 "50"、"1250" 这种碎片），必须优先走表格识别。
    """
    try:
        import fitz
    except ImportError:
        warnings.append(f"{path}: 缺少 PyMuPDF，跳过 PDF 解析（pip install pymupdf）")
        return []
    ctx = dict(ctx, kind="pdf")

    out = []
    parts, pages = [], {}
    try:
        # 单次打开文档：表格识别与文本提取共用，避免重复 IO
        with fitz.open(path) as doc:
            lineno = 0
            for pi, page in enumerate(doc, 1):
                for line in page.get_text().splitlines():
                    lineno += 1
                    parts.append(line)
                    pages[lineno] = pi
                finder = getattr(page, "find_tables", None)
                if finder is None:
                    continue
                for tb in finder().tables:
                    rows = tb.extract()
                    if len(rows) < 2:
                        continue
                    got = parse_table(rows, ctx, args, warnings, sheet=f"p{pi}-table")
                    for it in got:
                        loc = dict(it.get("locator") or {})
                        loc.setdefault("page", pi)
                        it["locator"] = loc
                    out += got
    except Exception as e:
        warnings.append(f"{path}: PDF 表格识别失败（{type(e).__name__}: {e}），退回文本行抽取")
        out = []
    except Exception as e:
        warnings.append(f"{path}: PDF 表格识别失败（{type(e).__name__}: {e}），退回文本行抽取")

    if out:
        return out
    return parse_text_lines("\n".join(parts), ctx, args, warnings, pages=pages)


def parse_manual(path, warnings):
    data = load_json(path)
    items = data.get("items", []) if isinstance(data, dict) else data
    if not isinstance(items, list):
        warnings.append(f"{path}: 手工 JSON 需为数组或含 items 数组")
        return []
    for it in items:
        it.setdefault("locator", {"kind": "none"})
        it["locator"].setdefault("kind", "none")
    return items


def collect_paths(inputs):
    out = []
    for p in inputs:
        if os.path.isdir(p):
            for fn in sorted(os.listdir(p)):
                if fn.lower().endswith(SUPPORTED) and not fn.startswith("~"):
                    out.append(os.path.join(p, fn))
        else:
            out.append(p)
    return out


def main():
    ap = argparse.ArgumentParser(description="任意格式报价单 -> 统一 quotes.json")
    ap.add_argument("paths", nargs="+", help="报价单文件、目录，或 agent 手工构造的 .json")
    ap.add_argument("--out", default="quotes.json")
    ap.add_argument("--supplier-map", default="", help="JSON 字符串或 JSON 文件路径：文件名/路径 -> 供应商名")
    ap.add_argument("--supplier", default="", help="统一供应商名（单份报价单时使用）")
    ap.add_argument("--source-type", default="upload", choices=["upload", "library", "web"])
    ap.add_argument("--source-ref", default="", help="资料库文档名或网页 URL")
    ap.add_argument("--source-date", default="", help="报价/采集日期 YYYY-MM-DD")
    ap.add_argument("--default-currency", default="CNY")
    ap.add_argument("--default-tax-included", action="store_true", default=True)
    ap.add_argument("--default-tax-rate", type=float, default=0.13)
    ap.add_argument("--merge", action="store_true", help="与已存在的 --out 文件合并（追加资料库/网络价）")
    args = ap.parse_args()

    smap = {}
    if args.supplier_map:
        try:
            smap = load_json(args.supplier_map) if os.path.isfile(args.supplier_map) else json.loads(args.supplier_map)
        except Exception as e:
            print(f"[warn] supplier-map 解析失败：{e}")

    warnings, items, seen_files = [], [], []
    for path in collect_paths(args.paths):
        ap_abs = os.path.abspath(path)
        ext = os.path.splitext(ap_abs)[1].lower()
        base = os.path.basename(ap_abs)
        if ext not in SUPPORTED:
            if ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"):
                warnings.append(f"{base}: 图片报价单需由 agent 视觉读取后手工构造 JSON（见 references/data-contract.md）")
            elif ext in (".xls",):
                warnings.append(f"{base}: 旧版 .xls 请先另存为 .xlsx 再解析")
            continue
        supplier = args.supplier or smap.get(base) or smap.get(ap_abs) or re.sub(
            r"(报价单|报价表|quotation|quote|报价|价格表|\.xlsx?|\.csv)", "", os.path.splitext(base)[0], flags=re.I
        ).strip(" _-（）()") or base
        ctx = {
            "file": ap_abs, "supplier": supplier, "source_type": args.source_type,
            "source_ref": args.source_ref, "source_date": args.source_date,
        }
        try:
            if ext == ".json":
                got = parse_manual(ap_abs, warnings)
            elif ext in (".xlsx", ".xlsm"):
                got = parse_xlsx(ap_abs, ctx, args, warnings)
            elif ext == ".csv":
                got = parse_csv(ap_abs, ctx, args, warnings)
            elif ext == ".docx":
                got = parse_docx(ap_abs, ctx, args, warnings)
            elif ext == ".pdf":
                got = parse_pdf(ap_abs, ctx, args, warnings)
            else:
                got = parse_text_lines(open(ap_abs, encoding="utf-8", errors="replace").read(),
                                       dict(ctx, kind="txt"), args, warnings)
        except Exception as e:
            warnings.append(f"{base}: 解析异常 {type(e).__name__}: {e}")
            continue
        if got:
            seen_files.append(base)
            items += got
            names = sorted({g.get("supplier") or "?" for g in got})
            shown = names[0] if len(names) == 1 else f"{len(names)} 个来源（{', '.join(names[:3])}…）"
            print(f"  {base}: {len(got)} 条  | 来源实体={shown}")

    if args.merge and os.path.isfile(args.out):
        try:
            old = load_json(args.out).get("items", [])
            items = old + items
            print(f"  --merge: 合并已有 {len(old)} 条")
        except Exception as e:
            warnings.append(f"merge 读取失败：{e}")

    for i, it in enumerate(items, 1):
        it["item_id"] = f"q{i:04d}"

    out = {
        "generated_at": today(),
        "defaults": {
            "currency": args.default_currency,
            "tax_included": args.default_tax_included,
            "tax_rate": args.default_tax_rate,
        },
        "files": sorted(set(seen_files)),
        "warnings": warnings,
        "item_count": len(items),
        "items": items,
    }
    dump_json(args.out, out)
    print(f"\n[OK] {args.out}  共 {len(items)} 条，来自 {len(set(i['supplier'] for i in items))} 个来源实体")
    if warnings:
        print(f"[warn] {len(warnings)} 条提示：")
        for w in warnings[:12]:
            print("  -", w)
    if not items:
        sys.exit(2)


if __name__ == "__main__":
    main()
