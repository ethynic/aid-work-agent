"""
错误日志数据库 Sink

自定义 Loguru sink，将 ERROR 级别日志异步写入数据库 log_error 表。
- 仅记录 ERROR 及以上级别日志
- 由 loguru enqueue 机制在独立线程中消费，不阻塞主流程
- 写入失败不影响文件日志（降级方案）
- 支持敏感信息过滤
"""

import os
import re
from datetime import datetime
from typing import Any

from loguru import logger

from src.db import get_db_connection


# 敏感信息过滤模式（与 error handling 保持一致）
SENSITIVE_PATTERNS = [
    r'password["\s:=]+\S+',
    r'api[_-]?key["\s:=]+\S+',
    r'token["\s:=]+\S+',
    r'smtp[_-]?password["\s:=]+\S+',
]


def sanitize_error_info(error_msg: str) -> str:
    """过滤敏感信息"""
    if not error_msg:
        return error_msg
    for pattern in SENSITIVE_PATTERNS:
        error_msg = re.sub(pattern, lambda m: m.group(0).split('=')[0] + '=***', error_msg, flags=re.IGNORECASE)
    return error_msg


def error_log_sink(message: Any) -> None:
    """
    Loguru 自定义 Sink，将 ERROR 级别日志写入数据库。

    此函数由 loguru enqueue 机制在独立线程中调用，
    直接同步写入 DB，不依赖 asyncio event loop。

    Args:
        message: Loguru 的 Message 对象（包含完整日志信息）
    """
    # 仅处理 ERROR 及以上级别
    if message.record["level"].no < 40:  # ERROR = 40
        return

    try:
        record = message.record

        # 提取模块信息：{name}:{function}:{line}
        module = f"{record.get('name', 'unknown')}:{record.get('function', 'unknown')}:{record.get('line', 0)}"

        # 提取异常类型
        error_type = None
        exception = record.get('exception')
        if exception and exception.type:
            error_type = exception.type.__name__

        # 错误消息
        message_text = str(record.get('message', ''))

        # 提取完整堆栈
        traceback = None
        if exception and exception.traceback:
            import traceback as tb_module
            traceback = "".join(tb_module.format_exception(
                type(exception.value),
                exception.value,
                exception.traceback
            ))

        # 过滤敏感信息
        message_text = sanitize_error_info(message_text)
        if traceback:
            traceback = sanitize_error_info(traceback)

        # 直接在消费线程中同步写入数据库
        _write_to_db(
            module,
            error_type,
            message_text,
            traceback,
            record.get('time', datetime.now())
        )

    except Exception as e:
        # sink 写入失败只记录 warning，不抛出异常，不影响主流程
        # 注意：这里不能用 logger.error，否则会造成无限递归
        logger.warning(f"写入错误日志到数据库失败: {e}")


def _write_to_db(module: str, error_type: str, message: str, traceback: str, timestamp: datetime) -> None:
    """
    同步写入数据库。

    注意：必须保证此函数不抛出任何异常，否则会导致 logger 崩溃。
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO log_error (timestamp, module, error_type, message, traceback, status)
                VALUES (%s, %s, %s, %s, %s, 'unprocessed')
            """, (timestamp, module, error_type, message, traceback))
            conn.commit()
    except Exception:
        # 静默失败，文件日志仍会记录错误
        pass


def register_error_log_sink() -> None:
    """
    注册错误日志 sink 到 Loguru。

    在 setup_logging() 之后调用。
    通过环境变量 WRITE_LOG_ERROR 控制是否启用（默认 false，不写入数据库）。
    """
    if os.getenv("WRITE_LOG_ERROR", "false").strip().lower() not in ("true", "1", "yes", "on"):
        logger.info("错误日志数据库 sink 未启用（WRITE_LOG_ERROR=false）")
        return

    logger.add(
        error_log_sink,
        level="ERROR",  # 仅处理 ERROR 及以上级别
        enqueue=True,   # 使用队列，在独立线程中处理
        catch=True,     # 捕获异常，不影响主程序
    )
    logger.info("错误日志数据库 sink 已注册")