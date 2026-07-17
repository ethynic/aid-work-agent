"""Browser run 的租户隔离状态与 owner lease 存储。"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timezone
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from src.core.cache_utils import CacheKeys
from src.core.redis_client import redis_client


class RunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: str
    user_id: str
    run_id: str
    session_id: str
    state: str
    execution_target: str = "server"
    owner_id: str | None = None
    owner_token: str | None = Field(default=None, repr=False)
    owner_expires_at: float | None = None
    cancel_requested: bool = False
    degraded_single_request: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class BrowserRunStore(Protocol):
    distributed: bool
    async def create(self, record: RunRecord) -> bool: ...
    async def get(self, tenant_id: str, run_id: str) -> RunRecord | None: ...
    async def compare_state(self, tenant_id: str, run_id: str, expected: set[str], new_state: str) -> RunRecord | None: ...
    async def acquire_owner(self, tenant_id: str, run_id: str, owner_id: str, ttl: int) -> str | bool: ...
    async def renew_owner(self, tenant_id: str, run_id: str, owner_token: str, ttl: int) -> bool: ...
    async def release_owner(self, tenant_id: str, run_id: str, owner_token: str) -> bool: ...
    async def fence_owner(self, tenant_id: str, run_id: str, owner_token: str, now: float) -> str | None: ...
    async def request_cancel(self, tenant_id: str, run_id: str) -> bool: ...
    async def list_expired(self, now: float) -> list[RunRecord]: ...


class InMemoryRunStore:
    """仅用于当前请求/单进程测试；不得用于跨请求恢复。"""

    distributed = False

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], RunRecord] = {}
        self._lock = asyncio.Lock()

    async def create(self, record: RunRecord) -> bool:
        key = (record.tenant_id, record.run_id)
        async with self._lock:
            if key in self._records:
                return False
            self._records[key] = record.model_copy(deep=True)
            return True

    async def get(self, tenant_id: str, run_id: str) -> RunRecord | None:
        async with self._lock:
            record = self._records.get((tenant_id, run_id))
            return record.model_copy(deep=True) if record else None

    async def compare_state(self, tenant_id: str, run_id: str, expected: set[str], new_state: str) -> RunRecord | None:
        async with self._lock:
            key = (tenant_id, run_id)
            record = self._records.get(key)
            if record is None or record.state not in expected:
                return None
            updated = record.model_copy(update={"state": new_state, "updated_at": datetime.now(timezone.utc)})
            self._records[key] = updated
            return updated.model_copy(deep=True)

    async def acquire_owner(self, tenant_id: str, run_id: str, owner_id: str, ttl: int) -> str | bool:
        async with self._lock:
            key = (tenant_id, run_id)
            record = self._records.get(key)
            now = time.time()
            if record is None:
                return False
            # acquire 只创建新 epoch，任何仍有效的 owner（即使 owner_id 相同）
            # 都必须通过 renew 延长，避免内存与 Redis 契约分叉。
            if record.owner_id and (record.owner_expires_at or 0) > now:
                return False
            owner_token = f"bo_{uuid.uuid4().hex}"
            self._records[key] = record.model_copy(update={
                "owner_id": owner_id, "owner_token": owner_token,
                "owner_expires_at": now + ttl,
            })
            return owner_token

    async def renew_owner(self, tenant_id: str, run_id: str, owner_token: str, ttl: int) -> bool:
        async with self._lock:
            key = (tenant_id, run_id)
            record = self._records.get(key)
            if record is None or record.owner_token != owner_token or (record.owner_expires_at or 0) <= time.time():
                return False
            self._records[key] = record.model_copy(update={"owner_expires_at": time.time() + ttl})
            return True

    async def release_owner(self, tenant_id: str, run_id: str, owner_token: str) -> bool:
        async with self._lock:
            key = (tenant_id, run_id)
            record = self._records.get(key)
            if record is None or record.owner_token != owner_token:
                return False
            self._records[key] = record.model_copy(update={
                "owner_id": None, "owner_token": None, "owner_expires_at": None,
            })
            return True

    async def fence_owner(
        self, tenant_id: str, run_id: str, owner_token: str, now: float
    ) -> str | None:
        """在同一锁内确认 epoch token、租约、取消和状态。None 表示允许写 IPC。"""
        async with self._lock:
            record = self._records.get((tenant_id, run_id))
            if record is None:
                return "OWNER_LEASE_LOST"
            if record.cancel_requested or record.state == "CANCELLING":
                return "CANCELLED"
            if record.state in {
                "FINALIZING", "SUCCEEDED", "FAILED", "TIMED_OUT", "EXPIRED", "CANCELLED"
            }:
                return "OWNER_LEASE_LOST"
            if record.owner_token != owner_token or (record.owner_expires_at or 0) <= now:
                return "OWNER_LEASE_LOST"
            return None

    async def request_cancel(self, tenant_id: str, run_id: str) -> bool:
        async with self._lock:
            key = (tenant_id, run_id)
            record = self._records.get(key)
            if record is None:
                return False
            self._records[key] = record.model_copy(update={"cancel_requested": True})
            return True

    async def list_expired(self, now: float) -> list[RunRecord]:
        async with self._lock:
            return [r.model_copy(deep=True) for r in self._records.values() if r.owner_id and (r.owner_expires_at or 0) <= now]


class RedisRunStore:
    """Redis 分布式存储；每次状态 CAS 由短锁串行化。"""

    distributed = True

    def __init__(self, run_ttl: int = 600) -> None:
        if not redis_client.is_available():
            raise RuntimeError("Redis 不可用")
        self.run_ttl = run_ttl

    def _run_key(self, tenant_id: str, run_id: str) -> str:
        return redis_client.make_key(CacheKeys.BROWSER_RUN, f"{tenant_id}:{run_id}")

    def _owner_key(self, tenant_id: str, run_id: str) -> str:
        return redis_client.make_key(CacheKeys.BROWSER_OWNER, f"{tenant_id}:{run_id}")

    async def create(self, record: RunRecord) -> bool:
        def op():
            if not redis_client.is_available():
                return False
            key = self._run_key(record.tenant_id, record.run_id)
            lock = key + ":create"
            if not redis_client.acquire_lock(lock, record.run_id, ex=5):
                return False
            try:
                if redis_client.exists(key):
                    return False
                redis_client.set(key, record.model_dump(mode="json"), ex=self.run_ttl)
                if not redis_client.is_available():
                    return False
                stored = redis_client.get(key)
                return bool(stored and stored.get("run_id") == record.run_id)
            finally:
                redis_client.release_lock(lock, record.run_id)
        return await asyncio.to_thread(op)

    async def get(self, tenant_id: str, run_id: str) -> RunRecord | None:
        if not redis_client.is_available():
            return None
        value = await asyncio.to_thread(redis_client.get, self._run_key(tenant_id, run_id))
        return RunRecord.model_validate(value) if value else None

    async def compare_state(self, tenant_id: str, run_id: str, expected: set[str], new_state: str) -> RunRecord | None:
        def op():
            if not redis_client.is_available():
                return None
            key = self._run_key(tenant_id, run_id)
            token = f"cas:{time.time_ns()}"
            lock = key + ":cas"
            if not redis_client.acquire_lock(lock, token, ex=5):
                return None
            try:
                value = redis_client.get(key)
                if not value:
                    return None
                record = RunRecord.model_validate(value)
                if record.state not in expected:
                    return None
                record = record.model_copy(update={"state": new_state, "updated_at": datetime.now(timezone.utc)})
                redis_client.set(key, record.model_dump(mode="json"), ex=self.run_ttl)
                if not redis_client.is_available():
                    return None
                stored = redis_client.get(key)
                if not stored:
                    return None
                verified = RunRecord.model_validate(stored)
                return verified if verified.state == new_state else None
            finally:
                redis_client.release_lock(lock, token)
        return await asyncio.to_thread(op)

    async def acquire_owner(self, tenant_id: str, run_id: str, owner_id: str, ttl: int) -> str | bool:
        if not redis_client.is_available():
            return False
        before = await self.get(tenant_id, run_id)
        if before is None:
            return False
        key = self._owner_key(tenant_id, run_id)
        owner_token = f"bo_{uuid.uuid4().hex}"
        acquired = await asyncio.to_thread(redis_client.acquire_lock, key, owner_token, ttl)
        if acquired:
            metadata_updated = await self._update_owner_metadata(
                tenant_id, run_id, owner_id, owner_token, time.time() + ttl,
                expected_owner_token=before.owner_token,
                check_expected_owner_token=True,
            )
            if not metadata_updated:
                await asyncio.to_thread(redis_client.release_lock, key, owner_token)
                return False
        return owner_token if acquired else False

    async def renew_owner(self, tenant_id: str, run_id: str, owner_token: str, ttl: int) -> bool:
        if not redis_client.is_available():
            return False
        key = self._owner_key(tenant_id, run_id)
        ok = await asyncio.to_thread(redis_client.renew_lock, key, owner_token, ttl)
        if ok:
            current = await self.get(tenant_id, run_id)
            if current is None:
                return False
            ok = await self._update_owner_metadata(
                tenant_id, run_id, current.owner_id, owner_token, time.time() + ttl,
                expected_owner_token=owner_token,
                check_expected_owner_token=True,
            )
        return ok

    async def release_owner(self, tenant_id: str, run_id: str, owner_token: str) -> bool:
        if not redis_client.is_available():
            return False
        ok = await asyncio.to_thread(redis_client.release_lock, self._owner_key(tenant_id, run_id), owner_token)
        if ok:
            await self._update_owner_metadata(
                tenant_id, run_id, None, None, None,
                expected_owner_token=owner_token,
                check_expected_owner_token=True,
            )
        return ok

    async def _update_owner_metadata(
        self, tenant_id: str, run_id: str, owner_id: str | None,
        owner_token: str | None, expires: float | None,
        expected_owner_token: str | None = None,
        check_expected_owner_token: bool = False,
    ) -> bool:
        def op():
            if not redis_client.is_available():
                return False
            key = self._run_key(tenant_id, run_id)
            token = f"owner:{time.time_ns()}"
            lock = key + ":cas"
            if not redis_client.acquire_lock(lock, token, ex=5):
                return False
            try:
                value = redis_client.get(key)
                if not value:
                    return False
                current = RunRecord.model_validate(value)
                if check_expected_owner_token and current.owner_token != expected_owner_token:
                    return False
                record = current.model_copy(
                    update={
                        "owner_id": owner_id, "owner_token": owner_token,
                        "owner_expires_at": expires,
                    }
                )
                redis_client.set(key, record.model_dump(mode="json"), ex=self.run_ttl)
                if not redis_client.is_available():
                    return False
                stored = redis_client.get(key)
                if not stored:
                    return False
                verified = RunRecord.model_validate(stored)
                return (
                    verified.owner_id == owner_id
                    and verified.owner_token == owner_token
                    and verified.owner_expires_at == expires
                )
            finally:
                redis_client.release_lock(lock, token)
        return await asyncio.to_thread(op)

    async def fence_owner(
        self, tenant_id: str, run_id: str, owner_token: str, now: float
    ) -> str | None:
        """用单条 Lua 同时校验 Redis lease key 与 run 状态，绝不续租。"""
        if not redis_client.is_available() or redis_client._client is None:
            return "OWNER_LEASE_LOST"

        def op() -> str | None:
            script = r'''
            local lease = redis.call('get', KEYS[1])
            local raw = redis.call('get', KEYS[2])
            if not lease or lease ~= ARGV[1] or not raw then return 3 end
            local ok, run = pcall(cjson.decode, raw)
            if not ok or type(run) ~= 'table' then return 3 end
            if run['cancel_requested'] == true or run['state'] == 'CANCELLING' then return 2 end
            local state = tostring(run['state'] or '')
            if state == 'FINALIZING' or state == 'SUCCEEDED' or state == 'FAILED' or
               state == 'TIMED_OUT' or state == 'EXPIRED' or state == 'CANCELLED' then return 3 end
            if tostring(run['owner_token'] or '') ~= ARGV[1] then return 3 end
            if tonumber(run['owner_expires_at'] or 0) <= tonumber(ARGV[2]) then return 3 end
            return 1
            '''
            try:
                result = redis_client._client.eval(
                    script, 2, self._owner_key(tenant_id, run_id),
                    self._run_key(tenant_id, run_id), owner_token, now,
                )
            except Exception:
                return "OWNER_LEASE_LOST"
            if result == 1:
                return None
            return "CANCELLED" if result == 2 else "OWNER_LEASE_LOST"

        return await asyncio.to_thread(op)

    async def request_cancel(self, tenant_id: str, run_id: str) -> bool:
        def op():
            if not redis_client.is_available():
                return False
            key = self._run_key(tenant_id, run_id)
            token = f"cancel:{time.time_ns()}"
            lock = key + ":cas"
            if not redis_client.acquire_lock(lock, token, ex=5):
                return False
            try:
                value = redis_client.get(key)
                if not value:
                    return False
                record = RunRecord.model_validate(value).model_copy(update={"cancel_requested": True})
                redis_client.set(key, record.model_dump(mode="json"), ex=self.run_ttl)
                if not redis_client.is_available():
                    return False
                stored = redis_client.get(key)
                return bool(stored and stored.get("cancel_requested") is True)
            finally:
                redis_client.release_lock(lock, token)
        return await asyncio.to_thread(op)

    async def list_expired(self, now: float) -> list[RunRecord]:
        def op():
            if not redis_client.is_available():
                return []
            pattern = redis_client.make_key(CacheKeys.BROWSER_RUN, "*")
            cursor, keys = 0, []
            while True:
                cursor, batch = redis_client.scan(cursor, pattern, 100)
                keys.extend(batch)
                if cursor == 0:
                    break
            records = []
            for key in keys:
                value = redis_client.get(key)
                if value:
                    try:
                        record = RunRecord.model_validate(value)
                    except Exception:
                        # browser_run:* 同时包含短期 :create/:cas 锁键。
                        continue
                    if record.owner_id and (record.owner_expires_at or 0) <= now:
                        records.append(record)
            return records
        return await asyncio.to_thread(op)


def create_run_store() -> BrowserRunStore:
    """Redis 不可用时显式降级为单请求内存存储。"""
    if redis_client.is_available():
        try:
            return RedisRunStore()
        except RuntimeError:
            pass
    return InMemoryRunStore()
