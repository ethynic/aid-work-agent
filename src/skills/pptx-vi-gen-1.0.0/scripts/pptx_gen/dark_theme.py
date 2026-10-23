"""Dark-navy default theme for route 2 (VI render).

render.py imports DARK_RENDERERS from here and installs it as the skill
default after defining the original light renderers (which stay available
as render.LIGHT_RENDERERS). Palette is resolved from the extracted VI
(accent / brand / dark) with this deck's navy-gold defaults as fallback,
so vi_override can recolor a dark deck.
"""
W, H = 1920, 1080
MARGIN = 120
FOOTER_Y = 1000

INK = "#FFFFFF"       # headings on dark
BODY = "#E8F0F8"      # body text on dark
MUTED = "#A9C2DA"     # secondary text on dark
DARKTXT = "#0A1E33"   # text on gold fills
CYAN = "#35B8D9"      # fixed secondary accent (positive column, connectors)

DEFAULTS = {"gold": "#FFB545", "top": "#14395F", "bottom": "#0A1E33"}


def _pal(c):
    vi = c.vi
    return {
        "gold": vi.get("accent") or DEFAULTS["gold"],
        "top": vi.get("brand") or DEFAULTS["top"],
        "bottom": vi.get("dark") or DEFAULTS["bottom"],
    }


def page_bg(c):
    """Deep navy gradient + soft glow circles for every content page."""
    p = _pal(c)
    c.vgrad(p["top"], p["bottom"])
    c.circle(c.w - 140, 80, 300, p["gold"], alpha=16)
    c.circle(30, c.h - 30, 240, CYAN, alpha=11)


def dark_header(c, title, lead=None):
    p = _pal(c)
    c.card([MARGIN, 96, MARGIN + 10, 182], p["gold"], radius=5)
    c.text((MARGIN + 34, 92), title, 58, INK, kind="title",
           max_w=c.w - 2 * MARGIN - 40)
    if lead:
        return c.text((MARGIN + 34, 188), lead, 30, MUTED,
                      max_w=c.w - 2 * MARGIN - 120) + 16
    return 202


def render_cover(c, p, i, n):
    pal = _pal(c)
    c.vgrad(pal["top"], pal["bottom"])
    c.circle(c.w - 130, 100, 330, pal["gold"], alpha=18)
    c.circle(c.w - 40, c.h - 40, 250, CYAN, alpha=12)
    c.circle(40, c.h - 220, 190, CYAN, alpha=8)
    c.card([MARGIN, 440, MARGIN + 150, 450], pal["gold"], radius=5)
    c.text((MARGIN, 482), p["title"], 92, INK, kind="title",
           max_w=c.w - 2 * MARGIN, lh=1.18)
    c.text((MARGIN, 646), p.get("subtitle", ""), 40, "#CDDCEF",
           max_w=c.w - 2 * MARGIN)
    # key-number chips (content-driven: page["chips"] = ["...", ...])
    x, y = MARGIN, 730
    f = c.font("body", 28)
    for t in p.get("chips", []):
        tw = int(c.d.textlength(t, font=f))
        c.card([x, y, x + tw + 56, y + 64], INK, alpha=12, radius=32,
               outline=pal["gold"], outline_alpha=80, width=2)
        c.text((x + 28, y + 32), t, 28, INK, anchor="lm")
        x += tw + 56 + 24
    meta = "   ·   ".join(t for t in (p.get("date"), p.get("org")) if t)
    if meta:
        c.text((MARGIN, 862), meta, 30, MUTED, alpha=235)


def render_section(c, p, i, n):
    pal = _pal(c)
    c.vgrad(pal["top"], pal["bottom"])
    c.circle(c.w - 240, 220, 330, CYAN, alpha=12)
    c.circle(c.w + 40, 640, 240, pal["gold"], alpha=10)
    c.text((MARGIN, 236), p.get("no", f"{i:02d}"), 210, pal["gold"],
           kind="title", alpha=95)
    c.card([MARGIN, 500, MARGIN + 130, 510], pal["gold"], radius=5)
    c.text((MARGIN, 546), p["title"], 78, INK, kind="title",
           max_w=c.w - 2 * MARGIN - 200)
    if p.get("lead"):
        c.text((MARGIN, 700), p["lead"], 34, MUTED,
               max_w=c.w - 2 * MARGIN - 240)
    c.footer(p.get("org", ""), i, n, dark=True)


def render_stats(c, p, i, n):
    pal = _pal(c)
    page_bg(c)
    top = dark_header(c, p["title"], p.get("lead"))
    stats = p["stats"]
    cols = min(len(stats), 4)
    gap = 36
    cw = (c.w - 2 * MARGIN - gap * (cols - 1)) // cols
    ch = 430
    y = top + 60
    x = MARGIN
    for s in stats:
        c.card([x, y, x + cw, y + ch], INK, alpha=12, radius=26,
               outline=INK, outline_alpha=30, width=2)
        c.card([x + 26, y + 30, x + 26 + 60, y + 36], pal["gold"], radius=3)
        c.text((x + 26, y + 80), s["num"], 104, pal["gold"], kind="title",
               max_w=cw - 52)
        c.text((x + 26, y + 224), s["label"], 32, INK, kind="title",
               max_w=cw - 52)
        if s.get("desc"):
            c.text((x + 26, y + 280), s["desc"], 25, MUTED,
                   max_w=cw - 52, lh=1.3)
        x += cw + gap
    c.footer(p.get("org", ""), i, n, dark=True)


def render_bullets(c, p, i, n):
    pal = _pal(c)
    page_bg(c)
    top = dark_header(c, p["title"], p.get("lead"))
    items = p["items"]
    avail = FOOTER_Y - 40 - top
    rh = min(190, avail // max(1, len(items)))
    y = top + 16
    for idx, it in enumerate(items):
        c.circle(MARGIN + 36, y + 42, 34, pal["gold"])
        c.center((MARGIN + 36, y + 42), f"{idx + 1}", 30, DARKTXT, kind="title")
        c.card([MARGIN + 80, y + 16, MARGIN + 83, y + rh - 36], CYAN,
               alpha=55, radius=2)
        c.text((MARGIN + 96, y), it["h"], 38, INK, kind="title",
               max_w=c.w - MARGIN - 96 - MARGIN)
        c.text((MARGIN + 96, y + 56), it.get("d", ""), 28, MUTED,
               max_w=c.w - MARGIN - 96 - MARGIN)
        y += rh
    c.footer(p.get("org", ""), i, n, dark=True)


def render_twocol(c, p, i, n):
    pal = _pal(c)
    page_bg(c)
    top = dark_header(c, p["title"], p.get("lead"))
    cols = [p["left"], p["right"]]
    gap = 48
    cw = (c.w - 2 * MARGIN - gap) // 2
    bottom = FOOTER_Y - 60
    strip = [CYAN, pal["gold"]]
    for k, col in enumerate(cols):
        x = MARGIN + k * (cw + gap)
        c.card([x, top + 12, x + cw, bottom], INK, alpha=12, radius=26,
               outline=INK, outline_alpha=28, width=2)
        c.card([x, top + 12, x + cw, top + 22], strip[k], radius=6)
        c.text((x + 36, top + 58), col["h"], 40, strip[k], kind="title",
               max_w=cw - 72)
        items = col["items"]
        area_top, area_bot = top + 150, bottom - 44
        step = (area_bot - area_top) // max(1, len(items))
        for j, it in enumerate(items):
            yy = area_top + j * step + (step - 40) // 2
            c.circle(x + 46, yy + 18, 7, strip[k])
            c.text((x + 70, yy), it, 28, BODY, max_w=cw - 106, lh=1.34)
    c.footer(p.get("org", ""), i, n, dark=True)


def render_image(c, p, i, n):
    pal = _pal(c)
    page_bg(c)
    top = dark_header(c, p["title"], p.get("lead"))
    box = [MARGIN, top + 16, c.w - MARGIN, FOOTER_Y - 90]
    # white exhibit card so the source chart pops on the dark page
    c.card(box, "#FFFFFF", radius=26, outline=pal["gold"],
           outline_alpha=120, width=2)
    pad = 28
    inner = [box[0] + pad, box[1] + pad, box[2] - pad, box[3] - pad - 70]
    img = getattr(c, "_page_image", None)
    if img is not None:
        iw, ih = inner[2] - inner[0], inner[3] - inner[1]
        r = min(iw / img.width, ih / img.height)
        nw, nh = int(img.width * r), int(img.height * r)
        im = img.resize((nw, nh))
        ox = inner[0] + (iw - nw) // 2
        oy = inner[1] + (ih - nh) // 2
        c.img.paste(im, (ox, oy), im if im.mode == "RGBA" else None)
    if p.get("caption"):
        c.text((box[0] + pad, box[3] - 62), p["caption"], 26, "#5A6B7E",
               max_w=box[2] - box[0] - 2 * pad)
    c.footer(p.get("org", ""), i, n, dark=True)


def render_end(c, p, i, n):
    pal = _pal(c)
    c.vgrad(pal["top"], pal["bottom"])
    c.circle(c.w // 2, 280, 340, pal["gold"], alpha=16)
    c.circle(c.w // 2 + 220, 820, 220, CYAN, alpha=12)
    c.card([c.w // 2 - 75, 468, c.w // 2 + 75, 478], pal["gold"], radius=5)
    c.text((c.w // 2, 540), p.get("title", "谢谢观看"), 84, INK,
           kind="title", anchor="ma", max_w=c.w - 2 * MARGIN)
    if p.get("subtitle"):
        c.text((c.w // 2, 680), p["subtitle"], 36, "#CDDCEF", anchor="ma",
               max_w=c.w - 2 * MARGIN)
    if p.get("contact"):
        c.text((c.w // 2, 800), p["contact"], 30, MUTED, anchor="ma",
               max_w=c.w - 2 * MARGIN, alpha=230)


DARK_RENDERERS = {
    "cover": render_cover,
    "section": render_section,
    "bullets": render_bullets,
    "stats": render_stats,
    "twocol": render_twocol,
    "image": render_image,
    "end": render_end,
}
