# -*- coding: utf-8 -*-
"""
把设计规范 PDF 整理成「按空间/业态拆分的多份 xlsx 报价清单」。

用法：
    python generate_quotation_lists.py --pdf <spec.pdf> --items <items.json> --out <输出目录>

items.json 结构（见 references/items_json_schema.md）：
{
  "project": {"title": "...", "project": "...", "project_no": "...", "issue_date": "..."},
  "precise_images": {"HR-1601": ["x", 18, 421]},     # 条目编号 -> 精确图片引用
  "page_renderings": {"32": 165},                    # 页号 -> 该页主视觉图片 xref（空间效果图）
  "files": [
    {"filename": "01_客房.xlsx", "sheet": "01 Guest Room", "scopes": ["..."], "default_img": ["x", 19, 80],
     "groups": [{"title": "01 分组标题", "items": [
        {"code": "HR-1101", "cn": "米色墙布", "en": "Textured Beige Wallcovering", "uom": "Sq.M",
         "basis": "按墙面展开面积+5%损耗", "vendor": "REGARSA", "spec": ["Product: ..."],
         "cn_note": "...", "scope": "乙供", "ref": "2.5 / 10.2 (P.20, 63)", "img": [63, 0], "area": ""}
     ]}]}
  ]
}
图片引用两种写法：
    ["x", 页号, xref]  -> 精确定位到某个图片对象
    [页号, 序号]        -> 该页第 N 张（0 起，按从左到右排序，排除右上角 logo）
"""
import argparse
import io
import json
import math
import os
import re

import fitz
import openpyxl
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

FONT = "Microsoft YaHei"
GREY = PatternFill("solid", fgColor="D3D3D3")
INPUT_FILL = PatternFill("solid", fgColor="FFF7DC")
_thin = Side(style="thin", color="000000")
BORDER = Border(left=_thin, right=_thin, top=_thin, bottom=_thin)

HEADERS = [
    ("A", "Control #\n编号"), ("B", "Description\n材料/产品名称与规格"),
    ("C", "Area / Location\n区域"), ("E", "Qty\n数量"), ("F", "UOM\n单位"),
    ("G", "Mfr / Vendor\n品牌/供应商"), ("H", "Image\n图片"), ("I", "Price\n单价"),
    ("J", "Amount\n合价"), ("K", "Qty Basis\n数量计算依据"), ("L", "Ref.\n依据章节/页码"),
]
WIDTHS = {"A": 11, "B": 48, "C": 20, "D": 8, "E": 8, "F": 9, "G": 20, "H": 15,
          "I": 12, "J": 13, "K": 30, "L": 18}
DEFAULT_NOTES = [
    ("1. 编制依据 Source", "条目 \"Ref.\" 列标注了所在章节与页码，可对照原文件复核。"),
    ("2. 数量列 Qty (E列)", "数量由「参数 Parameters」页驱动：已给出参数的行会自动算出数量；参数为公式引用的行，"
                           "在参数页填入房量/面积后自动更新。参数无法确定时才留空（浅黄底纹）。"),
    ("3. 参数页 Parameters", "集中填写客房总数、各空间面积、损耗率等；改一处，全表数量联动重算。"),
    ("4. 数量依据列 Qty Basis (K列)", "说明该数量的推导规则，便于复核与调整损耗。"),
    ("5. 单价与合价 Price / Amount", "I 列单价由供应商填写；J 列公式为 =IF(OR(E=\"\",I=\"\"),\"\",E*I)，"
                                     "数量与单价都填后自动出合价。"),
    ("6. 货币与价格口径", "需明确：币种、是否含税/含运/含安装、是否含损耗与备品、价格有效期。"),
    ("7. 甲供/乙供划分", "描述中标注 Owner Supplied / Contractor / TBC，需业主确认后锁定范围。"),
    ("8. 品牌替代", "采用替代品牌须经设计方审批，\"品牌/供应商\"列已列备选。"),
]


def char_w(ch):
    return 2 if ord(ch) > 127 else 1


def line_lines(text, units):
    n = 0
    for line in str(text).split("\n"):
        if not line:
            n += 1
            continue
        n += max(1, math.ceil(sum(char_w(c) for c in line) / units))
    return n


def estimate_height(desc, area, basis):
    lines = max(line_lines(desc, 44), line_lines(area, 26), line_lines(basis, 28))
    return max(16, lines * 12.5 + 4)


def en_prefix(title):
    m = re.match(r"^[^\u4e00-\u9fff（(]*", title)
    return (m.group(0) if m else title).strip(" -–")


def build_desc(it):
    lines = ["%s  %s" % (it.get("cn", ""), it.get("en", ""))]
    for s in it.get("spec", []):
        lines.append("• " + s)
    if it.get("cn_note"):
        lines += ["", "中文说明：" + it["cn_note"]]
    if it.get("scope"):
        lines += ["", "供货责任 Scope：" + it["scope"]]
    return "\n".join(lines)


class ImageFinder:
    """PDF 图片抽取 + 三级取图策略"""

    FLOOR = 60  # dpi/quality 降质下限

    def __init__(self, pdf, dpi=130, quality=80, render_dpi=100, render_quality=82):
        self.doc = fitz.open(pdf)
        self.cache = {}
        self.dpi, self.quality = dpi, quality
        self.render_dpi, self.render_quality = render_dpi, render_quality

    def degrade(self, factor=0.8):
        """体积超限时整体降质：dpi 与 quality 各乘 factor（不低于 FLOOR），清空图片缓存。
        返回 True 表示本轮有实际降质；False 表示已到下限，无法继续。"""
        changed = False
        for attr in ("dpi", "render_dpi", "quality", "render_quality"):
            v = getattr(self, attr)
            nv = max(self.FLOOR, int(v * factor))
            if nv < v:
                setattr(self, attr, nv)
                changed = True
        self.cache.clear()
        return changed

    def page_images(self, pno):
        pg = self.doc[pno - 1]
        info = [im for im in pg.get_image_info(xrefs=True) if im["bbox"][1] > 100]
        info.sort(key=lambda im: im["bbox"][0])
        return info

    def get(self, ref):
        """ref = ["x", page, xref] / [page, idx] / ["r", page, (x0,y0,x1,y1)?]"""
        key = tuple(ref)
        if key in self.cache:
            return self.cache[key]
        if ref[0] == "r":  # 渲染 PDF 区域（矢量平面图无图片对象，直接栅格化）
            pno = int(ref[1])
            pg = self.doc[pno - 1]
            if len(ref) >= 6:
                rect = fitz.Rect(ref[2], ref[3], ref[4], ref[5])
            else:
                rect = pg.rect
            pm = pg.get_pixmap(clip=rect, dpi=self.render_dpi)
            data = pm.tobytes("jpg", jpg_quality=self.render_quality)
            self.cache[key] = data
            return data
        if ref[0] == "x":
            _, pno, xref = ref
            pg = self.doc[pno - 1]
            info = [im for im in pg.get_image_info(xrefs=True) if im["xref"] == xref]
            if not info:
                return None
            rect = fitz.Rect(info[0]["bbox"])
        else:
            pno, idx = ref
            imgs = self.page_images(pno)
            if idx >= len(imgs):
                return None
            r = fitz.Rect(imgs[idx]["bbox"])
            rect = fitz.Rect(max(0, r.x0 - 6), max(0, r.y0 - 6), r.x1 + 6, r.y1 + 6)
        pm = self.doc[pno - 1].get_pixmap(clip=rect, dpi=self.dpi)
        data = pm.tobytes("jpg", jpg_quality=self.quality)
        self.cache[key] = data
        return data

    @staticmethod
    def img_size(data):
        from PIL import Image
        im = Image.open(io.BytesIO(data))
        return im.width, im.height

    def ref_pages(self, ref):
        if not ref:
            return []
        pages = [int(n) for n in re.findall(r"P\.?\s?(\d+)", ref)]
        for grp in re.findall(r"\(\s*P\.?([^\)]*)\)", ref):
            pages += [int(n) for n in re.findall(r"\d+", grp)]
        seen, out = set(), []
        for p in pages:
            if 1 <= p <= self.doc.page_count and p not in seen:
                seen.add(p)
                out.append(p)
        return out

    def auto_match_text(self, en_name, pages):
        """按英文标题位置就近匹配图片：图片在标题上方且横向重叠，取垂直最近者"""
        if not en_name or not pages:
            return None
        key = en_name.split(" (")[0].split(" c/w")[0].strip()
        for pno in pages:
            pg = self.doc[pno - 1]
            rects = pg.search_for(key)
            if not rects:
                continue
            imgs = [im for im in self.page_images(pno) if im["bbox"][3] < 520]
            best, best_d = None, None
            for im in imgs:
                x0, y0, x1, y1 = im["bbox"]
                if min(x1, rects[0].x1) - max(x0, rects[0].x0) <= 0:
                    continue
                d = abs(y1 - rects[0].y0)
                if best_d is None or d < best_d:
                    best, best_d = im, d
            if best is not None:
                return ["x", pno, best["xref"]]
        return None

    def auto_rendering(self, pno):
        """该页没登记效果图时，自动取页面内面积最大的图片作为空间效果图"""
        imgs = self.page_images(pno)
        if not imgs:
            return None
        best = max(imgs, key=lambda im: (im["bbox"][2] - im["bbox"][0]) * (im["bbox"][3] - im["bbox"][1]))
        return ["x", pno, best["xref"]]

    def resolve(self, it, precise, renderings, default_img, auto=True):
        """①条目内置图 ②精确映射 ③标题自动匹配 ④章节效果图 ⑤文件兜底
        auto=False 时跳过自动取图（避免矢量平面图被重复贴到每一行）"""
        if it.get("img"):
            return it["img"]
        code = it.get("code", "")
        if code in precise:
            return precise[code]
        if not auto:
            return default_img
        pages = self.ref_pages(it.get("ref"))
        cand = self.auto_match_text(it.get("en"), pages)
        if cand:
            return cand
        for pno in pages:
            if str(pno) in renderings:
                return ["x", pno, renderings[str(pno)]]
            if pno in renderings:
                return ["x", pno, renderings[pno]]
        # 未登记效果图时，自动取该页面积最大的图片兜底
        for pno in pages:
            cand = self.auto_rendering(pno)
            if cand:
                return cand
        return default_img


def write_header(ws, project):
    rows = [
        (1, project.get("title", "Material & Product Quotation List"), 11, True),
        (2, "Project:      " + project.get("project", ""), 9, False),
        (3, "Project #:    " + project.get("project_no", "-"), 9, False),
        (4, "Issue Date:   " + project.get("issue_date", ""), 9, False),
        (5, "By Area", 9, False),
    ]
    for r, val, sz, bold in rows:
        c = ws.cell(row=r, column=4, value=val)
        c.font = Font(name=FONT, size=sz, bold=bold)
        c.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
        ws.merge_cells("D%d:H%d" % (r, r))
    for col, txt in HEADERS:
        c = ws["%s6" % col]
        c.value = txt
        c.font = Font(name=FONT, size=9, bold=True)
        c.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
        c.fill = GREY
        c.border = BORDER
    ws.merge_cells("C6:D6")
    ws["D6"].fill = GREY
    ws["D6"].border = BORDER
    ws["E6"].alignment = Alignment(horizontal="right", vertical="center", wrap_text=True)
    ws.row_dimensions[6].height = 30


def write_notes(ws, scopes, notes=None):
    ws.column_dimensions["A"].width = 34
    ws.column_dimensions["B"].width = 110
    ws["A1"] = "报价清单编制说明 / Quotation List Notes"
    ws["A1"].font = Font(name=FONT, size=12, bold=True)
    ws.merge_cells("A1:B1")
    row = 3
    for k, v in (notes or DEFAULT_NOTES):
        ws.cell(row=row, column=1, value=k).font = Font(name=FONT, size=9, bold=True)
        c = ws.cell(row=row, column=2, value=v)
        c.font = Font(name=FONT, size=9)
        c.alignment = Alignment(wrap_text=True, vertical="top")
        ws.cell(row=row, column=1).alignment = Alignment(wrap_text=True, vertical="top")
        ws.row_dimensions[row].height = max(30, (len(v) // 40 + 1) * 15)
        row += 1
    row += 1
    ws.cell(row=row, column=1, value="本文件涵盖空间范围 Scope of This File").font = Font(name=FONT, size=10, bold=True)
    row += 1
    for line in scopes:
        c = ws.cell(row=row, column=1, value=line)
        c.font = Font(name=FONT, size=9)
        c.alignment = Alignment(wrap_text=True, vertical="top")
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=2)
        row += 1


PARAM_SHEET = "参数 Parameters"

# 从条目文本里自动识别数量的常见写法
AUTO_PATTERNS = [
    ("fixed", re.compile(r"[×xX\*]\s*(\d+(?:\.\d+)?)\s*(?:台|个|套|只|张|把|件|樘|面|块|组|米|㎡|平米)?\s*$")),
    ("fixed", re.compile(r"^(\d+(?:\.\d+)?)\s*(?:台|个|套|只|张|把|件|樘|面|块|组)(?![^\s]*面积)")),
    ("fixed", re.compile(r"数量\s*[:：]?\s*(\d+(?:\.\d+)?)")),
    ("fixed", re.compile(r"(\d+(?:\.\d+)?)\s*(?:台|套|个|张)(?:\s*×\s*\d+)?$")),
]
PER_ROOM_PAT = re.compile(r"每(?:间|个)?(?:客房|房间|卫生间|浴室|房)\s*(\d+(?:\.\d+)?)\s*(个|套|只|张|台|件|面|块|组)?")


def suggest_qty_rule(it):
    """从 basis / spec 文本中自动推断数量规则（识别不出时返回 None）"""
    texts = [it.get("basis", "")] + list(it.get("spec", []))
    for t in texts:
        m = PER_ROOM_PAT.search(t)
        if m:
            return {"mode": "per_room", "per": float(m.group(1))}
    for t in texts:
        for mode, pat in AUTO_PATTERNS:
            m = pat.search(t.strip())
            if m:
                v = float(m.group(1))
                if 0 < v < 100000:
                    return {"mode": "fixed", "value": v}
    # 面积类：单位为面积且 basis 里提到某空间的面积
    if it.get("uom") in ("Sq.M", "㎡", "平方米", "Sq Ft", "平方英尺"):
        return {"mode": "area"}
    return None


def build_parameters_sheet(wb, parameters, used):
    """生成参数页，返回 {参数名: 单元格引用}"""
    ws = wb.create_sheet(PARAM_SHEET)
    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 14
    ws.column_dimensions["C"].width = 46
    ws["A1"] = "参数表 Parameters —— 填写后，主表数量列自动重算"
    ws["A1"].font = Font(name=FONT, size=12, bold=True)
    ws.merge_cells("A1:C1")
    for col, txt in ((1, "参数名称 Parameter"), (2, "数值 Value"), (3, "说明 Remark")):
        c = ws.cell(row=3, column=col, value=txt)
        c.font = Font(name=FONT, size=9, bold=True)
        c.fill = GREY
        c.border = BORDER
    refs, r = {}, 4
    for k, meta in parameters.items():
        ws.cell(row=r, column=1, value=k).font = Font(name=FONT, size=9)
        cell = ws.cell(row=r, column=3, value=meta.get("remark", "") if isinstance(meta, dict) else "")
        cell.font = Font(name=FONT, size=8)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        v = meta.get("value") if isinstance(meta, dict) else meta
        c = ws.cell(row=r, column=2, value=v)
        c.font = Font(name=FONT, size=9, bold=True)
        c.fill = INPUT_FILL
        c.number_format = "#,##0.00"
        c.border = BORDER
        ws.cell(row=r, column=1).border = BORDER
        ws.cell(row=r, column=3).border = BORDER
        refs[k] = "'%s'!$B$%d" % (PARAM_SHEET, r)
        r += 1
    ws.cell(row=r + 1, column=1,
            value="提示：数值留空时，主表对应数量显示为空白；填入后自动联动。").font = Font(name=FONT, size=8)
    return refs


def qty_formula(rule, params_ref, it):
    """把 qty_rule 转成 Excel 公式或确定数值"""
    if not rule:
        return None
    mode = rule.get("mode")

    def ref(name):
        return params_ref.get(name, "")

    def wrap(expr, deps):
        """任一依赖参数为空则整格留空"""
        if not deps:
            return "=" + expr
        if len(deps) == 1:
            return '=IF(%s="","",%s)' % (deps[0], expr)
        conds = ",".join('%s=""' % d for d in deps)
        return '=IF(OR(%s),"",%s)' % (conds, expr)

    if mode == "fixed":
        return rule.get("value")

    if mode == "per_room":
        p = rule.get("param", "客房总数")
        r = ref(p)
        if not r:
            return None
        per = rule.get("per", 1)
        waste = rule.get("waste", 0)
        expr = "%s*%s" % (r, per)
        if waste:
            expr += "*(1+%s)" % waste
        return wrap(expr, [r])

    if mode == "area":
        p = rule.get("param") or "单间面积"
        r = ref(p)
        if not r:
            return None
        waste = rule.get("waste", 0.05)
        mult = rule.get("multiply", 1)
        expr = "%s*%s" % (r, mult)
        if waste:
            expr += "*(1+%s)" % waste
        return wrap(expr, [r])

    if mode == "expr":
        expr = rule["expr"]
        deps = []
        for name, r in params_ref.items():
            token = "{%s}" % name
            if token in expr:
                expr = expr.replace(token, r)
                deps.append(r)
        return wrap(expr, deps)
    return None


def auto_codes(spec, index):
    """条目未给 code 时，按 文件序号-分组序号-流水 自动生成，前缀取 file.code_prefix"""
    prefix = spec.get("code_prefix") or "F%d" % index
    for gi, grp in enumerate(spec["groups"], 1):
        for ii, it in enumerate(grp["items"], 1):
            if not it.get("code"):
                it["code"] = "%s-%d%02d" % (prefix, gi, ii)


def build_file(finder, spec, project, precise, renderings, out_dir, index=1, parameters=None, disable_auto=False):
    auto_codes(spec, index)
    wb = openpyxl.Workbook()
    params_ref = build_parameters_sheet(wb, parameters or {}, spec)
    ws = wb.active
    ws.title = re.sub(r'[\\/*?:\[\]]', '_', spec["sheet"])[:31]
    for k, v in WIDTHS.items():
        ws.column_dimensions[k].width = v
    write_header(ws, project)

    r = 7
    qty_filled = [0]
    used_auto_imgs = set()  # 自动兜底图每文件限用一次，防止同一张效果图贴满整组
    for grp in spec["groups"]:
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=7)
        c = ws.cell(row=r, column=1, value=grp["title"])
        c.font = Font(name=FONT, size=9, bold=True)
        c.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
        for col in range(1, 8):
            ws.cell(row=r, column=col).fill = GREY
            ws.cell(row=r, column=col).border = BORDER
        ws.row_dimensions[r].height = 20
        r += 1
        garea = en_prefix(grp["title"])
        for it in grp["items"]:
            desc = build_desc(it)
            area = it.get("area") or garea
            ws.cell(row=r, column=1, value=it.get("code"))
            ws.cell(row=r, column=2, value=desc)
            ws.cell(row=r, column=3, value=area)
            ws.merge_cells(start_row=r, start_column=3, end_row=r, end_column=4)
            ws.cell(row=r, column=6, value=it.get("uom"))
            ws.cell(row=r, column=7, value=it.get("vendor"))
            ws.cell(row=r, column=11, value=it.get("basis"))
            ws.cell(row=r, column=12, value=it.get("ref"))
            ws.cell(row=r, column=10, value='=IF(OR(E%d="",I%d=""),"",E%d*I%d)' % (r, r, r, r))
            for col in range(1, 13):
                cc = ws.cell(row=r, column=col)
                cc.border = BORDER
                if col in (1, 2, 3, 4, 6, 7, 11, 12):
                    cc.font = Font(name=FONT, size=8)
                cc.alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
            # 数量：优先用 qty_rule，其次自动识别，最后留空
            rule = it.get("qty_rule") or suggest_qty_rule(it)
            q = qty_formula(rule, params_ref, it)
            if q is not None:
                ws.cell(row=r, column=5, value=q)
                qty_filled[0] += 1
            ws.cell(row=r, column=5).alignment = Alignment(horizontal="right", vertical="top")
            ws.cell(row=r, column=10).alignment = Alignment(horizontal="right", vertical="top")
            ws.cell(row=r, column=5).fill = INPUT_FILL if q is None else PatternFill()
            for col in (9, 10):
                ws.cell(row=r, column=col).fill = INPUT_FILL
            for col in (5, 9, 10):
                ws.cell(row=r, column=col).number_format = "#,##0.00"

            h = estimate_height(desc, area, it.get("basis", ""))
            img = finder.resolve(it, precise, renderings, spec.get("default_img"), auto=not disable_auto)
            if img:
                # 条目显式指定图（img / precise_images）始终保留；自动兜底图去重，
                # 已用过的留空（宁缺勿错，避免做法类条目整组贴同一张空间效果图）
                explicit = bool(it.get("img")) or it.get("code", "") in precise
                if not explicit:
                    if tuple(img) in used_auto_imgs:
                        img = None
                    else:
                        used_auto_imgs.add(tuple(img))
            if img:
                data = finder.get(img)
                if data:
                    try:
                        xim = XLImage(io.BytesIO(data))
                        w, hh = finder.img_size(data)
                        xim.width = 110
                        xim.height = int(110 * hh / w)
                        ws.add_image(xim, "H%d" % r)
                        h = max(h, xim.height * 0.78 + 8)
                    except Exception as e:  # 单张失败不影响整表
                        print("  ! image failed", it.get("code"), e)
            ws.row_dimensions[r].height = min(h, 300)
            r += 1

    ws.cell(row=r, column=1, value="Total 合计")
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=9)
    ws.cell(row=r, column=10, value="=SUM(J7:J%d)" % (r - 1))
    for col in range(1, 13):
        cc = ws.cell(row=r, column=col)
        cc.font = Font(name=FONT, size=9, bold=True)
        cc.fill = GREY
        cc.border = BORDER
    ws.cell(row=r, column=1).alignment = Alignment(horizontal="right", vertical="center")
    ws.cell(row=r, column=10).number_format = "#,##0.00"
    ws.row_dimensions[r].height = 22

    ws.freeze_panes = "A7"
    ws.sheet_view.zoomScale = 90
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_title_rows = "6:6"

    write_notes(wb.create_sheet("Notes 说明"), spec.get("scopes", []))

    path = os.path.join(out_dir, spec["filename"])
    wb.save(path)
    n = sum(len(g["items"]) for g in spec["groups"])
    print("saved %s  (条目 %d，数量已计算 %d)" % (path, n, qty_filled[0]))
    return n, qty_filled[0], path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--items", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--project-name", help="覆盖 project.project（动态项目名）")
    ap.add_argument("--issue-date", help="覆盖 project.issue_date")
    ap.add_argument("--analysis", help="analyze_pdf.py 产出的 JSON，用于自动填充项目名")
    ap.add_argument("--dpi", type=int, help="抠图/栅格化 DPI（默认 130/100，与 --jpg-quality 用于一开始就压体积）")
    ap.add_argument("--jpg-quality", type=int, help="JPEG 质量（默认 80/82）")
    ap.add_argument("--size-budget-mb", type=float, help="单文件体积预算 MB；生成后超限的文件自动降质重嵌（最多 4 轮）")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    data = json.load(open(args.items, encoding="utf-8"))
    project = data.get("project", {})

    if args.analysis:
        ana = json.load(open(args.analysis, encoding="utf-8"))
        project.setdefault("project", ana.get("project_name", ""))
        project.setdefault("title", "%s  Material & Product Quotation List / 材料与产品报价清单"
                           % ana.get("project_name", ""))
    if args.project_name:
        project["project"] = args.project_name
        project.setdefault("title", "%s  Material & Product Quotation List / 材料与产品报价清单"
                           % args.project_name)
    if args.issue_date:
        project["issue_date"] = args.issue_date
    project.setdefault("title", "Material & Product Quotation List  /  室内材料及产品报价清单")
    project.setdefault("project", "-")
    project.setdefault("project_no", "-")
    project.setdefault("issue_date", "")

    precise = data.get("precise_images", {})
    renderings = data.get("page_renderings", {})
    parameters = data.get("parameters", {})
    kwargs = {}
    if args.dpi:
        kwargs = {"dpi": args.dpi, "render_dpi": args.dpi}
    if args.jpg_quality:
        kwargs.update({"quality": args.jpg_quality, "render_quality": args.jpg_quality})
    finder = ImageFinder(args.pdf, **kwargs)

    total, filled = 0, 0
    for i, spec in enumerate(data["files"], 1):
        n, k, path = build_file(finder, spec, project, precise, renderings, args.out, i,
                                parameters, disable_auto=data.get("disable_auto_image", False))
        total += n
        filled += k
        if args.size_budget_mb:
            budget_bytes = args.size_budget_mb * 1024 * 1024
            rounds = 0
            while os.path.getsize(path) > budget_bytes and rounds < 4 and finder.degrade():
                rounds += 1
                _, _, path = build_file(finder, spec, project, precise, renderings, args.out, i,
                                        parameters, disable_auto=data.get("disable_auto_image", False))
                print("  size-budget: 第 %d 轮降质重生成 %s (dpi=%s q=%s)"
                      % (rounds, os.path.basename(path), finder.dpi, finder.quality))
            if os.path.getsize(path) > budget_bytes:
                print("  ! size-budget: %s 已降至最低质量仍超预算 (%.1fMB > %.1fMB)"
                      % (os.path.basename(path),
                         os.path.getsize(path) / 1048576, args.size_budget_mb))
    print("TOTAL ITEMS: %d  (其中数量已计算 %d 条)" % (total, filled))


if __name__ == "__main__":
    main()
