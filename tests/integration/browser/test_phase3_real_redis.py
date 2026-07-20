"""Phase 3 真实 Redis 门禁。

仅在显式设置 BROWSER_PHASE3_REAL_REDIS=1 时运行，禁止用内存 fallback
伪造 consumer/TTL/跨 worker claim 验收。
"""

import asyncio
import os
import time
import uuid

import pytest

from src.core.cache_utils import CacheKeys
from src.core.redis_client import redis_client
from src.tools.browser.resume_store import AssistanceRecord, ResumeStore


pytestmark = [pytest.mark.integration, pytest.mark.browser]


def _real_redis_enabled() -> bool:
    return os.getenv("BROWSER_PHASE3_REAL_REDIS") == "1" and bool(
        redis_client.is_available() and redis_client._client is not None
    )


@pytest.mark.skipif(
    not _real_redis_enabled(),
    reason="需要 BROWSER_PHASE3_REAL_REDIS=1 和可用的真实 Redis",
)
@pytest.mark.asyncio
async def test_real_redis_two_workers_claim_once_and_reconnect_by_seq():
    suffix = uuid.uuid4().hex
    tenant_id = f"phase3-{suffix}"
    session_id = f"session-{suffix}"
    assistance_id = f"assist-{suffix}"
    continuation_id = f"continuation-{suffix}"
    record = AssistanceRecord(
        tenant_id=tenant_id,
        user_id="user-a",
        session_id=session_id,
        assistance_id=assistance_id,
        run_id=f"run-{suffix}",
        agent_execution_id=f"exec-{suffix}",
        tool_call_id=f"call-{suffix}",
        continuation_id=continuation_id,
        state="resume_queued",
        reason_code="CAPTCHA_REQUIRED",
        instruction_code="PAGE_VERIFICATION",
        completion_mode="auto_or_confirm",
        expires_at=time.time() + 60,
    )
    store_a = ResumeStore()
    store_b = ResumeStore()
    keys = [
        store_a._assistance_key(tenant_id, assistance_id),
        store_a._session_key(tenant_id, session_id),
        store_a._suspension_key(tenant_id, record.agent_execution_id, record.tool_call_id),
        store_a._events_key(tenant_id, continuation_id),
        redis_client.make_key(
            CacheKeys.BROWSER_RESUME_JOBS, f"claim:{tenant_id}:{assistance_id}"
        ),
    ]
    try:
        assert await store_a.save_suspension(record, 60)
        claims = await asyncio.gather(
            store_a.claim_resume(tenant_id, assistance_id, "worker-a"),
            store_b.claim_resume(tenant_id, assistance_id, "worker-b"),
        )
        assert sorted(claims) == [False, True]

        first = await store_a.append_event(
            tenant_id, continuation_id, {"type": "browser_resume_started"}
        )
        second = await store_b.append_event(
            tenant_id, continuation_id, {"type": "agent_continuation_started"}
        )
        third = await store_a.append_event(
            tenant_id, continuation_id, {"type": "agent_continuation_completed"}
        )
        assert [first["seq"], second["seq"], third["seq"]] == [1, 2, 3]
        assert await store_b.events_after(tenant_id, continuation_id, 1) == [second, third]

        assert redis_client.ttl(keys[0]) > 0
        assert redis_client.ttl(keys[1]) > 0
        assert redis_client.ttl(keys[2]) > 0
        assert redis_client.ttl(keys[3]) > 0
    finally:
        for key in keys:
            redis_client.delete(key)
