"""wechat_mp WP1 schema 单测：DDL 幂等性 + 关键结构存在 + db_update.yaml 批次可解析。"""

from pathlib import Path

from src.wechat_mp.db import DDL_STATEMENTS, init_wechat_mp_tables

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent


def test_ddl_statements_all_idempotent_guarded():
    """每条 DDL 都带 IF NOT EXISTS 幂等保护（防止未来改动引入非幂等语句）。"""
    assert len(DDL_STATEMENTS) >= 10
    for ddl in DDL_STATEMENTS:
        assert "IF NOT EXISTS" in ddl.upper(), f"非幂等语句: {ddl[:80]}"


def test_db_update_yaml_latest_block_valid():
    """deploy/db_update.yaml 全量加载校验通过，且批次中含本功能对象。

    WP4 起后续批次（如 wechat_mp appid 唯一索引）会顶掉「最新批次」位置，
    因此校验对象为全量批次合并内容，而非仅最新块。
    """
    from src.db.database import _load_db_update_blocks

    blocks = _load_db_update_blocks(PROJECT_ROOT / "deploy" / "db_update.yaml")
    assert blocks, "db_update.yaml 无批次"
    sql = "\n".join(b["statements"] for b in blocks)
    for name in (
        "bs_wechat_mp_articles",
        "bs_wechat_mp_events",
        "bs_wechat_mp_sync_runs",
        "bs_wechat_mp_sync_items",
        "uq_wechat_mp_runs_active",
        "uq_documents_origin_external",
        "ix_documents_status",
    ):
        assert name in sql, f"批次合并内容缺少 {name}"


def test_init_tables_idempotent(require_db):
    """init_wechat_mp_tables 连续执行两次无错（CREATE/ALTER IF NOT EXISTS 幂等）。"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        init_wechat_mp_tables(conn)
        init_wechat_mp_tables(conn)


def test_documents_new_columns_and_indexes_present(tenant_id):
    """documents 四列与两个索引存在。"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_name='documents' AND column_name IN
                ('origin','external_id','status','expires_at')
                """
            )
            cols = {r["column_name"] for r in cursor.fetchall()}
            assert cols == {"origin", "external_id", "status", "expires_at"}
            cursor.execute(
                """
                SELECT indexname FROM pg_indexes
                WHERE tablename='documents' AND indexname IN
                ('uq_documents_origin_external','ix_documents_status')
                """
            )
            idx = {r["indexname"] for r in cursor.fetchall()}
            assert idx == {"uq_documents_origin_external", "ix_documents_status"}


def test_new_tables_and_indexes_present(require_db):
    """四张 bs_ 表与关键索引存在（含租户级 running 部分唯一索引）。"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_name IN
                ('bs_wechat_mp_articles','bs_wechat_mp_events',
                 'bs_wechat_mp_sync_runs','bs_wechat_mp_sync_items')
                """
            )
            tables = {r["table_name"] for r in cursor.fetchall()}
            assert tables == {
                "bs_wechat_mp_articles",
                "bs_wechat_mp_events",
                "bs_wechat_mp_sync_runs",
                "bs_wechat_mp_sync_items",
            }
            cursor.execute(
                """
                SELECT indexname FROM pg_indexes
                WHERE indexname IN
                ('uq_wechat_mp_runs_active','idx_bs_wechat_mp_articles_retry',
                 'idx_bs_wechat_mp_events_status','idx_bs_wechat_mp_sync_runs_tenant_created',
                 'idx_bs_wechat_mp_sync_items_run')
                """
            )
            idx = {r["indexname"] for r in cursor.fetchall()}
            assert idx == {
                "uq_wechat_mp_runs_active",
                "idx_bs_wechat_mp_articles_retry",
                "idx_bs_wechat_mp_events_status",
                "idx_bs_wechat_mp_sync_runs_tenant_created",
                "idx_bs_wechat_mp_sync_items_run",
            }
            # 部分唯一索引谓词确认（WHERE status='running'）
            cursor.execute(
                "SELECT indexdef FROM pg_indexes WHERE indexname='uq_wechat_mp_runs_active'"
            )
            indexdef = cursor.fetchone()["indexdef"]
            assert "running" in indexdef and "WHERE" in indexdef.upper()


def test_sync_items_has_duplicate_of_item_id(require_db):
    """sync_items 含 duplicate_of_item_id（同批次别名重复项关联主 item）。"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_name='bs_wechat_mp_sync_items'
                  AND column_name='duplicate_of_item_id'
                """
            )
            assert cursor.fetchone() is not None
