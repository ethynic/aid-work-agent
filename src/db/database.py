"""
数据库配置和连接管理
仅支持 PostgreSQL（含 GaussDB/openGauss 兼容）
"""

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional, Any
from urllib.parse import urlparse

from loguru import logger

try:
    import psycopg2
    from psycopg2 import pool as pg_pool
    from psycopg2 import extras as pg_extras
except ImportError:
    psycopg2 = None
    pg_pool = None
    pg_extras = None

# 数据库配置
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost/aid_work_agent")
DATABASE_ECHO = os.getenv("DATABASE_ECHO", "false").lower() == "true"

# PostgreSQL 连接池配置
DB_POOL_MIN = int(os.getenv("DB_POOL_MIN", "2"))
DB_POOL_MAX = int(os.getenv("DB_POOL_MAX", "10"))

# 模块级连接池
_pg_connection_pool = None

# 数据库类型（默认 PostgreSQL）
DB_TYPE = "postgresql"


def get_database_config() -> dict:
    """解析数据库配置"""
    parsed = urlparse(DATABASE_URL)
    driver = parsed.scheme

    if driver == "postgresql":
        return {
            "driver": "postgresql",
            "host": parsed.hostname or "localhost",
            "port": parsed.port or 5432,
            "database": parsed.path.lstrip("/"),
            "user": parsed.username,
            "password": parsed.password
        }
    else:
        logger.warning(f"Unknown database driver: {driver}, defaulting to PostgreSQL")
        return {
            "driver": "postgresql",
            "host": "localhost",
            "port": 5432,
            "database": "aid_work_agent",
            "user": "postgres",
            "password": "postgres"
        }


# 获取数据库配置
DB_CONFIG = get_database_config()

# 连接池

def init_postgres_pool(minconn: int = None, maxconn: int = None):
    """初始化 PostgreSQL 连接池"""
    global _pg_connection_pool

    if DB_TYPE != "postgresql":
        logger.warning("跳过连接池初始化，当前数据库不是 PostgreSQL")
        return

    if psycopg2 is None or pg_pool is None:
        raise ImportError("psycopg2 is required for PostgreSQL. Install with: pip install psycopg2-binary")

    minconn = minconn or DB_POOL_MIN
    maxconn = maxconn or DB_POOL_MAX

    _pg_connection_pool = pg_pool.ThreadedConnectionPool(
        minconn=minconn,
        maxconn=maxconn,
        host=DB_CONFIG["host"],
        port=DB_CONFIG["port"],
        database=DB_CONFIG["database"],
        user=DB_CONFIG["user"],
        password=DB_CONFIG["password"],
        # TCP keepalive：防止空闲连接被防火墙/服务器断开
        keepalives=1,
        keepalives_idle=60,      # 空闲60秒后开始发送keepalive
        keepalives_interval=10,  # 每10秒重试
        keepalives_count=6,      # 6次无响应则断开
        # 连接超时
        connect_timeout=10,
        # 应用名称（方便在 pg_stat_activity 中识别）
        application_name="aid-work-agent"
    )
    logger.info(f"PostgreSQL 连接池初始化完成: min={minconn}, max={maxconn}")


def get_postgres_pool():
    """获取 PostgreSQL 连接池"""
    return _pg_connection_pool


def get_pooled_connection(max_retries: int = 3):
    """从连接池获取连接，并检查连接有效性"""
    if _pg_connection_pool is None:
        raise RuntimeError("PostgreSQL 连接池未初始化，请先调用 init_postgres_pool()")

    for attempt in range(max_retries):
        conn = _pg_connection_pool.getconn()

        # 检查连接是否有效
        try:
            if conn.closed:
                logger.warning("PostgreSQL 连接已关闭，重新获取 (attempt %d/%d)", attempt + 1, max_retries)
                _pg_connection_pool.putconn(conn, close=True)
                continue
            # 用轻量查询检测连接是否真的活着
            # conn.isolation_level 不发网络请求，无法检测服务端断开
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.close()
        except (psycopg2.OperationalError, psycopg2.InterfaceError):
            logger.warning("PostgreSQL 连接失效，重新获取 (attempt %d/%d)", attempt + 1, max_retries)
            try:
                _pg_connection_pool.putconn(conn, close=True)
            except Exception:
                pass
            continue
        except Exception as e:
            logger.warning("PostgreSQL 连接检查异常: %s，重新获取 (attempt %d/%d)", e, attempt + 1, max_retries)
            try:
                _pg_connection_pool.putconn(conn, close=True)
            except Exception:
                pass
            continue

        return conn

    raise RuntimeError(f"获取数据库连接失败：连续 {max_retries} 次获取到无效连接")


def return_pooled_connection(conn):
    """归还连接到连接池"""
    if _pg_connection_pool and conn:
        _pg_connection_pool.putconn(conn)


def close_postgres_pool():
    """关闭连接池"""
    global _pg_connection_pool
    if _pg_connection_pool:
        _pg_connection_pool.closeall()
        _pg_connection_pool = None
        logger.info("PostgreSQL 连接池已关闭")


def get_postgres_pool_status() -> dict:
    """获取连接池状态"""
    if _pg_connection_pool is None:
        return {"initialized": False}

    return {
        "initialized": True,
        "minconn": _pg_connection_pool.minconn,
        "maxconn": _pg_connection_pool.maxconn,
        "dsn": _pg_connection_pool.dsn,
    }


def get_db_placeholder() -> str:
    """获取当前数据库的参数占位符"""
    return "%s"


def get_current_timestamp() -> str:
    """获取当前数据库的时间戳函数"""
    # PostgreSQL 使用 INTERVAL
    return "CURRENT_TIMESTAMP"


def get_date_offset(days: int) -> str:
    """
    获取指定天数前的日期时间函数
    Args:
        days: 负数表示过去，正数表示未来
    Returns:
        SQL 函数调用字符串
    """
    # PostgreSQL 使用 INTERVAL
    return f"CURRENT_TIMESTAMP + INTERVAL '{days} days'"


@contextmanager
def get_db_connection() -> Generator[Any, None, None]:
    """获取 PostgreSQL 数据库连接的上下文管理器"""
    if psycopg2 is None or pg_pool is None:
        raise ImportError("psycopg2 is required. Install with: pip install psycopg2-binary")

    # 从连接池获取连接
    conn = get_pooled_connection()

    # 使用 DictCursor 使 psycopg2 返回字典-like 对象
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # 包装 cursor，添加 commit/rollback 方法以便兼容旧代码
    class CursorWrapper:
        def __init__(self, cursor, conn):
            self._cursor = cursor
            self._conn = conn

        def __getattr__(self, name):
            return getattr(self._cursor, name)

        def cursor(self):
            """兼容旧 API：返回 cursor 本身"""
            return self._cursor

        def commit(self):
            self._conn.commit()

        def rollback(self):
            self._conn.rollback()

        def close(self):
            pass  # 不实际关闭，由上下文管理器处理

    wrapper = CursorWrapper(cursor, conn)

    try:
        yield wrapper
    finally:
        # 归还连接到池而非关闭
        return_pooled_connection(conn)


def init_database():
    """初始化数据库表（PostgreSQL）"""
    _init_postgresql()


def _init_postgresql():
    """初始化 PostgreSQL 数据库表"""
    with get_db_connection() as conn:
        cursor = conn.cursor()

        # 用户表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                user_id TEXT UNIQUE NOT NULL,
                username TEXT,
                phone TEXT,
                password_hash TEXT,
                wx_openid TEXT,
                wx_unionid TEXT,
                avatar_url TEXT,
                tenant_id TEXT,
                role TEXT DEFAULT 'user',
                status TEXT DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 租户内手机号唯一约束（不同租户允许相同手机号）
        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_users_tenant_phone
            ON users (tenant_id, phone)
            WHERE phone IS NOT NULL AND tenant_id IS NOT NULL
        """)

        # 迁移：删除旧的 phone 全局唯一约束（如果存在）
        cursor.execute("""
            SELECT conname FROM pg_constraint
            WHERE conrelid = 'users'::regclass AND contype = 'u'
            AND conname LIKE '%phone%'
        """)
        old_constraint = cursor.fetchone()
        if old_constraint:
            cursor.execute(f'ALTER TABLE users DROP CONSTRAINT {old_constraint[0]}')
            logger.info(f"Dropped old phone unique constraint: {old_constraint[0]}")

        # 同步删除旧的 phone 唯一索引（如果存在）
        cursor.execute("""
            SELECT indexname FROM pg_indexes
            WHERE tablename = 'users' AND indexname = 'users_phone_key'
        """)
        old_index = cursor.fetchone()
        if old_index:
            cursor.execute('DROP INDEX IF EXISTS users_phone_key')
            logger.info("Dropped old phone unique index: users_phone_key")

        # 会话表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_sessions (
                id SERIAL PRIMARY KEY,
                session_id TEXT UNIQUE NOT NULL,
                user_id TEXT NOT NULL,
                tenant_id TEXT,
                subagent_id TEXT,
                title TEXT,
                context_data TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)

        # 消息表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_messages (
                id SERIAL PRIMARY KEY,
                message_id TEXT UNIQUE NOT NULL,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT,
                metadata TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES chat_sessions(session_id)
            )
        """)

        # 会话记录表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_records (
                id SERIAL PRIMARY KEY,
                record_id TEXT UNIQUE NOT NULL,
                session_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                user_message TEXT NOT NULL,
                assistant_message TEXT,
                total_token_count INTEGER DEFAULT 0,
                prompt_tokens INTEGER DEFAULT 0,
                completion_tokens INTEGER DEFAULT 0,
                model TEXT,
                execution_details TEXT,
                status TEXT DEFAULT 'completed',
                error_message TEXT,
                duration_ms INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES chat_sessions(session_id),
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)

        # 索引
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_chat_records_session
            ON chat_records(session_id, created_at DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_chat_records_user
            ON chat_records(user_id, created_at DESC)
        """)

        # 验证码表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sms_codes (
                id SERIAL PRIMARY KEY,
                phone TEXT NOT NULL,
                code TEXT NOT NULL,
                used INTEGER DEFAULT 0,
                expires_at TIMESTAMP NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 图形验证码表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS captchas (
                captcha_id TEXT PRIMARY KEY,
                code TEXT NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 远程连接凭据表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS remote_credentials (
                id SERIAL PRIMARY KEY,
                credential_id TEXT UNIQUE NOT NULL,
                user_id TEXT NOT NULL,
                connection_type TEXT NOT NULL,
                server_host TEXT NOT NULL,
                server_port INTEGER NOT NULL,
                username TEXT NOT NULL,
                password TEXT NOT NULL,
                remote_path TEXT NOT NULL,
                domain TEXT,
                name TEXT,
                description TEXT,
                status TEXT DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_remote_credentials_user
            ON remote_credentials(user_id, status, created_at DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_remote_credentials_path
            ON remote_credentials(user_id, remote_path, status)
        """)

        # Token 表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tokens (
                id SERIAL PRIMARY KEY,
                token TEXT UNIQUE NOT NULL,
                user_id TEXT NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_tokens_token
            ON tokens(token)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_tokens_user
            ON tokens(user_id, expires_at)
        """)

        # 定时任务表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_tasks (
                id SERIAL PRIMARY KEY,
                task_id TEXT UNIQUE NOT NULL,
                user_id TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                task_prompt TEXT NOT NULL,
                schedule_type TEXT NOT NULL,
                cron_expression TEXT,
                interval_seconds INTEGER,
                session_id TEXT,
                status TEXT DEFAULT 'active',
                max_retries INTEGER DEFAULT 3,
                retry_count INTEGER DEFAULT 0,
                last_run_at TIMESTAMP,
                next_run_at TIMESTAMP,
                total_runs INTEGER DEFAULT 0,
                success_count INTEGER DEFAULT 0,
                fail_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_user
            ON scheduled_tasks(user_id, status, created_at DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_next_run
            ON scheduled_tasks(next_run_at, status)
        """)

        # 定时任务执行日志表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_task_logs (
                id SERIAL PRIMARY KEY,
                log_id TEXT UNIQUE NOT NULL,
                task_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                session_id TEXT,
                status TEXT NOT NULL,
                trigger_type TEXT NOT NULL,
                result_summary TEXT,
                result_detail TEXT,
                error_message TEXT,
                error_trace TEXT,
                duration_ms INTEGER DEFAULT 0,
                token_usage INTEGER DEFAULT 0,
                started_at TIMESTAMP NOT NULL,
                completed_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (task_id) REFERENCES scheduled_tasks(task_id),
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_scheduled_task_logs_task
            ON scheduled_task_logs(task_id, created_at DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_scheduled_task_logs_user
            ON scheduled_task_logs(user_id, created_at DESC)
        """)

        # 文档表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id SERIAL PRIMARY KEY,
                user_id TEXT,
                title TEXT NOT NULL,
                source_type TEXT NOT NULL,
                file_type TEXT NOT NULL,
                file_path TEXT NOT NULL,
                file_size INTEGER,
                total_chunks INTEGER NOT NULL,
                embedding_model TEXT NOT NULL,
                thumbnail_path TEXT,
                duration INTEGER,
                width INTEGER,
                height INTEGER,
                mime_type TEXT,
                raw_text TEXT,
                metadata TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_documents_user
            ON documents(user_id)
        """)

        # 文本块表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chunks (
                id SERIAL PRIMARY KEY,
                doc_id INTEGER NOT NULL,
                chunk_index INTEGER NOT NULL,
                text TEXT NOT NULL,
                text_vec tsvector,
                tokens INTEGER NOT NULL,
                metadata TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (doc_id) REFERENCES documents(id) ON DELETE CASCADE
            )
        """)

        # 确保 text_vec 列存在（旧表迁移）
        cursor.execute("""
            ALTER TABLE chunks ADD COLUMN IF NOT EXISTS text_vec tsvector
        """)

        # 创建 GIN 索引用于全文检索
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_chunks_text_vec
            ON chunks USING GIN (text_vec)
        """)

        # 创建自动更新 text_vec 的触发器
        cursor.execute("""
            CREATE OR REPLACE FUNCTION chunks_text_vec_update()
            RETURNS TRIGGER AS $$
            BEGIN
                NEW.text_vec := to_tsvector('simple', NEW.text);
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
        """)

        # PostgreSQL 不支持 CREATE TRIGGER IF NOT EXISTS，需要先删除再创建
        cursor.execute("""
            DROP TRIGGER IF EXISTS chunks_text_vec_trigger ON chunks
        """)
        cursor.execute("""
            CREATE TRIGGER chunks_text_vec_trigger
            BEFORE INSERT OR UPDATE ON chunks
            FOR EACH ROW EXECUTE FUNCTION chunks_text_vec_update()
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_chunks_doc
            ON chunks(doc_id)
        """)

        # 启用 pgvector 扩展
        cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")

        # 向量表 (pgvector)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chunks_vec (
                chunk_id INTEGER PRIMARY KEY,
                embedding vector(1024)
            )
        """)

        # 创建 HNSW 索引
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_chunks_vec_embedding
            ON chunks_vec USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64)
        """)

        # 用户邮箱配置表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_email_settings (
                id SERIAL PRIMARY KEY,
                user_id TEXT UNIQUE NOT NULL,
                email_address TEXT NOT NULL,
                smtp_server TEXT NOT NULL,
                smtp_port INTEGER NOT NULL,
                smtp_user TEXT NOT NULL,
                smtp_password TEXT NOT NULL,
                smtp_encryption TEXT DEFAULT 'ssl',
                imap_server TEXT NOT NULL,
                imap_port INTEGER NOT NULL,
                imap_encryption TEXT DEFAULT 'ssl',
                status TEXT DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_user_email_settings_user
            ON user_email_settings(user_id)
        """)

        conn.commit()
        logger.info(f"PostgreSQL database initialized at {DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}")

        # 初始化 SaaS 多租户表
        try:
            from src.saas.db.tables import init_saas_tables_postgresql
            init_saas_tables_postgresql(conn)
        except Exception as e:
            logger.warning(f"Failed to initialize SaaS tables (saas module may not be configured): {e}")

        # 初始化客户管理表（trade-customer skill）
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "customer_manager",
                str(Path(__file__).parent.parent / "skills" / "trade-customer-1.0.0" / "scripts" / "customer_manager.py")
            )
            customer_manager = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(customer_manager)
            customer_manager.init_tables()
        except Exception as e:
            logger.warning(f"Failed to initialize customer tables: {e}")


if __name__ == "__main__":
    init_database()
    print(f"Database initialized: {DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}")