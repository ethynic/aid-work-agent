"""
日志配置模块

使用loguru进行日志管理。

多 worker 安全：所有文件 sink 启用 enqueue=True，日志通过 loguru 内部队列串行化写入，
多个 Gunicorn worker 并发写同一文件不会产生交错损坏。

按日期分片：主日志和错误日志每天半夜自动切换到新文件，文件名格式
`aid-work-agent_YYYYMMDD.log` / `error_YYYYMMDD.log`，与 agent_session_logs /
skill_execute / llm_invoke_logs 等按日期命名的 jsonl 日志一致。
`http_api_audit_YYYYMMDD.log` 为 http_api 工具外发请求/响应审计日志（单行 JSON）。

保留期：15 天，由 src/core/log_retention.py 统一清理。
启动时清理一次，运行中每天日期切换时触发一次惰性清理。
"""

import sys
from datetime import datetime
from pathlib import Path

from loguru import logger

from src.core.log_retention import cleanup_old_logs


class _DatedFileSink:
    """
    按日期分片的文件 sink。

    文件名格式：{prefix}_YYYYMMDD.log
    每天半夜（日期变化时）自动切换到新文件。

    通过 loguru 的 enqueue=True，sink 只在 loguru 内部消费者线程单线程调用，
    缓存的文件句柄是线程安全的。

    多 worker 各自持有独立的 sink 实例和文件句柄；多进程以 append 模式写同一文件，
    POSIX O_APPEND 保证写入不覆盖（单条日志小于 PIPE_BUF 时原子）。
    """

    def __init__(self, log_dir: Path, prefix: str):
        self.log_dir = log_dir
        self.prefix = prefix
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._current_date: str | None = None
        self._file = None

    def _get_file(self):
        date_str = datetime.now().strftime("%Y%m%d")
        if date_str != self._current_date:
            # 日期变化：关闭旧文件句柄，打开新文件
            if self._file:
                try:
                    self._file.close()
                except Exception:
                    pass
            self._current_date = date_str
            self._file = open(
                self.log_dir / f"{self.prefix}_{date_str}.log",
                "a", encoding="utf-8"
            )
            # 日期切换时触发一次惰性清理（每天一次）
            # ⚠️ 暂时禁用：在 _DatedFileSink.__call__（loguru 消费者线程）中调用
            #    cleanup_old_logs 会在 sink 执行期间再次向 loguru 队列塞入大量 INFO 日志，
            #    主线程同步等待时可能死锁/挂起（Windows 上观察到的启动卡死现象）。
            #    启动清理已在 setup_logging 末尾（主线程）执行一次，足够覆盖日常场景。
            # try:
            #     cleanup_old_logs()
            # except Exception:
            #     pass
        return self._file

    def __call__(self, message):
        f = self._get_file()
        f.write(str(message))
        f.flush()


def _is_http_audit(record) -> bool:
    """http_api 审计记录标记（写独立审计文件，不进主日志/控制台）"""
    return record["extra"].get("http_audit") is True


def setup_logging(
    log_level: str = "INFO",
    log_dir: str = "log/agent",
    rotation: str = "10 MB",
    retention: str = "7 days",
    json_format: bool = False,
) -> None:
    """
    配置日志系统

    Args:
        log_level: 日志级别
        log_dir: 日志目录
        rotation: 已废弃（保留参数兼容旧调用），改用按日期分片
        retention: 已废弃（保留参数兼容旧调用），统一由 log_retention.py 管理
        json_format: 是否使用JSON格式
    """
    # 移除默认处理器
    logger.remove()

    # 控制台输出格式
    console_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
        "<level>{message}</level>"
    )

    # 添加控制台处理器
    logger.add(
        sys.stdout,
        format=console_format,
        level=log_level,
        colorize=True,
        filter=lambda record: not _is_http_audit(record),
    )

    # 创建日志目录
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    # 文件输出格式
    file_format = (
        "{time:YYYY-MM-DD HH:mm:ss} | "
        "{level: <8} | "
        "{name}:{function}:{line} | "
        "{message}"
    )

    # 主日志 - 按日期分片，多 worker 安全
    logger.add(
        _DatedFileSink(log_path, "aid-work-agent"),
        format=file_format,
        level=log_level,
        enqueue=True,
        filter=lambda record: not _is_http_audit(record),
    )

    # 错误日志 - 按日期分片，多 worker 安全
    logger.add(
        _DatedFileSink(log_path, "error"),
        format=file_format,
        level="ERROR",
        enqueue=True,
        filter=lambda record: not _is_http_audit(record),
    )

    # http_api 审计日志 - 记录外发 HTTP 请求/响应原文，供推送问题追溯，
    # 与主日志同保留周期（文件名命中 log_retention 清理模式，15 天）
    logger.add(
        _DatedFileSink(log_path, "http_api_audit"),
        format="{message}",
        level="INFO",
        enqueue=True,
        catch=True,
        filter=_is_http_audit,
    )

    if json_format:
        # JSON格式日志（用于日志收集系统）
        logger.add(
            _DatedFileSink(log_path, "aid-work-agent"),
            format="{message}",
            level=log_level,
            serialize=True,
            enqueue=True,
            filter=lambda record: not _is_http_audit(record),
        )

    # 启动时清理一次过期日志
    try:
        deleted = cleanup_old_logs()
        if deleted:
            logger.info(f"启动清理过期日志 {deleted} 个")
    except Exception as e:
        logger.warning(f"启动清理日志失败: {e}")

    # 注册数据库错误日志 sink（仅 ERROR 级别）
    try:
        from src.core.error_log_sink import register_error_log_sink
        register_error_log_sink()
    except Exception as e:
        # sink 注册失败不影响主程序
        logger.warning(f"错误日志数据库 sink 注册失败: {e}")

    logger.info(f"日志系统初始化完成，日志级别: {log_level}")


def get_logger(name: str = "aid-work-agent"):
    """
    获取日志记录器

    Args:
        name: 日志记录器名称

    Returns:
        Logger实例
    """
    return logger.bind(name=name)
