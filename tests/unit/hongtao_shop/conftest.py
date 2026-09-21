"""hongtao_shop 单元测试 fixtures（真实 DB 模式，照 wechat_mp conftest 范式）。

每用例独立随机租户，测后清理通用 content_sync 表的本租户行与测试 documents 行
（模块零私有表）。
纯逻辑测试（fetcher/joiner/renderer）不依赖真实 DB，DB 不可用时照常运行。
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


def cleanup_tenant(tenant_id: str) -> None:
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        # 模块零私有表：清理通用 content_sync 表的本租户行
        # （module 表加模块过滤；bs_image_vision_cache 无 module 列按租户清）
        for table in (
            "bs_content_sync_items",
            "bs_content_sync_runs",
            "bs_content_sync_records",
            "bs_content_sync_sources",
        ):
            try:
                cursor = conn.cursor()
                cursor.execute(
                    f"DELETE FROM {table} WHERE tenant_id = %s AND module = 'hongtao_shop'",
                    (tenant_id,),
                )
            except Exception:  # noqa: BLE001 单表失败不阻断其余清理
                conn.rollback()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM bs_image_vision_cache WHERE tenant_id = %s", (tenant_id,)
            )
        except Exception:  # noqa: BLE001
            conn.rollback()
        # 入库链路残留：chunks_vec/chunks 无 tenant_id 列，经 documents 子查询清理
        for sql in (
            "DELETE FROM chunks_vec WHERE chunk_id IN "
            "(SELECT id FROM chunks WHERE doc_id IN "
            "(SELECT id FROM documents WHERE tenant_id = %s))",
            "DELETE FROM chunks WHERE doc_id IN "
            "(SELECT id FROM documents WHERE tenant_id = %s)",
            "DELETE FROM documents WHERE tenant_id = %s",
            "DELETE FROM knowledge_categories WHERE tenant_id = %s",
            # 计费链路真实落账（embedding/VL chat_records）与余额预检租户行
            "DELETE FROM chat_records WHERE tenant_id = %s",
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
    """尝试初始化 DB 连接池；不可用时只记录标志，由 require_db 决定跳过。"""
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
    # 通用表建表幂等（含计费种子行）
    from src.db.database import get_db_connection
    from src.services.content_sync.db import init_content_sync_tables

    try:
        with get_db_connection() as conn:
            init_content_sync_tables(conn)
    except Exception:  # noqa: BLE001
        yield
        return

    _DB_AVAILABLE = True
    yield


@pytest.fixture()
def require_db():
    """真实 DB 依赖门禁：DB 不可用时跳过（仅真实 DB 测试使用）。"""
    if not _DB_AVAILABLE:
        pytest.skip("hongtao_shop 真实 DB 测试需要可用 PostgreSQL DATABASE_URL")


@pytest.fixture()
def tenant_id(require_db):
    value = f"hts_test_{uuid.uuid4().hex[:12]}"
    yield value
    cleanup_tenant(value)
