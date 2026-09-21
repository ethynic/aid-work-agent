"""hongtao_shop 并发领取集成测试（真实 PostgreSQL，无 DATABASE_URL 跳过）。

验证两条并发保证（对齐 wechat_mp test_wechat_mp_concurrency 范式）：
- 已有 running 时可连续受理多个 queued（部分唯一索引只挡 running）；
- 并发 queued→running 领取，同租户至多一个成功（uq_hongtao_shop_runs_active）。
"""

import os
import threading
import uuid
from pathlib import Path

import pytest
from dotenv import load_dotenv

project_root = Path(__file__).parent.parent.parent
load_dotenv(project_root / ".env")
DATABASE_URL = os.getenv("DATABASE_URL", "")

psycopg2 = pytest.importorskip("psycopg2")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL or "postgresql" not in DATABASE_URL,
    reason="需要 PostgreSQL DATABASE_URL",
)


@pytest.fixture()
def service():
    from src.db.database import get_postgres_pool, init_postgres_pool
    from src.tenant_custom.hongtao_shop.service import HongtaoShopSyncService

    if get_postgres_pool() is None:
        init_postgres_pool()
    return HongtaoShopSyncService()


def _seed_tenant(tenant_id: str) -> None:
    from src.core.cache_utils import invalidate_tenant_cache
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO tenants (tenant_id, company_name, credit_balance)
            VALUES (%s, 'hongtao_shop 并发测试', 10000)
            ON CONFLICT (tenant_id) DO UPDATE
            SET credit_balance = EXCLUDED.credit_balance
            """,
            (tenant_id,),
        )
        conn.commit()
    invalidate_tenant_cache(tenant_id)


def _seed_runs(tenant_id: str, queued: int) -> None:
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO bs_content_sync_runs (tenant_id, module, trigger_type, status) "
            "VALUES (%s, 'hongtao_shop', 'manual', 'running')", (tenant_id,),
        )
        for _ in range(queued):
            cursor.execute(
                "INSERT INTO bs_content_sync_runs (tenant_id, module, trigger_type, status) "
                "VALUES (%s, 'hongtao_shop', 'manual', 'queued')", (tenant_id,),
            )
        conn.commit()


def _cleanup(tenant_id: str) -> None:
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        for sql in (
            "DELETE FROM bs_content_sync_items WHERE tenant_id = %s AND module = 'hongtao_shop'",
            "DELETE FROM bs_content_sync_runs WHERE tenant_id = %s",
            "DELETE FROM tenants WHERE tenant_id = %s",
        ):
            try:
                cursor.execute(sql, (tenant_id,))
            except Exception:  # noqa: BLE001
                conn.rollback()
        conn.commit()


@pytest.fixture()
def tenant_id():
    value = f"hts_it_{uuid.uuid4().hex[:12]}"
    yield value
    _cleanup(value)


def test_queued_accepted_while_running(tenant_id, service):
    """已有 running 不影响连续受理 queued（部分唯一索引只约束 running）。"""
    _seed_tenant(tenant_id)
    _seed_runs(tenant_id, queued=3)
    accepted = service.trigger_sync(tenant_id, trigger_type="manual")
    assert accepted["status"] == "queued"


def test_concurrent_claim_single_winner(tenant_id, service):
    """并发领取同租户多条 queued：至多一个成功（SKIP LOCKED 让位或唯一索引 conflict），
    其余为 None（无行可锁）或 'conflict'（提交撞 uq_hongtao_shop_runs_active）。"""
    from src.db.database import get_db_connection

    _seed_tenant(tenant_id)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        for _ in range(3):
            cursor.execute(
                "INSERT INTO bs_content_sync_runs (tenant_id, module, trigger_type, status) "
                "VALUES (%s, 'hongtao_shop', 'manual', 'queued')", (tenant_id,),
            )
        conn.commit()

    results = []
    lock = threading.Lock()

    def _claim():
        claimed = service._claim_next_run(tenant_id, uuid.uuid4().hex)
        with lock:
            results.append(claimed)

    threads = [threading.Thread(target=_claim) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    winners = [r for r in results if isinstance(r, dict)]
    assert len(winners) == 1, f"并发领取应恰有一个成功: {results}"
    assert all(r in (None, "conflict") for r in results if not isinstance(r, dict))
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) AS cnt FROM bs_content_sync_runs "
            "WHERE tenant_id = %s AND status = 'running'",
            (tenant_id,),
        )
        assert cursor.fetchone()["cnt"] == 1
