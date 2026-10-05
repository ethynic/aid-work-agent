"""Fresh real worker with one controlled child boundary scheduling window.

The original save method supplies every exception/result. The gate supplies
only timing; the integer/boolean report never contains inputs or credentials.
"""
from functools import wraps
import asyncio
import json
import os
from pathlib import Path
import time


def main():
    from src.services.agent_runner import worker
    from src.services.agent_runner.repository import RunnerRepository
    from src.services.agent_runner.runtime.child import ChildControl

    boundary = os.environ["RUNNER_TEST_CHILD_BOUNDARY"]
    if boundary not in {"before_model", "before_tool"}:
        raise ValueError("Unknown observation boundary")
    report = Path(os.environ["RUNNER_TEST_CHILD_REPORT"])
    release = Path(os.environ["RUNNER_TEST_CHILD_RELEASE"])
    if not report.is_absolute() or report.parent != release.parent or not report.parent.is_dir():
        raise ValueError("Owned report/release directory required")
    original_save = ChildControl.save
    original_init = worker.RunnerWorker.__init__
    gated = False

    def publish(value):
        temporary = report.with_suffix(".writing")
        temporary.write_text(json.dumps(value))
        temporary.replace(report)

    @wraps(original_init)
    def timed_init(self, config, *args, **kwargs):
        # Normal config DI isolates the intended race from the heartbeat. All
        # auth, providers, persistence, runtime and lifecycle remain original.
        return original_init(self, config.model_copy(update={"lease_seconds": 120, "heartbeat_seconds": 60}),
                             *args, **kwargs)

    @wraps(original_save)
    async def gated_save(self, state, actual_boundary):
        nonlocal gated
        if actual_boundary == boundary and not gated:
            gated = True
            facts = {"stage": "gate", "execution_id": state.execution_id,
                     "parent_call_id": self.call_id, "boundary": actual_boundary}
            publish(facts)
            deadline = time.monotonic() + 30
            while not release.is_file():
                if time.monotonic() >= deadline:
                    raise TimeoutError("Test-owned boundary release timed out")
                await asyncio.sleep(0.02)
            row = await asyncio.to_thread(RunnerRepository().get, self.parent_control.attempt.runner_id)
            publish({**facts, "stage": "released", "database_pause_requested": row["pause_requested"],
                     "parent_memory_pause_requested": self.parent_control.pause_requested})
        return await original_save(self, state, actual_boundary)

    worker.RunnerWorker.__init__ = timed_init
    ChildControl.save = gated_save
    try:
        worker.main()
    finally:
        worker.RunnerWorker.__init__ = original_init
        ChildControl.save = original_save


if __name__ == "__main__":
    main()
