"""Integration test fixtures - real component combinations"""

# 必须在任何其他导入之前设置 DATABASE_URL
import os
from pathlib import Path
from dotenv import load_dotenv

# 加载 .env 文件
project_root = Path(__file__).parent.parent.parent
load_dotenv(project_root / ".env")

# 强制设置环境变量（会覆盖默认值）
db_url = os.getenv("DATABASE_URL", "")
if db_url:
    os.environ["DATABASE_URL"] = db_url
    os.environ["DB_POOL_MIN"] = "2"
    os.environ["DB_POOL_MAX"] = "10"

import pytest


@pytest.fixture(scope="session", autouse=True)
def init_db_pool():
    """Initialize PostgreSQL connection pool"""
    from src.db.database import init_postgres_pool, get_postgres_pool

    # Skip if already initialized
    if get_postgres_pool() is not None:
        yield
        return

    db_url = os.getenv("DATABASE_URL", "")
    if not db_url or "postgresql" not in db_url:
        pytest.skip("Integration tests require PostgreSQL DATABASE_URL")

    try:
        init_postgres_pool()
    except Exception as e:
        pytest.skip(f"Cannot connect to PostgreSQL: {e}")

    yield

    from src.db.database import close_postgres_pool
    close_postgres_pool()


@pytest.fixture
def db_connection():
    """Get database connection"""
    from src.db.database import get_db_connection
    with get_db_connection() as conn:
        yield conn