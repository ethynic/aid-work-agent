#!/usr/bin/env python3
"""pptx-vi-gen 技能 CLI 入口：模板 VI + content.json -> 成品 PPT。

用法：
  python scripts/generate.py --template 模板.pptx --content content.json --output 输出.pptx
  cat content.json | python scripts/generate.py --template 模板.pptx --content - --output 输出.pptx

stdout 输出 JSON 结果（供 LLM 解析），错误时 success=false。
"""
import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

PROJECT_ROOT = os.environ.get("PROJECT_ROOT") or str(Path(__file__).resolve().parents[4])

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pptx_gen.pipeline import generate_from_vi  # noqa: E402
from pptx_gen.render import RENDERERS  # noqa: E402
from pptx_gen.vi import default_vi, extract_vi  # noqa: E402


def _resolve_input_path(raw: str) -> Path:
    """解析输入文件路径：绝对路径优先，再按常见根目录查找。

    不做跨租户 glob（storage/tenants 下含其他租户会话附件，避免同名误读）。
    """
    p = Path(raw)
    if p.is_absolute():
        if p.exists():
            return p.resolve()
        raise FileNotFoundError(f"文件不存在: {raw}")
    for root in [Path.cwd(), Path(PROJECT_ROOT), Path(PROJECT_ROOT) / "test_uploads"]:
        candidate = root / raw
        if candidate.exists():
            return candidate.resolve()
    raise FileNotFoundError(
        f"文件不存在: {raw}（请传会话附件的完整路径）"
    )


def _load_content(spec: str) -> dict:
    if spec == "-":
        data = json.load(sys.stdin)
    else:
        path = _resolve_input_path(spec)
        data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("slides"), list):
        raise ValueError("content 格式错误：缺少 slides 数组")
    if not data["slides"]:
        raise ValueError("content 格式错误：slides 不能为空")
    for i, page in enumerate(data["slides"], 1):
        role = page.get("role")
        if role not in RENDERERS:
            raise ValueError(
                f"第 {i} 页 role 非法: {role!r}，可用: {', '.join(sorted(RENDERERS))}"
            )
        if role != "end" and not page.get("title"):
            raise ValueError(f"第 {i} 页（{role}）缺少 title")
        err = _validate_page_fields(i, role, page)
        if err:
            raise ValueError(err)
    return data


def _validate_page_fields(i, role, page):
    """按 role 校验必需字段，缺失时给出页码与字段指引（供 LLM 自纠）。"""
    def missing(field):
        return f"第 {i} 页（{role}）缺少必需字段 {field}"

    if role == "bullets":
        items = page.get("items")
        if not isinstance(items, list) or not items:
            return missing("items（非空数组，每项含 h/d）")
        for j, it in enumerate(items, 1):
            if not it.get("h"):
                return f"第 {i} 页（bullets）items 第 {j} 项缺少 h（小标题）"
    elif role == "stats":
        stats = page.get("stats")
        if not isinstance(stats, list) or not stats:
            return missing("stats（非空数组，每项含 num/label）")
        if len(stats) > 4:
            return f"第 {i} 页（stats）超过 4 张卡（当前 {len(stats)}），版式最多 4 张，请精简"
        for j, s in enumerate(stats, 1):
            if not s.get("num") or not s.get("label"):
                return f"第 {i} 页（stats）第 {j} 项缺少 num/label"
    elif role == "twocol":
        for side in ("left", "right"):
            col = page.get(side)
            if not isinstance(col, dict) or not col.get("h") or not isinstance(col.get("items"), list) or not col["items"]:
                return missing(f"{side}（含 h 和非空 items 数组）")
    return None


def _resolve_image_refs(content: dict) -> dict:
    """把每页 image 字段解析为绝对路径，交由 pipeline 加载。"""
    for page in content["slides"]:
        ref = page.get("image")
        if ref:
            page["image"] = str(_resolve_input_path(ref))
    return content


def main():
    parser = argparse.ArgumentParser(description="模板 VI + AI 内容 -> PPT")
    parser.add_argument("--template", help="客户 .pptx 模板路径（省略则用默认 VI）")
    parser.add_argument("--content", required=True,
                        help="content.json 路径，或 '-' 表示 stdin")
    parser.add_argument("--output", help="输出 .pptx 文件名（默认按时间戳生成）")
    parser.add_argument("--brand", help="强制品牌色，如 #1F4E79")
    parser.add_argument("--accent", help="强制强调色，如 #2E9CCA")
    parser.add_argument("--preview", action="store_true",
                        help="同时输出每页 PNG 预览到 <output>_preview/ 目录")
    args = parser.parse_args()

    try:
        content = _load_content(args.content)
        _resolve_image_refs(content)

        vi_override = {}
        if args.brand:
            vi_override["brand"] = args.brand
        if args.accent:
            vi_override["accent"] = args.accent

        template_path = _resolve_input_path(args.template) if args.template else None
        if template_path:
            vi = extract_vi(template_path)
        else:
            vi = default_vi()
        vi.update(vi_override)

        out_name = args.output or f"vi_ppt_{time.strftime('%Y%m%d-%H%M%S')}.pptx"
        # 只取文件名部分，防路径穿越写出执行工作目录
        out_name = Path(out_name).name
        if not out_name.lower().endswith(".pptx"):
            out_name += ".pptx"
        out_path = Path.cwd() / out_name

        base_dir = str(template_path.parent) if template_path else str(Path.cwd())
        result = generate_from_vi(str(template_path) if template_path else None,
                                  content, str(out_path),
                                  vi_override=vi_override or None, base_dir=base_dir)

        vi_info = {k: v for k, v in vi.items()
                   if k != "logo_img" and not k.startswith("font_")}
        vi_info["font_title"] = Path(vi["font_title"]).name if vi.get("font_title") else None
        vi_info["font_body"] = Path(vi["font_body"]).name if vi.get("font_body") else None
        vi_info["has_logo"] = vi.get("logo_img") is not None

        out = {
            "success": True,
            "pptx": str(out_path.resolve()),
            "slides": result["slides"],
            "vi": vi_info,
        }

        if args.preview:
            preview_dir = out_path.with_suffix("").resolve()
            preview_dir = Path(str(preview_dir) + "_preview")
            preview_dir.mkdir(parents=True, exist_ok=True)
            for png in sorted(Path(result["tmp"]).glob("slide_*.png")):
                shutil.copy(png, preview_dir / png.name)
            out["preview_dir"] = str(preview_dir)

        # 中间 PNG 不再需要（已合成进 pptx / 已拷贝预览），清理避免磁盘累积
        shutil.rmtree(result["tmp"], ignore_errors=True)

        print(json.dumps(out, ensure_ascii=False))
        return 0
    except Exception as e:
        print(json.dumps({"success": False, "error": str(e)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
