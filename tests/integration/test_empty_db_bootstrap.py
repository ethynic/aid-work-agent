"""空库全量初始化验证（P5 R59①：发布检查——init-postgres.sql 于空白 database
跑通 + 幂等重放零差异）

流程（真实 PG，隔离的临时 database，用后 DROP）：
1. 以 DATABASE_URL 连接当前库，CREATE DATABASE wxm_empty_check_<ts>（无
   CREATEDB 权限则 skip，以手动执行记录替代——见发布检查报告）；
2. 逐批执行 deploy/init-postgres.sql 全量 DDL（单连接多语句执行）；
3. 断言关键表全部存在（desktop_automation_* 12 张、local_tool_operation_permits、
   bs_weixin_marketing_* 7 张、幂等表、事件闭环 4 张配套表）；
4. 结构快照（表+列）与种子行快照（token_cost_prices）后重放整份 SQL——
   断言零差异（幂等：IF NOT EXISTS / ON CONFLICT / DO UPDATE 同值）；
5. teardown DROP DATABASE（WITH FORCE 兜底断连）。

本文件不依赖任何 src.db 连接池（独立 psycopg2 连接直连目标临时库）。
"""

import os
import re
import time
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_REPO_ROOT = Path(__file__).parents[2]
_INIT_SQL = _REPO_ROOT / "deploy" / "init-postgres.sql"

DA_TABLES = (
    "desktop_automation_subjects",
    "desktop_automation_schedules",
    "desktop_automation_event_sources",
    "desktop_automation_events",
    "desktop_automation_occurrences",
    "desktop_automation_runs",
    "desktop_automation_deliveries",
    "desktop_automation_attempts",
    "desktop_automation_evidence",
    "desktop_automation_audit_events",
    "desktop_automation_outbox",
    "desktop_automation_quota_buckets",
)
WXM_TABLES = (
    "bs_weixin_marketing_automations",
    "bs_weixin_marketing_revisions",
    "bs_weixin_marketing_content_blocks",
    "bs_weixin_marketing_group_bindings",
    "bs_weixin_marketing_account_bindings",
    "bs_weixin_marketing_audit_events",
    "bs_weixin_marketing_assets",
)
REQUIRED_TABLES = (
    # C5: session task accounting, confirmations and C4 notifications must ship
    # in the full installer as well as the incremental initializers.
    "session_tasks",
    "session_task_specs",
    "session_task_assignments",
    "session_task_events",
    "session_task_messages",
    "session_task_batches",
    "session_task_decisions",
    "session_task_decision_attempts",
    "session_task_execution_links",
    "session_task_texts",
    "session_task_confirmations",
    "session_task_cost_reservations",
    "session_tasks_idempotency_keys",
    "session_task_notifications",
    "bs_weixin_conversation_bindings",
    *DA_TABLES,
    "local_tool_operation_permits",
    "local_tool_invocations",
    *WXM_TABLES,
    # 幂等表 + 事件闭环配套表（keys/nonces/payloads/example_orders）
    "weixin_marketing_idempotency_keys",
    "weixin_marketing_event_source_keys",
    "weixin_marketing_webhook_nonces",
    "weixin_marketing_event_payloads",
    "weixin_marketing_example_orders",
)


def _admin_url() -> str:
    from dotenv import load_dotenv

    load_dotenv(_REPO_ROOT / ".env")
    url = os.getenv("DATABASE_URL", "")
    if not url or "postgresql" not in url:
        pytest.skip("空库初始化验证需要 PostgreSQL DATABASE_URL")
    return url


@pytest.fixture(scope="module")
def empty_db():
    """临时空 database（模块级复用；用后强制 DROP）"""
    import psycopg2

    admin_url = _admin_url()
    db_name = f"wxm_empty_check_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    admin = psycopg2.connect(admin_url, connect_timeout=10)
    admin.autocommit = True
    try:
        with admin.cursor() as cur:
            cur.execute("SELECT rolcreatedb FROM pg_roles WHERE rolname = current_user")
            row = cur.fetchone()
            if row is None or not row[0]:
                admin.close()
                pytest.skip(
                    "当前数据库用户无 CREATEDB 权限，跳过自动空库验证"
                    "（以发布检查报告中的手动执行记录替代）"
                )
            cur.execute(
                # template0：服务器 template1 存在 collation version 失配
                #（musl 容器 libc 升级常见），CREATE DATABASE 默认模板会被拒；
                # template0 的 datcollversion 为 NULL 不触发该校验，结构等价可用
                f'CREATE DATABASE "{db_name}" TEMPLATE template0'
            )
    except Exception:
        # 建库前任何失败（含 skip 已抛出的 SkipException 之外的异常）都不泄漏 admin 连接
        if not admin.closed:
            admin.close()
        raise
    conn = None
    try:
        conn = psycopg2.connect(admin_url.rsplit("/", 1)[0] + f"/{db_name}", connect_timeout=10)
        yield conn
    finally:
        if conn is not None:
            conn.close()
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
        admin.close()


def _execute_init_sql(conn) -> None:
    sql = _INIT_SQL.read_text(encoding="utf-8")
    assert sql, "init-postgres.sql 为空"
    with conn.cursor() as cur:
        cur.execute(sql)  # psycopg2 单次执行多语句（等价 psql -f 全量跑）
    conn.commit()


def _structure_snapshot(conn) -> dict:
    """结构快照：public 下全部表 → 列名排序列表"""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            ORDER BY table_name
            """
        )
        tables = [r[0] for r in cur.fetchall()]
        snapshot = {}
        for table in tables:
            cur.execute(
                """
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = %s
                ORDER BY ordinal_position
                """,
                (table,),
            )
            snapshot[table] = cur.fetchall()
        return snapshot


def _seed_snapshot(conn) -> dict:
    """种子行快照：token_cost_prices（唯一有种子数据的表）全行内容"""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT model_name, input_price_per_m, output_price_per_m,
                   cached_input_price_per_m, is_multimodal
            FROM token_cost_prices ORDER BY model_name
            """
        )
        return {r[0]: r[1:] for r in cur.fetchall()}


class TestEmptyDbBootstrap:
    def test_init_sql_bootstraps_empty_db_idempotently(self, empty_db):
        conn = empty_db
        # ① 空库首次执行：无异常
        _execute_init_sql(conn)

        # ② 关键表存在
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = ANY(%s)
                """,
                (list(REQUIRED_TABLES),),
            )
            present = {r[0] for r in cur.fetchall()}
        missing = set(REQUIRED_TABLES) - present
        assert not missing, f"空库初始化后缺表: {sorted(missing)}"

        # ③ 幂等重放：结构与种子零差异
        before_structure = _structure_snapshot(conn)
        before_seed = _seed_snapshot(conn)
        _execute_init_sql(conn)
        assert _structure_snapshot(conn) == before_structure, "重放后结构漂移"
        assert _seed_snapshot(conn) == before_seed, "重放后种子数据漂移"

        # ④ 关键表行数快照重放前后一致（防御 DDL 外的隐式写）
        with conn.cursor() as cur:
            counts = {}
            for table in REQUIRED_TABLES:
                cur.execute(f'SELECT COUNT(*) FROM "{table}"')
                counts[table] = cur.fetchone()[0]
        _execute_init_sql(conn)
        with conn.cursor() as cur:
            for table, expected in counts.items():
                cur.execute(f'SELECT COUNT(*) FROM "{table}"')
                assert cur.fetchone()[0] == expected, f"{table} 行数在重放后变化"

    def test_expected_table_universe_reasonable(self, empty_db):
        """健康锚：初始化后 public 表数量在合理区间（防全量 DDL 大面积丢失静默通过）"""
        _execute_init_sql(empty_db)
        snapshot = _structure_snapshot(empty_db)
        assert 90 <= len(snapshot) <= 200, f"表数量异常: {len(snapshot)}"
