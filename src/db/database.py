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


def _is_lock_timeout_error(exc: Exception) -> bool:
    """判断异常是否为 PostgreSQL 锁等待超时（SQLSTATE 55P03 或消息含 lock timeout）"""
    if getattr(exc, "pgcode", None) == "55P03":
        return True
    msg = str(exc).lower()
    return "lock timeout" in msg or "锁超时" in msg


def _split_sql_statements(text: str) -> list:
    """将 SQL 文本切分为单条语句，支持 $$ 定界符块（如 DO $$ ... $$）与 -- 注释。"""
    statements = []
    current = []
    in_dollar_quote = False

    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue

        # 在 $$ 块内不剥离行内注释（块内容可能包含 --）
        if not in_dollar_quote and "--" in stripped:
            stripped = stripped.split("--")[0].strip()
            if not stripped:
                continue

        current.append(stripped)

        # 跟踪 $$ 定界符状态
        dollar_count = stripped.count("$$")
        if dollar_count % 2 == 1:
            in_dollar_quote = not in_dollar_quote

        # 仅在 $$ 块外遇到分号时才切断语句
        if not in_dollar_quote and stripped.endswith(";"):
            statements.append(" ".join(current))
            current = []

    if current:
        statement = " ".join(current)
        if not statement.endswith(";"):
            statement += ";"
        statements.append(statement)

    return statements


def _load_db_update_blocks(path):
    """加载并校验 deploy/db_update.yaml，返回有序块列表。

    每块为 {"datetime": str, "remark": str, "statements": str}。
    校验失败（字段缺失/时间格式错/重复/倒序/备注或 SQL 为空）抛 ValueError，
    由调用方 fail-fast 拒绝启动，绝不静默跳过脚本。
    """
    import re
    from datetime import datetime as dt

    try:
        import yaml
    except ImportError as e:
        raise ValueError("缺少 PyYAML 依赖，无法读取 db_update.yaml") from e

    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"db_update.yaml 顶层必须是数组，实际是 {type(raw).__name__}")

    blocks = []
    seen = set()
    prev = None
    for i, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"db_update.yaml 第 {i} 个块不是对象")

        try:
            dt_raw = item["datetime"]
            remark = item["remark"]
            statements = item["statements"]
        except KeyError as e:
            raise ValueError(f"db_update.yaml 第 {i} 个块缺少字段 {e.args[0]}") from e

        # 兼容 YAML 未加引号被解析成 datetime 对象
        if isinstance(dt_raw, dt):
            dt_str = dt_raw.strftime("%Y-%m-%d %H:%M:%S")
        elif isinstance(dt_raw, str):
            dt_str = dt_raw.strip()
        else:
            raise ValueError(f"db_update.yaml 第 {i} 个块 datetime 类型非法: {type(dt_raw).__name__}")

        if not re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", dt_str):
            raise ValueError(
                f"db_update.yaml 第 {i} 个块 datetime 格式非法: {dt_str!r}（应为 YYYY-MM-DD HH:MM:SS）"
            )
        try:
            dt.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            raise ValueError(f"db_update.yaml 第 {i} 个块 datetime 不是合法时间: {dt_str!r}")

        if dt_str in seen:
            raise ValueError(f"db_update.yaml 第 {i} 个块 datetime 重复: {dt_str}")
        seen.add(dt_str)

        if prev is not None and dt_str <= prev:
            raise ValueError(f"db_update.yaml 第 {i} 个块 datetime 未严格递增: {prev} -> {dt_str}")
        prev = dt_str

        if not remark or not str(remark).strip():
            raise ValueError(f"db_update.yaml 第 {i} 个块 remark 为空")
        if not isinstance(statements, str) or not statements.strip():
            raise ValueError(f"db_update.yaml 第 {i} 个块 statements 为空")

        blocks.append({
            "datetime": dt_str,
            "remark": str(remark).strip(),
            "statements": str(statements),
        })

    return blocks


def _apply_db_updates(conn, update_file=None):
    """
    执行 deploy/db_update.yaml 中的增量更新
    只执行 datetime 晚于 _db_update_applied.last_datetime 的块，文件可无限累积无需清理
    """
    import time

    project_root = Path(__file__).parent.parent.parent
    if update_file is None:
        update_file = project_root / "deploy" / "db_update.yaml"

    if not update_file.exists():
        logger.warning(f"数据库更新文件不存在: {update_file}")
        return

    # 加载并校验（格式错误即 fail-fast，绝不静默跳过脚本）
    try:
        blocks = _load_db_update_blocks(update_file)
    except Exception as e:
        logger.error(f"数据库更新文件校验失败，拒绝启动: {e}")
        raise

    if not blocks:
        logger.info("数据库更新文件无有效块")
        return

    cursor = conn.cursor()

    # 先回滚任何可能存在的失败事务，确保从干净的状态开始
    try:
        conn.rollback()
    except Exception:
        pass  # 忽略回滚失败（可能没有活动事务）

    # 创建更新记录表（新环境直接建新结构，存量环境补充 last_datetime 列）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS _db_update_applied (
            id TEXT PRIMARY KEY,
            file_hash TEXT,
            last_datetime TEXT,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    try:
        cursor.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = '_db_update_applied' AND column_name = 'last_datetime'
        """)
        if cursor.fetchone() is None:
            logger.info("升级 _db_update_applied 表结构，添加 last_datetime 列")
            cursor.execute("ALTER TABLE _db_update_applied ADD COLUMN IF NOT EXISTS last_datetime TEXT")
    except Exception as e:
        logger.warning(f"检查/升级表结构失败: {e}")

    # 清理旧的记录（旧版本使用 id='initial'）
    try:
        cursor.execute("DELETE FROM _db_update_applied WHERE id = 'initial'")
        if cursor.rowcount > 0:
            logger.info(f"清理了 {cursor.rowcount} 条旧记录（id='initial'）")
    except Exception as e:
        logger.warning(f"清理旧记录失败: {e}")

    # 读取 last_datetime，过滤待执行块并按时间排序
    try:
        cursor.execute("SELECT last_datetime FROM _db_update_applied WHERE id = 'db_update'")
        row = cursor.fetchone()
        last_datetime = (row["last_datetime"] or "") if row else ""
    except Exception as e:
        logger.warning(f"读取 last_datetime 失败，视为空: {e}")
        last_datetime = ""

    pending = [b for b in blocks if b["datetime"] > last_datetime]
    pending.sort(key=lambda b: b["datetime"])

    if not pending:
        logger.info(f"数据库更新无新增块（last_datetime={last_datetime or '空'}）")
        return

    for b in pending:
        b["statements_list"] = _split_sql_statements(b["statements"])

    # 使用 advisory lock 防止多 worker 并发执行，使用固定的 advisory lock key (123456)
    lock_key = 123456
    logger.info(f"数据库更新文件有 {len(pending)} 个新增块，尝试获取 advisory lock (key={lock_key})")

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
                locked = result["locked"]
            if locked:
                break
        except Exception as lock_err:
            logger.warning(f"尝试获取 advisory lock 时发生错误 (重试 {retry + 1}/{max_retries}): {lock_err}")
            locked = False
        logger.info(f"等待 advisory lock (重试 {retry + 1}/{max_retries})")
        time.sleep(retry_interval)
    else:
        logger.warning("无法获取 advisory lock，跳过数据库更新（可能由其他进程执行）")
        return

    try:
        # 获取锁后重读 last_datetime（可能已被其他进程更新）
        try:
            cursor.execute("SELECT last_datetime FROM _db_update_applied WHERE id = 'db_update'")
            row = cursor.fetchone()
            current_last = (row["last_datetime"] or "") if row else ""
            if current_last:
                pending = [b for b in pending if b["datetime"] > current_last]
                if not pending:
                    logger.info("其他进程已执行全部新增块，跳过")
                    return
        except Exception as e:
            logger.warning(f"获取锁后检查 last_datetime 失败，将继续执行更新: {e}")

        logger.info(f"开始执行数据库更新，共 {len(pending)} 个块")

        executed = 0
        failed = []
        lock_retry_times = 3
        for block in pending:
            for i, stmt in enumerate(block["statements_list"]):
                savepoint_name = f"sp_{executed}"
                for attempt in range(lock_retry_times + 1):
                    try:
                        # 为每条语句创建保存点，允许单条失败不影响其他语句
                        cursor.execute(f"SAVEPOINT {savepoint_name}")
                        cursor.execute(stmt)
                        executed += 1
                        break
                    except Exception as e:
                        # 回滚到保存点，清除错误状态
                        try:
                            cursor.execute(f"ROLLBACK TO SAVEPOINT {savepoint_name}")
                        except Exception as rollback_err:
                            logger.error(f"回滚保存点失败: {rollback_err}")
                            # 如果回滚失败，整个事务可能已无效，需要回滚整个事务
                            conn.rollback()
                            # 重新建立保存点以继续
                            cursor.execute(f"SAVEPOINT {savepoint_name}")
                        # 锁超时：DDL 等 ACCESS EXCLUSIVE 锁期间可能被并发事务阻塞，
                        # 等待锁释放后重试，避免偶发锁冲突导致语句被永久跳过
                        if _is_lock_timeout_error(e) and attempt < lock_retry_times:
                            wait_sec = (attempt + 1) * 5
                            logger.warning(
                                f"数据库更新语句因锁超时失败（第 {attempt + 1}/{lock_retry_times} 次重试）: "
                                f"{stmt[:80]}... 等待 {wait_sec}s 后重试"
                            )
                            time.sleep(wait_sec)
                            continue
                        failed.append((block["datetime"], stmt, str(e)))
                        logger.error(f"执行 SQL 语句失败 [{block['datetime']}]: {stmt[:100]}... 错误: {e}")
                        break

        if failed:
            # 有语句失败时不更新 last_datetime，下次启动重跑本次所有待执行块
            # 所有语句均幂等（IF NOT EXISTS / ON CONFLICT），重跑无害
            logger.error(
                f"数据库更新有 {len(failed)} 条语句失败（成功 {executed} 条），"
                f"本次不更新 last_datetime，下次启动将重试"
            )
            for dt_str, failed_stmt, err in failed:
                logger.error(f"  失败语句[{dt_str}]: {failed_stmt[:100]}... 错误: {err}")
        else:
            logger.info(f"数据库更新完成，成功执行 {executed} 条语句")

            # 全部成功：记录 last_datetime = 本次最大 datetime
            new_last = pending[-1]["datetime"]
            try:
                cursor.execute("""
                    INSERT INTO _db_update_applied (id, file_hash, last_datetime)
                    VALUES ('db_update', '', %s)
                    ON CONFLICT (id) DO UPDATE
                    SET last_datetime = EXCLUDED.last_datetime,
                        applied_at = CURRENT_TIMESTAMP
                """, (new_last,))
                logger.info(f"数据库更新记录已更新，last_datetime: {new_last}")
            except Exception as e:
                logger.error(f"更新 last_datetime 记录失败: {e}")
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
                unlocked = result["unlocked"]
            if not unlocked:
                logger.warning(f"释放 advisory lock 失败 (key={lock_key})")


def _init_postgresql():
    """初始化服务模块表并应用增量更新。

    核心表结构（users/chat_records 等）由 deploy/init-postgres.sql 全量创建，
    此处不再内联建表；存量环境的结构升级统一走 deploy/db_update.yaml。
    """
    with get_db_connection() as conn:
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

        # 招聘操作智能体职位库表（bs_recruiting_operator_jobs / bs_recruiting_operator_job_scripts）
        # 注意：必须在简历库表之前初始化（resumes.job_id 外键引用 jobs 表，
        # 简历-职位匹配 Phase 1 关联严密化）
        try:
            from src.services.recruiting_job_service import init_recruiting_job_tables
            init_recruiting_job_tables(conn)
        except Exception as e:
            logger.warning(f"Failed to initialize recruiting_operator job tables: {e}")
            try:
                conn.rollback()
            except Exception as rollback_err:
                logger.warning(f"Failed to rollback recruiting_operator job transaction: {rollback_err}")

        # 招聘操作智能体表（bs_recruiting_operator_resumes 简历库）
        try:
            from src.services.recruiting_resume_service import init_recruiting_operator_tables
            init_recruiting_operator_tables(conn)
        except Exception as e:
            logger.warning(f"Failed to initialize recruiting_operator tables: {e}")
            try:
                conn.rollback()
            except Exception as rollback_err:
                logger.warning(f"Failed to rollback recruiting_operator transaction: {rollback_err}")

        # 招聘操作智能体简历时间线表（沟通记录/邀约记录，第④期）
        # 注意：FK 引用简历表，必须在 init_recruiting_operator_tables 之后初始化
        try:
            from src.services.recruiting_resume_timeline_service import init_recruiting_timeline_tables
            init_recruiting_timeline_tables(conn)
        except Exception as e:
            logger.warning(f"Failed to initialize recruiting_resume_timeline tables: {e}")
            try:
                conn.rollback()
            except Exception as rollback_err:
                logger.warning(f"Failed to rollback recruiting_resume_timeline transaction: {rollback_err}")

        # 招聘面试邀约企微通知表（bs_recruiting_notify_settings / bs_recruiting_notify_logs，
        # 面试邀约通知设计 Phase 1，见 docs/design/recruiting/recruiting-interview-notify-design.md）
        try:
            from src.services.recruiting_notify_service import init_recruiting_notify_tables
            init_recruiting_notify_tables(conn)
        except Exception as e:
            logger.warning(f"Failed to initialize recruiting_notify tables: {e}")
            try:
                conn.rollback()
            except Exception as rollback_err:
                logger.warning(f"Failed to rollback recruiting_notify transaction: {rollback_err}")

        # Skill 表初始化由 SkillLoader._init_skill_tables() 统一处理，
        # 通过 SKILL.md 中的 init_script 字段声明，不再硬编码。

        # 执行增量数据库更新（db_update.yaml）
        _apply_db_updates(conn)
        conn.commit()

    # 种子数据：将磁盘风格文件写入 reply_styles 表
    _seed_reply_styles()


if __name__ == "__main__":
    init_database()
    print(f"Database initialized: {DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}")
