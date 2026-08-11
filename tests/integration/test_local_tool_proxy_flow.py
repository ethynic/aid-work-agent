"""LocalToolProxy 全链路集成测试（真实 DB，不可用时 skip；直接驱动 proxy，不经 LLM）

覆盖（对应 m05-implementation-spec.md §7）：
- 全链路：建设备（在线）→ proxy.execute(boss_goto) → fake Runtime 线程走
  claim/started/progress/result → proxy 返回成功且 progress 队列收到事件
- 取消：proxy 执行中 request_cancel → fake Runtime 在 progress 响应感知 cancel
  → 写终态 → proxy 返回 CANCELLED
- 超时：缩短 timeout 注入 → proxy 返回 TIMEOUT 且 invocation 落 cancelled
"""

import threading
import time
import uuid

import pytest

from src.local_tools import repository
from src.local_tools.proxy_tool import BossGotoTool
from src.local_tools.security import generate_claim_token, sha256_hex

pytestmark = pytest.mark.integration

PROVIDER_ID = "ai.aidwork.boss-recruiting"


@pytest.fixture(scope="module")
def tenant_user():
    """临时租户 + 用户，测试后物理清理"""
    from src.saas.db.tenant_db import TenantDB
    from src.db.models import UserDB

    code = f"T{uuid.uuid4().hex[:6].upper()}"
    tenant = TenantDB.create(
        company_name=f"proxy测试-{code}",
        tenant_code=code,
        contact_name="测试",
        contact_phone="13800000000",
    )
    if not tenant:
        pytest.skip("无法创建测试租户（DB 不可用）")
    user = UserDB.create(
        phone=f"198{uuid.uuid4().int % 10**8:08d}",
        username="proxy测试用户",
        tenant_id=tenant["tenant_id"],
    )
    if not user:
        pytest.skip("无法创建测试用户（DB 不可用）")

    yield {"tenant_id": tenant["tenant_id"], "user_id": user["user_id"]}

    from src.db.database import get_db_connection
    TenantDB.delete(tenant["tenant_id"])
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            for table in ("local_tool_events", "local_tool_invocations",
                          "local_tool_devices", "local_tool_pairing_tickets"):
                cur.execute(f"DELETE FROM {table} WHERE tenant_id = %s", (tenant["tenant_id"],))
            cur.execute("DELETE FROM tokens WHERE user_id = %s", (user["user_id"],))
            cur.execute("DELETE FROM users WHERE user_id = %s", (user["user_id"],))
            cur.execute("DELETE FROM tenants WHERE tenant_id = %s", (tenant["tenant_id"],))
            conn.commit()
    except Exception:
        pass


@pytest.fixture()
def device(tenant_user):
    """创建 + 选定一台在线设备（create_device 自带 last_seen_at=NOW()）"""
    dev = repository.create_device(
        tenant_user["tenant_id"],
        tenant_user["user_id"],
        token_hash=sha256_hex(uuid.uuid4().hex),
        name="proxy测试设备",
        platform="windows",
        capabilities={"provider_id": PROVIDER_ID},
    )
    ok = repository.select_device(tenant_user["tenant_id"], tenant_user["user_id"], str(dev["id"]))
    assert ok
    return dev


class FakeRuntime(threading.Thread):
    """模拟本机 Runtime：claim → started → progress×N → result（cancel_aware 时感知取消）"""

    def __init__(self, device_id, tenant_id, *, events=(), succeed=True, cancel_aware=False):
        super().__init__(daemon=True)
        self.device_id = device_id
        self.tenant_id = tenant_id
        self.events_spec = list(events)
        self.succeed = succeed
        self.cancel_aware = cancel_aware
        self.claimed = threading.Event()

    def run(self):
        claim_token = generate_claim_token()
        claim_hash = sha256_hex(claim_token)
        invocation = None
        deadline = time.time() + 30
        while time.time() < deadline:
            invocation = repository.claim_next(self.device_id, self.tenant_id, claim_hash, 60)
            if invocation:
                break
            time.sleep(0.2)
        if not invocation:
            return
        inv_id = str(invocation["id"])
        self.claimed.set()
        repository.mark_started(inv_id, self.tenant_id, claim_hash)

        cancelled = False
        for event in self.events_spec:
            result = repository.append_event(
                inv_id, self.tenant_id, claim_hash,
                event.get("stage"), event.get("current"), event.get("total"),
                event.get("message"), 60,
            )
            if result and result[1]:
                cancelled = True
                break
            time.sleep(0.2)

        if self.cancel_aware and not cancelled:
            # 持续上报进度，直到在响应里看到 cancel=true
            deadline = time.time() + 15
            while time.time() < deadline:
                result = repository.append_event(
                    inv_id, self.tenant_id, claim_hash, "poll", None, None, "执行中", 60,
                )
                if result and result[1]:
                    cancelled = True
                    break
                if not result:
                    break  # 状态不再允许上报（已终态）
                time.sleep(0.3)

        if cancelled:
            repository.write_result(
                inv_id, self.tenant_id, claim_hash, False, "CANCELLED", "用户取消了操作", "none"
            )
        elif self.succeed:
            repository.write_result(
                inv_id, self.tenant_id, claim_hash, True, None, "完成", "applied", {"ok": True}
            )
        else:
            repository.write_result(
                inv_id, self.tenant_id, claim_hash, False, "UI_CHANGED", "页面结构变化", "unknown"
            )


def _proxy_kwargs(tenant_user):
    return {
        "_trusted_tenant_id": tenant_user["tenant_id"],
        "_trusted_user_id": tenant_user["user_id"],
    }


class TestProxyFullFlow:
    async def test_full_flow_with_progress_events(self, tenant_user, device):
        """全链路：proxy 下发 → fake Runtime 执行成功 → proxy 成功且进度队列收到事件"""
        import asyncio

        runtime = FakeRuntime(
            str(device["id"]),
            tenant_user["tenant_id"],
            events=[
                {"stage": "goto", "current": 1, "total": 3, "message": "招呼进度"},
                {"stage": "goto", "current": 2, "total": 3, "message": "招呼进度"},
                {"stage": "goto", "current": 3, "total": 3, "message": "招呼进度"},
            ],
            succeed=True,
        )
        runtime.start()

        queue = asyncio.Queue()
        result = await BossGotoTool().execute(
            target="recommend", _progress_queue=queue, **_proxy_kwargs(tenant_user)
        )
        runtime.join(timeout=10)

        assert result["success"] is True, result
        assert result["effect"] == "applied"
        assert result["data"] == {"ok": True}
        assert result["invocation_id"]

        drained = []
        while not queue.empty():
            drained.append(queue.get_nowait())
        assert drained[0]["type"] == "started"
        texts = [e["text"] for e in drained if e.get("text")]
        assert any("1/3" in t for t in texts), texts
        assert any("3/3" in t for t in texts), texts

    async def test_cancel_mid_execution(self, tenant_user, device):
        """proxy 执行中 request_cancel → Runtime 感知 → 终态 → proxy 返回 CANCELLED"""
        import asyncio

        runtime = FakeRuntime(
            str(device["id"]), tenant_user["tenant_id"], cancel_aware=True, succeed=True,
        )
        runtime.start()

        queue = asyncio.Queue()
        task = asyncio.create_task(
            BossGotoTool().execute(
                target="chat", _progress_queue=queue, **_proxy_kwargs(tenant_user)
            )
        )

        # 等 fake Runtime claim 到 invocation，再请求取消
        claimed = await asyncio.to_thread(runtime.claimed.wait, 30)
        assert claimed, "fake Runtime 未 claim 到 invocation"
        started_evt = await asyncio.wait_for(queue.get(), timeout=10)
        assert started_evt["type"] == "started"
        invocation_id = started_evt["invocation_id"]

        ok = await asyncio.to_thread(
            repository.request_cancel, invocation_id, tenant_user["tenant_id"]
        )
        assert ok

        result = await asyncio.wait_for(task, timeout=30)
        runtime.join(timeout=10)
        assert result["success"] is False, result
        assert result["code"] == "CANCELLED"

    async def test_timeout_without_runtime(self, tenant_user, device):
        """无 Runtime 领取：超时 → TIMEOUT + request_cancel 落 cancelled 终态"""
        tool = BossGotoTool()
        tool.timeout_seconds = 2  # 缩短超时注入

        result = await tool.execute(target="recommend", **_proxy_kwargs(tenant_user))
        assert result["success"] is False
        assert result["code"] == "TIMEOUT"

        invocation = repository.get_invocation(result["invocation_id"], tenant_user["tenant_id"])
        assert invocation["state"] == "cancelled"  # queued 被取消直接落终态

    async def test_device_gate_no_invocation(self, tenant_user, device):
        """设备离线（last_seen 拨回 2 分钟）→ DEVICE_UNAVAILABLE，不建 invocation"""
        from src.db.database import get_db_connection
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE local_tool_devices SET last_seen_at = NOW() - INTERVAL '2 minutes' WHERE id = %s",
                (str(device["id"]),),
            )
            conn.commit()

        result = await BossGotoTool().execute(target="recommend", **_proxy_kwargs(tenant_user))
        assert result["success"] is False
        assert result["code"] == "DEVICE_UNAVAILABLE"
        assert "离线" in result["message"]
