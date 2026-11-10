#!/usr/bin/env python3
"""校验投标书自动生成的四个输入文件是否存在且可读。

用法:
    python check_inputs.py <招标书要求文件路径> <历史标书路径> <我方资质路径> <我方产品及价格路径>

退出码 0 = 全部可读；2 = 存在缺失或不可读（会在 stderr 打印缺失清单）。
"""
import sys
import os

LABELS = [
    "甲方招标书要求",
    "历史标书（范例）",
    "我方资质信息",
    "我方产品信息及价格信息",
]


def check(path: str) -> tuple[bool, str]:
    if not path:
        return False, "路径为空"
    if not os.path.exists(path):
        return False, "文件不存在"
    if not os.path.isfile(path):
        return False, "不是普通文件"
    if os.path.getsize(path) == 0:
        return False, "文件为空"
    try:
        with open(path, "rb") as f:
            f.read(1)
        return True, "OK"
    except Exception as e:  # noqa: BLE001
        return False, f"不可读: {e}"


def main(argv):
    if len(argv) != 5:
        print("用法: python check_inputs.py <招标书> <历史标书> <我方资质> <我方产品及价格>", file=sys.stderr)
        return 2
    missing = []
    for label, p in zip(LABELS, argv[1:]):
        ok, msg = check(p)
        mark = "✓" if ok else "✗"
        print(f"  {mark} {label}: {p}  ({msg})")
        if not ok:
            missing.append((label, p, msg))
    if missing:
        print("\n缺失/不可读项:", file=sys.stderr)
        for label, p, msg in missing:
            print(f"  - {label}: {p} ({msg})", file=sys.stderr)
        return 2
    print("\n全部输入就绪。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
