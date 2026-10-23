"""End-to-end: template VI + content spec -> native editable .pptx.

All decks produced by this skill are native and fully editable: this entry
delegates to native.generate_native (identical signature and return shape).
The Pillow renderer (render.py / dark_theme.py) is kept for QA previews
only -- it never produces deck output.
"""
import os

from PIL import Image

from .native import generate_native
from .render import render as _render_page_png
from .vi import default_vi, extract_vi


def generate_from_vi(template_path, content, out_path, vi_override=None,
                     base_dir=None):
    """Route 2 entry -> native editable deck (see native.generate_native)."""
    return generate_native(template_path, content, out_path,
                           vi_override=vi_override, base_dir=base_dir)


def preview_pngs(template_path, content, out_dir, vi_override=None,
                 base_dir=None):
    """QA-only: render the content to 1920x1080 PNG previews.

    Previews approximate the native deck's layout for a quick eyeball check.
    They are NOT deck output -- decks are always native & editable.
    Returns the list of PNG paths.
    """
    base_dir = base_dir or (os.getcwd() if template_path is None
                            else os.path.dirname(os.path.abspath(template_path)))
    vi = default_vi() if template_path is None else extract_vi(template_path)
    if vi_override:
        vi.update(vi_override)
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    total = len(content["slides"])
    for i, page in enumerate(content["slides"], 1):
        img = None
        ref = page.get("image")
        if ref:
            p = ref if os.path.isabs(ref) else os.path.join(base_dir, ref)
            if os.path.exists(p):
                img = Image.open(p).convert("RGBA")
        canvas = _render_page_png(vi, page, i, total, page_image=img)
        png = os.path.join(out_dir, f"slide_{i:02d}.png")
        canvas.save(png)
        paths.append(png)
    return paths


if __name__ == "__main__":
    import json
    import sys
    tpl, spec, out = sys.argv[1], sys.argv[2], sys.argv[3]
    with open(spec, encoding="utf-8") as f:
        content = json.load(f)
    print(json.dumps(generate_from_vi(tpl, content, out),
                     ensure_ascii=False, indent=2))
