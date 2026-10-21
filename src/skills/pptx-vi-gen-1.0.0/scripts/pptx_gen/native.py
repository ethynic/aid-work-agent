"""Route 3: native editable PPTX generation.

Same content.json schema and VI palette as the Pillow renderer, but every
page is built from native PPTX shapes (text boxes / rounded rects / ovals /
pictures) laid out on a 1920x1080 grid (1 px = 6350 EMU, 1 px = 0.5 pt), so
all text stays fully editable in PowerPoint / WPS.

Public API:
    generate_native(template_path, content, out_path,
                    vi_override=None, base_dir=None)
        -> {"out": path, "slides": n}
"""
import os

from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Pt

from .vi import default_vi, extract_vi

EMU_PX = 6350                     # 12192000 EMU / 1920 px  (144 px per inch)
W, H = 1920, 1080
MARGIN = 120
FOOTER_Y = 1000
FONT = "微软雅黑"

INK, BODY, MUTED = "FFFFFF", "E8F0F8", "A9C2DA"
DARKTXT, CYAN = "0A1E33", "35B8D9"


# ---------------------------------------------------------------- helpers
def _px(v):
    return Emu(int(round(v * EMU_PX)))


def _pt(v):
    return Pt(v * 0.5)            # 144 px/in, 72 pt/in


def _hex(c):
    c = (c or "").lstrip("#").upper()
    return c if len(c) == 6 else "000000"


def _alpha(parent, pct):
    """Append <a:alpha> to the srgbClr inside parent's a:solidFill."""
    sf = parent.find(qn("a:solidFill"))
    if sf is None:
        return
    clr = sf.find(qn("a:srgbClr"))
    if clr is None:
        return
    clr.append(clr.makeelement(qn("a:alpha"), {"val": str(int(pct * 1000))}))


def _em(ch):
    """Rough glyph advance of `ch` in em units (Microsoft YaHei-ish)."""
    o = ord(ch)
    if o >= 0x2E80 or ch in "·—…“”‘’":
        return 1.0
    if ch.isdigit():
        return 0.55
    if ch in ".,:;'!|":
        return 0.30
    if ch == "%":
        return 0.87
    if ch in "+-":
        return 0.76
    if ch in "()[]{}<>/":
        return 0.38
    if ch == " ":
        return 0.28
    return 0.56


def est_w(s, size):
    return sum(_em(ch) for ch in str(s)) * size


def rect(slide, x, y, w, h, fill=None, alpha=None, radius=None,
         line=None, line_alpha=None, line_w=1.0):
    kind = MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE
    sp = slide.shapes.add_shape(kind, _px(x), _px(y), _px(w), _px(h))
    if radius:
        try:
            sp.adjustments[0] = min(0.5, float(radius))
        except Exception:
            pass
    if fill:
        sp.fill.solid()
        sp.fill.fore_color.rgb = RGBColor.from_string(_hex(fill))
        if alpha is not None:
            _alpha(sp._element.spPr, alpha)
    else:
        sp.fill.background()
    if line:
        sp.line.color.rgb = RGBColor.from_string(_hex(line))
        sp.line.width = Pt(line_w)
        if line_alpha is not None:
            ln = sp._element.spPr.find(qn("a:ln"))
            if ln is not None:
                _alpha(ln, line_alpha)
    else:
        sp.line.fill.background()
    sp.shadow.inherit = False
    return sp


def oval(slide, cx, cy, r, fill, alpha=None):
    sp = slide.shapes.add_shape(MSO_SHAPE.OVAL, _px(cx - r), _px(cy - r),
                                _px(2 * r), _px(2 * r))
    sp.fill.solid()
    sp.fill.fore_color.rgb = RGBColor.from_string(_hex(fill))
    if alpha is not None:
        _alpha(sp._element.spPr, alpha)
    sp.line.fill.background()
    sp.shadow.inherit = False
    return sp


def bg(slide, pal):
    """Full-bleed vertical gradient + soft glow circles."""
    g = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, _px(W), _px(H))
    try:
        g.fill.gradient()
        stops = g.fill.gradient_stops
        n = len(stops)
        for i in range(n):
            stops[i].color.rgb = RGBColor.from_string(
                _hex(pal["top"] if i == 0 else
                     pal["bottom"] if i == n - 1 else pal["top"]))
        # pin the angle via XML: python-pptx's gradient_angle setter writes
        # 270 deg for an input of 90; ang=5400000 (90 deg) puts stop 0 on top
        grad = g._element.spPr.find(qn("a:gradFill"))
        lin = grad.find(qn("a:lin")) if grad is not None else None
        if lin is not None:
            lin.set("ang", "5400000")
    except Exception:
        g.fill.solid()
        g.fill.fore_color.rgb = RGBColor.from_string(_hex(pal["bottom"]))
    g.line.fill.background()
    g.shadow.inherit = False
    oval(slide, W - 140, 80, 300, pal["gold"], 16)
    oval(slide, 30, H - 30, 240, CYAN, 11)
    return g


def _set_run(r, text, size, color, bold=False, alpha=None):
    r.text = text
    f = r.font
    f.size = _pt(size)
    f.bold = bold
    f.color.rgb = RGBColor.from_string(_hex(color))
    f.name = FONT                       # latin
    rPr = r._r.get_or_add_rPr()
    for tag in ("a:ea", "a:cs"):        # east-asian + complex scripts
        e = rPr.find(qn(tag))
        if e is None:
            e = rPr.makeelement(qn(tag), {})
            rPr.append(e)
        e.set("typeface", FONT)
    if alpha is not None:
        sf = rPr.find(qn("a:solidFill"))
        if sf is not None:
            clr = sf.find(qn("a:srgbClr"))
            if clr is not None:
                clr.append(clr.makeelement(qn("a:alpha"),
                                           {"val": str(int(alpha * 1000))}))


def _fill_tf(tf, s, size, color, bold, align, anchor, lh, alpha):
    tf.word_wrap = True
    try:
        tf.auto_size = MSO_AUTO_SIZE.NONE
    except Exception:
        pass
    tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    for i, ln in enumerate(str(s).split("\n")):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        try:
            p.line_spacing = max(0.5, lh / 1.2)   # PowerPoint single ~1.2em
        except Exception:
            pass
        _set_run(p.add_run(), ln, size, color, bold, alpha)


def text(slide, x, y, w, h, s, size, color, bold=False,
         align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, lh=1.35, alpha=None):
    tb = slide.shapes.add_textbox(_px(x), _px(y), _px(w), _px(h))
    _fill_tf(tb.text_frame, s, size, color, bold, align, anchor, lh, alpha)
    return tb


def shape_text(sp, s, size, color, bold=False, align=PP_ALIGN.CENTER,
               anchor=MSO_ANCHOR.MIDDLE, lh=1.0):
    """Text attached to an autoshape (moves with the shape when edited)."""
    _fill_tf(sp.text_frame, s, size, color, bold, align, anchor, lh, None)
    return sp


def header(slide, pal, title, lead=None):
    rect(slide, MARGIN, 96, 10, 86, fill=pal["gold"])
    text(slide, MARGIN + 34, 90, W - 2 * MARGIN - 40, 80, title, 58, INK,
         bold=True, lh=1.2)
    if lead:
        text(slide, MARGIN + 34, 188, W - 2 * MARGIN - 120, 60, lead, 30,
             MUTED, lh=1.35)
        return 245
    return 202


def footer(slide, page, i, n):
    rect(slide, MARGIN, FOOTER_Y - 26, W - 2 * MARGIN, 1, fill=INK, alpha=55)
    text(slide, MARGIN, FOOTER_Y, 900, 40, page.get("org", ""), 24, INK,
         alpha=150, lh=1.2)
    # named so editor.py can refresh page numbers after add/delete/move
    tb = text(slide, W - MARGIN - 220, FOOTER_Y, 220, 40, f"{i:02d} / {n:02d}",
              24, INK, alpha=150, align=PP_ALIGN.RIGHT, lh=1.2)
    tb.name = "pptxgen_pageno"


# ------------------------------------------------------------- renderers
def render_cover(slide, page, i, n, pal, img_path):
    bg(slide, pal)
    oval(slide, W - 130, 100, 330, pal["gold"], 18)
    oval(slide, W - 40, H - 40, 250, CYAN, 12)
    oval(slide, 40, H - 220, 190, CYAN, 8)
    rect(slide, MARGIN, 440, 150, 10, fill=pal["gold"])
    text(slide, MARGIN, 470, W - 2 * MARGIN, 160, page["title"], 92, INK,
         bold=True, lh=1.18)
    text(slide, MARGIN, 640, W - 2 * MARGIN, 64, page.get("subtitle", ""), 40,
         "CDDCEF", lh=1.3)
    # key-number chips (content-driven: page["chips"])
    x, y = MARGIN, 730
    for t in page.get("chips", []):
        tw = est_w(t, 28)
        cw = tw + 56
        chip = rect(slide, x, y, cw, 64, fill=INK, alpha=12, radius=0.5,
                    line=pal["gold"], line_alpha=80, line_w=1.0)
        shape_text(chip, t, 28, INK)
        x += cw + 24
    meta = "   ·   ".join(t for t in (page.get("date"), page.get("org")) if t)
    if meta:
        text(slide, MARGIN, 856, W - 2 * MARGIN, 44, meta, 30, MUTED,
             alpha=235, lh=1.3)


def render_section(slide, page, i, n, pal, img_path):
    bg(slide, pal)
    oval(slide, W - 240, 220, 330, CYAN, 12)
    oval(slide, W + 40, 640, 240, pal["gold"], 10)
    text(slide, MARGIN, 230, 900, 300, page.get("no", f"{i:02d}"), 210,
         pal["gold"], bold=True, lh=1.1)
    rect(slide, MARGIN, 500, 130, 10, fill=pal["gold"])
    text(slide, MARGIN, 546, W - 2 * MARGIN - 200, 120, page["title"], 78,
         INK, bold=True, lh=1.2)
    if page.get("lead"):
        text(slide, MARGIN, 700, W - 2 * MARGIN - 240, 70, page["lead"], 34,
             MUTED, lh=1.35)
    footer(slide, page, i, n)


def render_stats(slide, page, i, n, pal, img_path):
    bg(slide, pal)
    top = header(slide, pal, page["title"], page.get("lead"))
    stats = page["stats"]
    cols = min(len(stats), 4)
    gap = 36
    cw = (W - 2 * MARGIN - gap * (cols - 1)) // cols
    ch = 430
    y = top + 60
    x = MARGIN
    for s in stats:
        rect(slide, x, y, cw, ch, fill=INK, alpha=12, radius=26.0 / ch,
             line=INK, line_alpha=30, line_w=1.0)
        rect(slide, x + 26, y + 30, 60, 6, fill=pal["gold"])
        text(slide, x + 26, y + 74, cw - 52, 140, s["num"], 104, pal["gold"],
             bold=True, lh=1.15)
        text(slide, x + 26, y + 218, cw - 52, 50, s["label"], 32, INK,
             bold=True, lh=1.25)
        if s.get("desc"):
            text(slide, x + 26, y + 274, cw - 52, 130, s["desc"], 25, MUTED,
                 lh=1.3)
        x += cw + gap
    footer(slide, page, i, n)


def render_bullets(slide, page, i, n, pal, img_path):
    bg(slide, pal)
    top = header(slide, pal, page["title"], page.get("lead"))
    items = page["items"]
    avail = FOOTER_Y - 40 - top
    rh = min(190, avail // max(1, len(items)))
    y = top + 16
    for idx, it in enumerate(items):
        dot = oval(slide, MARGIN + 36, y + 42, 34, pal["gold"])
        shape_text(dot, str(idx + 1), 30, DARKTXT, bold=True)
        rect(slide, MARGIN + 80, y + 16, 3, rh - 52, fill=CYAN, alpha=55)
        text(slide, MARGIN + 96, y, W - MARGIN - 96 - MARGIN, 60, it["h"], 38,
             INK, bold=True, lh=1.25)
        text(slide, MARGIN + 96, y + 56, W - MARGIN - 96 - MARGIN, 110,
             it.get("d", ""), 28, MUTED, lh=1.35)
        y += rh
    footer(slide, page, i, n)


def render_twocol(slide, page, i, n, pal, img_path):
    bg(slide, pal)
    top = header(slide, pal, page["title"], page.get("lead"))
    cols = [page["left"], page["right"]]
    gap = 48
    cw = (W - 2 * MARGIN - gap) // 2
    bottom = FOOTER_Y - 60
    strip = [CYAN, pal["gold"]]
    for k, col in enumerate(cols):
        x = MARGIN + k * (cw + gap)
        rect(slide, x, top + 12, cw, bottom - (top + 12), fill=INK, alpha=12,
             radius=26.0 / (bottom - top - 12), line=INK, line_alpha=28,
             line_w=1.0)
        rect(slide, x, top + 12, cw, 10, fill=strip[k], radius=0.5)
        text(slide, x + 36, top + 52, cw - 72, 64, col["h"], 40, strip[k],
             bold=True, lh=1.25)
        items = col["items"]
        area_top, area_bot = top + 150, bottom - 44
        step = (area_bot - area_top) // max(1, len(items))
        for j, it in enumerate(items):
            yy = area_top + j * step + (step - 40) // 2
            oval(slide, x + 46, yy + 18, 7, strip[k])
            text(slide, x + 70, yy, cw - 106, 110, it, 28, BODY, lh=1.34)
    footer(slide, page, i, n)


def render_image(slide, page, i, n, pal, img_path):
    bg(slide, pal)
    top = header(slide, pal, page["title"], page.get("lead"))
    box_top = top + 16
    box_bottom = FOOTER_Y - 90
    card_h = box_bottom - box_top
    # white exhibit card so the source chart pops on the dark page
    rect(slide, MARGIN, box_top, W - 2 * MARGIN, card_h, fill="FFFFFF",
         radius=26.0 / card_h, line=pal["gold"], line_alpha=120, line_w=1.0)
    pad = 28
    inner_x, inner_y = MARGIN + pad, box_top + pad
    inner_w = W - 2 * MARGIN - 2 * pad
    inner_h = card_h - 2 * pad - 70
    if img_path and os.path.exists(img_path):
        img = Image.open(img_path)
        r = min(inner_w / img.width, inner_h / img.height)
        nw, nh = int(img.width * r), int(img.height * r)
        ox = inner_x + (inner_w - nw) // 2
        oy = inner_y + (inner_h - nh) // 2
        slide.shapes.add_picture(img_path, _px(ox), _px(oy), _px(nw), _px(nh))
    if page.get("caption"):
        text(slide, MARGIN + pad, box_bottom - 62, W - 2 * MARGIN - 2 * pad, 44,
             page["caption"], 26, "5A6B7E", lh=1.3)
    footer(slide, page, i, n)


def render_end(slide, page, i, n, pal, img_path):
    bg(slide, pal)
    oval(slide, W // 2, 280, 340, pal["gold"], 16)
    oval(slide, W // 2 + 220, 820, 220, CYAN, 12)
    rect(slide, W // 2 - 75, 468, 150, 10, fill=pal["gold"])
    text(slide, MARGIN, 540, W - 2 * MARGIN, 130, page.get("title", "谢谢观看"),
         84, INK, bold=True, align=PP_ALIGN.CENTER, lh=1.2)
    if page.get("subtitle"):
        text(slide, MARGIN, 680, W - 2 * MARGIN, 64, page["subtitle"], 36,
             "CDDCEF", align=PP_ALIGN.CENTER, lh=1.3)
    if page.get("contact"):
        text(slide, MARGIN, 800, W - 2 * MARGIN, 48, page["contact"], 30,
             MUTED, alpha=230, align=PP_ALIGN.CENTER, lh=1.3)


NATIVE_RENDERERS = {
    "cover": render_cover,
    "section": render_section,
    "bullets": render_bullets,
    "stats": render_stats,
    "twocol": render_twocol,
    "image": render_image,
    "end": render_end,
}


def add_page_slide(prs, page, pal, img_path=None):
    """Append one native slide built from a role spec (used by editor ops).

    Page numbers get refreshed by the editor after structural ops; pass
    "no" explicitly for section pages so the big number stays right.
    """
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    n = len(prs.slides._sldIdLst)
    NATIVE_RENDERERS[page["role"]](slide, page, n, n, pal, img_path)
    return slide


# ---------------------------------------------------------------- pipeline
def _palette(vi):
    return {
        "gold": (vi.get("accent") or "#FFB545").lstrip("#"),
        "top": (vi.get("brand") or "#14395F").lstrip("#"),
        "bottom": (vi.get("dark") or "#0A1E33").lstrip("#"),
    }


def generate_native(template_path, content, out_path, vi_override=None,
                    base_dir=None):
    """Route 3 entry: build a fully editable .pptx from structured content."""
    base_dir = base_dir or (os.getcwd() if template_path is None
                            else os.path.dirname(os.path.abspath(template_path)))
    vi = default_vi() if template_path is None else extract_vi(template_path)
    if vi_override:
        vi.update(vi_override)
    pal = _palette(vi)

    prs = Presentation()
    prs.slide_width = Emu(12192000)   # 16:9
    prs.slide_height = Emu(6858000)
    blank = prs.slide_layouts[6]

    slides = content["slides"]
    total = len(slides)
    for i, page in enumerate(slides, 1):
        slide = prs.slides.add_slide(blank)
        img_path = None
        ref = page.get("image")
        if ref:
            p = ref if os.path.isabs(ref) else os.path.join(base_dir, ref)
            if os.path.exists(p):
                img_path = p
        NATIVE_RENDERERS[page["role"]](slide, page, i, total, pal, img_path)

    try:
        prs.core_properties.title = slides[0].get("title", "PPT")
    except Exception:
        pass
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    prs.save(out_path)
    return {"out": out_path, "slides": total}
