"""End-to-end: template VI + content spec -> native editable .pptx.

All decks produced by this skill are native and fully editable: this entry
delegates to native.generate_native (identical signature and return shape).
The Pillow renderer (render.py / dark_theme.py) is kept for QA previews
only -- it never produces deck output.
"""
import os
import sys

from PIL import Image

from .native import generate_native
from .render import render as _render_page_png
from .vi import default_vi, extract_vi


def generate_from_vi(template_path, content, out_path, vi_override=None,
                     base_dir=None, remap_fonts=True, embed_fonts=False):
    """Route 2 entry -> native editable deck (see native.generate_native).

    Fonts: every run is written as the commercially-free Source Han Sans CN
    (SIL OFL 1.1). remap_fonts=True is an idempotent safety net; embed_fonts
    embeds the OTFs into the file (+17 MB) for machines without the font.
    """
    return generate_native(template_path, content, out_path,
                           vi_override=vi_override, base_dir=base_dir,
                           remap_fonts=remap_fonts,
                           embed_fonts=embed_fonts)


def preview_pngs(template_path, content, out_dir, vi_override=None,
                 base_dir=None):
    """QA-only: render the content to 1920x1080 PNG previews.

    Previews approximate the native deck's layout for a quick eyeball check.
    They are NOT deck output -- decks are always native & editable.
    Returns the list of PNG paths.
    """
    base_dir = base_dir or (os.getcwd() if template_path is None
                            else os.path.dirname(os.path.abspath(template_path)))
    # 无模板时走内置默认 VI（与 native.generate_native 口径一致）
    vi = default_vi() if template_path is None else extract_vi(template_path)
    if vi_override:
        vi.update(vi_override)
    # 预览必须与 deck 用同一份商用免费字体（assets/fonts/ 内的思源黑体 OTF
    # 与 Noto Sans TTF）：CJK→思源、拉丁→Noto Sans
    if not vi.get("font_title") or not vi.get("font_body"):
        from .fonts import font_file
        vi["font_title"] = vi.get("font_title") or font_file(bold=True)
        vi["font_body"] = vi.get("font_body") or font_file(bold=False)
    if not vi.get("font_latin_title") or not vi.get("font_latin_body"):
        from .fonts import latin_font_file
        vi["font_latin_title"] = (vi.get("font_latin_title")
                                  or latin_font_file(bold=True))
        vi["font_latin_body"] = (vi.get("font_latin_body")
                                 or latin_font_file(bold=False))
    # 这四个键必须是"存在的字体文件路径"——若上游塞入了字体名等非路径值，
    # 一律纠正为随包字体，避免预览回退成微型点阵字体
    from .fonts import font_file as _ff, latin_font_file as _lf
    for k, resolver in (("font_title", lambda: _ff(True)),
                        ("font_body", lambda: _ff(False)),
                        ("font_latin_title", lambda: _lf(True)),
                        ("font_latin_body", lambda: _lf(False))):
        v = vi.get(k)
        if not v or not os.path.exists(v):
            vi[k] = resolver()
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    missing = []
    total = len(content["slides"])
    for i, page in enumerate(content["slides"], 1):
        img = None
        ref = page.get("image")
        if ref:
            p = ref if os.path.isabs(ref) else os.path.join(base_dir, ref)
            if os.path.exists(p):
                img = Image.open(p).convert("RGBA")
            else:
                missing.append("slide %d: image %r" % (i, ref))
        # multi-image roles: page["images"] entries are paths or
        # {"src": ..., "caption": ...}
        imgs = []
        for k, r in enumerate(page.get("images") or ([] if not ref else [ref])):
            p = (r if isinstance(r, str) else r.get("src"))
            p = p if os.path.isabs(p) else os.path.join(base_dir, p)
            if p and os.path.exists(p):
                imgs.append(Image.open(p).convert("RGBA"))
            else:
                imgs.append(None)
                missing.append("slide %d: images[%d] %r" % (i, k, p))
        canvas = _render_page_png(vi, page, i, total, page_image=img,
                                  page_images=imgs)
        png = os.path.join(out_dir, f"slide_{i:02d}.png")
        canvas.save(png)
        paths.append(png)
    if missing:
        sys.stderr.write("WARNING: preview image(s) not found: %s\n"
                         % "; ".join(missing))
    return paths


if __name__ == "__main__":
    import json
    import sys
    tpl, spec, out = sys.argv[1], sys.argv[2], sys.argv[3]
    with open(spec, encoding="utf-8") as f:
        content = json.load(f)
    print(json.dumps(generate_from_vi(tpl, content, out),
                     ensure_ascii=False, indent=2))
