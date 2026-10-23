#!/usr/bin/env python3
"""pptx-vi-gen 技能 CLI：路线一，模板占位符填充 / 整页拷贝替换。

用法：
  python scripts/template_fill.py analyze --template 模板.pptx
  python scripts/template_fill.py generate --template 模板.pptx --spec deck_spec.json --output 输出.pptx

analyze 解析模板版式/占位符/每页文本（JSON），供 LLM 决策 deck_spec；
generate 按 deck_spec 生成（占位符填充与整页拷贝替换可混用）。
stdout 输出 JSON 结果（供 LLM 解析），错误时 success=false。
"""
import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = os.environ.get("PROJECT_ROOT") or str(Path(__file__).resolve().parents[4])

sys.path.insert(0, str(Path(__file__).resolve().parent))

from generate import _resolve_input_path  # noqa: E402
from pptx_gen.generator import generate  # noqa: E402
from pptx_gen.template_analyzer import analyze  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="路线一：模板占位符填充")
    sub = parser.add_subparsers(dest="command", required=True)

    p_analyze = sub.add_parser("analyze", help="解析模板版式/占位符/每页文本")
    p_analyze.add_argument("--template", required=True, help="模板 .pptx 路径")

    p_gen = sub.add_parser("generate", help="按 deck_spec 生成 PPT")
    p_gen.add_argument("--template", required=True, help="模板 .pptx 路径")
    p_gen.add_argument("--spec", required=True,
                       help="deck_spec.json 路径，或 '-' 表示 stdin")
    p_gen.add_argument("--output", required=True, help="输出 .pptx 路径")
    p_gen.add_argument("--keep-template-slides", action="store_true",
                       help="保留原模板页（默认删除，只留生成页）")

    args = parser.parse_args()

    try:
        template_path = str(_resolve_input_path(args.template))
        if args.command == "analyze":
            result = analyze(template_path)
            print(json.dumps({"success": True, "template": template_path,
                              "analysis": result}, ensure_ascii=False))
            return 0

        if args.spec == "-":
            deck_spec = json.load(sys.stdin)
        else:
            spec_path = _resolve_input_path(args.spec)
            deck_spec = json.loads(spec_path.read_text(encoding="utf-8"))
        if not isinstance(deck_spec, dict) or not isinstance(deck_spec.get("slides"), list) \
                or not deck_spec["slides"]:
            raise ValueError("deck_spec 格式错误：缺少非空 slides 数组")

        # 只取文件名部分，防路径穿越写出执行工作目录（与 generate.py 同口径）
        out_name = Path(args.output).name
        if not out_name.lower().endswith(".pptx"):
            out_name += ".pptx"
        out_path = Path.cwd() / out_name
        result = generate(template_path, deck_spec, str(out_path))
        print(json.dumps({
            "success": True,
            "pptx": result["out"],
            "generated": result["generated"],
            "detail": result["slides"],
        }, ensure_ascii=False))
        return 0
    except Exception as e:
        print(json.dumps({"success": False, "error": str(e)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
