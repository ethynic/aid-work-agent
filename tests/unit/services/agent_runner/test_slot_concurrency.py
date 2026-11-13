"""Phase 7：轻池槽内并发（A 方案）与事件循环停跳看门狗单测。

覆盖：并发容量解析（角色钳制）、worker 并发领取上限、看门狗停跳检测与
执行体回收、监督进程多执行体路径（取消宽限/收尾回收按 owned_attempts）。
"""

import asyncio
import os
import threading
import time
from types import SimpleNamespace

import pytest

from src.services.agent_runner import slot_capacity
from src.services.agent_runner.ownership import Attempt
from src.services.agent_runner.slot_capacity import (
    LoopWatchdog,
    effective_slot_concurrency,
    resolve_slot_concurrency,
)


def _runner_config(slot_concurrency=4):
    return SimpleNamespace(enabled=True, slot_concurrency=slot_concurrency,
        watchdog_threshold=15, heartbeat_seconds=10, lease_seconds=30,
        poll_seconds=0.05, durable_sleep_enabled=False, concurrency=1,
        browser_owner=None)


class TestEffectiveSlotConcurrency:
    def test_light_uses_config_value(self):
        assert effective_slot_concurrency('light', _runner_config(4)) == 4

    def test_general_forced_single(self):
        assert effective_slot_concurrency('general', _runner_config(4)) == 1

    def test_invalid_role_forced_single(self):
        assert effective_slot_concurrency('weird', _runner_config(4)) == 1

    def test_light_env_fallback_to_one(self):
        assert effective_slot_concurrency('light', _runner_config(1)) == 1


class TestResolveSlotConcurrency:
    def test_explicit_overrides_config_for_light(self):
        assert resolve_slot_concurrency('light', _runner_config(4), explicit=2) == 2

    def test_explicit_still_clamped_for_general(self):
        assert resolve_slot_concurrency('general', _runner_config(4), explicit=8) == 1

    def test_none_explicit_falls_back_to_config(self):
        assert resolve_slot_concurrency('light', _runner_config(4), explicit=None) == 4


class TestConfigField:
    def test_agent_runner_config_defaults(self):
        from src.config.settings import AgentRunnerConfig
        config = AgentRunnerConfig()
        assert config.slot_concurrency == 4
        assert config.watchdog_threshold == 15

    def test_agent_runner_config_bounds(self):
        from src.config.settings import AgentRunnerConfig
        with pytest.raises(ValueError):
            AgentRunnerConfig(slot_concurrency=0)
        with pytest.raises(ValueError):
            AgentRunnerConfig(slot_concurrency=17)
        with pytest.raises(ValueError):
            AgentRunnerConfig(watchdog_threshold=4)


def _make_worker(monkeypatch, *, role='light', slot_concurrency=None,
                 config=None, **overrides):
    """构造仅依赖 fake 的 RunnerWorker，避开 DB/Agent 真实初始化。"""
    monkeypatch.setenv('AGENT_RUNNER_ROLE', role)
    from src.services.agent_runner.worker import RunnerWorker
    config = config or _runner_config()
    kwargs = dict(
        execution_repository=SimpleNamespace(reap_expired=lambda: []),
        repository=SimpleNamespace(),
        authorizer=SimpleNamespace(),
        usage_repository=SimpleNamespace(),
        finalizer=SimpleNamespace(settle_ready=lambda: []),
        runtime_factory=SimpleNamespace(),
        tool_recovery=SimpleNamespace(),
    )
    kwargs.update(overrides)
    return RunnerWorker(config, slot_concurrency=slot_concurrency, **kwargs)


class TestWorkerSlotConcurrency:
    def test_light_default_from_config(self, monkeypatch):
        worker = _make_worker(monkeypatch, role='light')
        assert worker.slot_concurrency == 4

    def test_general_clamped_even_with_explicit(self, monkeypatch):
        worker = _make_worker(monkeypatch, role='general', slot_concurrency=8)
        assert worker.slot_concurrency == 1

    @pytest.mark.asyncio
    async def test_run_worker_rejects_slot_state_with_concurrency(self, monkeypatch):
        # slot_state 单值语义只允许单执行体；并发模式不传 --slot-state-file。
        monkeypatch.setenv('AGENT_RUNNER_ROLE', 'light')
        from src.services.agent_runner.worker import run_worker
        arguments = SimpleNamespace(slot_state_file='/tmp/slot', worker_id='w1',
            slot_concurrency=2, once=False, max_tasks=None)
        # 校验位于 run_worker 顶部（任何 DB 初始化之前），await 即触发
        with pytest.raises(RuntimeError, match='SLOT_REQUIRES_SINGLE_CONCURRENCY'):
            await run_worker(arguments)

    @pytest.mark.asyncio
    async def test_run_concurrency_cap_async(self, monkeypatch):
        worker = _make_worker(monkeypatch, role='light', slot_concurrency=2)
        rows = ['r1', 'r2', 'r3']
        state = {'active': 0, 'max_active': 0, 'executed': []}

        async def fake_execute(row):
            state['active'] += 1
            state['max_active'] = max(state['max_active'], state['active'])
            await asyncio.sleep(0.05)
            state['active'] -= 1
            state['executed'].append(row)

        async def fake_acquire():
            await asyncio.sleep(0)
            return rows.pop(0) if rows else None

        monkeypatch.setattr(worker, 'execute', fake_execute)
        monkeypatch.setattr(worker, '_acquire_next', fake_acquire)
        completed = await worker.run(max_tasks=3)
        assert completed == 3
        assert state['max_active'] == 2
        assert sorted(state['executed']) == ['r1', 'r2', 'r3']

    @pytest.mark.asyncio
    async def test_run_single_concurrency_matches_legacy(self, monkeypatch):
        worker = _make_worker(monkeypatch, role='light', slot_concurrency=1)
        rows = ['r1', 'r2']
        state = {'active': 0, 'max_active': 0}

        async def fake_execute(row):
            state['active'] += 1
            state['max_active'] = max(state['max_active'], state['active'])
            await asyncio.sleep(0.02)
            state['active'] -= 1

        async def fake_acquire():
            await asyncio.sleep(0)
            return rows.pop(0) if rows else None

        monkeypatch.setattr(worker, 'execute', fake_execute)
        monkeypatch.setattr(worker, '_acquire_next', fake_acquire)
        completed = await worker.run(max_tasks=2)
        assert completed == 2
        assert state['max_active'] == 1


class _FakeLoop:
    """call_soon_threadsafe 排队但不执行回调——模拟事件循环停跳。"""

    def __init__(self, execute_probes=True):
        self.execute_probes = execute_probes
        self.submitted = 0

    def call_soon_threadsafe(self, callback, *args):
        self.submitted += 1
        if self.execute_probes:
            callback(*args)


class TestLoopWatchdog:
    def _fake_executions(self, attempts):
        record = {'interrupted': []}
        executions = SimpleNamespace(
            owned_attempts=lambda worker_id: list(attempts),
            interrupt_attempt=lambda attempt, **kwargs: record['interrupted'].append(attempt),
        )
        return executions, record

    def test_recover_owned_attempts(self, monkeypatch):
        attempts = [Attempt('r1', 'w1', 1), Attempt('r2', 'w1', 1)]
        executions, record = self._fake_executions(attempts)
        loop = _FakeLoop(execute_probes=False)
        watchdog = LoopWatchdog(loop, executions, 'w1', threshold_seconds=0.1)
        reclaimed = watchdog._recover_owned_attempts()
        assert [a.runner_id for a in reclaimed] == ['r1', 'r2']
        assert record['interrupted'] == attempts

    def test_recover_tolerates_db_failure(self, monkeypatch):
        def boom(attempt):
            raise RuntimeError('db down')
        executions = SimpleNamespace(
            owned_attempts=lambda worker_id: [Attempt('r1', 'w1', 1)],
            interrupt_attempt=boom)
        loop = _FakeLoop(execute_probes=False)
        watchdog = LoopWatchdog(loop, executions, 'w1', threshold_seconds=0.1)
        # 单个 attempt 回收失败不阻断后续回收，也不抛出
        assert watchdog._recover_owned_attempts() is not None

    @pytest.mark.asyncio
    async def test_responsive_loop_never_triggers(self, monkeypatch):
        executions, _ = self._fake_executions([])
        watchdog = LoopWatchdog(asyncio.get_running_loop(), executions, 'w1',
            threshold_seconds=0.3, probe_interval=0.05)
        watchdog.start()
        await asyncio.sleep(0.5)
        watchdog.stop()
        assert not watchdog._triggered

    def test_frozen_loop_triggers_reclaim_and_exit(self, monkeypatch):
        attempts = [Attempt('r1', 'w1', 1)]
        executions, record = self._fake_executions(attempts)
        loop = _FakeLoop(execute_probes=False)  # 探针永不执行 = 循环停跳
        watchdog = LoopWatchdog(loop, executions, 'w1',
            threshold_seconds=0.1, probe_interval=0.05)
        exited = threading.Event()

        def fake_exit(code):
            record['exit_code'] = code
            exited.set()

        monkeypatch.setattr(slot_capacity.os, '_exit', fake_exit)
        watchdog.start()
        assert exited.wait(timeout=5.0), 'watchdog did not trigger within timeout'
        watchdog.stop()
        assert record['exit_code'] == 1
        assert record['interrupted'] == attempts

    def test_triggers_only_once(self, monkeypatch):
        executions, record = self._fake_executions([])
        loop = _FakeLoop(execute_probes=False)
        watchdog = LoopWatchdog(loop, executions, 'w1',
            threshold_seconds=0.05, probe_interval=0.02)
        exited = threading.Event()

        def fake_exit(code):
            record['exit_count'] = record.get('exit_count', 0) + 1
            exited.set()

        monkeypatch.setattr(slot_capacity.os, '_exit', fake_exit)
        watchdog.start()
        assert exited.wait(timeout=5.0)
        time.sleep(0.1)
        watchdog.stop()
        assert record['exit_count'] == 1


class _FakeSupervisorRepository:
    def __init__(self, attempts, *, finished=(), cancel_requested=()):
        self._attempts = attempts
        self._finished = set(finished)
        self._cancel = set(cancel_requested)

    def owned_attempts(self, worker_id):
        return list(self._attempts)

    def heartbeat(self, attempt, lease_seconds):
        return {'closed': attempt.runner_id in self._finished, 'cancel_requested': False}

    def business_finished(self, attempt):
        return attempt.runner_id in self._finished

    def peek_cancel_requested(self, runner_id):
        return runner_id in self._cancel


class TestSupervisorObserveMulti:
    def _supervisor(self, repository, clock):
        from src.services.agent_runner.supervisor import RunnerSlotSupervisor
        supervisor = RunnerSlotSupervisor(repository=repository, poll_seconds=2,
            cleanup_seconds=120, cancel_grace_seconds=1200,
            worker_command=['python'], clock=clock)
        supervisor.lease_seconds = 30
        supervisor._multi_finishing = {}
        supervisor._kill_calls = []
        supervisor._kill_process_group = lambda process, attempt: supervisor._kill_calls.append(attempt)
        return supervisor

    def test_no_action_while_attempts_active(self):
        attempt = Attempt('r1', 'w1', 1)
        repo = _FakeSupervisorRepository([attempt])
        clock = SimpleNamespace()
        supervisor = self._supervisor(repo, lambda: 0.0)
        process = SimpleNamespace(poll=lambda: None)
        supervisor._observe_multi(process, 'w1')
        assert supervisor._kill_calls == []
        assert supervisor._multi_finishing == {}

    def test_finished_attempt_starts_cleanup_timer_then_kills(self):
        attempt = Attempt('r1', 'w1', 1)
        repo = _FakeSupervisorRepository([attempt], finished={'r1'})
        now = {'t': 0.0}
        supervisor = self._supervisor(repo, lambda: now['t'])
        process = SimpleNamespace(poll=lambda: None)
        supervisor._observe_multi(process, 'w1')
        assert set(supervisor._multi_finishing) == {('r1', 1)}
        assert supervisor._kill_calls == []
        now['t'] = 121.0
        supervisor._observe_multi(process, 'w1')
        assert [a.runner_id for a in supervisor._kill_calls] == ['r1']
        assert supervisor._multi_finishing == {}

    def test_lease_lost_unfinished_attempt_not_reclaimed(self):
        # 补租 LeaseLost（不在 active）但业务未完成：不得收尾回收
        attempt = Attempt('r1', 'w1', 1)
        repo = _FakeSupervisorRepository([attempt], finished=set())
        clock = SimpleNamespace()
        supervisor = self._supervisor(repo, lambda: 0.0)
        process = SimpleNamespace(poll=lambda: None)
        supervisor._observe_multi(process, 'w1')
        assert supervisor._multi_finishing == {}
        assert supervisor._kill_calls == []

    def test_cancel_grace_exceeded_kills_process(self):
        attempt = Attempt('r1', 'w1', 1)
        repo = _FakeSupervisorRepository([attempt], cancel_requested={'r1'})
        now = {'t': 0.0}
        supervisor = self._supervisor(repo, lambda: now['t'])
        process = SimpleNamespace(poll=lambda: None)
        supervisor._observe_multi(process, 'w1')
        assert supervisor._kill_calls == []
        now['t'] = 1300.0
        supervisor._observe_multi(process, 'w1')
        assert [a.runner_id for a in supervisor._kill_calls] == ['r1']

    def test_cancel_reset_clears_timer_without_kill(self):
        attempts = [Attempt('r1', 'w1', 1)]
        repo = _FakeSupervisorRepository(attempts, cancel_requested={'r1'})
        now = {'t': 0.0}
        supervisor = self._supervisor(repo, lambda: now['t'])
        process = SimpleNamespace(poll=lambda: None)
        supervisor._observe_multi(process, 'w1')
        assert ('r1' in supervisor._cancel_since)
        repo._cancel.clear()
        supervisor._observe_multi(process, 'w1')
        assert 'r1' not in supervisor._cancel_since
        assert supervisor._kill_calls == []

    def test_disappeared_attempt_timer_cleaned(self):
        attempt = Attempt('r1', 'w1', 1)
        repo = _FakeSupervisorRepository([attempt], finished={'r1'})
        now = {'t': 0.0}
        supervisor = self._supervisor(repo, lambda: now['t'])
        process = SimpleNamespace(poll=lambda: None)
        supervisor._observe_multi(process, 'w1')
        assert set(supervisor._multi_finishing) == {('r1', 1)}
        repo._attempts = []
        supervisor._observe_multi(process, 'w1')
        assert supervisor._multi_finishing == {}
        assert supervisor._kill_calls == []


class TestSupervisorSpawnMode:
    def test_multi_mode_spawns_without_slot_state_file(self):
        from src.services.agent_runner.supervisor import RunnerSlotSupervisor
        supervisor = RunnerSlotSupervisor(repository=SimpleNamespace(),
            worker_command=['python'])
        spawned = {}
        supervisor._spawn_worker = lambda worker_id, *, extra_args=(): spawned.update(
            worker_id=worker_id, extra_args=tuple(extra_args)) or SimpleNamespace(poll=lambda: 0)
        supervisor._supervise_until_exit = lambda process, scan: None
        supervisor._run_multi_slot(4)
        assert spawned['extra_args'] == ('--slot-concurrency', '4')

    def test_single_mode_spawns_with_slot_state_file(self, tmp_path):
        from src.services.agent_runner.supervisor import RunnerSlotSupervisor
        supervisor = RunnerSlotSupervisor(repository=SimpleNamespace(),
            worker_command=['python'])
        spawned = {}
        supervisor._spawn_worker = lambda worker_id, *, extra_args=(): spawned.update(
            worker_id=worker_id, extra_args=tuple(extra_args)) or SimpleNamespace(poll=lambda: 0)
        supervisor._supervise_until_exit = lambda process, scan: None
        monkey_tmp = tmp_path / 'slots'
        import src.services.agent_runner.supervisor as supervisor_module
        original_tempfile = supervisor_module.tempfile.TemporaryDirectory
        supervisor_module.tempfile.TemporaryDirectory = lambda **kwargs: _TmpDir(monkey_tmp)
        try:
            supervisor._run_single_slot()
        finally:
            supervisor_module.tempfile.TemporaryDirectory = original_tempfile
        assert spawned['extra_args'] == ('--slot-state-file', str(monkey_tmp / 'state'))


class _TmpDir:
    def __init__(self, path):
        self.name = str(path)

    def __enter__(self):
        os.makedirs(self.name, exist_ok=True)
        return self.name

    def __exit__(self, *args):
        pass
