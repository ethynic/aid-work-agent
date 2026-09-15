"""微信公众号内容入知识库 WP1 schema 基础集成用例（真实 PostgreSQL 约束验证）。

覆盖：租户级 running 部分唯一索引、events 幂等键唯一、sync_items 批次内唯一 +
duplicate_of_item_id 自关联、documents 外部源部分唯一索引。
细粒度并发/恢复语义由测试智能体在专项集成套件中扩展。
"""

import uuid

import pytest

WECHAT_MP_TABLES = (
    "bs_wechat_mp_sync_items",
    "bs_wechat_mp_sync_runs",
    "bs_wechat_mp_events",
    "bs_wechat_mp_articles",
)


def _cleanup_tenant(tenant_id: str) -> None:
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        for table in WECHAT_MP_TABLES:
            try:
                cursor = conn.cursor()
                cursor.execute(f"DELETE FROM {table} WHERE tenant_id = %s", (tenant_id,))
            except Exception:  # noqa: BLE001 单表失败不阻断其余清理
                conn.rollback()
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM documents WHERE tenant_id = %s", (tenant_id,))
        except Exception:  # noqa: BLE001
            conn.rollback()
        conn.commit()


@pytest.fixture()
def tenant_id():
    value = f"wmp_it_{uuid.uuid4().hex[:12]}"
    yield value
    _cleanup_tenant(value)


def _insert_run(conn, tenant_id, status):
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO bs_wechat_mp_sync_runs (tenant_id, trigger_type, status)
        VALUES (%s, 'manual', %s) RETURNING id
        """,
        (tenant_id, status),
    )
    return cursor.fetchone()["id"]


def test_tenant_single_running_run(db_connection, tenant_id):
    """同租户第二条 running 违反部分唯一索引；queued/终态不限条数。"""
    conn = db_connection
    _insert_run(conn, tenant_id, "running")
    conn.commit()
    with pytest.raises(Exception):
        _insert_run(conn, tenant_id, "running")
        conn.commit()
    conn.rollback()
    # queued 与 success 不受限
    _insert_run(conn, tenant_id, "queued")
    _insert_run(conn, tenant_id, "success")
    conn.commit()


def test_events_event_key_unique(db_connection, tenant_id):
    """(tenant_id, config_id, event_key) 幂等去重；不同 config_id 不碰撞。"""
    conn = db_connection
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO bs_wechat_mp_events (tenant_id, config_id, event_key)
        VALUES (%s, 'cfg1', 'msg1:MASSSENDJOBFINISH')
        """,
        (tenant_id,),
    )
    conn.commit()
    with pytest.raises(Exception):
        cursor.execute(
            """
            INSERT INTO bs_wechat_mp_events (tenant_id, config_id, event_key)
            VALUES (%s, 'cfg1', 'msg1:MASSSENDJOBFINISH')
            """,
            (tenant_id,),
        )
        conn.commit()
    conn.rollback()
    cursor.execute(
        """
        INSERT INTO bs_wechat_mp_events (tenant_id, config_id, event_key)
        VALUES (%s, 'cfg2', 'msg1:MASSSENDJOBFINISH')
        """,
        (tenant_id,),
    )
    conn.commit()


def test_sync_items_batch_unique_and_duplicate_link(db_connection, tenant_id):
    """(tenant_id, run_id, article_row_id) 唯一；skipped 重复项可关联主 item。"""
    conn = db_connection
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO bs_wechat_mp_articles (tenant_id, external_id, source_channel)
        VALUES (%s, 'appid:art1:0', 'manual') RETURNING id
        """,
        (tenant_id,),
    )
    article_id = cursor.fetchone()["id"]
    run_id = _insert_run(conn, tenant_id, "queued")
    cursor.execute(
        """
        INSERT INTO bs_wechat_mp_sync_items (tenant_id, run_id, article_row_id, status)
        VALUES (%s, %s, %s, 'pending') RETURNING id
        """,
        (tenant_id, run_id, article_id),
    )
    master_item_id = cursor.fetchone()["id"]
    conn.commit()
    with pytest.raises(Exception):
        cursor.execute(
            """
            INSERT INTO bs_wechat_mp_sync_items (tenant_id, run_id, article_row_id, status)
            VALUES (%s, %s, %s, 'pending')
            """,
            (tenant_id, run_id, article_id),
        )
        conn.commit()
    conn.rollback()
    # 另一文章行的重复项 item：skipped + duplicate_of_item_id 关联主 item
    cursor.execute(
        """
        INSERT INTO bs_wechat_mp_articles (tenant_id, external_id, source_channel)
        VALUES (%s, 'appid:art1:0:alias', 'manual') RETURNING id
        """,
        (tenant_id,),
    )
    alias_id = cursor.fetchone()["id"]
    cursor.execute(
        """
        INSERT INTO bs_wechat_mp_sync_items
            (tenant_id, run_id, article_row_id, status, duplicate_of_item_id)
        VALUES (%s, %s, %s, 'skipped', %s)
        """,
        (tenant_id, run_id, alias_id, master_item_id),
    )
    conn.commit()


def test_documents_external_partial_unique(db_connection, tenant_id):
    """同 (tenant, origin, external_id) 冲突；external_id NULL 不受限。"""
    conn = db_connection
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO documents (tenant_id, title, origin, external_id)
        VALUES (%s, 'a', 'wechat_mp', 'appid:art1:0')
        """,
        (tenant_id,),
    )
    conn.commit()
    with pytest.raises(Exception):
        cursor.execute(
            """
            INSERT INTO documents (tenant_id, title, origin, external_id)
            VALUES (%s, 'b', 'wechat_mp', 'appid:art1:0')
            """,
            (tenant_id,),
        )
        conn.commit()
    conn.rollback()
    # 默认 origin/status 生效；external_id NULL 两行不冲突
    cursor.execute(
        "INSERT INTO documents (tenant_id, title) VALUES (%s, 'm1') RETURNING origin, status",
        (tenant_id,),
    )
    row = cursor.fetchone()
    assert row["origin"] == "manual_upload" and row["status"] == "active"
    cursor.execute("INSERT INTO documents (tenant_id, title) VALUES (%s, 'm2')", (tenant_id,))
    conn.commit()
