"""微信公众号定时复核与队列驱动（WP7，设计 §3/§5.1，计划 WP7 节）。

定位（分层规则）：本模块是**薄驱动入口**——只做单副本锁、唤醒信号消费、
到期任务生成与租户分发；全部抓取/入库/计费/领取能力仍在 WeChatMPSyncService。

- **单副本驱动锁**：start() 时 Redis acquire_lock（随机 owner、TTL+周期续期）；
  抢不到（已有副本）→ 记日志空转返回；Redis 不可用 → **拒绝启动**（设计硬规则：
  不能无锁降级），但 background runner 整体不因此挂掉（记 ERROR 即可）。
- **驱动协程（短周期）**：非阻塞 lpop 唤醒信号 key（受理侧经 notify.rpush 跨进程
  唤醒）；取到或每 60s 兜底 → 执行一轮领取：查有 queued run 的租户（SELECT
  DISTINCT，只读跨租户租户发现，仅驱动使用）→ 逐租户 claim_and_run（async，
  内部自带租户 Redis 锁 + DB running 唯一第二道闸），跨租户有限并发
  （Semaphore，默认 4），单租户异常捕获不中断其他租户。
  **唤醒信号丢失不丢任务**：DB 队列是唯一事实来源，60s 兜底扫描保证最终被领取。
- **30min tick（复核/重试任务生成器）**（自身同步 DB 调用，经 asyncio.to_thread 执行）：
  ① 失败退避到期（processing_status IN ('sync_failed','pending') 且 next_retry_at
  <= now()，含 no_credit 退避保持的 pending）→ 建重试 run（trigger_type='retry'）
  + pending item（action=NULL，worker 视为 'new'）；
  ② 存量 active 文章存活复核（processing_status ∈ ('success','deferred') 且
  last_checked_at 超 24h，NULLS FIRST 优先从未复核的）→ 建 recheck run
  （trigger_type='recheck'）+ pending item（action='check'）；deferred（P2 图片
  VL 待解析）借同一通道实现「将自动重试」承诺（deferred 置 next_retry_at=NULL
  不进 ① 失败退避）。
  两个生成器都幂等：同文章已有挂在 queued/running run 上的 pending/running item
  就跳过；查询、建 run 与入 items 同事务。tick 只生成任务，不改文章状态
  （next_retry_at/last_checked_at 由 worker 处理时更新；run 若中断，到期条件
  依旧成立，下一 tick 自动重新生成——中断可恢复）。
  单 tick 每租户上限：重试 50 / 复核 20（限速），超出留到下轮。
- **30min tick 第③生成器（WP9 接口通道定时对账）**：扫描 tenant_channel_configs
  中 channel_type='wechat_mp' 且 verified=1、config（TEXT 列，jsonb 取值显式
  ::jsonb 转换）内 enabled 且 appid/secret 均非空的配置，当该 (tenant_id,
  config_id) 最近一条 trigger_type='scheduled' 的 run 的 created_at 早于
  now()-sync_interval_hours（config 字段，默认 6，解析容错）或不存在、且该配置
  无 queued/running 的 scheduled run 时 → 同事务建 queued run（无 items，
  config_id 落值；worker 对账 batchget 后按 diff 建 items）。到期判定用
  「最近尝试时间」=run created_at——失败 run 终态后随周期自然重试（退避语义）。
- **30min tick 第④生成器（WP13 清单源定时对账）**：扫描 tenant_channel_configs
  中 channel_type='wechat_mp' 且 config 内 list_session_token 非空（已扫码绑定）、
  list_sync_status ∈ ('active','expiring') 的配置，到期（sync_interval_hours，清单
  源默认 1h）且无 queued/running 的 list_sync run 时 → 建 queued run（无 items，
  config_id 落值；worker 拉自有号清单后按 diff 建 items）。expired/account_error
  停止自动拉取，重新扫码绑定后自然恢复。
- **stale 回收**：每 5min 调 service.recover_stale_runs()（周期短于 tick）。
- **stop()**：置停机事件 → 驱动协程退出 → 释放锁；不强行中断进行中的
  claim_and_run（宽限期后取消，由 service 内部锁与 heartbeat 保证可恢复）。
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
from src.wechat_mp.notify import WAKEUP_KEY

# ------------------------------- 常量 -------------------------------

DRIVER_LOCK_PREFIX = "wechat_mp_scheduler_lock"  # 单副本驱动锁（make_key 自动拼全局前缀）
DRIVER_LOCK_TTL_SECONDS = 300  # 锁租约；驱动循环内周期续期（远短于租约）
LOCK_RETRY_SECONDS = 30  # 失锁/Redis 故障后的空转重试间隔
IDLE_SCAN_SECONDS = 60  # 无唤醒信号时的兜底领取扫描周期
TICK_INTERVAL_SECONDS = 1800  # 30min tick（复核/重试任务生成）
RECOVER_INTERVAL_SECONDS = 300  # stale run 回收周期（5min，短于 tick）
STOP_JOIN_TIMEOUT_SECONDS = 10  # stop 等待驱动协程退出的宽限期
TENANT_CONCURRENCY = 4  # 跨租户领取有限并发（Semaphore）
RETRY_TICK_LIMIT = 50  # 每 tick 每租户重试入队上限（超出留到下轮）
RECHECK_TICK_LIMIT = 20  # 每 tick 每租户复核入队上限（限速）
RECHECK_INTERVAL_HOURS = 24  # 存活复核间隔（每篇）
SCHEDULED_TICK_LIMIT = 100  # 每 tick 定时对账 run 生成上限（配置量异常时的护栏）
SCHEDULED_DEFAULT_INTERVAL_HOURS = 6  # sync_interval_hours 缺失/非法时的默认对账周期
SCHEDULED_MAX_INTERVAL_HOURS = 24 * 365  # sync_interval_hours 上限护栏（防溢出 timedelta）
# WP13 清单源（第④生成器）：默认同步频率 1h（清单拉取 1~2 请求/次，高频同步最大化
# 会话存活概率，设计 §3.3）；不改 ③ 的默认 6h 回调/接口语义
LIST_SYNC_DEFAULT_INTERVAL_HOURS = 1
LIST_SYNC_TICK_LIMIT = 100  # 每 tick 清单对账 run 生成上限

# 重试到期条件（含 no_credit 退避保持的 pending 与失败退避的 sync_failed）。
# next_retry_at 由 service 以 Python UTC naive 写入，读取统一按 UTC 渲染比较，
# 避免会话时区（如 Asia/Shanghai）造成的偏移
_RETRY_DUE_SQL = """
    SELECT a.id FROM bs_wechat_mp_articles a
    WHERE a.tenant_id = %s
      AND a.status = 'active'
      AND a.processing_status IN ('sync_failed', 'pending')
      AND a.next_retry_at IS NOT NULL
      AND a.next_retry_at <= (now() AT TIME ZONE 'UTC')
      AND NOT EXISTS (
          SELECT 1 FROM bs_wechat_mp_sync_items it
          JOIN bs_wechat_mp_sync_runs r
            ON r.id = it.run_id AND r.tenant_id = it.tenant_id
          WHERE it.tenant_id = %s
            AND it.article_row_id = a.id
            AND it.status IN ('pending', 'running')
            AND r.status IN ('queued', 'running')
      )
    ORDER BY a.next_retry_at ASC
    LIMIT %s
"""

# 复核到期条件：active 主记录且 processing_status ∈ ('success','deferred')
# （alias/deleted/unconfirmed 不复核，设计 §5.4）。deferred（WP10 P2：图片 VL 解析
# 不可用/失败，error_code=deferred_image_pending）随 24h 复核自动重试——错误信息
# 承诺「将自动重试」，且 p1 存量 deferred 文章凭 pipeline_version 变化在复核时重建。
# last_checked_at 由 service 以 SQL now() 写入（会话时区），故用 now() 同口径比较
_RECHECK_DUE_SQL = """
    SELECT a.id FROM bs_wechat_mp_articles a
    WHERE a.tenant_id = %s
      AND a.status = 'active'
      AND a.processing_status IN ('success', 'deferred')
      AND (a.last_checked_at IS NULL
           OR a.last_checked_at < now() - make_interval(hours => %s))
      AND NOT EXISTS (
          SELECT 1 FROM bs_wechat_mp_sync_items it
          JOIN bs_wechat_mp_sync_runs r
            ON r.id = it.run_id AND r.tenant_id = it.tenant_id
          WHERE it.tenant_id = %s
            AND it.article_row_id = a.id
            AND it.status IN ('pending', 'running')
            AND r.status IN ('queued', 'running')
      )
    ORDER BY a.last_checked_at NULLS FIRST
    LIMIT %s
"""


class WeChatMPScheduler:
    """公众号队列驱动 + 定时复核调度器（background runner 单实例运行）。"""

    def __init__(self, redis: Any = None, service: Any = None):
        # redis 注入点：默认用全局 redis_client（测试注入进程内替身）
        if redis is None:
            from src.core.redis_client import redis_client

            redis = redis_client
        self._redis = redis
        self._service = service  # WeChatMPSyncService；None 时 start() 懒加载
        self._stop_event = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._owner = uuid.uuid4().hex
        self._lock_key: Optional[str] = None
        self._redis_down_logged = False  # Redis 故障 ERROR 只记一次，恢复后复位
        # tick 入队后立即再领取一轮（不等 60s 兜底），降低重试/复核延迟
        self._claim_requested = False

    # ==================== 生命周期 ====================

    async def start(self) -> bool:
        """启动驱动（抢不到单副本锁或 Redis 不可用 → 不启动，返回 False）。

        返回 False 不抛异常：调用方（background runner）记录日志后继续，
        runner 整体不因 wechat_mp 调度失败而挂掉。
        """
        if self._task is not None and not self._task.done():
            logger.bind(module="wechat_mp").warning("wechat_mp 调度器已在运行，跳过重复启动")
            return True

        if not await asyncio.to_thread(self._redis.is_available):
            # 设计硬规则：Redis 不可用拒绝启动，不能无锁单副本降级
            logger.bind(module="wechat_mp").error(
                "后端日志：wechat_mp 调度器拒绝启动：Redis 不可用（不能无锁单副本驱动）"
            )
            return False

        self._owner = uuid.uuid4().hex
        self._lock_key = self._redis.make_key(DRIVER_LOCK_PREFIX)
        acquired = await asyncio.to_thread(
            self._redis.acquire_lock, self._lock_key, self._owner, DRIVER_LOCK_TTL_SECONDS
        )
        if not acquired:
            logger.bind(module="wechat_mp").info(
                "wechat_mp 调度器已在其他副本运行，本副本空转返回 owner={}", self._owner
            )
            self._lock_key = None
            return False

        if self._service is None:
            from src.wechat_mp.service import WeChatMPSyncService

            self._service = WeChatMPSyncService()

        self._stop_event.clear()
        self._claim_requested = False
        self._redis_down_logged = False
        self._task = asyncio.create_task(self._run_loop(), name="wechat-mp-scheduler")
        logger.bind(module="wechat_mp").info(
            "wechat_mp 调度器已启动 owner={} tick={}s 兜底={}s 并发={} 复核间隔={}h",
            self._owner, TICK_INTERVAL_SECONDS, IDLE_SCAN_SECONDS,
            TENANT_CONCURRENCY, RECHECK_INTERVAL_HOURS,
        )
        return True

    async def stop(self) -> None:
        """优雅停机：置停机事件 → 等驱动协程退出（宽限期后取消）→ 释放锁。

        宽限期内不强行中断进行中的 claim_and_run；超时取消后 run 由
        heartbeat/recover_stale_runs 机制标记可恢复状态，不丢账本。
        """
        self._stop_event.set()
        task, self._task = self._task, None
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(task, timeout=STOP_JOIN_TIMEOUT_SECONDS)
            except Exception:  # noqa: BLE001 超时/异常统一取消并吞掉退出错误
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        if self._lock_key:
            try:
                self._redis.release_lock(self._lock_key, self._owner)
            except Exception as e:  # noqa: BLE001 释放失败靠 TTL 过期兜底
                logger.bind(module="wechat_mp").warning(
                    "wechat_mp 调度器驱动锁释放失败（等待 TTL 过期）: {}", e
                )
            self._lock_key = None
        logger.bind(module="wechat_mp").info("wechat_mp 调度器已停止")

    # ==================== 驱动循环 ====================

    async def _run_loop(self) -> None:
        """驱动主循环：唤醒/兜底 → 领取轮 → stale 回收（5min）→ tick（30min）。"""
        next_recover_at = time.monotonic() + RECOVER_INTERVAL_SECONDS
        next_tick_at = time.monotonic() + TICK_INTERVAL_SECONDS
        logger.bind(module="wechat_mp").debug("wechat_mp 调度器驱动循环启动")
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
                        logger.bind(module="wechat_mp").warning(
                            "wechat_mp 调度器回收 stale run {}", recovered
                        )
                    next_recover_at = time.monotonic() + RECOVER_INTERVAL_SECONDS

                if now >= next_tick_at:
                    tick = await asyncio.to_thread(self._run_tick)
                    if (
                        tick.get("retry_enqueued")
                        or tick.get("recheck_enqueued")
                        or tick.get("scheduled_enqueued")
                        or tick.get("list_sync_enqueued")
                    ):
                        self._claim_requested = True  # 有新任务立即领取
                    next_tick_at = time.monotonic() + TICK_INTERVAL_SECONDS
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 单轮异常不终止驱动
                logger.opt(exception=True).error(
                    "后端日志：wechat_mp 调度器单轮异常（继续）: {}", e
                )
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=5)
                except asyncio.TimeoutError:
                    pass
        logger.bind(module="wechat_mp").debug("wechat_mp 调度器驱动循环退出")

    async def _ensure_driver_lock(self) -> bool:
        """续期单副本驱动锁；失锁尝试重取，Redis 不可用拒绝驱动（不无锁降级）。

        Redis IO 一律经 asyncio.to_thread（对齐本模块其余驱动循环 IO 纪律），
        避免 Redis 故障重试（ping/重连各有 socket 超时）阻塞 runner 事件循环。
        """
        if not await asyncio.to_thread(self._redis.is_available):
            if not self._redis_down_logged:
                logger.bind(module="wechat_mp").error(
                    "后端日志：wechat_mp 调度器暂停驱动：Redis 不可用（不无锁降级，恢复后自动继续）"
                )
                self._redis_down_logged = True
            return False
        if self._redis_down_logged:
            logger.bind(module="wechat_mp").info("wechat_mp 调度器：Redis 已恢复，继续驱动")
            self._redis_down_logged = False
        lock_value = self._owner
        renewed = await asyncio.to_thread(
            self._redis.renew_lock, self._lock_key, lock_value, DRIVER_LOCK_TTL_SECONDS
        )
        if renewed:
            return True
        # 锁过期/被误删（其他副本不应在持有期内出现该情况）：尝试重取
        return await asyncio.to_thread(
            self._redis.acquire_lock, self._lock_key, lock_value, ex=DRIVER_LOCK_TTL_SECONDS
        )

    async def _wait_for_wakeup(self) -> None:
        """非阻塞消费唤醒信号；无信号则等待 60s 兜底（或停机）。"""
        if await asyncio.to_thread(self._redis.lpop, self._redis.make_key(WAKEUP_KEY)) is not None:
            # 排空积压信号（多 pop 幂等无害），一轮领取覆盖全部 queued 租户
            while (
                await asyncio.to_thread(self._redis.lpop, self._redis.make_key(WAKEUP_KEY))
                is not None
            ):
                pass
            return
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=IDLE_SCAN_SECONDS)
        except asyncio.TimeoutError:
            pass  # 兜底周期到，执行领取轮

    # ==================== 领取轮 ====================

    def _list_queued_tenants(self) -> List[str]:
        """有 queued run 的租户列表（只读；DB 队列是唯一事实来源）。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT DISTINCT tenant_id FROM bs_wechat_mp_sync_runs
                WHERE status = 'queued'
                """
            )
            return [r["tenant_id"] for r in cursor.fetchall()]

    async def _claim_round(self) -> None:
        """一轮领取：逐租户 claim_and_run，跨租户有限并发，单租户异常隔离。"""
        try:
            tenants = await asyncio.to_thread(self._list_queued_tenants)
        except Exception as e:  # noqa: BLE001
            logger.opt(exception=True).error("后端日志：wechat_mp 队列租户发现失败: {}", e)
            return
        if not tenants:
            return
        semaphore = asyncio.Semaphore(TENANT_CONCURRENCY)

        async def _run_tenant(tenant_id: str) -> None:
            async with semaphore:
                try:
                    result = await self._service.claim_and_run(tenant_id)
                    if result.get("executed"):
                        logger.bind(module="wechat_mp").info(
                            "wechat_mp 调度器领取完成 tenant_id={} runs={} reason={}",
                            tenant_id, len(result.get("run_ids") or []), result.get("reason"),
                        )
                except Exception as e:  # noqa: BLE001 单租户异常不中断其他租户
                    logger.opt(exception=True).error(
                        "后端日志：wechat_mp 调度器租户领取异常 tenant_id={}: {}",
                        tenant_id, sanitize_error_info(str(e)),
                    )

        await asyncio.gather(*(_run_tenant(t) for t in tenants))

    # ==================== 30min tick：任务生成器 ====================

    def _run_tick(self) -> Dict[str, int]:
        """复核/重试/定时对账任务生成器（同步方法，调用方经 asyncio.to_thread 执行）。

        ① 重试到期入队（trigger_type='retry'，item action=NULL）
        ② 存活复核到期入队（trigger_type='recheck'，item action='check'）
        ③ 接口通道定时对账到期入队（trigger_type='scheduled'，无 items，WP9）
        只生成任务不改文章状态；单租户异常捕获不中断其他租户。
        """
        tenants = self._list_tick_tenants()
        result = {
            "tenants": len(tenants),
            "retry_enqueued": 0,
            "recheck_enqueued": 0,
            "scheduled_enqueued": 0,
            "list_sync_enqueued": 0,
        }
        for tenant_id in tenants:
            try:
                counts = self._tick_tenant(tenant_id)
            except Exception as e:  # noqa: BLE001 单租户异常不中断其他租户
                logger.opt(exception=True).error(
                    "后端日志：wechat_mp tick 租户任务生成失败 tenant_id={}: {}",
                    tenant_id, sanitize_error_info(str(e)),
                )
                continue
            result["retry_enqueued"] += counts["retry"]
            result["recheck_enqueued"] += counts["recheck"]
        try:
            result["scheduled_enqueued"] = self._tick_scheduled()
        except Exception as e:  # noqa: BLE001 生成器异常不中断其他生成器
            logger.opt(exception=True).error(
                "后端日志：wechat_mp tick 定时对账生成失败: {}",
                sanitize_error_info(str(e)),
            )
        try:
            result["list_sync_enqueued"] = self._tick_list_sync()
        except Exception as e:  # noqa: BLE001 生成器异常不中断其他生成器
            logger.opt(exception=True).error(
                "后端日志：wechat_mp tick 清单对账生成失败: {}",
                sanitize_error_info(str(e)),
            )
        if (
            result["retry_enqueued"]
            or result["recheck_enqueued"]
            or result["scheduled_enqueued"]
            or result["list_sync_enqueued"]
        ):
            logger.bind(module="wechat_mp").info(
                "wechat_mp tick 任务生成 retry={} recheck={} scheduled={} list_sync={} tenants={}",
                result["retry_enqueued"], result["recheck_enqueued"],
                result["scheduled_enqueued"], result["list_sync_enqueued"],
                result["tenants"],
            )
        return result

    def _list_tick_tenants(self) -> List[str]:
        """存在到期任务的文章租户发现（只读跨租户 SELECT DISTINCT，仅驱动使用）。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT DISTINCT tenant_id FROM bs_wechat_mp_articles
                WHERE status = 'active'
                  AND (
                    (processing_status IN ('sync_failed', 'pending')
                     AND next_retry_at IS NOT NULL
                     AND next_retry_at <= (now() AT TIME ZONE 'UTC'))
                    OR
                    (processing_status IN ('success', 'deferred')
                     AND (last_checked_at IS NULL
                          OR last_checked_at < now() - make_interval(hours => %s)))
                  )
                """,
                (RECHECK_INTERVAL_HOURS,),
            )
            return [r["tenant_id"] for r in cursor.fetchall()]

    def _tick_tenant(self, tenant_id: str) -> Dict[str, int]:
        """单租户 tick：查到期文章 → 同事务建 run + pending items（幂等）。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    _RETRY_DUE_SQL, (tenant_id, tenant_id, RETRY_TICK_LIMIT)
                )
                retry_ids = [r["id"] for r in cursor.fetchall()]
                retry_count = self._create_batch_run(
                    cursor, tenant_id, retry_ids, trigger_type="retry", action=None
                )

                cursor.execute(
                    _RECHECK_DUE_SQL,
                    (tenant_id, RECHECK_INTERVAL_HOURS, tenant_id, RECHECK_TICK_LIMIT),
                )
                recheck_ids = [r["id"] for r in cursor.fetchall()]
                recheck_count = self._create_batch_run(
                    cursor, tenant_id, recheck_ids, trigger_type="recheck", action="check"
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return {"retry": retry_count, "recheck": recheck_count}

    @staticmethod
    def _create_batch_run(
        cursor,
        tenant_id: str,
        article_row_ids: List[int],
        *,
        trigger_type: str,
        action: Optional[str],
    ) -> int:
        """同事务建一个 queued run + 每篇一条 pending item（生成与入队同事务）。"""
        if not article_row_ids:
            return 0
        cursor.execute(
            """
            INSERT INTO bs_wechat_mp_sync_runs
                (tenant_id, config_id, user_id, trigger_type, status, total_count)
            VALUES (%s, NULL, NULL, %s, 'queued', %s)
            RETURNING id
            """,
            (tenant_id, trigger_type, len(article_row_ids)),
        )
        run_id = cursor.fetchone()["id"]
        for article_row_id in article_row_ids:
            cursor.execute(
                """
                INSERT INTO bs_wechat_mp_sync_items
                    (tenant_id, user_id, run_id, article_row_id, action, status)
                VALUES (%s, NULL, %s, %s, %s, 'pending')
                """,
                (tenant_id, run_id, article_row_id, action),
            )
        logger.bind(module="wechat_mp").info(
            "wechat_mp tick 任务入队 tenant_id={} run_id={} trigger={} count={} action={}",
            tenant_id, run_id, trigger_type, len(article_row_ids), action or "new",
        )
        return len(article_row_ids)

    # ==================== 30min tick ③：接口通道定时对账（WP9） ====================

    @staticmethod
    def _parse_sync_interval_hours(raw: Any) -> float:
        """sync_interval_hours 解析容错：int/float/数字字符串均可。

        非法/<=0/NaN/inf/超大值 → 回落默认 6（NaN 比较恒 False、inf/超大值会让
        timedelta(hours=...) 抛 OverflowError/ValueError——单个配置的坏字段不能
        中断整个第③生成器，殃及其他配置的定时对账）。
        """
        try:
            value = float(raw)
        except (TypeError, ValueError, OverflowError):
            return SCHEDULED_DEFAULT_INTERVAL_HOURS
        if not (value > 0):  # NaN/0/负数统一回落（NaN 的 >0 比较恒 False）
            return SCHEDULED_DEFAULT_INTERVAL_HOURS
        return min(value, SCHEDULED_MAX_INTERVAL_HOURS)

    def _list_wechat_mp_configs(self) -> List[Dict[str, Any]]:
        """扫描接口通道可用的 wechat_mp 配置（verified 列=1 + config 内凭据齐备）。

        config 是 TEXT 列：应用层 json.loads 后过滤（避免 SQL 内 ::jsonb 转换失败
        与 enabled JSON 布尔->> 文本比较的歧义）；secret 存的是 Fernet 密文，
        「非空」判断对密文同样成立，明文只在 worker 侧经解密读取。
        """
        import json as _json

        configs: List[Dict[str, Any]] = []
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT config_id, tenant_id, config FROM tenant_channel_configs
                WHERE channel_type = 'wechat_mp' AND verified = 1
                ORDER BY id ASC
                """
            )
            rows = cursor.fetchall()
            for row in rows:
                try:
                    cfg = _json.loads(row["config"]) if row["config"] else {}
                except (TypeError, ValueError):
                    logger.bind(module="wechat_mp").warning(
                        "wechat_mp scheduled 扫描跳过：config 非法 JSON config_id={}",
                        row["config_id"],
                    )
                    continue
                if not isinstance(cfg, dict) or cfg.get("enabled") is not True:
                    continue
                appid = str(cfg.get("appid") or "").strip()
                secret = str(cfg.get("secret") or "").strip()
                if not appid or not secret:
                    continue  # 仅回调+手动粘贴通道：不参与接口通道对账
                configs.append(
                    {
                        "config_id": row["config_id"],
                        "tenant_id": row["tenant_id"],
                        "interval_hours": self._parse_sync_interval_hours(
                            cfg.get("sync_interval_hours")
                        ),
                    }
                )
        return configs

    def _tick_scheduled(self) -> int:
        """第③生成器：接口通道定时对账到期配置 → 同事务建 queued run（无 items）。

        到期判定：「最近尝试时间」= 最近一条 trigger_type='scheduled' run 的
        created_at（失败 run 终态后随周期自然重试）；已有 queued/running 的
        scheduled run 时跳过（防队列堆积）。worker 对账完成后按 diff 建 items。
        """
        configs = self._list_wechat_mp_configs()
        if not configs:
            return 0
        config_ids = [c["config_id"] for c in configs]
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                # now()::timestamp 与 created_at（TIMESTAMP 列，会话时区 naive）同口径
                cursor.execute("SELECT now()::timestamp AS db_now")
                db_now = cursor.fetchone()["db_now"]
                cursor.execute(
                    """
                    SELECT config_id, max(created_at) AS last_created
                    FROM bs_wechat_mp_sync_runs
                    WHERE trigger_type = 'scheduled' AND config_id = ANY(%s)
                    GROUP BY config_id
                    """,
                    (config_ids,),
                )
                last_created = {r["config_id"]: r["last_created"] for r in cursor.fetchall()}
                cursor.execute(
                    """
                    SELECT DISTINCT config_id FROM bs_wechat_mp_sync_runs
                    WHERE trigger_type = 'scheduled'
                      AND status IN ('queued', 'running')
                      AND config_id = ANY(%s)
                    """,
                    (config_ids,),
                )
                active_configs = {r["config_id"] for r in cursor.fetchall()}

                enqueued = 0
                for cfg in configs:
                    if enqueued >= SCHEDULED_TICK_LIMIT:
                        logger.bind(module="wechat_mp").warning(
                            "wechat_mp scheduled 单 tick 生成达上限 {}，余量留到下轮",
                            SCHEDULED_TICK_LIMIT,
                        )
                        break
                    if cfg["config_id"] in active_configs:
                        continue  # 已有待执行/执行中对账 run，不堆积
                    last = last_created.get(cfg["config_id"])
                    if last is not None:
                        elapsed = db_now - last
                        threshold = timedelta(hours=cfg["interval_hours"])
                        if elapsed < threshold:
                            continue
                    cursor.execute(
                        """
                        INSERT INTO bs_wechat_mp_sync_runs
                            (tenant_id, config_id, user_id, trigger_type, status, total_count)
                        VALUES (%s, %s, NULL, 'scheduled', 'queued', 0)
                        RETURNING id
                        """,
                        (cfg["tenant_id"], cfg["config_id"]),
                    )
                    run_id = cursor.fetchone()["id"]
                    enqueued += 1
                    logger.bind(module="wechat_mp").info(
                        "wechat_mp scheduled 对账入队 tenant_id={} config_id={} run_id={} "
                        "interval={}h",
                        cfg["tenant_id"], cfg["config_id"], run_id, cfg["interval_hours"],
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return enqueued

    # ==================== 30min tick ④：清单源定时对账（WP13） ====================

    def _list_list_sync_configs(self) -> List[Dict[str, Any]]:
        """扫描清单源可用的 wechat_mp 配置（扫码绑定未解绑且状态 active/expiring）。

        - list_session_token 非空 = 已扫码绑定（config TEXT 列，应用层 json.loads 过滤）
        - list_sync_status 仅认 active/expiring：expired/account_error 停止自动拉取
          （重新扫码绑定置回 active 后自然恢复）；缺省视为 active（兼容旧数据）
        - enabled 不做门槛：该开关语义是「回调接收」，与清单拉取相互独立
        """
        import json as _json

        configs: List[Dict[str, Any]] = []
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT config_id, tenant_id, config FROM tenant_channel_configs
                WHERE channel_type = 'wechat_mp'
                ORDER BY id ASC
                """
            )
            rows = cursor.fetchall()
        for row in rows:
            try:
                cfg = _json.loads(row["config"]) if row["config"] else {}
            except (TypeError, ValueError):
                logger.bind(module="wechat_mp").warning(
                    "wechat_mp list_sync 扫描跳过：config 非法 JSON config_id={}",
                    row["config_id"],
                )
                continue
            if not isinstance(cfg, dict):
                continue
            if not str(cfg.get("list_session_token") or "").strip():
                continue  # 未扫码绑定
            status = str(cfg.get("list_sync_status") or "active")
            if status not in ("active", "expiring"):
                continue
            configs.append(
                {
                    "config_id": row["config_id"],
                    "tenant_id": row["tenant_id"],
                    # 清单源默认 1h（设计 §3.3）；解析容错复用 ③ 的口径，仅默认值不同
                    "interval_hours": min(
                        self._parse_sync_interval_hours(cfg.get("sync_interval_hours"))
                        if cfg.get("sync_interval_hours") is not None
                        else LIST_SYNC_DEFAULT_INTERVAL_HOURS,
                        SCHEDULED_MAX_INTERVAL_HOURS,
                    ),
                }
            )
        return configs

    def _tick_list_sync(self) -> int:
        """第④生成器：清单源同步到期配置 → 同事务建 queued run（无 items，config_id 落值）。

        到期判定与防堆积同第③生成器（最近一条 list_sync run 的 created_at；
        已有 queued/running 的 list_sync run 跳过）。worker 对账清单后按 diff 建
        items（auto_all 直接入队 / manual 落 pending_manual，见 service）。
        """
        configs = self._list_list_sync_configs()
        if not configs:
            return 0
        config_ids = [c["config_id"] for c in configs]
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("SELECT now()::timestamp AS db_now")
                db_now = cursor.fetchone()["db_now"]
                cursor.execute(
                    """
                    SELECT config_id, max(created_at) AS last_created
                    FROM bs_wechat_mp_sync_runs
                    WHERE trigger_type = 'list_sync' AND config_id = ANY(%s)
                    GROUP BY config_id
                    """,
                    (config_ids,),
                )
                last_created = {r["config_id"]: r["last_created"] for r in cursor.fetchall()}
                cursor.execute(
                    """
                    SELECT DISTINCT config_id FROM bs_wechat_mp_sync_runs
                    WHERE trigger_type = 'list_sync'
                      AND status IN ('queued', 'running')
                      AND config_id = ANY(%s)
                    """,
                    (config_ids,),
                )
                active_configs = {r["config_id"] for r in cursor.fetchall()}

                enqueued = 0
                for cfg in configs:
                    if enqueued >= LIST_SYNC_TICK_LIMIT:
                        logger.bind(module="wechat_mp").warning(
                            "wechat_mp list_sync 单 tick 生成达上限 {}，余量留到下轮",
                            LIST_SYNC_TICK_LIMIT,
                        )
                        break
                    if cfg["config_id"] in active_configs:
                        continue  # 已有待执行/执行中清单对账 run，不堆积
                    last = last_created.get(cfg["config_id"])
                    if last is not None:
                        elapsed = db_now - last
                        threshold = timedelta(hours=cfg["interval_hours"])
                        if elapsed < threshold:
                            continue
                    cursor.execute(
                        """
                        INSERT INTO bs_wechat_mp_sync_runs
                            (tenant_id, config_id, user_id, trigger_type, status, total_count)
                        VALUES (%s, %s, NULL, 'list_sync', 'queued', 0)
                        RETURNING id
                        """,
                        (cfg["tenant_id"], cfg["config_id"]),
                    )
                    run_id = cursor.fetchone()["id"]
                    enqueued += 1
                    logger.bind(module="wechat_mp").info(
                        "wechat_mp list_sync 对账入队 tenant_id={} config_id={} run_id={} "
                        "interval={}h",
                        cfg["tenant_id"], cfg["config_id"], run_id, cfg["interval_hours"],
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return enqueued
