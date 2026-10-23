"""Dark-navy default theme for route 2 previews (QA only, never deck output).

render.py installs DARK_RENDERERS as the default RENDERERS after defining
the original light renderers (kept as render.LIGHT_RENDERERS). Palette is
resolved from the extracted VI (accent / brand / dark) with navy-gold
defaults as fallback, so vi_override can recolor a dark deck.

Every renderer here mirrors native.py's layout geometry parameter-for-
parameter (native.py is the deck source of truth; this module only paints
the same layout with Pillow for eyeball QA).
"""
W, H = 1920, 1080
MARGIN = 120
FOOTER_Y = 1000

INK = "#FFFFFF"       # headings on dark
BODY = "#E8F0F8"      # body text on dark
MUTED = "#A9C2DA"     # secondary text on dark
DARKTXT = "#0A1E33"   # text on gold fills
CYAN = "#35B8D9"      # fixed secondary accent
ONWHITE = "#5A6B7E"   # muted text inside white cards
CELLTXT = "#1F2937"   # table body text

DEFAULTS = {"gold": "#FFB545", "top": "#14395F", "bottom": "#0A1E33"}


def _pal(c):
    vi = c.vi
    return {
        "gold": vi.get("accent") or DEFAULTS["gold"],
        "top": vi.get("brand") or DEFAULTS["top"],
        "bottom": vi.get("dark") or DEFAULTS["bottom"],
    }


def _is_num(s):
    t = str(s).strip().replace(",", "").replace("¥", "").replace("$", "")
    if t.endswith("%"):
        t = t[:-1]
    if t.startswith("+"):
        t = t[1:]
    try:
        float(t)
        return True
    except ValueError:
        return False


def page_bg(c):
    """Deep navy gradient + soft glow circles for every content page."""
    p = _pal(c)
    c.vgrad(p["top"], p["bottom"])
    c.circle(c.w - 140, 80, 300, p["gold"], alpha=16)
    c.circle(30, c.h - 30, 240, CYAN, alpha=11)


def dark_header(c, title, lead=None):
    p = _pal(c)
    c.card([MARGIN, 96, MARGIN + 10, 182], p["gold"], radius=5)
    ts = c.fit_line_size(title, "title", 58, c.w - 2 * MARGIN - 40)
    c.text((MARGIN + 34, 92), title, ts, INK, kind="title")
    if lead:
        c.text((MARGIN + 34, 188), lead, 30, MUTED,
               max_w=c.w - 2 * MARGIN - 120)
        return 245
    return 202


def dark_footer(c, page, i, n):
    c.footer(page.get("org", ""), i, n, dark=True)


def _paste_fit(c, img, box):
    """Paste img into box [x1,y1,x2,y2] preserving aspect, centered."""
    iw, ih = box[2] - box[0], box[3] - box[1]
    r = min(iw / img.width, ih / img.height)
    nw, nh = int(img.width * r), int(img.height * r)
    im = img.resize((nw, nh))
    c.img.paste(im, (box[0] + (iw - nw) // 2, box[1] + (ih - nh) // 2),
                im if im.mode == "RGBA" else None)


def _img_card(c, box, img, pal):
    """White exhibit card with image fitted; placeholder when missing."""
    c.card(box, "#FFFFFF", radius=26, outline=pal["gold"],
           outline_alpha=110, width=2)
    if img is not None:
        _paste_fit(c, img, [box[0] + 20, box[1] + 20, box[2] - 20,
                            box[3] - 20])
    else:
        c.card([box[0] + 20, box[1] + 20, box[2] - 20, box[3] - 20],
               MUTED, alpha=40, radius=8)


# ------------------------------- renderers -------------------------------
def render_cover(c, p, i, n):
    pal = _pal(c)
    c.vgrad(pal["top"], pal["bottom"])
    c.circle(c.w - 130, 100, 330, pal["gold"], alpha=18)
    c.circle(c.w - 40, c.h - 40, 250, CYAN, alpha=12)
    c.circle(40, c.h - 220, 190, CYAN, alpha=8)
    c.card([MARGIN, 440, MARGIN + 150, 450], pal["gold"], radius=5)
    ts = c.fit_line_size(p["title"], "title", 92, c.w - 2 * MARGIN)
    c.text((MARGIN, 470), p["title"], ts, INK, kind="title")
    if p.get("subtitle"):
        ss = c.fit_line_size(p["subtitle"], "body", 40, c.w - 2 * MARGIN)
        c.text((MARGIN, 640), p["subtitle"], ss, "#CDDCEF")
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
        c.text((MARGIN, 856), meta, 30, MUTED, alpha=235)


def render_section(c, p, i, n):
    pal = _pal(c)
    c.vgrad(pal["top"], pal["bottom"])
    c.circle(c.w - 240, 220, 330, CYAN, alpha=12)
    c.circle(c.w + 40, 640, 240, pal["gold"], alpha=10)
    c.text((MARGIN, 230), p.get("no", f"{i:02d}"), 210, pal["gold"],
           kind="title", alpha=95)
    c.card([MARGIN, 500, MARGIN + 130, 510], pal["gold"], radius=5)
    ts = c.fit_line_size(p["title"], "title", 78, c.w - 2 * MARGIN - 200)
    c.text((MARGIN, 546), p["title"], ts, INK, kind="title")
    if p.get("lead"):
        c.text((MARGIN, 700), p["lead"], 34, MUTED,
               max_w=c.w - 2 * MARGIN - 240)
    dark_footer(c, p, i, n)


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
        hs = c.fit_line_size(it["h"], "title", 38, c.w - MARGIN - 96 - MARGIN)
        c.text((MARGIN + 96, y), it["h"], hs, INK, kind="title")
        c.text((MARGIN + 96, y + 56), it.get("d", ""), 28, MUTED,
               max_w=c.w - MARGIN - 96 - MARGIN, lh=1.35, fit=True,
               max_h=rh - 64)
        y += rh
    dark_footer(c, p, i, n)


def render_stats(c, p, i, n):
    pal = _pal(c)
    page_bg(c)
    top = dark_header(c, p["title"], p.get("lead"))
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
    y = top + 56 + max(0, (avail_bot - top - 56 - ch) // 2)
    x = MARGIN
    for s in stats:
        c.card([x, y, x + cw, y + ch], INK, alpha=12, radius=26,
               outline=INK, outline_alpha=30, width=2)
        c.card([x + 26, y + 30, x + 26 + 60, y + 36], pal["gold"], radius=3)
        ns = c.fit_line_size(s["num"], "title", 104, (cw - 52) * 0.92)
        c.text((x + 26, y + 74), s["num"], ns, pal["gold"], kind="title")
        ls = c.fit_line_size(s["label"], "title", 32, cw - 52)
        c.text((x + 26, y + 218), s["label"], ls, INK, kind="title")
        if s.get("desc"):
            c.text((x + 26, y + 274), s["desc"], 25, MUTED,
                   max_w=cw - 52, lh=1.3, fit=True, max_h=ch - 274 - 28)
        x += cw + gap
    dark_footer(c, p, i, n)


def render_table(c, p, i, n):
    pal = _pal(c)
    page_bg(c)
    top = dark_header(c, p["title"], p.get("lead"))
    head, rows = p["head"], p["rows"]
    ncol = len(head)
    area_x, area_w = MARGIN, c.w - 2 * MARGIN
    area_top = top + 24
    area_bot = FOOTER_Y - 80
    if p.get("caption"):
        area_bot -= 60
    nrow = len(rows) + 1
    rh = max(44, min(92, (area_bot - area_top) // nrow))
    weights = []
    for ci in range(ncol):
        m = c.d.textlength(str(head[ci]), font=c.font("title", 26))
        for r in rows:
            m = max(m, c.d.textlength(str(r[ci]), font=c.font("body", 24)))
        weights.append(m)
    tot = sum(weights) or 1
    col_w = [max(120, int(area_w * wgt / tot)) for wgt in weights]
    col_w[-1] += area_w - sum(col_w)
    x = area_x
    for ci in range(ncol):
        c.card([x, area_top, x + col_w[ci], area_top + rh + 6], pal["top"],
               radius=0)
        c.text((x + 14, area_top + rh // 2 - 16), str(head[ci]), 26,
               "#FFFFFF", kind="title", max_w=col_w[ci] - 28, anchor="lm")
        x += col_w[ci]
    for r, row in enumerate(rows):
        y = area_top + rh + 6 + r * rh
        if r % 2 == 1:
            c.card([area_x, y, area_x + area_w, y + rh], "#EDF2F9", radius=0)
        x = area_x
        for ci, val in enumerate(row):
            s = str(val)
            if _is_num(s):
                c.text((x + col_w[ci] - 14, y + rh // 2 - 14), s, 24, CELLTXT,
                       max_w=col_w[ci] - 28, anchor="rm")
            elif ci == 0:
                c.text((x + 14, y + rh // 2 - 14), s, 24, CELLTXT,
                       max_w=col_w[ci] - 28, anchor="lm")
            else:
                c.text((x + col_w[ci] // 2, y + rh // 2 - 14), s, 24, CELLTXT,
                       max_w=col_w[ci] - 28, anchor="mm")
            x += col_w[ci]
    c.card([area_x, area_top + rh + 6, area_x + area_w, area_top + rh + 10],
           pal["gold"], radius=2)
    if p.get("caption"):
        c.text((area_x, area_top + rh * nrow + 18), p["caption"], 24, MUTED,
               max_w=area_w)
    dark_footer(c, p, i, n)


def render_twocol(c, p, i, n):
    pal = _pal(c)
    page_bg(c)
    top = dark_header(c, p["title"], p.get("lead"))
    cols = [p["left"], p["right"]]
    gap = 48
    cw = (c.w - 2 * MARGIN - gap) // 2
    bottom = FOOTER_Y - 60
    strip = [CYAN, pal["gold"]]
    item_h = 92
    n_max = max(len(col["items"]) for col in cols)
    card_h = 150 + n_max * item_h + 36
    y0 = top + 12 + max(0, (bottom - top - 12 - card_h) // 2)
    for k, col in enumerate(cols):
        x = MARGIN + k * (cw + gap)
        c.card([x, y0, x + cw, y0 + card_h], INK, alpha=12, radius=26,
               outline=INK, outline_alpha=28, width=2)
        c.card([x, y0, x + cw, y0 + 10], strip[k], radius=6)
        ts = c.fit_line_size(col["h"], "title", 40, cw - 72)
        c.text((x + 36, y0 + 52), col["h"], ts, strip[k], kind="title")
        for j, it in enumerate(col["items"]):
            yy = y0 + 150 + j * item_h + (item_h - 44) // 2
            c.circle(x + 46, yy + 20, 7, strip[k])
            c.text((x + 70, yy), it, 28, BODY, max_w=cw - 106, lh=1.34,
                   fit=True, max_h=44)
    dark_footer(c, p, i, n)


def render_image(c, p, i, n):
    pal = _pal(c)
    page_bg(c)
    top = dark_header(c, p["title"], p.get("lead"))
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
               outline=pal["gold"], outline_alpha=120, width=2)
        im = img.resize((nw, nh))
        c.img.paste(im, (cx + (card_w - nw) // 2, cy + 28),
                    im if im.mode == "RGBA" else None)
        if p.get("caption"):
            c.text((cx + 28, cy + card_h - 62), p["caption"], 26, ONWHITE,
                   max_w=card_w - 56)
    else:
        c.card([MARGIN, box_top, c.w - MARGIN, box_bottom], "#FFFFFF",
               radius=26, outline=pal["gold"], outline_alpha=120, width=2)
        c.text((MARGIN + 28, box_top + avail_h // 2 - 24), "图片占位", 26,
               ONWHITE, max_w=c.w - 2 * MARGIN - 56)
    dark_footer(c, p, i, n)


def render_image_text(c, p, i, n):
    pal = _pal(c)
    page_bg(c)
    top = dark_header(c, p["title"], p.get("lead"))
    bottom = FOOTER_Y - 60
    gap = 56
    img_w = int((c.w - 2 * MARGIN - gap) * 0.52)
    txt_w = c.w - 2 * MARGIN - gap - img_w
    side = p.get("side", "left")
    ix = MARGIN if side == "left" else MARGIN + txt_w + gap
    tx = MARGIN if side == "right" else MARGIN + img_w + gap
    _img_card(c, [ix, top + 16, ix + img_w, bottom],
              (getattr(c, "_page_images", None) or [None])[0], pal)
    y = top + 44
    if p.get("h"):
        ts = c.fit_line_size(p["h"], "title", 40, txt_w)
        y = c.text((tx, y), p["h"], ts, INK, kind="title") + 14
    pts = p.get("points", [])
    chips = p.get("chips") or []
    area_bot = bottom - 84 if chips else bottom - 16
    slot = (area_bot - y) // max(1, len(pts))
    for j, pt in enumerate(pts):
        py = y + j * slot
        if isinstance(pt, dict):
            c.circle(tx + 10, py + 20, 7, pal["gold"])
            hs = c.fit_line_size(pt.get("h", ""), "title", 30, txt_w - 34)
            c.text((tx + 34, py), pt.get("h", ""), hs, INK, kind="title")
            if pt.get("d"):
                c.text((tx + 34, py + 58), pt["d"], 26, MUTED,
                       max_w=txt_w - 34, lh=1.35, fit=True, max_h=slot - 66)
        else:
            c.circle(tx + 10, py + 18, 7, pal["gold"])
            c.text((tx + 34, py), str(pt), 28, BODY, max_w=txt_w - 34,
                   lh=1.34, fit=True, max_h=slot - 16)
    dark_footer(c, p, i, n)


def render_image_grid(c, p, i, n):
    pal = _pal(c)
    page_bg(c)
    top = dark_header(c, p["title"], p.get("lead"))
    bottom = FOOTER_Y - 80
    if p.get("caption"):
        bottom -= 56
    imgs = p.get("images") or ([p["image"]] if p.get("image") else [])
    images = getattr(c, "_page_images", None) or []
    k = len(imgs)
    if k == 4:
        cols, rows_n = 2, 2
    elif k == 3:
        cols, rows_n = 3, 1
    elif k == 2:
        cols, rows_n = 2, 1
    else:
        cols, rows_n = 1, 1
    gap = 36
    cw = (c.w - 2 * MARGIN - gap * (cols - 1)) // cols
    ch = (bottom - top - gap * (rows_n - 1)) // rows_n
    for idx in range(cols * rows_n):
        r, cc = divmod(idx, cols)
        x = MARGIN + cc * (cw + gap)
        y = top + r * (ch + gap)
        _img_card(c, [x, y, x + cw, y + ch - 66],
                  images[idx] if idx < len(images) else None, pal)
        cap = imgs[idx] if idx < k else None
        cap = cap.get("caption", "") if isinstance(cap, dict) else ""
        if cap:
            c.text((x, y + ch - 58), cap, 24, MUTED, max_w=cw)
    if p.get("caption"):
        c.text((MARGIN, bottom + 8), p["caption"], 24, MUTED,
               max_w=c.w - 2 * MARGIN)
    dark_footer(c, p, i, n)


def render_timeline(c, p, i, n):
    pal = _pal(c)
    page_bg(c)
    top = dark_header(c, p["title"], p.get("lead"))
    items = p["items"]
    k = len(items)
    axis_y = top + 210
    c.card([MARGIN + 30, axis_y, c.w - MARGIN - 30, axis_y + 4], INK,
           alpha=100, radius=2)
    span = (c.w - 2 * MARGIN - 80) // k
    for idx, it in enumerate(items):
        cx = MARGIN + 40 + span * idx + span // 2
        bw = span - 28
        bx = cx - bw // 2
        ts = c.fit_line_size(it.get("t", ""), "title", 26, bw)
        c.text((bx, axis_y - 96), it.get("t", ""), ts, pal["gold"],
               kind="title", anchor="ma")
        c.circle(cx, axis_y + 2, 18, pal["gold"])
        c.circle(cx, axis_y + 2, 9, pal["top"])
        hs = c.fit_line_size(it.get("h", ""), "title", 32, bw)
        c.text((bx, axis_y + 44), it.get("h", ""), hs, INK, kind="title",
               anchor="ma")
        if it.get("d"):
            c.text((bx, axis_y + 108), it["d"], 22, MUTED, anchor="ma",
                   max_w=bw, lh=1.35, fit=True, max_h=120)
    dark_footer(c, p, i, n)


def render_process(c, p, i, n):
    pal = _pal(c)
    page_bg(c)
    top = dark_header(c, p["title"], p.get("lead"))
    avail_bot = FOOTER_Y - 90
    steps = p["steps"]
    k = len(steps)
    gap = 76
    cw = (c.w - 2 * MARGIN - gap * (k - 1)) // k
    ch = 390
    y = top + 30 + max(0, (avail_bot - top - 30 - ch) // 2)
    for idx, st in enumerate(steps):
        x = MARGIN + idx * (cw + gap)
        c.card([x, y, x + cw, y + ch], INK, alpha=12, radius=26,
               outline=INK, outline_alpha=50, width=2)
        c.card([x + 30, y + 34, x + 94, y + 98], pal["gold"], radius=32)
        c.center((x + 62, y + 66), f"{idx + 1}", 32, DARKTXT, kind="title")
        ts = c.fit_line_size(st.get("h", ""), "title", 34, cw - 60)
        c.text((x + 30, y + 126), st.get("h", ""), ts, INK, kind="title")
        if st.get("d"):
            c.text((x + 30, y + 196), st["d"], 26, MUTED, max_w=cw - 60,
                   lh=1.35, fit=True, max_h=ch - 226)
        if idx < k - 1:
            cx = x + cw + gap // 2
            c.card([cx - 16, y + ch // 2 - 14, cx + 16, y + ch // 2 + 14],
                   pal["gold"], radius=6)
    dark_footer(c, p, i, n)


def render_quote(c, p, i, n):
    pal = _pal(c)
    c.vgrad(pal["top"], pal["bottom"])
    c.circle(c.w - 130, 100, 330, pal["gold"], alpha=18)
    c.circle(140, c.h - 200, 200, CYAN, alpha=10)
    c.circle(c.w - 120, c.h - 280, 140, pal["gold"], alpha=12)
    c.text((MARGIN, 120), "\u201c", 200, pal["gold"], kind="title", alpha=90)
    c.text((MARGIN + 90, 330), p["quote"], 48, INK, lh=1.5,
           max_w=c.w - 2 * MARGIN - 180, fit=True, max_h=420)
    c.card([MARGIN + 90, 800, MARGIN + 210, 808], pal["gold"], radius=4)
    if p.get("author"):
        c.text((MARGIN + 90, 840), p["author"], 32, "#CDDCEF", kind="title")
    if p.get("title"):
        c.text((MARGIN + 90, 900), p["title"], 26, MUTED)


def render_toc(c, p, i, n):
    pal = _pal(c)
    page_bg(c)
    top = dark_header(c, p.get("title", "目录"), p.get("lead"))
    items = p["items"]
    bottom = FOOTER_Y - 70
    ncol = 2 if len(items) > 4 else 1
    col_w = (c.w - 2 * MARGIN - 80) // ncol
    rows_n = (len(items) + ncol - 1) // ncol
    rh = min(180, (bottom - top - 20) // max(1, rows_n))
    for idx, it in enumerate(items):
        cc, r = divmod(idx, rows_n)
        x = MARGIN + cc * (col_w + 80)
        y = top + 16 + r * rh
        h = it.get("h", "") if isinstance(it, dict) else str(it)
        d = it.get("d", "") if isinstance(it, dict) else ""
        c.text((x, y + 8), f"{idx + 1:02d}", 54, pal["gold"], kind="title")
        ts = c.fit_line_size(h, "title", 36, col_w - 150)
        c.text((x + 150, y + 16), h, ts, INK, kind="title")
        if d:
            c.text((x + 150, y + 78), d, 26, MUTED, max_w=col_w - 150,
                   lh=1.35, fit=True, max_h=rh - 96)
        c.card([x, y + rh - 14, x + col_w, y + rh - 13], INK, alpha=60,
               radius=1)
    dark_footer(c, p, i, n)


def render_end(c, p, i, n):
    pal = _pal(c)
    c.vgrad(pal["top"], pal["bottom"])
    # decorative circles stay clear of the centered text band (y 500~880)
    c.circle(c.w - 130, 100, 330, pal["gold"], alpha=18)
    c.circle(140, c.h - 200, 200, CYAN, alpha=10)
    c.circle(c.w - 120, c.h - 280, 140, pal["gold"], alpha=12)
    c.card([c.w // 2 - 75, 468, c.w // 2 + 75, 478], pal["gold"], radius=5)
    ts = c.fit_line_size(p.get("title", "谢谢观看"), "title", 84,
                         c.w - 2 * MARGIN)
    c.text((c.w // 2, 540), p.get("title", "谢谢观看"), ts, INK,
           kind="title", anchor="ma")
    if p.get("subtitle"):
        ss = c.fit_line_size(p["subtitle"], "body", 36, c.w - 2 * MARGIN)
        c.text((c.w // 2, 680), p["subtitle"], ss, "#CDDCEF", anchor="ma")
    if p.get("contact"):
        c.text((c.w // 2, 800), p["contact"], 30, MUTED, anchor="ma",
               max_w=c.w - 2 * MARGIN, alpha=230)


DARK_RENDERERS = {
    "cover": render_cover,
    "toc": render_toc,
    "section": render_section,
    "bullets": render_bullets,
    "stats": render_stats,
    "table": render_table,
    "twocol": render_twocol,
    "image": render_image,
    "image_text": render_image_text,
    "image_grid": render_image_grid,
    "timeline": render_timeline,
    "process": render_process,
    "quote": render_quote,
    "end": render_end,
}
