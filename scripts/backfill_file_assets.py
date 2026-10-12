#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""file_assets 历史文件名补录脚本（file_assets 持久化上线后跑一次）

背景：file_assets 表上线前，上传/交付文件元数据只存 Redis（TTL 24h），过期后
磁盘自愈重建的 name 退化为存储名 file_xxx.ext，原始文件名丢失。本脚本从
两个数据源补录历史文件名：
  1. 磁盘扫描：storage/tenants/{tid}/{scene}/ 下存储名形态文件 upsert（name=磁盘名）
  2. 会话消息表（原始名，优先于磁盘名）：
     - chat_messages.metadata JSON：attachments[].{file_id,name}、
       images[].{file_id,display_name}、downloadableFiles[].{file_id,file_name}
       （tenant 通过 session_id JOIN chat_sessions 取）
     - channel_messages.attachments JSON：[].{file_id,name}（行内自带 tenant_id）

用法（容器内或宿主机项目根目录）：
    python3 scripts/backfill_file_assets.py --dry-run   # 默认，只统计不写库
    python3 scripts/backfill_file_assets.py --execute   # 实际写库

特性：幂等（asset_store.upsert_file_asset 的 name 保护：已有用户可见名不被
存储名/磁盘名覆盖；消息名在磁盘名之后写入，优先级最高）。重复执行安全。
"""

import argparse
import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 与 download_registry.STORAGE_FILE_NAME_RE 一致：file_{12位hex}
FILE_ID_RE = re.compile(r"^file_[0-9a-f]{12}$")

BATCH_SIZE = 500
SAMPLE_LIMIT = 20


def extract_names_from_message_metadata(metadata_raw) -> dict:
    """从 chat_messages.metadata JSON 文本提取 {file_id: 原始文件名}。

    纯函数，坏 JSON / 缺字段 / 非字典项均安全跳过。
    """
    names = {}
    if not metadata_raw:
        return names
    if isinstance(metadata_raw, bytes):
        metadata_raw = metadata_raw.decode("utf-8", errors="replace")
    try:
        meta = json.loads(metadata_raw) if isinstance(metadata_raw, str) else metadata_raw
    except (ValueError, TypeError):
        return names
    if not isinstance(meta, dict):
        return names

    def _collect(items, name_key):
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            fid = str(item.get("file_id") or "").strip()
            name = str(item.get(name_key) or "").strip()
            if fid and name and FILE_ID_RE.fullmatch(fid):
                names.setdefault(fid, name)

    _collect(meta.get("attachments"), "name")
    _collect(meta.get("images"), "display_name")
    _collect(meta.get("downloadableFiles"), "file_name")
    return names


def apply_message_names(names: dict, tenant_id, stats, sample, execute: bool) -> None:
    """把消息表提取的 {file_id: name} 补录进 DB（execute=True 才写）。

    existing/degraded 判断在 dry-run 与 execute 下一致执行（get_file_asset 为
    只读 SELECT），保证 dry-run 统计/样例与实际写库范围一致。
    name-only upsert：path/size 由 upsert COALESCE 保留。
    """
    from src.services.files.asset_store import get_file_asset, upsert_file_asset

    for fid, name in names.items():
        try:
            existing = get_file_asset(fid)
        except Exception:
            existing = None
        if existing is not None and not is_stored_name_degraded(existing.get("name")):
            continue
        stats["msg_pending"] += 1
        if len(sample) < SAMPLE_LIMIT:
            sample.append(("msg", tenant_id, fid, name))
        if execute:
            upsert_file_asset(
                {"file_id": fid, "name": name},
                tenant_id=tenant_id,
                source="msg_backfill",
            )
            stats["msg_fixed"] += 1


def extract_names_from_channel_attachments(attachments_raw) -> dict:
    """从 channel_messages.attachments JSON 文本提取 {file_id: 原始文件名}。"""
    names = {}
    if not attachments_raw:
        return names
    if isinstance(attachments_raw, bytes):
        attachments_raw = attachments_raw.decode("utf-8", errors="replace")
    try:
        items = json.loads(attachments_raw) if isinstance(attachments_raw, str) else attachments_raw
    except (ValueError, TypeError):
        return names
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            fid = str(item.get("file_id") or "").strip()
            name = str(item.get("name") or "").strip()
            if fid and name and FILE_ID_RE.fullmatch(fid):
                names.setdefault(fid, name)
    return names


def iter_storage_files(storage_root: Path):
    """遍历 storage/tenants/{tid}/{scene}/ 及 scene 下 1 层子目录中的存储名文件。

    与 src/core/storage.py find_uploaded_file_on_disk 的扫描口径一致。
    产出 (tenant_id, file_id, path)。
    """
    tenants_root = storage_root / "tenants"
    if not tenants_root.is_dir():
        return
    for tenant_dir in sorted(tenants_root.iterdir()):
        if not tenant_dir.is_dir():
            continue
        for scene_dir in sorted(tenant_dir.iterdir()):
            if not scene_dir.is_dir():
                continue
            search_dirs = [scene_dir]
            try:
                search_dirs.extend(d for d in sorted(scene_dir.iterdir()) if d.is_dir())
            except OSError:
                pass
            for d in search_dirs:
                try:
                    files = list(d.iterdir())
                except OSError:
                    continue
                for f in files:
                    if f.is_file() and FILE_ID_RE.fullmatch(f.stem):
                        yield tenant_dir.name, f.stem, f


def is_stored_name_degraded(name) -> bool:
    """DB 中已是用户可见名的视为已修复，无需被磁盘名覆盖。"""
    if not name:
        return True
    return bool(FILE_ID_RE.fullmatch(Path(str(name)).stem))


def main() -> int:
    parser = argparse.ArgumentParser(description="file_assets 历史文件名补录")
    parser.add_argument("--execute", action="store_true", help="实际写库（默认 dry-run）")
    parser.add_argument("--storage-root", default=str(PROJECT_ROOT / "storage"),
                        help="存储根目录（默认项目 storage/）")
    args = parser.parse_args()

    from src.db.database import init_postgres_pool
    from src.services.files.asset_store import get_file_asset, upsert_file_asset

    init_postgres_pool()
    mode = "EXECUTE" if args.execute else "DRY-RUN"
    print(f"[backfill] mode={mode}")

    stats = {"disk_scanned": 0, "disk_upserted": 0, "disk_pending": 0,
             "msg_rows_read": 0, "msg_names": 0, "msg_fixed": 0, "msg_pending": 0,
             "bad_json_rows": 0}
    sample = []

    # 1. 磁盘扫描 upsert（name=磁盘名；DB 已有用户可见名时跳过，样例与执行范围一致）
    for tenant_id, file_id, path in iter_storage_files(Path(args.storage_root)):
        stats["disk_scanned"] += 1
        try:
            existing = get_file_asset(file_id)
        except Exception:
            existing = None
        if existing is not None and not is_stored_name_degraded(existing.get("name")):
            continue
        stats["disk_pending"] += 1
        if len(sample) < SAMPLE_LIMIT:
            sample.append(("disk", tenant_id, file_id, path.name))
        if args.execute:
            from src.core.storage import _MIME_BY_SUFFIX
            upsert_file_asset(
                {
                    "file_id": file_id,
                    "name": path.name,
                    "path": str(path.absolute()),
                    "size": path.stat().st_size,
                    "mime_type": _MIME_BY_SUFFIX.get(path.suffix.lower(), "application/octet-stream"),
                    "type": "image" if path.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp") else "file",
                },
                tenant_id=tenant_id,
                source="disk_backfill",
            )
            stats["disk_upserted"] += 1
    print(f"[backfill] 磁盘扫描: {stats['disk_scanned']} 个存储名文件, "
          f"待补录 {stats['disk_pending']} 个"
          + (f", 已补录 {stats['disk_upserted']} 个" if args.execute else ""))

    # 2. 消息表原始名（最后写入，优先级最高——upsert 保护已修复的磁盘名，
    #    消息名只会覆盖"空名/存储名形态"，不会被磁盘名顶掉）
    try:
        from src.db.database import get_pooled_connection, return_pooled_connection

        conn = get_pooled_connection()
        try:
            with conn.cursor() as cur:
                # chat_messages：metadata 含附件引用；tenant 从 chat_sessions JOIN 取。
                # LIKE '%file\_%' 转义下划线（_ 在 LIKE 中是单字通配符），减少误匹配行
                cur.execute(
                    "SELECT m.id, m.metadata, s.tenant_id "
                    "FROM chat_messages m LEFT JOIN chat_sessions s "
                    "ON m.session_id = s.session_id "
                    "WHERE m.metadata LIKE %s ORDER BY m.id",
                    ('%file\\_%',),
                )
                while True:
                    rows = cur.fetchmany(BATCH_SIZE)
                    if not rows:
                        break
                    for _row_id, metadata_raw, tenant_id in rows:
                        stats["msg_rows_read"] += 1
                        names = extract_names_from_message_metadata(metadata_raw)
                        if not names:
                            stats["bad_json_rows"] += 1
                            continue
                        stats["msg_names"] += len(names)
                        apply_message_names(names, tenant_id, stats, sample, args.execute)

                # channel_messages：attachments JSON，行内自带 tenant_id
                cur.execute(
                    "SELECT id, tenant_id, attachments FROM channel_messages "
                    "WHERE attachments LIKE %s ORDER BY id",
                    ('%file\\_id%',),
                )
                while True:
                    rows = cur.fetchmany(BATCH_SIZE)
                    if not rows:
                        break
                    for _row_id, tenant_id, attachments_raw in rows:
                        stats["msg_rows_read"] += 1
                        names = extract_names_from_channel_attachments(attachments_raw)
                        if not names:
                            stats["bad_json_rows"] += 1
                            continue
                        stats["msg_names"] += len(names)
                        apply_message_names(names, tenant_id, stats, sample, args.execute)
        finally:
            return_pooled_connection(conn)
    except Exception as e:
        print(f"[backfill] 消息表补录异常（磁盘部分已完成）: {e}")
        import traceback
        traceback.print_exc()

    print(f"[backfill] 消息表: 读 {stats['msg_rows_read']} 行, "
          f"提取 {stats['msg_names']} 个文件名, "
          f"待补录 {stats['msg_pending']} 个"
          + (f", 已补录 {stats['msg_fixed']} 个" if args.execute else "")
          + f", 坏行/无文件名 {stats['bad_json_rows']} 行")
    print(f"[backfill] 样例（前 {SAMPLE_LIMIT}）:")
    for kind, tenant_id, fid, name in sample:
        print(f"  [{kind}] {tenant_id} {fid} -> {name}")
    if not args.execute:
        print("[backfill] dry-run 未写库；确认无误后加 --execute 重新执行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
