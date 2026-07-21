"""独立后台运行时（SERVER_MODE=background）

承载：
- APScheduler（系统任务 + 用户定时任务 + reconcile 对账），在其后台线程跑
- wecom_personal_rpa 服务端会话存档兜底轮询（asyncio 兄弟任务，内部自带 Redis 锁）
- 心跳文件（供 healthcheck 判活）

不启 FastAPI，不 import master_agent（由各回调懒加载）。

入口：``python -m src.background_runner``
"""

import asyncio
import os
import signal

from loguru import logger

# 心跳文件路径（相对 cwd）。healthcheck 用 `find -mmin -2` 判活
HEARTBEAT_FILE = os.path.join("storage", ".bg_runner_alive")
HEARTBEAT_INTERVAL = 30  # 秒

# 跨线程停机信号：signal handler 在主线程触发，经 call_soon_threadsafe 唤醒 asyncio Event
_stop: asyncio.Event = asyncio.Event()


def _init_resources():
    """初始化运行时依赖（DB 池 + 日志 + channel 表）。

    关键资源失败直接抛出终止启动；非关键资源失败降级告警。
    """
    from src.config.logging import setup_logging
    setup_logging(log_level=os.getenv("LOG_LEVEL", "INFO"), log_dir="log/agent")

    from src.db.database import init_postgres_pool, init_database, init_logs_pool
    logger.info("background runner: init_postgres_pool ...")
    init_postgres_pool()  # 关键
    logger.info("background runner: init_database ...")
    init_database()  # 关键

    # 日志库连接池（可观测性，非关键）
    try:
        logger.info("background runner: init_logs_pool ...")
        from src.db.database import init_logs_tables
        if init_logs_pool():
            init_logs_tables()
    except Exception as e:
        logger.warning(f"background runner: logs pool init failed (non-critical): {e}")

    # channel 表（wecom_kf 回调 / archive poller 需要）
    try:
        from src.channels.session import channel_session_manager
        channel_session_manager._ensure_tables()
    except Exception as e:
        logger.warning(f"background runner: channel tables ensure failed: {e}")


async def _heartbeat():
    """每 30s 写一次心跳文件，供 healthcheck 判活。"""
    while not _stop.is_set():
        try:
            os.makedirs(os.path.dirname(HEARTBEAT_FILE), exist_ok=True)
            with open(HEARTBEAT_FILE, "w") as f:
                f.write(str(asyncio.get_event_loop().time()))
        except Exception as e:
            logger.warning(f"background runner: heartbeat write failed: {e}")
        try:
            await asyncio.wait_for(_stop.wait(), timeout=HEARTBEAT_INTERVAL)
        except asyncio.TimeoutError:
            pass  # 正常超时，继续下一轮


async def _run():
    """主协程：初始化 → 启调度器 → 启 poller → 启心跳 → 等停机信号 → 优雅关闭。"""
    logger.info("background runner 启动")

    _init_resources()

    # 1. APScheduler（线程化，非阻塞）
    from src.scheduler.manager import scheduled_task_manager
    scheduled_task_manager.start()  # 抢 Redis 锁；失败（已有副本）→ 空转返回

    # 2. archive poller（兄弟异步任务，自带 Redis 锁，不改其内部）
    _poller = None
    try:
        from src.channels.wecom_personal_rpa.archive.poller import poller as _poller
        await _poller.start()
    except Exception as e:
        logger.error(
            f"background runner: archive poller 启动失败（不影响 runner）: {e}",
            exc_info=True,
        )

    # 3. 心跳
    asyncio.create_task(_heartbeat())
    logger.info("background runner 就绪")

    await _stop.wait()
    logger.info("background runner 收到停止信号，开始优雅停机")

    # 停机：poller → scheduler → DB 池
    try:
        if _poller is not None:
            await _poller.stop()
    except Exception as e:
        logger.warning(f"background runner: poller stop 异常: {e}")

    try:
        scheduled_task_manager.shutdown()
    except Exception as e:
        logger.error(f"background runner: scheduler shutdown error: {e}", exc_info=True)

    try:
        from src.db.database import close_postgres_pool, close_logs_pool
        close_postgres_pool()
        close_logs_pool()
    except Exception:
        pass

    logger.info("background runner 已停止")


def main():
    """入口：主线程 asyncio loop 承载 poller + 心跳；信号经 call_soon_threadsafe 唤醒 Event。"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _on_signal(*_):
        logger.info("background runner: 收到停止信号")
        loop.call_soon_threadsafe(_stop.set)

    # Linux 容器主要收到 SIGTERM；开发期 Ctrl+C 走 SIGINT
    try:
        signal.signal(signal.SIGTERM, _on_signal)
        signal.signal(signal.SIGINT, _on_signal)
    except (ValueError, OSError) as e:
        # 非主线程或平台不支持时，退化为不拦截（容器仍可通过 SIGKILL 终止）
        logger.warning(f"background runner: signal handler 注册失败: {e}")

    try:
        loop.run_until_complete(_run())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
