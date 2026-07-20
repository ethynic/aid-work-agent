"""人工协作、工具挂起、恢复任务与 continuation 事件存储。"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.core.cache_utils import CacheKeys
from src.core.redis_client import redis_client


ACTIVE_ASSISTANCE_STATES = {"pending", "controlling", "completed", "resume_queued"}


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

    def _jobs_key(self) -> str:
        return redis_client.make_key(CacheKeys.BROWSER_RESUME_JOBS, "stream")

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
                record = record.model_copy(update={"state": new_state, **updates})
                ttl = max(1, int(record.expires_at - time.time()) + 120)
                redis_client.set(key, record.model_dump(mode="json"), ex=ttl)
                return record
            finally:
                redis_client.release_lock(key + ":cas", token)
        return await asyncio.to_thread(op)

    async def enqueue_resume(self, record: AssistanceRecord, job_id: str) -> bool:
        def op() -> bool:
            key = self._jobs_key()
            if redis_client._client is None or not redis_client.is_available():
                return False
            redis_client._client.xadd(key, {
                "tenant_id": record.tenant_id, "assistance_id": record.assistance_id,
                "run_id": record.run_id, "job_id": job_id,
            }, maxlen=10_000, approximate=True)
            return True
        return await asyncio.to_thread(op)

    async def claim_resume(self, tenant_id: str, assistance_id: str, consumer_id: str) -> bool:
        key = redis_client.make_key(CacheKeys.BROWSER_RESUME_JOBS, f"claim:{tenant_id}:{assistance_id}")
        return await asyncio.to_thread(redis_client.acquire_lock, key, consumer_id, 600)

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
