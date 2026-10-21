"""End-to-end: template VI + content spec -> rendered .pptx.

generate_from_vi(template_path, content, out_path, vi_override=None)

  content = {"slides": [ {role, ...}, ... ]}
  vi_override = partial VI dict to force brand/accent/... (demo / brand lock)
"""
import os
import tempfile

from PIL import Image

from .compose import compose
from .render import render
from .vi import default_vi, extract_vi


def _load_page_image(page, base_dir):
    ref = page.get("image")
    if not ref:
        return None
    path = ref if os.path.isabs(ref) else os.path.join(base_dir, ref)
    if not os.path.exists(path):
        return None
    return Image.open(path).convert("RGBA")


def generate_from_vi(template_path, content, out_path, vi_override=None, base_dir=None):
    if template_path:
        base_dir = base_dir or os.path.dirname(os.path.abspath(template_path))
        vi = extract_vi(template_path)
    else:
        # 无模板时用默认 VI（可再被 vi_override 覆盖）
        base_dir = base_dir or os.getcwd()
        vi = default_vi()
    if vi_override:
        vi.update(vi_override)

    slides = content["slides"]
    total = len(slides)
    tmp = tempfile.mkdtemp(prefix="vigen_")
    png_paths = []
    for i, page in enumerate(slides, 1):
        page_image = _load_page_image(page, base_dir)
        canvas = render(vi, page, i, total, page_image=page_image)
        png = os.path.join(tmp, f"slide_{i:02d}.png")
        canvas.save(png)
        png_paths.append(png)

    return {"out": compose(png_paths, out_path), "slides": total, "tmp": tmp}


if __name__ == "__main__":
    import json
    import sys
    tpl, spec, out = sys.argv[1], sys.argv[2], sys.argv[3]
    with open(spec, encoding="utf-8") as f:
        content = json.load(f)
    print(json.dumps(generate_from_vi(tpl, content, out),
                     ensure_ascii=False, indent=2))
