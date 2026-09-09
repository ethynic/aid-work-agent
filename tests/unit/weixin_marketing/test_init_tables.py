"""表族 init 幂等与结构测试（R40：7 张 bs_weixin_marketing_*）"""

import psycopg2
import pytest

pytestmark = pytest.mark.unit

EXPECTED_TABLES = (
    "bs_weixin_marketing_account_bindings",
    "bs_weixin_marketing_assets",
    "bs_weixin_marketing_audit_events",
    "bs_weixin_marketing_automations",
    "bs_weixin_marketing_content_blocks",
    "bs_weixin_marketing_group_bindings",
    "bs_weixin_marketing_revisions",
)


class TestInitTables:
    def test_all_seven_tables_exist(self, tenant_id):
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name LIKE 'bs_weixin_marketing%' "
                "ORDER BY table_name"
            )
            tables = {r["table_name"] for r in cur.fetchall()}
        assert tables == set(EXPECTED_TABLES)

    def test_init_idempotent(self, tenant_id):
        """重复执行 init DDL 幂等（IF NOT EXISTS；不破坏既有数据）"""
        from src.db.database import get_db_connection
        from src.weixin_marketing.init_tables import init_weixin_marketing_tables

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO bs_weixin_marketing_automations (tenant_id, user_id, name) "
                "VALUES (%s, 'owner-1', '存活探针') RETURNING id",
                (tenant_id,),
            )
            probe_id = str(cur.fetchone()["id"])
            conn.commit()
        with get_db_connection() as conn:
            init_weixin_marketing_tables(conn)  # 二次执行
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT name FROM bs_weixin_marketing_automations "
                "WHERE tenant_id = %s AND id = %s",
                (tenant_id, probe_id),
            )
            assert cur.fetchone()["name"] == "存活探针"

    def test_content_blocks_check_constraints(self, tenant_id):
        """kind 与字段互斥 CHECK 生效（text 带 url / link 缺 url 均拒绝）"""
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO bs_weixin_marketing_automations (tenant_id, user_id, name) "
                "VALUES (%s, 'owner-1', 't') RETURNING id",
                (tenant_id,),
            )
            automation_id = cur.fetchone()["id"]
            cur.execute(
                "INSERT INTO bs_weixin_marketing_revisions (tenant_id, automation_id, user_id, revision_no) "
                "VALUES (%s, %s, 'owner-1', 1) RETURNING id",
                (tenant_id, automation_id),
            )
            revision_id = cur.fetchone()["id"]
            for bad_sql, bad_params in (
                (
                    "INSERT INTO bs_weixin_marketing_content_blocks "
                    "(tenant_id, revision_id, user_id, position, kind, text_content, url) "
                    "VALUES (%s, %s, 'owner-1', 1, 'text', '正文', 'https://x')",
                    (tenant_id, revision_id),
                ),
                (
                    "INSERT INTO bs_weixin_marketing_content_blocks "
                    "(tenant_id, revision_id, user_id, position, kind, url) "
                    "VALUES (%s, %s, 'owner-1', 1, 'link', NULL)",
                    (tenant_id, revision_id),
                ),
                (
                    "INSERT INTO bs_weixin_marketing_content_blocks "
                    "(tenant_id, revision_id, user_id, position, kind) "
                    "VALUES (%s, %s, 'owner-1', 1, 'video')",
                    (tenant_id, revision_id),
                ),
            ):
                # 每次违规后回滚复位事务（InFailedSqlTransaction 会让后续语句失真）
                with pytest.raises(psycopg2.errors.CheckViolation):
                    cur.execute(bad_sql, bad_params)
                conn.rollback()
            conn.rollback()
