"""独立后台运行时（SERVER_MODE=background）

承载：
- APScheduler（系统任务 + 用户定时任务 + reconcile 对账），在其后台线程跑
- wecom_personal_rpa 服务端会话存档兜底轮询（asyncio 兄弟任务，内部自带 Redis 锁）
- recap 轮后任务消费者（API worker 入队 Redis，本进程执行，与 HTTP worker 重启解耦）
- wechat_mp 队列驱动 + 定时复核调度（WP7，asyncio 兄弟任务，内部自带单副本 Redis 锁）
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

    # 微信营销自动化适配器受信注册（weixin_marketing.enabled 门控内；默认 false 零注册）
    try:
        from src.weixin_marketing.registration import ensure_registered
        ensure_registered()
    except Exception as e:
        logger.warning(f"background runner: weixin_marketing registration failed: {e}")


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


async def _recap_consumer():
    """recap 任务消费者：轮询 Redis 队列 -> 重建 payload -> 复用 runner._run_tasks 执行。

    - 2s 轮询（recap 量级为每轮对话一条，无吞吐压力）
    - 单条消息消费失败不影响后续轮询；任务级失败由 _run_tasks 内部吞掉
    - 消费后立即 create_task，不串行等待执行完成（与 API worker 现有行为一致）
    """
    from src.core.cache_utils import CacheKeys
    from src.core.redis_client import redis_client

    queue_key = redis_client.make_key(CacheKeys.RECAP_QUEUE)
    logger.info(f"background runner: recap 消费者启动, queue={queue_key}")

    while not _stop.is_set():
        try:
            msg = await asyncio.to_thread(redis_client.lpop, queue_key)
        except Exception as e:
            logger.warning(f"background runner: recap 队列读取异常: {e}")
            msg = None

        if msg is None:
            try:
                await asyncio.wait_for(_stop.wait(), timeout=2)
            except asyncio.TimeoutError:
                pass
            continue

        try:
            # 解析与 create_task 必须在事件循环内执行（to_thread 线程中无 loop）；
            # 仅 lpop 的 Redis IO 放线程，解析本身是纯内存操作
            _handle_recap_message(msg)
        except Exception as e:
            logger.opt(exception=True).error(f"background runner: recap 消息处理异常: {e}")


def _handle_recap_message(msg) -> None:
    """解析单条 recap 队列消息并派发执行（同步入口，异常向上抛给消费者循环）"""
    import json
    import time

    from src.services.recap.runner import (
        RECAP_MAX_QUEUE_AGE_SECONDS,
        RecapPayload,
        _run_tasks,
        rebuild_tasks,
    )

    if not isinstance(msg, dict):
        try:
            msg = json.loads(msg) if isinstance(msg, str) else {}
        except (json.JSONDecodeError, TypeError):
            logger.warning(f"background runner: recap 消息格式非法，已丢弃: {str(msg)[:200]}")
            return

    payload = RecapPayload.from_dict(msg)
    tasks = rebuild_tasks(payload.task_config)
    if not tasks:
        logger.warning(f"background runner: recap 消息无可执行任务，已丢弃 session={payload.session_id}")
        return

    # 滞留过久的陈旧消息直接丢弃：滞留超过幂等键 TTL 后 SET NX 防重已失效，
    # 且用过期对话内容推送跟进对客户无意义。旧消息无 enqueued_at 时不检查（兼容发布窗口期）
    if (
        payload.enqueued_at
        and time.time() - payload.enqueued_at > RECAP_MAX_QUEUE_AGE_SECONDS
    ):
        logger.warning(
            f"background runner: recap 消息滞留超 {RECAP_MAX_QUEUE_AGE_SECONDS}s，"
            f"已丢弃 session={payload.session_id} round={payload.round_message_id}"
        )
        return

    bg_task = asyncio.create_task(_run_tasks(tasks, payload))
    _recap_bg_tasks.add(bg_task)
    bg_task.add_done_callback(_recap_bg_tasks.discard)


# recap 消费后派发的执行任务自持引用集（仅防 GC，无其他语义）
_recap_bg_tasks: set = set()


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
        logger.opt(exception=True).error(
            f"background runner: archive poller 启动失败（不影响 runner）: {e}",
        )

    # 3. 心跳
    asyncio.create_task(_heartbeat())

    # 4. recap 任务消费者（API worker 入队 Redis、本进程执行，与 HTTP worker
    # 重启解耦；多副本下 LPOP 天然单消费者，配 RECAP_TASK_DEDUP 双保险）
    asyncio.create_task(_recap_consumer())

    # 5. wechat_mp 队列驱动 + 定时复核（WP7；单副本 Redis 锁，抢不到/Redis 不可用
    # 时不启动；对齐 poller 模式 try/except，启动失败不影响 runner）
    _wmp_scheduler = None
    try:
        from src.wechat_mp.scheduler import WeChatMPScheduler

        _wmp_scheduler = WeChatMPScheduler()
        await _wmp_scheduler.start()
    except Exception as e:
        logger.opt(exception=True).error(
            f"background runner: wechat_mp scheduler 启动失败（不影响 runner）: {e}",
        )
    logger.info("background runner 就绪")

    await _stop.wait()
    logger.info("background runner 收到停止信号，开始优雅停机")

    # 停机：poller → wechat_mp scheduler → scheduler → DB 池
    try:
        if _poller is not None:
            await _poller.stop()
    except Exception as e:
        logger.warning(f"background runner: poller stop 异常: {e}")

    try:
        if _wmp_scheduler is not None:
            await _wmp_scheduler.stop()
    except Exception as e:
        logger.opt(exception=True).error(f"background runner: wechat_mp scheduler stop error: {e}")

    try:
        scheduled_task_manager.shutdown()
    except Exception as e:
        logger.opt(exception=True).error(f"background runner: scheduler shutdown error: {e}")

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
