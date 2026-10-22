#!/usr/bin/env python3
"""pptx-vi-gen 技能 CLI：对已生成的 deck 做手术式修改（edit_deck）。

用法：
  python scripts/edit.py --pptx 输出.pptx --spec edit_spec.json
  cat edit_spec.json | python scripts/edit.py --pptx 输出.pptx --spec -

edit_spec 见 references/usage.md 第 7 节；缺省原地修改（自动留 .bak 备份），
--output 指定新路径时原文件不动。
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
from pptx_gen.editor import edit_deck  # noqa: E402


def _load_spec(spec: str) -> dict:
    if spec == "-":
        data = json.load(sys.stdin)
    else:
        path = _resolve_input_path(spec)
        data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("ops"), list) or not data["ops"]:
        raise ValueError("edit_spec 格式错误：缺少非空 ops 数组")
    return data


def main():
    parser = argparse.ArgumentParser(description="对已生成 deck 做手术式修改")
    parser.add_argument("--pptx", required=True, help="目标 .pptx 路径")
    parser.add_argument("--spec", required=True,
                        help="edit_spec.json 路径，或 '-' 表示 stdin")
    parser.add_argument("--output", help="输出路径（缺省原地修改，自动留 .bak）")
    args = parser.parse_args()

    try:
        spec = _load_spec(args.spec)
        deck_path = str(_resolve_input_path(args.pptx))
        out_path = None
        if args.output:
            # 只取文件名部分，防路径穿越写出执行工作目录（与 generate.py 同口径）
            out_name = Path(args.output).name
            if not out_name.lower().endswith(".pptx"):
                out_name += ".pptx"
            out_path = str(Path.cwd() / out_name)
        report = edit_deck(deck_path, spec, out_path=out_path)
        print(json.dumps({
            "success": True,
            "deck": report["out"],
            "slides": report["slides"],
            "applied": report["applied"],
            "warnings": report["warnings"],
        }, ensure_ascii=False))
        return 0
    except Exception as e:
        print(json.dumps({"success": False, "error": str(e)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
