"""生产 BrowserAutomationTool/BrowserOrchestrator Phase 2 生命周期测试。"""

import asyncio
from unittest.mock import AsyncMock

import pytest

import src.tools.browser.automation_tool as automation_module
import src.tools.browser.orchestrator as orchestrator_module
from src.config.settings import settings
from src.saas.context import clear_tenant_context, set_tenant_context
from src.tools.browser.automation_tool import BrowserAutomationTool
from src.tools.browser.orchestrator import BrowserOrchestrator
from src.tools.browser.run_manager import RunState
from src.tools.browser.run_store import RunRecord


pytestmark = [pytest.mark.unit, pytest.mark.browser]


class _FakePageOps:
    snapshot = {"success": True, "url": "https://example.com/", "page_text": "", "interactive_elements": []}

    def __init__(self, executor, run_id):
        self.executor = executor
        self.run_id = run_id

    async def take_snapshot(self): return dict(self.snapshot)
    async def fill(self, field, value, ref=None): return {"success": True, "message": "已填写"}
    async def keyboard(self, key): return {"success": True}


class _FakeManager:
    def __init__(self):
        self.finalized = []
        self.executor = object()

    async def start(self, record): return self.executor
    async def finalize(self, tenant_id, run_id, terminal, reason):
        self.finalized.append((tenant_id, run_id, terminal, reason))
        return RunRecord(tenant_id=tenant_id, user_id="user", run_id=run_id, session_id="session", state=terminal.value)


def _orchestrator(monkeypatch, run_id="br_" + "1" * 32):
    manager = _FakeManager()
    record = RunRecord(tenant_id="tenant", user_id="user", run_id=run_id, session_id="session", state="CREATED")
    monkeypatch.setattr(orchestrator_module, "PageOps", _FakePageOps)
    return BrowserOrchestrator("session", run_manager=manager, run_record=record), manager


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "decision", "snapshot", "max_steps", "terminal"),
    [
        ("success", {"action": "done", "reason": "完成"}, _FakePageOps.snapshot, 30, RunState.SUCCEEDED),
        ("error", {"action": "done"}, {"success": False, "error": "raw secret"}, 30, RunState.FAILED),
        ("ask_user", {"action": "ask_user", "reason": "需要验证码"}, _FakePageOps.snapshot, 30, RunState.FAILED),
        ("max_steps", {"action": "done"}, _FakePageOps.snapshot, 0, RunState.FAILED),
    ],
)
async def test_orchestrator_terminal_paths_finalize_once(monkeypatch, case, decision, snapshot, max_steps, terminal):
    orchestrator, manager = _orchestrator(monkeypatch)
    _FakePageOps.snapshot = snapshot
    orchestrator.max_steps = max_steps
    monkeypatch.setattr(orchestrator, "_get_decision", AsyncMock(return_value=decision))
    result = await orchestrator.execute(task="不含敏感信息的任务")
    if case == "ask_user":
        assert result["error_code"] == "HUMAN_REQUIRED"
        assert "浏览器画面" in result["instruction"]
        # WAITING_HUMAN 是唯一非终态例外，交由 ToolSuspension 持久化后续跑。
        assert manager.finalized == []
        return
    if case == "max_steps": assert result["error_code"] == "MAX_STEPS_EXCEEDED"
    assert manager.finalized[0][2] == terminal
    assert len(manager.finalized) == 1


@pytest.mark.asyncio
async def test_orchestrator_cancel_and_timeout_finalize(monkeypatch):
    entered = asyncio.Event()
    async def wait_decision(*args): entered.set(); await asyncio.Event().wait()
    orchestrator, manager = _orchestrator(monkeypatch)
    monkeypatch.setattr(orchestrator, "_get_decision", wait_decision)
    task = asyncio.create_task(orchestrator.execute(task="取消测试"))
    await entered.wait(); task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert manager.finalized[0][2] == RunState.CANCELLED

    orchestrator, manager = _orchestrator(monkeypatch, "br_" + "2" * 32)
    monkeypatch.setattr(orchestrator, "_get_decision", wait_decision)
    monkeypatch.setattr(settings.tools.browser, "task_timeout", 0.01)
    result = await orchestrator.execute(task="超时测试")
    assert result["error_code"] == "TASK_TIMEOUT"
    assert manager.finalized[0][2] == RunState.TIMED_OUT


@pytest.mark.asyncio
async def test_access_block_snapshot_cannot_be_overridden_by_llm_done(monkeypatch):
    orchestrator, manager = _orchestrator(monkeypatch)
    original_snapshot = _FakePageOps.snapshot
    try:
        _FakePageOps.snapshot = {
            "success": True,
            "url": "https://example.com/",
            "title": "403 Forbidden",
            "page_text": "异常访问",
            "interactive_elements": [],
        }
        decision = AsyncMock(return_value={"action": "done", "reason": "任务完成"})
        monkeypatch.setattr(orchestrator, "_get_decision", decision)
        result = await orchestrator.execute(task="读取官网")
        assert result["success"] is False
        assert result["status"] == "blocked"
        assert result["error_code"] == "ACCESS_BLOCKED"
        decision.assert_not_awaited()
        assert manager.finalized[0][2] == RunState.FAILED
    finally:
        _FakePageOps.snapshot = original_snapshot


@pytest.mark.asyncio
async def test_fill_body_absent_from_result_and_logs(monkeypatch):
    from loguru import logger
    orchestrator, _ = _orchestrator(monkeypatch)
    secret = "credential=phase2-secret-body"
    monkeypatch.setattr(orchestrator, "_get_decision", AsyncMock(side_effect=[
        {"action": "fill", "target": "e1", "value": secret},
        {"action": "done", "reason": "完成"},
    ]))
    logged = []
    sink = logger.add(lambda message: logged.append(str(message)), level="DEBUG")
    try: result = await orchestrator.execute(task="填写测试")
    finally: logger.remove(sink)
    assert result["steps"][0]["value"] == "***"
    assert secret not in str(result)
    assert secret not in "".join(logged)


class _FakeToolManager:
    instances = []
    def __init__(self):
        self.finalize_calls = []
        self.create_calls = []
        self.__class__.instances.append(self)
    async def create(self, tenant_id, user_id, session_id, execution_target):
        self.create_calls.append((tenant_id, user_id, session_id, execution_target))
        return RunRecord(tenant_id=tenant_id, user_id=user_id, run_id="br_" + "3" * 32, session_id=session_id, state="CREATED")
    async def finalize(self, *args): self.finalize_calls.append(args)


class _FakeOrchestrator:
    behavior = "success"
    def __init__(self, session_id, run_manager, run_record): pass
    def cancel(self): pass
    async def execute(self, **kwargs):
        if self.behavior == "error": raise RuntimeError("credential=secret")
        if self.behavior == "wait": await asyncio.Event().wait()
        return {"success": True, "status": "done"}


@pytest.mark.asyncio
@pytest.mark.parametrize("behavior", ["success", "error"])
async def test_automation_tool_boundary_always_finalizes(monkeypatch, behavior):
    set_tenant_context("tenant", "user")
    _FakeToolManager.instances.clear(); _FakeOrchestrator.behavior = behavior
    monkeypatch.setattr(automation_module, "BrowserRunManager", _FakeToolManager)
    monkeypatch.setattr(automation_module, "BrowserOrchestrator", _FakeOrchestrator)
    try: result = await BrowserAutomationTool().execute(task="测试")
    finally: clear_tenant_context()
    assert result["success"] is (behavior == "success")
    if behavior == "error": assert "secret" not in result["error"]
    assert len(_FakeToolManager.instances[0].finalize_calls) == 1


def test_agent_schema_hides_deprecated_parameters_and_display_name_hides_input():
    tool = BrowserAutomationTool(); properties = tool.to_tool_definition()["input_schema"].get("properties", {})
    assert "headless" in properties
    assert {"session_id", "user_response", "_audit_session_id"}.isdisjoint(properties)
    assert "secret" not in tool.get_display_name({"task": "secret"})


@pytest.mark.asyncio
async def test_missing_context_and_deprecated_parameters_do_not_start_browser(monkeypatch):
    clear_tenant_context(); created = AsyncMock()
    monkeypatch.setattr(automation_module, "BrowserRunManager", created)
    result = await BrowserAutomationTool().execute(task="测试")
    assert result["error_code"] == "MISSING_EXECUTION_CONTEXT"
    created.assert_not_called()
    result = await BrowserAutomationTool().execute(task="测试", user_response="secret")
    assert result["error_code"] == "DEPRECATED_PARAMETER"
    created.assert_not_called()


@pytest.mark.asyncio
async def test_trusted_execution_context_overrides_stale_mutable_tool_identity(monkeypatch):
    clear_tenant_context()
    _FakeToolManager.instances.clear()
    _FakeOrchestrator.behavior = "success"
    monkeypatch.setattr(automation_module, "BrowserRunManager", _FakeToolManager)
    monkeypatch.setattr(automation_module, "BrowserOrchestrator", _FakeOrchestrator)
    tool = BrowserAutomationTool()
    tool.set_tenant_id("stale-tenant")
    tool.set_user_id("stale-user")
    result = await tool.execute(
        task="测试",
        _trusted_tenant_id="trusted-tenant",
        _trusted_user_id="trusted-user",
        _audit_session_id="trusted-session",
    )
    assert result["success"] is True
    assert _FakeToolManager.instances[0].create_calls == [
        ("trusted-tenant", "trusted-user", "trusted-session", "server")
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("requested", "interactive", "trusted", "expected", "error_code"),
    [
        (None, False, False, None, None),
        (False, True, True, False, None),
        (False, True, False, None, "VISIBLE_BROWSER_NOT_ALLOWED"),
        (False, False, True, None, "VISIBLE_BROWSER_NOT_ALLOWED"),
        (True, False, False, True, None),
    ],
)
async def test_headless_override_trust_boundary(
    monkeypatch, requested, interactive, trusted, expected, error_code,
):
    set_tenant_context("tenant", "user")
    _FakeToolManager.instances.clear()
    _FakeOrchestrator.behavior = "success"
    monkeypatch.setattr(automation_module, "BrowserRunManager", _FakeToolManager)
    monkeypatch.setattr(automation_module, "BrowserOrchestrator", _FakeOrchestrator)
    monkeypatch.setattr(automation_module, "_has_interactive_desktop", lambda: interactive)
    kwargs = {
        "task": "测试",
        "_trusted_local_interactive": (
            automation_module._LOCAL_INTERACTIVE_TRUST if trusted else True
        ),
    }
    if requested is not None:
        kwargs["headless"] = requested
    try:
        result = await BrowserAutomationTool().execute(**kwargs)
    finally:
        clear_tenant_context()
    if error_code:
        assert result["error_code"] == error_code
        assert _FakeToolManager.instances == []
    else:
        assert result["success"] is True
        assert _FakeToolManager.instances[0].headless_override is expected


@pytest.mark.asyncio
async def test_headless_override_does_not_leak_between_runs(monkeypatch):
    set_tenant_context("tenant", "user")
    _FakeToolManager.instances.clear()
    _FakeOrchestrator.behavior = "success"
    monkeypatch.setattr(automation_module, "BrowserRunManager", _FakeToolManager)
    monkeypatch.setattr(automation_module, "BrowserOrchestrator", _FakeOrchestrator)
    try:
        first = await BrowserAutomationTool().execute(task="first", headless=True)
        second = await BrowserAutomationTool().execute(task="second")
    finally:
        clear_tenant_context()
    assert first["success"] is True
    assert second["success"] is True
    assert [manager.headless_override for manager in _FakeToolManager.instances] == [
        True, None,
    ]


@pytest.mark.asyncio
async def test_local_interactive_entry_injects_unserializable_capability(monkeypatch):
    set_tenant_context("tenant", "user")
    _FakeToolManager.instances.clear()
    _FakeOrchestrator.behavior = "success"
    monkeypatch.setattr(automation_module, "BrowserRunManager", _FakeToolManager)
    monkeypatch.setattr(automation_module, "BrowserOrchestrator", _FakeOrchestrator)
    monkeypatch.setattr(automation_module, "_has_interactive_desktop", lambda: True)
    try:
        result = await BrowserAutomationTool().execute_local_interactive(task="测试")
    finally:
        clear_tenant_context()
    assert result["success"] is True
    assert _FakeToolManager.instances[0].headless_override is False


def test_local_interactive_capability_is_not_in_agent_schema():
    schema = automation_module.BrowserAutomationInput.model_json_schema()
    assert "_trusted_local_interactive" not in schema["properties"]
    assert "execute_local_interactive" not in schema["properties"]


@pytest.mark.asyncio
async def test_legacy_session_parameter_still_rejected_with_headless(monkeypatch):
    created = AsyncMock()
    monkeypatch.setattr(automation_module, "BrowserRunManager", created)
    result = await BrowserAutomationTool().execute(
        task="测试", headless=True, session_id="legacy",
    )
    assert result["error_code"] == "DEPRECATED_PARAMETER"
    created.assert_not_called()


def test_windows_session_one_without_sessionname_allows_input_desktop():
    closed = []
    assert automation_module._windows_interactive_desktop(
        session_id_provider=lambda: 1,
        open_input_desktop=lambda: 123,
        close_desktop=lambda handle: closed.append(handle) or True,
    ) is True
    assert closed == [123]


def test_windows_open_input_desktop_failure_is_not_interactive():
    assert automation_module._windows_interactive_desktop(
        session_id_provider=lambda: 1,
        open_input_desktop=lambda: 0,
        close_desktop=lambda _: True,
    ) is False


def test_windows_64_bit_desktop_handle_is_closed_without_truncation():
    handle = 0x1_0000_0001
    closed = []
    assert automation_module._windows_interactive_desktop(
        session_id_provider=lambda: 1,
        open_input_desktop=lambda: handle,
        close_desktop=lambda value: closed.append(value) or True,
    ) is True
    assert closed == [handle]


def test_windows_close_desktop_failure_is_fail_closed():
    assert automation_module._windows_interactive_desktop(
        session_id_provider=lambda: 1,
        open_input_desktop=lambda: 123,
        close_desktop=lambda _: False,
    ) is False


@pytest.mark.parametrize("failing_api", ["session", "open", "close"])
def test_windows_desktop_api_exceptions_are_fail_closed(failing_api):
    def fail():
        raise OSError("win32 failure")

    def fail_close(_):
        raise OSError("win32 failure")

    assert automation_module._windows_interactive_desktop(
        session_id_provider=fail if failing_api == "session" else lambda: 1,
        open_input_desktop=fail if failing_api == "open" else lambda: 123,
        close_desktop=fail_close if failing_api == "close" else lambda _: True,
    ) is False


def test_windows_session_zero_rejects_without_opening_desktop():
    opened = []
    assert automation_module._windows_interactive_desktop(
        session_id_provider=lambda: 0,
        open_input_desktop=lambda: opened.append(True) or 123,
        close_desktop=lambda _: True,
    ) is False
    assert opened == []


def test_windows_process_session_api_uses_explicit_win32_signature(monkeypatch):
    class _ProcessIdToSessionId:
        argtypes = None
        restype = None

        def __call__(self, _pid, session_ptr):
            session_ptr._obj.value = 0
            return 1

    session_api = _ProcessIdToSessionId()
    monkeypatch.setattr(
        automation_module.ctypes,
        "windll",
        type(
            "_WinDll",
            (),
            {
                "kernel32": type(
                    "_Kernel32", (), {"ProcessIdToSessionId": session_api}
                )(),
            },
        )(),
        raising=False,
    )
    assert automation_module._windows_interactive_desktop() is False
    assert session_api.argtypes == [
        automation_module.wintypes.DWORD,
        automation_module.ctypes.POINTER(automation_module.wintypes.DWORD),
    ]
    assert session_api.restype is automation_module.wintypes.BOOL


@pytest.mark.parametrize(
    ("display", "wayland", "expected"),
    [
        (None, None, False),
        (":0", None, True),
        (None, "wayland-0", True),
    ],
)
def test_non_windows_interactive_desktop_detection(
    monkeypatch, display, wayland, expected,
):
    monkeypatch.setattr(automation_module.os, "name", "posix")
    for name, value in (("DISPLAY", display), ("WAYLAND_DISPLAY", wayland)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    assert automation_module._has_interactive_desktop() is expected
