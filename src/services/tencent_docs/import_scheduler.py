"""腾讯文档导入批次调度器（background runner 单副本运行）

- 单副本 Redis 锁：抢不到（可能为强杀残留幽灵锁）→ 仍启动驱动任务，循环内每 30s
  重试接管；Redis 不可用拒绝启动（对齐 wechat_mp 调度器硬规则），runner 整体不因此挂掉。
- 驱动循环 10s：领 queued 批次置 running -> 领到期 item（跨批次）并发执行
  （Semaphore 2）。同批次 item 的腾讯文档调用经 import_service.throttle_tdoc_call
  全局节流（0.5s 间隔）。
- 批次收敛：每个 item 执行完 refresh_progress 滚动汇总，items 全终态时批次
  置 succeeded/partial/failed。
- DB 队列是唯一事实来源：执行中进程崩溃，item 停留 running 会被下一轮…
  不会自动回收（running 卡死场景低频，重试入口允许手动重置 failed；
  running 卡死批次可通过重新创建批次兜底，Phase 1 不做 stale 回收）。
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Dict, List, Optional

from loguru import logger

from src.db.tdoc_import import TdocImportBatchDB, TdocImportItemDB

DRIVER_LOCK_PREFIX = "tdoc_import_scheduler_lock"
DRIVER_LOCK_TTL_SECONDS = 300
LOCK_RETRY_SECONDS = 30
SCAN_INTERVAL_SECONDS = 10  # 有用户在等进度，轮询要短
STOP_JOIN_TIMEOUT_SECONDS = 30  # 宽限期覆盖单篇导出轮询（最长 120s），超时取消由 DB 状态兜底
ITEM_CONCURRENCY = 2
CLAIM_BATCH_SIZE = 10


class TdocImportScheduler:
    """腾讯文档导入批次调度器。"""

    def __init__(self, redis: Any = None, execute_item: Any = None):
        if redis is None:
            from src.core.redis_client import redis_client

            redis = redis_client
        self._redis = redis
        self._execute_item = execute_item  # 注入点（测试替身）；None 时懒加载真实实现
        self._stop_event = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._owner = uuid.uuid4().hex
        self._lock_key: Optional[str] = None

    async def start(self) -> bool:
        """启动驱动；锁被占也启动驱动任务（循环内重试接管），Redis 不可用拒绝启动。"""
        if self._task is not None and not self._task.done():
            logger.warning("腾讯文档导入调度器已在运行，跳过重复启动")
            return True

        if not await asyncio.to_thread(self._redis.is_available):
            logger.error("后端日志：腾讯文档导入调度器拒绝启动：Redis 不可用（不能无锁单副本驱动）")
            return False

        self._lock_key = self._redis.make_key(DRIVER_LOCK_PREFIX)
        acquired = await asyncio.to_thread(
            self._redis.acquire_lock, self._lock_key, self._owner, DRIVER_LOCK_TTL_SECONDS
        )
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop(), name="tdoc-import-scheduler")
        if acquired:
            logger.info(
                "腾讯文档导入调度器已启动 owner={} scan={}s 并发={}",
                self._owner, SCAN_INTERVAL_SECONDS, ITEM_CONCURRENCY,
            )
        else:
            # 锁被占：可能为强杀残留的幽灵锁（TTL 过期后可接管）。不再放弃启动，
            # 保留锁 key 交给 _run_loop 内 _ensure_driver_lock 的失锁自愈路径重试接管
            logger.info(
                "腾讯文档导入调度器锁被占（可能为强杀残留幽灵锁），驱动任务已启动，"
                "循环内每 {}s 重试接管 owner={}",
                LOCK_RETRY_SECONDS, self._owner,
            )
        return True

    async def stop(self) -> None:
        self._stop_event.set()
        task, self._task = self._task, None
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(task, timeout=STOP_JOIN_TIMEOUT_SECONDS)
            except Exception:  # noqa: BLE001
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        if self._lock_key:
            try:
                self._redis.release_lock(self._lock_key, self._owner)
            except Exception as e:  # noqa: BLE001 释放失败靠 TTL 过期兜底
                logger.warning("腾讯文档导入调度器锁释放失败（等待 TTL 过期）: {}", e)
            self._lock_key = None
        logger.info("腾讯文档导入调度器已停止")

    async def _run_loop(self) -> None:
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

                await self._scan_round()
            except Exception as e:
                logger.opt(exception=True).error(f"腾讯文档导入调度器轮次异常: {e}")

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=SCAN_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                pass
        logger.info("腾讯文档导入调度器驱动循环退出")

    async def _ensure_driver_lock(self) -> bool:
        """续期驱动锁；失锁尝试重取，Redis 不可用暂停驱动（不无锁降级）。"""
        if not await asyncio.to_thread(self._redis.is_available):
            logger.warning("腾讯文档导入调度器暂停驱动：Redis 不可用（恢复后自动继续）")
            return False
        renewed = await asyncio.to_thread(
            self._redis.renew_lock, self._lock_key, self._owner, DRIVER_LOCK_TTL_SECONDS
        )
        if renewed:
            return True
        # 锁过期/被误删：尝试重取
        return await asyncio.to_thread(
            self._redis.acquire_lock, self._lock_key, self._owner, ex=DRIVER_LOCK_TTL_SECONDS
        )

    async def _scan_round(self) -> None:
        """一轮领取：queued 批次置 running -> 领到期 item -> 并发执行。"""
        await asyncio.to_thread(TdocImportBatchDB.claim_queued, CLAIM_BATCH_SIZE)
        items: List[Dict[str, Any]] = await asyncio.to_thread(
            TdocImportItemDB.claim_due, CLAIM_BATCH_SIZE
        )
        if not items:
            return

        if self._execute_item is None:
            from .import_service import execute_item as _real_execute

            self._execute_item = _real_execute

        # 补齐批次 user_id（items 表无 user_id 列）
        batch_ids = {i["batch_id"] for i in items}
        batch_user: Dict[str, str] = {}
        for batch_id in batch_ids:
            with_batch = await asyncio.to_thread(self._get_batch_user, batch_id)
            batch_user[batch_id] = with_batch
        for item in items:
            item["user_id"] = batch_user.get(item["batch_id"], "")

        semaphore = asyncio.Semaphore(ITEM_CONCURRENCY)

        async def _run_one(it: Dict[str, Any]) -> None:
            async with semaphore:
                # item 执行内部消化全部异常并滚动汇总批次进度
                await self._execute_item(it)

        await asyncio.gather(*(_run_one(i) for i in items))

    @staticmethod
    def _get_batch_user(batch_id: str) -> str:
        batch = TdocImportBatchDB.get(batch_id)  # 后台驱动无租户上下文，按 batch_id 直查
        return (batch or {}).get("user_id") or ""
