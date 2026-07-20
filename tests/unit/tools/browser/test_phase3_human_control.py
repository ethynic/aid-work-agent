import asyncio
import hashlib
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.api import browser_runs
from src.tools.browser.agent_resume_coordinator import AgentResumeCoordinator
from src.tools.browser.human_completion_monitor import (
    CompletionObservation, CompletionPredicate, HumanCompletionMonitor,
)
from src.tools.browser.human_control import HumanControlCoordinator, OwnedHumanRuntime
from src.tools.browser.resume_store import AssistanceRecord
from src.tools.browser.resume_store import ResumeStore
from src.tools.browser.view_hub import BrowserFrame, BrowserViewHub


@pytest.mark.asyncio
async def test_view_hub_slow_consumer_receives_latest_only():
    hub = BrowserViewHub()
    subscription = await hub.subscribe("tenant-a", "br_" + "a" * 32)
    for seq in range(1, 101):
        await hub.publish("tenant-a", BrowserFrame(
            run_id="br_" + "a" * 32, seq=seq, jpeg=f"frame-{seq}".encode(),
            width=1280, height=720, captured_at=float(seq),
        ))
    frame = await subscription.next_frame(timeout=0.01)
    assert frame is not None
    assert frame.seq == 100
    assert frame.jpeg == b"frame-100"
    await subscription.close()


@pytest.mark.asyncio
async def test_view_hub_is_tenant_isolated():
    hub = BrowserViewHub()
    run_id = "br_" + "b" * 32
    own = await hub.subscribe("tenant-a", run_id)
    other = await hub.subscribe("tenant-b", run_id)
    await hub.publish("tenant-a", BrowserFrame(run_id, 1, b"jpeg", 10, 10, 1.0))
    assert (await own.next_frame(timeout=0.01)).jpeg == b"jpeg"
    assert await other.next_frame(timeout=0.01) is None


def test_completion_predicate_rejects_query_and_unknown_type():
    with pytest.raises(ValueError):
        CompletionPredicate(type="url_origin_path_matches", value="https://example.com/path?token=x")
    with pytest.raises(ValueError):
        CompletionPredicate(type="free_javascript", value="true")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_completion_requires_two_stable_samples():
    monitor = HumanCompletionMonitor(sample_interval=0)
    calls = 0

    async def sampler():
        nonlocal calls
        calls += 1
        return CompletionObservation(
            origin_path="https://example.com/success",
            present_elements=frozenset({"main"}),
            challenge_iframe_present=False,
        )

    met, missing = await monitor.stable((
        CompletionPredicate(type="challenge_iframe_absent", value="absent"),
        CompletionPredicate(type="element_present", value="main"),
    ), sampler)
    assert met is True
    assert missing == []
    assert calls == 2


@pytest.mark.asyncio
async def test_completion_stops_on_unmet_first_sample():
    monitor = HumanCompletionMonitor(sample_interval=0)

    async def sampler():
        return CompletionObservation(
            origin_path="https://example.com/login", challenge_iframe_present=True
        )

    met, missing = await monitor.stable((
        CompletionPredicate(type="challenge_iframe_absent", value="absent"),
    ), sampler)
    assert met is False
    assert missing == ["challenge_iframe_absent"]


def test_agent_runtime_treats_suspension_as_control_flow():
    source = Path("src/core/agent.py").read_text(encoding="utf-8")
    suspension_branch = source[source.index("if isinstance(result, ToolSuspension)"):]
    suspension_branch = suspension_branch[:suspension_branch.index("# 发送工具执行完成事件")]
    assert "yield result.event" in suspension_branch
    assert "return" in suspension_branch
    assert "tool_results.append" not in suspension_branch
    assert "mark_task_completed" not in suspension_branch


def test_pointer_and_keyboard_are_guarded_by_running_human():
    source = Path("src/api/browser_runs.py").read_text(encoding="utf-8")
    assert 'store_record.state != "RUNNING_HUMAN"' in source
    assert "runtime.executor.keyboard" in source
    assert "runtime.executor.pointer" in source


def test_phase4_remote_executor_not_implemented_early():
    assert not Path("src/tools/browser/executor/remote.py").exists()


class _TicketRedis:
    def __init__(self, value):
        self.value = value

    @staticmethod
    def make_key(prefix, identifier):
        return f"{prefix}:{identifier}"

    def acquire_lock(self, key, value, ex=60):
        return True

    def get(self, key):
        return self.value

    def delete(self, key):
        self.value = None

    def release_lock(self, key, value):
        return True


@pytest.mark.asyncio
async def test_view_ticket_is_bearer_authenticated_and_consumed_once(monkeypatch):
    secret = "unpredictable-secret-value-with-32-bytes"
    fake_redis = _TicketRedis({
        "ticket_hash": hashlib.sha256(secret.encode()).hexdigest(),
        "tenant_id": "tenant-a", "user_id": "user-a", "run_id": "run-a",
    })
    monkeypatch.setattr(browser_runs, "redis_client", fake_redis)

    # 浏览器 WebSocket 不能附带 Authorization header，身份必须来自一次性 ticket。
    ticket = f"{'a' * 32}.{secret}"
    identity = await browser_runs._consume_ticket(object(), "run-a", ticket)
    assert identity == ("tenant-a", "user-a")
    assert await browser_runs._consume_ticket(object(), "run-a", ticket) is None


@pytest.mark.asyncio
async def test_assistance_action_rejects_assistance_from_another_run(monkeypatch):
    route_record = SimpleNamespace(run_id="run-a")
    assistance = SimpleNamespace(tenant_id="tenant-a", user_id="user-a", run_id="run-b")
    called = False

    class Store:
        async def get_assistance(self, tenant_id, assistance_id):
            return assistance

    class Coordinator:
        store = Store()

        async def take_control(self, *args):
            nonlocal called
            called = True

    async def authorize(request, run_id):
        return "tenant-a", "user-a", route_record

    monkeypatch.setattr(browser_runs, "_authorize_run", authorize)
    monkeypatch.setattr(browser_runs, "HumanControlCoordinator", Coordinator)
    with pytest.raises(HTTPException) as exc:
        await browser_runs._coordinator_call(object(), "run-a", "assist-b", "take_control")
    assert exc.value.status_code == 404
    assert called is False


def _assistance(**updates):
    values = {
        "tenant_id": "tenant-a", "user_id": "user-a", "session_id": "session-a",
        "assistance_id": "assist-a", "run_id": "run-a", "agent_execution_id": "exec-a",
        "tool_call_id": "call-a", "continuation_id": "continuation-a",
        "state": "controlling", "reason_code": "CAPTCHA_REQUIRED",
        "instruction_code": "PAGE_VERIFICATION", "completion_mode": "auto_or_confirm",
        "predicates": ({"type": "challenge_iframe_absent", "value": "absent"},),
        "expires_at": time.time() + 300,
    }
    values.update(updates)
    return AssistanceRecord(**values)


@pytest.mark.asyncio
async def test_manual_complete_marks_auto_mode_as_completed_by_human(monkeypatch):
    record = _assistance()

    class Store:
        async def get_assistance(self, tenant_id, assistance_id):
            return record

        async def cas_state(self, tenant_id, assistance_id, expected, state, **updates):
            nonlocal record
            if record.state not in expected:
                return None
            record = record.model_copy(update={"state": state, **updates})
            return record

        async def enqueue_resume(self, queued, job_id):
            return True

    class PageOps:
        async def take_snapshot(self):
            return {"success": True, "url": "https://example.com/done", "interactive_elements": []}

    class Manager:
        async def transition(self, *args):
            return None

    class RunDB:
        async def update_assistance_state(self, *args):
            raise RuntimeError("audit unavailable")

    runtime = OwnedHumanRuntime(Manager(), SimpleNamespace(page_ops=PageOps()), object())
    monkeypatch.setattr(
        "src.tools.browser.human_control.get_owned_runtime",
        lambda tenant_id, run_id: asyncio.sleep(0, result=runtime),
    )
    coordinator = HumanControlCoordinator(
        store=Store(), run_db=RunDB(), monitor=HumanCompletionMonitor(sample_interval=0)
    )
    queued, missing = await coordinator.complete("tenant-a", "user-a", "assist-a")
    assert missing == []
    assert queued.state == "resume_queued"
    assert queued.completed_by_human is True


@pytest.mark.asyncio
async def test_take_control_context_lost_clears_session_gate(monkeypatch):
    record = _assistance(state="pending")

    class Store:
        def __init__(self):
            self.cleared = False

        async def get_assistance(self, tenant_id, assistance_id):
            return record

        async def get_active_session(self, tenant_id, session_id):
            return {"assistance_id": record.assistance_id}

        async def cas_state(self, *args, **kwargs):
            return record.model_copy(update={"state": "failed"})

        async def clear(self, item):
            self.cleared = True

    store = Store()
    monkeypatch.setattr(
        "src.tools.browser.human_control.get_owned_runtime",
        lambda tenant_id, run_id: asyncio.sleep(0, result=None),
    )
    coordinator = HumanControlCoordinator(store=store, run_db=object())
    with pytest.raises(RuntimeError, match="RESUME_CONTEXT_LOST"):
        await coordinator.take_control("tenant-a", "user-a", "assist-a")
    assert store.cleared is True


@pytest.mark.asyncio
async def test_cancel_does_not_touch_run_after_state_was_consumed(monkeypatch):
    record = _assistance(state="resume_queued")

    class Store:
        async def get_assistance(self, *args):
            return record

        async def get_active_session(self, *args):
            return {"assistance_id": record.assistance_id}

        async def cas_state(self, *args, **kwargs):
            return None

    async def unexpected_runtime(*args):
        raise AssertionError("CAS 失败后不得再操作 run runtime")

    monkeypatch.setattr(
        "src.tools.browser.human_control.get_owned_runtime", unexpected_runtime
    )
    coordinator = HumanControlCoordinator(store=Store(), run_db=object())
    with pytest.raises(RuntimeError, match="RESUME_ALREADY_CONSUMED"):
        await coordinator.cancel("tenant-a", "user-a", "assist-a")


@pytest.mark.asyncio
async def test_resume_context_lost_emits_terminal_event_and_clears_gate(monkeypatch):
    record = _assistance(state="resume_queued")

    class Store:
        def __init__(self):
            self.claimed = False
            self.events = []
            self.cleared = False

        async def claim_resume(self, tenant_id, assistance_id, consumer_id):
            if self.claimed:
                return False
            self.claimed = True
            return True

        async def get_assistance(self, tenant_id, assistance_id):
            return record

        async def cas_state(self, *args, **kwargs):
            return record.model_copy(update={"state": "failed"})

        async def append_event(self, tenant_id, continuation_id, event):
            self.events.append(event)
            return event

        async def clear(self, item):
            self.cleared = True

    store = Store()
    monkeypatch.setattr(
        "src.tools.browser.agent_resume_coordinator.get_owned_runtime",
        lambda tenant_id, run_id: asyncio.sleep(0, result=None),
    )
    coordinator = AgentResumeCoordinator(store=store, consumer_id="consumer-a")
    first = await coordinator.resume("tenant-a", "assist-a")
    second = await coordinator.resume("tenant-a", "assist-a")
    assert first == {"success": False, "error_code": "RESUME_CONTEXT_LOST"}
    assert second == {"success": False, "error_code": "RESUME_ALREADY_CONSUMED"}
    assert store.events == [{"type": "browser_run_closed", "error_code": "RESUME_CONTEXT_LOST"}]
    assert store.cleared is True


@pytest.mark.asyncio
async def test_resume_events_do_not_persist_browser_result_body(monkeypatch):
    record = _assistance(state="resume_queued")

    class Store:
        def __init__(self):
            self.events = []

        async def claim_resume(self, *args):
            return True

        async def get_assistance(self, *args):
            return record

        async def append_event(self, tenant_id, continuation_id, event):
            self.events.append(event)
            return event

        async def cas_state(self, *args, **kwargs):
            return record.model_copy(update={"state": "resumed"})

        async def clear(self, item):
            return None

    class Orchestrator:
        async def resume_from_human(self, **kwargs):
            return {"success": True, "result": "sensitive page body"}

    store = Store()
    runtime = OwnedHumanRuntime(object(), Orchestrator(), object())
    monkeypatch.setattr(
        "src.tools.browser.agent_resume_coordinator.get_owned_runtime",
        lambda tenant_id, run_id: asyncio.sleep(0, result=runtime),
    )
    result = await AgentResumeCoordinator(store=store).resume("tenant-a", "assist-a")
    assert result["success"] is True
    assert "sensitive page body" not in repr(store.events)
    assert any(event["type"] == "tool_result" for event in store.events)


class _MemoryRedis:
    def __init__(self):
        self.values = {}
        self.locks = {}
        self.guard = threading.Lock()

    @staticmethod
    def is_available():
        return True

    @staticmethod
    def make_key(prefix, identifier):
        return f"{prefix}:{identifier}"

    def acquire_lock(self, key, value, ex=60):
        with self.guard:
            if key in self.locks:
                return False
            self.locks[key] = value
            return True

    def release_lock(self, key, value):
        with self.guard:
            if self.locks.get(key) != value:
                return False
            self.locks.pop(key)
            return True

    def get(self, key):
        with self.guard:
            return self.values.get(key)

    def set(self, key, value, ex=None):
        with self.guard:
            self.values[key] = value

    def delete(self, key):
        with self.guard:
            self.values.pop(key, None)


@pytest.mark.asyncio
async def test_same_session_allows_only_one_suspension(monkeypatch):
    fake_redis = _MemoryRedis()
    monkeypatch.setattr("src.tools.browser.resume_store.redis_client", fake_redis)
    store = ResumeStore()
    first = _assistance(assistance_id="assist-1", tool_call_id="call-1")
    second = _assistance(assistance_id="assist-2", tool_call_id="call-2")

    results = await asyncio.gather(
        store.save_suspension(first, 300), store.save_suspension(second, 300)
    )
    assert sorted(results) == [False, True]
    active = await store.get_active_session("tenant-a", "session-a")
    assert active["assistance_id"] in {"assist-1", "assist-2"}


@pytest.mark.asyncio
async def test_replacing_suspension_has_no_session_gate_gap(monkeypatch):
    fake_redis = _MemoryRedis()
    monkeypatch.setattr("src.tools.browser.resume_store.redis_client", fake_redis)
    store = ResumeStore()
    first = _assistance(assistance_id="assist-1")
    second = _assistance(assistance_id="assist-2", continuation_id="continuation-2")

    assert await store.save_suspension(first, 300) is True
    assert await store.replace_suspension(first, second, 300) is True
    assert (await store.get_active_session("tenant-a", "session-a"))["assistance_id"] == "assist-2"

    # 迟到的旧 assistance 清理不能误删新 gate。
    await store.clear(first)
    assert (await store.get_active_session("tenant-a", "session-a"))["assistance_id"] == "assist-2"


@pytest.mark.asyncio
async def test_concurrent_complete_queues_resume_once(monkeypatch):
    record = _assistance()

    class Store:
        def __init__(self):
            self.lock = asyncio.Lock()
            self.enqueue_count = 0

        async def get_assistance(self, tenant_id, assistance_id):
            return record

        async def cas_state(self, tenant_id, assistance_id, expected, state, **updates):
            nonlocal record
            async with self.lock:
                if record.state not in expected:
                    return None
                record = record.model_copy(update={"state": state, **updates})
                return record

        async def enqueue_resume(self, queued, job_id):
            self.enqueue_count += 1
            return True

    class PageOps:
        async def take_snapshot(self):
            await asyncio.sleep(0)
            return {"success": True, "url": "https://example.com/done", "interactive_elements": []}

    class Manager:
        async def transition(self, *args):
            return None

    class RunDB:
        async def update_assistance_state(self, *args):
            return True

    store = Store()
    runtime = OwnedHumanRuntime(Manager(), SimpleNamespace(page_ops=PageOps()), object())
    monkeypatch.setattr(
        "src.tools.browser.human_control.get_owned_runtime",
        lambda tenant_id, run_id: asyncio.sleep(0, result=runtime),
    )
    coordinator = HumanControlCoordinator(
        store=store, run_db=RunDB(), monitor=HumanCompletionMonitor(sample_interval=0)
    )
    outcomes = await asyncio.gather(
        coordinator.complete("tenant-a", "user-a", "assist-a"),
        coordinator.complete("tenant-a", "user-a", "assist-a"),
        return_exceptions=True,
    )
    assert sum(not isinstance(item, Exception) for item in outcomes) == 1
    assert sum(isinstance(item, RuntimeError) for item in outcomes) == 1
    assert store.enqueue_count == 1
