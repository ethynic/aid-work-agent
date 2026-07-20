import asyncio
import hashlib
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.api import browser_runs
from src.core.agent import Agent, _preserve_suspension_sibling_results
from src.tools.browser.agent_resume_coordinator import (
    AgentResumeCoordinator, BrowserResumeWorker,
)
from src.tools.browser.human_completion_monitor import (
    CompletionObservation, CompletionPredicate, HumanCompletionMonitor,
)
from src.tools.browser.human_control import (
    HumanControlCoordinator, OwnedHumanRuntime, register_owned_runtime,
    unregister_owned_runtime,
)
from src.tools.browser.reaper import HumanAssistanceReaper
from src.tools.browser.resume_store import AssistanceRecord
from src.tools.browser.resume_store import ResumeStore
from src.tools.browser.view_hub import BrowserFrame, BrowserViewHub
from src.tools.browser.worker_main import _contains_challenge_iframe


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


def test_challenge_iframe_detection_uses_visible_snapshot_metadata():
    assert _contains_challenge_iframe([
        {
            "src": "https://example.com/content",
            "nested_iframes": [{"title": "安全验证码"}],
        }
    ]) is True
    assert _contains_challenge_iframe([
        {"src": "https://example.com/content", "title": "embedded report"}
    ]) is False


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


def test_suspension_preserves_executed_siblings_and_defers_pending_calls():
    messages = []

    class Memory:
        def __init__(self):
            self.saved = []

        def add_message(self, session_id, message):
            self.saved.append((session_id, message))

    memory = Memory()
    _preserve_suspension_sibling_results(
        messages,
        memory,
        "session-a",
        [{"tool_call_id": "call-before", "content": {"success": True}}],
        [{"id": "call-after", "name": "later_tool", "arguments": {}}],
    )
    assert [item["tool_call_id"] for item in messages] == [
        "call-before", "call-after",
    ]
    assert messages[0]["content"] == {"success": True}
    assert messages[1]["content"]["error_code"] == (
        "TOOL_DEFERRED_BY_HUMAN_ASSISTANCE"
    )
    assert memory.saved == [
        ("session-a", messages[0]), ("session-a", messages[1]),
    ]


@pytest.mark.asyncio
async def test_agent_continuation_does_not_create_synthetic_user_message():
    agent = object.__new__(Agent)
    captured = {}

    async def process_message(**kwargs):
        captured.update(kwargs)
        yield {"type": "response", "data": "continued"}

    agent.process_message = process_message
    events = [event async for event in agent.continue_tool_call(
        session_id="session-a",
        tool_call_id="call-a",
        result={"success": True},
    )]
    assert events == [{"type": "response", "data": "continued"}]
    assert captured["user_input"] == ""
    assert captured["_continuation_tool_result"] == {
        "tool_call_id": "call-a", "content": {"success": True},
    }


def test_pointer_and_keyboard_are_guarded_by_running_human():
    source = Path("src/api/browser_runs.py").read_text(encoding="utf-8")
    assert 'store_record.state != "RUNNING_HUMAN"' in source
    assert "runtime.executor.keyboard" in source
    assert "runtime.executor.pointer" in source


def test_complete_api_only_enqueues_persistent_resume_job():
    source = Path("src/api/browser_runs.py").read_text(encoding="utf-8")
    branch = source[source.index("async def complete_assistance"):]
    branch = branch[:branch.index("@router.post", 1)]
    assert "create_task" not in branch
    assert "AgentResumeCoordinator" not in branch


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
async def test_non_owner_worker_does_not_claim_or_clear_resume(monkeypatch):
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
    assert first == {"success": False, "error_code": "RESUME_NOT_OWNER"}
    assert second == {"success": False, "error_code": "RESUME_NOT_OWNER"}
    assert store.claimed is False
    assert store.events == []
    assert store.cleared is False


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


@pytest.mark.asyncio
async def test_resume_restarts_agent_once_with_original_tool_call(monkeypatch):
    record = _assistance(state="resume_queued")

    class Store:
        def __init__(self):
            self.state = record
            self.claimed = False
            self.events = []
            self.refreshed = []

        async def get_assistance(self, *args):
            return self.state

        async def claim_resume(self, *args):
            if self.claimed:
                return False
            self.claimed = True
            return True

        async def cas_state(self, tenant_id, assistance_id, expected, state, **updates):
            if self.state.state not in expected:
                return None
            self.state = self.state.model_copy(update={"state": state, **updates})
            return self.state

        async def append_event(self, tenant_id, continuation_id, event):
            self.events.append(event)
            return event

        async def refresh_suspension(self, item, ttl):
            self.refreshed.append((item.state, ttl, item.expires_at))
            return True

        async def clear(self, item):
            return None

    class Orchestrator:
        def __init__(self):
            self.calls = []

        async def resume_from_human(self, **kwargs):
            self.calls.append(kwargs)
            return {"success": True, "result": "done"}

    store = Store()
    orchestrator = Orchestrator()
    runtime = OwnedHumanRuntime(object(), orchestrator, object())
    monkeypatch.setattr(
        "src.tools.browser.agent_resume_coordinator.get_owned_runtime",
        lambda *args: asyncio.sleep(0, result=runtime),
    )
    callbacks = []

    async def continue_agent(item, result):
        callbacks.append((item.tool_call_id, result, item.state))

    coordinator = AgentResumeCoordinator(
        store=store, continuation_callback=continue_agent
    )
    first = await coordinator.resume("tenant-a", "assist-a")
    second = await coordinator.resume("tenant-a", "assist-a")
    assert first["success"] is True
    assert second == {"success": False, "error_code": "RESUME_ALREADY_CONSUMED"}
    assert callbacks == [("call-a", first, "agent_resuming")]
    assert orchestrator.calls == [{"completed_by_human": False, "step_index": 0}]
    assert store.refreshed[0][0] == "agent_resuming"
    assert store.refreshed[0][1] >= 900
    assert store.refreshed[0][2] >= time.time() + 890
    assert sum(e["type"] == "agent_continuation_completed" for e in store.events) == 1


@pytest.mark.asyncio
async def test_repeated_human_suspension_keeps_original_agent_route():
    previous = _assistance(
        state="resumed", agent_name="travel-quote-agent"
    )

    class Store:
        def __init__(self):
            self.replacement = None

        def available(self):
            return True

        async def replace_suspension(self, old, new, ttl):
            assert old is previous
            self.replacement = new
            return True

        async def clear(self, *args):
            return None

    class RunDB:
        async def create_assistance(self, *args):
            return None

    class Manager:
        async def transition(self, *args):
            return None

    store = Store()
    manager = Manager()
    await HumanControlCoordinator(store=store, run_db=RunDB()).suspend(
        tenant_id=previous.tenant_id,
        user_id=previous.user_id,
        session_id=previous.session_id,
        agent_execution_id=previous.agent_execution_id,
        tool_call_id=previous.tool_call_id,
        run_id=previous.run_id,
        manager=manager,
        orchestrator=object(),
        executor=object(),
        replaces=previous,
    )
    try:
        assert store.replacement.agent_name == "travel-quote-agent"
    finally:
        await unregister_owned_runtime(previous.tenant_id, previous.run_id)


@pytest.mark.asyncio
async def test_automatic_completion_queues_and_resumes(monkeypatch):
    record = _assistance(state="pending")
    queued_event = asyncio.Event()

    class Store:
        def __init__(self):
            self.record = record

        async def get_assistance(self, *args):
            return self.record

        async def get_active_session(self, *args):
            return {"assistance_id": self.record.assistance_id}

        async def cas_state(self, tenant_id, assistance_id, expected, state, **updates):
            if self.record.state not in expected:
                return None
            self.record = self.record.model_copy(update={"state": state, **updates})
            return self.record

        async def enqueue_resume(self, *args):
            queued_event.set()
            return True

    class PageOps:
        async def take_snapshot(self):
            return {
                "success": True,
                "url": "https://example.com/done",
                "interactive_elements": [],
            }

    class Manager:
        async def transition(self, *args):
            return None

    class RunDB:
        async def update_assistance_state(self, *args):
            return True

    store = Store()
    runtime = OwnedHumanRuntime(
        Manager(), SimpleNamespace(page_ops=PageOps()), object()
    )
    await register_owned_runtime("tenant-a", "run-a", runtime)
    coordinator = HumanControlCoordinator(
        store=store, run_db=RunDB(), monitor=HumanCompletionMonitor(sample_interval=0)
    )
    await coordinator.take_control("tenant-a", "user-a", "assist-a")
    await asyncio.wait_for(queued_event.wait(), timeout=1)
    assert store.record.state == "resume_queued"
    assert store.record.completed_by_human is True


@pytest.mark.asyncio
async def test_automatic_completion_waits_while_challenge_iframe_is_present(monkeypatch):
    record = _assistance(state="controlling")

    class Store:
        async def get_assistance(self, *args):
            return record

        async def get_active_session(self, *args):
            return {"assistance_id": record.assistance_id}

        async def cas_state(self, *args, **kwargs):
            raise AssertionError("挑战 iframe 存在时不应推进 assistance 状态")

        async def enqueue_resume(self, *args):
            raise AssertionError("挑战 iframe 存在时不应入队续跑")

    class PageOps:
        async def take_snapshot(self):
            return {
                "success": True,
                "url": "https://example.com/challenge",
                "interactive_elements": [],
                "challenge_iframe_present": True,
            }

    runtime = OwnedHumanRuntime(
        object(), SimpleNamespace(page_ops=PageOps()), object()
    )
    monkeypatch.setattr(
        "src.tools.browser.human_control.get_owned_runtime",
        lambda *args: asyncio.sleep(0, result=runtime),
    )
    coordinator = HumanControlCoordinator(
        store=Store(), run_db=object(),
        monitor=HumanCompletionMonitor(sample_interval=0),
    )
    current, missing = await coordinator.complete(
        "tenant-a", "user-a", "assist-a", automatic=True
    )
    assert current is record
    assert missing == ["challenge_iframe_absent"]


@pytest.mark.asyncio
async def test_resume_worker_restart_replays_persistent_job():
    job = SimpleNamespace(tenant_id="tenant-a", assistance_id="assist-a")
    processed = asyncio.Event()

    class Store:
        def __init__(self, expose_job):
            self.expose_job = expose_job
            self.calls = 0

        async def read_resume_jobs(self, after_id, block_ms=1000):
            self.calls += 1
            if self.expose_job and self.calls == 1:
                return [("1-0", job)]
            await asyncio.sleep(60)

    class Coordinator:
        async def resume(self, tenant_id, assistance_id):
            processed.set()
            return {"success": True}

    first = BrowserResumeWorker(
        store=Store(expose_job=False), coordinator_factory=Coordinator
    )
    first.start()
    await asyncio.sleep(0)
    await first.stop()

    restarted = BrowserResumeWorker(
        store=Store(expose_job=True), coordinator_factory=Coordinator
    )
    restarted.start()
    await asyncio.wait_for(processed.wait(), timeout=1)
    await restarted.stop()


@pytest.mark.asyncio
async def test_two_resume_consumers_callback_once(monkeypatch):
    record = _assistance(state="resume_queued")

    class Store:
        def __init__(self):
            self.record = record
            self.claim_lock = asyncio.Lock()
            self.claimed = False

        async def get_assistance(self, *args):
            return self.record

        async def claim_resume(self, *args):
            async with self.claim_lock:
                if self.claimed:
                    return False
                self.claimed = True
                return True

        async def append_event(self, *args):
            return None

        async def cas_state(self, tenant_id, assistance_id, expected, state, **updates):
            if self.record.state not in expected:
                return None
            self.record = self.record.model_copy(update={"state": state, **updates})
            return self.record

        async def clear(self, *args):
            return None

    class Orchestrator:
        async def resume_from_human(self, **kwargs):
            await asyncio.sleep(0)
            return {"success": True}

    store = Store()
    runtime = OwnedHumanRuntime(object(), Orchestrator(), object())
    monkeypatch.setattr(
        "src.tools.browser.agent_resume_coordinator.get_owned_runtime",
        lambda *args: asyncio.sleep(0, result=runtime),
    )
    callback_count = 0

    async def callback(*args):
        nonlocal callback_count
        callback_count += 1

    results = await asyncio.gather(
        AgentResumeCoordinator(
            store=store, consumer_id="worker-a", continuation_callback=callback
        ).resume("tenant-a", "assist-a"),
        AgentResumeCoordinator(
            store=store, consumer_id="worker-b", continuation_callback=callback
        ).resume("tenant-a", "assist-a"),
    )
    assert callback_count == 1
    assert sum(result.get("success") is True for result in results) == 1


@pytest.mark.asyncio
async def test_human_timeout_reaper_claims_once_and_closes_same_runtime(monkeypatch):
    record = _assistance(state="controlling", expires_at=time.time() - 1)

    class Store:
        def __init__(self):
            self.record = record
            self.events = []
            self.cleared = 0

        async def list_expired_assistance(self, now):
            return [self.record] if self.record.state == "controlling" else []

        async def cas_state(self, tenant_id, assistance_id, expected, state, **updates):
            if self.record.state not in expected:
                return None
            self.record = self.record.model_copy(update={"state": state})
            return self.record

        async def append_event(self, tenant_id, continuation_id, event):
            self.events.append(event)

        async def clear(self, item):
            self.cleared += 1

    class Manager:
        def __init__(self):
            self.finalized = []
            self.store = SimpleNamespace(distributed=True)

        async def finalize(self, *args):
            self.finalized.append(args)

        async def request_cancel(self, *args):
            raise AssertionError("本 owner runtime 不应走跨 worker 取消")

    store = Store()
    manager = Manager()
    runtime = OwnedHumanRuntime(manager, object(), object())
    await register_owned_runtime("tenant-a", "run-a", runtime)
    monkeypatch.setattr("src.tools.browser.resume_store.ResumeStore", lambda: store)
    reaper = HumanAssistanceReaper(manager)
    assert await reaper.reap_once() == 1
    assert await reaper.reap_once() == 0
    assert manager.finalized[0][-1] == "human_timeout"
    assert store.events == [{"type": "browser_run_closed", "error_code": "HUMAN_TIMEOUT"}]


@pytest.mark.asyncio
async def test_human_timeout_reaper_does_not_expire_concurrently_extended_lease(monkeypatch):
    scanned = _assistance(state="controlling", expires_at=time.time() - 1)
    extended = scanned.model_copy(update={"expires_at": time.time() + 300})

    class Store:
        async def list_expired_assistance(self, now):
            return [scanned]

        async def cas_state(
            self, tenant_id, assistance_id, expected, state,
            expected_expires_at=None, **updates,
        ):
            assert expected_expires_at == scanned.expires_at
            if extended.expires_at != expected_expires_at:
                return None
            raise AssertionError("已延期记录不应进入 expired")

    class Manager:
        async def request_cancel(self, *args):
            raise AssertionError("已延期记录不应请求取消")

    monkeypatch.setattr("src.tools.browser.resume_store.ResumeStore", lambda: Store())
    assert await HumanAssistanceReaper(Manager()).reap_once() == 0


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

    def expire(self, key, ttl):
        with self.guard:
            return key in self.values


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
async def test_continuation_reconnect_fetches_only_events_after_last_seq(monkeypatch):
    fake_redis = _MemoryRedis()
    monkeypatch.setattr("src.tools.browser.resume_store.redis_client", fake_redis)
    store = ResumeStore()
    first = await store.append_event(
        "tenant-a", "continuation-a", {"type": "browser_resume_started"}
    )
    second = await store.append_event(
        "tenant-a", "continuation-a", {"type": "agent_continuation_started"}
    )
    third = await store.append_event(
        "tenant-a", "continuation-a", {"type": "agent_continuation_completed"}
    )
    assert [first["seq"], second["seq"], third["seq"]] == [1, 2, 3]
    assert await store.events_after("tenant-a", "continuation-a", 1) == [second, third]
    assert await store.events_after("tenant-b", "continuation-a", 0) == []


@pytest.mark.asyncio
async def test_assistance_cas_rejects_stale_expiry_after_extension(monkeypatch):
    fake_redis = _MemoryRedis()
    monkeypatch.setattr("src.tools.browser.resume_store.redis_client", fake_redis)
    store = ResumeStore()
    original = _assistance(state="controlling", expires_at=time.time() + 30)
    assert await store.save_suspension(original, 300) is True
    extended = await store.cas_state(
        "tenant-a", "assist-a", {"controlling"}, "controlling",
        expires_at=original.expires_at + 300,
    )
    assert extended is not None
    assert await store.cas_state(
        "tenant-a", "assist-a", {"controlling"}, "expired",
        expected_expires_at=original.expires_at,
    ) is None
    current = await store.get_assistance("tenant-a", "assist-a")
    assert current is not None
    assert current.state == "controlling"
    assert current.expires_at == extended.expires_at


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
