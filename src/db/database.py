"""
数据库配置和连接管理
支持SQLite（默认）和 PostgreSQL/MySQL 通过 DATABASE_URL 配置
"""

import os
import sqlite3
from contextlib import contextmanager
from typing import Generator, Optional, Any, Union
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
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./aid_work_agent.db")
DATABASE_ECHO = os.getenv("DATABASE_ECHO", "false").lower() == "true"

# PostgreSQL 连接池配置
DB_POOL_MIN = int(os.getenv("DB_POOL_MIN", "2"))
DB_POOL_MAX = int(os.getenv("DB_POOL_MAX", "10"))

# 模块级连接池
_pg_connection_pool = None


def get_database_config() -> dict:
    """解析数据库配置"""
    parsed = urlparse(DATABASE_URL)
    driver = parsed.scheme

    if driver == "sqlite":
        db_path = parsed.path.lstrip("/")
        # 如果是相对路径，转换为绝对路径
        if not os.path.isabs(db_path):
            db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), db_path)
        return {
            "driver": "sqlite",
            "path": db_path
        }
    elif driver == "postgresql":
        return {
            "driver": "postgresql",
            "host": parsed.hostname or "localhost",
            "port": parsed.port or 5432,
            "database": parsed.path.lstrip("/"),
            "user": parsed.username,
            "password": parsed.password
        }
    elif driver == "mysql":
        return {
            "driver": "mysql",
            "host": parsed.hostname or "localhost",
            "port": parsed.port or 3306,
            "database": parsed.path.lstrip("/"),
            "user": parsed.username,
            "password": parsed.password
        }
    else:
        logger.warning(f"Unknown database driver: {driver}, falling back to SQLite")
        return {"driver": "sqlite", "path": "./aid_work_agent.db"}


# 获取数据库配置
DB_CONFIG = get_database_config()

# 数据库类型
DB_TYPE = DB_CONFIG.get("driver", "sqlite")

# 连接池
_sqlite_connections = {}
_pg_connections = {}


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
        password=DB_CONFIG["password"]
    )
    logger.info(f"PostgreSQL 连接池初始化完成: min={minconn}, max={maxconn}")


def get_postgres_pool():
    """获取 PostgreSQL 连接池"""
    return _pg_connection_pool


def get_pooled_connection():
    """从连接池获取连接，并检查连接有效性"""
    if _pg_connection_pool is None:
        raise RuntimeError("PostgreSQL 连接池未初始化，请先调用 init_postgres_pool()")

    conn = _pg_connection_pool.getconn()

    # 检查连接是否有效
    try:
        if conn.closed:
            # 连接已关闭，重新获取
            logger.warning("PostgreSQL 连接已关闭，重新获取")
            return get_pooled_connection()
        # 执行简单查询检查连接状态
        conn.isolation_level
    except (psycopg2.OperationalError, psycopg2.InterfaceError):
        # 连接失效，重新获取
        logger.warning("PostgreSQL 连接失效，重新获取")
        return get_pooled_connection()

    return conn


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


def get_sqlite_path() -> str:
    """获取SQLite数据库路径"""
    if DB_CONFIG["driver"] == "sqlite":
        return DB_CONFIG["path"]
    return "./aid_work_agent.db"


def get_db_placeholder() -> str:
    """获取当前数据库的参数占位符"""
    if DB_TYPE == "postgresql":
        return "%s"
    return "?"  # SQLite


def get_current_timestamp() -> str:
    """获取当前数据库的时间戳函数"""
    # SQLite 和 PostgreSQL 语法相同
    return "CURRENT_TIMESTAMP"

class PGRow:
    """PostgreSQL 行包装器，提供类似 sqlite3.Row 的接口"""

    def __init__(self, data: dict):
        self._data = data

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self._data.values())[key]
        return self._data.get(key)

    def __iter__(self):
        return iter(self._data)

    def keys(self):
        return self._data.keys()

    def values(self):
        return self._data.values()

    def items(self):
        return self._data.items()

    def __len__(self):
        return len(self._data)

    def __contains__(self, key):
        return key in self._data

    def get(self, key, default=None):
        return self._data.get(key, default)

    def __repr__(self):
        return f"PGRow({self._data})"


@contextmanager
def get_db_connection() -> Generator[Any, None, None]:
    """获取数据库连接的上下文管理器"""
    if DB_TYPE == "sqlite":
        db_path = get_sqlite_path()
        os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True)

        conn = sqlite3.connect(db_path, check_same_thread=False, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        try:
            yield conn
        finally:
            conn.close()
    elif DB_TYPE == "postgresql":
        if psycopg2 is None or pg_pool is None:
            raise ImportError("psycopg2 is required for PostgreSQL. Install with: pip install psycopg2-binary")

        # 从连接池获取连接
        conn = get_pooled_connection()

        # 使用 DictCursor 使 psycopg2 返回字典-like 对象
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # 创建一个包装器，使 cursor 具有 sqlite3 Row 兼容的行为
        class DictCursorWrapper:
            def __init__(self, cursor):
                self._cursor = cursor

            def execute(self, query, params=None):
                # 自动将 SQLite 的 ? 占位符转换为 PostgreSQL 的 %s
                if params and "?" in query:
                    query = query.replace("?", "%s")
                return self._cursor.execute(query, params)

            def executemany(self, query, params_list):
                # 自动将 SQLite 的 ? 占位符转换为 PostgreSQL 的 %s
                if params_list and "?" in query:
                    query = query.replace("?", "%s")
                return self._cursor.executemany(query, params_list)

            def fetchone(self):
                row = self._cursor.fetchone()
                if row is None:
                    return None
                # 转换为类似 sqlite3.Row 的对象
                return PGRow(dict(row))

            def fetchall(self):
                rows = self._cursor.fetchall()
                return [PGRow(dict(row)) for row in rows]

            @property
            def rowcount(self):
                return self._cursor.rowcount

        wrapper = DictCursorWrapper(cursor)

        # 创建一个连接包装器，替换 cursor 方法
        class PGConnectionWrapper:
            def __init__(self, conn, cursor):
                self._conn = conn
                self._cursor = cursor
                self._wrapper = wrapper

            def cursor(self):
                return self._wrapper

            def execute(self, query, params=None):
                """直接执行 SQL（用于 DDL）"""
                return self._cursor.execute(query, params)

            def commit(self):
                self._conn.commit()

            def rollback(self):
                self._conn.rollback()

            def close(self):
                self._cursor.close()
                self._conn.close()

        try:
            yield PGConnectionWrapper(conn, cursor)
        finally:
            # 归还连接到池而非关闭
            return_pooled_connection(conn)
    else:
        # Fallback to SQLite
        db_path = get_sqlite_path()
        os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True)
        conn = sqlite3.connect(db_path, check_same_thread=False, timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()


def init_database():
    """初始化数据库表"""
    if DB_TYPE == "postgresql":
        _init_postgresql()
    else:
        _init_sqlite()


def _init_sqlite():
    """初始化 SQLite 数据库表"""
    with get_db_connection() as conn:
        cursor = conn.cursor()

        # 用户表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT UNIQUE NOT NULL,
                username TEXT,
                phone TEXT UNIQUE,
                password_hash TEXT,
                wx_openid TEXT UNIQUE,
                wx_unionid TEXT,
                avatar_url TEXT,
                tenant_id TEXT,
                status INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 会话表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT UNIQUE NOT NULL,
                user_id TEXT NOT NULL,
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
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id TEXT UNIQUE NOT NULL,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT,
                metadata TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES chat_sessions(session_id)
            )
        """)

        # 会话记录表（每次和AI的对话）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
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
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone TEXT NOT NULL,
                code TEXT NOT NULL,
                used INTEGER DEFAULT 0,
                expires_at TIMESTAMP NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 远程连接凭据表 (SMB/FTP)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS remote_credentials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
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
                status INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)

        # 远程凭据索引
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_remote_credentials_user
            ON remote_credentials(user_id, status, created_at DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_remote_credentials_path
            ON remote_credentials(user_id, remote_path, status)
        """)

        # Token 表（用于多进程共享 session）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token TEXT UNIQUE NOT NULL,
                user_id TEXT NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)

        # Token 索引
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
                id INTEGER PRIMARY KEY AUTOINCREMENT,
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
                id INTEGER PRIMARY KEY AUTOINCREMENT,
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

        # ============== Knowledge Base Tables ==============

        # 文档表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
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
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_documents_user
            ON documents(user_id)
        """)

        # 文本块表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                doc_id INTEGER NOT NULL,
                chunk_index INTEGER NOT NULL,
                text TEXT NOT NULL,
                tokens INTEGER NOT NULL,
                metadata TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (doc_id) REFERENCES documents(id) ON DELETE CASCADE
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_chunks_doc
            ON chunks(doc_id)
        """)

        # 尝试创建向量表（sqlite-vec），失败则跳过
        try:
            cursor.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0(
                    chunk_id INTEGER PRIMARY KEY,
                    embedding float[1024]
                )
            """)
        except Exception as e:
            logger.warning(f"无法创建向量表 (sqlite-vec 可能未安装): {e}")

        # 尝试创建 FTS5 表，失败则跳过
        try:
            cursor.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                    text,
                    content=chunks,
                    content_rowid=id,
                    tokenize = 'unicode61'
                )
            """)

            # 创建触发器同步 FTS5
            cursor.execute("""
                CREATE TRIGGER IF NOT EXISTS chunks_fts_insert AFTER INSERT ON chunks BEGIN
                    INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
                END
            """)

            cursor.execute("""
                CREATE TRIGGER IF NOT EXISTS chunks_fts_delete AFTER DELETE ON chunks BEGIN
                    INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES('delete', old.id, old.text);
                END
            """)

            cursor.execute("""
                CREATE TRIGGER IF NOT EXISTS chunks_fts_update AFTER UPDATE ON chunks BEGIN
                    INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES('delete', old.id, old.text);
                    INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
                END
            """)
        except Exception as e:
            logger.warning(f"无法创建 FTS5 表: {e}")

        # 用户邮箱配置表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_email_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
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
                status INTEGER DEFAULT 1,
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
        logger.info(f"SQLite database initialized at {get_sqlite_path()}")

        # 初始化 SaaS 多租户表
        try:
            from src.saas.db.tables import init_saas_tables_sqlite
            init_saas_tables_sqlite(conn)
        except Exception as e:
            logger.warning(f"Failed to initialize SaaS tables (saas module may not be configured): {e}")


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
                phone TEXT UNIQUE,
                password_hash TEXT,
                wx_openid TEXT UNIQUE,
                wx_unionid TEXT,
                avatar_url TEXT,
                tenant_id TEXT,
                status INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 会话表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_sessions (
                id SERIAL PRIMARY KEY,
                session_id TEXT UNIQUE NOT NULL,
                user_id TEXT NOT NULL,
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
                status INTEGER DEFAULT 1,
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
                user_id INTEGER,
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
                status INTEGER DEFAULT 1,
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


if __name__ == "__main__":
    init_database()
    print(f"Database initialized at: {get_sqlite_path()}")