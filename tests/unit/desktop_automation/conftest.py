"""desktop_automation 单元测试 fixtures（真实 DB 模式：DATABASE_URL 可用时直连，否则 skip）

沿用 tests/integration/conftest.py 的池初始化方式；租户用随机字符串（本表族 SQL 不做
tenants 表 join，无需真实租户行），每用例独立 tenant + 测后物理清理全部相关表。
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

# 本表族全部表（T-P1-1：按依赖序清理——deliveries/attempts 先于 runs/occurrences，
# schedules/subjects 先于 events/quota/outbox/audit；rowcount 核实，失败告警不吞错）
DA_TABLES_BY_ORDER = (
    "desktop_automation_attempts",
    "desktop_automation_evidence",
    "desktop_automation_deliveries",
    "desktop_automation_runs",
    "desktop_automation_occurrences",
    "desktop_automation_outbox",
    "desktop_automation_events",
    "desktop_automation_event_sources",
    "desktop_automation_schedules",
    "desktop_automation_subjects",
    "desktop_automation_audit_events",
    "desktop_automation_quota_buckets",
    "local_tool_operation_permits",
    "local_tool_events",
    "local_tool_invocations",
    "local_tool_devices",
)
# 兼容既有引用（tests/integration 清理复用）
DA_TABLES = DA_TABLES_BY_ORDER


def cleanup_tenant(tenant_id: str) -> None:
    """删除指定租户在本表族的全部行；任何失败 logger.warning 记录表名与异常（不静默吞错），
    清理后按表核实残留并告警（T-P1-1：杜绝静默泄漏）"""
    from src.db.database import get_db_connection

    failed = []
    residual = []
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            for table in DA_TABLES_BY_ORDER:
                try:
                    cur.execute(f"DELETE FROM {table} WHERE tenant_id = %s", (tenant_id,))
                except Exception as e:  # noqa: BLE001 单表失败不阻断其余表清理
                    failed.append((table, str(e)))
            conn.commit()
            for table in DA_TABLES_BY_ORDER:
                cur.execute(
                    f"SELECT COUNT(*) AS c FROM {table} WHERE tenant_id = %s", (tenant_id,)
                )
                if cur.fetchone()["c"]:
                    residual.append(table)
    except Exception as e:  # noqa: BLE001
        failed.append(("<connection>", str(e)))
    if failed or residual:
        import logging

        logging.getLogger(__name__).warning(
            "desktop_automation 测试租户清理异常 tenant=%s failed=%s residual=%s",
            tenant_id, failed, residual,
        )


def da_tables_ready() -> bool:
    """探测 desktop_automation 表族是否已建（未建时 skip，避免误报）"""
    try:
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name='desktop_automation_subjects'"
            )
            return cur.fetchone() is not None
    except Exception:
        return False


@pytest.fixture(scope="session", autouse=True)
def _init_db_pool():
    from src.db.database import init_postgres_pool, get_postgres_pool, close_postgres_pool

    if get_postgres_pool() is not None:
        yield
        return
    if not db_url or "postgresql" not in db_url:
        pytest.skip("desktop_automation 单测需要 PostgreSQL DATABASE_URL")
    try:
        init_postgres_pool()
    except Exception as e:
        pytest.skip(f"无法连接 PostgreSQL: {e}")
    if not da_tables_ready():
        close_postgres_pool()
        pytest.skip("desktop_automation 表未初始化（先运行 init_database）")
    yield
    close_postgres_pool()


@pytest.fixture()
def tenant_id():
    """每用例独立租户 + 测后按依赖序清理（失败告警，不静默吞错）"""
    tid = f"da_test_{uuid.uuid4().hex[:12]}"
    yield tid
    cleanup_tenant(tid)
