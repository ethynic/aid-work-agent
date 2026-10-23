"""Pillow page renderer: extracted VI + structured content -> 1920x1080 PNG.

Every slide is drawn from a small set of primitives so the whole deck shares
one visual language driven by the customer VI (colors / fonts / logo).

Light-theme preview renderers (legacy). The default preview theme is the
dark one in dark_theme.py (installed as RENDERERS at the end of this file),
which mirrors native.py's deck geometry parameter-for-parameter. Switch back:
    import pptx_gen.render as R
    R.RENDERERS = R.LIGHT_RENDERERS
Roles (dark theme, all 14): cover, toc, section, bullets, stats, table,
twocol, image, image_text, image_grid, timeline, process, quote, end.
"""
import os

from PIL import Image, ImageDraw, ImageFont

W, H = 1920, 1080
MARGIN = 120
FOOTER_Y = 1000


def _hex(c):
    if isinstance(c, str):
        c = c.lstrip("#")
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))
    return tuple(c[:3])


def _rgba(color, alpha=255):
    r, g, b = _hex(color)
    return (r, g, b, alpha)


class Canvas:
    def __init__(self, vi, w=W, h=H):
        self.vi = vi
        self.w, self.h = w, h
        self.img = Image.new("RGB", (w, h), _hex(vi.get("light", (245, 247, 250))))
        self.d = ImageDraw.Draw(self.img, "RGBA")
        self._fc = {}

    # ---- fonts ----
    def font(self, kind, size):
        key = (kind, size)
        if key not in self._fc:
            path = self.vi.get("font_title" if kind == "title" else "font_body")
            try:
                self._fc[key] = ImageFont.truetype(path, size)
            except Exception:
                self._fc[key] = ImageFont.load_default()
        return self._fc[key]

    # ---- backgrounds ----
    def _paste_grad(self, grad):
        self.img.paste(grad, (0, 0))
        self.d = ImageDraw.Draw(self.img, "RGBA")

    def vgrad(self, c1, c2):
        a, b = _hex(c1), _hex(c2)
        col = Image.new("RGB", (1, self.h))
        for y in range(self.h):
            t = y / (self.h - 1)
            col.putpixel((0, y), tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3)))
        self._paste_grad(col.resize((self.w, self.h)))

    def hgrad(self, c1, c2):
        a, b = _hex(c1), _hex(c2)
        col = Image.new("RGB", (self.w, 1))
        for x in range(self.w):
            t = x / (self.w - 1)
            col.putpixel((x, 0), tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3)))
        self._paste_grad(col.resize((self.w, self.h)))

    # ---- text ----
    def wrap(self, s, font, max_w):
        out = []
        for para in str(s).split("\n"):
            if para == "":
                out.append("")
                continue
            cur = ""
            for ch in para:
                if self.d.textlength(cur + ch, font=font) <= max_w or not cur:
                    cur += ch
                else:
                    out.append(cur)
                    cur = ch
            out.append(cur)
        return out

    def fit_line_size(self, s, kind, max_size, max_w):
        """Largest font size <= max_size whose single line fits within max_w."""
        size = max_size
        while size > 8:
            if self.d.textlength(str(s), font=self.font(kind, size)) <= max_w:
                return size
            size -= 2
        return 8

    def fit_size(self, s, kind, max_size, max_w, max_h, lh=1.35):
        """Largest font size <= max_size whose wrapped block fits max_w x max_h."""
        size = max_size
        for _ in range(60):
            if size < 8:
                size = 8
                break
            font = self.font(kind, size)
            lines = self.wrap(s, font, max_w) if max_w else [str(s)]
            if int(size * lh) * len(lines) <= max_h:
                return size
            size -= 2
        return max(size, 8)

    def text(self, xy, s, size, color, kind="body", max_w=None, lh=1.35,
             alpha=255, anchor="la", fit=False, max_h=None):
        x, y = xy
        if (fit or max_h) and max_w:
            size = self.fit_size(s, kind, size, max_w, max_h or 999999, lh)
        font = self.font(kind, size)
        lines = self.wrap(s, font, max_w) if max_w else str(s).split("\n")
        step = int(size * lh)
        for ln in lines:
            self.d.text((x, y), ln, font=font, fill=_rgba(color, alpha), anchor=anchor)
            y += step
        return y

    def center(self, xy, s, size, color, kind="body", alpha=255):
        self.d.text(xy, str(s), font=self.font(kind, size),
                    fill=_rgba(color, alpha), anchor="mm")

    # ---- shapes ----
    def card(self, box, color, alpha=255, radius=22, outline=None,
             outline_alpha=255, width=2):
        self.d.rounded_rectangle(
            box, radius=radius, fill=_rgba(color, alpha),
            outline=(_rgba(outline, outline_alpha) if outline else None), width=width)

    def circle(self, cx, cy, r, color, alpha=255):
        self.d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=_rgba(color, alpha))

    def logo(self, pos, height=None, width=None):
        lg = self.vi.get("logo_img")
        if lg is None:
            return
        if height and not width:
            width = int(lg.width * height / lg.height)
        elif width and not height:
            height = int(lg.height * width / lg.width)
        elif not height and not width:
            height, width = 80, int(lg.width * 80 / lg.height)
        lg2 = lg.resize((width, height))
        self.img.paste(lg2, pos, lg2 if lg2.mode == "RGBA" else None)

    def logo_badge(self, cx, cy, h=72, pad=22, bg_alpha=238, radius=16):
        """Logo on a light rounded plate so it stays visible on a dark bg."""
        lg = self.vi.get("logo_img")
        if lg is None:
            return
        w = int(lg.width * h / lg.height)
        bw, bh = w + 2 * pad, h + 2 * pad
        self.card([cx - bw // 2, cy - bh // 2, cx + bw // 2, cy + bh // 2],
                  "#FFFFFF", alpha=bg_alpha, radius=radius)
        self.logo((cx - w // 2, cy - h // 2), height=h)

    # ---- chrome ----
    def footer(self, left_text, page, total, dark=False):
        c = (255, 255, 255) if dark else self.vi.get("muted", "#6B7280")
        self.d.line([(MARGIN, FOOTER_Y - 26), (self.w - MARGIN, FOOTER_Y - 26)],
                    fill=_rgba(c, 55), width=1)
        self.text((MARGIN, FOOTER_Y), left_text, 24, c, alpha=150)
        self.text((self.w - MARGIN, FOOTER_Y), f"{page:02d} / {total:02d}",
                  24, c, anchor="ra", alpha=150)

    def header(self, title, lead=None, color=None):
        ink = color or self.vi.get("dark", "#10263D")
        self.card([MARGIN, 92, MARGIN + 10, 178], self.vi["accent"], radius=5)
        ts = self.fit_line_size(title, "title", 58, self.w - 2 * MARGIN - 40)
        self.text((MARGIN + 34, 92), title, ts, ink, kind="title")
        y = 186
        if lead:
            y = self.text((MARGIN + 34, 188), lead, 30, self.vi.get("muted", "#6B7280"),
                          max_w=self.w - 2 * MARGIN - 120)
        return y + 16

    def save(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.img.save(path)


# --------------------------- role renderers ---------------------------

def render_cover(c, p, i, n):
    vi = c.vi
    c.vgrad(vi["brand"], vi["dark"])
    c.circle(c.w - 150, 120, 300, vi["accent"], alpha=38)
    c.circle(c.w - 60, c.h - 40, 240, vi["accent"], alpha=26)
    c.logo_badge(MARGIN + 72, 150, h=60)
    c.card([MARGIN, 452, MARGIN + 150, 462], vi["accent"], radius=5)
    c.text((MARGIN, 494), p["title"], 92, "#FFFFFF", kind="title",
           max_w=c.w - 2 * MARGIN, lh=1.18)
    c.text((MARGIN, 660), p.get("subtitle", ""), 40, "#CDDCEF",
           max_w=c.w - 2 * MARGIN)
    meta = "   ·   ".join(x for x in (p.get("date"), p.get("org")) if x)
    if meta:
        c.text((MARGIN, 792), meta, 30, "#A9C2DA", alpha=235)


def render_section(c, p, i, n):
    c.card([0, 0, c.w, c.h], c.vi["light"])
    c.circle(c.w - 260, 240, 320, c.vi["accent"], alpha=26)
    c.text((MARGIN, 250), p.get("no", f"{i:02d}"), 210, c.vi["accent"],
           kind="title", alpha=95)
    c.card([MARGIN, 520, MARGIN + 130, 530], c.vi["accent"], radius=5)
    ts = c.fit_line_size(p["title"], "title", 78, c.w - 2 * MARGIN - 200)
    c.text((MARGIN, 566), p["title"], ts, c.vi["dark"], kind="title")
    if p.get("lead"):
        c.text((MARGIN, 720), p["lead"], 34, c.vi.get("muted", "#6B7280"),
               max_w=c.w - 2 * MARGIN - 240)
    c.footer(p.get("org", ""), i, n)


def render_bullets(c, p, i, n):
    top = c.header(p["title"], p.get("lead"))
    items = p["items"]
    avail = FOOTER_Y - 40 - top
    rh = min(168, avail // max(1, len(items)))
    y = top + 12
    for idx, it in enumerate(items):
        c.circle(MARGIN + 36, y + 42, 34, c.vi["accent"])
        c.center((MARGIN + 36, y + 42), f"{idx + 1}", 30, "#FFFFFF", kind="title")
        c.text((MARGIN + 96, y), it["h"], 38, c.vi["dark"], kind="title",
               max_w=c.w - MARGIN - 96 - MARGIN, fit=True, max_h=46)
        c.text((MARGIN + 96, y + 56), it.get("d", ""), 28,
               c.vi.get("muted", "#6B7280"),
               max_w=c.w - MARGIN - 96 - MARGIN, fit=True, max_h=rh - 64)
        y += rh
    c.footer(p.get("org", ""), i, n)


def render_stats(c, p, i, n):
    top = c.header(p["title"], p.get("lead"))
    stats = p["stats"]
    cols = min(len(stats), 4)
    gap = 36
    cw = (c.w - 2 * MARGIN - gap * (cols - 1)) // cols

    def _card_h(s):
        d = s.get("desc", "")
        d_h = 0
        if d:
            ds = c.fit_size(d, "body", 25, cw - 52, 130, 1.3)
            d_h = int(ds * 1.3) * len(c.wrap(d, c.font("body", ds), cw - 52))
        return int(274 + d_h + 34)

    ch = max(_card_h(s) for s in stats)
    avail_bot = FOOTER_Y - 60
    y = top + 40 + max(0, (avail_bot - top - 40 - ch) // 2)
    x = MARGIN
    for s in stats:
        c.card([x, y, x + cw, y + ch], "#FFFFFF", radius=26,
               outline=c.vi["accent"], outline_alpha=130, width=2)
        c.card([x + 26, y + 30, x + 26 + 60, y + 36], c.vi["accent"], radius=3)
        ns = c.fit_line_size(s["num"], "title", 104, cw - 52)
        c.text((x + 26, y + 74), s["num"], ns, c.vi["brand"], kind="title")
        c.text((x + 26, y + 218), s["label"], 32, c.vi["dark"], kind="title",
               max_w=cw - 52)
        if s.get("desc"):
            c.text((x + 26, y + 274), s["desc"], 25, c.vi.get("muted", "#6B7280"),
                   max_w=cw - 52, lh=1.3, fit=True, max_h=ch - 274 - 28)
        x += cw + gap
    c.footer(p.get("org", ""), i, n)


def render_twocol(c, p, i, n):
    top = c.header(p["title"], p.get("lead"))
    cols = [p["left"], p["right"]]
    gap = 48
    cw = (c.w - 2 * MARGIN - gap) // 2
    bottom = FOOTER_Y - 60
    strip = [c.vi["accent"], c.vi["accent"]]
    item_h = 92
    n_max = max(len(col["items"]) for col in cols)
    card_h = 150 + n_max * item_h + 36
    y0 = top + 12 + max(0, (bottom - top - 12 - card_h) // 2)
    for k, col in enumerate(cols):
        x = MARGIN + k * (cw + gap)
        c.card([x, y0, x + cw, y0 + card_h], "#FFFFFF", radius=26,
               outline=c.vi["accent"], outline_alpha=110, width=2)
        c.card([x, y0, x + cw, y0 + 10], c.vi["accent"], radius=6)
        c.text((x + 36, y0 + 52), col["h"], 40, c.vi["brand"], kind="title",
               max_w=cw - 72)
        for j, it in enumerate(col["items"]):
            yy = y0 + 150 + j * item_h + (item_h - 44) // 2
            c.circle(x + 46, yy + 20, 7, c.vi["accent"])
            c.text((x + 70, yy), it, 28, c.vi.get("ink", "#1A1A1A"),
                   max_w=cw - 106, lh=1.34, fit=True, max_h=44)
    c.footer(p.get("org", ""), i, n)


def render_image(c, p, i, n):
    top = c.header(p["title"], p.get("lead"))
    box_top, box_bottom = top + 16, FOOTER_Y - 90
    avail_h = box_bottom - box_top
    cap_h = 76 if p.get("caption") else 0
    img = getattr(c, "_page_image", None)
    if img is not None:
        max_iw, max_ih = c.w - 2 * MARGIN, avail_h - 56 - cap_h
        r = min(max_iw / img.width, max_ih / img.height)
        nw, nh = int(img.width * r), int(img.height * r)
        card_w = min(c.w - 2 * MARGIN, nw + 56)
        card_h = nh + 56 + cap_h
        cx = MARGIN + (c.w - 2 * MARGIN - card_w) // 2
        cy = box_top + max(0, (avail_h - card_h) // 2)
        c.card([cx, cy, cx + card_w, cy + card_h], "#FFFFFF", radius=26,
               outline=c.vi["accent"], outline_alpha=110, width=2)
        im = img.resize((nw, nh))
        c.img.paste(im, (cx + (card_w - nw) // 2, cy + 28),
                    im if im.mode == "RGBA" else None)
        if p.get("caption"):
            c.text((cx + 28, cy + card_h - 62), p["caption"], 26,
                   c.vi.get("muted", "#6B7280"), max_w=card_w - 56)
    else:
        c.card([MARGIN, box_top, c.w - MARGIN, box_bottom], "#FFFFFF",
               radius=26, outline=c.vi["accent"], outline_alpha=110, width=2)
        c.text((MARGIN + 28, box_top + avail_h // 2 - 24), "图片占位", 26,
               c.vi.get("muted", "#6B7280"), max_w=c.w - 2 * MARGIN - 56)
    c.footer(p.get("org", ""), i, n)


def render_end(c, p, i, n):
    vi = c.vi
    c.vgrad(vi["brand"], vi["dark"])
    c.circle(c.w // 2, 300, 320, vi["accent"], alpha=30)
    c.logo_badge(c.w // 2, 300, h=84)
    c.text((c.w // 2, 560), p.get("title", "谢谢观看"), 84, "#FFFFFF",
           kind="title", anchor="ma", max_w=c.w - 2 * MARGIN)
    if p.get("subtitle"):
        c.text((c.w // 2, 700), p["subtitle"], 36, "#CDDCEF", anchor="ma",
               max_w=c.w - 2 * MARGIN)
    if p.get("contact"):
        c.text((c.w // 2, 820), p["contact"], 30, "#A9C2DA", anchor="ma",
               max_w=c.w - 2 * MARGIN, alpha=230)


# 浅色备用版式（ legacy ）：默认不启用，切回方式见文件末尾
LIGHT_RENDERERS = {
    "cover": render_cover,
    "section": render_section,
    "bullets": render_bullets,
    "stats": render_stats,
    "twocol": render_twocol,
    "image": render_image,
    "end": render_end,
}


def render(vi, page, index, total, page_image=None, page_images=None):
    c = Canvas(vi)
    c._page_image = page_image
    c._page_images = page_images or []
    role = page["role"]
    if role not in RENDERERS:
        # graceful preview fallback so a new native role never blocks QA
        c.vgrad(vi["brand"], vi["dark"])
        c.text((MARGIN, 120), page.get("title", role), 58, "#FFFFFF",
               kind="title", max_w=c.w - 2 * MARGIN)
        c.text((MARGIN, 240), f"[preview N/A for role: {role}]", 30,
               "#A9C2DA")
        return c
    RENDERERS[role](c, page, index, total)
    return c


# ---- default preview theme: dark (mirrors native.py deck geometry) ----
from .dark_theme import DARK_RENDERERS  # noqa: E402

RENDERERS = DARK_RENDERERS
