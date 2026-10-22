"""真实worker调度/心跳；仓储与任务体为内存边界，不访问数据库或LLM。"""

import asyncio
from types import SimpleNamespace

import pytest

from src.services.agent_runner.ownership import Attempt, LeaseLost
from src.services.agent_runner.worker import RunnerWorker
from src.services.agent_runner.usage_context import UsageScope
from src.core.agent_engine.contracts import CheckpointFailure
import psycopg2


class WorkerHarness:
    def __init__(self, names, *, concurrency=2):
        self.worker = object.__new__(RunnerWorker)
        self.worker.config = SimpleNamespace(
            enabled=True, concurrency=concurrency, poll_seconds=0.001,
            heartbeat_seconds=0.005, lease_seconds=30,
        )
        self.worker.stopping = asyncio.Event()
        self.worker.jobs = {}
        self.worker.sources = None
        self.names = iter(names)
        self.started = {name: asyncio.Event() for name in names}
        self.handlers = {}
        self.tasks = {}
        self.acquired, self.completed, self.cancelled, self.interrupted = [], [], [], []
        self.opened, self.closed = 0, 0
        self.worker.factory = SimpleNamespace(open=self.open, close=self.close)
        self.worker.executions = SimpleNamespace(
            reap_expired=lambda: None, heartbeat=self.failed_heartbeat,
        )
        self.worker.finalizer = SimpleNamespace(settle_ready=lambda: None)
        self.worker._acquire_next = self.acquire
        self.worker.execute = self.execute

    async def open(self):
        self.opened += 1

    async def close(self):
        # 共享资源必须在所有owned执行任务收尾之后关闭。
        assert all(task.done() for task in self.tasks.values())
        self.closed += 1

    async def acquire(self):
        name = next(self.names, None)
        if name is not None:
            self.acquired.append(name)
        return name

    @staticmethod
    def failed_heartbeat(attempt, lease_seconds):
        raise LeaseLost("RUNNER_ATTEMPT_EXPIRED")

    async def execute(self, name):
        task = asyncio.current_task()
        self.tasks[name] = task
        self.worker.jobs[task] = SimpleNamespace(
            interrupt=lambda: self.interrupted.append(name),
        )
        self.started[name].set()
        try:
            handler = self.handlers.get(name)
            if handler:
                await handler()
            self.completed.append(name)
        except asyncio.CancelledError:
            self.cancelled.append(name)
            raise
        finally:
            self.worker.jobs.pop(task, None)

    async def wait_started(self, *names):
        await asyncio.wait_for(
            asyncio.gather(*(self.started[name].wait() for name in names)), timeout=2,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize('failure,reason', [
    (LeaseLost('RUNNER_ATTEMPT_EXPIRED'), '租约已失效'),
    (psycopg2.OperationalError('fixture connection lost'), '心跳存储暂不可用'),
    (RuntimeError('fixture unexpected heartbeat failure'), '心跳发生异常'),
])
async def test_heartbeat_stops_owned_io_with_the_actual_public_cause(failure, reason):
    harness = WorkerHarness([])
    def fail(*args): raise failure
    harness.worker.executions.heartbeat = fail
    control = SimpleNamespace(interrupt=lambda: None)
    owner = asyncio.create_task(asyncio.Event().wait())
    await harness.worker._heartbeat(Attempt('original', 'worker', 1), control, owner)
    with pytest.raises(asyncio.CancelledError): await owner
    assert reason in control.interruption_reason
    assert str(failure) not in control.interruption_reason, 'raw exceptions must not become public details'


def test_explicit_service_stop_is_distinguishable_from_lease_or_storage_failure():
    harness = WorkerHarness([])
    # A hashable task stand-in is enough for stop's existing owned job map.
    class Task:
        def cancel(self): pass
    control = SimpleNamespace(interrupt=lambda: None)
    harness.worker.jobs = {Task(): control}
    harness.worker.stop()
    assert '服务正在停止' in control.interruption_reason
    assert harness.worker.stopping.is_set()


def test_usage_ownership_loss_keeps_its_cause_but_real_storage_failure_still_blocks():
    scope = UsageScope(Attempt('original', 'worker', 1), None, 'original')
    lost = LeaseLost('RUNNER_ATTEMPT_EXPIRED')
    scope.fail(lost)
    with pytest.raises(LeaseLost) as rejected: scope.check()
    assert rejected.value is lost
    scope = UsageScope(scope.attempt, None, 'original')
    scope.fail(psycopg2.OperationalError('fixture database unavailable'))
    with pytest.raises(CheckpointFailure, match='RUNNER_USAGE_STORAGE_FAILED'): scope.check()


@pytest.mark.asyncio
@pytest.mark.parametrize("concurrency", [1, 2])
async def test_lease_cancel_keeps_peer_and_next_attempt_running(concurrency):
    """真实心跳取消只结束原attempt，不能杀peer；取消须计入max_tasks以允许有界退出。"""
    harness = WorkerHarness(["expired", "peer", "next"], concurrency=concurrency)
    old_attempt_io = []

    async def expired():
        owner = asyncio.current_task()
        heartbeat = asyncio.create_task(harness.worker._heartbeat(
            Attempt("expired", "test-worker", 1), harness.worker.jobs[owner], owner,
        ))
        try:
            await asyncio.Event().wait()
            old_attempt_io.append("forbidden_model_or_tool_io")
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

    async def peer():
        if concurrency == 2:
            # peer占着并发位，第三项必须靠expired释放的位进入。
            await harness.started["next"].wait()

    harness.handlers.update(expired=expired, peer=peer)
    result = await asyncio.wait_for(harness.worker.run(max_tasks=3), timeout=2)

    assert result == 3
    assert harness.acquired == ["expired", "peer", "next"]
    assert set(harness.completed) == {"peer", "next"}
    assert harness.cancelled == ["expired"]
    assert harness.interrupted == ["expired"]
    assert old_attempt_io == []
    assert harness.opened == harness.closed == 1


@pytest.mark.asyncio
async def test_cancelled_attempt_keeps_slot_until_cleanup_finishes():
    """取消请求不等于收尾已完成；不提前复用尚未退出的执行槽。"""
    harness = WorkerHarness(["cancelled", "next"], concurrency=1)
    cleaning, release = asyncio.Event(), asyncio.Event()

    async def cancelled():
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()

    harness.handlers["cancelled"] = cancelled
    runner = asyncio.create_task(harness.worker.run(max_tasks=2))
    try:
        await harness.wait_started("cancelled")
        harness.tasks["cancelled"].cancel()
        await asyncio.wait_for(cleaning.wait(), timeout=2)
        await asyncio.sleep(0.01)
        assert not harness.started["next"].is_set()
        assert not runner.done()
    finally:
        release.set()
    assert await asyncio.wait_for(runner, timeout=2) == 2
    assert harness.completed == ["next"]
    assert harness.cancelled == ["cancelled"]
    assert harness.closed == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled", [False, True])
async def test_once_collects_one_attempt_even_when_it_was_cancelled(cancelled):
    """运维--once可正常回收取消任务，不能把一次执行取消升级为进程异常。"""
    harness = WorkerHarness(["one", "unclaimed"])

    async def cancel():
        raise asyncio.CancelledError()

    if cancelled:
        harness.handlers["one"] = cancel
    assert await asyncio.wait_for(harness.worker.run(once=True), timeout=2) == 1
    assert harness.acquired == ["one"]
    assert harness.cancelled == (["one"] if cancelled else [])
    assert harness.closed == 1


@pytest.mark.asyncio
async def test_once_without_pending_work_returns_zero():
    """空队列不能交给asyncio.wait空集合而产生新异常。"""
    harness = WorkerHarness([])
    assert await harness.worker.run(once=True) == 0
    assert harness.closed == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("once", [False, True])
async def test_external_worker_cancel_still_propagates_and_drains_owned_tasks(once):
    """主协程取消是服务生命周期信号，必须传播并等待所有任务退出。"""
    names = ["one"] if once else ["one", "two"]
    harness = WorkerHarness(names)
    for name in names:
        harness.handlers[name] = asyncio.Event().wait
    runner = asyncio.create_task(harness.worker.run(once=once))
    await harness.wait_started(*names)
    assert not harness.worker.stopping.is_set()
    runner.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(runner, timeout=2)
    assert set(harness.cancelled) == set(names)
    assert harness.closed == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("once", [False, True])
async def test_explicit_stop_waits_for_task_cleanup_then_closes_resources(once):
    """显式停机仍需取消owned任务并等待收尾，而不是被任务隔离逻辑吞掉。"""
    names = ["one"] if once else ["one", "two"]
    harness = WorkerHarness(names)
    cleaning = {name: asyncio.Event() for name in names}
    release = asyncio.Event()

    async def waiting(name):
        try:
            await asyncio.Event().wait()
        finally:
            cleaning[name].set()
            await release.wait()

    for name in names:
        async def handler(name=name):
            await waiting(name)
        harness.handlers[name] = handler
    runner = asyncio.create_task(harness.worker.run(once=once))
    try:
        await harness.wait_started(*names)
        harness.worker.stop()
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in cleaning.values())), timeout=2)
        assert not runner.done()
        assert harness.closed == 0
    finally:
        release.set()
    await asyncio.wait_for(runner, timeout=2)
    assert set(harness.cancelled) == set(names)
    assert harness.closed == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("once", [False, True])
async def test_unexpected_execution_error_is_not_silently_hidden(once):
    """只隔离取消；意外执行异常仍应暴露并保留原有服务清理行为。"""
    names = ["failed"] if once else ["failed", "peer"]
    harness = WorkerHarness(names)

    async def failed():
        if not once:
            await harness.started["peer"].wait()
        raise RuntimeError("probe_unexpected_execution_fault")

    harness.handlers["failed"] = failed
    if not once:
        harness.handlers["peer"] = asyncio.Event().wait
    with pytest.raises(RuntimeError, match="probe_unexpected_execution_fault"):
        await asyncio.wait_for(harness.worker.run(once=once), timeout=2)
    assert harness.completed == []
    assert harness.cancelled == ([] if once else ["peer"])
    assert harness.closed == 1
