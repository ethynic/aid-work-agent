"""浏览器六类终态进程回收红灯基线。

测试只记录本测试启动后新增的子进程，每条记录同时包含 PID 和
create_time。清理时会再次校验两者，绝不按进程名批量终止。
"""

import asyncio
import os
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest


try:
    import psutil
except ImportError:  # pragma: no cover - 仅用于未安装测试依赖的环境跳过
    psutil = None

import src.tools.browser.orchestrator as orchestrator_module
from src.config.settings import settings
from src.tools.browser.orchestrator import BrowserOrchestrator
from src.tools.browser.session import (
    BrowserSession,
    _browser_sessions,
    close_all_owned_browser_runs,
)


pytestmark = [
    pytest.mark.integration,
    pytest.mark.browser,
    pytest.mark.skipif(psutil is None, reason="进程探针需要 psutil"),
    pytest.mark.skipif(
        os.getenv("RUN_BROWSER_PROCESS_TESTS") != "1",
        reason="需要本机 Playwright Chromium；设置 RUN_BROWSER_PROCESS_TESTS=1 显式运行",
    ),
]


@dataclass(frozen=True)
class ProcessIdentity:
    """不受 PID 复用影响的进程身份。"""

    pid: int
    create_time: float


class OwnedProcessProbe:
    """只跟踪本测试 Playwright 驱动进程及其后代。"""

    def __init__(self):
        self._parent = psutil.Process()
        self._baseline = {
            ProcessIdentity(child.pid, child.create_time())
            for child in self._parent.children(recursive=True)
        }
        self.owned: tuple[ProcessIdentity, ...] = ()

    def capture_started(self, driver_pid: int) -> tuple[ProcessIdentity, ...]:
        """以本次 Playwright driver 为所有权根，避免并发子进程被误收。"""
        driver = psutil.Process(driver_pid)
        processes = [driver, *driver.children(recursive=True)]
        identities = [
            ProcessIdentity(process.pid, process.create_time())
            for process in processes
        ]
        if any(identity in self._baseline for identity in identities):
            raise RuntimeError("Playwright 驱动进程不应属于测试启动前基线")
        self.owned = tuple(identities)
        return self.owned

    @staticmethod
    def _same_process(identity: ProcessIdentity):
        try:
            process = psutil.Process(identity.pid)
            if process.create_time() != identity.create_time:
                return None
            return process
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return None

    async def assert_all_stopped(self, timeout: float = 5.0):
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if not any(self._same_process(identity) for identity in self.owned):
                return
            await asyncio.sleep(0.1)
        alive = [identity for identity in self.owned if self._same_process(identity)]
        assert not alive, f"终态后仍存在本测试启动的进程: {alive}"

    def cleanup_owned(self):
        """定向清理探针记录的进程，先校验 PID + create_time。"""
        processes = [
            process
            for identity in reversed(self.owned)
            if (process := self._same_process(identity)) is not None
        ]
        for process in processes:
            process.terminate()
        _, alive = psutil.wait_procs(processes, timeout=3)
        for process in alive:
            identity = next(item for item in self.owned if item.pid == process.pid)
            same_process = self._same_process(identity)
            if same_process is not None:
                same_process.kill()
        psutil.wait_procs(alive, timeout=3)


def _playwright_driver_pid(session: BrowserSession) -> int:
    """取得当前会话唯一对应的 Playwright driver PID。"""
    try:
        return session.playwright._impl_obj._connection._transport._proc.pid
    except (AttributeError, TypeError) as exc:
        raise RuntimeError("无法确定本测试 Playwright 驱动进程，拒绝执行进程清理") from exc


class _ProbePageOps:
    """只替换页面语义/截图，生命周期仍走生产 Orchestrator。"""

    def __init__(self, session, session_id):
        self.session = session
        self.session_id = session_id

    async def take_snapshot(self):
        return {
            "success": True,
            "url": "https://example.com/",
            "interactive_elements": [],
        }

    async def take_screenshot(self, path):
        del path
        return {"success": True}


TERMINAL_PATHS = (
    "success",
    "error",
    "cancel",
    "timeout",
    "shutdown",
    "ask_user_expire",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_state", TERMINAL_PATHS)
async def test_owned_browser_processes_return_to_baseline(monkeypatch, terminal_state):
    """六类终态经生产 execute/finally 或 shutdown 后进程必须归零。"""
    probe = OwnedProcessProbe()
    session = BrowserSession(headless=True)
    real_page = None
    session_id = f"process_probe_{terminal_state}"

    try:
        await session.start()
        real_page = session.page
        if not probe.capture_started(_playwright_driver_pid(session)):
            raise RuntimeError("Playwright 未启动可跟踪的进程")
        fake_page = MagicMock(
            close=AsyncMock(side_effect=RuntimeError("page close failed"))
        )
        fake_page.url = "https://example.com/"
        session.page = fake_page
        _browser_sessions[session_id] = session
        monkeypatch.setattr(orchestrator_module, "PageOps", _ProbePageOps)

        if terminal_state == "shutdown":
            await close_all_owned_browser_runs(reason="shutdown")
        else:
            orchestrator = BrowserOrchestrator(session_id=session_id)
            if terminal_state == "success":
                decision = AsyncMock(return_value={"action": "done", "reason": "完成"})
            elif terminal_state == "error":
                decision = AsyncMock(side_effect=RuntimeError("llm failed"))
            elif terminal_state == "ask_user_expire":
                decision = AsyncMock(
                    return_value={"action": "ask_user", "reason": "需要人工"}
                )
            else:
                async def wait_forever(*args):
                    await asyncio.Event().wait()

                decision = wait_forever
            monkeypatch.setattr(orchestrator, "_get_decision", decision)

            if terminal_state == "cancel":
                task = asyncio.create_task(orchestrator.execute(task="取消测试"))
                await asyncio.sleep(0.05)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                if terminal_state == "timeout":
                    monkeypatch.setattr(settings.tools.browser, "task_timeout", 0.01)
                await orchestrator.execute(task="进程回收测试")

        await probe.assert_all_stopped()
    finally:
        _browser_sessions.pop(session_id, None)
        if not probe.owned and session.playwright is not None:
            probe.capture_started(_playwright_driver_pid(session))
        session.page = real_page
        for resource, method_name in (
            (real_page, "close"),
            (session.context, "close"),
            (session.browser, "close"),
            (session.playwright, "stop"),
        ):
            if resource is not None:
                try:
                    await getattr(resource, method_name)()
                except Exception:
                    pass
        probe.cleanup_owned()
