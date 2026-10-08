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

    def __init__(self, executor: BrowserExecutor, store, tenant_id: str, run_id: str, owner_token: str,
                 execution_owner=None):
        self._executor = executor
        self._store = store
        self._tenant_id = tenant_id
        self._run_id = run_id
        self._owner_token = owner_token
        self.execution_owner = execution_owner
        if execution_owner is not None:
            require_proof=getattr(executor,'require_resource_close_proof',None)
            if require_proof is None:
                from ..owner_port import BrowserOwnerFailure
                raise BrowserOwnerFailure('BROWSER_CLOSE_PROOF_NOT_SUPPORTED')
            require_proof()
        self._native_fence = hasattr(executor, "set_command_fence")
        if self._native_fence:
            executor.set_command_fence(self._fence)

    @property
    def run_id(self) -> str | None:
        return self._executor.run_id

    async def _fence(self) -> str | None:
        error = await self._store.fence_owner(
            self._tenant_id, self._run_id, self._owner_token, time.time()
        )
        if self.execution_owner is not None:
            from ..owner_port import BrowserOwnerFailure, current_browser_agent_action_owner
            if error is not None:
                raise BrowserOwnerFailure(error)
            action_owner = current_browser_agent_action_owner()
            if action_owner is not None and action_owner is not self.execution_owner:
                raise BrowserOwnerFailure('BROWSER_AGENT_SCOPE_MISMATCH')
            if action_owner is self.execution_owner:
                # The native executor invokes this inside its IO lock directly
                # before writing the original command frame. Human/frame IO has
                # a separate Browser lease and never uses a parked Runner fence.
                await self.execution_owner.authorize_agent_action()
        return error

    @property
    def resource_close_confirmed(self):
        return getattr(self._executor, 'resource_close_confirmed', False)

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

    async def execute_deferred(self,builder,result_type,*,deadline_at):
        from ..owner_port import HumanActionRejected
        deferred=getattr(self._executor,'execute_deferred',None)
        if not self._native_fence or deferred is None:
            raise HumanActionRejected('HUMAN_DEFERRED_IPC_REQUIRED')
        return await deferred(builder,result_type,deadline_at=deadline_at)

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
