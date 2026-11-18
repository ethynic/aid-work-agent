"""就业/创业数据源同步调度器（照 hongtao_shop/scheduler.py 模式，两模块共用基类）。

薄驱动入口：只做单副本锁、唤醒信号消费、到期源生成与租户分发；全部业务能力
仍在各模块 service。租户定制模块的调度注册点是 background_runner 挂载块
（configs/config.yaml tenant_custom_modules 清单 → optional_modules 通用加载，
平台层零租户专名）。

- **单副本驱动锁**：`{module}_scheduler_lock`（TTL 300s 周期续期）；抢不到
  （可能为强杀残留的幽灵锁）→ 仍启动驱动任务，循环内每 30s 重试接管；Redis
  不可用 → 拒绝启动（不能无锁降级），runner 不因此挂掉。
- **驱动协程**：非阻塞 lpop 唤醒（trigger_sync rpush）或 60s 兜底 → 领取轮：
  查有 queued run 的租户 → 逐租户 claim_and_run（Semaphore(4) 跨租户有限并发，
  单租户异常隔离）。唤醒丢失不丢任务（DB 队列唯一事实来源）。
- **30min tick（到期源生成器）**：扫描 bs_content_sync_sources 中本 module 且
  enabled 的源行，距最近一次任意类型 run 超过 sync_interval_hours 且本模块无
  queued/running run → trigger_sync('scheduled')（余额预检 ≤0 → 终态
  skipped_no_credit，账本可见）。幂等防堆积。
- **每日对账 run**（仅 lyjy_employment，凌晨低峰窗口）：trigger_type='reconcile'
  （水位清零全量比对，防 refreshtime 不随编辑更新的滞后）；距上次对账 run ≥24h
  且当前 DB 时间（会话时区 Asia/Shanghai）落在低峰窗口内才生成。sccy_policy
  每个 run 本就是全量比对（默认间隔 24h 即每日全量），无需独立对账类型。
- **stale 回收**：每 5min 调 service.recover_stale_runs()（heartbeat 超 3600s）。
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
from src.tenant_custom.lyjy_employment.notify import _wakeup_key

# ------------------------------- 常量 -------------------------------

DRIVER_LOCK_TTL_SECONDS = 300
LOCK_RETRY_SECONDS = 30
IDLE_SCAN_SECONDS = 60
TICK_INTERVAL_SECONDS = 1800  # 30min 到期源生成
RECOVER_INTERVAL_SECONDS = 300  # 5min stale 回收
STOP_JOIN_TIMEOUT_SECONDS = 10
TENANT_CONCURRENCY = 4
DEFAULT_INTERVAL_HOURS = 1  # sources 缺省/非法时回落（lyjy 时效性强；sccy 覆写 24）
MAX_INTERVAL_HOURS = 24 * 365
SCHEDULED_TICK_LIMIT = 20  # 单 tick 生成上限（源数异常护栏，宏陶同款）

# 每日对账：低峰窗口 [1, 6) 小时（DB 会话时区 Asia/Shanghai）+ 距上次对账 ≥24h
RECONCILE_WINDOW_HOURS = (1, 6)
RECONCILE_MIN_GAP_HOURS = 24


def _parse_interval_hours(raw: Any, default_hours: float) -> float:
    """sync_interval_hours 解析容错：非法/<=0/NaN/超大 → 回落默认值。"""
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError):
        return default_hours
    if not (value > 0):
        return default_hours
    return min(value, MAX_INTERVAL_HOURS)


class KbSyncSchedulerBase:
    """客户数据源队列驱动 + 到期源调度器基类（background runner 单实例运行）。"""

    MODULE = ""
    INTERVAL_DEFAULT_HOURS = DEFAULT_INTERVAL_HOURS
    ENABLE_RECONCILE = False  # lyjy 对账 run 开关（sccy 每个 run 即全量，不开启）

    def __init__(self, redis: Any = None, service: Any = None):
        if redis is None:
            from src.core.redis_client import redis_client

            redis = redis_client
        self._redis = redis
        self._service = service  # KbSyncServiceBase 子类；None 时 start() 懒加载
        self._stop_event = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._owner = uuid.uuid4().hex
        self._lock_key: Optional[str] = None
        self._redis_down_logged = False
        self._claim_requested = False
        self._active_owners: set = set()  # 本实例 claim 产生的 run owner（stop 收尾用）

    # ==================== 生命周期 ====================

    async def start(self) -> bool:
        """启动驱动；锁被占也启动驱动任务（循环内重试接管），Redis 不可用拒绝启动。"""
        if self._task is not None and not self._task.done():
            logger.bind(module=self.MODULE).warning(
                "{} 调度器已在运行，跳过重复启动", self.MODULE
            )
            return True

        if not await asyncio.to_thread(self._redis.is_available):
            logger.bind(module=self.MODULE).error(
                "后端日志：{} 调度器拒绝启动：Redis 不可用（不能无锁单副本驱动）",
                self.MODULE,
            )
            return False

        if self._service is None:
            self._service = self._create_service()

        self._owner = uuid.uuid4().hex
        self._lock_key = self._redis.make_key(f"{self.MODULE}_scheduler_lock")
        acquired = await asyncio.to_thread(
            self._redis.acquire_lock, self._lock_key, self._owner, DRIVER_LOCK_TTL_SECONDS
        )
        self._stop_event.clear()
        self._claim_requested = False
        self._redis_down_logged = False
        self._task = asyncio.create_task(
            self._run_loop(), name=f"{self.MODULE}-scheduler"
        )
        if acquired:
            logger.bind(module=self.MODULE).info(
                "{} 调度器已启动 owner={} tick={}s 兜底={}s 并发={} 对账窗口={}h",
                self.MODULE, self._owner, TICK_INTERVAL_SECONDS, IDLE_SCAN_SECONDS,
                TENANT_CONCURRENCY,
                RECONCILE_WINDOW_HOURS if self.ENABLE_RECONCILE else "off",
            )
        else:
            # 锁被占：可能为强杀残留的幽灵锁（TTL 过期后可接管），失锁自愈路径重试
            logger.bind(module=self.MODULE).info(
                "{} 调度器锁被占（可能为强杀残留幽灵锁），驱动任务已启动，"
                "循环内每 {}s 重试接管 owner={}",
                self.MODULE, LOCK_RETRY_SECONDS, self._owner,
            )
        return True

    def _create_service(self):
        """懒加载本模块 service 单例（子类覆盖）。"""
        raise NotImplementedError

    async def stop(self) -> None:
        """优雅停机：置停机事件 → 等协程退出（宽限期后取消）→ 按 owner 收尾 → 释放锁。"""
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
                logger.bind(module=self.MODULE).warning(
                    "后端日志：{} 停机中断在飞 run {} 个（防租户阻塞）",
                    self.MODULE, interrupted,
                )
        except Exception as e:  # noqa: BLE001 收尾失败兜底走 stale 回收
            logger.opt(exception=True).warning(
                "{} 停机收尾失败（由 stale 回收兜底）: {}", self.MODULE, e
            )
        self._active_owners.clear()
        if self._lock_key:
            try:
                self._redis.release_lock(self._lock_key, self._owner)
            except Exception as e:  # noqa: BLE001 释放失败靠 TTL 过期兜底
                logger.bind(module=self.MODULE).warning(
                    "{} 调度器驱动锁释放失败（等待 TTL 过期）: {}", self.MODULE, e
                )
            self._lock_key = None
        logger.bind(module=self.MODULE).info("{} 调度器已停止", self.MODULE)

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
                        logger.bind(module=self.MODULE).warning(
                            "{} 调度器回收 stale run {}", self.MODULE, recovered
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
                    "后端日志：{} 调度器单轮异常（继续）: {}", self.MODULE, e
                )
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=5)
                except asyncio.TimeoutError:
                    pass

    async def _ensure_driver_lock(self) -> bool:
        """续期驱动锁；失锁重取；Redis 不可用暂停驱动（不无锁降级）。"""
        if not await asyncio.to_thread(self._redis.is_available):
            if not self._redis_down_logged:
                logger.bind(module=self.MODULE).error(
                    "后端日志：{} 调度器暂停驱动：Redis 不可用（不无锁降级，恢复后自动继续）",
                    self.MODULE,
                )
                self._redis_down_logged = True
            return False
        if self._redis_down_logged:
            logger.bind(module=self.MODULE).info(
                "{} 调度器：Redis 已恢复，继续驱动", self.MODULE
            )
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
        key = self._redis.make_key(_wakeup_key(self.MODULE))
        if await asyncio.to_thread(self._redis.lpop, key) is not None:
            while await asyncio.to_thread(self._redis.lpop, key) is not None:
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
                (self.MODULE,),
            )
            return [r["tenant_id"] for r in cursor.fetchall()]

    async def _claim_round(self) -> None:
        """一轮领取：逐租户 claim_and_run，跨租户有限并发，单租户异常隔离。"""
        try:
            tenants = await asyncio.to_thread(self._list_queued_tenants)
        except Exception as e:  # noqa: BLE001
            logger.opt(exception=True).error("{} 队列租户发现失败: {}", self.MODULE, e)
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
                        logger.bind(module=self.MODULE).info(
                            "{} 调度器领取完成 tenant_id={} runs={} reason={}",
                            self.MODULE, tenant_id,
                            len(result.get("run_ids") or []), result.get("reason"),
                        )
                except Exception as e:  # noqa: BLE001 单租户异常不中断其他租户
                    logger.opt(exception=True).error(
                        "{} 调度器租户领取异常 tenant_id={}: {}",
                        self.MODULE, tenant_id, sanitize_error_info(str(e)),
                    )

        await asyncio.gather(*(_run_tenant(t) for t in tenants))
        self._active_owners.update(owner_tokens_collector)

    # ==================== 30min tick：到期源生成器 ====================

    def _run_tick(self) -> Dict[str, int]:
        """到期源生成（同步方法，调用方经 asyncio.to_thread 执行）。幂等防堆积。

        受理统一走 service.trigger_sync：余额 ≤0 → 终态 skipped_no_credit（账本
        可见）；已有 queued/running → 去重返回。到期判定与宏陶一致：距最近一次
        **任意类型** run 超 sync_interval_hours（手动同步同样计入新鲜度）。
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT now()::timestamp AS db_now")
            db_now = cursor.fetchone()["db_now"]
            # 启用的源行（module 过滤；enabled 门槛即「定时开关」）
            cursor.execute(
                """
                SELECT tenant_id, sync_interval_hours
                FROM bs_content_sync_sources
                WHERE module = %s AND enabled = TRUE
                """,
                (self.MODULE,),
            )
            sources = [
                {
                    "tenant_id": r["tenant_id"],
                    "interval_hours": _parse_interval_hours(
                        r["sync_interval_hours"], self.INTERVAL_DEFAULT_HOURS
                    ),
                }
                for r in cursor.fetchall()
            ]
            if not sources:
                return {"scheduled_enqueued": 0}
            tenant_ids = [s["tenant_id"] for s in sources]
            # 新鲜度基准：最近一次任意类型 run 的 created_at（手动同样计入）
            cursor.execute(
                """
                SELECT tenant_id, max(created_at) AS last_created
                FROM bs_content_sync_runs
                WHERE module = %s AND tenant_id = ANY(%s)
                GROUP BY tenant_id
                """,
                (self.MODULE, tenant_ids),
            )
            last_created = {r["tenant_id"]: r["last_created"] for r in cursor.fetchall()}
            # 防堆积：本模块已有 queued/running run 的租户跳过（模块单源串行）
            cursor.execute(
                """
                SELECT DISTINCT tenant_id FROM bs_content_sync_runs
                WHERE module = %s AND status IN ('queued', 'running')
                  AND tenant_id = ANY(%s)
                """,
                (self.MODULE, tenant_ids),
            )
            active_tenants = {r["tenant_id"] for r in cursor.fetchall()}
            # 对账基准：最近一次 reconcile run 的 created_at（lyjy 每日全量比对）
            last_reconcile: Dict[str, Any] = {}
            if self.ENABLE_RECONCILE:
                cursor.execute(
                    """
                    SELECT tenant_id, max(created_at) AS last_created
                    FROM bs_content_sync_runs
                    WHERE module = %s AND tenant_id = ANY(%s)
                      AND trigger_type = 'reconcile'
                    GROUP BY tenant_id
                    """,
                    (self.MODULE, tenant_ids),
                )
                last_reconcile = {
                    r["tenant_id"]: r["last_created"] for r in cursor.fetchall()
                }

            enqueued = 0
            for source in sources:
                if enqueued >= SCHEDULED_TICK_LIMIT:
                    logger.bind(module=self.MODULE).warning(
                        "{} scheduled 单 tick 生成达上限 {}，余量留到下轮",
                        self.MODULE, SCHEDULED_TICK_LIMIT,
                    )
                    break
                tenant_id = source["tenant_id"]
                if tenant_id in active_tenants:
                    continue
                last = last_created.get(tenant_id)
                interval_due = last is None or (
                    db_now - last >= timedelta(hours=source["interval_hours"])
                )
                reconcile_due = (
                    self.ENABLE_RECONCILE
                    and self._reconcile_due(db_now, last_reconcile.get(tenant_id))
                )
                if not interval_due and not reconcile_due:
                    continue
                # 对账优先：reconcile 本身是全量同步（覆盖增量语义），且会推进
                # 新鲜度基准，窗口内不会与 interval run 叠跑
                trigger_type = "reconcile" if reconcile_due else "scheduled"
                # 受理统一入口：余额预检 ≤0 → skipped_no_credit 终态；在队去重
                result = self._service.trigger_sync(tenant_id, trigger_type)
                enqueued += 1
                logger.bind(module=self.MODULE).info(
                    "{} 同步入队 tenant_id={} run_id={} type={} interval={}h",
                    self.MODULE, tenant_id, result.get("run_id"),
                    trigger_type, source["interval_hours"],
                )
        return {"scheduled_enqueued": enqueued}

    def _reconcile_due(self, db_now, last_reconcile: Optional[Any]) -> bool:
        """对账 run 到期判定：低峰窗口内（DB 会话时区挂钟）且距上次 ≥24h。"""
        window_start, window_end = RECONCILE_WINDOW_HOURS
        if not (window_start <= db_now.hour < window_end):
            return False
        if last_reconcile is None:
            return True
        return db_now - last_reconcile >= timedelta(hours=RECONCILE_MIN_GAP_HOURS)


class LyjyEmploymentScheduler(KbSyncSchedulerBase):
    """聊易就业同步调度器（常规 interval run + 每日凌晨对账 run）。"""

    MODULE = "lyjy_employment"
    INTERVAL_DEFAULT_HOURS = DEFAULT_INTERVAL_HOURS
    ENABLE_RECONCILE = True

    def _create_service(self):
        from src.tenant_custom.lyjy_employment.service import lyjy_employment_service

        return lyjy_employment_service


def create_scheduler():
    """optional_modules 工厂约定：无参构造调度器实例（background_runner 通用加载）。"""
    return LyjyEmploymentScheduler()
