"""浏览器 Run 状态机、owner lease 与执行器生命周期。"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
import weakref
from enum import Enum

from loguru import logger

from src.config.settings import settings

from .executor.base import BrowserExecutor
from .executor.fenced import FencedBrowserExecutor
from .executor.models import BrowserRunSpec
from .router import BrowserRouter
from .run_db import BrowserRunDB
from .run_store import BrowserRunStore, RunRecord, create_run_store


_ACTIVE_MANAGERS: weakref.WeakSet["BrowserRunManager"] = weakref.WeakSet()


class RunState(str, Enum):
    CREATED = "CREATED"
    ROUTING = "ROUTING"
    STARTING = "STARTING"
    RUNNING_AGENT = "RUNNING_AGENT"
    WAITING_HUMAN = "WAITING_HUMAN"
    RUNNING_HUMAN = "RUNNING_HUMAN"
    RESUMING = "RESUMING"
    ESCALATING_CLIENT = "ESCALATING_CLIENT"
    WAITING_CLIENT = "WAITING_CLIENT"
    CANCELLING = "CANCELLING"
    FINALIZING = "FINALIZING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


TERMINAL_STATES = {RunState.SUCCEEDED, RunState.FAILED, RunState.TIMED_OUT, RunState.EXPIRED, RunState.CANCELLED}
_TRANSITIONS: dict[RunState, set[RunState]] = {
    RunState.CREATED: {RunState.ROUTING, RunState.CANCELLING, RunState.FINALIZING},
    RunState.ROUTING: {RunState.STARTING, RunState.CANCELLING, RunState.FINALIZING},
    RunState.STARTING: {RunState.RUNNING_AGENT, RunState.CANCELLING, RunState.FINALIZING},
    RunState.RUNNING_AGENT: {RunState.WAITING_HUMAN, RunState.ESCALATING_CLIENT, RunState.CANCELLING, RunState.FINALIZING},
    RunState.WAITING_HUMAN: {RunState.RUNNING_HUMAN, RunState.CANCELLING, RunState.FINALIZING},
    RunState.RUNNING_HUMAN: {RunState.RESUMING, RunState.CANCELLING, RunState.FINALIZING},
    RunState.RESUMING: {RunState.RUNNING_AGENT, RunState.CANCELLING, RunState.FINALIZING},
    RunState.ESCALATING_CLIENT: {RunState.FINALIZING},
    RunState.WAITING_CLIENT: {RunState.STARTING, RunState.CANCELLING, RunState.FINALIZING},
    RunState.CANCELLING: {RunState.FINALIZING},
    RunState.FINALIZING: set(TERMINAL_STATES),
}


class InvalidRunTransition(RuntimeError):
    pass


class BrowserRunManager:
    def __init__(
        self, store: BrowserRunStore | None = None, router: BrowserRouter | None = None,
        owner_id: str | None = None, run_db: BrowserRunDB | None = None,
    ) -> None:
        self.store = store or create_run_store()
        self.router = router or BrowserRouter()
        self.owner_id = owner_id or f"srv_{os.getpid()}_{uuid.uuid4().hex}"
        self.run_db = run_db or BrowserRunDB()
        self._executors: dict[tuple[str, str], BrowserExecutor] = {}
        self._renew_tasks: dict[tuple[str, str], asyncio.Task] = {}
        self._finalize_locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._owner_tokens: dict[tuple[str, str], str] = {}
        _ACTIVE_MANAGERS.add(self)

    @property
    def degraded(self) -> bool:
        return not self.store.distributed

    async def create(self, tenant_id: str, user_id: str, session_id: str, execution_target: str = "server") -> RunRecord:
        if not tenant_id or not user_id:
            raise ValueError("tenant_id 和 user_id 必填")
        if self.degraded and execution_target != "server":
            raise RuntimeError("REDIS_REQUIRED")
        run_id = f"br_{uuid.uuid4().hex}"
        record = RunRecord(
            tenant_id=tenant_id, user_id=user_id, run_id=run_id, session_id=session_id,
            state=RunState.CREATED.value, execution_target=execution_target,
            degraded_single_request=self.degraded,
        )
        if not await self.store.create(record):
            raise RuntimeError("RUN_ID_CONFLICT")
        try:
            await self.run_db.create_run(record.model_dump(mode="json"))
        except Exception as exc:
            # 审计库故障不得阻止单请求 server run；终态仍由 finally 回收。
            logger.warning("browser run 审计创建失败: type={}", type(exc).__name__)
        return record

    async def start(self, record: RunRecord) -> BrowserExecutor:
        await self.transition(record.tenant_id, record.run_id, RunState.ROUTING)
        key = (record.tenant_id, record.run_id)
        owner_acquired = False
        owner_conflict = False
        try:
            executor = self.router.create_executor(record.execution_target)
            ttl = int(getattr(settings.tools.browser, "owner_lease_ttl", 30))
            owner_token = await self.store.acquire_owner(
                record.tenant_id, record.run_id, self.owner_id, ttl
            )
            if not owner_token:
                owner_conflict = True
                raise RuntimeError("RUN_OWNER_CONFLICT")
            owner_acquired = True
            self._owner_tokens[key] = str(owner_token)
            executor = FencedBrowserExecutor(
                executor, self.store, record.tenant_id, record.run_id, str(owner_token)
            )
            self._executors[key] = executor
            if self.store.distributed:
                self._renew_tasks[key] = asyncio.create_task(
                    self._renew_owner(record.tenant_id, record.run_id, str(owner_token))
                )
            await self.transition(record.tenant_id, record.run_id, RunState.STARTING)
            requested_headless = getattr(self, "headless_override", None)
            effective_headless = (
                settings.tools.browser.headless
                if requested_headless is None
                else bool(requested_headless)
            )
            spec = BrowserRunSpec(
                run_id=record.run_id, tenant_id=record.tenant_id, user_id=record.user_id,
                session_id=record.session_id, execution_target=record.execution_target,
                headless=effective_headless,
                viewport_width=min(settings.tools.browser.viewport_width, 1280),
                viewport_height=min(settings.tools.browser.viewport_height, 720),
            )
            result = await executor.start(spec)
            if result.status.value == "error":
                raise RuntimeError(result.error_code or "BROWSER_START_FAILED")
            fence_error = await self.store.fence_owner(
                record.tenant_id, record.run_id, str(owner_token), time.time()
            )
            if fence_error is not None:
                raise RuntimeError(fence_error)
            await self.transition(record.tenant_id, record.run_id, RunState.RUNNING_AGENT)
            return executor
        except BaseException:
            # 未取得 owner 时绝不能替其他 worker 收口；router 创建失败时尚无 owner，
            # 但本 run 仍由当前调用创建，可安全写入失败终态。
            if not owner_conflict:
                try:
                    await asyncio.shield(
                        self.finalize(
                            record.tenant_id, record.run_id, RunState.FAILED, "start_failed"
                        )
                    )
                except Exception as exc:
                    logger.warning("browser start 失败收口异常: type={}", type(exc).__name__)
            raise

    async def transition(self, tenant_id: str, run_id: str, new_state: RunState) -> RunRecord:
        record = await self.store.get(tenant_id, run_id)
        if record is None:
            raise KeyError("RUN_NOT_FOUND")
        current = RunState(record.state)
        if new_state not in _TRANSITIONS.get(current, set()):
            raise InvalidRunTransition(f"{current.value}->{new_state.value}")
        updated = await self.store.compare_state(tenant_id, run_id, {current.value}, new_state.value)
        if updated is None:
            raise InvalidRunTransition("RUN_STATE_RACE")
        try:
            await self.run_db.update_run_state(tenant_id, run_id, new_state.value)
        except Exception as exc:
            logger.warning("browser run 审计更新失败: type={}", type(exc).__name__)
        return updated

    async def request_cancel(self, tenant_id: str, user_id: str, run_id: str) -> bool:
        record = await self.store.get(tenant_id, run_id)
        if record is None or record.user_id != user_id:
            return False
        if RunState(record.state) in TERMINAL_STATES:
            return True
        await self.store.request_cancel(tenant_id, run_id)
        try:
            await self.transition(tenant_id, run_id, RunState.CANCELLING)
        except InvalidRunTransition:
            pass
        executor = self._executors.get((tenant_id, run_id))
        if executor:
            await executor.close("cancelled")
            await self.finalize(tenant_id, run_id, RunState.CANCELLED, "cancelled")
        # 其他 worker 的 owner 会在续租轮询中观察 cancel_requested 并回收；
        # 当前 worker 不能把仍在执行的远端进程提前伪标为终态。
        return True

    async def finalize(self, tenant_id: str, run_id: str, terminal: RunState, reason: str) -> RunRecord:
        if terminal not in TERMINAL_STATES:
            raise ValueError("finalize 目标必须是终态")
        key = (tenant_id, run_id)
        lock = self._finalize_locks.setdefault(key, asyncio.Lock())
        async with lock:
            record = await self.store.get(tenant_id, run_id)
            if record is None:
                await self._close_local_executor(key, reason)
                await self._stop_renew_task(key)
                await self._release_owner(key)
                raise KeyError("RUN_NOT_FOUND")
            state = RunState(record.state)
            if state in TERMINAL_STATES:
                await self._close_local_executor(key, reason)
                await self._stop_renew_task(key)
                await self._release_owner(key)
                return record
            owner_token = self._owner_tokens.get(key)
            if owner_token is not None and record.owner_token != owner_token:
                # 本 manager 已被新 epoch 取代：只回收自己的本地进程，绝不能
                # transition/finalize 新 owner 的 run，也不能释放新 owner lease。
                await self._close_local_executor(key, "owner_lost")
                await self._stop_renew_task(key)
                self._owner_tokens.pop(key, None)
                raise RuntimeError("OWNER_LEASE_LOST")
            if state != RunState.FINALIZING:
                await self.transition(tenant_id, run_id, RunState.FINALIZING)
            await self._close_local_executor(key, reason)
            updated = await self.store.compare_state(tenant_id, run_id, {RunState.FINALIZING.value}, terminal.value)
            if updated is None:
                updated = await self.store.get(tenant_id, run_id)
                if updated is None:
                    raise KeyError("RUN_NOT_FOUND")
            try:
                await self.run_db.update_run_state(
                    tenant_id, run_id, terminal.value, close_reason=reason
                )
            except Exception as exc:
                logger.warning("browser run 终态审计失败: type={}", type(exc).__name__)
            await self._stop_renew_task(key)
            await self._release_owner(key)
            return updated

    async def _release_owner(self, key: tuple[str, str]) -> None:
        owner_token = self._owner_tokens.pop(key, None)
        if owner_token:
            await self.store.release_owner(key[0], key[1], owner_token)

    async def _close_local_executor(self, key: tuple[str, str], reason: str) -> None:
        executor = self._executors.pop(key, None)
        if executor:
            try:
                await asyncio.shield(executor.close(reason))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("browser executor 关闭失败: type={}", type(exc).__name__)

    async def _stop_renew_task(self, key: tuple[str, str]) -> None:
        renew = self._renew_tasks.pop(key, None)
        if renew and renew is not asyncio.current_task():
            renew.cancel()
            await asyncio.gather(renew, return_exceptions=True)

    async def _renew_owner(self, tenant_id: str, run_id: str, owner_token: str) -> None:
        interval = float(getattr(settings.tools.browser, "owner_renew_interval", 10.0))
        ttl = int(getattr(settings.tools.browser, "owner_lease_ttl", 30))
        try:
            while True:
                await asyncio.sleep(interval)
                record = await self.store.get(tenant_id, run_id)
                if record is None:
                    await self._close_local_executor((tenant_id, run_id), "owner_state_lost")
                    return
                if record.owner_token != owner_token:
                    await self._close_local_executor((tenant_id, run_id), "owner_lost")
                    self._owner_tokens.pop((tenant_id, run_id), None)
                    return
                if record.cancel_requested:
                    executor = self._executors.get((tenant_id, run_id))
                    if executor:
                        await executor.close("cancelled")
                    try:
                        if RunState(record.state) != RunState.CANCELLING:
                            await self.transition(tenant_id, run_id, RunState.CANCELLING)
                        await self.finalize(tenant_id, run_id, RunState.CANCELLED, "cancelled")
                    except (InvalidRunTransition, KeyError):
                        pass
                    return
                if not await self.store.renew_owner(tenant_id, run_id, owner_token, ttl):
                    await self._close_local_executor((tenant_id, run_id), "owner_lost")
                    return
        except asyncio.CancelledError:
            return

    async def close_all(self, reason: str = "shutdown") -> None:
        for tenant_id, run_id in list(self._executors):
            try:
                await self.finalize(tenant_id, run_id, RunState.FAILED, reason)
            except Exception as exc:
                logger.warning("browser run shutdown 失败: type={}", type(exc).__name__)


async def close_all_active_browser_managers(reason: str = "shutdown") -> None:
    """关闭本进程所有请求级 manager 拥有的 executor。"""
    managers = list(_ACTIVE_MANAGERS)
    if managers:
        await asyncio.gather(
            *(manager.close_all(reason) for manager in managers),
            return_exceptions=True,
        )
