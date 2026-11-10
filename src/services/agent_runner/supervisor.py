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
            process.wait()
    # An unexpectedly dead Worker may have left same-group tool grandchildren.
    # Do not start another execution process until its group has been cleared.
    if os.name == 'posix':
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


class RunnerSlotSupervisor:
    def __init__(self, repository=None, *, poll_seconds=2, cleanup_seconds=120,
                 worker_command=None, clock=time.monotonic):
        self.repository = repository or ExecutionRepository()
        self.poll_seconds = poll_seconds
        self.cleanup_seconds = cleanup_seconds
        self.worker_command = worker_command or [sys.executable, '-m', 'src.services.agent_runner.worker']
        self.clock = clock
        self.stopping = False

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

        state = slot.read()
        if state.get('state') != 'BUSY' or state.get('worker_id') != worker_id:
            return None
        attempt = Attempt(state['runner_id'], worker_id, state['attempt'])
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
        killed = False
        with slot.locked() as (_, current):
            if process.poll() is None and slot.matches(current, attempt):
                logger.warning('AgentRunner slot cleanup exceeded limit runner={} attempt={}',
                               attempt.runner_id, attempt.number)
                try:
                    if os.name == 'posix':
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                    killed = True
                except ProcessLookupError:
                    pass
        if killed:
            process.wait()
        return None

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
