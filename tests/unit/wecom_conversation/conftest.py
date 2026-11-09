"""wecom_conversation 测试 fixtures（真实 DB 模式，照 boss_conversation 范式）。

必须在 `./scripts/dev_test.sh --isolated-db` 提供的本机 aid_test_* 库上运行；
无 DATABASE_URL 时整目录跳过。每用例独立随机租户，测后按表清理。
"""

import os
import uuid
from pathlib import Path

import pytest
from dotenv import load_dotenv

project_root = Path(__file__).parent.parent.parent.parent
load_dotenv(project_root / ".env")
db_url = os.getenv("DATABASE_URL", "")

WECOM_TABLES = ("bs_wecom_proxy_batches", "bs_wecom_proxy_task_sessions", "bs_wecom_proxy_tasks",
                "bs_wecom_daily_summaries", "bs_wecom_messages", "bs_wecom_conversations")

RECEIPT_TABLES = ("local_tool_invocations", "chat_sessions", "scheduled_tasks",
                  # P6 派发/核定测试直插的 Runner 行与权限行（按 tenant_id 清理）
                  "user_agent_permissions", "subscriptions", "users", "agent_runners")


def _cleanup_tenant(tenant_id: str) -> None:
    from src.db.database import get_db_connection

    if not db_url:
        return
    with get_db_connection() as conn:
        cursor = conn.cursor()
        for table in WECOM_TABLES:
            cursor.execute(f"DELETE FROM {table} WHERE tenant_id = %s", (tenant_id,))
        for table in RECEIPT_TABLES:
            cursor.execute(f"DELETE FROM {table} WHERE tenant_id = %s", (tenant_id,))
        conn.commit()


@pytest.fixture(scope="session")
def db_ready():
    """SQL 测试前置：初始化连接池并幂等建表；不可用时跳过本批 SQL 用例。"""
    if not db_url or "postgresql" not in db_url:
        pytest.skip("未配置 PostgreSQL DATABASE_URL（请用 dev_test.sh --isolated-db 运行）")
    from src.db.database import init_postgres_pool, get_postgres_pool

    if get_postgres_pool() is None:
        try:
            init_postgres_pool()
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"无法连接 PostgreSQL: {exc}")
    from src.db.database import get_db_connection
    from src.wecom_conversation.tables import init_tables

    with get_db_connection() as conn:
        init_tables(conn)
        init_tables(conn)  # 幂等：重复执行无错
    yield


@pytest.fixture()
def tenant_id():
    value = f"wecom_test_{uuid.uuid4().hex[:12]}"
    yield value
    _cleanup_tenant(value)
