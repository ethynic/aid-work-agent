"""Real PG recovery transactions: original claim, one consumption and fencing.

Application tree rebuilding and fresh execution authorization are separate
worker/API gates; these cases do not pretend storage acceptance proves them.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import copy
import queue
import threading
import uuid

from psycopg2 import errors, sql
import pytest

from src.services.agent_runner.contracts import RunnerError
from src.services.agent_runner.ownership import LeaseLost, StopRequested
from src.services.agent_runner.recovery_repository import RecoveryRepository

from .conftest import wait_for
from .test_control_storage import controls, command, ledger, park_waiting, reply
from .test_storage import attempt, storage

pytestmark = pytest.mark.integration


@pytest.fixture
def recovery(service_database):
    return RecoveryRepository(service_database.connect)


def accepted_reply(storage, controls, actor):
    row, principal, target = park_waiting(storage, actor)
    control, _ = controls.submit(principal, row["runner_id"], reply(target))
    checkpoint = copy.deepcopy(row["checkpoint"])
    checkpoint["applied_control_id"] = control["control_id"]
    return row, principal, control, checkpoint


def claim(recovery, row, control, checkpoint, *, worker="new-attempt"):
    return recovery.claim_resume(row["runner_id"], worker, 30, revision=row["revision"],
                                 control_id=control["control_id"], checkpoint=checkpoint)


def test_resume_consumes_control_with_new_attempt_same_claim_and_immutable_input(
        storage, controls, recovery, actors, service_database):
    repository, execution, submit = storage
    row, principal, control, checkpoint = accepted_reply(storage, controls, actors["a"])
    queued, _ = submit(actors["a"])
    candidate = recovery.next_candidate()
    assert candidate["runner_id"] == row["runner_id"]
    resumed = claim(recovery, row, control, checkpoint)
    assert resumed["runner_id"] == row["runner_id"] and resumed["status"] == "running"
    assert resumed["attempt"] == row["attempt"] + 1 and resumed["revision"] == row["revision"] + 1
    for key in ("queue_order", "input_digest", "input", "accepted_at", "session_id", "record_id"):
        assert resumed[key] == row[key]
    assert resumed["checkpoint"] == checkpoint and not resumed["pause_requested"]
    assert resumed["resume_control_id"] is None
    consumed = controls.get(principal, row["runner_id"], control["control_id"])
    assert consumed["status"] == "consumed" and consumed["consumed_attempt"] == resumed["attempt"]
    assert consumed["consumed_at"] is not None
    assert service_database.rows("SELECT owner_runner_id FROM agent_runner_session_claims WHERE session_id=%s",
                                 (principal.identity.session_id,))[0]["owner_runner_id"] == row["runner_id"]
    assert execution.acquire("not-next-turn", 30) is None
    assert repository.get(queued["runner_id"])["status"] == "queued"
    assert claim(recovery, row, control, checkpoint) is None
    assert recovery.next_candidate() is None


def test_competing_workers_consume_original_resume_only_once(storage, controls, recovery, actors, service_database):
    row, principal, control, checkpoint = accepted_reply(storage, controls, actors["a"])
    ready = threading.Barrier(2)
    def acquire(worker):
        ready.wait(timeout=5)
        return claim(recovery, row, control, checkpoint, worker=worker)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(acquire, ("resume-owner-a", "resume-owner-b")))
    won = [result for result in results if result is not None]
    assert len(won) == 1 and results.count(None) == 1
    assert won[0]["attempt"] == row["attempt"] + 1
    assert len(ledger(service_database, row["runner_id"])) == 1
    assert controls.get(principal, row["runner_id"], control["control_id"])["consumed_attempt"] == won[0]["attempt"]


def test_consumed_wait_cannot_accept_another_reply_even_if_old_wait_stays_in_checkpoint(
        storage, controls, recovery, actors):
    repository, execution, _ = storage
    row, principal, control, checkpoint = accepted_reply(storage, controls, actors["a"])
    resumed = claim(recovery, row, control, checkpoint)
    execution.park(attempt(resumed), resumed["revision"], "waiting", checkpoint, {})
    target = control["payload"]["target_execution_id"]
    with pytest.raises(RunnerError, match="CONTROL_WAIT_CONSUMED"):
        controls.submit(principal, row["runner_id"], reply(target))
    assert repository.get(row["runner_id"])["resume_control_id"] is None


@pytest.mark.parametrize("changed", ["revision", "control", "claim", "wait"])
def test_resume_revalidates_durable_cas_claim_and_wait_before_consumption(
        storage, controls, recovery, actors, service_database, changed):
    repository, _, _ = storage
    row, principal, control, checkpoint = accepted_reply(storage, controls, actors["a"])
    if changed == "revision":
        stale = dict(row, revision=row["revision"] - 1)
        assert claim(recovery, stale, control, checkpoint) is None
    elif changed == "control":
        stale = dict(control, control_id="not-the-accepted-control")
        stale_cp = dict(checkpoint, applied_control_id=stale["control_id"])
        assert claim(recovery, row, stale, stale_cp) is None
    elif changed == "claim":
        # 已丢失会话所有权时，回复不能恢复旧执行；渠道投递没有额外 claim gate。
        service_database.rows("DELETE FROM agent_runner_session_claims WHERE owner_runner_id=%s", (row["runner_id"],))
        with pytest.raises(RunnerError, match="RECOVERY_CLAIM_LOST"):
            claim(recovery, row, control, checkpoint)
    else:
        altered = copy.deepcopy(row["checkpoint"])
        target = control["payload"]["target_execution_id"]
        altered["execution"]["children"][target]["checkpoint"]["waiting"]["wait_id"] = "a-new-wait"
        import json
        service_database.rows("UPDATE agent_runners SET checkpoint=%s::jsonb WHERE runner_id=%s", (json.dumps(altered), row["runner_id"]))
        with pytest.raises(RunnerError, match="CONTROL_WAIT_STALE"):
            claim(recovery, row, control, checkpoint)
    unchanged = repository.get(row["runner_id"])
    assert unchanged["status"] == "waiting" and unchanged["attempt"] == row["attempt"]
    assert unchanged["revision"] == row["revision"]
    assert controls.get(principal, row["runner_id"], control["control_id"])["status"] == "accepted"


def test_control_consumption_rolls_back_when_runner_checkpoint_update_fails(
        storage, controls, recovery, actors, service_database):
    repository, _, _ = storage
    row, principal, control, checkpoint = accepted_reply(storage, controls, actors["a"])
    suffix = uuid.uuid4().hex
    function, trigger = "fixture_recovery_fault_" + suffix, "fixture_recovery_trigger_" + suffix
    with service_database.connect() as connection, connection.cursor() as cursor:
        cursor.execute(sql.SQL("""CREATE FUNCTION {}() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN IF NEW.runner_id={} THEN RAISE EXCEPTION 'fixture checkpoint commit fault'; END IF;
            RETURN NEW; END $$""").format(sql.Identifier(function), sql.Literal(row["runner_id"])))
        cursor.execute(sql.SQL("CREATE TRIGGER {} BEFORE UPDATE ON agent_runners FOR EACH ROW EXECUTE FUNCTION {}()")
                       .format(sql.Identifier(trigger), sql.Identifier(function)))
    try:
        with pytest.raises(errors.RaiseException):
            claim(recovery, row, control, checkpoint)
        after = repository.get(row["runner_id"])
        assert after["revision"] == row["revision"] and after["attempt"] == row["attempt"]
        assert after["checkpoint"] == row["checkpoint"] and after["status"] == "waiting"
        receipt = controls.get(principal, row["runner_id"], control["control_id"])
        assert receipt["status"] == "accepted" and receipt["consumed_at"] is None and receipt["consumed_attempt"] is None
    finally:
        with service_database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql.SQL("DROP TRIGGER {} ON agent_runners").format(sql.Identifier(trigger)))
            cursor.execute(sql.SQL("DROP FUNCTION {}()").format(sql.Identifier(function)))
    retried = claim(recovery, row, control, checkpoint)
    assert retried["attempt"] == row["attempt"] + 1


def test_queued_pause_acknowledgement_then_resume_keeps_original_fifo_position(
        storage, controls, recovery, actors, service_database):
    repository, execution, submit = storage
    row, principal = submit(actors["a"])
    pause, _ = controls.submit(principal, row["runner_id"], command("pause"))
    assert service_database.rows("SELECT 1 FROM agent_runner_session_claims WHERE owner_runner_id=%s", (row["runner_id"],)) == []
    owned = execution.acquire("fifo-head-owner", 30)
    checkpoint = {"unstarted": True, "execution_context": {"fixture": "accepted-config"}}
    parked = recovery.acknowledge_pause(attempt(owned), owned["revision"], checkpoint, {})
    assert parked["status"] == "paused" and parked["checkpoint"] == checkpoint
    assert parked["worker_id"] is None and parked["lease_until"] is None
    consumed = controls.get(principal, row["runner_id"], pause["control_id"])
    assert consumed["status"] == "consumed" and consumed["consumed_attempt"] == owned["attempt"]
    resume, _ = controls.submit(principal, row["runner_id"], command("resume"))
    restored = dict(checkpoint, applied_control_id=resume["control_id"])
    resumed = claim(recovery, parked, resume, restored)
    assert resumed["queue_order"] == row["queue_order"] and resumed["input_digest"] == row["input_digest"]
    assert resumed["attempt"] == owned["attempt"] + 1
    with pytest.raises(LeaseLost, match="RUNNER_ATTEMPT_EXPIRED"):
        execution.save_checkpoint(attempt(owned), resumed["revision"], {"stale": True})


def test_ack_pause_waiting_on_lock_past_lease_cannot_park_old_attempt(
        storage, controls, actors, service_database):
    repository, execution, submit = storage
    row, principal = submit(actors["a"])
    controls.submit(principal, row["runner_id"], command("pause"))
    owned = execution.acquire("short-lease-pause-owner", 2)
    backend = queue.Queue()
    @contextmanager
    def observed_connection():
        with service_database.connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT pg_backend_pid() AS pid")
            backend.put(cursor.fetchone()["pid"])
            yield connection
    delayed = RecoveryRepository(observed_connection)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with service_database.connect() as lock, lock.cursor() as cursor:
            cursor.execute("SELECT runner_id FROM agent_runners WHERE runner_id=%s FOR UPDATE", (row["runner_id"],))
            future = pool.submit(delayed.acknowledge_pause, attempt(owned), owned["revision"], {"unstarted": True}, {})
            try:
                pid = backend.get(timeout=3)
                def blocked_before_expiry():
                    rows = service_database.rows("""SELECT a.wait_event_type, a.xact_start<r.lease_until AS began_before,
                        clock_timestamp()<r.lease_until AS valid FROM pg_stat_activity a CROSS JOIN agent_runners r
                        WHERE a.pid=%s AND r.runner_id=%s""", (pid, row["runner_id"]))
                    return rows and rows[0]["wait_event_type"] == "Lock" and rows[0]["began_before"] and rows[0]["valid"]
                wait_for(blocked_before_expiry, timeout=1)
                wait_for(lambda: service_database.rows("SELECT clock_timestamp()>=lease_until AS expired FROM agent_runners WHERE runner_id=%s",
                                                        (row["runner_id"],))[0]["expired"], timeout=3)
            finally:
                lock.commit()
        with pytest.raises(LeaseLost, match="RUNNER_ATTEMPT_EXPIRED"):
            future.result(timeout=3)
    after = repository.get(row["runner_id"])
    assert after["revision"] == owned["revision"] and after["status"] == "running"
    assert ledger(service_database, row["runner_id"])[0]["status"] == "accepted"


def test_cancel_supersedes_accepted_resume_without_consumption(storage, controls, recovery, actors):
    repository, _, _ = storage
    row, principal, control, checkpoint = accepted_reply(storage, controls, actors["a"])
    repository.cancel(principal, row["runner_id"])
    assert recovery.next_candidate() is None and claim(recovery, row, control, checkpoint) is None
    cancelled_control = controls.get(principal, row["runner_id"], control["control_id"])
    assert cancelled_control["status"] == "rejected" and cancelled_control["error_code"] == "CONTROL_EXECUTION_CLOSED"
    assert cancelled_control["consumed_at"] is not None and cancelled_control["consumed_attempt"] is None
    assert repository.get(row["runner_id"])["attempt"] == row["attempt"]


def test_cancel_prevents_pause_acknowledgement(storage, controls, recovery, actors):
    repository, execution, submit = storage
    row, principal = submit(actors["a"])
    controls.submit(principal, row["runner_id"], command("pause"))
    owned = execution.acquire("pause-cancel-race", 30)
    repository.cancel(principal, row["runner_id"])
    with pytest.raises(StopRequested, match="RUNNER_CANCEL_REQUESTED"):
        recovery.acknowledge_pause(attempt(owned), owned["revision"], {"unstarted": True}, {})
    assert repository.get(row["runner_id"])["status"] == "running"


def test_rejected_recovery_preserves_checkpoint_claim_and_allows_a_new_control(
        storage, controls, recovery, actors, service_database):
    repository, _, _ = storage
    row, principal, control, _ = accepted_reply(storage, controls, actors["a"])
    assert recovery.reject_resume(row["runner_id"], revision=row["revision"],
                                   control_id=control["control_id"], error_code="PROFILE_FORBIDDEN")
    rejected = controls.get(principal, row["runner_id"], control["control_id"])
    assert rejected["status"] == "rejected" and rejected["error_code"] == "PROFILE_FORBIDDEN"
    current = repository.get(row["runner_id"])
    assert current["checkpoint"] == row["checkpoint"] and current["status"] == "waiting"
    assert current["revision"] == row["revision"] and current["resume_control_id"] is None
    assert service_database.rows("SELECT owner_runner_id FROM agent_runner_session_claims WHERE session_id=%s",
                                 (principal.identity.session_id,))[0]["owner_runner_id"] == row["runner_id"]
    assert not recovery.reject_resume(row["runner_id"], revision=row["revision"],
                                       control_id=control["control_id"], error_code="OTHER_FAILURE")
    retried, created = controls.submit(principal, row["runner_id"], reply(control["payload"]["target_execution_id"]))
    assert created and retried["control_id"] != control["control_id"]


def test_late_recovery_rejection_cannot_change_consumed_control_or_finalization_intent(
        storage, controls, recovery, actors):
    repository, execution, _ = storage
    row, principal, control, checkpoint = accepted_reply(storage, controls, actors["a"])
    resumed = claim(recovery, row, control, checkpoint)
    result = {"status": "completed", "output": "already-produced-recovery-result"}
    staged = execution.stage_finalization(attempt(resumed), resumed["revision"], checkpoint, result, {})
    assert not recovery.reject_resume(row["runner_id"], revision=staged["revision"],
                                       control_id=control["control_id"], error_code="LATE_REJECTION")
    current = repository.get(row["runner_id"])
    assert current["status"] == "finalizing" and current["checkpoint"] == staged["checkpoint"]
    assert current["checkpoint"]["pending_finalization"] == result and current["revision"] == staged["revision"]
    consumed = controls.get(principal, row["runner_id"], control["control_id"])
    assert consumed["status"] == "consumed" and consumed["error_code"] is None
