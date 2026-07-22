"""人工协作、工具挂起、恢复任务与 continuation 事件存储。"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.core.cache_utils import CacheKeys
from src.core.redis_client import redis_client


ACTIVE_ASSISTANCE_STATES = {
    "pending", "controlling", "completed", "resume_queued", "agent_resuming",
}
EXPIRABLE_ASSISTANCE_STATES = {
    "pending", "controlling", "completed", "resume_queued",
}


async def get_active_session_suspension(tenant_id: str, session_id: str) -> dict[str, Any] | None:
    """API 并发门禁；Redis 不可用时不存在合法跨请求挂起。"""
    if not redis_client.is_available():
        return None
    key = redis_client.make_key(CacheKeys.AGENT_SESSION_SUSPENSION, f"{tenant_id}:{session_id}")
    value = await asyncio.to_thread(redis_client.get, key)
    return value if value and value.get("assistance_id") else None


class AssistanceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: str
    user_id: str
    session_id: str
    assistance_id: str
    run_id: str
    agent_execution_id: str
    tool_call_id: str
    continuation_id: str
    agent_name: str | None = None
    state: str = "pending"
    reason_code: str
    instruction_code: str
    completion_mode: str
    predicates: tuple[dict[str, str], ...] = ()
    step_index: int = 0
    completed_by_human: bool = False
    extended: bool = False
    expires_at: float
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ResumeJob(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: str
    assistance_id: str
    run_id: str
    job_id: str
    claimed_by: str | None = None


class ResumeStore:
    """安全关键挂起只允许使用真实 Redis。"""

    def __init__(self) -> None:
        if not redis_client.is_available():
            raise RuntimeError("REDIS_REQUIRED")

    @staticmethod
    def available() -> bool:
        return redis_client.is_available()

    def _assistance_key(self, tenant_id: str, assistance_id: str) -> str:
        return redis_client.make_key(CacheKeys.BROWSER_ASSISTANCE, f"{tenant_id}:{assistance_id}")

    def _suspension_key(self, tenant_id: str, execution_id: str, tool_call_id: str) -> str:
        return redis_client.make_key(CacheKeys.AGENT_TOOL_SUSPENSION, f"{tenant_id}:{execution_id}:{tool_call_id}")

    def _session_key(self, tenant_id: str, session_id: str) -> str:
        return redis_client.make_key(CacheKeys.AGENT_SESSION_SUSPENSION, f"{tenant_id}:{session_id}")

    def _events_key(self, tenant_id: str, continuation_id: str) -> str:
        return redis_client.make_key(CacheKeys.AGENT_CONTINUATION_EVENTS, f"{tenant_id}:{continuation_id}")

    async def save_suspension(self, record: AssistanceRecord, ttl: int) -> bool:
        def op() -> bool:
            if not redis_client.is_available():
                return False
            session_key = self._session_key(record.tenant_id, record.session_id)
            token = f"suspend:{time.time_ns()}"
            if not redis_client.acquire_lock(session_key + ":cas", token, ex=5):
                return False
            try:
                active = redis_client.get(session_key)
                if active and active.get("assistance_id") != record.assistance_id:
                    return False
                redis_client.set(
                    self._assistance_key(record.tenant_id, record.assistance_id),
                    record.model_dump(mode="json"), ex=ttl + 120,
                )
                suspension = {
                    "tenant_id": record.tenant_id, "user_id": record.user_id,
                    "session_id": record.session_id, "assistance_id": record.assistance_id,
                    "run_id": record.run_id, "agent_execution_id": record.agent_execution_id,
                    "tool_call_id": record.tool_call_id, "continuation_id": record.continuation_id,
                }
                redis_client.set(self._suspension_key(record.tenant_id, record.agent_execution_id, record.tool_call_id), suspension, ex=ttl + 120)
                redis_client.set(session_key, suspension, ex=ttl + 120)
                return bool(redis_client.get(session_key))
            finally:
                redis_client.release_lock(session_key + ":cas", token)
        return await asyncio.to_thread(op)

    async def refresh_suspension(self, record: AssistanceRecord, ttl: int) -> bool:
        """延期时同步刷新 assistance、tool suspension 与 session gate。"""
        def op() -> bool:
            session_key = self._session_key(record.tenant_id, record.session_id)
            token = f"refresh:{time.time_ns()}"
            if not redis_client.acquire_lock(session_key + ":cas", token, ex=5):
                return False
            try:
                active = redis_client.get(session_key)
                if not active or active.get("assistance_id") != record.assistance_id:
                    return False
                keys = (
                    self._assistance_key(record.tenant_id, record.assistance_id),
                    self._suspension_key(
                        record.tenant_id, record.agent_execution_id, record.tool_call_id
                    ),
                    session_key,
                )
                return all(redis_client.expire(key, ttl + 120) for key in keys)
            finally:
                redis_client.release_lock(session_key + ":cas", token)
        return await asyncio.to_thread(op)

    async def replace_suspension(
        self, previous: AssistanceRecord, record: AssistanceRecord, ttl: int,
    ) -> bool:
        """同一原工具再次求助时原子替换 session gate，避免短暂放行 Agent loop。"""
        def op() -> bool:
            session_key = self._session_key(record.tenant_id, record.session_id)
            token = f"replace:{time.time_ns()}"
            if not redis_client.acquire_lock(session_key + ":cas", token, ex=5):
                return False
            try:
                active = redis_client.get(session_key)
                if not active or active.get("assistance_id") != previous.assistance_id:
                    return False
                suspension = {
                    "tenant_id": record.tenant_id, "user_id": record.user_id,
                    "session_id": record.session_id, "assistance_id": record.assistance_id,
                    "run_id": record.run_id, "agent_execution_id": record.agent_execution_id,
                    "tool_call_id": record.tool_call_id, "continuation_id": record.continuation_id,
                }
                redis_client.set(
                    self._assistance_key(record.tenant_id, record.assistance_id),
                    record.model_dump(mode="json"), ex=ttl + 120,
                )
                redis_client.set(
                    self._suspension_key(
                        record.tenant_id, record.agent_execution_id, record.tool_call_id
                    ),
                    suspension, ex=ttl + 120,
                )
                redis_client.set(session_key, suspension, ex=ttl + 120)
                return True
            finally:
                redis_client.release_lock(session_key + ":cas", token)
        return await asyncio.to_thread(op)

    async def get_assistance(self, tenant_id: str, assistance_id: str) -> AssistanceRecord | None:
        value = await asyncio.to_thread(redis_client.get, self._assistance_key(tenant_id, assistance_id))
        return AssistanceRecord.model_validate(value) if value else None

    async def get_active_session(self, tenant_id: str, session_id: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(redis_client.get, self._session_key(tenant_id, session_id))

    async def cas_state(
        self, tenant_id: str, assistance_id: str, expected: set[str], new_state: str,
        expected_expires_at: float | None = None,
        **updates: Any,
    ) -> AssistanceRecord | None:
        def op():
            key = self._assistance_key(tenant_id, assistance_id)
            token = f"assist:{time.time_ns()}"
            if not redis_client.acquire_lock(key + ":cas", token, ex=5):
                return None
            try:
                raw = redis_client.get(key)
                if not raw:
                    return None
                record = AssistanceRecord.model_validate(raw)
                if record.state not in expected:
                    return None
                if (
                    expected_expires_at is not None
                    and record.expires_at != expected_expires_at
                ):
                    return None
                record = record.model_copy(update={"state": new_state, **updates})
                ttl = max(1, int(record.expires_at - time.time()) + 120)
                redis_client.set(key, record.model_dump(mode="json"), ex=ttl)
                return record
            finally:
                redis_client.release_lock(key + ":cas", token)
        return await asyncio.to_thread(op)

    async def bind_agent(self, record: AssistanceRecord, agent_name: str | None) -> bool:
        """在工具 coroutine 返回后补充可跨 worker 解析的 Agent 路由。"""
        updated = await self.cas_state(
            record.tenant_id,
            record.assistance_id,
            {"pending"},
            "pending",
            agent_name=agent_name,
        )
        return updated is not None

    async def list_expired_assistance(self, now: float) -> list[AssistanceRecord]:
        """扫描已过人工租约且仍处于活动状态的 assistance。"""
        def op() -> list[AssistanceRecord]:
            if not redis_client.is_available():
                return []
            pattern = redis_client.make_key(CacheKeys.BROWSER_ASSISTANCE, "*")
            cursor = 0
            records: list[AssistanceRecord] = []
            while True:
                cursor, keys = redis_client.scan(cursor, pattern, 100)
                for key in keys:
                    raw = redis_client.get(key)
                    if not raw:
                        continue
                    try:
                        record = AssistanceRecord.model_validate(raw)
                    except Exception:
                        continue
                    if record.state in EXPIRABLE_ASSISTANCE_STATES and record.expires_at <= now:
                        records.append(record)
                if cursor == 0:
                    return records

        return await asyncio.to_thread(op)

    async def enqueue_resume(self, record: AssistanceRecord, job_id: str) -> bool:
        """入队 resume job 到 PostgreSQL 持久队列（Phase 3R 替代 Redis Stream）。

        ``assistance_id`` 唯一约束保证幂等。Redis publish 仅作为可选降延迟通知，
        失败不得阻塞入队（丢通知时 worker 靠短轮询恢复）。不再调用 XADD/XREAD。
        """
        from .run_db import BrowserResumeJobDB

        enqueued = await BrowserResumeJobDB().enqueue_from_assistance(
            tenant_id=record.tenant_id,
            assistance_id=record.assistance_id,
            run_id=record.run_id,
            job_id=job_id,
        )
        if enqueued and redis_client.is_available():
            try:
                redis_client.publish(
                    redis_client.make_key(CacheKeys.BROWSER_RESUME_JOBS, "notify"),
                    json.dumps({
                        "tenant_id": record.tenant_id,
                        "assistance_id": record.assistance_id,
                        "job_id": job_id,
                    }),
                )
            except Exception:
                # 通知失败不影响持久化入队；worker 轮询会补取
                pass
        return enqueued

    async def claim_resume_jobs(
        self, *, lease_owner: str, lease_seconds: int = 600, limit: int = 10,
    ) -> list[ResumeJob]:
        """领取 pending 到期或租约过期的 resume job（FOR UPDATE SKIP LOCKED）。

        claim 在领取事务内完成，不再使用 Redis SETNX。多 worker 互不阻塞。
        """
        from .run_db import BrowserResumeJobDB

        rows = await BrowserResumeJobDB().claim_batch(
            lease_owner=lease_owner, lease_seconds=lease_seconds, limit=limit,
        )
        jobs: list[ResumeJob] = []
        for row in rows:
            try:
                jobs.append(ResumeJob(
                    tenant_id=row["tenant_id"],
                    assistance_id=row["assistance_id"],
                    run_id=row["run_id"],
                    job_id=row["job_id"],
                    claimed_by=lease_owner,
                ))
            except Exception:
                continue
        return jobs

    async def complete_resume_job(
        self, tenant_id: str, job_id: str, lease_owner: str,
    ) -> bool:
        from .run_db import BrowserResumeJobDB

        return await BrowserResumeJobDB().mark_completed(
            tenant_id=tenant_id, job_id=job_id, lease_owner=lease_owner,
        )

    async def fail_resume_job(
        self, tenant_id: str, job_id: str, lease_owner: str, error_code: str,
        *, permanent: bool, backoff_seconds: int = 30,
    ) -> None:
        from .run_db import BrowserResumeJobDB

        await BrowserResumeJobDB().mark_failed(
            tenant_id=tenant_id, job_id=job_id, lease_owner=lease_owner,
            error_code=error_code,
            permanent=permanent, backoff_seconds=backoff_seconds,
        )

    async def append_event(self, tenant_id: str, continuation_id: str, event: dict[str, Any]) -> dict[str, Any]:
        def op():
            key = self._events_key(tenant_id, continuation_id)
            token = f"event:{time.time_ns()}"
            if not redis_client.acquire_lock(key + ":cas", token, ex=5):
                raise RuntimeError("CONTINUATION_EVENT_BUSY")
            try:
                state = redis_client.get(key) or {"last_seq": 0, "events": []}
                seq = int(state["last_seq"]) + 1
                safe_event = {**event, "seq": seq}
                events = list(state.get("events", []))[-499:] + [safe_event]
                redis_client.set(key, {"last_seq": seq, "events": events}, ex=900)
                return safe_event
            finally:
                redis_client.release_lock(key + ":cas", token)
        return await asyncio.to_thread(op)

    async def events_after(self, tenant_id: str, continuation_id: str, after_seq: int) -> list[dict[str, Any]]:
        state = await asyncio.to_thread(redis_client.get, self._events_key(tenant_id, continuation_id)) or {}
        return [item for item in state.get("events", []) if int(item.get("seq", 0)) > after_seq]

    async def clear(self, record: AssistanceRecord) -> None:
        def op() -> None:
            session_key = self._session_key(record.tenant_id, record.session_id)
            token = f"clear:{time.time_ns()}"
            for _ in range(5):
                if redis_client.acquire_lock(session_key + ":cas", token, ex=5):
                    break
                time.sleep(0.01)
            else:
                return
            try:
                active = redis_client.get(session_key)
                if active and active.get("assistance_id") == record.assistance_id:
                    redis_client.delete(session_key)
                suspension_key = self._suspension_key(
                    record.tenant_id, record.agent_execution_id, record.tool_call_id
                )
                suspension = redis_client.get(suspension_key)
                if suspension and suspension.get("assistance_id") == record.assistance_id:
                    redis_client.delete(suspension_key)
            finally:
                redis_client.release_lock(session_key + ":cas", token)
        await asyncio.to_thread(op)
