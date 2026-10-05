"""基于独占子进程的本地 Playwright 执行器。"""

from __future__ import annotations

import asyncio
import base64
import os
import signal
import sys
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, TypeVar

from loguru import logger
from pydantic import BaseModel, ValidationError

from src.config.settings import settings
from src.tools.browser.worker_protocol import ProtocolError, read_frame, write_frame

from .models import (
    BrowserCommand, BrowserRunSpec, ClickCommand, CloseCommand, CloseResult,
    CommandResult, ContentCommand, ContentResult, ExportStorageStateCommand,
    ExportStorageStateResult, FillCommand, KeyboardCommand,
    NavigateCommand, PointerCommand, ResultStatus, SelectCommand, SnapshotCommand,
    SnapshotResult, StartCommand, StartResult, ScreenshotCommand, ScreenshotResult,
)

TResult = TypeVar("TResult", bound=BaseModel)


class LocalPlaywrightExecutor:
    """一个实例只执行一个 run；所有请求在单锁内严格串行。"""

    def __init__(self) -> None:
        self._run: BrowserRunSpec | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._seq = 0
        self._io_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._close_result: CloseResult | None = None
        self._stderr_task: asyncio.Task | None = None
        self._owned_pgid: int | None = None
        self._leader_birth: int | None = None
        self._group_members: dict[int, int] = {}
        self._group_signal_revoked = False
        self._command_fence: Callable[[], Awaitable[str | None]] | None = None
        self._windows_job = None
        self._frame_task: asyncio.Task | None = None
        self._frame_seq = 0
        self._require_close_proof = False
        self._resource_close_confirmed = False

    def require_resource_close_proof(self):
        self._require_close_proof = True

    @property
    def resource_close_confirmed(self):
        return self._resource_close_confirmed

    @property
    def run_id(self) -> str | None:
        return self._run.run_id if self._run else None

    def _deadline(self) -> datetime:
        timeout = float(getattr(settings.tools.browser, "command_timeout", 30.0))
        return datetime.now(timezone.utc) + timedelta(seconds=timeout)

    def set_command_fence(
        self, callback: Callable[[], Awaitable[str | None]]
    ) -> None:
        """设置写帧前 fence；仅普通命令调用，close/finalize 永不受阻。"""
        self._command_fence = callback

    def _command_fields(self) -> dict:
        if self._run is None:
            raise RuntimeError("executor 尚未启动")
        self._seq += 1
        return {
            "run_id": self._run.run_id,
            "seq": self._seq,
            "command_id": f"bc_{uuid.uuid4().hex}",
            "deadline_at": self._deadline(),
        }

    async def start(self, run: BrowserRunSpec) -> StartResult:
        if self._process is not None:
            raise RuntimeError("executor 不能复用于第二个 run")
        self._run = run
        kwargs = {
            "stdin": asyncio.subprocess.PIPE,
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
            "start_new_session": True,
        }
        self._process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "src.tools.browser.worker_main", **kwargs
        )
        if os.name == "nt":
            try:
                from .windows_job import WindowsJob

                self._windows_job = WindowsJob()
                self._windows_job.attach(self._process.pid)
            except BaseException:
                # Windows 无 Job 托管时必须 fail-closed；worker 尚未收到 start，
                # 不会生成 Chromium 子进程。
                if self._windows_job is not None:
                    self._windows_job.close()
                    self._windows_job = None
                await asyncio.shield(self._force_reap("windows_job_attach_failed"))
                self._process = None
                raise
        elif os.name == "posix":
            try:
                pgid = os.getpgid(self._process.pid)
                if pgid == self._process.pid:
                    self._owned_pgid = pgid
                    if sys.platform.startswith('linux'):
                        identity = self._process_identity(self._process.pid)
                        if identity is not None and identity[0] == pgid and identity[2] != 'Z':
                            self._leader_birth = identity[1]
                    self._remember_owned_group()
            except (ProcessLookupError, PermissionError, OSError):
                self._owned_pgid = None
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        command = StartCommand(**self._command_fields(), run=run)
        try:
            result = await self._request(command, StartResult)
            if result.status == ResultStatus.OK:
                self._frame_task = asyncio.create_task(self._frame_loop())
            return result
        except BaseException:
            await asyncio.shield(self._force_reap("start_failed"))
            raise

    async def navigate(self, command: NavigateCommand) -> CommandResult:
        return await self._request(command, CommandResult)

    async def snapshot(self, command: SnapshotCommand) -> SnapshotResult:
        return await self._request(command, SnapshotResult)

    async def click(self, command: ClickCommand) -> CommandResult:
        return await self._request(command, CommandResult)

    async def fill(self, command: FillCommand) -> CommandResult:
        return await self._request(command, CommandResult)

    async def select(self, command: SelectCommand) -> CommandResult:
        return await self._request(command, CommandResult)

    async def keyboard(self, command: KeyboardCommand) -> CommandResult:
        return await self._request(command, CommandResult)

    async def pointer(self, command: PointerCommand) -> CommandResult:
        return await self._request(command, CommandResult)

    async def content(self, command: ContentCommand) -> ContentResult:
        return await self._request(command, ContentResult)

    async def export_storage_state(self) -> dict[str, Any] | None:
        """导出当前 run 的 storage_state（cookies + localStorage，B0.5）。

        用于登录完成后捕获登录态。返回 dict（含 cookies/origins）或 ``None``
        （worker 未启动 / 命令失败 / run 未启动）。返回值含敏感 cookie，调用方负责
        加密持久化（``account_session_store.save_storage_state``）且不得写入日志/审计。
        """
        if self._process is None or self._run is None:
            return None
        command = ExportStorageStateCommand(**self._command_fields())
        try:
            result = await self._request(command, ExportStorageStateResult)
        except (asyncio.TimeoutError, ProtocolError, ConnectionError, OSError, ValidationError):
            return None
        if result.status != ResultStatus.OK:
            return None
        return result.storage_state

    async def execute(self, command: BrowserCommand) -> BaseModel:
        """供 PageOps 按 DTO 类型分派。"""
        mapping = {
            NavigateCommand: self.navigate, SnapshotCommand: self.snapshot,
            ClickCommand: self.click, FillCommand: self.fill, SelectCommand: self.select,
            KeyboardCommand: self.keyboard, PointerCommand: self.pointer,
            ContentCommand: self.content,
        }
        handler = mapping.get(type(command))
        if handler is None:
            raise TypeError("不支持的命令类型")
        return await handler(command)

    async def execute_deferred(self,builder,result_type,*,deadline_at):
        """Build an original human DTO only after both guards in the IO lock."""
        from ..owner_port import current_human_action_guard,HumanActionRejected
        if current_human_action_guard() is None:
            raise HumanActionRejected('HUMAN_ACTION_SCOPE_REQUIRED')
        return await self._request(None,result_type,builder=builder,deadline_at=deadline_at)

    async def _request(self, command: BrowserCommand, result_type: type[TResult], *, builder=None, deadline_at=None, close_request=False) -> TResult:
        from ..owner_port import current_human_action_guard,current_resource_close_guard, HumanActionRejected
        human_guard = current_resource_close_guard() if close_request else current_human_action_guard()
        write_started = False
        process = self._process
        if process is None or process.stdin is None or process.stdout is None:
            raise RuntimeError("browser worker 不可用")
        remaining = (deadline_at if builder is not None else command.deadline_at).timestamp() - datetime.now(timezone.utc).timestamp()
        if remaining <= 0:
            if human_guard is not None:
                raise HumanActionRejected('HUMAN_ACTION_DEADLINE_EXCEEDED')
            raise asyncio.TimeoutError("browser command deadline exceeded")
        try:
            fence_error = None
            message = None
            async with asyncio.timeout(remaining):
                async with self._io_lock:
                    if human_guard is not None:
                        await asyncio.wait_for(human_guard(),timeout=min(3.0,remaining))
                    if (
                        self._command_fence is not None
                        and not isinstance(command, CloseCommand)
                        and not close_request
                    ):
                        fence_error = await self._command_fence()
                    if fence_error is None:
                        if builder is not None:
                            command=builder()
                        # A partial frame is already a possible dispatch. Only
                        # the lock/guard stage can prove that no bytes were sent.
                        write_started = True
                        if human_guard is not None:
                            human_guard.write_started = True
                        await write_frame(process.stdin, command.model_dump(mode="json"))
                        if human_guard is not None:
                            human_guard.frame_written = True
                        message = await read_frame(process.stdout)
            if fence_error is not None:
                await self._force_reap("owner_fence_failed")
                return result_type(
                    run_id=command.run_id,
                    command_id=command.command_id,
                    seq=command.seq,
                    status=ResultStatus.ERROR,
                    error_code=fence_error,
                )
            assert message is not None
            if message.get("command_id") != command.command_id:
                raise ProtocolError("response_command_mismatch")
            if message.get("run_id") != command.run_id or message.get("seq") != command.seq:
                raise ProtocolError("response_identity_mismatch")
            self._seq = max(self._seq, command.seq)
            self._remember_owned_group()
            return result_type.model_validate(message)
        except asyncio.CancelledError:
            if human_guard is not None and not write_started:
                raise HumanActionRejected('HUMAN_ACTION_DETACHED') from None
            await asyncio.shield(self._force_reap("cancelled"))
            raise
        except HumanActionRejected:
            raise
        except (
            asyncio.TimeoutError,
            ProtocolError,
            ConnectionError,
            OSError,
            ValidationError,
        ) as error:
            if human_guard is not None and not write_started:
                raise HumanActionRejected('HUMAN_ACTION_NOT_DISPATCHED') from error
            await self._force_reap("protocol_or_timeout")
            raise

    async def abort(self, reason: str = "owner_fence_failed") -> None:
        """不写协议帧，直接回收 owned worker/process tree。"""
        await self._force_reap(reason)

    async def close(self, reason: str = "completed") -> CloseResult:
        async with self._close_lock:
            if self._close_result is not None:
                return self._close_result
            if self._run is None:
                self._resource_close_confirmed = True
                self._close_result = CloseResult(
                    run_id="", command_id="", seq=0, status=ResultStatus.OK,
                    closed=True, forced=False,
                )
                return self._close_result
            from ..owner_port import current_resource_close_guard
            resource_guard=current_resource_close_guard()
            if resource_guard is not None:
                # Reject a changed cancellation owner before stopping capture
                # or reaping a dead leader's retained original process group.
                async with self._io_lock:
                    from ..owner_port import HumanActionRejected
                    try:
                        await asyncio.wait_for(resource_guard(),timeout=3)
                    except (asyncio.TimeoutError,asyncio.CancelledError,OSError) as error:
                        raise HumanActionRejected('BROWSER_CANCEL_NOT_DISPATCHED') from error
            forced = False
            frame_task = self._frame_task
            if resource_guard is None and frame_task is not None and frame_task is not asyncio.current_task():
                self._frame_task = None
                frame_task.cancel()
                await asyncio.gather(frame_task, return_exceptions=True)
            response: CloseResult | None = None
            process = self._process
            if process is not None:
                if process.returncode is None:
                    from ..owner_port import current_resource_close_guard,HumanActionRejected
                    try:
                        if current_resource_close_guard() is not None:
                            response=await self._request(None,CloseResult,
                                builder=lambda:CloseCommand(**self._command_fields(),reason=reason),
                                deadline_at=self._deadline(),close_request=True)
                        else:
                            command=CloseCommand(**self._command_fields(),reason=reason)
                            response = await self._request(command, CloseResult)
                        await asyncio.wait_for(process.wait(), timeout=5.0)
                    except HumanActionRejected:
                        raise
                    except asyncio.CancelledError:
                        forced = True
                        await asyncio.shield(self._force_reap("close_cancelled"))
                        raise
                    except Exception:
                        forced = True
                        await self._force_reap("close_unconfirmed")
                else:
                    forced = True
                    await self._force_reap('close_dead_worker')
            if resource_guard is not None and frame_task is not None and frame_task is not asyncio.current_task():
                self._frame_task = None
                frame_task.cancel()
                await asyncio.gather(frame_task,return_exceptions=True)
            await self._finish_stderr()
            confirmed = bool(response is not None and response.closed and process is not None
                             and process.returncode is not None)
            if self._require_close_proof and not confirmed:
                # A dead worker alone does not prove its browser children were
                # closed. Retain the original handles for explicit verification.
                from ..owner_port import BrowserOwnerFailure
                raise BrowserOwnerFailure('BROWSER_CLOSE_VERIFICATION_REQUIRED')
            self._resource_close_confirmed = confirmed
            self._close_windows_job()
            self._process = None
            self._close_result = response or CloseResult(
                run_id=self._run.run_id, command_id=f"bc_{uuid.uuid4().hex}",
                seq=self._seq, status=ResultStatus.ERROR if forced else ResultStatus.OK,
                error_code="CLOSE_UNCONFIRMED" if forced else None,
                closed=True, forced=forced,
            )
            return self._close_result

    async def _frame_loop(self) -> None:
        try:
            while (self._process is not None and self._process.returncode is None
                   and self._run is not None):
                from src.tools.browser.view_hub import browser_view_hub
                if browser_view_hub.observer_count(self._run.tenant_id, self._run.run_id):
                    await self._capture_frame()
                if self._process is None or self._process.returncode is not None:
                    return
                await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            return
        except Exception:
            # 画面链路失败不能破坏浏览器命令主链路。
            return

    async def _capture_frame(self) -> None:
        if self._run is None or self._process is None or self._process.returncode is not None:
            return
        try:
            command = ScreenshotCommand(
                run_id=self._run.run_id, seq=max(1, self._seq),
                command_id=f"bf_{uuid.uuid4().hex}", deadline_at=self._deadline(),
            )
            result = await self._request(command, ScreenshotResult)
            if result.status != ResultStatus.OK or not result.jpeg_base64:
                return
            data = base64.b64decode(result.jpeg_base64, validate=True)
            from src.tools.browser.view_hub import BrowserFrame, browser_view_hub
            self._frame_seq += 1
            await browser_view_hub.publish(self._run.tenant_id, BrowserFrame(
                run_id=self._run.run_id, seq=self._frame_seq, jpeg=data,
                width=result.width, height=result.height,
                captured_at=datetime.now(timezone.utc).timestamp(),
            ))
        except asyncio.CancelledError:
            raise
        except Exception:
            return

    async def _force_reap(self, reason: str) -> None:
        """只回收当前 executor 明确拥有的进程/进程组。"""
        process = self._process
        if process is None:
            await self._finish_stderr()
            self._close_windows_job()
            return
        logger.warning("browser worker 需要强制回收: reason={}", reason)
        if os.name == "nt" and self._windows_job is not None:
            self._windows_job.terminate(1)
        elif os.name == "posix" and self._owned_pgid is not None:
            self._signal_owned_group(signal.SIGTERM)
        elif process.returncode is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
        try:
            await asyncio.wait_for(process.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            if os.name == "posix" and self._owned_pgid is not None:
                self._signal_owned_group(signal.SIGKILL)
            else:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            try:
                await asyncio.wait_for(process.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                logger.error("browser worker 回收确认超时")
                if self._require_close_proof:
                    from ..owner_port import BrowserOwnerFailure
                    raise BrowserOwnerFailure('BROWSER_CLOSE_VERIFICATION_REQUIRED')
        if os.name == 'posix' and self._owned_pgid is not None:
            # A dead leader does not imply its original Chromium group died.
            if not await self._wait_owned_group(1.0):
                self._signal_owned_group(signal.SIGKILL)
                if not await self._wait_owned_group(2.0) and self._require_close_proof:
                    from ..owner_port import BrowserOwnerFailure
                    raise BrowserOwnerFailure('BROWSER_CLOSE_VERIFICATION_REQUIRED')
        await self._finish_stderr()
        self._close_windows_job()

    async def _wait_owned_group(self, seconds: float) -> bool:
        deadline = asyncio.get_running_loop().time() + seconds
        while True:
            if not self._owned_group_alive():
                return True
            if asyncio.get_running_loop().time() >= deadline:
                return False
            await asyncio.sleep(0.05)

    def _owned_group_alive(self) -> bool:
        if self._group_signal_revoked:
            return False
        if sys.platform.startswith('linux'):
            members = self._current_group_members()
            if members == {}:
                self._group_signal_revoked = True
                return False
            # Unknown is not a kill capability; _signal checks exact births.
            return True
        try:
            os.killpg(self._owned_pgid, 0)
        except ProcessLookupError:
            self._group_signal_revoked = True
            return False
        return True

    @staticmethod
    def _process_identity(pid: int) -> tuple[int, int, str] | None:
        from pathlib import Path
        try:
            fields = Path('/proc', str(pid), 'stat').read_text().rsplit(')', 1)[1].split()
            return int(fields[2]), int(fields[19]), fields[0]
        except (OSError, ValueError, IndexError):
            return None

    def _current_group_members(self) -> dict[int, int] | None:
        from pathlib import Path
        members = {}
        try:
            for path in Path('/proc').glob('[0-9]*/stat'):
                try:
                    fields = path.read_text().rsplit(')', 1)[1].split()
                    if int(fields[2]) == self._owned_pgid and fields[0] != 'Z':
                        members[int(path.parent.name)] = int(fields[19])
                except FileNotFoundError:
                    continue
                except (OSError, ValueError, IndexError):
                    return None
        except OSError:
            return None
        return members

    def _remember_owned_group(self) -> None:
        if (self._process is None or self._owned_pgid is None
                or self._group_signal_revoked or not sys.platform.startswith('linux')):
            return
        leader = self._process_identity(self._process.pid)
        if (leader is None or self._leader_birth is None
                or leader[:2] != (self._owned_pgid, self._leader_birth)
                or leader[2] == 'Z'):
            return
        members = self._current_group_members()
        if members is not None:
            self._group_members.update(members)

    def _signal_owned_group(self, signal_number: int) -> None:
        if self._group_signal_revoked:
            return
        if sys.platform.startswith('linux'):
            self._remember_owned_group()
            members = self._current_group_members()
            if members == {}:
                self._group_signal_revoked = True
                return
            authorized = members is not None and any(
                self._group_members.get(pid) == birth for pid, birth in members.items())
        else:
            # The original unreaped child handle prevents PID reuse. A dead
            # leader has no portable birth proof and grants no numeric kill.
            authorized = self._process is not None and self._process.returncode is None
        if not authorized:
            from ..owner_port import BrowserOwnerFailure
            raise BrowserOwnerFailure('BROWSER_PROCESS_OWNER_VERIFICATION_REQUIRED')
        try:
            os.killpg(self._owned_pgid, signal_number)
        except ProcessLookupError:
            self._group_signal_revoked = True

    def _close_windows_job(self) -> None:
        job, self._windows_job = self._windows_job, None
        if job is not None:
            job.close()

    async def _drain_stderr(self) -> None:
        """持续排空 stderr 防死锁；只记字节计数，不记录潜在页面正文。"""
        process = self._process
        if process is None or process.stderr is None:
            return
        total = 0
        while True:
            chunk = await process.stderr.read(4096)
            if not chunk:
                break
            total = min(total + len(chunk), 64 * 1024)
        if total:
            logger.debug("browser worker 诊断已丢弃: bytes={}", total)

    async def _finish_stderr(self) -> None:
        task = self._stderr_task
        self._stderr_task = None
        if task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=1.0)
            except asyncio.TimeoutError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            except asyncio.CancelledError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                # Shield also raises when the stderr child itself was already
                # cancelled. Only a caller cancellation must escape cleanup;
                # swallowing it can keep a dead worker's frame loop alive.
                current = asyncio.current_task()
                if current is None or current.cancelling():
                    raise
