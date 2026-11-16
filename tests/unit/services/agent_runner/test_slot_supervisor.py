"""One blocked execution must keep its lease without making a finished slot reusable."""

import psycopg2
import pytest
from types import SimpleNamespace

from src.services.agent_runner.ownership import Attempt
from src.services.agent_runner.execution_repository import ExecutionRepository
from src.services.agent_runner.supervisor import RunnerSlotSupervisor, SlotState, _die_with_parent, _stop_worker


class MemoryRepository:
    def __init__(self):
        self.active = {}
        self.finished = set()
        self.renewed = []
        self.finish_checks = []
        self.cancel_requested = set()

    def owned_attempts(self, worker_id):
        return [attempt for attempt in self.active.values() if attempt.worker_id == worker_id]

    def heartbeat(self, attempt, lease_seconds):
        self.renewed.append((attempt, lease_seconds))
        return {'closed': False}

    def business_finished(self, attempt):
        self.finish_checks.append(attempt)
        return attempt in self.finished

    def peek_cancel_requested(self, runner_id):
        return runner_id in self.cancel_requested


class Process:
    pid = 12345

    def __init__(self):
        self.exited = False

    def poll(self):
        return 0 if self.exited else None


def test_blocked_slot_renews_only_its_live_exact_attempt(tmp_path):
    repository = MemoryRepository()
    first = Attempt('tenant-a-runner', 'worker-a', 2)
    second = Attempt('tenant-b-runner', 'worker-b', 1)
    repository.active = {'a': first, 'b': second}
    slot = SlotState(tmp_path / 'slot')
    slot.reset('worker-a')
    slot.busy(first)
    supervisor = RunnerSlotSupervisor(repository)
    supervisor.lease_seconds = 30

    assert supervisor._observe(Process(), slot, 'worker-a', None) is None
    assert repository.renewed == [(first, 30)]
    assert repository.finish_checks == [], 'active attempts do not need checkpoint reads'
    assert slot.read()['state'] == 'BUSY'


def test_cleanup_timer_requires_committed_release_and_same_busy_identity(tmp_path, monkeypatch):
    repository = MemoryRepository()
    attempt = Attempt('runner', 'worker', 3)
    slot = SlotState(tmp_path / 'slot')
    slot.reset('worker')
    slot.busy(attempt)
    clock = [0]
    supervisor = RunnerSlotSupervisor(repository, cleanup_seconds=120, clock=lambda: clock[0])
    supervisor.lease_seconds = 30
    process = Process()

    assert supervisor._observe(process, slot, 'worker', None) is None
    repository.finished.add(attempt)
    timer = supervisor._observe(process, slot, 'worker', None)
    assert timer == (('runner', 3), 0)
    clock[0] = 119
    assert supervisor._observe(process, slot, 'worker', timer) == timer
    slot.idle(attempt)
    clock[0] = 121
    assert supervisor._observe(process, slot, 'worker', timer) is None
    assert process.exited is False


def test_cleanup_rechecks_identity_under_lock_before_kill(tmp_path, monkeypatch):
    repository = MemoryRepository()
    attempt = Attempt('runner', 'worker', 3)
    slot = SlotState(tmp_path / 'slot')
    slot.reset('worker')
    slot.busy(attempt)
    repository.finished.add(attempt)
    clock = [121]
    supervisor = RunnerSlotSupervisor(repository, cleanup_seconds=120, clock=lambda: clock[0])
    supervisor.lease_seconds = 30
    process = Process()

    # Simulate IDLE being committed just before the supervisor's locked CAS.
    original = slot.locked
    count = [0]
    def racing_lock():
        count[0] += 1
        if count[0] == 2:
            slot.idle(attempt)
        return original()
    monkeypatch.setattr(slot, 'locked', racing_lock)
    assert supervisor._observe(process, slot, 'worker', (('runner', 3), 0)) is None
    assert process.exited is False


def test_worker_id_reset_fences_old_child_state(tmp_path):
    slot = SlotState(tmp_path / 'slot')
    old = Attempt('runner', 'old-worker', 1)
    slot.reset(old.worker_id)
    slot.busy(old)
    slot.reset('new-worker')
    try:
        slot.idle(old)
    except RuntimeError as error:
        assert str(error) == 'RUNNER_SLOT_OWNER_CHANGED'
    else:
        raise AssertionError('old child must not mark new process idle')
    assert slot.read() == {'state': 'IDLE', 'worker_id': 'new-worker'}


def test_parent_death_guard_accepts_supervisor_as_pid_one(monkeypatch):
    class Libc:
        def prctl(self, option, signal_number):
            assert option == 1
            return 0
    monkeypatch.setattr('src.services.agent_runner.supervisor.ctypes.CDLL', lambda _: Libc())
    monkeypatch.setattr('src.services.agent_runner.supervisor.signal.SIGKILL', 9, raising=False)
    monkeypatch.setattr('src.services.agent_runner.supervisor.os.getppid', lambda: 1)
    monkeypatch.setattr('src.services.agent_runner.supervisor.os._exit',
                        lambda code: pytest.fail('live PID 1 supervisor was mistaken for dead'))
    _die_with_parent(1)


def test_normal_stop_signals_worker_before_waiting_for_drain(monkeypatch):
    events = []
    class Child:
        pid = 12345
        exited = False
        def poll(self): return 0 if self.exited else None
        def terminate(self): events.append('worker-term')
        def wait(self, timeout=None):
            events.append('worker-drain')
            self.exited = True
    monkeypatch.setattr('src.services.agent_runner.supervisor.os.killpg',
                        lambda pid, signum: events.append('group-cleanup'), raising=False)
    _stop_worker(Child(), grace_seconds=30)
    assert events[:2] == ['worker-term', 'worker-drain']


def test_owner_scan_filters_worker_status_and_database_lease():
    class Cursor:
        def execute(self, sql, params):
            assert "worker_id=%s" in sql
            assert "status IN ('running','finalizing')" in sql
            assert 'lease_until>clock_timestamp()' in sql
            assert params == ('worker-a',)
        def fetchall(self):
            return [{'runner_id': 'runner', 'attempt': 4}]
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def cursor(self): return Cursor()
    repository = ExecutionRepository(connection_factory=Connection)
    assert repository.owned_attempts('worker-a') == [Attempt('runner', 'worker-a', 4)]


def test_heartbeat_retries_transient_row_lock_contention(monkeypatch):
    """P0②：行锁竞争（save_checkpoint 并发持行）是瞬时状况，短退避重试后成功。"""
    from unittest.mock import patch
    calls = []
    def once(attempt, lease_seconds):
        calls.append(lease_seconds)
        if len(calls) < 3:
            raise psycopg2.errors.LockNotAvailable('fixture contention')
        return {'closed': False, 'cancel_requested': False}
    repository = ExecutionRepository(connection_factory=lambda: None)
    monkeypatch.setattr(repository, '_heartbeat_once', once)
    with patch('time.sleep') as slept:
        flags = repository.heartbeat(Attempt('runner', 'worker', 1), 30)
    assert flags == {'closed': False, 'cancel_requested': False}
    assert len(calls) == 3
    assert slept.call_count == 2, 'retry backoff happens between contended beats'


def test_heartbeat_contention_exhaustion_propagates_without_renaming():
    """重试耗尽后原样抛出 LockNotAvailable，不得改判为租约丢失。"""
    from unittest.mock import patch
    def once(attempt, lease_seconds):
        raise psycopg2.errors.LockNotAvailable('fixture contention')
    repository = ExecutionRepository(connection_factory=lambda: None)
    monkeypatch_safe = once
    repository._heartbeat_once = monkeypatch_safe
    with patch('time.sleep'):
        with pytest.raises(psycopg2.errors.LockNotAvailable):
            repository.heartbeat(Attempt('runner', 'worker', 1), 30)


def test_peek_cancel_requested_is_plain_mvcc_read():
    class Cursor:
        def execute(self, sql, params):
            assert 'FOR UPDATE' not in sql, 'supervision bound must never block on the row lock'
            assert sql.count('SELECT') == 1
            assert params == ('runner',)
        def fetchone(self):
            return {'cancel_requested': True}
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def cursor(self): return Cursor()
    repository = ExecutionRepository(connection_factory=Connection)
    assert repository.peek_cancel_requested('runner') is True


def test_peek_cancel_requested_tolerates_missing_row():
    class Cursor:
        def execute(self, sql, params): pass
        def fetchone(self): return None
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def cursor(self): return Cursor()
    repository = ExecutionRepository(connection_factory=Connection)
    assert repository.peek_cancel_requested('gone') is False


def test_dead_worker_exits_slot_container_before_any_new_child(monkeypatch):
    from src.config.settings import settings
    calls = []
    class DeadChild:
        pid = 12345
        def poll(self): return 1
    monkeypatch.setattr(settings, 'agent_runner', SimpleNamespace(
        enabled=True, peers={'bridge': object()}, web_service_token='fixture',
        concurrency=1, lease_seconds=30, cancel_grace_seconds=1200,
        slot_concurrency=1))
    monkeypatch.setattr('src.db.database.init_postgres_pool',
                        lambda **kwargs: calls.append(('pool', kwargs)))
    monkeypatch.setattr('src.db.database.close_postgres_pool', lambda: None)
    monkeypatch.setattr('src.services.agent_runner.repository.RunnerRepository.assert_schema',
                        lambda self: None)
    monkeypatch.setattr('src.services.agent_runner.supervisor.subprocess.Popen',
                        lambda *args, **kwargs: (calls.append(('child', args[0])), DeadChild())[1])
    monkeypatch.setattr('src.services.agent_runner.supervisor.os.killpg',
                        lambda pid, signum: None, raising=False)

    with pytest.raises(RuntimeError, match='AGENT_RUNNER_SLOT_CONTAINER_RESTART_REQUIRED'):
        RunnerSlotSupervisor(MemoryRepository()).run()
    assert calls[0] == ('pool', {'minconn': 1, 'maxconn': 2})
    assert len([call for call in calls if call[0] == 'child']) == 1


def test_linux_entrypoint_requires_pid_one_before_spawning(monkeypatch):
    from src.services.agent_runner import supervisor
    monkeypatch.setattr(supervisor.sys, 'argv', ['supervisor'])
    monkeypatch.setattr(supervisor.sys, 'platform', 'linux')
    monkeypatch.setattr(supervisor.os, 'getpid', lambda: 42)
    with pytest.raises(RuntimeError, match='AGENT_RUNNER_SLOT_REQUIRES_CONTAINER_PID_ONE'):
        supervisor.main()


def _busy_slot(tmp_path, attempt):
    slot = SlotState(tmp_path / 'slot')
    slot.reset(attempt.worker_id)
    slot.busy(attempt)
    return slot


def test_cancel_grace_forces_recovery_after_bound(tmp_path, monkeypatch):
    repository = MemoryRepository()
    attempt = Attempt('runner', 'worker', 3)
    slot = _busy_slot(tmp_path, attempt)
    repository.cancel_requested.add('runner')
    clock = [0]
    supervisor = RunnerSlotSupervisor(repository, cancel_grace_seconds=1200,
                                      clock=lambda: clock[0])
    supervisor.lease_seconds = 30
    process = Process()
    killed = []
    monkeypatch.setattr('src.services.agent_runner.supervisor.os.killpg',
                        lambda pid, signum: killed.append(pid), raising=False)
    monkeypatch.setattr('src.services.agent_runner.supervisor._bounded_wait',
                        lambda process, **kwargs: None)

    supervisor._observe(process, slot, 'worker', None)
    assert killed == [], 'cancel within grace must not be killed'
    clock[0] = 1201
    supervisor._observe(process, slot, 'worker', None)
    assert killed == [process.pid], 'stuck cancel beyond grace must be force recovered'
    assert supervisor._cancel_since == {}, 'bound state resets after recovery'


def test_cancel_grace_resets_when_request_cleared(tmp_path, monkeypatch):
    repository = MemoryRepository()
    attempt = Attempt('runner', 'worker', 3)
    slot = _busy_slot(tmp_path, attempt)
    repository.cancel_requested.add('runner')
    clock = [0]
    supervisor = RunnerSlotSupervisor(repository, cancel_grace_seconds=1200,
                                      clock=lambda: clock[0])
    supervisor.lease_seconds = 30
    process = Process()
    monkeypatch.setattr('src.services.agent_runner.supervisor.os.killpg',
                        lambda pid, signum: pytest.fail('must not kill'), raising=False)

    supervisor._observe(process, slot, 'worker', None)
    clock[0] = 1300
    repository.cancel_requested.clear()
    supervisor._observe(process, slot, 'worker', None)
    clock[0] = 2600
    repository.cancel_requested.add('runner')
    supervisor._observe(process, slot, 'worker', None)
    assert slot.read()['state'] == 'BUSY', 'fresh cancel observation restarts the bound'


def test_lock_contention_skips_renewal_but_keeps_recovery_reachable(tmp_path, monkeypatch):
    repository = MemoryRepository()
    attempt = Attempt('runner', 'worker', 3)
    slot = _busy_slot(tmp_path, attempt)
    repository.finished.add(attempt)

    def contended_heartbeat(a, lease_seconds):
        raise psycopg2.errors.LockNotAvailable()
    repository.heartbeat = contended_heartbeat

    clock = [0]
    supervisor = RunnerSlotSupervisor(repository, cleanup_seconds=120,
                                      clock=lambda: clock[0])
    supervisor.lease_seconds = 30
    supervisor.cancel_grace_seconds = 1200
    process = Process()
    monkeypatch.setattr('src.services.agent_runner.supervisor.os.killpg',
                        lambda pid, signum: process.__setattr__('exited', True),
                        raising=False)
    monkeypatch.setattr('src.services.agent_runner.supervisor._bounded_wait',
                        lambda process, **kwargs: None)

    timer = supervisor._observe(process, slot, 'worker', None)
    assert timer == (('runner', 3), 0), 'contention must not abort the scan before the timer'
    clock[0] = 121
    supervisor._observe(process, slot, 'worker', timer)
    assert process.exited is True, 'bounded cleanup must stay reachable under lock contention'


def test_cancel_grace_read_failure_never_kills(tmp_path, monkeypatch):
    repository = MemoryRepository()
    attempt = Attempt('runner', 'worker', 3)
    slot = _busy_slot(tmp_path, attempt)

    def broken_peek(runner_id):
        raise RuntimeError('db unavailable')
    repository.peek_cancel_requested = broken_peek
    supervisor = RunnerSlotSupervisor(repository, cancel_grace_seconds=1200)
    supervisor.lease_seconds = 30
    process = Process()
    monkeypatch.setattr('src.services.agent_runner.supervisor.os.killpg',
                        lambda pid, signum: pytest.fail('must not kill'), raising=False)
    supervisor._observe(process, slot, 'worker', None)
    assert slot.read()['state'] == 'BUSY'
