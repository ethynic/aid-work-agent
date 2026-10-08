"""Attempt fencing shared by execution, checkpoint and receipt transactions."""

from dataclasses import dataclass
from src.core.agent_engine.contracts import CheckpointFailure
from .repository import decoded


@dataclass(frozen=True)
class Attempt:
    runner_id: str
    worker_id: str
    number: int


class LeaseLost(CheckpointFailure):
    pass


class StopRequested(CheckpointFailure):
    def __init__(self, command):
        super().__init__("RUNNER_" + command.upper() + "_REQUESTED")
        self.command = command


def lock_runner(cursor, runner_id, attempt=None, *, dispatch=False):
    cursor.execute("SELECT * FROM agent_runners WHERE runner_id=%s FOR UPDATE", (runner_id,))
    row = decoded(cursor.fetchone())
    if row is None:
        raise LeaseLost("RUNNER_NOT_FOUND")
    # Evaluate AFTER the row lock is held: a transaction or SELECT projection may
    # have started before a long LockRows wait and must not freeze the lease clock.
    cursor.execute("SELECT clock_timestamp() AS database_now")
    now = cursor.fetchone()["database_now"]
    row["lease_valid"] = row["lease_until"] is not None and row["lease_until"] > now
    if attempt is not None:
        if (row["attempt"] != attempt.number or row["worker_id"] != attempt.worker_id
                or not row["lease_valid"] or row["status"] not in ("running", "finalizing")):
            raise LeaseLost("RUNNER_ATTEMPT_EXPIRED")
        if dispatch and row["cancel_requested"]:
            raise StopRequested("cancel")
        if dispatch and row.get('pause_requested'):
            raise StopRequested('pause')
        if dispatch and row["status"] != "running":
            raise LeaseLost("RUNNER_EXECUTION_CLOSED")
    return row
