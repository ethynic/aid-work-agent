"""为任意 BrowserExecutor 增加 owner lease fencing。"""

from __future__ import annotations

import time

from .base import BrowserExecutor
from .models import (
    BrowserRunSpec, ClickCommand, CloseResult, CommandResult, ContentCommand,
    ContentResult, FillCommand, KeyboardCommand, NavigateCommand, PointerCommand,
    ResultStatus, SelectCommand, SnapshotCommand, SnapshotResult, StartResult,
)


class FencedBrowserExecutor:
    """生产命令必须通过 store 原子 fence，close 永远直接透传。"""

    def __init__(self, executor: BrowserExecutor, store, tenant_id: str, run_id: str, owner_token: str):
        self._executor = executor
        self._store = store
        self._tenant_id = tenant_id
        self._run_id = run_id
        self._owner_token = owner_token
        self._native_fence = hasattr(executor, "set_command_fence")
        if self._native_fence:
            executor.set_command_fence(self._fence)

    @property
    def run_id(self) -> str | None:
        return self._executor.run_id

    async def _fence(self) -> str | None:
        return await self._store.fence_owner(
            self._tenant_id, self._run_id, self._owner_token, time.time()
        )

    async def _fallback_guard(self, command, result_type):
        error = await self._fence()
        if error is None:
            return None
        abort = getattr(self._executor, "abort", None)
        if abort is not None:
            await abort("owner_fence_failed")
        else:
            await self._executor.close("owner_fence_failed")
        return result_type(
            run_id=command.run_id, command_id=command.command_id, seq=command.seq,
            status=ResultStatus.ERROR, error_code=error,
        )

    async def _execute(self, command, result_type, method_name):
        if not self._native_fence:
            rejected = await self._fallback_guard(command, result_type)
            if rejected is not None:
                return rejected
        return await getattr(self._executor, method_name)(command)

    async def start(self, run: BrowserRunSpec) -> StartResult:
        return await self._executor.start(run)

    async def navigate(self, command: NavigateCommand) -> CommandResult:
        return await self._execute(command, CommandResult, "navigate")

    async def snapshot(self, command: SnapshotCommand) -> SnapshotResult:
        return await self._execute(command, SnapshotResult, "snapshot")

    async def click(self, command: ClickCommand) -> CommandResult:
        return await self._execute(command, CommandResult, "click")

    async def fill(self, command: FillCommand) -> CommandResult:
        return await self._execute(command, CommandResult, "fill")

    async def select(self, command: SelectCommand) -> CommandResult:
        return await self._execute(command, CommandResult, "select")

    async def keyboard(self, command: KeyboardCommand) -> CommandResult:
        return await self._execute(command, CommandResult, "keyboard")

    async def pointer(self, command: PointerCommand) -> CommandResult:
        return await self._execute(command, CommandResult, "pointer")

    async def content(self, command: ContentCommand) -> ContentResult:
        return await self._execute(command, ContentResult, "content")

    async def close(self, reason: str = "completed") -> CloseResult:
        return await self._executor.close(reason)

    async def abort(self, reason: str = "owner_fence_failed") -> None:
        abort = getattr(self._executor, "abort", None)
        if abort is not None:
            await abort(reason)
        else:
            await self._executor.close(reason)
