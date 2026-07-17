"""Windows Job Object 仅以 mock kernel32 验证，不操作真实系统进程。"""

import asyncio
import ctypes
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import src.tools.browser.executor.local as local_module
import src.tools.browser.executor.windows_job as job_module
from src.tools.browser.executor.local import LocalPlaywrightExecutor
from src.tools.browser.executor.models import BrowserRunSpec, ResultStatus, StartResult
from src.tools.browser.executor.windows_job import (
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
    WindowsJob, WindowsJobError,
)


class FakeKernel32:
    def __init__(self, fail=None):
        self.fail = fail
        self.calls = []

    def CreateJobObjectW(self, *_):
        self.calls.append(("create",))
        return 0 if self.fail == "create" else 101

    def SetInformationJobObject(self, handle, info_class, pointer, size):
        info = ctypes.cast(
            pointer, ctypes.POINTER(JOBOBJECT_EXTENDED_LIMIT_INFORMATION)
        ).contents
        self.calls.append(("set", handle, info_class, size, info.BasicLimitInformation.LimitFlags))
        return self.fail != "set"

    def OpenProcess(self, access, inherit, pid):
        self.calls.append(("open", access, inherit, pid))
        return 0 if self.fail == "open" else 202

    def AssignProcessToJobObject(self, job, process):
        self.calls.append(("assign", job, process))
        return self.fail != "assign"

    def TerminateJobObject(self, job, code):
        self.calls.append(("terminate", job, code)); return True

    def CloseHandle(self, handle):
        self.calls.append(("close", handle)); return True


def test_windows_job_structure_flags_call_order_and_idempotent_close():
    assert ctypes.sizeof(JOBOBJECT_EXTENDED_LIMIT_INFORMATION) == (144 if ctypes.sizeof(ctypes.c_void_p) == 8 else 112)
    kernel = FakeKernel32()
    job = WindowsJob(kernel)
    job.attach(4242)
    assert [call[0] for call in kernel.calls[:5]] == ["create", "set", "open", "assign", "close"]
    set_call = kernel.calls[1]
    assert set_call[3] == ctypes.sizeof(JOBOBJECT_EXTENDED_LIMIT_INFORMATION)
    assert set_call[4] == JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    assert kernel.calls[4] == ("close", 202)
    job.terminate(7)
    job.close(); job.close()
    assert ("terminate", 101, 7) in kernel.calls
    assert kernel.calls.count(("close", 101)) == 1


@pytest.mark.skipif(os.name != "nt", reason="仅 Windows 可检查真实 kernel32 签名")
def test_windows_job_real_kernel32_signatures_are_configured():
    job = WindowsJob()
    kernel = job._kernel32
    assert kernel.CreateJobObjectW.restype is job_module.wintypes.HANDLE
    assert kernel.SetInformationJobObject.restype is job_module.wintypes.BOOL
    assert kernel.OpenProcess.restype is job_module.wintypes.HANDLE
    assert kernel.AssignProcessToJobObject.restype is job_module.wintypes.BOOL
    assert kernel.TerminateJobObject.restype is job_module.wintypes.BOOL
    assert kernel.CloseHandle.restype is job_module.wintypes.BOOL
    assert len(kernel.SetInformationJobObject.argtypes) == 4
    assert len(kernel.OpenProcess.argtypes) == 3


@pytest.mark.parametrize("failure", ["create", "set", "open", "assign"])
def test_windows_job_attach_failure_closes_every_owned_handle(failure):
    kernel = FakeKernel32(failure)
    job = WindowsJob(kernel)
    with pytest.raises(WindowsJobError):
        job.attach(4242)
    assert job.attached is False
    if failure != "create":
        assert kernel.calls.count(("close", 101)) == 1
    if failure == "assign":
        assert kernel.calls.count(("close", 202)) == 1


class FakeProcess:
    def __init__(self):
        self.pid = 5151
        self.stdin = object(); self.stdout = object()
        self.stderr = SimpleNamespace(read=AsyncMock(return_value=b""))
        self.returncode = None
        self.terminate_calls = 0

    def terminate(self): self.terminate_calls += 1; self.returncode = 1
    async def wait(self): self.returncode = self.returncode or 0; return self.returncode


@pytest.mark.asyncio
async def test_local_windows_attach_failure_is_fail_closed(monkeypatch):
    process = FakeProcess()
    events = []
    class FailJob:
        def __init__(self): events.append("job_create")
        def attach(self, pid): events.append(("attach", pid)); raise WindowsJobError("assign")
        def close(self): events.append("job_close")
    monkeypatch.setattr(local_module, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(job_module, "WindowsJob", FailJob)
    create = AsyncMock(return_value=process)
    monkeypatch.setattr(local_module.asyncio, "create_subprocess_exec", create)
    executor = LocalPlaywrightExecutor()
    run = BrowserRunSpec(
        run_id="br_" + "1" * 32, tenant_id="tenant", user_id="user", session_id="session"
    )
    with pytest.raises(WindowsJobError):
        await executor.start(run)
    assert events[:2] == ["job_create", ("attach", 5151)]
    assert process.terminate_calls == 1
    assert executor._process is None
    assert create.await_args.kwargs["start_new_session"] is True


@pytest.mark.asyncio
async def test_local_windows_attach_precedes_start_and_force_terminates_job(monkeypatch):
    process = FakeProcess(); events = []
    class FakeJob:
        def __init__(self): self.attached = False
        def attach(self, pid): self.attached = True; events.append(("attach", pid))
        def terminate(self, code): events.append(("terminate", code)); process.returncode = 1
        def close(self): events.append("job_close")
    monkeypatch.setattr(local_module, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(job_module, "WindowsJob", FakeJob)
    monkeypatch.setattr(local_module.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    executor = LocalPlaywrightExecutor()
    async def request(command, result_type):
        events.append("start_request")
        return StartResult(
            run_id=command.run_id, command_id=command.command_id,
            seq=command.seq, status=ResultStatus.OK,
        )
    monkeypatch.setattr(executor, "_request", request)
    run = BrowserRunSpec(
        run_id="br_" + "2" * 32, tenant_id="tenant", user_id="user", session_id="session"
    )
    await executor.start(run)
    assert events[:2] == [("attach", 5151), "start_request"]
    process.returncode = None
    await executor._force_reap("test")
    assert ("terminate", 1) in events
    assert events[-1] == "job_close"
