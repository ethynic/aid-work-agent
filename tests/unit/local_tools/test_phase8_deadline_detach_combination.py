"""Phase 8 组合验收（组合 3，Phase 4）：>180s 健康执行 + 前端断开再订阅 + 旧 deadline 存量兼容。

计划文档 Phase 8 重点组合第 3 条，三段组合进一个连续场景：
1. 超过 180 秒的健康执行（执行归属有效、租约活）不进错误验证态——用可控
   FakeLoop 时钟让轮询墙钟真实越过 timeout_seconds=180 观察窗（断言未放大）；
2. 前端断开（订阅协程取消）不取消 invocation；重连（再次订阅同一 saved phase）
   复用原 invocation，不重发（bind 零调用）；
3. 旧 deadline 存量兼容：行上已保存的过期持久 deadline 仍投影为
   LOCAL_DEADLINE_VERIFICATION_REQUIRED（既有持久事实不忽略）。

mock repository（无真实 DB，集成层缺口见会话结果）。
"""

import asyncio
import contextlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.local_tools.durable_flow import LocalContinuationRequired
from src.local_tools.overlay_flow import poll_operation
from src.local_tools.proxy_tool import BossGotoTool

pytestmark = pytest.mark.unit

TENANT = "tenant_p8c"
USER = "user_p8c"
UTC = timezone.utc
REPO = "src.local_tools.proxy_tool.repository"
ARGS = {"target": "recommend"}


class FakeLoop:
    """可控时钟：替代 _poll_invocation 内 asyncio.get_event_loop() 的 loop.time()"""

    def __init__(self, start=1000.0, step=100.0):
        self._now = start
        self._step = step

    def advance(self):
        self._now += self._step

    def time(self):
        return self._now


def _running(**extra):
    return {"id": "inv-p8c", "state": "running", "effect": None, "result_json": {},
            "deadline_at": None, "credit_cost": None, **extra}


def _succeeded(**extra):
    return {"id": "inv-p8c", "state": "succeeded", "effect": "applied",
            "result_json": {"success": True, "message": "完成", "data": {}},
            "deadline_at": None, "credit_cost": None, **extra}


def _repo_mocks(get_invocation, *, lease_stale=False):
    mocks = dict(
        list_events=MagicMock(return_value=[]),
        get_invocation=MagicMock(side_effect=get_invocation),
        claim_lease_expired=MagicMock(return_value=lease_stale),
        expire_stale_claims=MagicMock(return_value=1),
        request_cancel=MagicMock(return_value=True),
        observed_invocation=MagicMock(side_effect=lambda inv: inv),
    )
    return patch.multiple(REPO, **mocks), mocks


def _saved_phase(invocation, deadline_iso=None):
    return {"request": {"arguments": dict(ARGS), "tool_name": "boss_goto",
                        "device_id": "dev-p8c", "deadline_at": deadline_iso},
            "invocation": invocation}


@pytest.fixture(autouse=True)
def _fast_poll(monkeypatch):
    monkeypatch.setattr("src.local_tools.proxy_tool.POLL_INTERVAL_SECONDS", 0.01)


@pytest.mark.asyncio
async def test_combo3_beyond_180s_healthy_then_detach_reconnect_then_legacy_deadline_projection():
    """一段连续场景：健康执行越过 180s 墙钟 → 前端断开不取消 → 重连复用原调用
    → 旧 deadline 存量投影仍进核验家族。"""
    tool = BossGotoTool()
    assert tool.timeout_seconds == 180, '语义拆除而非数字调整：timeout 保持 180'

    fake_loop = FakeLoop(start=1000.0, step=100.0)
    terminal = {"done": False}

    def get_invocation(_inv_id, _tenant):
        # 每次读取推进时钟 100 秒：观察窗若仍在，deadline=1000+180=1180 早已越过
        fake_loop.advance()
        return _succeeded() if terminal["done"] else _running()

    owner = SimpleNamespace(
        saved_phase=AsyncMock(return_value=_saved_phase(_running())),
        bind_invocation=AsyncMock(side_effect=AssertionError("重连不得重新绑定/重发 invocation")))
    operation = SimpleNamespace(owner=owner, branch="main", ordinal=0)

    patches, mocks = _repo_mocks(get_invocation, lease_stale=False)
    with patches, patch("asyncio.get_event_loop", return_value=fake_loop):
        # —— 第一段：健康执行观察，墙钟越过 180s 观察窗，不进错误验证态 ——
        task = asyncio.create_task(poll_operation(
            tool, operation, dict(ARGS), tenant_id=TENANT, user_id=USER,
            session_id="sess-p8c", progress_queue=None))
        await asyncio.sleep(0.12)  # ≥5 次轮询：fake 时钟 1000 → 1500+
        assert fake_loop._now > 1180, "墙钟必须真实越过 180 秒观察窗"
        assert not task.done(), "健康执行不得因越过 timeout 而终止/抛验证异常"

        # —— 第二段：前端断开（订阅协程取消）——不请求取消 ——
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        mocks["request_cancel"].assert_not_called()
        assert mocks["claim_lease_expired"].called, "执行健康性以租约活性观察"

        # —— 第三段：重连（再次订阅同一 saved phase）——不重发，等至原调用终态 ——
        terminal["done"] = True
        result = await poll_operation(
            tool, operation, dict(ARGS), tenant_id=TENANT, user_id=USER,
            session_id="sess-p8c", progress_queue=None)

    assert result["success"] is True and result["invocation_id"] == "inv-p8c"
    owner.bind_invocation.assert_not_called()  # 重连不得重新绑定 invocation（不重发）
    assert owner.saved_phase.await_count == 2, "两次订阅均读 saved phase 复用原调用"
    mocks["request_cancel"].assert_not_called()
    assert not mocks["expire_stale_claims"].called, "租约活的健康执行无需清扫"

    # —— 第四段：旧 deadline 存量兼容——过期持久 deadline 仍进核验家族 ——
    expired = datetime.now(UTC) - timedelta(seconds=1)
    legacy_invocation = _running(deadline_at=expired)
    legacy_owner = SimpleNamespace(saved_phase=AsyncMock(return_value=_saved_phase(
        legacy_invocation, deadline_iso=expired.isoformat())))
    patches2, mocks2 = _repo_mocks(lambda *_: legacy_invocation)
    with patches2:
        with pytest.raises(LocalContinuationRequired) as raised:
            await poll_operation(BossGotoTool(), SimpleNamespace(
                owner=legacy_owner, branch="main", ordinal=0), dict(ARGS),
                tenant_id=TENANT, user_id=USER, session_id="sess-p8c", progress_queue=None)
    assert raised.value.code == "LOCAL_DEADLINE_VERIFICATION_REQUIRED"
    mocks2["request_cancel"].assert_not_called()
    mocks2["expire_stale_claims"].assert_not_called()
