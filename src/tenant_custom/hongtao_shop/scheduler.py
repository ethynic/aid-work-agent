"""宏陶商城同步调度器（照 WeChatMPScheduler 模式，设计 §8 v1.4）。

薄驱动入口：只做单副本锁、唤醒信号消费、到期源生成与租户分发；全部业务能力
仍在 HongtaoShopSyncService。租户定制模块的调度注册点是 background_runner
挂载块（模块目录外无平台侵入）。

- **单副本驱动锁**：`hongtao_shop_scheduler_lock`（TTL 300s 周期续期）；抢不到
  → 空转返回；Redis 不可用 → 拒绝启动（不能无锁降级），runner 不因此挂掉。
- **驱动协程**：非阻塞 lpop 唤醒（trigger_sync rpush）或 60s 兜底 → 领取轮：
  查有 queued run 的租户 → 逐租户 claim_and_run（Semaphore(4) 跨租户有限并发，
  单租户异常隔离）。唤醒丢失不丢任务（DB 队列唯一事实来源）。
- **30min tick（到期源生成器）**：扫描 bs_content_sync_sources 中
  module='hongtao_shop' 且 enabled 的源行，距最近一次任意类型 run 超过
  sync_interval_hours（默认 24h；手动同步同样计入新鲜度）且本模块无
  queued/running run → 建 queued run（trigger_type='scheduled'，无 items——
  worker 全量拉取后按 hash diff 自建）。幂等防堆积。
- **stale 回收**：每 5min 调 service.recover_stale_runs()（heartbeat 超 3600s，单闸决议阈值）。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import timedelta
from typing import Any, Dict, List, Optional

from loguru import logger

from src.db.database import get_db_connection
from src.knowledge.embedding.embedding_client import sanitize_error_info
from src.tenant_custom.hongtao_shop.notify import WAKEUP_KEY

# ------------------------------- 常量 -------------------------------

DRIVER_LOCK_PREFIX = "hongtao_shop_scheduler_lock"
DRIVER_LOCK_TTL_SECONDS = 300
LOCK_RETRY_SECONDS = 30
IDLE_SCAN_SECONDS = 60
TICK_INTERVAL_SECONDS = 1800  # 30min 到期源生成
RECOVER_INTERVAL_SECONDS = 300  # 5min stale 回收
STOP_JOIN_TIMEOUT_SECONDS = 10
TENANT_CONCURRENCY = 4
SCHEDULED_TICK_LIMIT = 20  # 单 tick 生成上限（源数异常护栏，设计 §8/§11）
DEFAULT_INTERVAL_HOURS = 24  # sources 缺省/非法时的同步周期（设计 §10 v1.2）
MAX_INTERVAL_HOURS = 24 * 365

MODULE = "hongtao_shop"


class HongtaoIngestScheduler:
    """hongtao_shop 队列驱动 + 到期源调度器（background runner 单实例运行）。"""

    def __init__(self, redis: Any = None, service: Any = None):
        if redis is None:
            from src.core.redis_client import redis_client

            redis = redis_client
        self._redis = redis
        self._service = service  # HongtaoShopSyncService；None 时 start() 懒加载
        self._stop_event = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._owner = uuid.uuid4().hex
        self._lock_key: Optional[str] = None
        self._redis_down_logged = False
        self._claim_requested = False
        self._active_owners: set = set()  # 本实例 claim 产生的 run owner（stop 收尾用）

    # ==================== 生命周期 ====================

    async def start(self) -> bool:
        """启动驱动（抢不到锁或 Redis 不可用 → 返回 False，runner 不受影响）。"""
        if self._task is not None and not self._task.done():
            logger.bind(module="hongtao_shop").warning("hongtao_shop 调度器已在运行，跳过重复启动")
            return True

        if not await asyncio.to_thread(self._redis.is_available):
            logger.bind(module="hongtao_shop").error(
                "后端日志：hongtao_shop 调度器拒绝启动：Redis 不可用（不能无锁单副本驱动）"
            )
            return False

        self._owner = uuid.uuid4().hex
        self._lock_key = self._redis.make_key(DRIVER_LOCK_PREFIX)
        acquired = await asyncio.to_thread(
            self._redis.acquire_lock, self._lock_key, self._owner, DRIVER_LOCK_TTL_SECONDS
        )
        if not acquired:
            logger.bind(module="hongtao_shop").info(
                "hongtao_shop 调度器已在其他副本运行，本副本空转返回 owner={}", self._owner
            )
            self._lock_key = None
            return False

        if self._service is None:
            from src.tenant_custom.hongtao_shop.service import HongtaoShopSyncService

            self._service = HongtaoShopSyncService()

        self._stop_event.clear()
        self._claim_requested = False
        self._redis_down_logged = False
        self._task = asyncio.create_task(self._run_loop(), name="hongtao-shop-scheduler")
        logger.bind(module="hongtao_shop").info(
            "hongtao_shop 调度器已启动 owner={} tick={}s 兜底={}s 并发={}",
            self._owner, TICK_INTERVAL_SECONDS, IDLE_SCAN_SECONDS, TENANT_CONCURRENCY,
        )
        return True

    async def stop(self) -> None:
        """优雅停机：置停机事件 → 等协程退出（宽限期后取消）→ 释放锁。

        取消会穿透在飞 run 的事务包装（CancelledError 非 Exception），run 停留
        running 会因部分唯一索引阻塞该租户直至 stale 回收（最长 1h）——故取消后
        按本实例产生的 owner_token 主动置 interrupted 收尾。
        """
        self._stop_event.set()
        task, self._task = self._task, None
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(task, timeout=STOP_JOIN_TIMEOUT_SECONDS)
            except Exception:  # noqa: BLE001
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        try:
            interrupted = await asyncio.to_thread(
                self._service.interrupt_runs_by_owners, list(self._active_owners)
            )
            if interrupted:
                logger.bind(module="hongtao_shop").warning(
                    "后端日志：hongtao_shop 停机中断在飞 run {} 个（防租户阻塞）", interrupted
                )
        except Exception as e:  # noqa: BLE001 收尾失败兜底走 stale 回收
            logger.opt(exception=True).warning(
                "hongtao_shop 停机收尾失败（由 stale 回收兜底）: {}", e
            )
        self._active_owners.clear()
        if self._lock_key:
            try:
                self._redis.release_lock(self._lock_key, self._owner)
            except Exception as e:  # noqa: BLE001 释放失败靠 TTL 过期兜底
                logger.bind(module="hongtao_shop").warning(
                    "hongtao_shop 调度器驱动锁释放失败（等待 TTL 过期）: {}", e
                )
            self._lock_key = None
        logger.bind(module="hongtao_shop").info("hongtao_shop 调度器已停止")

    # ==================== 驱动循环 ====================

    async def _run_loop(self) -> None:
        """驱动主循环：唤醒/兜底 → 领取轮 → stale 回收（5min）→ tick（30min）。"""
        next_recover_at = time.monotonic() + RECOVER_INTERVAL_SECONDS
        next_tick_at = time.monotonic() + TICK_INTERVAL_SECONDS
        while not self._stop_event.is_set():
            try:
                if not await self._ensure_driver_lock():
                    try:
                        await asyncio.wait_for(
                            self._stop_event.wait(), timeout=LOCK_RETRY_SECONDS
                        )
                    except asyncio.TimeoutError:
                        pass
                    continue

                if not self._claim_requested:
                    await self._wait_for_wakeup()
                self._claim_requested = False
                if self._stop_event.is_set():
                    break

                await self._claim_round()

                now = time.monotonic()
                if now >= next_recover_at:
                    recovered = await asyncio.to_thread(self._service.recover_stale_runs)
                    if recovered.get("interrupted"):
                        logger.bind(module="hongtao_shop").warning(
                            "hongtao_shop 调度器回收 stale run {}", recovered
                        )
                    next_recover_at = time.monotonic() + RECOVER_INTERVAL_SECONDS

                if now >= next_tick_at:
                    tick = await asyncio.to_thread(self._run_tick)
                    if tick.get("scheduled_enqueued"):
                        self._claim_requested = True  # 有新任务立即领取
                    next_tick_at = time.monotonic() + TICK_INTERVAL_SECONDS
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 单轮异常不终止驱动
                logger.opt(exception=True).error(
                    "后端日志：hongtao_shop 调度器单轮异常（继续）: {}", e
                )
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=5)
                except asyncio.TimeoutError:
                    pass

    async def _ensure_driver_lock(self) -> bool:
        """续期驱动锁；失锁重取；Redis 不可用暂停驱动（不无锁降级）。"""
        if not await asyncio.to_thread(self._redis.is_available):
            if not self._redis_down_logged:
                logger.bind(module="hongtao_shop").error(
                    "后端日志：hongtao_shop 调度器暂停驱动：Redis 不可用（不无锁降级，恢复后自动继续）"
                )
                self._redis_down_logged = True
            return False
        if self._redis_down_logged:
            logger.bind(module="hongtao_shop").info("hongtao_shop 调度器：Redis 已恢复，继续驱动")
            self._redis_down_logged = False
        renewed = await asyncio.to_thread(
            self._redis.renew_lock, self._lock_key, self._owner, DRIVER_LOCK_TTL_SECONDS
        )
        if renewed:
            return True
        return await asyncio.to_thread(
            self._redis.acquire_lock, self._lock_key, self._owner, ex=DRIVER_LOCK_TTL_SECONDS
        )

    async def _wait_for_wakeup(self) -> None:
        """非阻塞消费唤醒信号；无信号等待 60s 兜底（或停机）。"""
        if await asyncio.to_thread(self._redis.lpop, self._redis.make_key(WAKEUP_KEY)) is not None:
            while (
                await asyncio.to_thread(self._redis.lpop, self._redis.make_key(WAKEUP_KEY))
                is not None
            ):
                pass
            return
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=IDLE_SCAN_SECONDS)
        except asyncio.TimeoutError:
            pass

    # ==================== 领取轮 ====================

    def _list_queued_tenants(self) -> List[str]:
        """有本模块 queued run 的租户列表（只读；DB 队列是唯一事实来源）。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT DISTINCT tenant_id FROM bs_content_sync_runs
                WHERE module = %s AND status = 'queued'
                """,
                (MODULE,),
            )
            return [r["tenant_id"] for r in cursor.fetchall()]

    async def _claim_round(self) -> None:
        """一轮领取：逐租户 claim_and_run，跨租户有限并发，单租户异常隔离。"""
        try:
            tenants = await asyncio.to_thread(self._list_queued_tenants)
        except Exception as e:  # noqa: BLE001
            logger.opt(exception=True).error("hongtao_shop 队列租户发现失败: {}", e)
            return
        if not tenants:
            return
        semaphore = asyncio.Semaphore(TENANT_CONCURRENCY)
        owner_tokens_collector: list = []

        async def _run_tenant(tenant_id: str) -> None:
            async with semaphore:
                try:
                    result = await self._service.claim_and_run(
                        tenant_id, owner_tokens_collector
                    )
                    if result.get("executed"):
                        logger.bind(module="hongtao_shop").info(
                            "hongtao_shop 调度器领取完成 tenant_id={} runs={} reason={}",
                            tenant_id, len(result.get("run_ids") or []), result.get("reason"),
                        )
                except Exception as e:  # noqa: BLE001 单租户异常不中断其他租户
                    logger.opt(exception=True).error(
                        "hongtao_shop 调度器租户领取异常 tenant_id={}: {}",
                        tenant_id, sanitize_error_info(str(e)),
                    )

        await asyncio.gather(*(_run_tenant(t) for t in tenants))
        self._active_owners.update(owner_tokens_collector)

    # ==================== 30min tick：到期源生成器 ====================

    def _run_tick(self) -> Dict[str, int]:
        """到期源生成（同步方法，调用方经 asyncio.to_thread 执行）。幂等防堆积。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("SELECT now()::timestamp AS db_now")
                db_now = cursor.fetchone()["db_now"]
                # 启用的源行（module 过滤；enabled 门槛即「定时开关」）
                cursor.execute(
                    """
                    SELECT tenant_id, sync_interval_hours
                    FROM bs_content_sync_sources
                    WHERE module = %s AND enabled = TRUE
                    """,
                    (MODULE,),
                )
                sources = [
                    {
                        "tenant_id": r["tenant_id"],
                        "interval_hours": _parse_interval_hours(r["sync_interval_hours"]),
                    }
                    for r in cursor.fetchall()
                ]
                if not sources:
                    return {"scheduled_enqueued": 0}
                tenant_ids = [s["tenant_id"] for s in sources]
                # 新鲜度基准：最近一次**任意类型** run 的 created_at（手动同步同样
                # 计入——刚手动跑过就不该立刻再定时跑）
                cursor.execute(
                    """
                    SELECT tenant_id, max(created_at) AS last_created
                    FROM bs_content_sync_runs
                    WHERE module = %s AND tenant_id = ANY(%s)
                    GROUP BY tenant_id
                    """,
                    (MODULE, tenant_ids),
                )
                last_created = {r["tenant_id"]: r["last_created"] for r in cursor.fetchall()}
                # 防堆积：本模块已有 queued/running run 的租户跳过（模块单源串行）
                cursor.execute(
                    """
                    SELECT DISTINCT tenant_id FROM bs_content_sync_runs
                    WHERE module = %s AND status IN ('queued', 'running')
                      AND tenant_id = ANY(%s)
                    """,
                    (MODULE, tenant_ids),
                )
                active_tenants = {r["tenant_id"] for r in cursor.fetchall()}

                enqueued = 0
                for source in sources:
                    if enqueued >= SCHEDULED_TICK_LIMIT:
                        logger.bind(module="hongtao_shop").warning(
                            "hongtao_shop scheduled 单 tick 生成达上限 {}，余量留到下轮",
                            SCHEDULED_TICK_LIMIT,
                        )
                        break
                    if source["tenant_id"] in active_tenants:
                        continue
                    last = last_created.get(source["tenant_id"])
                    if last is not None:
                        if db_now - last < timedelta(hours=source["interval_hours"]):
                            continue
                    cursor.execute(
                        """
                        INSERT INTO bs_content_sync_runs
                            (tenant_id, module, user_id, trigger_type, status)
                        VALUES (%s, %s, NULL, 'scheduled', 'queued')
                        RETURNING id
                        """,
                        (source["tenant_id"], MODULE),
                    )
                    run_id = cursor.fetchone()["id"]
                    enqueued += 1
                    logger.bind(module="hongtao_shop").info(
                        "hongtao_shop scheduled 同步入队 tenant_id={} run_id={} interval={}h",
                        source["tenant_id"], run_id, source["interval_hours"],
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return {"scheduled_enqueued": enqueued}


def _parse_interval_hours(raw: Any) -> float:
    """sync_interval_hours 解析容错：非法/<=0/NaN/超大 → 回落默认 24h。"""
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError):
        return DEFAULT_INTERVAL_HOURS
    if not (value > 0):
        return DEFAULT_INTERVAL_HOURS
    return min(value, MAX_INTERVAL_HOURS)


def create_scheduler():
    """optional_modules 工厂约定：无参构造调度器实例（background_runner 通用加载）。"""
    return HongtaoIngestScheduler()
