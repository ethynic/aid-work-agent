"""
数据库配置和连接管理
仅支持 PostgreSQL（含 GaussDB/openGauss 兼容）
"""

import os
import time
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
# 默认值适配 Gunicorn 多 worker 场景：单 worker 池上限 15，4 worker 总并发 60（PG max_connections=80 仍留有余量）
# 通过环境变量 DB_POOL_MIN / DB_POOL_MAX 可覆盖
DB_POOL_MIN = int(os.getenv("DB_POOL_MIN", "2"))
DB_POOL_MAX = int(os.getenv("DB_POOL_MAX", "15"))

# 追踪库配置（可观测性数据，独立数据库）
LOGS_DATABASE_URL = os.getenv("LOGS_DATABASE_URL", "")
LOGS_DB_POOL_MIN = int(os.getenv("LOGS_DB_POOL_MIN", "1"))
LOGS_DB_POOL_MAX = int(os.getenv("LOGS_DB_POOL_MAX", "3"))

# 模块级连接池
_pg_connection_pool = None
_logs_connection_pool = None

# 连接池后台健康检查线程
_pool_health_check_thread = None

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
        keepalives_idle=30,      # 空闲30秒后开始发送keepalive（降低到60→30）
        keepalives_interval=5,   # 每5秒重试（降低到10→5）
        keepalives_count=3,      # 3次无响应则断开（降低到6→3）
        # 连接超时（降低到5秒，避免长时间阻塞）
        connect_timeout=5,
        # 语句超时（30秒保护，防止慢查询堆积）
        options="-c statement_timeout=30000",
        # 应用名称（方便在 pg_stat_activity 中识别）
        application_name="aid-work-agent"
    )
    logger.info(f"PostgreSQL 连接池初始化完成: min={minconn}, max={maxconn}")

    # 启动后台健康检查线程：定期校验池中空闲连接，主动丢弃失效连接
    # 必要性：容器经 Docker NAT 访问外部 PG，中间网络层会在 ~30s 空闲后回收 TCP 状态，
    # 而 psycopg2 的 keepalives 参数在当前环境未生效（OS 默认 7200s）。
    # 后台线程每 POOL_HEALTH_CHECK_INTERVAL 秒对池内连接做 SELECT 1，保证业务侧
    # get_pooled_connection 取到的连接总是刚校验过的，避免 "server closed the connection
    # unexpectedly" 警告。
    _start_pool_health_check_thread()


def _start_pool_health_check_thread():
    """启动连接池后台健康检查守护线程"""
    global _pool_health_check_thread

    # 避免重复启动
    if _pool_health_check_thread is not None and _pool_health_check_thread.is_alive():
        return

    interval = int(os.getenv("POOL_HEALTH_CHECK_INTERVAL", "120")) # 2分钟，需要显著小于 POSTGRES_IDLE_SESSION_TIMEOUT

    def _loop():
        while True:
            try:
                time.sleep(interval)
                if _pg_connection_pool is not None:
                    health_check_pool()
            except Exception as e:
                # 线程内异常不能让线程退出，记日志后继续
                logger.warning(f"PostgreSQL 连接池健康检查线程异常（已忽略，继续运行）: {e}")
                time.sleep(interval)

    import threading
    _pool_health_check_thread = threading.Thread(
        target=_loop, name="pg-pool-health-check", daemon=True
    )
    _pool_health_check_thread.start()
    logger.info(f"PostgreSQL 连接池健康检查线程已启动，检查间隔: {interval}s")


def get_postgres_pool():
    """获取 PostgreSQL 连接池"""
    return _pg_connection_pool


def get_pooled_connection(max_retries: int = 2) -> psycopg2.extensions.connection:
    """从连接池获取连接，并检查连接有效性

    Args:
        max_retries: 最大重试次数，默认 2 次（减少阻塞时间）
    """
    if _pg_connection_pool is None:
        raise RuntimeError("PostgreSQL 连接池未初始化，请先调用 init_postgres_pool()")

    last_error = None
    for attempt in range(max_retries):
        conn = _pg_connection_pool.getconn()

        # 检查连接是否有效
        try:
            if conn.closed:
                logger.warning("PostgreSQL 连接已关闭，重新获取 (attempt {}/{}): {}", attempt + 1, max_retries, conn)
                _pg_connection_pool.putconn(conn, close=True)
                continue
            # 用轻量查询检测连接是否真的活着
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.close()
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
            logger.warning("PostgreSQL 连接失效，重新获取 (attempt {}/{}): {}", attempt + 1, max_retries, e)
            try:
                _pg_connection_pool.putconn(conn, close=True)
            except Exception:
                pass
            last_error = e
            continue
        except Exception as e:
            logger.warning("PostgreSQL 连接检查异常: {}，重新获取 (attempt {}/{})", e, attempt + 1, max_retries)
            try:
                _pg_connection_pool.putconn(conn, close=True)
            except Exception:
                pass
            last_error = e
            continue

        return conn

    # 所有重试都失败，抛出最后一个错误
    raise RuntimeError(f"获取数据库连接失败：连续 {max_retries} 次获取到无效连接") from (last_error or Exception("unknown"))


def return_pooled_connection(conn, close: bool = False):
    """归还连接到连接池

    Args:
        conn: 要归还的连接
        close: 是否关闭并丢弃该连接（用于坏连接）
    """
    if _pg_connection_pool and conn:
        _pg_connection_pool.putconn(conn, close=close)


def close_postgres_pool():
    """关闭连接池"""
    global _pg_connection_pool
    if _pg_connection_pool:
        _pg_connection_pool.closeall()
        _pg_connection_pool = None
        logger.info("PostgreSQL 连接池已关闭")


def _get_logs_db_config() -> dict:
    """解析追踪库数据库配置"""
    if not LOGS_DATABASE_URL:
        return None
    parsed = urlparse(LOGS_DATABASE_URL)
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
        "database": parsed.path.lstrip("/"),
        "user": parsed.username,
        "password": parsed.password,
    }


def init_logs_pool():
    """初始化追踪库连接池（独立于业务库）"""
    global _logs_connection_pool

    if not LOGS_DATABASE_URL:
        logger.info("追踪库未配置 (LOGS_DATABASE_URL)，跳过初始化")
        return False

    if psycopg2 is None or pg_pool is None:
        logger.warning("psycopg2 未安装，跳过追踪库初始化")
        return False

    config = _get_logs_db_config()
    if not config:
        return False

    try:
        _logs_connection_pool = pg_pool.ThreadedConnectionPool(
            minconn=LOGS_DB_POOL_MIN,
            maxconn=LOGS_DB_POOL_MAX,
            host=config["host"],
            port=config["port"],
            database=config["database"],
            user=config["user"],
            password=config["password"],
            keepalives=1,
            keepalives_idle=30,
            keepalives_interval=5,
            keepalives_count=3,
            connect_timeout=5,
            options="-c statement_timeout=30000",
            application_name="aid-work-agent-logs",
        )
        logger.info(f"追踪库连接池初始化完成: {config['host']}:{config['port']}/{config['database']}")
        return True
    except Exception as e:
        logger.warning(f"追踪库连接池初始化失败（不影响业务）: {e}")
        _logs_connection_pool = None
        return False


def close_logs_pool():
    """关闭追踪库连接池"""
    global _logs_connection_pool
    if _logs_connection_pool:
        _logs_connection_pool.closeall()
        _logs_connection_pool = None
        logger.info("追踪库连接池已关闭")


@contextmanager
def get_logs_connection() -> Generator[Any, None, None]:
    """获取追踪库连接的上下文管理器（带重试）"""
    if _logs_connection_pool is None:
        raise RuntimeError("追踪库连接池未初始化，请检查 LOGS_DATABASE_URL 配置")

    max_retries = 2
    last_error = None
    for attempt in range(max_retries):
        conn = _logs_connection_pool.getconn()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.close()
            break  # 连接有效，跳出重试
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
            _logs_connection_pool.putconn(conn, close=True)
            conn = None
            last_error = e
            logger.debug(f"追踪库连接失效（尝试 {attempt + 1}/{max_retries}）: {e}")
    else:
        raise RuntimeError(f"追踪库连接失效（重试 {max_retries} 次后仍失败）: {last_error}")

    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    class LogsCursorWrapper:
        def __init__(self, cursor, conn):
            self._cursor = cursor
            self._conn = conn

        def __getattr__(self, name):
            return getattr(self._cursor, name)

        def commit(self):
            self._conn.commit()

        def rollback(self):
            self._conn.rollback()

        def close(self):
            pass

    wrapper = LogsCursorWrapper(cursor, conn)

    try:
        yield wrapper
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            conn.rollback()
        except Exception:
            _logs_connection_pool.putconn(conn, close=True)
            return
        _logs_connection_pool.putconn(conn)


def _replenish_pool_to_min():
    """补充连接池到 minconn 水位（健康检查清理坏连接后调用）

    ThreadedConnectionPool 不会主动维持 minconn，清理坏连接后需要手动补齐，
    否则池水位会逐渐下降。在锁内创建连接以避免与 getconn 竞争。
    """
    if _pg_connection_pool is None:
        return
    try:
        with _pg_connection_pool._lock:
            needed = _pg_connection_pool.minconn - len(_pg_connection_pool._pool) - len(_pg_connection_pool._used)
        # 在锁外创建连接（connect 可能阻塞），逐个补齐
        for _ in range(max(0, needed)):
            try:
                new_conn = _pg_connection_pool._connect()
                with _pg_connection_pool._lock:
                    _pg_connection_pool._pool.append(new_conn)
            except Exception as e:
                logger.warning(f"PostgreSQL 补充连接池失败: {e}")
                break
    except Exception as e:
        logger.warning(f"PostgreSQL 补充连接池异常: {e}")


def health_check_pool() -> dict:
    """连接池健康检查：检测并清理坏连接

    Returns:
        dict: 包含 healthy/bad 连接数和详细信息
    """
    if _pg_connection_pool is None:
        return {"initialized": False, "healthy": 0, "bad": 0}

    healthy = 0
    bad = 0
    bad_details = []

    # 检查池中所有可用（空闲）连接
    # 注意：直接操作 _pool 内部列表。坏连接不能用 putconn(conn, close=True) 回收，
    # 因为 putconn 期望 conn 来自 _used，对 _pool 中的连接会抛 PoolError。
    # 这里直接从 _pool 移除并 close，避免坏连接残留导致下次重复失败。
    if hasattr(_pg_connection_pool, '_pool') and _pg_connection_pool._pool is not None:
        with _pg_connection_pool._lock:
            connections_to_check = list(_pg_connection_pool._pool)
        for conn in connections_to_check:
            try:
                if conn.closed:
                    bad += 1
                    bad_details.append("连接已关闭")
                    with _pg_connection_pool._lock:
                        if conn in _pg_connection_pool._pool:
                            _pg_connection_pool._pool.remove(conn)
                    try:
                        conn.close()
                    except Exception:
                        pass
                    continue
                cursor = conn.cursor()
                cursor.execute("SELECT 1")
                cursor.close()
                conn.rollback()  # 确保连接状态干净
                healthy += 1
            except Exception as e:
                bad += 1
                bad_details.append(str(e)[:100])
                # 从池中移除坏连接并关闭
                with _pg_connection_pool._lock:
                    if conn in _pg_connection_pool._pool:
                        _pg_connection_pool._pool.remove(conn)
                try:
                    conn.close()
                except Exception:
                    pass
                # 低于 minconn 时，让池自动补充新连接
                _replenish_pool_to_min()

    result = {
        "initialized": True,
        "healthy": healthy,
        "bad": bad,
    }
    if bad_details:
        result["bad_details"] = bad_details

    if bad > 0:
        logger.warning(f"后端日志：连接池健康检查发现 {bad} 个坏连接，已清理")

    return result


def get_postgres_pool_status() -> dict:
    """获取连接池状态（含健康检测）"""
    if _pg_connection_pool is None:
        return {"initialized": False}

    # ThreadedConnectionPool 的内部状态
    # _pool: 可用连接列表, _used: 已借出连接字典
    pool_size = len(_pg_connection_pool._pool) if hasattr(_pg_connection_pool, '_pool') else -1
    used_size = len(_pg_connection_pool._used) if hasattr(_pg_connection_pool, '_used') else -1

    return {
        "initialized": True,
        "minconn": _pg_connection_pool.minconn,
        "maxconn": _pg_connection_pool.maxconn,
        "pool_available": pool_size,   # 池中可用连接数
        "pool_in_use": used_size,       # 正在被使用的连接数
        "dsn": getattr(_pg_connection_pool, 'dsn', getattr(_pg_connection_pool, 'connstring', '')),
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

        def cursor(self, cursor_factory=None):
            """兼容旧 API：如果指定了 cursor_factory，返回新的 cursor；否则返回内部的 cursor"""
            if cursor_factory is not None:
                return self._conn.cursor(cursor_factory=cursor_factory)
            return self._cursor

        def commit(self):
            self._conn.commit()

        def rollback(self):
            self._conn.rollback()

        @property
        def autocommit(self):
            return self._conn.autocommit

        @autocommit.setter
        def autocommit(self, value):
            self._conn.autocommit = value

        def close(self):
            pass  # 不实际关闭，由上下文管理器处理

    wrapper = CursorWrapper(cursor, conn)
    conn_handled = False  # 标记连接是否已被处理（归还/丢弃）

    try:
        yield wrapper
    except Exception:
        # 发生异常时必须 rollback，否则连接进入 aborted transaction 状态
        # 后续所有查询都会报 "current transaction is aborted" 导致连接被毒化
        try:
            conn.rollback()
        except Exception as rollback_err:
            logger.error(f"后端日志：rollback 失败，连接将被丢弃: {rollback_err}")
            # rollback 失败说明连接已坏，直接关闭丢弃
            return_pooled_connection(conn, close=True)
            conn_handled = True
        raise
    finally:
        if not conn_handled:
            # 正常退出时也 rollback，清理可能残留的未提交事务
            # 这是防止连接池毒化的关键：确保归还的连接始终处于干净状态
            try:
                conn.rollback()
            except Exception:
                # rollback 失败则关闭丢弃该连接
                return_pooled_connection(conn, close=True)
                return
            # 归还连接到池而非关闭
            return_pooled_connection(conn)


def _seed_reply_styles():
    """将磁盘风格文件作为系统内置种子数据写入 reply_styles 表"""
    try:
        from src.prompts.style_manager import get_style_manager
        sm = get_style_manager()
        sm.seed_system_styles()
    except Exception as e:
        logger.warning(f"Failed to seed reply styles: {e}")


def init_database():
    """初始化数据库表（PostgreSQL）"""
    _init_postgresql()


def init_logs_tables():
    """初始化追踪库表（obs_traces, obs_spans, obs_scores）"""
    if not LOGS_DATABASE_URL or _logs_connection_pool is None:
        return

    project_root = Path(__file__).parent.parent.parent
    sql_file = project_root / "deploy" / "init-postgres-logs.sql"

    if not sql_file.exists():
        logger.warning(f"追踪库初始化脚本不存在: {sql_file}")
        return

    # 多 worker 并发执行 DDL 会因 AccessExclusiveLock 互相等待导致死锁
    # 用 advisory lock 串行化（key 与主库 123456 区分，避免冲突）
    LOGS_INIT_LOCK_KEY = 654321

    conn = None
    try:
        conn = _logs_connection_pool.getconn()
        conn.autocommit = True
        cur = conn.cursor()

        # 阻塞式获取 advisory lock，确保只有一个 worker 执行 DDL
        cur.execute("SELECT pg_advisory_lock(%s)", (LOGS_INIT_LOCK_KEY,))
        logger.info(f"[pid={os.getpid()}] 已获取追踪库初始化 advisory lock (key={LOGS_INIT_LOCK_KEY})")

        try:
            sql_content = sql_file.read_text(encoding='utf-8')
            cur.execute(sql_content)
            logger.info("追踪库表初始化完成")
        finally:
            try:
                cur.execute("SELECT pg_advisory_unlock(%s)", (LOGS_INIT_LOCK_KEY,))
            except Exception as unlock_err:
                logger.warning(f"释放追踪库 advisory lock 失败: {unlock_err}")
            cur.close()
    except Exception as e:
        logger.warning(f"追踪库表初始化失败（不影响业务）: {e}")
    finally:
        if conn:
            conn.autocommit = False
            _logs_connection_pool.putconn(conn, close=False)


def _apply_db_updates(conn):
    """
    执行 deploy/db_update.sql 中的增量更新
    使用文件哈希检测变化，确保每次文件变化后只执行一次
    """
    from pathlib import Path
    import hashlib
    import time

    project_root = Path(__file__).parent.parent.parent
    update_file = project_root / "deploy" / "db_update.sql"

    if not update_file.exists():
        logger.warning(f"数据库更新文件不存在: {update_file}")
        return

    # 计算当前文件哈希
    try:
        file_content = update_file.read_text(encoding='utf-8')
        file_hash = hashlib.sha256(file_content.encode('utf-8')).hexdigest()[:32]
    except Exception as e:
        logger.error(f"读取数据库更新文件失败: {e}")
        return

    # 解析 SQL 语句，支持 $$ 定界符块（如 DO $$ ... $$）
    statements = []
    current = []
    in_dollar_quote = False
    lines = file_content.split('\n')

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith('--'):
            continue

        # 在 $$ 块内不剥离行内注释（块内容可能包含 --）
        if not in_dollar_quote and '--' in stripped:
            stripped = stripped.split('--')[0].strip()
            if not stripped:
                continue

        current.append(stripped)

        # 跟踪 $$ 定界符状态
        dollar_count = stripped.count('$$')
        if dollar_count % 2 == 1:
            in_dollar_quote = not in_dollar_quote

        # 仅在 $$ 块外遇到分号时才切断语句
        if not in_dollar_quote and stripped.endswith(';'):
            statement = ' '.join(current)
            statements.append(statement)
            current = []

    if current:
        statement = ' '.join(current)
        if not statement.endswith(';'):
            statement += ';'
        statements.append(statement)

    cursor = conn.cursor()

    # 先回滚任何可能存在的失败事务，确保从干净的状态开始
    try:
        conn.rollback()
    except Exception:
        pass  # 忽略回滚失败（可能没有活动事务）

    # 创建更新记录表（包含文件哈希）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS _db_update_applied (
            id TEXT PRIMARY KEY,
            file_hash TEXT NOT NULL,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 检查并升级表结构（兼容旧版本）
    try:
        # 检查 file_hash 列是否存在
        cursor.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = '_db_update_applied' AND column_name = 'file_hash'
        """)
        has_file_hash = cursor.fetchone() is not None

        if not has_file_hash:
            logger.info("升级 _db_update_applied 表结构，添加 file_hash 列")
            cursor.execute("ALTER TABLE _db_update_applied ADD COLUMN file_hash TEXT")
            # 为现有记录设置默认值（空字符串）
            cursor.execute("UPDATE _db_update_applied SET file_hash = '' WHERE file_hash IS NULL")
    except Exception as e:
        logger.warning(f"检查/升级表结构失败: {e}")
        # 继续执行，后面的查询可能会失败，但会由错误处理机制捕获

    # 清理旧的记录（旧版本使用 id='initial'）
    try:
        cursor.execute("DELETE FROM _db_update_applied WHERE id = 'initial'")
        if cursor.rowcount > 0:
            logger.info(f"清理了 {cursor.rowcount} 条旧记录（id='initial'）")
    except Exception as e:
        logger.warning(f"清理旧记录失败: {e}")

    # 检查当前哈希是否已应用
    try:
        # 首先检查 file_hash 列是否存在（避免 UndefinedColumn 错误）
        cursor.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = '_db_update_applied' AND column_name = 'file_hash'
        """)
        has_file_hash = cursor.fetchone() is not None

        if not has_file_hash:
            logger.warning("file_hash 列不存在，无法检查哈希记录，将继续执行更新")
            # 列不存在，无法检查哈希，继续执行更新
        else:
            cursor.execute("""
                SELECT file_hash FROM _db_update_applied WHERE id = 'db_update'
            """)
            row = cursor.fetchone()
            if row and row['file_hash'] == file_hash:
                logger.info("数据库更新文件未变化，跳过执行")
                return
    except Exception as e:
        # 如果查询失败（例如表不存在），继续执行
        logger.warning(f"检查哈希记录失败，将继续执行更新: {e}")

    # 如果文件没有实际语句（只有注释或空），只更新哈希记录
    if len(statements) == 0:
        logger.info("数据库更新文件无有效语句，只更新哈希记录")
        try:
            # 确保 file_hash 列存在
            cursor.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = '_db_update_applied' AND column_name = 'file_hash'
            """)
            has_file_hash = cursor.fetchone() is not None

            if not has_file_hash:
                logger.warning("file_hash 列不存在，尝试添加")
                try:
                    cursor.execute("ALTER TABLE _db_update_applied ADD COLUMN file_hash TEXT")
                    logger.info("成功添加 file_hash 列")
                except Exception as add_col_err:
                    logger.error(f"添加 file_hash 列失败: {add_col_err}")
                    # 无法添加列，跳过插入哈希记录
                    logger.warning("跳过插入哈希记录（列不存在）")
                    return

            cursor.execute("""
                INSERT INTO _db_update_applied (id, file_hash)
                VALUES ('db_update', %s)
                ON CONFLICT (id) DO UPDATE
                SET file_hash = EXCLUDED.file_hash,
                    applied_at = CURRENT_TIMESTAMP
            """, (file_hash,))
        except Exception as e:
            logger.error(f"更新哈希记录失败: {e}")
            # 插入失败不影响主流程
        # 不在这里提交，由外部事务统一提交
        return

    # 哈希不同且存在有效语句，需要执行更新，使用 advisory lock 防止多 worker 并发执行
    # 使用固定的 advisory lock key (123456)
    lock_key = 123456
    logger.info(f"数据库更新文件有变化，尝试获取 advisory lock (key={lock_key})")

    # 使用 pg_try_advisory_lock 非阻塞尝试，如果失败则等待
    max_retries = 10  # 降低到 10 次（原 30 次），减少等待时间
    retry_interval = 1  # 秒
    locked = False
    for retry in range(max_retries):
        try:
            cursor.execute("SELECT pg_try_advisory_lock(%s) AS locked", (lock_key,))
            result = cursor.fetchone()
            if result is None:
                logger.warning("pg_try_advisory_lock 查询返回空结果，视为未获取锁")
                locked = False
            else:
                locked = result['locked']
            if locked:
                break
        except Exception as lock_err:
            logger.warning(f"尝试获取 advisory lock 时发生错误 (重试 {retry+1}/{max_retries}): {lock_err}")
            locked = False
        logger.info(f"等待 advisory lock (重试 {retry+1}/{max_retries})")
        time.sleep(retry_interval)
    else:
        logger.warning("无法获取 advisory lock，跳过数据库更新（可能由其他进程执行）")
        return

    try:
        # 获取锁后再次检查哈希（可能已被其他进程更新）
        try:
            # 首先检查 file_hash 列是否存在
            cursor.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = '_db_update_applied' AND column_name = 'file_hash'
            """)
            has_file_hash = cursor.fetchone() is not None

            if not has_file_hash:
                logger.warning("file_hash 列不存在，无法检查哈希记录，将继续执行更新")
                # 列不存在，无法检查哈希，继续执行更新
            else:
                cursor.execute("SELECT file_hash FROM _db_update_applied WHERE id = 'db_update'")
                row = cursor.fetchone()
                if row and row['file_hash'] == file_hash:
                    logger.info("其他进程已执行更新，跳过")
                    return
        except Exception as e:
            logger.warning(f"获取锁后检查哈希失败，将继续执行更新: {e}")
            # 哈希检查失败，继续执行更新

        logger.info(f"开始执行数据库更新，共 {len(statements)} 条语句")

        executed = 0
        for i, stmt in enumerate(statements):
            # 为每条语句创建保存点，允许单条失败不影响其他语句
            savepoint_name = f"sp_{i}"
            try:
                cursor.execute(f"SAVEPOINT {savepoint_name}")
                cursor.execute(stmt)
                executed += 1
            except Exception as e:
                logger.error(f"执行 SQL 语句失败: {stmt[:100]}... 错误: {e}")
                # 回滚到保存点，清除错误状态
                try:
                    cursor.execute(f"ROLLBACK TO SAVEPOINT {savepoint_name}")
                except Exception as rollback_err:
                    logger.error(f"回滚保存点失败: {rollback_err}")
                    # 如果回滚失败，整个事务可能已无效，需要回滚整个事务
                    conn.rollback()
                    # 重新建立保存点以继续
                    cursor.execute(f"SAVEPOINT {savepoint_name}")
                continue

        logger.info(f"数据库更新完成，成功执行 {executed}/{len(statements)} 条语句")

        # 更新或插入哈希记录
        try:
            # 确保 file_hash 列存在
            cursor.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = '_db_update_applied' AND column_name = 'file_hash'
            """)
            has_file_hash = cursor.fetchone() is not None

            if not has_file_hash:
                logger.warning("file_hash 列不存在，尝试添加")
                try:
                    cursor.execute("ALTER TABLE _db_update_applied ADD COLUMN file_hash TEXT")
                    logger.info("成功添加 file_hash 列")
                except Exception as add_col_err:
                    logger.error(f"添加 file_hash 列失败: {add_col_err}")
                    # 无法添加列，跳过插入哈希记录
                    logger.warning("跳过插入哈希记录（列不存在）")
                    return

            cursor.execute("""
                INSERT INTO _db_update_applied (id, file_hash)
                VALUES ('db_update', %s)
                ON CONFLICT (id) DO UPDATE
                SET file_hash = EXCLUDED.file_hash,
                    applied_at = CURRENT_TIMESTAMP
            """, (file_hash,))

            logger.info(f"数据库更新记录已更新，哈希: {file_hash}")
        except Exception as e:
            logger.error(f"更新哈希记录失败: {e}")
            # 插入失败不影响已执行的更新，继续执行（释放锁）

    finally:
        # 释放 advisory lock
        if locked:
            cursor.execute("SELECT pg_advisory_unlock(%s) AS unlocked", (lock_key,))
            result = cursor.fetchone()
            if result is None:
                logger.warning(f"pg_advisory_unlock 查询返回空结果，无法确认锁是否释放 (key={lock_key})")
                unlocked = False
            else:
                unlocked = result['unlocked']
            if not unlocked:
                logger.warning(f"释放 advisory lock 失败 (key={lock_key})")


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
                nickname TEXT,
                tenant_id TEXT,
                role TEXT DEFAULT 'user',
                status TEXT DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 租户内手机号索引（非唯一，支持跨渠道用户绑定同一手机号）
        # 同一员工在飞书/钉钉/企微都有账号时，多渠道写回 phone 不再触发唯一约束冲突
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_users_tenant_phone
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
            cursor.execute(f'ALTER TABLE users DROP CONSTRAINT {old_constraint["conname"]}')
            logger.info(f"Dropped old phone unique constraint: {old_constraint['conname']}")

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
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 会话记录表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_records (
                id SERIAL PRIMARY KEY,
                record_id TEXT UNIQUE NOT NULL,
                session_id TEXT NOT NULL,
                tenant_id TEXT,
                user_id TEXT NOT NULL,
                user_message TEXT NOT NULL,
                assistant_message TEXT,
                total_token_count INTEGER DEFAULT 0,
                prompt_tokens INTEGER DEFAULT 0,
                completion_tokens INTEGER DEFAULT 0,
                cached_input_tokens INTEGER DEFAULT 0,
                model TEXT,
                provider TEXT,
                execution_details TEXT,
                agent_iterations INTEGER DEFAULT 0,
                subagent_calls TEXT,
                status TEXT DEFAULT 'completed',
                error_message TEXT,
                duration_ms INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
                tenant_id TEXT,
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
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                summary TEXT,
                uuid TEXT UNIQUE
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_documents_user
            ON documents(user_id)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_documents_tenant
            ON documents(tenant_id)
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
                uuid TEXT UNIQUE
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
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
            from src.saas.db.tables import init_saas_tables
            init_saas_tables(conn)
        except Exception as e:
            logger.warning(f"Failed to initialize SaaS tables (saas module may not be configured): {e}")
            # 回滚失败的事务，避免后续操作报错
            try:
                conn.rollback()
            except Exception as rollback_err:
                logger.warning(f"Failed to rollback SaaS transaction: {rollback_err}")

        try:
            from src.social_media.db import init_social_media_tables
            init_social_media_tables(conn)
        except Exception as e:
            logger.warning(f"Failed to initialize social media tables: {e}")
            try:
                conn.rollback()
            except Exception as rollback_err:
                logger.warning(f"Failed to rollback social media transaction: {rollback_err}")

        # 巡检商机 outbound 表：商机池 3 表（B1）+ 托管登录态（B0.5），均幂等建表
        try:
            from src.social_media.outbound.db import init_outbound_tables
            from src.social_media.outbound.account_session_store import init_outbound_account_sessions_table
            init_outbound_tables(conn)
            init_outbound_account_sessions_table(conn)
        except Exception as e:
            logger.warning(f"Failed to initialize outbound tables: {e}")
            try:
                conn.rollback()
            except Exception as rollback_err:
                logger.warning(f"Failed to rollback outbound transaction: {rollback_err}")

        # 视频生成表（gen_sessions / gen_cards），MVP 抽卡式工具，见 mvp-design.md §3
        try:
            from src.video_gen.db import init_video_gen_tables
            init_video_gen_tables(conn)
        except Exception as e:
            logger.warning(f"Failed to initialize video_gen tables: {e}")
            try:
                conn.rollback()
            except Exception as rollback_err:
                logger.warning(f"Failed to rollback video_gen transaction: {rollback_err}")

        # 视频创作智能体表（asset_library / prompt_library + subagent_definitions 扩展列）
        # 见 docs/plans/plan-video-agent-phase1.md §1.6
        try:
            from src.video_agent.db import init_video_agent_tables
            init_video_agent_tables(conn)
        except Exception as e:
            logger.warning(f"Failed to initialize video_agent tables: {e}")
            try:
                conn.rollback()
            except Exception as rollback_err:
                logger.warning(f"Failed to rollback video_agent transaction: {rollback_err}")

        # Skill 表初始化由 SkillLoader._init_skill_tables() 统一处理，
        # 通过 SKILL.md 中的 init_script 字段声明，不再硬编码。

        # 执行增量数据库更新（db_update.sql）
        _apply_db_updates(conn)
        conn.commit()

    # 种子数据：将磁盘风格文件写入 reply_styles 表
    _seed_reply_styles()


if __name__ == "__main__":
    init_database()
    print(f"Database initialized: {DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}")
