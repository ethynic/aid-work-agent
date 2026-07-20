"""人工完成后的幂等 Browser/Agent continuation 协调器。"""

from __future__ import annotations

import os
import uuid
from typing import Awaitable, Callable

from loguru import logger

from .human_control import HumanControlCoordinator, get_owned_runtime, unregister_owned_runtime
from .resume_store import ResumeStore
from .run_manager import RunState


ContinuationCallback = Callable[[dict], Awaitable[None]]


class AgentResumeCoordinator:
    def __init__(self, store: ResumeStore | None = None, consumer_id: str | None = None) -> None:
        self.store = store or ResumeStore()
        self.consumer_id = consumer_id or f"resume_{os.getpid()}_{uuid.uuid4().hex}"

    async def resume(self, tenant_id: str, assistance_id: str) -> dict:
        if not await self.store.claim_resume(tenant_id, assistance_id, self.consumer_id):
            return {"success": False, "error_code": "RESUME_ALREADY_CONSUMED"}
        record = await self.store.get_assistance(tenant_id, assistance_id)
        if record is None or record.state != "resume_queued":
            return {"success": False, "error_code": "RESUME_ALREADY_CONSUMED"}
        runtime = await get_owned_runtime(tenant_id, record.run_id)
        if runtime is None:
            try:
                await self.store.cas_state(
                    tenant_id, assistance_id, {"resume_queued"}, "failed"
                )
                await self.store.append_event(tenant_id, record.continuation_id, {
                    "type": "browser_run_closed", "error_code": "RESUME_CONTEXT_LOST",
                })
            finally:
                await self.store.clear(record)
                await unregister_owned_runtime(tenant_id, record.run_id)
            return {"success": False, "error_code": "RESUME_CONTEXT_LOST"}
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
            await self.store.cas_state(tenant_id, assistance_id, {"resume_queued"}, "resumed")
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
                    tenant_id, assistance_id, {"resume_queued"}, "failed"
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
