"""Real PG lifecycle race, without creating or running a Runtime/Engine.

Only an after-commit observation barrier delays the genuine transaction owner.
This narrow owner test does not claim default CLI execution coverage.
"""
import asyncio
from contextlib import contextmanager
import threading
import os
from pathlib import Path
import uuid
from types import SimpleNamespace

import pytest
from psycopg2 import errors, sql

from src.services.agent_runner.durable_control import DurableControl
from src.services.agent_runner.finalizer import RunnerFinalizer
from src.services.agent_runner.recovery_repository import RecoveryRepository
from src.services.agent_runner.usage_repository import UsageRepository
from src.services.agent_runner.worker import RunnerWorker

from .test_storage import attempt, storage

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def isolated_observation_pool(service_database, monkeypatch):
    from src.db import database
    assert database._logs_connection_pool is None
    monkeypatch.setattr(database, "LOGS_DATABASE_URL", os.environ["DATABASE_URL"])
    ddl = Path(__file__).resolve().parents[3] / "deploy" / "init-postgres-logs.sql"
    with service_database.connect() as connection, connection.cursor() as cursor:
        cursor.execute(ddl.read_text())
    assert database.init_logs_pool()
    try:
        with database.get_logs_connection() as cursor:
            cursor.execute("SELECT current_database() AS name")
            assert cursor.fetchone()["name"] == service_database.name
        yield
    finally:
        database.close_logs_pool()


def worker(database, execution, repository, finalizer):
    return RunnerWorker(SimpleNamespace(heartbeat_seconds=.03, lease_seconds=30),
                        execution_repository=execution, repository=repository,
                        usage_repository=UsageRepository(database.connect),
                        finalizer=finalizer, authorizer=SimpleNamespace(),
                        runtime_factory=SimpleNamespace())


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["terminal", "paused"])
async def test_committed_release_does_not_cancel_owner_before_commit_method_returns(
        storage, actors, service_database, boundary):
    repository, execution, submit = storage
    row, _ = submit(actors["a"])
    owned = execution.acquire("release-window-owner", 30)
    claimed = attempt(owned)
    control = DurableControl(execution, claimed, owned)
    finalizer = RunnerFinalizer(service_database.connect)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    callbacks = []
    if boundary == "terminal":
        result = {"status": "completed", "output": "Lifecycle fixture response", "images": [], "messages": []}
        execution.stage_finalization(claimed, owned["revision"], {}, result, {})
        original = finalizer._after_commit

        def barrier(saved, delta):
            entered.set()
            assert release.wait(5), "Owned observation barrier was not released"
            original(saved, delta)
            callbacks.append(saved["runner_id"])
            finished.set()

        finalizer._after_commit = barrier
        operation = lambda: finalizer.finalize(claimed)
    else:
        service_database.rows("UPDATE agent_runners SET pause_requested=TRUE WHERE runner_id=%s", (row["runner_id"],))
        recovery = RecoveryRepository(service_database.connect)

        def operation():
            saved = recovery.acknowledge_pause(claimed, owned["revision"], {"unstarted": True}, {})
            entered.set()
            assert release.wait(5), "Owned observation barrier was not released"
            callbacks.append(saved["runner_id"])
            finished.set()
            return saved

    owner = asyncio.create_task(asyncio.to_thread(operation))
    heartbeat = asyncio.create_task(worker(service_database, execution, repository, finalizer)._heartbeat(claimed, control, owner))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        committed = repository.get(row["runner_id"])
        assert committed["status"] == ("completed" if boundary == "terminal" else "paused")
        assert committed["attempt"] == claimed.number
        assert committed["worker_id"] is None and committed["lease_until"] is None
        await asyncio.sleep(.15)  # five real heartbeat opportunities while commit-return remains blocked
        assert not control.stopped, "A legitimately committed owner release was misclassified as lost authority"
        assert not owner.done(), "Heartbeat cancelled the owner after its genuine commit"
        release.set()
        returned = await asyncio.wait_for(owner, 3)
        assert returned["runner_id"] == row["runner_id"] and callbacks == [row["runner_id"]]
        records = service_database.rows("SELECT record_id FROM chat_records WHERE session_id=%s", (actors["a"].session_id,))
        assert len(records) == (1 if boundary == "terminal" else 0)
        messages = service_database.rows("SELECT message_id FROM chat_messages WHERE session_id=%s", (actors["a"].session_id,))
        assert len(messages) == (2 if boundary == "terminal" else 0)
    finally:
        release.set()
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
        await asyncio.gather(owner, return_exceptions=True)
        # A cancelled asyncio waiter does not terminate its real thread.
        assert await asyncio.to_thread(finished.wait, 3), "Owned post-commit thread did not finish"


@pytest.mark.asyncio
@pytest.mark.parametrize("loss", ["lease", "epoch", "database", "forged_terminal", "forged_paused", "missing"])
async def test_real_authority_loss_still_interrupts_and_cancels_owner(storage, actors, service_database, loss):
    repository, execution, submit = storage
    row, _ = submit(actors["a"])
    owned = execution.acquire("actual-loss-owner", 30)
    claimed = attempt(owned)
    control = DurableControl(execution, claimed, owned)
    if loss == "lease":
        service_database.rows("UPDATE agent_runners SET lease_until=clock_timestamp()-INTERVAL '1 second' WHERE runner_id=%s", (row["runner_id"],))
    elif loss == "epoch":
        service_database.rows("UPDATE agent_runners SET attempt=attempt+1 WHERE runner_id=%s", (row["runner_id"],))
    elif loss.startswith("forged_"):
        status = "completed" if loss == "forged_terminal" else "paused"
        service_database.rows("UPDATE agent_runners SET status=%s,worker_id=NULL,lease_until=NULL WHERE runner_id=%s", (status, row["runner_id"]))
    elif loss == "missing":
        service_database.rows("DELETE FROM agent_runner_session_claims WHERE owner_runner_id=%s", (row["runner_id"],))
        service_database.rows("DELETE FROM agent_runner_controls WHERE runner_id=%s", (row["runner_id"],))
        service_database.rows("DELETE FROM agent_runners WHERE runner_id=%s", (row["runner_id"],))
    else:
        @contextmanager
        def database_fault():
            with service_database.connect() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT 1/0")  # actual PG failure, no synthetic exception
                yield connection
        from src.services.agent_runner.execution_repository import ExecutionRepository
        execution = ExecutionRepository(database_fault)
    owner = asyncio.create_task(asyncio.Event().wait())
    heartbeat = asyncio.create_task(worker(service_database, execution, repository, RunnerFinalizer(service_database.connect))._heartbeat(claimed, control, owner))
    try:
        await asyncio.wait_for(heartbeat, 2)
        assert control.stopped
        with pytest.raises(asyncio.CancelledError):
            await owner
        if loss == "missing":
            from src.services.agent_runner.contracts import RunnerError
            with pytest.raises(RunnerError, match="RUNNER_NOT_FOUND"):
                repository.get(row["runner_id"])
            saved = None
        else:
            saved = repository.get(row["runner_id"])
        if loss.startswith("forged_"):
            assert saved["status"] == ("completed" if loss == "forged_terminal" else "paused")
        elif loss != "missing":
            assert saved["status"] == "running"
        assert service_database.rows("SELECT 1 FROM chat_records WHERE session_id=%s", (actors["a"].session_id,)) == []
    finally:
        heartbeat.cancel()
        owner.cancel()
        await asyncio.gather(heartbeat, owner, return_exceptions=True)


@pytest.mark.parametrize("boundary", ["terminal", "paused"])
def test_release_proof_commits_with_original_owner_facts_or_rolls_back_all(
        storage, actors, service_database, boundary):
    from src.services.agent_runner.control_contracts import RunnerControl
    from src.services.agent_runner.control_repository import ControlRepository
    repository, execution, submit = storage
    row, principal = submit(actors["a"])
    owned = execution.acquire("release-rollback-owner", 30)
    claimed = attempt(owned)
    finalizer = RunnerFinalizer(service_database.connect)
    controls = ControlRepository(service_database.connect)
    if boundary == "terminal":
        usage = UsageRepository(service_database.connect)
        receipt = usage.start(claimed, call_id="fixture-storage-fact", execution_id=row["runner_id"])
        usage.observe(claimed, receipt["receipt_id"], {"prompt_tokens": 11, "completion_tokens": 7})
        result = {"status": "completed", "output": "Storage transaction fixture", "images": [], "messages": []}
        before = execution.stage_finalization(claimed, owned["revision"], {}, result, {})
        operation = lambda: finalizer.finalize(claimed)
        desired = "completed"
    else:
        pause, _ = controls.submit(principal, row["runner_id"], RunnerControl(client_request_id=uuid.uuid4().hex, action="pause"))
        before = repository.get(row["runner_id"])
        recovery = RecoveryRepository(service_database.connect)
        operation = lambda: recovery.acknowledge_pause(claimed, owned["revision"], {"unstarted": True}, {})
        desired = "paused"
    constraint = "fixture_release_rollback_" + uuid.uuid4().hex
    with service_database.connect() as connection, connection.cursor() as cursor:
        cursor.execute(sql.SQL("ALTER TABLE agent_runners ADD CONSTRAINT {} CHECK (runner_id <> {} OR status <> {})").format(
            sql.Identifier(constraint), sql.Literal(row["runner_id"]), sql.Literal(desired)))
    try:
        with pytest.raises(errors.CheckViolation):
            operation()
        after = repository.get(row["runner_id"])
        for name in ("status", "attempt", "worker_id", "lease_until", "checkpoint", "revision"):
            assert after[name] == before[name]
        assert not after["checkpoint"] or not after["checkpoint"].get("released_attempt")
        assert service_database.rows("SELECT 1 FROM agent_runner_session_claims WHERE owner_runner_id=%s", (row["runner_id"],)) == [{"?column?": 1}]
        assert service_database.rows("SELECT 1 FROM chat_records WHERE session_id=%s", (actors["a"].session_id,)) == []
        assert service_database.rows("SELECT 1 FROM chat_messages WHERE session_id=%s", (actors["a"].session_id,)) == []
        if boundary == "terminal":
            assert service_database.rows("SELECT applied FROM agent_runner_usage_receipts WHERE receipt_id=%s", (receipt["receipt_id"],)) == [{"applied": False}]
        else:
            control_row = controls.get(principal, row["runner_id"], pause["control_id"])
            assert control_row["status"] == "accepted" and control_row["consumed_attempt"] is None
    finally:
        with service_database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql.SQL("ALTER TABLE agent_runners DROP CONSTRAINT {}").format(sql.Identifier(constraint)))
    returned = operation()
    assert returned["status"] == desired
    assert returned["checkpoint"]["released_attempt"]
    assert execution.heartbeat(claimed, 30)["closed"] is True
    if boundary == "terminal":
        assert len(service_database.rows("SELECT 1 FROM chat_records WHERE session_id=%s", (actors["a"].session_id,))) == 1
        assert len(service_database.rows("SELECT 1 FROM chat_messages WHERE session_id=%s", (actors["a"].session_id,))) == 2
        assert service_database.rows("SELECT applied FROM agent_runner_usage_receipts WHERE receipt_id=%s", (receipt["receipt_id"],)) == [{"applied": True}]
    else:
        assert controls.get(principal, row["runner_id"], pause["control_id"])["consumed_attempt"] == claimed.number
