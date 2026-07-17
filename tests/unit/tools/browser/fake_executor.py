"""测试专用远端执行器，不代表生产远端能力可用。"""

from src.tools.browser.executor.models import (
    CloseResult, CommandResult, ContentResult, ResultStatus, SnapshotResult, StartResult,
)


class FakeRemoteExecutor:
    def __init__(self):
        self._run_id = None
        self.last_seq = 0
        self.results = {}
        self.closed = False

    @property
    def run_id(self):
        return self._run_id

    async def start(self, run):
        self._run_id = run.run_id
        self.last_seq = 1
        return StartResult(run_id=run.run_id, command_id="bc_start00", seq=1, status=ResultStatus.OK)

    def _result(self, command, cls=CommandResult, **kwargs):
        if command.command_id in self.results:
            return self.results[command.command_id]
        if command.seq != self.last_seq + 1:
            return cls(run_id=command.run_id, command_id=command.command_id, seq=command.seq, status=ResultStatus.ERROR, error_code="SEQ_REJECTED", **kwargs)
        self.last_seq = command.seq
        result = cls(run_id=command.run_id, command_id=command.command_id, seq=command.seq, status=ResultStatus.OK, **kwargs)
        self.results[command.command_id] = result
        return result

    async def navigate(self, command): return self._result(command, current_origin_path="https://example.test/")
    async def snapshot(self, command): return self._result(command, SnapshotResult, current_origin_path="https://example.test/")
    async def click(self, command): return self._result(command)
    async def fill(self, command): return self._result(command)
    async def select(self, command): return self._result(command)
    async def keyboard(self, command): return self._result(command)
    async def pointer(self, command): return self._result(command)
    async def content(self, command): return self._result(command, ContentResult, content="fixture", content_length=7)

    async def close(self, reason="completed"):
        self.closed = True
        return CloseResult(run_id=self._run_id or "", command_id="bc_close00", seq=self.last_seq + 1, status=ResultStatus.OK, closed=True)
