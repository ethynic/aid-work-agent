"""Narrow timing DI: persist genuine Runtime failed facts then expire lease before stage.

The real Engine creates FAILED state. This probe explicitly adds a real owner
checkpoint between that state and staging; it does not claim Engine normally
has a failed-save boundary.
"""
import asyncio
import json
import os
from pathlib import Path


def main():
    from src.db.database import get_db_connection
    from src.services.agent_runner.durable_control import DurableControl
    from src.services.agent_runner import worker
    original = DurableControl.stage
    report = Path(os.environ["RUNNER_TEST_FAILED_STAGE_REPORT"])
    assert report.is_absolute() and report.parent.is_dir()

    async def stage(control, state, result):
        if state is not None and state.outcome.value == "failed":
            checkpoint, snapshot = control._capture(state)
            row = await asyncio.to_thread(control.repository.save_checkpoint,
                control.attempt, control.revision, checkpoint, snapshot)
            control.envelope, control.revision = row["checkpoint"], row["revision"]
            with get_db_connection() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT checkpoint FROM agent_runners WHERE runner_id=%s", (control.attempt.runner_id,))
                checkpoint = cursor.fetchone()["checkpoint"]
                assert checkpoint["execution"]["outcome"] == "failed"
                cursor.execute("UPDATE agent_runners SET lease_until=clock_timestamp()-INTERVAL '1 second' WHERE runner_id=%s", (control.attempt.runner_id,))
                connection.commit()
                cursor.execute("SELECT clock_timestamp()>lease_until AS expired FROM agent_runners WHERE runner_id=%s", (control.attempt.runner_id,))
                assert cursor.fetchone()["expired"]
            report.write_text(json.dumps({"saved_outcome": "failed", "before_staging": True}))
        return await original(control, state, result)

    DurableControl.stage = stage
    try:
        worker.main()
    finally:
        DurableControl.stage = original


if __name__ == "__main__":
    main()
