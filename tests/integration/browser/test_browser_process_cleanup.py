"""浏览器六类终态进程回收红灯基线。

测试只记录本测试启动后新增的子进程，每条记录同时包含 PID 和
create_time。清理时会再次校验两者，绝不按进程名批量终止。
"""

import asyncio
import os
from dataclasses import dataclass

import pytest


try:
    import psutil
except ImportError:  # pragma: no cover - 仅用于未安装测试依赖的环境跳过
    psutil = None

from src.tools.browser.run_manager import BrowserRunManager, RunState
from src.tools.browser.run_store import InMemoryRunStore


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


class _NoopDB:
    async def create_run(self, data):
        del data

    async def update_run_state(self, *args, **kwargs):
        del args, kwargs
        return True


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
async def test_owned_browser_processes_return_to_baseline(terminal_state):
    """六类终态经生产 RunManager/LocalExecutor 或 shutdown 后进程必须归零。"""
    probe = OwnedProcessProbe()
    manager = BrowserRunManager(store=InMemoryRunStore(), run_db=_NoopDB())
    record = await manager.create("tenant", "user", f"process_probe_{terminal_state}")

    try:
        executor = await manager.start(record)
        # 生产返回带 owner fencing 的包装器，进程探针只下钻测试拥有的 local executor。
        local_executor = executor._executor
        if not probe.capture_started(local_executor._process.pid):
            raise RuntimeError("browser worker 未启动可跟踪的进程")

        if terminal_state == "cancel":
            assert await manager.request_cancel("tenant", "user", record.run_id)
        elif terminal_state == "shutdown":
            await manager.close_all("shutdown")
        else:
            terminal = {
                "success": RunState.SUCCEEDED,
                "error": RunState.FAILED,
                "timeout": RunState.TIMED_OUT,
                "ask_user_expire": RunState.FAILED,
            }[terminal_state]
            await manager.finalize("tenant", record.run_id, terminal, terminal_state)

        await probe.assert_all_stopped()
    finally:
        await manager.close_all("test_cleanup")
        probe.cleanup_owned()
