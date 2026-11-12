"""One process per execution slot: lease renewal and bounded post-commit cleanup."""

import argparse
from contextlib import contextmanager
import ctypes
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import uuid

import psycopg2
from loguru import logger

from .execution_repository import ExecutionRepository
from .ownership import Attempt, LeaseLost


class SlotState:
    """The small same-container BUSY/IDLE record, serialized with recovery kills."""

    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def locked(self):
        self.path.touch(exist_ok=True)
        with self.path.open('r+b') as stream:
            stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                stream.seek(0)
                raw = stream.read()
                state = json.loads(raw) if raw else {'state': 'IDLE'}
                yield stream, state
            finally:
                if os.name == 'nt':
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _write(stream, state):
        stream.seek(0)
        stream.write(json.dumps(state, separators=(',', ':')).encode())
        stream.truncate()
        stream.flush()

    def reset(self, worker_id):
        with self.locked() as (stream, _):
            self._write(stream, {'state': 'IDLE', 'worker_id': worker_id})

    def busy(self, attempt):
        with self.locked() as (stream, state):
            if state.get('worker_id') != attempt.worker_id or state['state'] != 'IDLE':
                raise RuntimeError('RUNNER_SLOT_OWNER_CHANGED')
            self._write(stream, {'state': 'BUSY', 'worker_id': attempt.worker_id,
                                 'runner_id': attempt.runner_id, 'attempt': attempt.number})

    def idle(self, attempt):
        with self.locked() as (stream, state):
            if self.matches(state, attempt):
                self._write(stream, {'state': 'IDLE', 'worker_id': attempt.worker_id})
            else:
                raise RuntimeError('RUNNER_SLOT_OWNER_CHANGED')

    def read(self):
        with self.locked() as (_, state):
            return state

    @staticmethod
    def matches(state, attempt):
        return (state.get('state') == 'BUSY' and state.get('worker_id') == attempt.worker_id
                and state.get('runner_id') == attempt.runner_id
                and state.get('attempt') == attempt.number)


def _die_with_parent(expected_parent_pid):
    """Linux child cannot outlive a supervisor killed without a catchable signal."""
    libc = ctypes.CDLL(None)
    if libc.prctl(1, signal.SIGKILL) != 0:
        raise OSError('PR_SET_PDEATHSIG_FAILED')
    if os.getppid() != expected_parent_pid:
        os._exit(1)


def _stop_worker(process, *, grace_seconds):
    if process.poll() is None:
        # Give only the Worker its normal stop signal. It owns cancellation,
        # checkpointing and child drain; signaling tools at the same time would
        # bypass that protocol.
        process.terminate()
        try:
            process.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            if os.name == 'posix':
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            _bounded_wait(process, runner_id=None, attempt=None)


def _bounded_wait(process, *, runner_id, attempt):
    """SIGKILL 后仍不可回收（如 NFS D-state）时以 PID 1 退出触发容器销毁，
    由进程命名空间统一回收（Phase 2 既定遏制路径），避免监督进程永挂。"""
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        logger.critical('AgentRunner slot worker unkillable runner={} attempt={}; '
                        'exiting PID 1 for container teardown',
                        runner_id, attempt)
        from src.core.temp_logger import tlog
        tlog("runner取消自愈", "SIGKILL 后 30s 仍未回收，PID 1 退出触发容器销毁 runner={runner} attempt={attempt}",
             runner=runner_id, attempt=attempt, level="ERROR")
        os._exit(1)
    # An unexpectedly dead Worker may have left same-group tool grandchildren.
    # Do not start another execution process until its group has been cleared.
    if os.name == 'posix':
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


class RunnerSlotSupervisor:
    def __init__(self, repository=None, *, poll_seconds=2, cleanup_seconds=120,
                 cancel_grace_seconds=1200, worker_command=None, clock=time.monotonic):
        self.repository = repository or ExecutionRepository()
        self.poll_seconds = poll_seconds
        self.cleanup_seconds = cleanup_seconds
        self.cancel_grace_seconds = cancel_grace_seconds
        self.worker_command = worker_command or [sys.executable, '-m', 'src.services.agent_runner.worker']
        self.clock = clock
        self.stopping = False
        # P0① 自愈：记录运行中 attempt 的 cancel_requested 首次观察时刻。
        self._cancel_since = {}

    def stop(self, *_):
        self.stopping = True

    def _observe(self, process, slot, worker_id, finishing):
        # A child blocked in a synchronous tool cannot run its own asyncio heartbeat.
        active = set()
        for attempt in self.repository.owned_attempts(worker_id):
            if process.poll() is not None:
                return None
            try:
                flags = self.repository.heartbeat(attempt, self.lease_seconds)
                if not flags.get('closed', False):
                    active.add(attempt)
            except LeaseLost:
                pass
            except psycopg2.errors.LockNotAvailable:
                # P0②：补租遇行锁竞争只跳过本拍，不得中止整轮扫描——
                # 否则下方两分钟收尾回收与取消挂死自愈永远不可达。
                logger.debug('AgentRunner slot heartbeat lock contention runner={}',
                             attempt.runner_id)
                from src.core.temp_logger import tlog
                tlog("runner心跳锁竞争", "监督补租跳过本拍 runner={runner} attempt={attempt}",
                     runner=attempt.runner_id, attempt=attempt.number)

        state = slot.read()
        if state.get('state') != 'BUSY' or state.get('worker_id') != worker_id:
            return None
        attempt = Attempt(state['runner_id'], worker_id, state['attempt'])
        self._enforce_cancel_grace(process, slot, attempt)
        if attempt in active:
            return None
        if not self.repository.business_finished(attempt):
            return None
        identity = (attempt.runner_id, attempt.number)
        if finishing is None or finishing[0] != identity:
            return identity, self.clock()
        if self.clock() - finishing[1] < self.cleanup_seconds:
            return finishing
        # The worker may have become IDLE or started another attempt since the
        # DB read. The Worker cannot claim another attempt before writing IDLE,
        # so no database call is needed while holding the cross-process lock.
        with slot.locked() as (_, current):
            if process.poll() is None and slot.matches(current, attempt):
                logger.warning('AgentRunner slot cleanup exceeded limit runner={} attempt={}',
                               attempt.runner_id, attempt.number)
                self._kill_process_group(process, attempt)
        return None

    def _enforce_cancel_grace(self, process, slot, attempt):
        """P0①：cancel_requested 已置位但执行进程超过宽限仍未提交终态（挂死，
        补租与两分钟回收均不可达）时强制回收，槽位不再永久损失。"""
        try:
            requested = self.repository.peek_cancel_requested(attempt.runner_id)
        except Exception:
            return
        if not requested:
            self._cancel_since.pop(attempt.runner_id, None)
            return
        if attempt.runner_id not in self._cancel_since:
            from src.core.temp_logger import tlog
            tlog("runner取消自愈", "首次观察到取消挂起 runner={runner} attempt={attempt} 宽限={grace}s",
                 runner=attempt.runner_id, attempt=attempt.number, grace=self.cancel_grace_seconds)
        first = self._cancel_since.setdefault(attempt.runner_id, self.clock())
        stuck_for = self.clock() - first
        if stuck_for < self.cancel_grace_seconds:
            return
        logger.warning('AgentRunner slot cancel grace exceeded runner={} attempt={} '
                       'stuck_for={:.0f}s; forcing recovery', attempt.runner_id,
                       attempt.number, stuck_for)
        from src.core.temp_logger import tlog
        tlog("runner取消自愈", "取消宽限超时强制回收 runner={runner} attempt={attempt} stuck_for={stuck:.0f}s",
             runner=attempt.runner_id, attempt=attempt.number, stuck=stuck_for, level="ERROR")
        self._cancel_since.pop(attempt.runner_id, None)
        with slot.locked() as (_, current):
            if process.poll() is None and slot.matches(current, attempt):
                self._kill_process_group(process, attempt)

    def _kill_process_group(self, process, attempt):
        try:
            if os.name == 'posix':
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            pass
        _bounded_wait(process, runner_id=attempt.runner_id, attempt=attempt.number)

    def run(self):
        from src.config.settings import settings
        from src.db.database import init_postgres_pool, close_postgres_pool
        from .repository import RunnerRepository

        config = settings.agent_runner
        if not config.enabled or not config.peers or not config.web_service_token:
            raise RuntimeError('AGENT_RUNNER_SERVICE_AUTH_REQUIRED')
        if config.concurrency != 1:
            raise RuntimeError('AGENT_RUNNER_SLOT_REQUIRES_SINGLE_CONCURRENCY')
        self.lease_seconds = config.lease_seconds
        self.cancel_grace_seconds = config.cancel_grace_seconds
        # One sequential supervisor scan needs no Worker-sized connection pool.
        init_postgres_pool(minconn=1, maxconn=2)
        try:
            RunnerRepository().assert_schema()
            with tempfile.TemporaryDirectory(prefix='agent-runner-slot-') as directory:
                slot = SlotState(Path(directory) / 'state')
                if not self.stopping:
                    worker_id = 'worker_' + uuid.uuid4().hex
                    slot.reset(worker_id)
                    parent_pid = os.getpid()
                    process = subprocess.Popen(
                        [*self.worker_command, '--worker-id', worker_id,
                         '--slot-state-file', str(slot.path)],
                        start_new_session=True,
                        **({'preexec_fn': lambda: _die_with_parent(parent_pid)}
                           if sys.platform == 'linux' else {}),
                    )
                    finishing = None
                    try:
                        while process.poll() is None and not self.stopping:
                            try:
                                finishing = self._observe(process, slot, worker_id, finishing)
                            except Exception as error:
                                logger.opt(exception=True).error(
                                    'AgentRunner slot supervision scan failed kind={}', type(error).__name__)
                            if process.poll() is None and not self.stopping:
                                time.sleep(self.poll_seconds)
                    finally:
                        _stop_worker(process, grace_seconds=30)
                    if not self.stopping:
                        # Tools may have launched detached sessions outside the
                        # Worker's process group. Exit PID 1 so the container's
                        # process namespace is torn down before Compose starts
                        # another Worker with a new ID.
                        raise RuntimeError('AGENT_RUNNER_SLOT_CONTAINER_RESTART_REQUIRED')
        finally:
            close_postgres_pool()


def main():
    parser = argparse.ArgumentParser(description='Supervise one AgentRunner execution slot')
    parser.parse_args()
    if sys.platform == 'linux' and os.getpid() != 1:
        # A direct host invocation cannot rely on PID namespace teardown to
        # stop detached skill sessions after a Worker crash.
        raise RuntimeError('AGENT_RUNNER_SLOT_REQUIRES_CONTAINER_PID_ONE')
    supervisor = RunnerSlotSupervisor()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, supervisor.stop)
    supervisor.run()


if __name__ == '__main__':
    main()
