"""租户物理删除 SQL 冒烟测试（真库）

背景：核心删除/孤儿删除 SQL 的列名错误会被单元测试的 cursor mock 掩盖
（真实案例：documents 主键是 id 不是 doc_id，mock 测不出 column not exist）。
EXPLAIN 只生成执行计划、不执行删除，用于验证所有动态拼接 SQL 的表名列名有效。
"""

import pytest

from src.db.database import get_db_connection

import src.saas.services.tenant_purge as tenant_purge

pytestmark = pytest.mark.integration

_SMOKE_TENANT = "__smoke_nonexistent_tenant__"


def test_core_purge_sql_plans_against_real_db():
    """核心删除 SQL：每条都能在真库生成执行计划（列名/表名有效）"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        for table, sql in tenant_purge.CORE_PURGE_SQL:
            cursor.execute("EXPLAIN " + sql, (_SMOKE_TENANT,))
            assert cursor.fetchall(), f"EXPLAIN 无输出: {table}"


def test_tenants_status_guard_sql_plans_against_real_db():
    """tenants 行 status 复核删除 SQL 有效"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "EXPLAIN DELETE FROM tenants WHERE tenant_id = %s AND status = %s",
            (_SMOKE_TENANT, "deactivated"),
        )
        assert cursor.fetchall()


def test_orphan_scan_targets_and_delete_sql_plans_against_real_db():
    """孤儿扫描目标发现 + 每个目标的动态删除 SQL 都能生成执行计划

    一次性覆盖 information_schema 查询正确性（BASE TABLE 过滤、排除 tenants）
    和全部约百张表的列名有效性。
    """
    targets = tenant_purge._list_orphan_scan_targets()
    assert len(targets) > 0
    assert all(t != "tenants" for t, _ in targets)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        for table, column in targets:
            table_ident = f'"{table}"'
            column_ident = f'"{column}"'
            cursor.execute(
                f"EXPLAIN DELETE FROM {table_ident} WHERE ctid IN ("
                f"  SELECT ctid FROM {table_ident}"
                f"  WHERE {column_ident} IS NOT NULL AND {column_ident} <> ''"
                f"    AND {column_ident} NOT LIKE '\\_%'"
                f"    AND {column_ident} NOT IN (SELECT tenant_id FROM tenants)"
                f"  LIMIT 1)"
            )
            assert cursor.fetchall(), f"EXPLAIN 无输出: {table}.{column}"
