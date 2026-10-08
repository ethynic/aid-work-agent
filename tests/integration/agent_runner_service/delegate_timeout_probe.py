"""Normal RuntimeFactory DI with the existing delegate wait timeout shortened.

Real executor records, tasks and exceptions are retained; only the wait duration
changes. This first-execution harness is not used by the restoring default CLI.
"""
import asyncio
import json
import os
from pathlib import Path
import signal


def main():
    from src.config.settings import settings
    from src.db.database import init_postgres_pool, close_postgres_pool, init_logs_pool, close_logs_pool
    from src.services.agent_runner.repository import RunnerRepository
    from src.services.agent_runner.worker import RunnerWorker, RuntimeFactory

    class TimedFactory(RuntimeFactory):
        async def create(self, row, principal, control):
            runtime = await super().create(row, principal, control)
            executor = runtime.resources.subagent_executor
            original = executor.wait_for_result

            async def bounded_wait(execution_id, timeout=300, poll_interval=0.5):
                return await original(execution_id, timeout=min(timeout, 5), poll_interval=min(poll_interval, 0.1))

            executor.wait_for_result = bounded_wait
            report = os.environ.get("RUNNER_TEST_PARKED_TASK_REPORT")
            if report:
                async def observe_owned_task_completion():
                    observed_tasks = {}
                    while True:
                        # _run_instance removes its registry entry in finally,
                        # before the asyncio Task becomes done. Retain the real
                        # object references; don't manufacture completion flags.
                        observed_tasks.update(executor._active_executions)
                        if observed_tasks and all(task.done() for task in observed_tasks.values()):
                            Path(report).write_text(json.dumps({"all_owned_tasks_done": True, "execution_ids": list(observed_tasks)}))
                            return
                        await asyncio.sleep(0.05)
                runtime._fixture_task_completion_observer = asyncio.create_task(observe_owned_task_completion())
            return runtime

    async def run():
        await asyncio.to_thread(init_postgres_pool)
        try:
            await asyncio.to_thread(init_logs_pool)
            await asyncio.to_thread(RunnerRepository().assert_schema)
            worker = RunnerWorker(settings.agent_runner, worker_id="fixture-delegate-timeout-owner",
                                  runtime_factory=TimedFactory())
            loop = asyncio.get_running_loop()
            for signum in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(signum, worker.stop)
            await worker.run(once=True)
        finally:
            await asyncio.to_thread(close_logs_pool)
            await asyncio.to_thread(close_postgres_pool)

    asyncio.run(run())


if __name__ == "__main__":
    main()
