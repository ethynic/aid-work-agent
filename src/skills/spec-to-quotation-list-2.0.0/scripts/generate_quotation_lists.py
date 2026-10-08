# -*- coding: utf-8 -*-
"""
spec-to-quotation-list 核心生成器（数据驱动，不写死任何空间/大类/文件清单）：

输入 items.json：
  - 每条目必须带三个维度：space(文档真实空间名) / theme(大类) / category(产品类别)
  - 顶层：project / parameters / precise_images / page_renderings / items[]

生成逻辑（全部由数据值动态决定）：
  1. 动态发现：空间集合、大类集合
  2. 动态分组：(space, theme) -> {category: [items]}
  3. 动态确定：仅为「有真实产品的 (space × theme) 组合」生成文件
  4. 文件命名：{space}_{theme}.xlsx
  5. 每文件内：■ 产品类别分区 + 类别小计 + 合计总金额公式
  6. 含「参数 Parameters」页（数量联动）与「Notes 说明」页

空间名/大类名/类别名/文件数均取自数据，源码不预设任何清单。
源文档变更（如新增某空间、某大类无产品）后，重跑即自动增减文件。
"""
import argparse, io, json, math, os, re
import fitz
import openpyxl
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

FONT = "Microsoft YaHei"
GREY      = PatternFill("solid", fgColor="D3D3D3")
CAT_FILL  = PatternFill("solid", fgColor="BDD7EE")   # 类别抬头
CATSUB    = PatternFill("solid", fgColor="DDEBF7")   # 类别小计
GRAND     = PatternFill("solid", fgColor="FFD966")   # 合计总金额
INPUT     = PatternFill("solid", fgColor="FFF7DC")   # 待填（数量/单价/合价）
_thin = Side(style="thin", color="000000")
BORDER = Border(left=_thin, right=_thin, top=_thin, bottom=_thin)

HEADERS = [
    ("A", "Control #\n编号"), ("B", "Category\n产品类别"),
    ("C", "Description\n材料/产品名称与规格"), ("D", "Area / Location\n空间"),
    ("E", "Qty\n数量"), ("F", "UOM\n单位"), ("G", "Mfr / Vendor\n品牌/供应商"),
    ("H", "Image\n图片"), ("I", "Price\n单价"), ("J", "Amount\n合价"),
    ("K", "Qty Basis\n数量计算依据"), ("L", "Ref.\n依据章节/页码"),
]
WIDTHS = {"A": 11, "B": 15, "C": 46, "D": 18, "E": 8, "F": 9, "G": 20,
          "H": 15, "I": 12, "J": 14, "K": 30, "L": 18}

PARAM_SHEET = "参数 Parameters"

# ===================== 图片 =====================
class ImageFinder:
    """PDF 图片抽取 + 三级取图策略；支持 dpi/quality 降质（--size-budget-mb 用）"""

    FLOOR = 60  # dpi/quality 降质下限

    def __init__(self, pdf, dpi=130, quality=80, render_dpi=100, render_quality=82):
        self.doc = fitz.open(pdf); self.cache = {}
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
        pg = self.doc[pno-1]
        info=[im for im in pg.get_image_info(xrefs=True) if im["bbox"][1]>100]
        info.sort(key=lambda im:im["bbox"][0]); return info
    def _bytes(self, pm):
        # CMYK / 带透明通道的图直接编码 JPEG/PNG 会失败，先转 RGB
        p = pm
        if p.n - p.alpha >= 4:          # CMYK 或更多通道
            p = fitz.Pixmap(fitz.csRGB, p)
        try:
            return p.tobytes("jpg", jpg_quality=self.quality)
        except Exception:
            try:
                return p.tobytes("png")
            except Exception:
                p2 = fitz.Pixmap(fitz.csRGB, pm)   # 再次确保 RGB
                return p2.tobytes("png")
    def get(self, ref):
        key=tuple(ref)
        if key in self.cache: return self.cache[key]
        try:
            if ref[0]=="r":
                pno=int(ref[1]); pg=self.doc[pno-1]
                rect=fitz.Rect(ref[2],ref[3],ref[4],ref[5]) if len(ref)>=6 else pg.rect
                self.cache[key]=self._bytes(pg.get_pixmap(clip=rect,dpi=self.render_dpi)); return self.cache[key]
            if ref[0]=="x":
                _,pno,xref=ref; pg=self.doc[pno-1]
                info=[im for im in pg.get_image_info(xrefs=True) if im["xref"]==xref]
                if not info: return None
                rect=fitz.Rect(info[0]["bbox"])
            else:
                pno,idx=ref; imgs=self.page_images(pno)
                if idx>=len(imgs): return None
                r=fitz.Rect(imgs[idx]["bbox"])
                rect=fitz.Rect(max(0,r.x0-6),max(0,r.y0-6),r.x1+6,r.y1+6)
            self.cache[key]=self._bytes(self.doc[pno-1].get_pixmap(clip=rect,dpi=self.dpi)); return self.cache[key]
        except Exception as e:
            print("  ! image skip (ref=%s): %s"%(ref,e)); return None
    @staticmethod
    def img_size(data):
        from PIL import Image
        im=Image.open(io.BytesIO(data)); return im.width, im.height
    def ref_pages(self, ref):
        if not ref: return []
        pages=[int(n) for n in re.findall(r"P\.?\s?(\d+)", ref)]
        for grp in re.findall(r"\(\s*P\.?([^\)]*)\)", ref):
            pages+=[int(n) for n in re.findall(r"\d+", grp)]
        seen,out=set(),[]
        for p in pages:
            if 1<=p<=self.doc.page_count and p not in seen: seen.add(p); out.append(p)
        return out
    def auto_match_text(self, en_name, pages):
        if not en_name or not pages: return None
        key=en_name.split(" (")[0].split(" c/w")[0].strip()
        for pno in pages:
            pg=self.doc[pno-1]; rects=pg.search_for(key)
            if not rects: continue
            imgs=[im for im in self.page_images(pno) if im["bbox"][3]<520]
            best,bd=None,None
            for im in imgs:
                x0,y0,x1,y1=im["bbox"]
                if min(x1,rects[0].x1)-max(x0,rects[0].x0)<=0: continue
                d=abs(y1-rects[0].y0)
                if bd is None or d<bd: best,bd=im,d
            if best is not None: return ["x",pno,best["xref"]]
        return None
    def auto_rendering(self, pno):
        imgs=self.page_images(pno)
        if not imgs: return None
        best=max(imgs, key=lambda im:(im["bbox"][2]-im["bbox"][0])*(im["bbox"][3]-im["bbox"][1])); return ["x",pno,best["xref"]]
    def resolve(self, it, precise, renderings, default_img, auto=True):
        if it.get("img"): return it["img"]
        code=it.get("code","")
        if code in precise: return precise[code]
        if not auto: return default_img
        pages=self.ref_pages(it.get("ref"))
        cand=self.auto_match_text(it.get("en"),pages)
        if cand: return cand
        for pno in pages:
            if str(pno) in renderings: return ["x",pno,renderings[str(pno)]]
            if pno in renderings: return ["x",pno,renderings[pno]]
        for pno in pages:
            cand=self.auto_rendering(pno)
            if cand: return cand
        return default_img

# ===================== 数量 =====================
AUTO_PATTERNS=[("fixed",re.compile(r"[×xX\*]\s*(\d+(?:\.\d+)?)\s*(?:台|个|套|只|张|把|件|樘|面|块|组|米|㎡|平米)?\s*$")),
 ("fixed",re.compile(r"^(\d+(?:\.\d+)?)\s*(?:台|个|套|只|张|把|件|樘|面|块|组)(?![^\s]*面积)")),
 ("fixed",re.compile(r"数量\s*[:：]?\s*(\d+(?:\.\d+)?)")),
 ("fixed",re.compile(r"(\d+(?:\.\d+)?)\s*(?:台|套|个|张)(?:\s*×\s*\d+)?$"))]
PER_ROOM_PAT=re.compile(r"每(?:间|个)?(?:客房|房间|卫生间|浴室|房)\s*(\d+(?:\.\d+)?)\s*(个|套|只|张|台|件|面|块|组)?")
def suggest_qty_rule(it):
    texts=[it.get("basis","")]+list(it.get("spec",[]))
    for t in texts:
        m=PER_ROOM_PAT.search(t)
        if m: return {"mode":"per_room","per":float(m.group(1))}
    for t in texts:
        for mode,pat in AUTO_PATTERNS:
            m=pat.search(t.strip())
            if m:
                v=float(m.group(1))
                if 0<v<100000: return {"mode":"fixed","value":v}
    if it.get("uom") in ("Sq.M","㎡","平方米","Sq Ft","平方英尺"): return {"mode":"area"}
    return None

def qty_formula(rule, params_ref):
    if not rule: return None
    mode=rule.get("mode")
    def ref(n): return params_ref.get(n,"")
    def wrap(expr,deps):
        if not deps: return "="+expr
        if len(deps)==1: return '=IF(%s="","",%s)'%(deps[0],expr)
        conds=",".join('%s=""'%d for d in deps)
        return '=IF(OR(%s),"",%s)'%(conds,expr)
    if mode=="fixed": return rule.get("value")
    if mode=="per_room":
        p=rule.get("param","客房总数"); r=ref(p)
        if not r: return None
        per=rule.get("per",1); waste=rule.get("waste",0); expr="%s*%s"%(r,per)
        if waste: expr+="*(1+%s)"%waste
        return wrap(expr,[r])
    if mode=="area":
        p=rule.get("param") or "单间面积"; r=ref(p)
        if not r: return None
        waste=rule.get("waste",0.05); mult=rule.get("multiply",1); expr="%s*%s"%(r,mult)
        if waste: expr+="*(1+%s)"%waste
        return wrap(expr,[r])
    if mode=="expr":
        expr=rule["expr"]; deps=[]
        for name,r in params_ref.items():
            tk="{%s}"%name
            if tk in expr: expr=expr.replace(tk,r); deps.append(r)
        return wrap(expr,deps)
    return None

def build_parameters_sheet(wb, parameters):
    ws=wb.create_sheet(PARAM_SHEET)
    ws.column_dimensions["A"].width=30; ws.column_dimensions["B"].width=14; ws.column_dimensions["C"].width=46
    ws["A1"]="参数表 Parameters —— 填写后，主表数量列自动重算"
    ws["A1"].font=Font(name=FONT,size=12,bold=True); ws.merge_cells("A1:C1")
    for col,txt in ((1,"参数名称 Parameter"),(2,"数值 Value"),(3,"说明 Remark")):
        c=ws.cell(row=3,column=col,value=txt); c.font=Font(name=FONT,size=9,bold=True); c.fill=GREY; c.border=BORDER
    refs,r={},4
    for k,meta in parameters.items():
        ws.cell(row=r,column=1,value=k).font=Font(name=FONT,size=9)
        ws.cell(row=r,column=3,value=meta.get("remark","") if isinstance(meta,dict) else "").font=Font(name=FONT,size=8)
        ws.cell(row=r,column=3).alignment=Alignment(wrap_text=True,vertical="top")
        v=meta.get("value") if isinstance(meta,dict) else meta
        c=ws.cell(row=r,column=2,value=v); c.font=Font(name=FONT,size=9,bold=True); c.fill=INPUT; c.number_format="#,##0.00"; c.border=BORDER
        ws.cell(row=r,column=1).border=BORDER; ws.cell(row=r,column=3).border=BORDER
        refs[k]="'%s'!$B$%d"%(PARAM_SHEET,r); r+=1
    ws.cell(row=r+1,column=1,value="提示：数值留空时，主表对应数量显示为空白；填入后自动联动。").font=Font(name=FONT,size=8)
    return refs

def char_w(ch): return 2 if ord(ch)>127 else 1
def line_lines(text,units):
    n=0
    for line in str(text).split("\n"):
        if not line: n+=1; continue
        n+=max(1,math.ceil(sum(char_w(c) for c in line)/units))
    return n
def estimate_height(desc,area,basis):
    lines=max(line_lines(desc,44),line_lines(area,26),line_lines(basis,28)); return max(16,lines*12.5+4)
def build_desc(it):
    lines=["%s  %s"%(it.get("cn",""),it.get("en",""))]
    for s in it.get("spec",[]): lines.append("• "+s)
    if it.get("cn_note"): lines+=["","中文说明："+it["cn_note"]]
    if it.get("scope"): lines+=["","供货责任 Scope："+it["scope"]]
    return "\n".join(lines)

def write_header(ws, project, theme, by_all=False):
    scope_txt = "All Areas (no space split)  ·  大类："+theme if by_all else "By Space & Category  ·  大类："+theme
    for r,val,sz,bold in [(1,project.get("title","Material & Product Quotation List"),11,True),
                          (2,"Project:      "+project.get("project",""),9,False),
                          (3,"Project #:    "+project.get("project_no","-"),9,False),
                          (4,"Issue Date:   "+project.get("issue_date",""),9,False),
                          (5,scope_txt,9,False)]:
        c=ws.cell(row=r,column=4,value=val); c.font=Font(name=FONT,size=sz,bold=bold)
        c.alignment=Alignment(horizontal="left",vertical="center",wrap_text=True); ws.merge_cells("D%d:H%d"%(r,r))
    for col,txt in HEADERS:
        c=ws["%s6"%col]; c.value=txt; c.font=Font(name=FONT,size=9,bold=True)
        c.alignment=Alignment(horizontal="left",vertical="center",wrap_text=True); c.fill=GREY; c.border=BORDER
    ws.row_dimensions[6].height=30

def write_notes(wb, space, theme, scopes, whole=False):
    ws=wb.create_sheet("Notes 说明"); ws.column_dimensions["A"].width=34; ws.column_dimensions["B"].width=110
    ws["A1"]="报价清单编制说明 / Quotation List Notes"; ws["A1"].font=Font(name=FONT,size=12,bold=True); ws.merge_cells("A1:B1")
    org=("本文件为「%s」大类的全项目汇总报价，不按空间拆分（未按空间另分文件）。"%theme) if whole \
        else ("本文件为「%s」大类中「%s」空间的独立报价（按实际空间与大类动态生成，未写死清单）。"%(theme,space))
    notes=[("1. 编制依据 Source","条目 \"Ref.\" 列标注了所在章节与页码，可对照原文件复核。"),
           ("2. 组织方式 Organization",org),
           ("3. 数量列 Qty (E列)","由「参数 Parameters」页驱动：已给参数的行自动算量；参数为公式引用的行，填房量/面积后自动更新；无法确定时留空（浅黄底纹）。"),
           ("4. 小计与合计 Subtotals","每个产品类别末有「小计」，文件末有「合计总金额 Grand Total」=SUM(各类别小计)，均为公式，数量/单价填后自动累计。"),
           ("5. 单价与合价 Price / Amount","I 列单价由供应商填写；J 列合价 = E×I；空白数量或单价时合价为空。"),
           ("6. 货币与价格口径","需明确：币种、是否含税/含运/含安装、是否含损耗与备品、价格有效期。"),
           ("7. 甲供/乙供划分","描述中标注 Owner Supplied / Contractor / TBC，需业主确认后锁定范围。"),
           ("8. 品牌替代","采用替代品牌须经设计方审批。")]
    row=3
    for k,v in notes:
        ws.cell(row=row,column=1,value=k).font=Font(name=FONT,size=9,bold=True)
        c=ws.cell(row=row,column=2,value=v); c.font=Font(name=FONT,size=9); c.alignment=Alignment(wrap_text=True,vertical="top")
        ws.cell(row=row,column=1).alignment=Alignment(wrap_text=True,vertical="top")
        ws.row_dimensions[row].height=max(30,(len(v)//40+1)*15); row+=1
    row+=1
    ws.cell(row=row,column=1,value="本文件涵盖范围 Scope of This File").font=Font(name=FONT,size=10,bold=True); row+=1
    for line in scopes:
        c=ws.cell(row=row,column=1,value=line); c.font=Font(name=FONT,size=9); c.alignment=Alignment(wrap_text=True,vertical="top")
        ws.merge_cells(start_row=row,start_column=1,end_row=row,end_column=2); row+=1

def sum_cells(cells):
    return "=SUM("+",".join("J%d"%c for c in cells)+")"

def area_label(s):
    """空间名简称（用于较窄的 Area / Location 列）；保留文档真实名的辨识主体。"""
    s = (s or "").strip()
    if s.lower().startswith("the "): s = s[4:]
    return s

def build_file(finder, space, theme, cats_sorted, project, precise, renderings, out_dir, parameters,
               file_space_label=None):
    """cats_sorted: [(category, [items])]。
    默认：本文件仅含这一个 (space, theme) 组合，一开头写 ◆ 空间抬头。
    file_space_label：跨空间合并的大类总文件（如「标识」「客用品与易耗品」不按空间拆分）时传入显示用标题，
                      此时不写 ◆ 抬头行，D 列写每条自身的真实空间名。
    """
    merged = bool(file_space_label)
    wb=openpyxl.Workbook()
    params_ref=build_parameters_sheet(wb, parameters)
    sheet_title="%s / %s"%(file_space_label or space, theme)
    ws=wb.active; ws.title=re.sub(r'[\\/*?:\[\]]','_',sheet_title)[:31]
    for k,v in WIDTHS.items(): ws.column_dimensions[k].width=v
    write_header(ws, project, theme, by_all=merged)
    r=7; qty_filled=[0]
    if not merged:
        # 空间抬头（文件即单一空间）
        ws.merge_cells(start_row=r,start_column=1,end_row=r,end_column=12)
        c=ws.cell(row=r,column=1,value="◆ "+space); c.font=Font(name=FONT,size=10,bold=True)
        c.alignment=Alignment(horizontal="left",vertical="center")
        for col in range(1,13): ws.cell(row=r,column=col).fill=GREY; ws.cell(row=r,column=col).border=BORDER
        ws.row_dimensions[r].height=22; r+=1
    cat_subtotals=[]
    for cat, items in cats_sorted:
        if not items: continue
        # 类别抬头
        ws.merge_cells(start_row=r,start_column=1,end_row=r,end_column=12)
        c=ws.cell(row=r,column=1,value="■ "+cat); c.font=Font(name=FONT,size=9,bold=True)
        c.alignment=Alignment(horizontal="left",vertical="center")
        for col in range(1,13): ws.cell(row=r,column=col).fill=CAT_FILL; ws.cell(row=r,column=col).border=BORDER
        ws.row_dimensions[r].height=18; r+=1
        first=r
        for it in items:
            desc=build_desc(it)
            _sp=it.get("space") or space
            _d=area_label(_sp) if merged else _sp.split(" ")[0]
            ws.cell(row=r,column=1,value=it.get("code"))
            ws.cell(row=r,column=2,value=cat.split(" ")[0])   # 类别简称入 B 列，便于筛选
            ws.cell(row=r,column=3,value=desc)
            ws.cell(row=r,column=4,value=_d)
            ws.cell(row=r,column=6,value=it.get("uom"))
            ws.cell(row=r,column=7,value=it.get("vendor"))
            ws.cell(row=r,column=11,value=it.get("basis"))
            ws.cell(row=r,column=12,value=it.get("ref"))
            ws.cell(row=r,column=10,value='=IF(OR(E%d="",I%d=""),"",E%d*I%d)'%(r,r,r,r))
            for col in range(1,13):
                cc=ws.cell(row=r,column=col); cc.border=BORDER
                if col in (1,2,3,4,6,7,11,12): cc.font=Font(name=FONT,size=8)
                cc.alignment=Alignment(horizontal="left",vertical="top",wrap_text=True)
            rule=it.get("qty_rule") or suggest_qty_rule(it)
            q=qty_formula(rule, params_ref)
            if q is not None: ws.cell(row=r,column=5,value=q); qty_filled[0]+=1
            ws.cell(row=r,column=5).alignment=Alignment(horizontal="right",vertical="top")
            ws.cell(row=r,column=10).alignment=Alignment(horizontal="right",vertical="top")
            if q is None: ws.cell(row=r,column=5).fill=INPUT
            for col in (9,10): ws.cell(row=r,column=col).fill=INPUT
            for col in (5,9,10): ws.cell(row=r,column=col).number_format="#,##0.00"
            h=estimate_height(desc,_d,it.get("basis",""))
            img=finder.resolve(it,precise,renderings,None,auto=True)
            if img:
                data=finder.get(img)
                if data:
                    try:
                        xim=XLImage(io.BytesIO(data)); w,hh=finder.img_size(data)
                        xim.width=110; xim.height=int(110*hh/w); ws.add_image(xim,"H%d"%r)
                        h=max(h,xim.height*0.78+8)
                    except Exception as e: print("  ! image failed",it.get("code"),e)
            ws.row_dimensions[r].height=min(h,300); r+=1
        # 类别小计
        ws.merge_cells(start_row=r,start_column=1,end_row=r,end_column=9)
        ws.cell(row=r,column=1,value="小计 Subtotal — "+cat.split(" ")[0]).font=Font(name=FONT,size=9,bold=True)
        ws.cell(row=r,column=10,value=sum_cells(list(range(first,r))))
        for col in range(1,13):
            cc=ws.cell(row=r,column=col); cc.fill=CATSUB; cc.border=BORDER
            if col==10: cc.number_format="#,##0.00"; cc.font=Font(name=FONT,size=9,bold=True)
        ws.cell(row=r,column=1).alignment=Alignment(horizontal="right",vertical="center")
        cat_subtotals.append(r); r+=1
    # 合计总金额
    ws.merge_cells(start_row=r,start_column=1,end_row=r,end_column=9)
    ws.cell(row=r,column=1,value="合计总金额 Grand Total").font=Font(name=FONT,size=11,bold=True)
    ws.cell(row=r,column=10,value=sum_cells(cat_subtotals))
    for col in range(1,13):
        cc=ws.cell(row=r,column=col); cc.fill=GRAND; cc.border=BORDER
        if col==10: cc.number_format="#,##0.00"; cc.font=Font(name=FONT,size=11,bold=True)
    ws.cell(row=r,column=1).alignment=Alignment(horizontal="right",vertical="center")
    ws.row_dimensions[r].height=24
    ws.freeze_panes="A7"; ws.sheet_view.zoomScale=90
    ws.page_setup.orientation="landscape"; ws.page_setup.fitToWidth=1
    ws.sheet_properties.pageSetUpPr.fitToPage=True; ws.print_title_rows="6:6"
    if merged:
        areas=sorted({(it.get("space") or "") for _,its in cats_sorted for it in its})
        scopes=[
            "本文件为「%s」大类的全项目汇总报价，不按空间拆分，仅此一份（涵盖：%s）。"%(theme," / ".join(areas)),
            "按产品类别（%s）分区，各类别有「小计」，文件末有「合计总金额 Grand Total」公式。"
            % " / ".join(cat.split(" ")[0] for cat,_ in cats_sorted),
            "每条 D 列标注其所属空间，便于询价后按空间拆分下单；数量由「参数 Parameters」页联动（填房量/面积后自动重算）；"
            "单价 I 列由供应商填写，J 列合价=E×I。",
        ]
        write_notes(wb, file_space_label, theme, scopes, whole=True)
        filename="%s.xlsx" % safe_name(theme)
    else:
        scopes=[
            "本文件为「%s」大类中「%s」空间的独立报价（按实际空间与大类动态生成）。"% (theme, space),
            "空间内按产品类别（%s）分区，各类别有「小计」，文件末有「合计总金额 Grand Total」公式。"
            % " / ".join(cat.split(" ")[0] for cat,_ in cats_sorted),
            "数量由「参数 Parameters」页联动（填房量/面积后自动重算）；单价 I 列由供应商填写，J 列合价=E×I。",
        ]
        write_notes(wb, space, theme, scopes)
        filename="%s_%s.xlsx" % (safe_name(space), safe_name(theme))
    path=os.path.join(out_dir,filename); wb.save(path)
    n=sum(len(items) for _,items in cats_sorted)
    print("  saved %s  (条目 %d，数量已计算 %d)"%(filename,n,qty_filled[0]))
    return n, qty_filled[0], path

def safe_name(s):
    return "".join(c if c not in '\\/:*?"<>|' else "_" for c in s).strip()

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--items", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--analysis")
    ap.add_argument("--project-name")
    ap.add_argument("--issue-date")
    ap.add_argument("--structure", help="确认后的结构 JSON（spaces/themes/renames），"
                                         "仅生成其中列出的空间×大类组合，并按 renames 重命名")
    ap.add_argument("--dpi", type=int, help="抠图/栅格化 DPI（默认 130/100，与 --jpg-quality 用于一开始就压体积）")
    ap.add_argument("--jpg-quality", type=int, help="JPEG 质量（默认 80/82）")
    ap.add_argument("--size-budget-mb", type=float, help="单文件体积预算 MB；生成后超限的文件自动降质重嵌（最多 4 轮）")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    data = json.load(open(args.items, encoding="utf-8"))
    items = data["items"]

    # ---------- 结构约束（用户确认后的空间/大类，可选）----------
    struct = None
    if args.structure and os.path.exists(args.structure):
        struct = json.load(open(args.structure, encoding="utf-8"))
        sset = set(struct.get("spaces", []))
        tset = set(struct.get("themes", []))
        # global_themes 大类（跨空间合并文件）不要求列进 themes，过滤时豁免
        gset = set(struct.get("global_themes", []) or [])
        ren = struct.get("renames", {}) or {}
        before = len(items)
        kept = []
        for it in items:
            sp = ren.get(it.get("space"), it.get("space"))
            th = ren.get(it.get("theme"), it.get("theme"))
            it["space"] = sp
            it["theme"] = th
            if sset and sp not in sset and th not in gset:
                continue
            if tset and th not in tset and th not in gset:
                continue
            kept.append(it)
        items = kept
        print("【结构约束】仅生成确认的空间(%d)×大类(%d)；应用重命名 %s；条目 %d→%d"
              % (len(sset), len(tset), ren, before, len(items)))
    project = data.get("project", {})
    if args.project_name: project["project"]=args.project_name
    if args.issue_date: project["issue_date"]=args.issue_date
    if args.analysis and not project.get("project"):
        try:
            a=json.load(open(args.analysis,encoding="utf-8"))
            project["project"]=a.get("project_name") or project.get("project")
        except Exception: pass
    precise=data.get("precise_images",{})
    renderings=data.get("page_renderings",{})
    parameters=data.get("parameters",{})

    # ---------- 动态发现 ----------
    spaces = sorted({i["space"] for i in items if i.get("space")}, key=lambda s:s)
    themes = sorted({i["theme"] for i in items if i.get("theme")}, key=lambda t:t)
    print("【动态发现】空间 %d 个: %s" % (len(spaces), spaces))
    print("【动态发现】大类 %d 个: %s" % (len(themes), themes))

    # ---------- 动态分组（仅真实产品计入"是否生成"）----------
    combo = {}   # (space, theme) -> {category: [items]}
    for it in items:
        sp = it.get("space"); th = it.get("theme")
        if not sp or not th: continue
        cat = it.get("category") or "其他"
        combo.setdefault((sp,th), {}).setdefault(cat, []).append(it)

    def _real(cats):
        return [it for its in cats.values() for it in its if not it.get("tbc")]

    # 跨空间合并的大类（如 标识 / 客用品与易耗品）：全项目只出一份文件，不按空间拆分
    gthemes = set((struct or {}).get("global_themes", []) or [])
    if gthemes:
        print("【合并大类】以下大类不按空间拆分，各生成 1 份全项目文件: %s" % sorted(gthemes & {t for _,t in combo}))

    pairs = []          # [(space, theme)] 常规：按空间×大类各自成文件
    mergedcats = {}     # theme -> {category: [items]}  跨空间合并
    for (sp,th), cats in combo.items():
        if not _real(cats): continue
        if th in gthemes:
            bucket = mergedcats.setdefault(th, {})
            for cat, its in cats.items():
                bucket.setdefault(cat, []).extend(its)
        else:
            pairs.append((sp,th))
    pairs.sort(key=lambda p:(p[1], p[0]))   # 先按大类、再按空间
    merged = sorted(mergedcats.items(), key=lambda kv: kv[0])

    nfiles = len(pairs) + len(merged)
    print("【动态确定】将生成 %d 个文件（仅含有真实产品的组合）:" % nfiles)
    for th, cats in merged:
        n = sum(len(v) for v in cats.values())
        print("   - %s.xlsx   (全项目合并 / %d 类 / %d 条)" % (safe_name(th), len(cats), n))
    for (sp,th) in pairs:
        cats = combo[(sp,th)]
        n = sum(len(v) for v in cats.values())
        print("   - %s_%s.xlsx   (%d 类 / %d 条)" % (safe_name(sp), safe_name(th), len(cats), n))

    # ---------- 动态生成 ----------
    finder_kwargs = {}
    if args.dpi: finder_kwargs["dpi"] = args.dpi
    if args.jpg_quality: finder_kwargs["quality"] = args.jpg_quality
    finder = ImageFinder(args.pdf, **finder_kwargs)

    def gen_with_budget(build):
        """生成一个文件并按 --size-budget-mb 自动降质重嵌（最多 4 轮），返回 (n, k, path)。"""
        n, k, path = build()
        if args.size_budget_mb:
            budget_bytes = args.size_budget_mb * 1024 * 1024
            rounds = 0
            while os.path.getsize(path) > budget_bytes and rounds < 4 and finder.degrade():
                rounds += 1
                _, _, path = build()
                print("  size-budget: 第 %d 轮降质重生成 %s (dpi=%s q=%s)"
                      % (rounds, os.path.basename(path), finder.dpi, finder.quality))
            if os.path.getsize(path) > budget_bytes:
                print("  ! size-budget: %s 已降至最低质量仍超预算 (%.1fMB > %.1fMB)"
                      % (os.path.basename(path),
                         os.path.getsize(path) / 1048576, args.size_budget_mb))
        return n, k, path

    total, filled = 0, 0
    for th, cats in merged:
        cats_sorted = sorted(cats.items(), key=lambda kv: kv[1][0].get("code","") if kv[1] else "")
        n, k, _ = gen_with_budget(lambda: build_file(
            finder, "", th, cats_sorted, project, precise, renderings, args.out, parameters,
            file_space_label="全项目 All Areas"))
        total += n; filled += k
    for (sp,th) in pairs:
        cats = combo[(sp,th)]
        cats_sorted = sorted(cats.items(), key=lambda kv: kv[1][0].get("code","") if kv[1] else "")
        n, k, _ = gen_with_budget(lambda: build_file(
            finder, sp, th, cats_sorted, project, precise, renderings, args.out, parameters))
        total += n; filled += k
    print("【完成】共 %d 条，数量已计算 %d 条；文件 %d 个（其中跨空间合并 %d 个）。"
          % (total, filled, nfiles, len(merged)))

if __name__ == "__main__":
    main()
