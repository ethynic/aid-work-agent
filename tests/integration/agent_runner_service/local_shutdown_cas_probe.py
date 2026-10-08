"""Real outstanding bind versus shutdown duration CAS timing; no fake results."""
import asyncio
import contextvars
import json
from pathlib import Path
import signal
import sys
import time

_duration=contextvars.ContextVar('fixture_duration_write',default=False)


def wait(path):
    end=time.monotonic()+20
    while not path.exists() and time.monotonic()<end: time.sleep(.02)
    if not path.exists(): raise TimeoutError('FIXTURE_TIMING_GATE_NOT_RELEASED')


async def main(folder):
    from src.config.settings import settings
    from src.db.database import init_postgres_pool,close_postgres_pool,init_logs_pool,close_logs_pool
    from src.services.agent_runner.execution_repository import ExecutionRepository
    from src.services.agent_runner.local_invocations import LocalInvocationRepository
    from src.services.agent_runner.worker import RunnerWorker
    folder=Path(folder)
    actual_bind=LocalInvocationRepository.bind
    def gated_bind(self,*args,**kwargs):
        (folder/'bind-entered').touch()
        wait(folder/'release-bind')
        result=actual_bind(self,*args,**kwargs)
        (folder/'bind-committed').touch()
        return result
    LocalInvocationRepository.bind=gated_bind
    class ObservedExecution(ExecutionRepository):
        def save_checkpoint(self,*args,**kwargs):
            if _duration.get():
                (folder/'duration-read').touch()
                wait(folder/'release-duration')
            return super().save_checkpoint(*args,**kwargs)
    class ObservedWorker(RunnerWorker):
        async def _preserve_duration(self,*args):
            token=_duration.set(True)
            try: return await super()._preserve_duration(*args)
            finally: _duration.reset(token)
    await asyncio.to_thread(init_postgres_pool)
    try:
        await asyncio.to_thread(init_logs_pool)
        worker=ObservedWorker(settings.agent_runner,worker_id='fixture-shutdown-cas-owner',execution_repository=ObservedExecution())
        loop=asyncio.get_running_loop()
        for sig in (signal.SIGTERM,signal.SIGINT): loop.add_signal_handler(sig,worker.stop)
        await worker.run(once=True)
    finally:
        LocalInvocationRepository.bind=actual_bind
        await asyncio.to_thread(close_logs_pool)
        await asyncio.to_thread(close_postgres_pool)


if __name__=='__main__': asyncio.run(main(sys.argv[1]))
