"""人工完成后的幂等 Browser/Agent continuation 协调器。"""

from __future__ import annotations

import os
import asyncio
import time
import uuid
from typing import Awaitable, Callable

from loguru import logger

from .human_control import HumanControlCoordinator, get_owned_runtime, unregister_owned_runtime
from .resume_store import AssistanceRecord, ResumeStore
from .run_manager import RunState


ContinuationCallback = Callable[[AssistanceRecord, dict], Awaitable[None]]
_CONTINUATION_CALLBACK: ContinuationCallback | None = None


def configure_continuation_callback(callback: ContinuationCallback | None) -> None:
    """由应用入口注入 Agent Runtime 接线，browser 模块不反向依赖 main。"""
    global _CONTINUATION_CALLBACK
    _CONTINUATION_CALLBACK = callback


class BrowserResumeWorker:
    """持久 Stream fan-out worker；重启从头补读，owner + claim 保证只执行一次。"""

    def __init__(
        self,
        store: ResumeStore | None = None,
        coordinator_factory: Callable[[], "AgentResumeCoordinator"] | None = None,
    ) -> None:
        self.store = store or ResumeStore()
        self.coordinator_factory = coordinator_factory or (
            lambda: AgentResumeCoordinator(store=self.store)
        )
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _loop(self) -> None:
        last_id = "0-0"
        while True:
            try:
                jobs = await self.store.read_resume_jobs(last_id, block_ms=1000)
                for stream_id, job in jobs:
                    last_id = stream_id
                    await self.coordinator_factory().resume(
                        job.tenant_id, job.assistance_id
                    )
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning(
                    "browser resume worker 异常: type={}", type(exc).__name__
                )
                await asyncio.sleep(1)


class AgentResumeCoordinator:
    def __init__(
        self,
        store: ResumeStore | None = None,
        consumer_id: str | None = None,
        continuation_callback: ContinuationCallback | None = None,
    ) -> None:
        self.store = store or ResumeStore()
        self.consumer_id = consumer_id or f"resume_{os.getpid()}_{uuid.uuid4().hex}"
        self.continuation_callback = continuation_callback or _CONTINUATION_CALLBACK

    async def resume(self, tenant_id: str, assistance_id: str) -> dict:
        record = await self.store.get_assistance(tenant_id, assistance_id)
        if record is None or record.state != "resume_queued":
            return {"success": False, "error_code": "RESUME_ALREADY_CONSUMED"}
        runtime = await get_owned_runtime(tenant_id, record.run_id)
        if runtime is None:
            # 请求可能落在非 owner Gunicorn worker；不能在确认 owner 已丢失前
            # 抢 claim 或清理另一个 worker 的有效 page/context。
            return {"success": False, "error_code": "RESUME_NOT_OWNER"}
        if not await self.store.claim_resume(tenant_id, assistance_id, self.consumer_id):
            return {"success": False, "error_code": "RESUME_ALREADY_CONSUMED"}
        await self.store.append_event(tenant_id, record.continuation_id, {
            "type": "browser_resume_started", "run_id": record.run_id,
        })
        keep_runtime = False
        try:
            result = await runtime.orchestrator.resume_from_human(
                completed_by_human=record.completed_by_human,
                step_index=record.step_index,
            )
            if result.get("status") == "ask_user":
                await self.store.cas_state(
                    tenant_id, assistance_id, {"resume_queued"}, "resumed"
                )
                next_suspension = await HumanControlCoordinator(store=self.store).suspend(
                    tenant_id=record.tenant_id, user_id=record.user_id,
                    session_id=record.session_id,
                    agent_execution_id=record.agent_execution_id,
                    tool_call_id=record.tool_call_id, run_id=record.run_id,
                    manager=runtime.manager, orchestrator=runtime.orchestrator,
                    executor=runtime.executor,
                    reason_code=result.get("error_code", "HUMAN_REQUIRED"),
                    step_index=len(runtime.orchestrator.steps),
                    replaces=record,
                )
                await self.store.append_event(tenant_id, record.continuation_id, next_suspension.event)
                keep_runtime = True
                return {"success": False, "status": "ask_user", "assistance_id": next_suspension.assistance_id}
            await self.store.append_event(tenant_id, record.continuation_id, {
                "type": "tool_result", "tool_call_id": record.tool_call_id,
                # continuation stream 只承载状态，不复制可能包含页面正文的工具结果。
                "success": bool(result.get("success")),
                "error_code": result.get("error_code") if not result.get("success") else None,
            })
            await self.store.append_event(tenant_id, record.continuation_id, {
                "type": "agent_continuation_started", "continuation_id": record.continuation_id,
            })
            continuation_ttl = max(
                900, int(record.expires_at - time.time())
            )
            continuation_expires_at = max(
                record.expires_at, time.time() + continuation_ttl
            )
            resuming = await self.store.cas_state(
                tenant_id, assistance_id, {"resume_queued"}, "agent_resuming",
                expires_at=continuation_expires_at,
            )
            if resuming is None:
                return {"success": False, "error_code": "RESUME_ALREADY_CONSUMED"}
            refresh = getattr(self.store, "refresh_suspension", None)
            if refresh is not None and not await refresh(resuming, continuation_ttl):
                raise RuntimeError("CONTINUATION_LEASE_REFRESH_FAILED")
            if self.continuation_callback is not None:
                await self.continuation_callback(resuming, result)
            await self.store.append_event(tenant_id, record.continuation_id, {
                "type": "agent_continuation_completed",
                "continuation_id": record.continuation_id,
            })
            await self.store.cas_state(
                tenant_id, assistance_id, {"agent_resuming"}, "resumed"
            )
            await self.store.clear(record)
            return result
        except Exception:
            try:
                await runtime.manager.finalize(
                    tenant_id, record.run_id, RunState.FAILED, "resume_failed"
                )
            except Exception as exc:
                logger.warning("browser resume 失败收口异常: type={}", type(exc).__name__)
            try:
                await self.store.cas_state(
                    tenant_id, assistance_id,
                    {"resume_queued", "agent_resuming"}, "failed",
                )
                await self.store.append_event(tenant_id, record.continuation_id, {
                    "type": "browser_run_closed", "error_code": "RESUME_CONTEXT_LOST",
                })
            finally:
                await self.store.clear(record)
            return {"success": False, "error_code": "RESUME_CONTEXT_LOST"}
        finally:
            if not keep_runtime:
                await unregister_owned_runtime(tenant_id, record.run_id)
