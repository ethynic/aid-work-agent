#!/usr/bin/env python3
"""
租户代码批量更新工具

功能：
1. 为所有现有租户生成默认租户代码（如果为空）
2. 重新生成所有租户的租户代码（覆盖现有）
3. 检查租户代码唯一性

用法：
    python scripts/update_tenant_codes.py [--regenerate] [--dry-run]

参数：
    --regenerate   重新生成所有租户代码（覆盖现有）
    --dry-run      只显示将要执行的操作，不实际修改数据库
"""

import sys
import os
import argparse
import logging
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.db.database import get_db_connection
from loguru import logger

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def generate_default_code(tenant_id: str) -> str:
    """生成默认租户代码：'t' + 租户ID后5位大写字母"""
    # tenant_id 格式: tenant_a1b2c3d4e5f6
    # 取最后5个字符，转换为大写
    suffix = tenant_id[-5:].upper()
    return f"t{suffix}"


def update_tenant_codes(regenerate: bool = False, dry_run: bool = False):
    """更新租户代码"""
    with get_db_connection() as conn:
        cursor = conn.cursor()

        # 获取所有租户
        cursor.execute("SELECT tenant_id, tenant_code FROM tenants ORDER BY created_at")
        rows = cursor.fetchall()

        logger.info(f"找到 {len(rows)} 个租户")

        updates = []
        for row in rows:
            tenant_id = row["tenant_id"]
            current_code = row["tenant_code"]

            if not regenerate and current_code:
                logger.debug(f"租户 {tenant_id} 已有代码: {current_code}，跳过")
                continue

            new_code = generate_default_code(tenant_id)

            # 检查唯一性（大小写不敏感）
            cursor.execute(
                "SELECT COUNT(*) as cnt FROM tenants WHERE UPPER(tenant_code) = %s AND tenant_id != %s",
                (new_code.upper(), tenant_id)
            )
            conflict_count = cursor.fetchone()["cnt"]

            if conflict_count > 0:
                logger.warning(f"租户 {tenant_id} 的默认代码 {new_code} 与其他租户冲突，跳过")
                continue

            updates.append((tenant_id, new_code, current_code))

        if dry_run:
            logger.info("=== 干跑模式，不会修改数据库 ===")
            for tenant_id, new_code, current_code in updates:
                logger.info(f"租户 {tenant_id}: {current_code or '(空)'} -> {new_code}")
            logger.info(f"总计 {len(updates)} 个租户需要更新")
            return

        # 执行更新
        updated = 0
        for tenant_id, new_code, current_code in updates:
            try:
                cursor.execute(
                    "UPDATE tenants SET tenant_code = %s WHERE tenant_id = %s",
                    (new_code, tenant_id)
                )
                updated += 1
                logger.info(f"更新租户 {tenant_id}: {current_code or '(空)'} -> {new_code}")
            except Exception as e:
                logger.error(f"更新租户 {tenant_id} 失败: {e}")

        conn.commit()
        logger.info(f"完成，更新了 {updated}/{len(updates)} 个租户")


def check_uniqueness():
    """检查租户代码唯一性"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT UPPER(tenant_code) as code_upper, COUNT(*) as cnt,
                   STRING_AGG(tenant_id, ', ') as tenant_ids
            FROM tenants
            WHERE tenant_code IS NOT NULL AND tenant_code != ''
            GROUP BY UPPER(tenant_code)
            HAVING COUNT(*) > 1
        """)
        conflicts = cursor.fetchall()

        if conflicts:
            logger.warning("发现重复的租户代码（大小写不敏感）:")
            for row in conflicts:
                logger.warning(f"  代码 {row['code_upper']}: {row['cnt']} 个租户 ({row['tenant_ids']})")
            return False
        else:
            logger.info("所有租户代码唯一性检查通过")
            return True


def main():
    parser = argparse.ArgumentParser(description="租户代码批量更新工具")
    parser.add_argument("--regenerate", action="store_true", help="重新生成所有租户代码（覆盖现有）")
    parser.add_argument("--dry-run", action="store_true", help="干跑模式，不实际修改数据库")
    parser.add_argument("--check", action="store_true", help="仅检查租户代码唯一性")

    args = parser.parse_args()

    if args.check:
        check_uniqueness()
        return

    update_tenant_codes(regenerate=args.regenerate, dry_run=args.dry_run)

    # 检查唯一性
    check_uniqueness()


if __name__ == "__main__":
    main()