"""Real Engine failure + explicit original CP/interruption-port timing DI.

Observe the exception from the original Runtime async generator before Worker
interrupts its control. Save that actual FAILED state through the original ports,
then propagate the original exception. No outcome/receipt/authority is fabricated.
The resuming process uses the default CLI without this hook.
"""
import asyncio
import json
import os
from pathlib import Path
import runpy

from src.core.agent_engine.contracts import Outcome
from src.services.agent_runner.runtime.executor import RuntimeExecution

original_run = RuntimeExecution.run
observed_failure = False


async def persist_real_failure_before_worker_handling(self, *args, **kwargs):
    global observed_failure
    try:
        async for event in original_run(self, *args, **kwargs):
            yield event
    except Exception:
        state = self.state
        if (not observed_failure and getattr(self.control, 'durable_owner', False)
                and state is not None and state.outcome == Outcome.FAILED):
            assert state.model_calls[-1]['phase'] == 'failed'
            assert self.control.stopped is False
            observed_failure = True
            await self.control.save(state, 'fixture_real_failed_before_worker_handling')
            row = await asyncio.to_thread(self.control.repository.interrupt_attempt,
                                          self.control.attempt)
            Path(os.environ['KF_ADMISSION_FAILED_REPORT']).write_text(json.dumps({
                'outcome': state.outcome.value,
                'last_model_phase': state.model_calls[-1]['phase'],
                'status': row['status'], 'attempt': row['attempt']}))
        raise


RuntimeExecution.run = persist_real_failure_before_worker_handling
runpy.run_path(str(Path(__file__).with_name('kf_admission_process.py')), run_name='__main__')
