"""生产 BrowserAutomationTool/BrowserOrchestrator 执行边界生命周期测试。"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.tools.browser.automation_tool as automation_module
import src.tools.browser.orchestrator as orchestrator_module
from src.config.settings import settings
from src.tools.browser.automation_tool import BrowserAutomationTool
from src.tools.browser.orchestrator import BrowserOrchestrator
from src.tools.browser.session import _browser_sessions, get_browser_session


pytestmark = [pytest.mark.unit, pytest.mark.browser]


class _FakePageOps:
    snapshot = {"success": True, "url": "https://example.com/", "interactive_elements": []}

    def __init__(self, session, session_id):
        self.session = session
        self.session_id = session_id

    async def take_snapshot(self):
        return dict(self.snapshot)

    async def take_screenshot(self, path):
        del path
        return {"success": True}

    async def fill(self, field, value, ref=None):
        del field, value, ref
        return {"success": True, "message": "已填写: 密码"}


@pytest.fixture(autouse=True)
def _clear_registry():
    _browser_sessions.clear()
    yield
    _browser_sessions.clear()


def _running_session(run_id: str):
    session = get_browser_session(run_id, headless=True)
    page = MagicMock()
    page.url = "https://example.com/path?secret=hidden"
    page.close = AsyncMock()
    page.evaluate = AsyncMock(return_value="")
    page.screenshot = AsyncMock()
    session.page = page
    session.context = MagicMock(close=AsyncMock())
    session.browser = MagicMock(close=AsyncMock())
    session.playwright = MagicMock(stop=AsyncMock())
    return session, page


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "decision", "snapshot", "max_steps", "expected_status"),
    [
        ("success", {"action": "done", "reason": "完成"}, _FakePageOps.snapshot, 30, "done"),
        ("error", {"action": "done"}, {"success": False, "error": "raw secret"}, 30, "done"),
        ("ask_user", {"action": "ask_user", "reason": "需要验证码"}, _FakePageOps.snapshot, 30, "ask_user"),
        ("max_steps", {"action": "done"}, _FakePageOps.snapshot, 0, "done"),
    ],
)
async def test_orchestrator_terminal_paths_close_owned_session(
    monkeypatch, case, decision, snapshot, max_steps, expected_status
):
    run_id = f"run_{case}"
    session, page = _running_session(run_id)
    _FakePageOps.snapshot = snapshot
    monkeypatch.setattr(orchestrator_module, "PageOps", _FakePageOps)

    orchestrator = BrowserOrchestrator(run_id)
    orchestrator.max_steps = max_steps
    monkeypatch.setattr(orchestrator, "_get_decision", AsyncMock(return_value=decision))

    result = await orchestrator.execute(task="不含敏感信息的任务")

    assert result["status"] == expected_status
    if case == "ask_user":
        assert result["error_code"] == "HUMAN_REQUIRED"
        assert "重新发起" in result["instruction"]
    if case == "max_steps":
        assert result["error_code"] == "MAX_STEPS_EXCEEDED"
    assert "secret=hidden" not in result.get("final_url", "")
    page.close.assert_awaited_once()
    assert session.context is None
    assert run_id not in _browser_sessions


@pytest.mark.asyncio
async def test_orchestrator_cancel_propagates_and_closes(monkeypatch):
    run_id = "run_cancel"
    _, page = _running_session(run_id)
    _FakePageOps.snapshot = {
        "success": True,
        "url": "https://example.com/",
        "interactive_elements": [],
    }
    monkeypatch.setattr(orchestrator_module, "PageOps", _FakePageOps)
    entered = asyncio.Event()

    async def wait_decision(*args):
        entered.set()
        await asyncio.Event().wait()

    orchestrator = BrowserOrchestrator(run_id)
    monkeypatch.setattr(orchestrator, "_get_decision", wait_decision)
    task = asyncio.create_task(orchestrator.execute(task="取消测试"))
    await entered.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    page.close.assert_awaited_once()
    assert run_id not in _browser_sessions


@pytest.mark.asyncio
async def test_orchestrator_timeout_closes(monkeypatch):
    run_id = "run_timeout"
    _, page = _running_session(run_id)
    _FakePageOps.snapshot = {
        "success": True,
        "url": "https://example.com/",
        "interactive_elements": [],
    }
    monkeypatch.setattr(orchestrator_module, "PageOps", _FakePageOps)
    monkeypatch.setattr(settings.tools.browser, "task_timeout", 0.01)

    async def wait_decision(*args):
        await asyncio.Event().wait()

    orchestrator = BrowserOrchestrator(run_id)
    monkeypatch.setattr(orchestrator, "_get_decision", wait_decision)
    result = await orchestrator.execute(task="超时测试")

    assert result["error_code"] == "TASK_TIMEOUT"
    page.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_fill_body_is_absent_from_result_and_logs(monkeypatch):
    from loguru import logger

    run_id = "run_sensitive_fill"
    _, page = _running_session(run_id)
    page.keyboard.press = AsyncMock()
    _FakePageOps.snapshot = {
        "success": True,
        "url": "https://example.com/",
        "interactive_elements": [],
    }
    monkeypatch.setattr(orchestrator_module, "PageOps", _FakePageOps)
    orchestrator = BrowserOrchestrator(run_id)
    secret = "credential=phase1-secret-body"
    monkeypatch.setattr(
        orchestrator,
        "_get_decision",
        AsyncMock(
            side_effect=[
                {"action": "fill", "target": "e1", "value": secret},
                {"action": "done", "reason": "完成"},
            ]
        ),
    )
    logged: list[str] = []
    sink_id = logger.add(lambda message: logged.append(str(message)), level="DEBUG")
    try:
        result = await orchestrator.execute(task="填写测试")
    finally:
        logger.remove(sink_id)

    assert result["success"] is True
    assert result["steps"][0]["value"] == "***"
    assert secret not in str(result)
    assert secret not in "".join(logged)


@pytest.mark.asyncio
async def test_orchestrator_ensure_session_failure_still_runs_close(monkeypatch):
    orchestrator = BrowserOrchestrator("run_start_error")
    close_mock = AsyncMock(return_value={})
    monkeypatch.setattr(orchestrator, "_ensure_session", AsyncMock(side_effect=RuntimeError("secret")))
    monkeypatch.setattr(orchestrator_module, "close_browser_session", close_mock)

    result = await orchestrator.execute(task="启动失败")

    assert result["error_code"] == "INTERNAL_ERROR"
    assert "secret" not in result["error"]
    close_mock.assert_awaited_once()


class _FakeOrchestrator:
    behavior = "success"

    def __init__(self, session_id):
        self.session_id = session_id

    def cancel(self):
        return None

    async def execute(self, **kwargs):
        del kwargs
        if self.behavior == "error":
            raise RuntimeError("credential=secret")
        if self.behavior == "wait":
            await asyncio.Event().wait()
        return {"success": True, "status": "done"}


@pytest.mark.asyncio
@pytest.mark.parametrize("behavior", ["success", "error"])
async def test_automation_tool_boundary_always_closes(monkeypatch, behavior):
    _FakeOrchestrator.behavior = behavior
    close_mock = AsyncMock(return_value={})
    monkeypatch.setattr(automation_module, "BrowserOrchestrator", _FakeOrchestrator)
    monkeypatch.setattr(automation_module, "close_browser_session", close_mock)

    result = await BrowserAutomationTool().execute(task="测试")

    assert result["success"] is (behavior == "success")
    if behavior == "error":
        assert "secret" not in result["error"]
    close_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_automation_tool_timeout_and_cancel_close(monkeypatch):
    _FakeOrchestrator.behavior = "wait"
    close_mock = AsyncMock(return_value={})
    monkeypatch.setattr(automation_module, "BrowserOrchestrator", _FakeOrchestrator)
    monkeypatch.setattr(automation_module, "close_browser_session", close_mock)
    monkeypatch.setattr(settings.tools.browser, "task_timeout", 0.01)

    timeout_result = await BrowserAutomationTool().execute(task="超时")
    assert timeout_result["error_code"] == "TASK_TIMEOUT"

    monkeypatch.setattr(settings.tools.browser, "task_timeout", 300.0)
    task = asyncio.create_task(BrowserAutomationTool().execute(task="取消"))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert close_mock.await_count == 2


def test_agent_schema_hides_deprecated_parameters_and_display_name_hides_input():
    tool = BrowserAutomationTool()
    schema = tool.to_tool_definition()["input_schema"]
    properties = schema.get("properties", {})
    assert "headless" not in properties
    assert "session_id" not in properties
    assert "user_response" not in properties
    assert "secret" not in tool.get_display_name({"task": "secret", "user_response": "secret"})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("deprecated_args", "expected_error"),
    [
        (
            {"session_id": "old"},
            "旧版浏览器会话恢复参数已停用，请重新发起完整的浏览器任务",
        ),
        (
            {"user_response": "credential=secret"},
            "旧版浏览器会话恢复参数已停用，请重新发起完整的浏览器任务",
        ),
        (
            {"headless": False},
            "headless 参数已停用，浏览器模式由服务端配置决定",
        ),
    ],
)
async def test_deprecated_parameters_are_stable_without_starting_browser(
    monkeypatch, deprecated_args, expected_error
):
    orchestrator = MagicMock()
    monkeypatch.setattr(automation_module, "BrowserOrchestrator", orchestrator)

    result = await BrowserAutomationTool().execute(task="测试", **deprecated_args)

    assert result == {
        "success": False,
        "error_code": "DEPRECATED_PARAMETER",
        "error": expected_error,
    }
    orchestrator.assert_not_called()
    assert "secret" not in str(result)
