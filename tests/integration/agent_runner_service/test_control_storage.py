"""M4 private controls against real PostgreSQL, without an execution-loop mock.

This file tests command acceptance, not worker consumption or fresh HTTP auth.
Those require the independently frozen worker/API slice and separate cases.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import threading
import uuid

import pytest

from src.core.agent_engine.contracts import AgentMode, ExecutionState, Outcome
from src.services.agent_runner.contracts import RunnerError
from src.services.agent_runner.control_contracts import RunnerControl, public_control
from src.services.agent_runner.control_repository import ControlRepository

from .test_storage import attempt, storage  # Reuse the real PG submission fixture.

pytestmark = pytest.mark.integration


@pytest.fixture
def controls(service_database):
    return ControlRepository(service_database.connect)


def command(action, **values):
    return RunnerControl(client_request_id=uuid.uuid4().hex, action=action, **values)


def park_waiting(storage, actor, *, child=True, kind="clarification"):
    repository, execution, submit = storage
    row, principal = submit(actor)
    owned = execution.acquire("control-fixture-owner", 30)
    assert owned["runner_id"] == row["runner_id"]
    root = ExecutionState(principal.identity, "root-execution", AgentMode.MASTER, "", [])
    state = root
    if child:
        state = ExecutionState(principal.identity, "child-execution", AgentMode.SUBAGENT, "", [])
    state.outcome = Outcome.WAITING
    state.waiting = {"kind": kind, "wait_id": "original-wait", "tool_call_id": "original-call"}
    if child:
        root.children[state.execution_id] = {"checkpoint": state.checkpoint()}
    checkpoint = {"execution": root.checkpoint()}
    parked = execution.park(attempt(owned), owned["revision"], "waiting", checkpoint, {})
    return parked, principal, state.execution_id


def reply(target, **changes):
    return command("reply", target_execution_id=target, wait_id="original-wait",
                   answer="Fictional clarification answer", **changes)


def ledger(service_database, runner_id):
    return service_database.rows("SELECT * FROM agent_runner_controls WHERE runner_id=%s", (runner_id,))


def test_same_key_same_reply_returns_original_control_without_another_mutation(
        storage, controls, actors, service_database):
    repository, _, _ = storage
    row, principal, target = park_waiting(storage, actors["a"])
    request = reply(target)
    accepted, created = controls.submit(principal, row["runner_id"], request,
                                        expected_revision=row["revision"])
    after = repository.get(row["runner_id"])
    duplicate, created_again = controls.submit(principal, row["runner_id"], request,
                                               expected_revision=row["revision"] - 1)
    assert created and not created_again
    assert duplicate["control_id"] == accepted["control_id"]
    assert repository.get(row["runner_id"])["view_revision"] == after["view_revision"]
    assert after["revision"] == row["revision"] and after["checkpoint"] == row["checkpoint"]
    assert after["resume_control_id"] == accepted["control_id"]
    assert len(ledger(service_database, row["runner_id"])) == 1
    projection = public_control(accepted)
    assert "answer" not in projection and "payload" not in projection and "intent_digest" not in projection
    assert request.answer in accepted["payload"]["answer"]


@pytest.mark.parametrize("mutation", ["action", "answer"])
def test_control_key_is_shared_across_actions_and_changed_reply_conflicts(
        storage, controls, actors, service_database, mutation):
    row, principal, target = park_waiting(storage, actors["a"])
    request = reply(target)
    accepted, _ = controls.submit(principal, row["runner_id"], request)
    conflicting = (RunnerControl(client_request_id=request.client_request_id, action="pause")
                   if mutation == "action" else request.model_copy(update={"answer": "Different answer"}))
    with pytest.raises(RunnerError, match="IDEMPOTENCY_INPUT_MISMATCH") as caught:
        controls.submit(principal, row["runner_id"], conflicting)
    assert caught.value.status == 409
    assert [item["control_id"] for item in ledger(service_database, row["runner_id"])] == [accepted["control_id"]]


@pytest.mark.parametrize("foreign", ["tenant", "user", "source"])
def test_owner_check_precedes_idempotent_reply_and_control_lookup(
        storage, controls, actors, service_database, foreign):
    row, principal, target = park_waiting(storage, actors["a"])
    request = reply(target)
    accepted, _ = controls.submit(principal, row["runner_id"], request)
    identity = principal.identity
    if foreign == "tenant":
        stranger = replace(principal, identity=replace(identity, tenant_id=actors["b"].tenant_id))
    elif foreign == "user":
        stranger = replace(principal, identity=replace(identity, user_id=actors["a_other"].user_id),
                           actor_id=actors["a_other"].user_id)
    else:
        stranger = replace(principal, identity=replace(identity, source="feishu"))
    for operation in (lambda: controls.submit(stranger, row["runner_id"], request),
                      lambda: controls.get(stranger, row["runner_id"], accepted["control_id"])):
        with pytest.raises(RunnerError, match="RUNNER_NOT_FOUND") as caught:
            operation()
        assert caught.value.status == 404
    assert len(ledger(service_database, row["runner_id"])) == 1


def test_null_tenant_does_not_allow_another_global_owner_to_retry_control(storage, controls, actors):
    row, principal, target = park_waiting(storage, actors["global"])
    request = reply(target)
    controls.submit(principal, row["runner_id"], request)
    other = replace(principal, actor_id=actors["global_other"].user_id,
                    identity=replace(principal.identity, user_id=actors["global_other"].user_id))
    with pytest.raises(RunnerError, match="RUNNER_NOT_FOUND"):
        controls.submit(other, row["runner_id"], request)


@pytest.mark.parametrize("target", ["parent", "missing_child", "stale_wait", "browser_kind"])
def test_reply_must_match_exact_child_wait_and_kind(storage, controls, actors, service_database, target):
    row, principal, child_id = park_waiting(storage, actors["a"],
                                          kind="human_assistance" if target == "browser_kind" else "clarification")
    request = reply(child_id)
    if target == "parent":
        request = request.model_copy(update={"target_execution_id": "root-execution"})
    elif target == "missing_child":
        request = request.model_copy(update={"target_execution_id": "never-existed-child"})
    elif target == "stale_wait":
        request = request.model_copy(update={"wait_id": "previous-wait"})
    expected = "CONTROL_WAIT_KIND_MISMATCH" if target == "browser_kind" else "CONTROL_WAIT_STALE"
    with pytest.raises(RunnerError, match=expected):
        controls.submit(principal, row["runner_id"], request)
    assert ledger(service_database, row["runner_id"]) == []


def test_stale_checkpoint_revision_does_not_leave_an_accepted_control(storage, controls, actors, service_database):
    repository, _, _ = storage
    row, principal, target = park_waiting(storage, actors["a"])
    with pytest.raises(RunnerError, match="CHECKPOINT_REVISION_CHANGED"):
        controls.submit(principal, row["runner_id"], reply(target), expected_revision=row["revision"] - 1)
    current = repository.get(row["runner_id"])
    assert current["revision"] == row["revision"] and current["control_revision"] == row["control_revision"]
    assert current["view_revision"] == row["view_revision"] and current["resume_control_id"] is None
    assert ledger(service_database, row["runner_id"]) == []


@pytest.mark.parametrize("same_key", [True, False])
def test_concurrent_replies_accept_one_wait_control(storage, controls, actors, service_database, same_key):
    row, principal, target = park_waiting(storage, actors["a"])
    first = reply(target)
    second = first if same_key else reply(target)
    start = threading.Barrier(2)
    def submit(request):
        start.wait(timeout=5)
        try:
            return controls.submit(principal, row["runner_id"], request)
        except RunnerError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, (first, second)))
    accepted = [result for result in results if isinstance(result, tuple)]
    if same_key:
        assert len(accepted) == 2 and sorted(result[1] for result in accepted) == [False, True]
        assert accepted[0][0]["control_id"] == accepted[1][0]["control_id"]
    else:
        assert len(accepted) == 1 and accepted[0][1]
        assert results.count("RUNNER_RESUME_PENDING") == 1
    rows = ledger(service_database, row["runner_id"])
    assert len(rows) == 1 and rows[0]["status"] == "accepted" and rows[0]["consumed_attempt"] is None


def test_pause_supersedes_unconsumed_resume_without_changing_checkpoint_or_claim(
        storage, controls, actors, service_database):
    repository, _, _ = storage
    row, principal, target = park_waiting(storage, actors["a"])
    resume, _ = controls.submit(principal, row["runner_id"], reply(target))
    paused, created = controls.submit(principal, row["runner_id"], command("pause"))
    assert created and paused["status"] == "consumed" and paused["consumed_at"] is not None
    original = controls.get(principal, row["runner_id"], resume["control_id"])
    assert original["status"] == "rejected" and original["error_code"] == "RESUME_SUPERSEDED_BY_PAUSE"
    current = repository.get(row["runner_id"])
    assert current["pause_requested"] and current["resume_control_id"] is None
    assert current["revision"] == row["revision"] and current["checkpoint"] == row["checkpoint"]
    assert current["queue_order"] == row["queue_order"] and current["attempt"] == row["attempt"]
    assert service_database.rows("SELECT owner_runner_id FROM agent_runner_session_claims WHERE session_id=%s",
                                 (principal.identity.session_id,))[0]["owner_runner_id"] == row["runner_id"]


def test_queued_pause_does_not_claim_session_or_interrupt_previous_running_turn(
        storage, controls, actors, service_database):
    repository, execution, submit = storage
    first, _ = submit(actors["a"])
    running = execution.acquire("previous-running-owner", 30)
    queued, principal = submit(actors["a"])
    accepted, _ = controls.submit(principal, queued["runner_id"], command("pause"))
    assert accepted["status"] == "accepted" and accepted["consumed_at"] is None
    current = repository.get(queued["runner_id"])
    assert current["status"] == "queued" and current["pause_requested"]
    assert current["attempt"] == 0 and current["worker_id"] is None
    assert current["queue_order"] == queued["queue_order"] and current["revision"] == queued["revision"]
    assert execution.acquire("must-not-preclaim-paused-turn", 30) is None
    still_authorized = execution.assert_dispatch(attempt(running))
    assert still_authorized["runner_id"] == first["runner_id"] and not still_authorized["cancel_requested"]
    assert not still_authorized["pause_requested"] and still_authorized["lease_until"] == running["lease_until"]
    assert service_database.rows("SELECT owner_runner_id FROM agent_runner_session_claims WHERE session_id=%s",
                                 (principal.identity.session_id,))[0]["owner_runner_id"] == first["runner_id"]
    assert service_database.rows("SELECT 1 FROM agent_runner_usage_receipts WHERE runner_id=%s", (queued["runner_id"],)) == []


def test_cancel_takes_priority_over_new_reply_or_pause(storage, controls, actors, service_database):
    repository, _, _ = storage
    row, principal, target = park_waiting(storage, actors["a"])
    repository.cancel(principal, row["runner_id"])
    for request in (reply(target), command("pause")):
        with pytest.raises(RunnerError, match="CONTROL_EXECUTION_CLOSED"):
            controls.submit(principal, row["runner_id"], request)
    assert ledger(service_database, row["runner_id"]) == []
    assert repository.get(row["runner_id"])["cancel_requested"]


def test_browser_completion_reference_cannot_self_attest_without_private_owner_repository(
        storage, controls, actors, service_database):
    row, principal, target = park_waiting(storage, actors["a"], kind="human_assistance")
    forged = command("browser_complete", target_execution_id=target, wait_id="original-wait",
                     completion_ref="untrusted-caller-says-completed")
    with pytest.raises(RunnerError, match="CONTROL_COMPLETION_FORBIDDEN") as caught:
        controls.submit(principal, row["runner_id"], forged)
    assert caught.value.status == 403 and ledger(service_database, row["runner_id"]) == []
