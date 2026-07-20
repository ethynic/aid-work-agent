"""网页人工接管协调器与 owner 进程运行时注册表。"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from loguru import logger

from src.core.tool_suspension import ToolSuspension

from .human_completion_monitor import (
    CompletionObservation, CompletionPredicate, HumanCompletionMonitor,
)
from .resume_store import AssistanceRecord, ResumeStore
from .run_db import BrowserRunDB
from .run_manager import BrowserRunManager, RunState


HUMAN_LEASE_SECONDS = 300


@dataclass(slots=True)
class OwnedHumanRuntime:
    manager: BrowserRunManager
    orchestrator: Any
    executor: Any


_OWNED_RUNTIMES: dict[tuple[str, str], OwnedHumanRuntime] = {}
_RUNTIME_LOCK = asyncio.Lock()
_COMPLETION_TASKS: dict[tuple[str, str], asyncio.Task] = {}


async def register_owned_runtime(tenant_id: str, run_id: str, runtime: OwnedHumanRuntime) -> None:
    async with _RUNTIME_LOCK:
        _OWNED_RUNTIMES[(tenant_id, run_id)] = runtime


async def get_owned_runtime(tenant_id: str, run_id: str) -> OwnedHumanRuntime | None:
    async with _RUNTIME_LOCK:
        return _OWNED_RUNTIMES.get((tenant_id, run_id))


async def unregister_owned_runtime(tenant_id: str, run_id: str) -> None:
    async with _RUNTIME_LOCK:
        _OWNED_RUNTIMES.pop((tenant_id, run_id), None)
        monitor_task = _COMPLETION_TASKS.pop((tenant_id, run_id), None)
    if monitor_task and monitor_task is not asyncio.current_task():
        monitor_task.cancel()
        await asyncio.gather(monitor_task, return_exceptions=True)
    from .view_hub import browser_view_hub
    await browser_view_hub.clear(tenant_id, run_id)


async def stop_all_completion_monitors() -> None:
    """应用 shutdown 时停止所有自动完成采样，不遗留后台 task。"""
    async with _RUNTIME_LOCK:
        tasks = list(_COMPLETION_TASKS.values())
        _COMPLETION_TASKS.clear()
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def _instructions(reason_code: str) -> tuple[str, str, tuple[str, ...]]:
    if reason_code == "CAPTCHA_REQUIRED":
        return (
            "PAGE_VERIFICATION", "请完成页面验证", (
                "点击“开始接管”",
                "直接在浏览器画面中完成验证码，不要在聊天中发送验证码或密码",
                "验证成功后点击“完成并继续”，系统会检测页面状态后续跑",
            ),
        )
    return (
        "PAGE_CONFIRMATION", "需要你在页面中完成操作", (
            "点击“开始接管”",
            "按页面提示完成操作，密码或验证码只在浏览器中输入",
            "确认页面已完成后，点击“完成并继续”",
        ),
    )


class HumanControlCoordinator:
    def __init__(
        self, store: ResumeStore | None = None, run_db: BrowserRunDB | None = None,
        monitor: HumanCompletionMonitor | None = None,
    ) -> None:
        self.store = store or ResumeStore()
        self.run_db = run_db or BrowserRunDB()
        self.monitor = monitor or HumanCompletionMonitor()

    async def _update_audit_state(
        self, tenant_id: str, assistance_id: str, expected: str, new: str,
    ) -> None:
        """审计库不是控制面的权威状态，故障不能中断人工接管主链路。"""
        try:
            await self.run_db.update_assistance_state(
                tenant_id, assistance_id, expected, new
            )
        except Exception as exc:
            logger.warning("browser assistance 审计更新失败: type={}", type(exc).__name__)

    async def _fail_runtime(
        self, record: AssistanceRecord, runtime: OwnedHumanRuntime,
        expected: set[str], reason: str,
    ) -> None:
        await self.store.cas_state(
            record.tenant_id, record.assistance_id, expected, "failed"
        )
        try:
            await runtime.manager.finalize(
                record.tenant_id, record.run_id, RunState.FAILED, reason
            )
        except Exception as exc:
            logger.warning("browser assistance 失败收口异常: type={}", type(exc).__name__)
        await unregister_owned_runtime(record.tenant_id, record.run_id)
        await self.store.clear(record)

    async def suspend(
        self, *, tenant_id: str, user_id: str, session_id: str,
        agent_execution_id: str, tool_call_id: str, run_id: str,
        manager: BrowserRunManager, orchestrator: Any, executor: Any,
        reason_code: str = "HUMAN_REQUIRED", step_index: int = 0,
        replaces: AssistanceRecord | None = None,
    ) -> ToolSuspension:
        if not self.store.available():
            raise RuntimeError("TOOL_SUSPEND_FAILED")
        assistance_id = f"bha_{uuid.uuid4().hex}"
        continuation_id = f"bac_{uuid.uuid4().hex}"
        instruction_code, title, steps = _instructions(reason_code)
        is_captcha = reason_code == "CAPTCHA_REQUIRED"
        completion_mode = "auto_or_confirm" if is_captcha else "confirm_only"
        predicates = (
            ({"type": "challenge_iframe_absent", "value": "absent"},)
            if is_captcha else ()
        )
        expires_at = time.time() + HUMAN_LEASE_SECONDS
        record = AssistanceRecord(
            tenant_id=tenant_id, user_id=user_id, session_id=session_id,
            assistance_id=assistance_id, run_id=run_id,
            agent_execution_id=agent_execution_id, tool_call_id=tool_call_id,
            continuation_id=continuation_id, reason_code=reason_code,
            instruction_code=instruction_code, completion_mode=completion_mode,
            predicates=predicates, step_index=step_index, expires_at=expires_at,
            agent_name=replaces.agent_name if replaces is not None else None,
        )
        saved = (
            await self.store.replace_suspension(
                replaces, record, HUMAN_LEASE_SECONDS
            )
            if replaces is not None
            else await self.store.save_suspension(record, HUMAN_LEASE_SECONDS)
        )
        if not saved:
            raise RuntimeError("TOOL_SUSPEND_FAILED")
        try:
            try:
                await self.run_db.create_assistance({
                    **record.model_dump(mode="json"),
                    "predicate_type": predicates[0]["type"] if predicates else None,
                    "expires_at": datetime.fromtimestamp(expires_at, timezone.utc),
                })
            except Exception as exc:
                logger.warning("browser assistance 审计创建失败: type={}", type(exc).__name__)
            await manager.transition(tenant_id, run_id, RunState.WAITING_HUMAN)
            await register_owned_runtime(
                tenant_id, run_id, OwnedHumanRuntime(manager, orchestrator, executor)
            )
        except BaseException:
            try:
                await asyncio.shield(
                    manager.finalize(
                        tenant_id, run_id, RunState.FAILED,
                        "human_suspension_setup_failed",
                    )
                )
            except Exception as exc:
                logger.warning("browser assistance 挂起收口异常: type={}", type(exc).__name__)
            await unregister_owned_runtime(tenant_id, run_id)
            await self.store.clear(record)
            raise
        event = {
            "type": "browser_human_required", "assistance_id": assistance_id,
            "run_id": run_id, "continuation_id": continuation_id,
            "reason_code": reason_code, "surface": "server_web", "title": title,
            "steps": list(steps), "completion_mode": completion_mode,
            "completion_status": "waiting", "expires_at": datetime.fromtimestamp(
                expires_at, timezone.utc
            ).isoformat(),
        }
        return ToolSuspension(
            tenant_id=tenant_id, user_id=user_id, session_id=session_id,
            agent_execution_id=agent_execution_id, tool_call_id=tool_call_id,
            run_id=run_id, assistance_id=assistance_id,
            continuation_id=continuation_id, event=event,
        )

    async def take_control(self, tenant_id: str, user_id: str, assistance_id: str) -> AssistanceRecord:
        record = await self._owned_record(tenant_id, user_id, assistance_id)
        runtime = await get_owned_runtime(tenant_id, record.run_id)
        if runtime is None:
            await self.store.cas_state(tenant_id, assistance_id, {record.state}, "failed")
            await self.store.clear(record)
            await unregister_owned_runtime(tenant_id, record.run_id)
            raise RuntimeError("RESUME_CONTEXT_LOST")
        updated = await self.store.cas_state(tenant_id, assistance_id, {"pending"}, "controlling")
        if updated is None:
            raise RuntimeError("CONTROL_ALREADY_TAKEN")
        try:
            await runtime.manager.transition(
                tenant_id, record.run_id, RunState.RUNNING_HUMAN
            )
        except Exception:
            await self._fail_runtime(
                updated, runtime, {"controlling"}, "human_control_transition_failed"
            )
            raise RuntimeError("RESUME_CONTEXT_LOST")
        await self._update_audit_state(
            tenant_id, assistance_id, "pending", "controlling"
        )
        if updated.completion_mode == "auto_or_confirm":
            await self._start_completion_monitor(updated)
        return updated

    async def _start_completion_monitor(self, record: AssistanceRecord) -> None:
        """owner worker 后台双采样；按钮与自动事件仍共用同一个 CAS。"""
        key = (record.tenant_id, record.run_id)
        async with _RUNTIME_LOCK:
            current = _COMPLETION_TASKS.get(key)
            if current and not current.done():
                return
            task = asyncio.create_task(
                self._completion_loop(record.tenant_id, record.user_id, record.assistance_id)
            )
            _COMPLETION_TASKS[key] = task

    async def _completion_loop(
        self, tenant_id: str, user_id: str, assistance_id: str,
    ) -> None:
        try:
            while True:
                record = await self.store.get_assistance(tenant_id, assistance_id)
                if record is None or record.state != "controlling":
                    return
                if record.expires_at <= time.time():
                    return
                try:
                    queued, missing = await self.complete(
                        tenant_id, user_id, assistance_id, automatic=True
                    )
                    if not missing:
                        # enqueue_resume 已写入持久 Stream；由 BrowserResumeWorker
                        # 领取，不能退化成请求内 create_task。
                        return
                except RuntimeError as exc:
                    if str(exc) in {"RESUME_ALREADY_CONSUMED", "HUMAN_TIMEOUT"}:
                        return
                    if str(exc) == "RESUME_CONTEXT_LOST":
                        return
                    logger.warning(
                        "browser 自动完成检测异常: code={}", str(exc)
                    )
                await asyncio.sleep(max(0.2, self.monitor.sample_interval))
        except asyncio.CancelledError:
            return

    async def complete(
        self, tenant_id: str, user_id: str, assistance_id: str,
        automatic: bool = False,
    ) -> tuple[AssistanceRecord, list[str]]:
        record = await self._owned_record(tenant_id, user_id, assistance_id)
        runtime = await get_owned_runtime(tenant_id, record.run_id)
        if runtime is None:
            await self.store.cas_state(tenant_id, assistance_id, {record.state}, "failed")
            await self.store.clear(record)
            await unregister_owned_runtime(tenant_id, record.run_id)
            raise RuntimeError("RESUME_CONTEXT_LOST")
        if record.state == "pending":
            record = await self.take_control(tenant_id, user_id, assistance_id)
        if record.state != "controlling":
            raise RuntimeError("RESUME_ALREADY_CONSUMED")

        async def sampler() -> CompletionObservation:
            snapshot = await runtime.orchestrator.page_ops.take_snapshot()
            if not snapshot.get("success"):
                raise RuntimeError("RESUME_CONTEXT_LOST")
            elements = snapshot.get("interactive_elements", [])
            structural = frozenset(
                str(item.get("role") or item.get("element_type") or "") for item in elements
            )
            challenge_markers = (
                "captcha", "recaptcha", "hcaptcha", "turnstile",
                "验证码", "人机验证", "安全验证",
            )
            challenge = any(
                any(marker in (
                    str(item.get("label", "")) + str(item.get("role", ""))
                ).lower() for marker in challenge_markers)
                for item in elements
            ) or bool(snapshot.get("challenge_iframe_present"))
            return CompletionObservation(
                origin_path=str(snapshot.get("url", "")), present_elements=structural,
                challenge_iframe_present=challenge,
            )

        missing: list[str] = []
        if record.completion_mode != "confirm_only":
            predicates = tuple(CompletionPredicate(**item) for item in record.predicates)
            met, missing = await self.monitor.stable(predicates, sampler)
            if not met:
                return record, missing
        else:
            await sampler()  # confirm_only 仍必须确认活页面并取得新快照。

        updated = await self.store.cas_state(
            tenant_id, assistance_id, {"controlling"}, "completed",
            # complete API 只由人工按钮触发；即使是 auto_or_confirm 模式，
            # 进入此路径也必须把当前不可逆页面动作记为人工已完成。
            completed_by_human=True,
        )
        if updated is None:
            raise RuntimeError("RESUME_ALREADY_CONSUMED")
        try:
            await runtime.manager.transition(
                tenant_id, record.run_id, RunState.RESUMING
            )
        except Exception:
            await self._fail_runtime(
                updated, runtime, {"completed"}, "human_resume_transition_failed"
            )
            raise RuntimeError("RESUME_CONTEXT_LOST")
        queued = await self.store.cas_state(tenant_id, assistance_id, {"completed"}, "resume_queued")
        if queued is None:
            raise RuntimeError("RESUME_ALREADY_CONSUMED")
        job_id = f"brj_{uuid.uuid4().hex}"
        if not await self.store.enqueue_resume(queued, job_id):
            await self.store.cas_state(tenant_id, assistance_id, {"resume_queued"}, "failed")
            try:
                await runtime.manager.finalize(
                    tenant_id, record.run_id, RunState.FAILED, "resume_enqueue_failed"
                )
            finally:
                await unregister_owned_runtime(tenant_id, record.run_id)
                await self.store.clear(record)
            raise RuntimeError("TOOL_SUSPEND_FAILED")
        await self._update_audit_state(
            tenant_id, assistance_id, "controlling", "resume_queued"
        )
        return queued, []

    async def extend(self, tenant_id: str, user_id: str, assistance_id: str) -> AssistanceRecord:
        record = await self._owned_record(tenant_id, user_id, assistance_id)
        if record.extended:
            raise RuntimeError("HUMAN_EXTEND_LIMIT")
        updated = await self.store.cas_state(
            tenant_id, assistance_id, {"pending", "controlling"}, record.state,
            extended=True, expires_at=time.time() + HUMAN_LEASE_SECONDS,
        )
        if updated is None:
            raise RuntimeError("RESUME_ALREADY_CONSUMED")
        if not await self.store.refresh_suspension(updated, HUMAN_LEASE_SECONDS):
            raise RuntimeError("TOOL_SUSPEND_FAILED")
        return updated

    async def cancel(self, tenant_id: str, user_id: str, assistance_id: str) -> None:
        record = await self._owned_record(tenant_id, user_id, assistance_id)
        cancelled = await self.store.cas_state(
            tenant_id, assistance_id, {"pending", "controlling", "resume_queued"}, "cancelled"
        )
        if cancelled is None:
            raise RuntimeError("RESUME_ALREADY_CONSUMED")
        runtime = await get_owned_runtime(tenant_id, record.run_id)
        try:
            if runtime:
                await runtime.manager.request_cancel(tenant_id, user_id, record.run_id)
        finally:
            await unregister_owned_runtime(tenant_id, record.run_id)
            await self.store.clear(record)

    async def _owned_record(self, tenant_id: str, user_id: str, assistance_id: str) -> AssistanceRecord:
        record = await self.store.get_assistance(tenant_id, assistance_id)
        if record is None or record.user_id != user_id:
            raise KeyError("ASSISTANCE_NOT_FOUND")
        get_active = getattr(self.store, "get_active_session", None)
        if get_active is not None:
            active = await get_active(tenant_id, record.session_id)
            if not active or active.get("assistance_id") != assistance_id:
                raise KeyError("ASSISTANCE_NOT_FOUND")
        if record.expires_at <= time.time():
            runtime = await get_owned_runtime(tenant_id, record.run_id)
            try:
                if runtime:
                    await runtime.manager.finalize(
                        tenant_id, record.run_id, RunState.EXPIRED, "human_timeout"
                    )
            finally:
                await self.store.cas_state(
                    tenant_id, assistance_id, {record.state}, "expired"
                )
                await unregister_owned_runtime(tenant_id, record.run_id)
                await self.store.clear(record)
            raise RuntimeError("HUMAN_TIMEOUT")
        return record
