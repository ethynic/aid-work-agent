#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""存量文件存储规范迁移脚本（方案见 docs/plans/plan-storage-legacy-migration.md）

在服务器宿主机直接运行（仅依赖标准库，兼容 Python 3.6）。

用法：
    python3 migrate_storage_legacy.py --env test \
        --storage-root /var/www/qb3_upload/agent2_storage --dry-run
    python3 migrate_storage_legacy.py --env test \
        --storage-root /var/www/qb3_upload/agent2_storage

三类操作：
1. cp 合并（源保留 30 天回滚）：storage/tenants/{tid}/ 嵌套、
   uploads/tenant_{tid}/ 各子目录 -> tenants/{tid}/{scene}/
   - 目标同名且内容一致：跳过
   - 目标同名但内容不同：复制为 {stem}_legacy{N}{suffix}，不丢数据
2. mv 归档（归档目录 = 存储根同级 *_legacy_YYYYMMDD/）：
   analysis_charts / analysis_data / output / uploads/wecom_kf /
   uploads/wecom / uploads/dingtalk / memories / subagents2
3. 清理：uploads/conversation、uploads/knowledge 为空则删；
   全部为空的残留目录 rmdir（只删空目录，永不递归删非空）

特性：幂等（重复执行安全）、--dry-run 只打印计划不落盘、
日志写 {存储根父目录}/log/temp/storage_migrate_{env}.log
"""

import argparse
import hashlib
import os
import shutil
import sys
from datetime import datetime

# mv 归档对象（相对存储根的路径）
MV_TARGETS = [
    "analysis_charts",
    "analysis_data",
    "output",
    "uploads/wecom_kf",
    "uploads/wecom",
    "uploads/dingtalk",
    "memories",
    "subagents2",
]

# 为空则删除的对象（相对存储根）
DELETE_IF_EMPTY = [
    "uploads/conversation",
    "uploads/knowledge",
]

# uploads/tenant_{tid}/ 下的子目录 -> 目标场景映射
SCENE_MAP = {
    "knowledge": "knowledge",
    "templates": "templates",
    "data_sources": "data_sources",
    "user_xxx": "conversation",   # user_{uid} 目录
    "other": "conversation",      # uuid 等未识别目录 / 散文件
}

LOG = []


def log(msg, force_print=True):
    LOG.append(msg)
    if force_print:
        print(msg)


def normalize_tenant_id(tid):
    """剥 tenant_ 前缀，与 src/core/storage.py 的 normalize_tenant_id 对齐"""
    if tid and tid.startswith("tenant_"):
        return tid[len("tenant_"):]
    return tid


def file_md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_dir(path, dry_run):
    if not os.path.isdir(path):
        if dry_run:
            log("  [mkdir] {}".format(path), force_print=_fp())
        else:
            os.makedirs(path, exist_ok=True)


def copy_file_no_overwrite(src, dst, dry_run, counters):
    """复制单文件：同名同内容跳过，同名不同内容加 _legacy{N} 后缀"""
    if not os.path.exists(dst):
        if not dry_run:
            ensure_dir(os.path.dirname(dst), dry_run)
            shutil.copy2(src, dst)
        counters["copied"] += 1
        log("  [cp] {} -> {}".format(_rel(src), _rel(dst)), force_print=_fp())
        return
    if file_md5(src) == file_md5(dst):
        counters["skipped_same"] += 1
        return
    base, ext = os.path.splitext(dst)
    n = 1
    while os.path.exists("{}_legacy{}{}".format(base, n, ext)):
        if file_md5(src) == file_md5("{}_legacy{}{}".format(base, n, ext)):
            counters["skipped_same"] += 1
            return
        n += 1
    alt = "{}_legacy{}{}".format(base, n, ext)
    if not dry_run:
        shutil.copy2(src, alt)
    counters["copied_renamed"] += 1
    log("  [cp-rename] {} -> {}".format(_rel(src), _rel(alt)), force_print=_fp())


def _rel(path):
    """路径去 /var/www/qb3_upload 级别的前缀，便于日志阅读"""
    parts = path.split(os.sep)
    if len(parts) > 3:
        return os.sep.join(parts[-3:])
    return path


def cp_tree_merge(src_root, dst_root, dry_run, counters):
    """递归 cp 合并：目录结构保持，单文件按不覆盖策略处理"""
    for root, dirs, files in os.walk(src_root):
        rel = os.path.relpath(root, src_root)
        target_dir = os.path.join(dst_root, rel) if rel != "." else dst_root
        ensure_dir(target_dir, dry_run)
        for name in files:
            copy_file_no_overwrite(
                os.path.join(root, name), os.path.join(target_dir, name), dry_run, counters
            )


def mv_to_archive(src, archive_root, dry_run, counters):
    """mv 到归档目录；跨设备时 copy+unlink 兜底；已归档则跳过"""
    if not os.path.exists(src):
        counters["mv_missing"] += 1
        return
    dst = os.path.join(archive_root, os.path.relpath(src, SRC_ROOT))
    if os.path.exists(dst):
        log("  [mv-skip] 已存在归档 {}，跳过 {}".format(_rel(dst), _rel(src)))
        counters["mv_skipped"] += 1
        return
    ensure_dir(os.path.dirname(dst), dry_run)
    if not dry_run:
        try:
            shutil.move(src, dst)
        except OSError:
            # 跨设备：copy + unlink
            if os.path.isdir(src):
                shutil.copytree(src, dst)
                shutil.rmtree(src)
            else:
                shutil.copy2(src, dst)
                os.unlink(src)
    counters["mv_done"] += 1
    log("  [mv] {} -> {}".format(_rel(src), _rel(dst)), force_print=_fp())


def rmdir_if_empty(path, dry_run, counters):
    """只删空目录（rmdir 语义），非空静默跳过"""
    if not os.path.isdir(path):
        return
    if os.listdir(path):
        counters["rmdir_kept_nonempty"] += 1
        return
    if not dry_run:
        try:
            os.rmdir(path)
        except OSError:
            return
    counters["rmdir_done"] += 1
    log("  [rmdir] {}".format(_rel(path)), force_print=_fp())


def delete_tree_if_empty_dir(path, dry_run, counters):
    """uploads/conversation、uploads/knowledge：空则整个删掉，非空归档"""
    if not os.path.isdir(path):
        counters["del_missing"] += 1
        return
    if not os.listdir(path):
        if not dry_run:
            os.rmdir(path)
        counters["rmdir_done"] += 1
        log("  [rmdir] {} (空)".format(_rel(path)), force_print=_fp())
    else:
        log("  [warn] {} 非空，按 mv 归档处理".format(_rel(path)))
        mv_to_archive(path, ARCHIVE_ROOT, dry_run, counters)


def migrate_upload_tenant_dir(up_tenant_dir, tenants_root, dry_run, counters):
    """uploads/tenant_{tid}/xxx -> tenants/{tid}/{scene}/ 的 cp 合并"""
    tid = normalize_tenant_id(os.path.basename(up_tenant_dir))
    for entry in sorted(os.listdir(up_tenant_dir)):
        src = os.path.join(up_tenant_dir, entry)
        if os.path.isdir(src):
            scene = SCENE_MAP.get(entry)
            if scene is None:
                scene = "conversation"  # user_{uid} / uuid 目录
        else:
            scene = "conversation"
        dst = os.path.join(tenants_root, tid, scene)
        log("  [plan] {} -> {}".format(_rel(src), _rel(dst)), force_print=_fp())
        if os.path.isdir(src):
            if not os.listdir(src):
                counters["empty_dir"] += 1
                continue
            cp_tree_merge(src, dst, dry_run, counters)
        else:
            copy_file_no_overwrite(src, os.path.join(dst, entry), dry_run, counters)


SRC_ROOT = None
ARCHIVE_ROOT = None
DRY_RUN = False


def _fp():
    """dry-run 时逐条打印明细供审查；正式执行只记日志文件"""
    return DRY_RUN


def main():
    global SRC_ROOT, ARCHIVE_ROOT
    parser = argparse.ArgumentParser(description="存量文件存储规范迁移")
    parser.add_argument("--env", required=True, choices=["test", "prod"])
    parser.add_argument("--storage-root", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--archive-root", default=None,
                        help="归档目录，默认 {存储根同级}/<{存储根目录名>_legacy_YYYYMMDD/")
    args = parser.parse_args()

    global DRY_RUN
    DRY_RUN = args.dry_run
    SRC_ROOT = os.path.abspath(args.storage_root)
    if not os.path.isdir(SRC_ROOT):
        print("存储根不存在: {}".format(SRC_ROOT))
        sys.exit(1)
    if args.archive_root:
        ARCHIVE_ROOT = os.path.abspath(args.archive_root)
    else:
        parent = os.path.dirname(SRC_ROOT)
        ARCHIVE_ROOT = os.path.join(
            parent, "{}_legacy_{}".format(os.path.basename(SRC_ROOT), datetime.now().strftime("%Y%m%d"))
        )

    counters = {
        "copied": 0, "copied_renamed": 0, "skipped_same": 0,
        "mv_done": 0, "mv_skipped": 0, "mv_missing": 0,
        "rmdir_done": 0, "rmdir_kept_nonempty": 0, "del_missing": 0,
        "empty_dir": 0,
    }

    log("=" * 70)
    log("存储规范迁移 env={} storage_root={} dry_run={} 时间={}".format(
        args.env, SRC_ROOT, args.dry_run, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    log("归档目录: {}".format(ARCHIVE_ROOT))
    log("=" * 70)

    tenants_root = os.path.join(SRC_ROOT, "tenants")
    ensure_dir(tenants_root, args.dry_run)

    # ---- ① storage/tenants/{tid}/ 嵌套 -> tenants/{tid}/ cp 合并 ----
    nested = os.path.join(SRC_ROOT, "storage", "tenants")
    log("\n== ① storage/tenants 嵌套合并 ==")
    if os.path.isdir(nested):
        for tid in sorted(os.listdir(nested)):
            src = os.path.join(nested, tid)
            if not os.path.isdir(src):
                continue
            dst = os.path.join(tenants_root, normalize_tenant_id(tid))
            log("[tenant] {} -> {}".format(_rel(src), _rel(dst)), force_print=_fp())
            cp_tree_merge(src, dst, args.dry_run, counters)
    else:
        log("  不存在，跳过")

    # ---- ② uploads/tenant_{tid}/ -> tenants/{tid}/{scene}/ cp 合并 ----
    log("\n== ② uploads/tenant_* 场景归位 ==")
    uploads_root = os.path.join(SRC_ROOT, "uploads")
    if os.path.isdir(uploads_root):
        for name in sorted(os.listdir(uploads_root)):
            if not name.startswith("tenant_"):
                continue
            up_tenant_dir = os.path.join(uploads_root, name)
            if os.path.isdir(up_tenant_dir):
                migrate_upload_tenant_dir(up_tenant_dir, tenants_root, args.dry_run, counters)
    else:
        log("  uploads 不存在，跳过")

    # ---- ③ mv 归档 ----
    log("\n== ③ mv 归档 ==")
    for rel in MV_TARGETS:
        mv_to_archive(os.path.join(SRC_ROOT, rel), ARCHIVE_ROOT, args.dry_run, counters)

    # ---- ④ 为空则删 ----
    log("\n== ④ 空目录删除 ==")
    for rel in DELETE_IF_EMPTY:
        delete_tree_if_empty_dir(os.path.join(SRC_ROOT, rel), args.dry_run, counters)

    # ---- ⑤ 残留空目录 rmdir（uploads/tenant_*、uploads、storage 链）----
    log("\n== ⑤ 残留空目录清理 ==")
    if os.path.isdir(uploads_root):
        # 自底向上清掉 uploads 下所有空目录（含 tenant_* 及其空子目录），非空目录安全跳过
        for root, dirs, files in os.walk(uploads_root, topdown=False):
            rmdir_if_empty(root, args.dry_run, counters)
        rmdir_if_empty(uploads_root, args.dry_run, counters)
    for rel in ["storage/tenants", "storage", "output", "output/output", "output/storage"]:
        rmdir_if_empty(os.path.join(SRC_ROOT, rel), args.dry_run, counters)

    log("\n== 统计 ==")
    for k, v in sorted(counters.items()):
        log("  {}: {}".format(k, v))
    log("完成。{}".format("(dry-run，未落盘)" if args.dry_run else ""))

    # 落日志
    log_dir = os.path.join(os.path.dirname(SRC_ROOT), "log", "temp")
    log_path = os.path.join(log_dir, "storage_migrate_{}.log".format(args.env))
    if not args.dry_run:
        try:
            os.makedirs(log_dir, exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write("\n".join(LOG) + "\n")
            print("\n日志已写入: {}".format(log_path))
        except OSError as e:
            print("\n日志写入失败(不影响迁移): {}".format(e))


if __name__ == "__main__":
    main()
