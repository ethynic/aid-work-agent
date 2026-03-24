"""
数据库配置和连接管理
支持SQLite（默认）和 PostgreSQL/MySQL 通过 DATABASE_URL 配置
"""

import os
import sqlite3
from contextlib import contextmanager
from typing import Generator, Optional
from urllib.parse import urlparse

from loguru import logger

# 数据库配置
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./aid_work_agent.db")
DATABASE_ECHO = os.getenv("DATABASE_ECHO", "false").lower() == "true"


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

# SQLite 连接池（简单实现）
_sqlite_connections = {}


def get_sqlite_path() -> str:
    """获取SQLite数据库路径"""
    if DB_CONFIG["driver"] == "sqlite":
        return DB_CONFIG["path"]
    return "./aid_work_agent.db"


@contextmanager
def get_db_connection() -> Generator[sqlite3.Connection, None, None]:
    """获取SQLite连接的上下文管理器"""
    db_path = get_sqlite_path()
    
    # 确保目录存在
    os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True)
    
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_database():
    """初始化数据库表"""
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

        conn.commit()
        logger.info(f"Database initialized at {get_sqlite_path()}")


if __name__ == "__main__":
    init_database()
    print(f"Database initialized at: {get_sqlite_path()}")