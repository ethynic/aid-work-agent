"""Durable ownership/fencing behavior using real PG connections and repositories."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import queue
import threading
import uuid

import pytest

from src.core.agent_engine.contracts import Identity
from src.services.agent_runner.contracts import Principal, RunnerSubmit
from src.services.agent_runner.execution_repository import ExecutionRepository
from src.services.agent_runner.ownership import Attempt, LeaseLost, StopRequested
from src.services.agent_runner.repository import RunnerRepository
from src.services.agent_runner.usage_repository import UsageRepository

from .conftest import wait_for

pytestmark = pytest.mark.integration


@pytest.fixture
def storage(service_database):
    repository = RunnerRepository(service_database.connect)
    execution = ExecutionRepository(service_database.connect)

    def submit(actor):
        principal = Principal(Identity(actor.tenant_id, actor.user_id, actor.session_id),
                              "user", actor.user_id, "storage-test")
        request = RunnerSubmit(client_request_id=uuid.uuid4().hex,
                               session={"kind": "web", "session_id": actor.session_id},
                               text="Storage fixture input")
        row, created = repository.submit(principal, request, "fixture-profile")
        assert created
        return row, principal

    return repository, execution, submit


def attempt(row):
    return Attempt(row["runner_id"], row["worker_id"], row["attempt"])


def test_locked_queue_head_is_not_skipped_by_a_second_worker(storage, actors, service_database):
    repository, execution, submit = storage
    first, _ = submit(actors["a"])
    second, _ = submit(actors["a"])
    unrelated, _ = submit(actors["b"])
    with service_database.connect() as lock, lock.cursor() as cursor:
        cursor.execute("SELECT runner_id FROM agent_runners WHERE runner_id=%s FOR UPDATE", (first["runner_id"],))
        picked = execution.acquire("worker-two", 30)
        assert picked["runner_id"] == unrelated["runner_id"]
        assert execution.acquire("worker-three", 30) is None
        assert repository.get(second["runner_id"])["status"] == "queued"
    picked = execution.acquire("worker-one", 30)
    assert picked["runner_id"] == first["runner_id"]
    assert execution.acquire("worker-four", 30) is None
    claims = service_database.rows("SELECT owner_runner_id FROM agent_runner_session_claims")
    assert {row["owner_runner_id"] for row in claims} == {first["runner_id"], unrelated["runner_id"]}


def test_two_workers_claim_different_sessions_but_not_two_turns_in_one_session(storage, actors):
    repository, execution, submit = storage
    first, _ = submit(actors["a"])
    second, _ = submit(actors["a"])
    unrelated, _ = submit(actors["b"])
    ready = threading.Barrier(2)
    def acquire(worker):
        ready.wait(timeout=3)
        return execution.acquire(worker, 30)
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(acquire, ("concurrent-a", "concurrent-b")))
    assert {row["runner_id"] for row in rows} == {first["runner_id"], unrelated["runner_id"]}
    assert repository.get(second["runner_id"])["status"] == "queued"
    assert execution.acquire("third-worker", 30) is None


@pytest.mark.parametrize("status", ["waiting", "paused", "interrupted"])
def test_parked_claim_blocks_next_turn_and_cancel_is_durably_finalization_only(storage, actors, service_database, status):
    repository, execution, submit = storage
    row, principal = submit(actors["a"])
    second, _ = submit(actors["a"])
    owned = execution.acquire("first-owner", 30)
    checkpoint = {"children": {"child-a": {"phase": "waiting"}}, "marker": "saved-before-park"}
    parked = execution.park(attempt(owned), owned["revision"], status, checkpoint, {"progress": "waiting"})
    assert parked["status"] == status and parked["worker_id"] is None and parked["lease_until"] is None
    assert execution.acquire("uninvited-resume", 30) is None
    assert repository.get(second["runner_id"])["status"] == "queued"
    repository.cancel(principal, row["runner_id"])
    cancel = execution.acquire("cancel-sweeper", 30)
    assert cancel["runner_id"] == row["runner_id"] and cancel["cancel_only"]
    assert cancel["status"] == "finalizing" and cancel["checkpoint"]["marker"] == "saved-before-park"
    assert cancel["checkpoint"]["finalization_intent"] == "cancel_only"
    assert cancel["attempt"] == owned["attempt"] + 1
    with pytest.raises(StopRequested, match="RUNNER_CANCEL_REQUESTED"):
        execution.assert_dispatch(attempt(cancel))
    assert service_database.rows("SELECT owner_runner_id FROM agent_runner_session_claims WHERE session_id=%s",
                                 (actors["a"].session_id,))[0]["owner_runner_id"] == row["runner_id"]
    assert service_database.rows("SELECT 1 FROM chat_records WHERE session_id=%s", (actors["a"].session_id,)) == []


def test_expired_execution_is_interrupted_with_checkpoint_and_claim_preserved(storage, actors, service_database):
    repository, execution, submit = storage
    first, _ = submit(actors["a"])
    submit(actors["a"])
    owned = execution.acquire("lost-worker", 30)
    saved = execution.save_checkpoint(attempt(owned), owned["revision"], {"model_calls": [{"call_id": "actual-call"}]})
    service_database.rows("UPDATE agent_runners SET lease_until=clock_timestamp()-INTERVAL '1 second' WHERE runner_id=%s", (first["runner_id"],))
    assert execution.reap_expired() == [first["runner_id"]]
    interrupted = repository.get(first["runner_id"])
    assert interrupted["status"] == "interrupted"
    assert interrupted["checkpoint"] == saved["checkpoint"]
    assert interrupted["worker_id"] is None and interrupted["lease_until"] is None
    assert execution.acquire("replacement", 30) is None
    with pytest.raises(LeaseLost, match="RUNNER_ATTEMPT_EXPIRED"):
        execution.save_checkpoint(attempt(owned), interrupted["revision"], {"wrong": "old-worker"})


@pytest.mark.parametrize("operation", ["dispatch", "checkpoint", "heartbeat", "usage_start"])
def test_old_transaction_waiting_on_row_lock_cannot_use_pre_wait_lease_clock(
        storage, actors, service_database, operation):
    repository, execution, submit = storage
    row, _ = submit(actors["a"])
    owned = execution.acquire("timed-owner", 2)
    original = repository.get(row["runner_id"])
    backend = queue.Queue()
    @contextmanager
    def observed_connection():
        with service_database.connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT pg_backend_pid() AS pid")
            backend.put(cursor.fetchone()["pid"])
            yield connection
    delayed = ExecutionRepository(observed_connection)
    def action():
        if operation == "dispatch":
            return delayed.assert_dispatch(attempt(owned))
        if operation == "checkpoint":
            return delayed.save_checkpoint(attempt(owned), owned["revision"], {"stale": True})
        if operation == "heartbeat":
            return delayed.heartbeat(attempt(owned), 30)
        return UsageRepository(observed_connection).start(attempt(owned), call_id="stale-dispatch",
                                                         execution_id="root", model=None)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with service_database.connect() as lock, lock.cursor() as cursor:
            cursor.execute("SELECT runner_id FROM agent_runners WHERE runner_id=%s FOR UPDATE", (row["runner_id"],))
            future = pool.submit(action)
            try:
                pid = backend.get(timeout=3)
                def blocked_before_expiry():
                    result = service_database.rows("""SELECT a.wait_event_type, a.xact_start < r.lease_until AS began_before,
                        clock_timestamp() < r.lease_until AS lease_valid FROM pg_stat_activity a
                        CROSS JOIN agent_runners r WHERE a.pid=%s AND r.runner_id=%s""", (pid, row["runner_id"]))
                    return result and result[0]["wait_event_type"] == "Lock" and result[0]["began_before"] and result[0]["lease_valid"]
                wait_for(blocked_before_expiry, timeout=1)
                wait_for(lambda: service_database.rows("SELECT clock_timestamp()>=lease_until AS expired FROM agent_runners WHERE runner_id=%s",
                                                        (row["runner_id"],))[0]["expired"], timeout=3)
            finally:
                # Always unlock before waiting for the worker, including an
                # observation assertion failure. No blocked thread leaks out.
                lock.commit()
        with pytest.raises(LeaseLost, match="RUNNER_ATTEMPT_EXPIRED"):
            future.result(timeout=3)
    current = repository.get(row["runner_id"])
    assert current["revision"] == original["revision"] and current["checkpoint"] == original["checkpoint"]
    assert current["lease_until"] == original["lease_until"]
    assert service_database.rows("SELECT 1 FROM agent_runner_usage_receipts WHERE runner_id=%s", (row["runner_id"],)) == []


def test_concurrent_checkpoint_cas_accepts_one_fact_and_rejects_stale_sibling(storage, actors):
    repository, execution, submit = storage
    row, _ = submit(actors["a"])
    owned = execution.acquire("checkpoint-owner", 30)
    barrier = threading.Barrier(2)
    def save(child):
        barrier.wait(timeout=3)
        try:
            return execution.save_checkpoint(attempt(owned), owned["revision"], {"children": {child: {"phase": "completed"}}})
        except LeaseLost as error:
            return str(error)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, ("child-a", "child-b")))
    saved = [result for result in results if isinstance(result, dict)]
    assert len(saved) == 1 and results.count("CHECKPOINT_REVISION_CHANGED") == 1
    current = repository.get(row["runner_id"])
    assert current["revision"] == owned["revision"] + 1 and current["checkpoint"] == saved[0]["checkpoint"]


def test_cancel_control_revision_does_not_conflict_with_final_execution_checkpoint(storage, actors):
    repository, execution, submit = storage
    row, principal = submit(actors["a"])
    owned = execution.acquire("control-owner", 30)
    cancelled = repository.cancel(principal, row["runner_id"])
    assert cancelled["revision"] == owned["revision"]
    assert cancelled["control_revision"] == owned["control_revision"] + 1
    with pytest.raises(StopRequested):
        execution.save_checkpoint(attempt(owned), owned["revision"], {"dispatch": True}, dispatch=True)
    checkpoint = {"children": {"child": {"phase": "cancelled"}}}
    saved = execution.save_checkpoint(attempt(owned), owned["revision"], checkpoint)
    checkpoint["children"]["child"]["phase"] = "mutated-in-caller"
    current = repository.get(row["runner_id"])
    assert current["checkpoint"] == {"children": {"child": {"phase": "cancelled"}}}
    assert current["revision"] == owned["revision"] + 1
    assert current["cancel_requested"] and current["control_revision"] == cancelled["control_revision"]
    assert current["view_revision"] == saved["view_revision"]


def test_staged_completion_cannot_be_overwritten_by_cancel_checkpoint_park_or_different_result(storage, actors, service_database):
    repository, execution, submit = storage
    row, principal = submit(actors["a"])
    owned = execution.acquire("finalizer-first", 30)
    result = {"status": "completed", "output": "already-produced-result"}
    staged = execution.stage_finalization(attempt(owned), owned["revision"], {"marker": "complete"}, result, {"output": "finished"})
    repository.cancel(principal, row["runner_id"])
    for action in (
        lambda: execution.save_checkpoint(attempt(owned), staged["revision"], {"wrong": True}),
        lambda: execution.park(attempt(owned), staged["revision"], "waiting", {}, {}),
        lambda: execution.stage_finalization(attempt(owned), staged["revision"], {}, {"status": "cancelled"}, {}),
    ):
        with pytest.raises(LeaseLost):
            action()
    same = execution.stage_finalization(attempt(owned), owned["revision"], {"wrong": "idempotent-must-not-write"}, result, {"wrong": True})
    assert same["checkpoint"] == staged["checkpoint"] and same["public_snapshot"] == staged["public_snapshot"]
    service_database.rows("UPDATE agent_runners SET lease_until=clock_timestamp()-INTERVAL '1 second' WHERE runner_id=%s", (row["runner_id"],))
    retry = execution.acquire("finalizer-retry", 30)
    assert retry["runner_id"] == row["runner_id"] and not retry["cancel_only"]
    assert retry["status"] == "finalizing" and retry["checkpoint"]["pending_finalization"] == result
    with pytest.raises(StopRequested):
        execution.assert_dispatch(attempt(retry))
    with pytest.raises(LeaseLost):
        execution.assert_dispatch(attempt(owned))


def test_cancel_only_finalizing_may_stage_only_its_first_cancelled_result(storage, actors):
    repository, execution, submit = storage
    row, principal = submit(actors["a"])
    owned = execution.acquire("park-owner", 30)
    execution.park(attempt(owned), owned["revision"], "waiting", {"marker": "original"}, {})
    repository.cancel(principal, row["runner_id"])
    cancel = execution.acquire("cancel-only-owner", 30)
    with pytest.raises(LeaseLost):
        execution.stage_finalization(attempt(cancel), cancel["revision"], {}, {"status": "completed"}, {})
    staged = execution.stage_finalization(attempt(cancel), cancel["revision"], cancel["checkpoint"], {"status": "cancelled"}, {})
    assert staged["checkpoint"]["pending_finalization"]["status"] == "cancelled"
    assert staged["checkpoint"]["finalization_intent"] == "cancel_only"


def test_expired_finalizing_attempt_retries_original_intent_without_dispatch_authority(storage, actors, service_database):
    repository, execution, submit = storage
    row, _ = submit(actors["a"])
    submit(actors["a"])
    owned = execution.acquire("complete-owner", 30)
    result = {"status": "completed", "output": "original-result"}
    staged = execution.stage_finalization(attempt(owned), owned["revision"], {"model_call": "finished"}, result, {})
    with pytest.raises(LeaseLost, match="RUNNER_EXECUTION_CLOSED"):
        execution.assert_dispatch(attempt(owned))
    assert execution.acquire("too-early-retry", 30) is None
    service_database.rows("UPDATE agent_runners SET lease_until=clock_timestamp()-INTERVAL '1 second' WHERE runner_id=%s", (row["runner_id"],))
    assert execution.reap_expired() == []
    retry = execution.acquire("new-finalizer-owner", 30)
    assert retry["runner_id"] == row["runner_id"] and retry["attempt"] == owned["attempt"] + 1
    assert retry["checkpoint"] == staged["checkpoint"] and retry["status"] == "finalizing"
    with pytest.raises(LeaseLost, match="RUNNER_EXECUTION_CLOSED"):
        execution.assert_dispatch(attempt(retry))
    with pytest.raises(LeaseLost, match="RUNNER_ATTEMPT_EXPIRED"):
        execution.heartbeat(attempt(owned), 30)
    stopped = execution.interrupt_attempt(attempt(retry))
    assert stopped["status"] == "finalizing" and stopped["checkpoint"]["pending_finalization"] == result
    assert repository.get(row["runner_id"])["revision"] == retry["revision"]


def test_cancel_only_intent_survives_sweeper_crash_before_result_is_staged(storage, actors, service_database):
    repository, execution, submit = storage
    row, principal = submit(actors["a"])
    owned = execution.acquire("paused-owner", 30)
    execution.park(attempt(owned), owned["revision"], "paused", {"children": {"child-a": {"phase": "paused"}}}, {})
    repository.cancel(principal, row["runner_id"])
    sweeper = execution.acquire("first-sweeper", 30)
    assert sweeper["cancel_only"] and "pending_finalization" not in sweeper["checkpoint"]
    service_database.rows("UPDATE agent_runners SET lease_until=clock_timestamp()-INTERVAL '1 second' WHERE runner_id=%s", (row["runner_id"],))
    retry = execution.acquire("sweeper-after-crash", 30)
    assert retry["status"] == "finalizing" and retry["checkpoint"]["finalization_intent"] == "cancel_only"
    assert retry["checkpoint"]["children"] == {"child-a": {"phase": "paused"}}
    with pytest.raises(StopRequested):
        execution.assert_dispatch(attempt(retry))
    with pytest.raises(LeaseLost, match="FINALIZATION_INTENT_IMMUTABLE"):
        execution.stage_finalization(attempt(retry), retry["revision"], {}, {"status": "completed"}, {})
    staged = execution.stage_finalization(attempt(retry), retry["revision"], retry["checkpoint"], {"status": "cancelled"}, {})
    assert staged["checkpoint"]["pending_finalization"]["status"] == "cancelled"
