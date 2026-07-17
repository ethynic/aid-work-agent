"""抢占 owner lease 已过期的 browser run。"""

from __future__ import annotations

import asyncio
import time
import uuid

from loguru import logger

from src.config.settings import settings

from .run_manager import BrowserRunManager, RunState, TERMINAL_STATES


class BrowserRunReaper:
    def __init__(self, manager: BrowserRunManager) -> None:
        self.manager = manager
        self.reaper_id = f"reaper_{uuid.uuid4().hex}"
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self.manager.store.distributed and self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def reap_once(self) -> int:
        claimed = 0
        ttl = int(getattr(settings.tools.browser, "owner_lease_ttl", 30))
        for record in await self.manager.store.list_expired(time.time()):
            if RunState(record.state) in TERMINAL_STATES:
                continue
            claim_token = await self.manager.store.acquire_owner(
                record.tenant_id, record.run_id, self.reaper_id, ttl
            )
            if not claim_token:
                continue
            try:
                current = await self.manager.store.get(record.tenant_id, record.run_id)
                if (
                    current is None or current.owner_id != self.reaper_id
                    or current.owner_token != claim_token
                ):
                    continue
                if RunState(current.state) in TERMINAL_STATES:
                    continue
                if RunState(current.state) != RunState.FINALIZING:
                    updated = await self.manager.store.compare_state(
                        record.tenant_id, record.run_id, {current.state}, RunState.FINALIZING.value
                    )
                    if updated is None:
                        continue
                updated = await self.manager.store.compare_state(
                    record.tenant_id, record.run_id, {RunState.FINALIZING.value}, RunState.EXPIRED.value
                )
                if updated is None:
                    continue
                try:
                    await self.manager.run_db.update_run_state(
                        record.tenant_id,
                        record.run_id,
                        RunState.EXPIRED.value,
                        close_reason="owner_lease_expired",
                    )
                except Exception as exc:
                    logger.warning("browser reaper 审计更新失败: type={}", type(exc).__name__)
                claimed += 1
            finally:
                await self.manager.store.release_owner(
                    record.tenant_id, record.run_id, str(claim_token)
                )
        return claimed

    async def _loop(self) -> None:
        interval = float(getattr(settings.tools.browser, "reaper_interval", 15.0))
        while True:
            try:
                await asyncio.sleep(interval)
                await self.reap_once()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning("browser reaper 异常: type={}", type(exc).__name__)
