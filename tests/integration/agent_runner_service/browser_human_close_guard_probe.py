"""Hold actual native Close IO lock; observe immutable oldAttempt guard only."""
import asyncio
import json
from pathlib import Path
import sys
import time


def install_close_gate(report, entered, release):
    from src.services.agent_runner.worker import RuntimeFactory
    from src.tools.browser.executor.local import LocalPlaywrightExecutor
    from src.tools.browser.owner_port import current_resource_close_guard, HumanActionRejected

    original_close = LocalPlaywrightExecutor.close
    original_cancel = RuntimeFactory.request_resource_cancellation
    path = Path(report).with_suffix('.guard.json')
    observations = {'guarded_close_calls': 0, 'rejected': False}
    def publish():
        temporary = path.with_suffix('.pending')
        temporary.write_text(json.dumps(observations))
        temporary.replace(path)

    async def close(self, *args, **kwargs):
        if current_resource_close_guard() is None or observations['guarded_close_calls']:
            return await original_close(self, *args, **kwargs)
        observations['guarded_close_calls'] += 1
        before_seq, process = self._seq, self._process
        locked = asyncio.Event()
        async def holder():
            async with self._io_lock:
                locked.set()
                Path(entered).touch()
                until = time.monotonic() + 12
                while not Path(release).exists():
                    if time.monotonic() >= until:
                        raise TimeoutError('Fixture original Close gate not released')
                    await asyncio.sleep(.02)
        task = asyncio.create_task(holder())
        await locked.wait()
        try:
            return await original_close(self, *args, **kwargs)
        except HumanActionRejected as error:
            observations.update(rejected=True, code=error.code,
                seq_unchanged=self._seq == before_seq,
                same_handle=self._process is process,
                process_live=process is not None and process.returncode is None,
                resource_confirmed=bool(self.resource_close_confirmed))
            publish()
            raise
        finally:
            Path(release).touch()
            await task

    async def cancellation(self, attempt):
        owners = [m for m in self.browser_process.get('retained_managers', set())
            if m.execution_owner.runner_id == attempt.runner_id]
        tokens = [m._owner_tokens.get((m.execution_owner.record.tenant_id, m.execution_owner.record.run_id)) for m in owners]
        observations['captured_attempt'] = attempt.number
        try:
            return await original_cancel(self, attempt)
        finally:
            observations['managers_retained'] = all(m in self.browser_process.get('retained_managers', set()) for m in owners)
            observations['tokens_unchanged'] = all(m._owner_tokens.get((m.execution_owner.record.tenant_id,
                m.execution_owner.record.run_id)) == token for m, token in zip(owners, tokens))
            observations['executor_handles_present'] = all(m.human_executor(m.execution_owner.record.tenant_id,
                m.execution_owner.record.run_id) is not None for m in owners)
            publish()
    LocalPlaywrightExecutor.close = close
    RuntimeFactory.request_resource_cancellation = cancellation


if __name__ == '__main__':
    report, entered, release, *arguments = sys.argv[1:]
    install_close_gate(report, entered, release)
    sys.argv = ['worker', *arguments]
    from src.services.agent_runner.worker import main
    main()
