#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模板归位胶水脚本（我方加，非上游）：把 file_id / 已有路径的模板 .docx 复制到工作目录。

用法:
    python3 fetch_file.py --file-id file_xxx --dest <工作目录>/templates/
    python3 fetch_file.py --src /abs/path/模板.docx --dest <工作目录>/templates/

- --file-id：Redis uploaded_file:{file_id} 元数据解析（数字员工模板文件 / 会话附件），
  复用 src.core.storage.resolve_uploaded_file_path（仅 file_ 前缀，元数据 path 本就限定租户目录）。
- --src：已有磁盘路径（如 knowledge_file_search 返回的 file_path），fail-closed 校验必须位于
  storage/tenants/ 租户附件根下，防路径逃逸读取任意文件。
- stdout 只输出一行 JSON：{success, resolved_path, dest_path, name}。

安全边界（2026-11-11 跨租户路径防护口径一致）：
- file_id 走 Redis 元数据，元数据由上传侧写入，天然限定租户场景目录；
- --src 仅放行 realpath 位于 storage/tenants/ 下的文件，其余一律拒绝。
"""
import argparse
import json
import os
import shutil
import sys
from pathlib import Path

TENANTS_ROOT = Path("storage/tenants").resolve()


def _resolve_file_id(file_id: str) -> Path:
    project_root = os.environ.get("PROJECT_ROOT") or str(Path.cwd())
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from src.core.storage import resolve_uploaded_file_path

    resolved = resolve_uploaded_file_path(file_id)
    if not resolved or not Path(resolved).is_file():
        raise FileNotFoundError(f"file_id 无法解析或文件不存在: {file_id}")
    return Path(resolved).resolve()


def _validate_src(src: str) -> Path:
    p = Path(src).resolve()
    if TENANTS_ROOT not in p.parents:
        raise PermissionError(f"拒绝访问租户存储目录之外的路径: {src}")
    if not p.is_file():
        raise FileNotFoundError(f"文件不存在: {src}")
    return p


def main() -> int:
    parser = argparse.ArgumentParser(description="模板归位：file_id 或路径 -> 工作目录")
    parser.add_argument("--file-id", help="file_xxx 形式的文件 ID（数字员工模板文件/会话附件）")
    parser.add_argument("--src", help="已有磁盘路径（知识库 file_path 等，须在 storage/tenants/ 下）")
    parser.add_argument("--dest", required=True, help="目标目录（须已存在或可创建）")
    parser.add_argument("--name", help="落盘文件名（默认沿用源文件名）")
    args = parser.parse_args()

    if not args.file_id and not args.src:
        print(json.dumps({"success": False, "error": "必须提供 --file-id 或 --src 之一"}))
        return 2

    try:
        src_path = _resolve_file_id(args.file_id) if args.file_id else _validate_src(args.src)
        dest_dir = Path(args.dest).expanduser().resolve()
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / (args.name or src_path.name)
        shutil.copyfile(src_path, dest_path)
        print(json.dumps({
            "success": True,
            "resolved_path": str(src_path),
            "dest_path": str(dest_path),
            "name": dest_path.name,
        }, ensure_ascii=False))
        return 0
    except (FileNotFoundError, PermissionError) as e:
        print(json.dumps({"success": False, "error": str(e)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
