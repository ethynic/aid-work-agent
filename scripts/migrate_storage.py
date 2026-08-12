#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
存储结构迁移脚本

将旧目录结构迁移到新结构：
旧: uploads/{tenant_id}/*           ->  新: storage/uploads/{tenant_id}/conversation/*
旧: uploads/knowledge/{tenant_id}/*  ->  新: storage/uploads/{tenant_id}/knowledge/*
旧: uploads/wecom/*                  ->  新: storage/uploads/wecom/*

使用方法:
    python scripts/migrate_storage.py          # 预览迁移
    python scripts/migrate_storage.py --yes    # 执行迁移

数据库中 file_path 字段可以手动更新
update documents set tenant_id='tenant_9eb3e45cab83';
UPDATE documents
SET file_path = 'storage/uploads/' || tenant_id || '/knowledge/' || split_part(file_path, '/', -1)
WHERE file_path LIKE 'uploads/knowledge/%';
"""

import os
import shutil
import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="存储结构迁移脚本")
    parser.add_argument("--yes", action="store_true", help="确认执行迁移（不添加则仅预览）")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    old_uploads_dir = project_root / "uploads"
    new_uploads_dir = project_root / "storage" / "uploads"

    if not old_uploads_dir.exists():
        print(f"旧目录不存在: {old_uploads_dir}")
        return

    print(f"项目根目录: {project_root}")
    print(f"旧上传目录: {old_uploads_dir}")
    print(f"新上传目录: {new_uploads_dir}")
    print()

    if not args.yes:
        print("=== 预览模式 ===")
        print("以下是将要执行的迁移操作：")
        print()

    # 1. 迁移 wecom 目录 (无租户)
    old_wecom = old_uploads_dir / "wecom"
    new_wecom = new_uploads_dir / "wecom"
    action = "移动" if args.yes else "将移动"
    if old_wecom.exists():
        print(f"[企业微信媒体] {action}:")
        print(f"  {old_wecom}")
        print(f"  -> {new_wecom}")
        if args.yes:
            new_wecom.parent.mkdir(parents=True, exist_ok=True)
            if new_wecom.exists():
                # 目标已存在，合并
                for item in old_wecom.iterdir():
                    target = new_wecom / item.name
                    if target.exists():
                        print(f"    跳过（目标已存在）: {item.name}")
                    else:
                        shutil.move(str(item), str(new_wecom))
            else:
                shutil.move(str(old_wecom), str(new_wecom))
        print()

    # 2. 迁移租户对话上传文件
    # old: uploads/{tenant_id}/* -> new: storage/uploads/{tenant_id}/conversation/*
    print(f"[租户对话文件] {action if args.yes else '将移动'}:")
    tenant_count = 0
    for item in old_uploads_dir.iterdir():
        if item.is_dir() and item.name not in ["knowledge", "wecom"]:
            tenant_id = item.name
            old_tenant_dir = item
            new_tenant_dir = new_uploads_dir / tenant_id / "conversation"

            count = len(list(old_tenant_dir.iterdir())) if old_tenant_dir.exists() else 0
            if count > 0:
                tenant_count += 1
                print(f"  租户 {tenant_id} ({count} 个文件)")
                print(f"    {old_tenant_dir}")
                print(f"    -> {new_tenant_dir}")

                if args.yes:
                    new_tenant_dir.mkdir(parents=True, exist_ok=True)
                    for file_item in old_tenant_dir.iterdir():
                        target = new_tenant_dir / file_item.name
                        if target.exists():
                            print(f"      跳过（目标已存在）: {file_item.name}")
                        else:
                            shutil.move(str(file_item), str(new_tenant_dir))

    if tenant_count == 0:
        print("  无可迁移的租户对话文件")
    print()

    # 3. 迁移知识库文件
    # old: uploads/knowledge/{tenant_id}/* -> new: storage/uploads/{tenant_id}/knowledge/*
    old_knowledge_root = old_uploads_dir / "knowledge"
    if old_knowledge_root.exists():
        print(f"[知识库文件] {action if args.yes else '将移动'}:")
        kb_count = 0

        # 检查是否有租户子目录
        has_tenant_dirs = False
        for item in old_knowledge_root.iterdir():
            if item.is_dir():
                has_tenant_dirs = True
                tenant_id = item.name
                old_tenant_kb = item
                new_tenant_kb = new_uploads_dir / tenant_id / "knowledge"

                count = len(list(old_tenant_kb.iterdir())) if old_tenant_kb.exists() else 0
                if count > 0:
                    kb_count += 1
                    print(f"  租户 {tenant_id} ({count} 个文档)")
                    print(f"    {old_tenant_kb}")
                    print(f"    -> {new_tenant_kb}")

                    if args.yes:
                        new_tenant_kb.mkdir(parents=True, exist_ok=True)
                        for file_item in old_tenant_kb.iterdir():
                            target = new_tenant_kb / file_item.name
                            if target.exists():
                                print(f"      跳过（目标已存在）: {file_item.name}")
                            else:
                                shutil.move(str(file_item), str(new_tenant_kb))

        # 如果没有租户子目录，说明是非租户模式，所有文件在 knowledge 根下
        if not has_tenant_dirs:
            old_tenant_kb = old_knowledge_root
            new_tenant_kb = new_uploads_dir / "knowledge"
            count = len(list(old_tenant_kb.iterdir())) if old_tenant_kb.exists() else 0
            if count > 0:
                kb_count += 1
                print(f"  全局知识库 ({count} 个文档)")
                print(f"    {old_tenant_kb}")
                print(f"    -> {new_tenant_kb}")

                if args.yes:
                    new_tenant_kb.mkdir(parents=True, exist_ok=True)
                    for file_item in old_tenant_kb.iterdir():
                        target = new_tenant_kb / file_item.name
                        if target.exists():
                            print(f"      跳过（目标已存在）: {file_item.name}")
                        else:
                            shutil.move(str(file_item), str(new_tenant_kb))

        if kb_count == 0:
            print("  无可迁移的知识库文件")
        print()

    # 4. 迁移非租户模式下的对话上传文件（uploads 根下的文件）
    print(f"[全局对话文件] {action if args.yes else '将移动'}:")
    global_file_count = 0
    for item in old_uploads_dir.iterdir():
        if item.is_file():
            global_file_count += 1
            if global_file_count <= 5:  # 只显示前5个
                print(f"  {item.name}")

    if global_file_count > 5:
        print(f"  ... 还有 {global_file_count - 5} 个文件")

    if global_file_count > 0:
        target_dir = new_uploads_dir / "conversation"
        print(f"  -> {target_dir}")
        if args.yes:
            target_dir.mkdir(parents=True, exist_ok=True)
            for item in old_uploads_dir.iterdir():
                if item.is_file():
                    target = target_dir / item.name
                    if target.exists():
                        print(f"    跳过（目标已存在）: {item.name}")
                    else:
                        shutil.move(str(item), str(target_dir))
    else:
        print("  无可迁移的全局对话文件")
    print()

    if args.yes:
        print("=== 迁移完成 ===")
        print()
        print("提示：")
        print("1. 所有文件已移动到新目录结构")
        print("2. 原目录 uploads/ 中的空目录可以手动删除")
        print("3. 如果有问题，可以手动将文件移回原目录进行回滚")
    else:
        print("=== 预览结束 ===")
        print()
        print("如需执行迁移，请运行:")
        print("  python scripts/migrate_storage.py --yes")


if __name__ == "__main__":
    main()
