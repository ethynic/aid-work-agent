"""wechat_mp 单元测试 fixtures（真实 DB 模式，照 session_tasks conftest 范式）。

每用例独立随机租户，测后物理清理四张 bs_wechat_mp_* 表与测试 documents 行。
"""

import os
import uuid
from pathlib import Path

import pytest
from dotenv import load_dotenv

# 必须在任何 src 导入之前加载 .env（DATABASE_URL）
project_root = Path(__file__).parent.parent.parent.parent
load_dotenv(project_root / ".env")
db_url = os.getenv("DATABASE_URL", "")
if db_url:
    os.environ["DATABASE_URL"] = db_url
    os.environ.setdefault("DB_POOL_MIN", "2")
    os.environ.setdefault("DB_POOL_MAX", "10")

WECHAT_MP_TABLES = (
    "bs_wechat_mp_sync_items",
    "bs_wechat_mp_sync_runs",
    "bs_wechat_mp_events",
    "bs_wechat_mp_articles",
)


def cleanup_tenant(tenant_id: str) -> None:
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        for table in WECHAT_MP_TABLES:
            try:
                cursor = conn.cursor()
                cursor.execute(f"DELETE FROM {table} WHERE tenant_id = %s", (tenant_id,))
            except Exception:  # noqa: BLE001 单表失败不阻断其余清理
                conn.rollback()
        # WP5 入库链路残留：chunks_vec/chunks 无 tenant_id 列，经 documents 子查询清理
        for sql in (
            "DELETE FROM chunks_vec WHERE chunk_id IN "
            "(SELECT id FROM chunks WHERE doc_id IN "
            "(SELECT id FROM documents WHERE tenant_id = %s))",
            "DELETE FROM chunks WHERE doc_id IN "
            "(SELECT id FROM documents WHERE tenant_id = %s)",
            "DELETE FROM documents WHERE tenant_id = %s",
            "DELETE FROM chat_records WHERE tenant_id = %s",
            "DELETE FROM knowledge_categories WHERE tenant_id = %s",
            "DELETE FROM subagent_knowledge_sources WHERE tenant_id = %s",
            "DELETE FROM subscriptions WHERE tenant_id = %s",
            "DELETE FROM tenants WHERE tenant_id = %s",
        ):
            try:
                cursor = conn.cursor()
                cursor.execute(sql, (tenant_id,))
            except Exception:  # noqa: BLE001
                conn.rollback()
        conn.commit()


_DB_AVAILABLE = False


@pytest.fixture(scope="session", autouse=True)
def _init_db_pool():
    """尝试初始化 DB 连接池；不可用时只记录标志，由 require_db 决定跳过。

    纯逻辑测试（identity/fetcher/content）不依赖真实 DB，DB 不可用时照常运行。
    """
    global _DB_AVAILABLE
    if not db_url or "postgresql" not in db_url:
        yield
        return
    from src.db.database import init_postgres_pool, get_postgres_pool

    if get_postgres_pool() is None:
        try:
            init_postgres_pool()
        except Exception:  # noqa: BLE001
            yield
            return
    # 模块建表幂等
    from src.db.database import get_db_connection

    from src.wechat_mp.db import init_wechat_mp_tables

    try:
        with get_db_connection() as conn:
            init_wechat_mp_tables(conn)
    except Exception:  # noqa: BLE001
        yield
        return

    # WP10 计费列幂等自愈：token_cost_prices.price_per_call + 按张计费种子行。
    # db_update.yaml 迁移只在应用启动时执行，测试进程不跑启动链路；而
    # TokenCostPriceDB.get_by_model_name 的 SELECT 已包含该列，不补列则计费
    # 相关用例全部失败。与 deploy/db_update.yaml "2026-09-15 21:30:00" 批次一致。
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "ALTER TABLE token_cost_prices ADD COLUMN IF NOT EXISTS "
                "price_per_call NUMERIC(10,4)"
            )
            cursor.execute(
                "INSERT INTO token_cost_prices (model_name, price_per_call) "
                "VALUES ('wechat_mp_image_parse', 0.01) "
                "ON CONFLICT (model_name) DO NOTHING"
            )
            conn.commit()
    except Exception:  # noqa: BLE001 单语句失败不阻断其余测试（require_db 侧兜底）
        pass

    _DB_AVAILABLE = True
    yield


@pytest.fixture(scope="session")
def wechat_mp_fixtures() -> Path:
    """tests/fixtures/wechat_mp/ 目录（WP3 真实抓取 + 合成夹具）。"""
    return Path(__file__).parent.parent.parent / "fixtures" / "wechat_mp"


@pytest.fixture()
def require_db():
    """真实 DB 依赖门禁：DB 不可用时跳过（仅真实 DB 测试使用）。"""
    if not _DB_AVAILABLE:
        pytest.skip("wechat_mp 真实 DB 测试需要可用 PostgreSQL DATABASE_URL")


@pytest.fixture()
def tenant_id(require_db):
    value = f"wmp_test_{uuid.uuid4().hex[:12]}"
    yield value
    cleanup_tenant(value)
