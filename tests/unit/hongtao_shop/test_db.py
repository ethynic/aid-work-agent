"""content_sync 通用表 schema 单测（hongtao_shop 消费视角）：幂等 + module 维度 + 种子行。"""

from pathlib import Path

from src.services.content_sync.db import DDL_STATEMENTS, init_content_sync_tables

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent


def test_ddl_statements_all_idempotent_guarded():
    """每条 DDL 都带幂等保护（IF NOT EXISTS 或 ON CONFLICT DO NOTHING）。"""
    assert len(DDL_STATEMENTS) >= 9  # 五表 + 四索引（种子行已移模块自举）
    for ddl in DDL_STATEMENTS:
        upper = ddl.upper()
        assert "IF NOT EXISTS" in upper or "ON CONFLICT" in upper, (
            f"非幂等语句: {ddl[:80]}"
        )


def test_db_update_yaml_contains_content_sync_block():
    """deploy/db_update.yaml 全量加载校验通过，且批次中含五张通用表与种子行。"""
    from src.db.database import _load_db_update_blocks

    blocks = _load_db_update_blocks(PROJECT_ROOT / "deploy" / "db_update.yaml")
    assert blocks, "db_update.yaml 无批次"
    sql = "\n".join(b["statements"] for b in blocks)
    for name in (
        "bs_content_sync_sources",
        "bs_content_sync_runs",
        "bs_content_sync_items",
        "bs_content_sync_records",
        "bs_image_vision_cache",
        "uq_content_sync_runs_active",
    ):
        assert name in sql, f"批次合并内容缺少 {name}"
    # 平台 DDL 零租户痕迹：无 hongtao 私有表、无租户专名种子行（自举种植）
    assert "bs_hongtao_shop" not in sql, "db_update.yaml 残留 hongtao 私有表"
    assert "hongtao" not in sql.lower(), "平台 DDL 残留租户专名"


def test_init_tables_idempotent(require_db):
    """init_content_sync_tables 连续执行两次无错（幂等 DDL + 种子行）。"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        init_content_sync_tables(conn)
        init_content_sync_tables(conn)


def test_generic_tables_module_dimension(require_db):
    """五张通用表存在且 module 维度就位（租户×源隔离键）。"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        for table in (
            "bs_content_sync_sources",
            "bs_content_sync_runs",
            "bs_content_sync_items",
            "bs_content_sync_records",
            "bs_image_vision_cache",
        ):
            cursor.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_name = %s",
                (table,),
            )
            assert cursor.fetchone() is not None, f"{table} 未建成"
        cursor.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'bs_content_sync_records' AND column_name = 'module'"
        )
        assert cursor.fetchone() is not None


def test_vl_price_seed_planted_by_module_bootstrap(require_db):
    """种子行由 hongtao 模块自举种植（平台 DDL 不含租户专名）：幂等、价格正确。"""
    from src.db.database import get_db_connection
    from src.tenant_custom.hongtao_shop.bootstrap import (
        VL_IMAGE_PARSE_MODEL,
        ensure_billing_seed,
    )

    inserted = ensure_billing_seed()
    again = ensure_billing_seed()  # 幂等：重复种植不再插入、不改价
    assert again is False
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT price_per_call FROM token_cost_prices WHERE model_name = %s",
            (VL_IMAGE_PARSE_MODEL,),
        )
        row = cursor.fetchone()
    assert row is not None
    assert float(row["price_per_call"]) == 0.01
    _ = inserted  # 首次可能已存在（历史种植），不作为断言条件


def test_runs_active_partial_unique_index(require_db):
    """租户×源串行闸门：(tenant_id, module) WHERE status='running' 部分唯一索引存在。"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT indexname FROM pg_indexes "
            "WHERE indexname = 'uq_content_sync_runs_active'"
        )
        assert cursor.fetchone() is not None
