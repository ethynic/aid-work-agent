# -*- coding: utf-8 -*-
"""商用免费字体策略（commercially-free font policy）——pptx_gen 唯一字体决策点。

字体双轨策略（commercially-free，零授权风险）：
  - **东亚字符（CJK）** → ``FONT_NAME`` = 思源黑体 CN（Source Han Sans CN，OFL 1.1）。
  - **拉丁字母 / 数字 / 基础标点** → ``FONT_NAME_LATIN`` = Noto Sans（OFL 1.1）。

为什么拉丁字符要单独换字体：思源黑体本质上是为 CJK 设计的，它的拉丁字母/
数字字形是按汉字混排习惯做的"全角感"字形，单独看偏宽、偏怪。Noto Sans 与
思源黑体同源（Noto Sans CJK 即 Source Han 的 Google 发行版），拉丁字形干净
专业，二者搭配视觉一致。PowerPoint/WPS 按字符所属"脚本"自动选字体：latin
标签管 ASCII、ea 标签管 CJK，所以我们把 latin/cs 写成 Noto Sans、ea 写成
思源黑体即可各司其职。

铁律：
  1. deck 内：latin/cs → FONT_NAME_LATIN（Noto Sans），ea → FONT_NAME（思源）。
  2. 测量（native.measure）、QA 预览（render.Canvas.font）、字体嵌入
     （embed_fonts）全部用同一份随包字体文件（assets/fonts/），保证
     "测得准"与"渲染一致"。measure 按字符脚本分别用对应字体度量。
  3. 路线一保留客户模板版式时，模板字体由白名单 + 映射表接管（可按 latin/ea
     标签分别映射）：商业拉丁字体（Arial/Calibri/Times...）→ FONT_NAME_LATIN；
     商业中文字体（微软雅黑/宋体...）→ FONT_NAME；白名单免费字体原样保留。

为什么禁用微软雅黑：msyh 是微软商业授权字体，"随 Windows 分发"不等于
"可商用"——把生成的 PPT 交付客户属于商业使用。思源黑体 CN / Noto Sans 由
Adobe / Google 发布，OFL 1.1 明确允许商业使用与嵌入；许可证见 OFL-1.1.txt。

Public API:
    FONT_NAME                       东亚字符使用的字体族名（思源黑体 CN）
    FONT_NAME_LATIN                 拉丁字符使用的字体族名（Noto Sans）
    font_file(bold)                 随包思源黑体路径（CJK，找不到了回退系统免费字体）
    latin_font_file(bold)           随包 Noto Sans 路径（拉丁，找不到了回退系统免费字体）
    is_cjk(ch)                      判断一个字符是否属于东亚脚本（用思源度量）
    remap_typeface(name, tag)       单个字体名 → 免费等价（可按 a:latin/a:ea 标签分流）
    remap_presentation(prs, strict) 全 deck（幻灯片/版式/母版/主题/图表）字体合规化
    embed_fonts(pptx_path, out)     把思源 + Noto Sans 嵌入 pptx（opt-in）
"""
import os
import re
import shutil
import zipfile

from lxml import etree
from pptx.oxml.ns import qn

FONT_NAME = "Source Han Sans CN"      # 东亚字符字体族（与包内 OTF 家族名一致）
FONT_NAME_LATIN = "Noto Sans"          # 拉丁字母/数字字体族（与包内 TTF 家族名一致）

_HERE = os.path.dirname(os.path.abspath(__file__))
# 字体文件随包分发（pptx_gen/assets/fonts/），保证"测得准=渲染一致"。
# 注意：解析不到包内文件时会回退系统字体——验证脚本必须断言这里返回
# 的是包内字体文件，否则测量与 deck 字体名会静默分叉。
_FONT_DIR = os.path.join(_HERE, "assets", "fonts")
_REGULAR = os.path.join(_FONT_DIR, "SourceHanSansCN-Regular.otf")
_BOLD = os.path.join(_FONT_DIR, "SourceHanSansCN-Bold.otf")
_LATIN_REGULAR = os.path.join(_FONT_DIR, "NotoSans-Regular.ttf")
_LATIN_BOLD = os.path.join(_FONT_DIR, "NotoSans-Bold.ttf")
_LICENSE = os.path.join(_FONT_DIR, "OFL-1.1.txt")

# ---------------------------------------------------------------- 白名单
# 已知可免费商用的字体族（规范化后比较；规范化 = 小写去空格/下划线/连字符）
_FREE_FAMILIES = {
    # 思源家族（OFL）
    "sourcehansans", "sourcehansanssc", "sourcehansanscn", "sourcehansanshc",
    "sourcehanserif", "sourcehanserifsc", "sourcehanserifcn", "sourcehanserifhc",
    "sourcesanspro", "sourceserifpro", "思源黑体", "思源宋体", "思源真黑体",
    # Noto（OFL）
    "notosanssc", "notoserifsc", "notosanscjksc", "notoserifcjksc",
    "notosans", "notoserif", "notosansmono",
    # 国产免费可商用
    "misans", "misansnormal", "小米miSans",
    "alibabapuhuiti", "alibabapuhuiti30", "alibabasans", "阿里巴巴普惠体",
    "harmonyossans", "harmonyossanssc", "harmonyos sans sc",
    "opposans", "opposans30",
    "douyinsans", "抖音美好体",
    "zcoolkuaile", "zcoolgaoduanhei", "zcoolqingkehuangyou", "zcoolwenyiti",
    "站酷快乐体", "站酷高端黑", "站酷庆科黄油体", "站酷文艺体",
    "glowsans", "未来荧黑", "sarasa", "更纱黑体",
    "江城圆体", "站酷小薇体", "优设标题黑", "优设好身体",
    "MiSans", " HarmonyOS Sans SC",
}
# 系统里可能已装的免费 CJK 字体文件名（font_file 的系统回退顺序）
_SYSTEM_FREE_CANDIDATES = [
    "SourceHanSansSC-Regular.otf", "SourceHanSansCN-Regular.otf",
    "NotoSansSC-Regular.otf", "NotoSansCJKsc-Regular.otf",
    "MiSans-Regular.ttf", "AlibabaPuHuiTi-3-55-Regular.otf",
    "msyh.ttc",                       # 兜底：测量总比估算强（deck 字体名仍写 FONT_NAME）
]
# 系统里可能已装的免费拉丁字体文件名（latin_font_file 的系统回退顺序）
_SYSTEM_FREE_LATIN_CANDIDATES = [
    "NotoSans-Regular.ttf", "NotoSans-Bold.ttf",
    "NotoSans-Regular.otf", "NotoSans-Bold.otf",
    "NotoSansSC-Regular.otf",         # Noto Sans SC 也含拉丁字形，可作回退
]

# ---------------------------------------------------------------- 映射表
# 已知商业/需授权字体 → FONT_NAME（serif 系列降级为 sans，报告里会标注）
_SERIF_KEYS = {"simsun", "nsimsun", "simsunextb", "宋体", "宋体繁",
               "stsong", "华文宋体", "stzhongsong", "华文中宋",
               "simfang", "fangsong", "fangsonggb2312", "仿宋", "仿宋gb2312",
               "stfang", "华文仿宋", "simkai", "kaiti", "kaitigb2312", "楷体",
               "楷体gb2312", "stkaiti", "华文楷体"}
_NONFREE_MAP = {}
for _k in [
    # 微软系
    "microsoftyahei", "microsoftyaheiui", "msyh", "微软雅黑",
    "simhei", "黑体", "dengxian", "dengxianlight", "等线", "等线light",
    "arial", "arialblack", "arialunicode", "helvetica", "helveticaneue",
    "calibri", "calibrilight", "cambria", "cambriamath", "timesnewroman",
    "georgia", "verdana", "tahoma", "segoeui", "segoeuilight", "candara",
    "consolas", "constantia", "corbel", "franklingothic", "trebuchetms",
    "garamond", "bookantiqua", "centurygothic", "centuryschoolbook",
    "rockwell", "broadway", "brushscript", "pingfangsc", "pingfangtc",
    "pingfang hk", "sfpro", "sfns", "stheiti", "华文黑体", "stxihei", "华文细黑",
]:
    _NONFREE_MAP[_k] = FONT_NAME
for _k in list(_SERIF_KEYS):
    _NONFREE_MAP[_k] = FONT_NAME          # serif→sans 降级（许可证优先于字形风格）
# 方正 / 汉仪 等字库品牌：前缀命中即替换
_NONFREE_PREFIXES = ("方正", "founder", "汉仪", "hanyi", "汉仪字体", "typecraft",
                     "华康", "dynacomware", "文鼎", "威锋", "全真", "造字工房",
                     "字体管家", "新蒂", "sitongxiongdi")


def _norm(name):
    """规范化字体名：小写、去空白/下划线/连字符/点号，便于匹配。"""
    return re.sub(r"[\s_\-\.]+", "", str(name or "")).lower()


def is_cjk(ch):
    """判断字符是否属于东亚脚本（CJK + 中文常用标点），应用思源黑体度量。"""
    o = ord(ch)
    return o >= 0x2E80 or ch in "·—…“”‘’"


def font_file(bold=False):
    """随包思源黑体（CJK）文件路径；缺失时回退系统已装免费 CJK 字体。

    返回 None 表示无可用字体文件（调用方应走 em 估算兜底并告警）。
    """
    for p in ([_BOLD, _REGULAR] if bold else [_REGULAR]):
        if os.path.exists(p):
            return p
    fdir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    names = [n.replace("Regular", "Bold") if bold else n
             for n in _SYSTEM_FREE_CANDIDATES]
    for n in names:
        p = os.path.join(fdir, n)
        if os.path.exists(p):
            return p
    return None


def latin_font_file(bold=False):
    """随包 Noto Sans（拉丁）文件路径；缺失时回退系统已装免费拉丁字体。

    数字/字母用 Noto Sans 渲染（干净专业），与思源黑体同源设计、视觉一致。
    返回 None 时调用方回退到思源度量/字体（退化为全思源，仍免费）。
    """
    for p in ([_LATIN_BOLD, _LATIN_REGULAR] if bold else [_LATIN_REGULAR]):
        if os.path.exists(p):
            return p
    fdir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    names = [n.replace("Regular", "Bold") if bold else n
             for n in _SYSTEM_FREE_LATIN_CANDIDATES]
    for n in names:
        p = os.path.join(fdir, n)
        if os.path.exists(p):
            return p
    return None


def remap_typeface(name, tag=None, strict=True):
    """单个字体名 → (新名字, 原因)；返回 (None, 原因) 表示保留原字体。

    tag 为类型标签（"a:latin" / "a:ea" / "a:cs" / None）。拉丁类标签命中
    商业字体时映射为 FONT_NAME_LATIN（Noto Sans），东亚标签命中商业字体时
    映射为 FONT_NAME（思源黑体）。这样模板里 Arial/Calibri 这类拉丁字体仍
    回到免费拉丁字体，而不是被强行换成中文字体。

    strict=True 时，白名单外的未知字体也替换为对应脚本的免费字体（产品默认：
    对客户模板零字体授权风险）；strict=False 时保留并在报告里列出待审。
    """
    key = _norm(name)
    if not key:
        return None, "empty"
    if key in _FREE_FAMILIES or key in {"", "+mjlt", "+mnlt", "+mjea", "+mnea",
                                        "+mjcs", "+mncs"}:
        return None, "free"
    if key in _NONFREE_MAP:
        tgt = FONT_NAME_LATIN if tag in ("a:latin", "a:cs") else FONT_NAME
        return tgt, ("serif->sans" if key in _SERIF_KEYS else "nonfree")
    if any(key.startswith(_norm(p)) or key.startswith(p) for p in _NONFREE_PREFIXES):
        tgt = FONT_NAME_LATIN if tag in ("a:latin", "a:cs") else FONT_NAME
        return tgt, "nonfree-brand"
    # unknown
    tgt = FONT_NAME_LATIN if tag in ("a:latin", "a:cs") else FONT_NAME
    return (tgt, "unknown->default") if strict else (None, "unknown-kept")


# ---------------------------------------------------------------- 全 deck 接管
# 所有携带 typeface 属性的元素 → 它对应的脚本标签（remap_typeface 用）。
# 除 a:latin/a:ea/a:cs 外还有两类容易被漏掉的字体引用：
#   - a:buFont：项目符号字形（•/▪），模板母版里常写死 Arial；
#   - a:sym：符号字体（Wingdings 类）。
# 这两类只渲染符号/项目符号字符，按拉丁字体处理。
_TYPEFACE_TAGS = {
    qn("a:latin"): "a:latin",
    qn("a:ea"): "a:ea",
    qn("a:cs"): "a:cs",
    qn("a:buFont"): "a:latin",
    qn("a:sym"): "a:latin",
}
# el.tag 是 lxml 全限定名（{ns}latin），remap_typeface 收前缀名（"a:latin"）——
# 两边必须转换，否则 tag 比对永远落空、拉丁字体被错换成中文字体。
_FONT_SCHEME_TAGS = {qn("a:majorFont"), qn("a:minorFont")}
# fontScheme 里按脚本指定的回退字体：<a:font script="Hans" typeface="宋体"/>。
# 模板主题默认带一整串商业字体（宋体/ＭＳ Ｐゴシック/新細明細/Times...），
# 中文脚本映射到思源、其余映射到 Noto Sans——同样是授权风险点。
_CJK_SCRIPTS = {"hans", "hant", "jpan", "hang", "kore", "yiii", "hrkt"}


def _remap_element(root, strict, report):
    """就地重写一个部件 XML 里所有字体引用（latin/ea/cs/buFont/sym/script）。

    覆盖四类元素，缺一类就会把商业字体留在交付 deck 里：
      a:latin/a:ea/a:cs（run 级，按脚本分流）、a:buFont/a:sym（符号）、
      a:font script=...（主题按脚本回退）、空 a:ea（补思源）。
    """
    for el in root.iter():
        # ① 按脚本的回退字体（只出现在主题 fontScheme）
        if el.tag == qn("a:font") and el.get("script"):
            old = el.get("typeface")
            tag = ("a:ea" if _norm(el.get("script")) in _CJK_SCRIPTS
                   else "a:latin")
            new, _why = remap_typeface(old, tag=tag, strict=strict)
            if new:
                el.set("typeface", new)
                report["replaced"].setdefault(old or "(empty)",
                                              [new, 0])[1] += 1
            continue
        # ② run 级 / 符号级字体引用
        tname = _TYPEFACE_TAGS.get(el.tag)
        if tname is None:
            continue
        old = el.get("typeface")
        if not old or old.startswith("+"):        # 主题引用交给 theme 部件处理
            # 主题 fontScheme 里空的 a:ea 会让继承主题字体的中文落到查看器
            # 默认字体（宋体/等线，商业授权）——补成思源黑体 CN。
            if (el.tag == qn("a:ea") and not old and el.getparent() is not None
                    and el.getparent().tag in _FONT_SCHEME_TAGS):
                el.set("typeface", FONT_NAME)
                report["filled"].setdefault("(empty a:ea)",
                                            [FONT_NAME, 0])[1] += 1
            continue
        new, why = remap_typeface(old, tag=tname, strict=strict)
        if new:
            el.set("typeface", new)
            report["replaced"].setdefault(old, [new, 0])[1] += 1
            if why == "serif->sans":
                report["serif_demoted"].add(old)
        else:
            report["kept"].add(old)


def _remap_part(part, strict, report):
    """兼容两类部件：XmlPart（_element，就地改）与通用 Part（_blob，回写）。

    theme/notesMaster 等部件在 python-pptx 里是通用 Part——曾经只遍历
    _element 导致主题字体（Calibri）被静默跳过，deck 留下商业字体。
    """
    el = getattr(part, "_element", None)
    if el is not None:
        _remap_element(el, strict, report)
        return
    blob = getattr(part, "_blob", None)
    if blob is None or not part.partname.endswith(".xml"):
        return
    try:
        root = etree.fromstring(blob)
    except etree.XMLSyntaxError:
        return
    before = (len(report["replaced"]), len(report["kept"]),
              len(report["serif_demoted"]), len(report["filled"]))
    _remap_element(root, strict, report)
    after = (len(report["replaced"]), len(report["kept"]),
             len(report["serif_demoted"]), len(report["filled"]))
    if before != after:                       # 确有改动才回写，避免无谓序列化
        part._blob = etree.tostring(root, xml_declaration=True,
                                    encoding="UTF-8", standalone=True)


def remap_presentation(prs, strict=True):
    """把整个 deck（幻灯片/版式/母版/主题/备注/图表）的字体合规化。

    返回审计报告 {"replaced": {旧: [新, 次数]}, "kept": [...],
    "serif_demoted": [...], "filled": {描述: [新, 次数]}}。其中 filled
    记录主题里空 a:ea 被补成思源黑体的次数。幂等：对已合规的 deck 是 no-op。
    """
    report = {"replaced": {}, "kept": set(), "serif_demoted": set(),
              "filled": {}}
    for part in prs.part.package.iter_parts():
        try:
            _remap_part(part, strict, report)
        except (AttributeError, TypeError):
            continue
    report["kept"] = sorted(report["kept"])
    report["serif_demoted"] = sorted(report["serif_demoted"])
    return report


# ---------------------------------------------------------------- 字体嵌入
_CT_FONTDATA = ('<Default Extension="fntdata" '
                'ContentType="application/x-fontdata"/>')
_REL_FONT = ("http://schemas.openxmlformats.org/officeDocument"
             "/2006/relationships/font")
_P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _font_bundle():
    """返回需要嵌入的字体清单：{typeface, reg, bold} 列表（缺失项自动跳过）。"""
    return [
        {"typeface": FONT_NAME, "reg": font_file(False), "bold": font_file(True)},
        {"typeface": FONT_NAME_LATIN, "reg": latin_font_file(False),
         "bold": latin_font_file(True)},
    ]


def _font_parts():
    """(arcname, path, typeface, weight) for every bundled font file we embed."""
    parts = []
    idx = 1
    for b in _font_bundle():
        for weight in ("reg", "bold"):
            p = b.get(weight)
            if p:
                parts.append((f"ppt/fonts/font{idx}.fntdata", p,
                              b["typeface"], weight))
                idx += 1
    return parts


def _patch_content_types(data):
    root = etree.fromstring(data)
    if not any(d.get("Extension") == "fntdata" for d in root):
        root.append(etree.fromstring(_CT_FONTDATA))
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8",
                          standalone=True)


def _patch_presentation(data, embeds):
    root = etree.fromstring(data)
    if root.get("embedTrueTypeFonts") != "1":
        root.set("embedTrueTypeFonts", "1")
        root.set("saveSubsetFonts", "0")
    lst = root.find(f"{{{_P_NS}}}embeddedFontLst")
    if lst is None:
        lst = etree.SubElement(root, f"{{{_P_NS}}}embeddedFontLst")
    existing = {ef.get("typeface") for ef in lst}
    for e in embeds:
        if e["typeface"] in existing:
            continue
        ef = etree.SubElement(lst, f"{{{_P_NS}}}embeddedFont")
        f = etree.SubElement(ef, f"{{{_P_NS}}}font")
        f.set("typeface", e["typeface"])
        if e["rid_reg"]:
            etree.SubElement(ef, f"{{{_P_NS}}}regular").set(
                f"{{{_R_NS}}}id", e["rid_reg"])
        if e["rid_bold"]:
            etree.SubElement(ef, f"{{{_P_NS}}}bold").set(
                f"{{{_R_NS}}}id", e["rid_bold"])
    # CT_Presentation 元素序：... sldSz, notesSz, [smartTags],
    # embeddedFontLst, ..., defaultTextStyle —— 插到 notesSz 之后
    notes = root.find(f"{{{_P_NS}}}notesSz")
    if notes is not None:
        lst.getparent().remove(lst)
        notes.addnext(lst)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8",
                          standalone=True)


def _patch_rels(data, rels):
    root = etree.fromstring(data)
    existing = {r.get("Id") for r in root}
    for rid, target in rels:
        if rid and rid not in existing:
            rel = etree.SubElement(root, f"{{{_R_NS}}}Relationship")
            rel.set("Id", rid)
            rel.set("Type", _REL_FONT)
            rel.set("Target", target)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8",
                          standalone=True)


def embed_fonts(pptx_path, out_path=None):
    """把思源黑体 + Noto Sans（各 Regular/Bold）嵌入 pptx（opt-in）。

    嵌入后 PowerPoint / WPS 即使没装这些字体也能原样渲染（latin 用 Noto
    Sans、ea 用思源黑体，与 deck 写法一致）；文件约 +18MB。
    幂等：重复嵌入不会叠加部件。返回 {"embedded": bool, "parts": [...]}。
    """
    out_path = out_path or pptx_path
    parts = _font_parts()
    if not parts:
        return {"embedded": False, "parts": [],
                "warning": "bundled font files not found"}
    embeds = {}
    rels = []
    for arc, fp, tf, weight in parts:
        rid = "rIdFnt%d" % (len(rels) + 1)
        rels.append((rid, arc[4:]))   # arc 形如 ppt/fonts/fontN.fntdata
        d = embeds.setdefault(tf, {"typeface": tf, "rid_reg": None,
                                   "rid_bold": None})
        d["rid_reg" if weight == "reg" else "rid_bold"] = rid
    embeds = list(embeds.values())

    tmp = out_path + ".embed.tmp"
    with zipfile.ZipFile(pptx_path) as zin:
        names = set(zin.namelist())
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename == "[Content_Types].xml":
                    data = _patch_content_types(data)
                elif item.filename == "ppt/presentation.xml":
                    data = _patch_presentation(data, embeds)
                elif item.filename == "ppt/_rels/presentation.xml.rels":
                    data = _patch_rels(data, rels)
                zout.writestr(item, data)
            for arc, fp, tf, weight in parts:
                if arc not in names:
                    zout.write(fp, arc)
    shutil.move(tmp, out_path)
    return {"embedded": True, "parts": [a for a, *_ in parts]}
