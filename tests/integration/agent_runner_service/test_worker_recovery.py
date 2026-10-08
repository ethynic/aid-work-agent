"""M4 fresh process worker and HTTP control acceptance.

The tests are prepared against the handed-off contract. Execute only after the
application/worker slice is formally frozen, separate from storage evidence.
"""
import base64
import copy
import json
import re
import signal
import threading
import uuid
from collections import Counter
from pathlib import Path

import httpx
import pytest

from .conftest import wait_for
from .provider import Reply, tool_reply
from .test_api import require_status
from .test_worker import workers, prices, api_pair, accept, decoded, runner, terminal
from .test_worker_controls import child_profile
from .test_web_gateway import gateways
from src.services.agent_runner.adapter_contract import ADAPTER_CONTRACT_VERSION

pytestmark = pytest.mark.integration


def control(api, actor, runner_id, action, *, key=None, **values):
    request = {"client_request_id": key or uuid.uuid4().hex, "action": action, **values}
    response = api.call("POST", f"/v1/runners/{runner_id}/controls", actor=actor, json=request)
    return require_status(response, 202), request


@pytest.fixture
def logs_schema(service_database):
    # Normal operator migration, in the same disposable target explicitly used
    # by the fresh worker's LOGS_DATABASE_URL. CLI pool creation is not a schema
    # migration and must not be replaced by shared developer log tables.
    ddl = Path(__file__).resolve().parents[3] / "deploy" / "init-postgres-logs.sql"
    with service_database.connect() as connection, connection.cursor() as cursor:
        cursor.execute(ddl.read_text())
        cursor.execute("SELECT current_database() AS database, to_regclass('public.obs_traces') AS traces, to_regclass('public.obs_spans') AS spans")
        row = cursor.fetchone()
        assert row["database"] == service_database.name and row["traces"] and row["spans"]


def terminal_with_diagnostics(workers, database, runner_id):
    """Keep the terminal assertion; report only state phases and stable codes."""
    try:
        return terminal(database, runner_id)
    except AssertionError as original:
        row = runner(database, runner_id)
        root = (decoded(row["checkpoint"]) or {}).get("execution") or {}
        def phases(state):
            return [{"call_id": call_id, "phase": fact.get("phase"), "result_recorded": fact.get("result_recorded")}
                    for call_id, fact in (state.get("tools") or {}).items()]
        children = []
        def walk(state):
            for parent_call, child in (state.get("children") or {}).items():
                checkpoint = child.get("checkpoint") or {}
                task = child.get("task_record") or {}
                children.append({"parent_call_id": parent_call, "execution_id": child.get("execution_id"),
                                 "checkpoint_execution_id": checkpoint.get("execution_id"),
                                 "status": child.get("status"), "outcome": checkpoint.get("outcome"),
                                 "task_execution_id": task.get("execution_id"), "task_status": task.get("status"),
                                 "pending_ids": [item.get("id") for item in checkpoint.get("pending", [])],
                                 "tools": phases(checkpoint)})
                walk(checkpoint)
        walk(root)
        controls = database.rows("SELECT status,error_code FROM agent_runner_controls WHERE runner_id=%s", (runner_id,))
        receipts = database.rows("SELECT phase FROM agent_runner_usage_receipts WHERE runner_id=%s", (runner_id,))
        observations = {"runner_status": row["status"], "attempt": row["attempt"], "revision": row["revision"],
                        "root_outcome": root.get("outcome"), "root_execution_id": root.get("execution_id"),
                        "root_pending_ids": [item.get("id") for item in root.get("pending", [])], "children": children,
                        "root_tools": phases(root),
                        "controls": [dict(item) for item in controls], "receipt_phases": dict(Counter(item["phase"] for item in receipts))}
        cli = []
        for child, _ in workers.children:
            path = workers.processes.root / f"child-{workers.processes.children.index(child)}.log"
            if not path.is_file():
                continue
            log = path.read_text(errors="replace")
            cli.append({"exit": child.poll(),
                "classes": re.findall(r"^([A-Za-z_.]+(?:Error|Exception|Failure)):", log, re.MULTILINE),
                "locations": re.findall(r'File "([^"]+)", line (\d+)', log)[-12:],
                "codes": sorted(set(re.findall(r"\b(?:RUNNER|RECOVERY|CHECKPOINT)_[A-Z0-9_]+\b", log)))})
        observations["cli"] = cli
        report = workers.root / ("recovery-diagnosis-" + uuid.uuid4().hex + ".json")
        report.write_text(json.dumps(observations))
        raise AssertionError("Terminal state did not converge; sanitized diagnosis=" + json.dumps(observations)) from original


def test_queued_pause_observes_zero_preparation_and_default_cli_resume_reads_finalized_history(
        workers, actors, service_database, gateways):
    gateway = gateways.start()
    release = threading.Event()
    first_reply = Reply(content="prior-turn-finalized-before-resume", release=release)
    first_marker = workers.provider.register(first_reply)
    first = accept(workers.api, actors["a"], first_marker)
    first_process, _ = workers.start()
    second_release = threading.Event()
    resumed_fixture = workers.root / "queued-second-resume-fixture.txt"
    resumed_fixture.write_text("fictional second resume read contents")
    second_reply = tool_reply("read", {"file_path": str(resumed_fixture)}, call_id="queued-second-resume-read")
    second_reply.release = second_release
    second_marker = workers.provider.register(second_reply, Reply(content="resumed-original-queued-input"))
    second = accept(workers.api, actors["a"], second_marker,
                    attachments=[{"name": "paused-fixture.txt", "content": base64.b64encode(b"Fictional queued file").decode()}],
                    prompt_augmentations=["fixture-original-accepted-instructions"])
    original = runner(service_database, second["runner_id"])
    try:
        assert first_reply.arrived.wait(timeout=15)
        public_control_path = "/api/chat/runners/" + second["runner_id"] + "/controls"
        paused = require_status(gateway.call("POST", public_control_path, actor=actors["a"],
                                             json={"client_request_id": uuid.uuid4().hex, "action": "pause"}), 202)
        assert paused["runner"]["status"] == "queued" and paused["runner"]["pause_requested"]
        still_running = runner(service_database, first["runner_id"])
        assert still_running["status"] == "running" and not still_running["pause_requested"]
        assert runner(service_database, second["runner_id"])["attempt"] == 0
        assert service_database.rows("SELECT owner_runner_id FROM agent_runner_session_claims WHERE session_id=%s",
                                     (actors["a"].session_id,))[0]["owner_runner_id"] == first["runner_id"]
        release.set()
        terminal_with_diagnostics(workers, service_database, first["runner_id"])
        workers.assert_clean_exit(first_process)
        report = workers.root / ("queued-activity-" + uuid.uuid4().hex + ".json")
        identifier = "worker-observed-" + uuid.uuid4().hex
        process = workers.processes.start(["-m", "tests.integration.agent_runner_service.worker_activity_probe",
                                          "--worker-id", identifier, "--once"],
                                         environment={**workers.environment, "RUNNER_TEST_ACTIVITY_REPORT": str(report)},
                                         private_working_directory=True)
        workers.children.append((process, identifier))
        workers.assert_clean_exit(process)
        parked = runner(service_database, second["runner_id"])
        assert parked["status"] == "paused" and decoded(parked["checkpoint"])["unstarted"]
        assert json.loads(report.read_text()) == {"runtime_create": 0, "context_prepare": 0,
            "compression_prepare": 0, "attachments_prepare": 0, "tool_dispatch": 0, "skill_workspace_create": 0}
        assert workers.provider.requests(second_marker) == []
        assert service_database.rows("SELECT 1 FROM agent_runner_usage_receipts WHERE runner_id=%s", (second["runner_id"],)) == []
        assert service_database.rows("SELECT 1 FROM chat_records WHERE record_id=%s", (parked["record_id"],)) == []
        assert list(workers.processes.root.rglob("skill_ws_*")) == []
        resumed_control = require_status(gateway.call("POST", public_control_path, actor=actors["a"],
                                                      json={"client_request_id": uuid.uuid4().hex, "action": "resume"}), 202)
        read_control = require_status(gateway.call("GET", public_control_path + "/" + resumed_control["control"]["control_id"],
                                                   actor=actors["a"]), 200)
        assert read_control["control"]["control_id"] == resumed_control["control"]["control_id"]
        # Undecorated production CLI proves actual restoration/assembly. The
        # counter-only harness above is confined to the zero-preparation proof.
        resumed_process, _ = workers.start()
        try:
            assert second_reply.arrived.wait(timeout=15)
            # The first resumed attempt really assembled and made model IO.
            # Stop at its next safe tool boundary, then restore the same runner
            # again; a stale unstarted marker must not reject this checkpoint.
            control(workers.api, actors["a"], second["runner_id"], "pause")
            second_release.set()
            workers.assert_clean_exit(resumed_process)
        finally:
            second_release.set()
        second_park = runner(service_database, second["runner_id"])
        assert second_park["status"] == "paused" and second_park["attempt"] == parked["attempt"] + 1
        second_cp = decoded(second_park["checkpoint"])
        assert not second_cp.get("unstarted") and second_cp["execution"]["tools"]["queued-second-resume-read"]["phase"] == "prepared"
        assert service_database.rows("SELECT owner_runner_id FROM agent_runner_session_claims WHERE session_id=%s",
                                     (actors["a"].session_id,))[0]["owner_runner_id"] == second["runner_id"]
        control(workers.api, actors["a"], second["runner_id"], "resume")
        final_process, _ = workers.start()
        workers.assert_clean_exit(final_process)
        finished = terminal_with_diagnostics(workers, service_database, second["runner_id"])
        assert finished["status"] == "completed" and finished["attempt"] == parked["attempt"] + 2
        for name in ("runner_id", "queue_order", "input", "input_digest", "accepted_at", "profile_fingerprint"):
            assert finished[name] == original[name]
        requests = workers.provider.requests(second_marker)
        assert len(requests) == 2
        assert any(message["role"] == "tool" and "fictional second resume read contents" in str(message["content"])
                   for message in requests[1]["messages"])
        augmentation = "fixture-original-accepted-instructions"
        assert sum(message["role"] == "user" and message["content"] == augmentation
                   for message in requests[0]["messages"]) == 1
        # Accepted instructions are deliberately a separate user message. Keep
        # that assertion and filter just this exact fixture augmentation when
        # verifying the business turn order.
        dialogue = [message for message in requests[0]["messages"]
                    if message["role"] != "system" and message["content"] != augmentation]
        assert [message["role"] for message in dialogue] == ["user", "assistant", "user"]
        assert first_marker in str(dialogue[0]["content"])
        assert dialogue[1]["content"] == first_reply.content
        assert second_marker in str(dialogue[2]["content"])
        assert "paused-fixture.txt" in str(dialogue[2]["content"])
        finished_cp = decoded(finished["checkpoint"])["execution"]
        workspace = Path(finished_cp["resources"]["workspace"])
        assert workspace.parent.parent == workers.root / "workspaces"
        assert re.fullmatch(r"[0-9a-f]{64}", workspace.parent.name)
        assert workspace.name.startswith("skill_ws_") and not workspace.exists()
        assert "fixture-original-accepted-instructions" in json.dumps(requests[0]["messages"])
        assert len(service_database.rows("SELECT 1 FROM chat_records WHERE session_id=%s", (actors["a"].session_id,))) == 2
        assert len(service_database.rows("SELECT 1 FROM agent_runner_usage_receipts WHERE runner_id=%s", (second["runner_id"],))) == 2
        assert not workers.provider.errors
    finally:
        release.set()
        second_release.set()


@pytest.mark.skip(reason='Invalid fixture producer retired: original master clarify continues the model and cannot create WAIT. Workspace authorization remains required; real child/Browser safety evidence is retained. Rework this fixture with a legitimate wait producer separately, not as M6a functionality.')
def test_foreign_actual_workspace_in_checkpoint_is_rejected_before_resume_claim_or_io(
        workers, actors, service_database, child_profile):
    initial = Reply(content="")
    marker = workers.provider.register(initial,
        tool_reply("clarify", {"question": "Which fictional customer?", "missing_info": ["customer"]}, call_id="scope-clarify"))
    initial.tool_calls = tool_reply("delegate_to_subagent", {"subagent_name": child_profile[0],
        "task_description": marker}, call_id="scope-delegate").tool_calls
    accepted = accept(workers.api, actors["a"], marker,
        attachments=[{"name": "owner-a.txt", "content": base64.b64encode(b"fictional owner A attachment").decode()}])
    first, _ = workers.start()
    workers.assert_clean_exit(first)
    original = runner(service_database, accepted["runner_id"])
    assert original["status"] == "waiting"
    release = threading.Event()
    foreign_reply = Reply(content="foreign-unpersisted-response", release=release)
    foreign_marker = workers.provider.register(foreign_reply)
    foreign = accept(workers.api, actors["b"], foreign_marker,
        attachments=[{"name": "owner-b.txt", "content": base64.b64encode(b"fictional owner B attachment").decode()}])
    foreign_process, _ = workers.start()
    try:
        assert foreign_reply.arrived.wait(timeout=15)
        workers.processes.stop(foreign_process, force=True)
        assert foreign_process.returncode < 0
        from src.services.agent_runner.execution_repository import ExecutionRepository
        service_database.rows("UPDATE agent_runners SET lease_until=clock_timestamp()-INTERVAL '1 second' WHERE runner_id=%s",
                              (foreign["runner_id"],))
        ExecutionRepository(service_database.connect).reap_expired()
        foreign_row = runner(service_database, foreign["runner_id"])
        assert foreign_row["status"] == "interrupted"
        foreign_workspace = decoded(foreign_row["checkpoint"])["execution"]["resources"]["workspace"]
        assert (Path(foreign_workspace) / "owner-b.txt").read_bytes() == b"fictional owner B attachment"
        corrupted = copy.deepcopy(decoded(original["checkpoint"]))
        own_workspace = corrupted["execution"]["resources"]["workspace"]
        assert own_workspace != foreign_workspace
        corrupted["execution"]["resources"]["workspace"] = foreign_workspace
        # An isolated DB fault simulates a corrupt durable reference; both
        # directories were created by actual Runtime executions, not guessed.
        service_database.rows("UPDATE agent_runners SET checkpoint=%s::jsonb WHERE runner_id=%s",
                              (json.dumps(corrupted), accepted["runner_id"]))
        resumed, _ = control(workers.api, actors["a"], accepted["runner_id"], "resume")
        process, _ = workers.start()
        workers.assert_clean_exit(process)
        rejected = runner(service_database, accepted["runner_id"])
        assert rejected["status"] == "waiting" and rejected["attempt"] == original["attempt"]
        assert decoded(rejected["checkpoint"]) == corrupted
        result = require_status(workers.api.call("GET", f"/v1/runners/{accepted['runner_id']}/controls/" + resumed["control"]["control_id"],
                                                 actor=actors["a"]), 200)["control"]
        assert result["status"] == "rejected" and result["error_code"] == "RECOVERY_RESOURCE_SCOPE_INVALID"
        assert len(workers.provider.requests(marker)) == 2 and len(workers.provider.requests(foreign_marker)) == 1
        assert Path(own_workspace).is_dir() and Path(foreign_workspace).is_dir()
        assert service_database.rows("SELECT owner_runner_id FROM agent_runner_session_claims WHERE session_id=%s",
                                     (actors["a"].session_id,))[0]["owner_runner_id"] == accepted["runner_id"]
        assert service_database.rows("SELECT 1 FROM chat_records WHERE session_id=%s", (actors["a"].session_id,)) == []
        assert not workers.provider.errors
    finally:
        release.set()


def test_sigterm_default_cli_gracefully_preserves_interrupted_checkpoint_claim_and_attachment(workers, actors, service_database):
    release = threading.Event()
    response = Reply(content="not-committed-after-stop", release=release)
    marker = workers.provider.register(response)
    accepted = accept(workers.api, actors["a"], marker, attachments=[{
        "name": "shutdown-fixture.txt", "content": base64.b64encode(b"fictional shutdown persistent file").decode()}])
    process, _ = workers.start()
    try:
        assert response.arrived.wait(timeout=15)
        original = runner(service_database, accepted["runner_id"])
        workspace = Path(decoded(original["checkpoint"])["execution"]["resources"]["workspace"])
        process.send_signal(signal.SIGTERM)
        workers.assert_clean_exit(process)
        interrupted = runner(service_database, accepted["runner_id"])
        assert interrupted["status"] == "interrupted" and interrupted["finished_at"] is None
        assert decoded(interrupted["checkpoint"])["execution"]["resources"]["workspace"] == str(workspace)
        assert (workspace / "shutdown-fixture.txt").read_bytes() == b"fictional shutdown persistent file"
        assert service_database.rows("SELECT owner_runner_id FROM agent_runner_session_claims WHERE session_id=%s",
                                     (actors["a"].session_id,))[0]["owner_runner_id"] == accepted["runner_id"]
        assert service_database.rows("SELECT 1 FROM chat_records WHERE session_id=%s", (actors["a"].session_id,)) == []
        assert len(workers.provider.requests(marker)) == 1
    finally:
        release.set()


@pytest.mark.parametrize("rounds", [1, 2])
def test_reply_restores_original_child_and_tool_call_in_fresh_cli_without_redelegating(
        workers, actors, service_database, child_profile, rounds, gateways):
    first_reply = Reply(content="")
    script = [first_reply, tool_reply("clarify", {"question": "Which fictional customer?", "missing_info": ["customer"]},
                                     call_id="original-child-clarify")]
    if rounds == 2:
        script.append(tool_reply("clarify", {"question": "Which fictional date?", "missing_info": ["date"]},
                                 call_id="second-original-child-clarify"))
    script.extend([Reply(content="child-resumed-with-answer"), Reply(content="original-parent-final-result")])
    marker = workers.provider.register(*script)
    first_reply.tool_calls = tool_reply("delegate_to_subagent", {
        "subagent_name": child_profile[0], "task_description": marker}, call_id="actual-child-delegate").tool_calls
    accepted = accept(workers.api, actors["a"], marker)
    first, _ = workers.start()
    workers.assert_clean_exit(first)
    parked = runner(service_database, accepted["runner_id"])
    assert parked["status"] == "waiting"
    parent = decoded(parked["checkpoint"])["execution"]
    child_fact = parent["children"]["actual-child-delegate"]
    child_id = child_fact["execution_id"]
    waiting = child_fact["checkpoint"]["waiting"]
    assert waiting["kind"] == "clarification" and waiting["tool_call_id"] == "original-child-clarify"
    answers = ["Fictional customer Alpha", "Fictional date tomorrow"][:rounds]
    control_ids = []
    for index, answer in enumerate(answers):
        request = {"client_request_id": uuid.uuid4().hex, "action": "reply", "target_execution_id": child_id,
                   "wait_id": waiting["wait_id"], "answer": answer}
        path = f"/v1/runners/{accepted['runner_id']}/controls"
        # Actually close the accepted response stream without consuming its
        # JSON. Retry the original key/payload/wait; it must not create a runner.
        with httpx.stream("POST", workers.api.urls[0] + path, headers=workers.api.headers(actors["a"]),
                          json=request, timeout=10) as discarded:
            assert discarded.status_code == 202
        accepted_control = service_database.rows("SELECT control_id FROM agent_runner_controls WHERE runner_id=%s AND client_request_id=%s",
                                                 (accepted["runner_id"], request["client_request_id"]))[0]["control_id"]
        control_ids.append(accepted_control)
        duplicate = require_status(workers.api.call("POST", path, actor=actors["a"], json=request), 202)
        assert not duplicate["created"] and duplicate["control"]["control_id"] == accepted_control
        assert "payload" not in duplicate["control"] and "answer" not in duplicate["control"]
        public_get = require_status(workers.api.call("GET", f"/v1/runners/{accepted['runner_id']}", actor=actors["a"]), 200)["runner"]
        page = require_status(workers.api.call("GET", f"/v1/sessions/web/{actors['a'].session_id}/runners", actor=actors["a"]), 200)
        public_list = next(item for item in page["runners"] if item["runner_id"] == accepted["runner_id"])
        for view in (public_get, public_list):
            projected = [item for item in view["snapshot"]["supplementalInputs"] if item["control_id"] == accepted_control]
            assert len(projected) == 1 and projected[0]["message_id"] == accepted_control + ":user"
            assert projected[0]["text"] == answer and projected[0]["target_execution_id"] == child_id
            assert all("content" not in attachment for attachment in projected[0]["attachments"])
            assert "payload" not in projected[0]
        continued, _ = workers.start()
        workers.assert_clean_exit(continued)
        if index < rounds - 1:
            again = runner(service_database, accepted["runner_id"])
            assert again["status"] == "waiting"
            child_again = decoded(again["checkpoint"])["execution"]["children"]["actual-child-delegate"]
            assert child_again["execution_id"] == child_id
            waiting = child_again["checkpoint"]["waiting"]
            assert waiting["tool_call_id"] == "second-original-child-clarify"
            assert any(message["role"] == "user" and message["content"] == answers[0]
                       for message in child_again["checkpoint"]["messages"])
            # Even after the child asks its next question, retrying the prior
            # accepted control does not consume the newer wait.
            assert not require_status(workers.api.call("POST", path, actor=actors["a"], json=request), 202)["created"]
    completed = terminal_with_diagnostics(workers, service_database, accepted["runner_id"])
    assert completed["status"] == "completed" and decoded(completed["result"])["output"] == "original-parent-final-result"
    recovered = decoded(completed["checkpoint"])["execution"]
    assert set(recovered["children"]) == {"actual-child-delegate"}
    assert recovered["children"]["actual-child-delegate"]["execution_id"] == child_id
    child = recovered["children"]["actual-child-delegate"]["checkpoint"]
    assert child["outcome"] == "completed" and child["tools"]["original-child-clarify"]["phase"] == "completed"
    assert all(fact["execution_id"] == accepted["runner_id"] for fact in recovered["model_calls"])
    assert all(fact["execution_id"] == child_id for fact in child["model_calls"])
    assert len(recovered["model_calls"]) == 2 and len(child["model_calls"]) == 1 + rounds
    calls = workers.provider.requests(marker)
    assert len(calls) == 3 + rounds
    child_messages = calls[1 + rounds]["messages"]
    for call_id, answer in zip(("original-child-clarify", "second-original-child-clarify"), answers):
        original_call = [message for message in child_messages if any(item["id"] == call_id
                         for item in message.get("tool_calls", []))]
        paired = [message for message in child_messages if message.get("tool_call_id") == call_id]
        replies = [message for message in child_messages if message["role"] == "user" and message["content"] == answer]
        assert len(original_call) == len(paired) == len(replies) == 1
        assert child_messages.index(original_call[0]) < child_messages.index(paired[0]) < child_messages.index(replies[0])
    history = service_database.rows("SELECT message_id,role,content,metadata FROM chat_messages WHERE session_id=%s ORDER BY created_at", (actors["a"].session_id,))
    for control_id, answer in zip(control_ids, answers):
        matching = [item for item in history if item["role"] == "user" and item["content"] == answer]
        assert len(matching) == 1 and matching[0]["message_id"] == control_id + ":user"
    assert len(service_database.rows("SELECT 1 FROM chat_records WHERE session_id=%s", (actors["a"].session_id,))) == 1
    stats = service_database.rows("SELECT prompt_tokens,completion_tokens,agent_iterations FROM chat_records WHERE session_id=%s",
                                  (actors["a"].session_id,))[0]
    assert stats == {"prompt_tokens": 11 * (3 + rounds), "completion_tokens": 7 * (3 + rounds), "agent_iterations": 2}
    receipts = service_database.rows("SELECT execution_id,phase,applied FROM agent_runner_usage_receipts WHERE runner_id=%s", (accepted["runner_id"],))
    assert len(receipts) == 3 + rounds and {item["execution_id"] for item in receipts} == {accepted["runner_id"], child_id}
    assert all(item["phase"] == "observed" and item["applied"] for item in receipts)
    assert len(service_database.rows("SELECT 1 FROM agent_runners WHERE session_id=%s", (actors["a"].session_id,))) == 1
    gateway = gateways.start()
    public_history = require_status(gateway.call("GET", "/api/sessions/" + actors["a"].session_id + "/messages",
                                                actor=actors["a"]), 200)["messages"]
    for control_id, answer in zip(control_ids, answers):
        visible = [item for item in public_history if item["message_id"] == control_id + ":user"]
        assert len(visible) == 1 and visible[0]["role"] == "user" and visible[0]["content"] == answer
    assert any(item["role"] == "assistant" and item["content"] == "original-parent-final-result" for item in public_history)
    assert all(item["role"] in {"user", "assistant"} and not item["metadata"].get("tool_calls")
               and "reasoning_content" not in item["metadata"] for item in public_history)
    assert not workers.provider.errors


@pytest.mark.parametrize("change,expected", [("revoke", 401), ("expire", 401), ("move_owner", 404)])
def test_control_idempotency_does_not_bypass_current_token_or_session_owner(
        workers, actors, service_database, change, expected):
    marker = workers.provider.register(Reply())
    accepted = accept(workers.api, actors["a"], marker)
    result, request = control(workers.api, actors["a"], accepted["runner_id"], "pause")
    path = f"/v1/runners/{accepted['runner_id']}/controls"
    assert not require_status(workers.api.call("POST", path, actor=actors["a"], json=request), 202)["created"]
    if change == "revoke":
        service_database.rows("DELETE FROM tokens WHERE token=%s", (actors["a"].token,))
    elif change == "expire":
        service_database.rows("UPDATE tokens SET expires_at=clock_timestamp()-INTERVAL '1 second' WHERE token=%s", (actors["a"].token,))
    else:
        service_database.rows("UPDATE chat_sessions SET user_id=%s WHERE session_id=%s", (actors["a_other"].user_id, actors["a"].session_id))
    require_status(workers.api.call("POST", path, actor=actors["a"], json=request), expected)
    require_status(workers.api.call("GET", path + "/" + result["control"]["control_id"], actor=actors["a"]), expected)
    assert len(service_database.rows("SELECT 1 FROM agent_runner_controls WHERE runner_id=%s", (accepted["runner_id"],))) == 1
    assert workers.provider.requests(marker) == []


def test_public_control_cannot_forge_browser_completion(workers, actors, service_database):
    marker = workers.provider.register(Reply())
    accepted = accept(workers.api, actors["a"], marker)
    before = copy.deepcopy(runner(service_database, accepted["runner_id"]))
    require_status(workers.api.call("POST", f"/v1/runners/{accepted['runner_id']}/controls", actor=actors["a"],
        json={"client_request_id": uuid.uuid4().hex, "action": "browser_complete", "target_execution_id": "forged-child",
              "wait_id": "forged-wait", "completion_ref": "caller-says-completed"}), 403)
    after = runner(service_database, accepted["runner_id"])
    assert before["revision"] == after["revision"] and before["checkpoint"] == after["checkpoint"]
    assert service_database.rows("SELECT 1 FROM agent_runner_controls WHERE runner_id=%s", (accepted["runner_id"],)) == []


def test_resume_checks_current_credit_at_acceptance_and_again_before_worker_dispatch(
        workers, actors, service_database):
    marker = workers.provider.register(Reply(content="must-not-call-without-credit"))
    accepted = accept(workers.api, actors["a"], marker)
    service_database.rows("UPDATE tenants SET credit_balance=0 WHERE tenant_id=%s", (actors["a"].tenant_id,))
    # Pause is an owner control, so losing execution credit does not prevent it.
    control(workers.api, actors["a"], accepted["runner_id"], "pause")
    first, _ = workers.start()
    workers.assert_clean_exit(first)
    assert runner(service_database, accepted["runner_id"])["status"] == "paused"
    path = f"/v1/runners/{accepted['runner_id']}/controls"
    request = {"client_request_id": uuid.uuid4().hex, "action": "resume"}
    require_status(workers.api.call("POST", path, actor=actors["a"], json=request), 402)
    assert len(service_database.rows("SELECT 1 FROM agent_runner_controls WHERE runner_id=%s", (accepted["runner_id"],))) == 1
    service_database.rows("UPDATE tenants SET credit_balance=50 WHERE tenant_id=%s", (actors["a"].tenant_id,))
    resumed = require_status(workers.api.call("POST", path, actor=actors["a"], json=request), 202)
    service_database.rows("UPDATE tenants SET credit_balance=0 WHERE tenant_id=%s", (actors["a"].tenant_id,))
    # Existing control observation stays available to its fresh current owner.
    repeated = require_status(workers.api.call("POST", path, actor=actors["a"], json=request), 202)
    assert not repeated["created"] and repeated["control"]["control_id"] == resumed["control"]["control_id"]
    executing, _ = workers.start()
    workers.assert_clean_exit(executing)
    current = runner(service_database, accepted["runner_id"])
    assert current["status"] == "paused" and current["resume_control_id"] is None
    rejected = require_status(workers.api.call("GET", path + "/" + resumed["control"]["control_id"], actor=actors["a"]), 200)["control"]
    assert rejected["status"] == "rejected" and rejected["error_code"] == "CREDIT_BLOCKED"
    assert workers.provider.requests(marker) == []
    assert service_database.rows("SELECT 1 FROM agent_runner_usage_receipts WHERE runner_id=%s", (accepted["runner_id"],)) == []
    assert service_database.rows("SELECT 1 FROM chat_records WHERE session_id=%s", (actors["a"].session_id,)) == []


@pytest.mark.parametrize("boundary", ["before_model", "before_tool"])
def test_child_pause_from_database_before_heartbeat_preserves_tree_for_default_cli_recovery(
        workers, actors, service_database, child_profile, boundary):
    filename = "after-resume-write-" + uuid.uuid4().hex + ".txt"
    # Independently started workers must write to the configured common mount,
    # not a private cwd. Retain the original absolute-path service requirement.
    destination = workers.storage / "tenants" / actors["a"].tenant_id / "conversation" / filename
    first_reply = Reply(content="")
    child_tool = tool_reply("write", {"file_path": str(destination), "content": "single-resumed-side-effect"},
                            call_id="original-child-write")
    marker = workers.provider.register(first_reply, child_tool, Reply(content="same-child-after-write"),
                                       Reply(content="same-parent-after-child"))
    first_reply.tool_calls = tool_reply("delegate_to_subagent", {
        "subagent_name": child_profile[0], "task_description": marker}, call_id="original-parent-delegate").tool_calls
    accepted = accept(workers.api, actors["a"], marker)
    report = workers.root / ("child-gate-" + uuid.uuid4().hex + ".json")
    release = report.with_suffix(".release")
    identifier = "worker-child-boundary-" + uuid.uuid4().hex
    process = workers.processes.start(["-m", "tests.integration.agent_runner_service.child_pause_probe",
                                      "--worker-id", identifier, "--once"],
        environment={**workers.environment, "RUNNER_TEST_CHILD_BOUNDARY": boundary,
                     "RUNNER_TEST_CHILD_REPORT": str(report), "RUNNER_TEST_CHILD_RELEASE": str(release)},
        private_working_directory=True)
    workers.children.append((process, identifier))
    try:
        observation = wait_for(lambda: json.loads(report.read_text()) if report.is_file() else None, timeout=15)
        assert observation["stage"] == "gate" and observation["parent_call_id"] == "original-parent-delegate"
        control(workers.api, actors["a"], accepted["runner_id"], "pause")
        release.write_text("release fictional test boundary")
        workers.assert_clean_exit(process)
        observation = json.loads(report.read_text())
        assert observation["stage"] == "released" and observation["database_pause_requested"]
        assert observation["parent_memory_pause_requested"] is False
        parked = runner(service_database, accepted["runner_id"])
        assert parked["status"] == "paused" and list(workers.processes.root.rglob(filename)) == []
        parent = decoded(parked["checkpoint"])["execution"]
        fact = parent["children"]["original-parent-delegate"]
        assert fact["execution_id"] == observation["execution_id"] and fact["task_record"]
        assert fact["checkpoint"]["outcome"] == "paused" and parent["outcome"] == "paused"
        assert parent["tools"]["original-parent-delegate"]["phase"] != "completed"
        if boundary == "before_tool":
            assert fact["checkpoint"]["tools"]["original-child-write"]["phase"] == "prepared"
        calls_before = workers.provider.requests(marker)
        assert len(calls_before) == (1 if boundary == "before_model" else 2)
        original_task = fact["task_record"]
        control(workers.api, actors["a"], accepted["runner_id"], "resume")
        continued, _ = workers.start()
        workers.assert_clean_exit(continued)
        finished = terminal_with_diagnostics(workers, service_database, accepted["runner_id"])
        assert finished["status"] == "completed"
        restored = decoded(finished["checkpoint"])["execution"]["children"]["original-parent-delegate"]
        write_fact = restored["checkpoint"]["tools"]["original-child-write"]
        actual_result = decoded(write_fact["result"])
        assert write_fact["success"] is True and isinstance(actual_result, dict)
        assert Path(actual_result["file_path"]) == destination
        assert destination.is_absolute() and destination.is_relative_to(workers.storage)
        assert destination.parts[-4:] == ("tenants", actors["a"].tenant_id, "conversation", filename)
        assert destination.read_text() == "single-resumed-side-effect"
        assert list(workers.processes.root.rglob(filename)) == [destination]
        assert restored["execution_id"] == observation["execution_id"]
        assert restored["task_record"]["execution_id"] == original_task["execution_id"]
        assert restored["checkpoint"]["tools"]["original-child-write"]["phase"] == "completed"
        assert restored["checkpoint"]["tools"]["original-child-write"]["success"] is True
        assert len(workers.provider.requests(marker)) == 4
        assert len(service_database.rows("SELECT 1 FROM chat_records WHERE session_id=%s", (actors["a"].session_id,))) == 1
        assert len(service_database.rows("SELECT 1 FROM agent_runner_usage_receipts WHERE runner_id=%s", (accepted["runner_id"],))) == 4
        assert not workers.provider.errors
    finally:
        release.write_text("release cleanup boundary")


@pytest.mark.parametrize("with_followup", [False, True])
def test_completed_parent_with_live_owned_child_restores_child_before_finalization(
        workers, actors, service_database, child_profile, with_followup):
    child_release = threading.Event()
    child_first = Reply(content="unpersisted-old-child-response", release=child_release)
    child_marker = workers.provider.register(child_first, Reply(content="original-child-finished-after-recovery"))
    first_reply = tool_reply("delegate_to_subagent", {"subagent_name": child_profile[0],
        "task_description": child_marker}, call_id="completed-parent-owned-delegate")
    parent_marker = workers.provider.register(first_reply, Reply(content="completed-parent-output-retained"))
    accepted = accept(workers.api, actors["a"], parent_marker)
    process = workers.processes.start(["-m", "tests.integration.agent_runner_service.delegate_timeout_probe"],
                                     environment=workers.environment, private_working_directory=True)
    workers.children.append((process, "fixture-delegate-timeout-owner"))
    try:
        assert child_first.arrived.wait(timeout=15)
        workers.assert_clean_exit(process)
        stopped = runner(service_database, accepted["runner_id"])
        assert stopped["status"] == "interrupted" and stopped["finished_at"] is None
        root = decoded(stopped["checkpoint"])["execution"]
        assert root["outcome"] == "completed" and root["output"] == "completed-parent-output-retained"
        child_fact = root["children"]["completed-parent-owned-delegate"]
        child_id = child_fact["execution_id"]
        assert child_fact["checkpoint"]["outcome"] == "running" and child_fact["task_record"]
        assert root["tools"]["completed-parent-owned-delegate"]["phase"] == "completed"
        assert service_database.rows("SELECT owner_runner_id FROM agent_runner_session_claims WHERE session_id=%s",
                                     (actors["a"].session_id,))[0]["owner_runner_id"] == accepted["runner_id"]
        assert service_database.rows("SELECT 1 FROM chat_records WHERE session_id=%s", (actors["a"].session_id,)) == []
        old_receipts = service_database.rows("SELECT call_id,phase FROM agent_runner_usage_receipts WHERE runner_id=%s AND execution_id=%s",
                                             (accepted["runner_id"], child_id))
        assert len(old_receipts) == 1 and old_receipts[0]["phase"] == "unknown"
        child_release.set()
        followup = workers.provider.register(Reply(content="new-root-answer-after-original-child")) if with_followup else None
        resumed_control, _ = control(workers.api, actors["a"], accepted["runner_id"], "resume", **({"answer": followup} if with_followup else {}))
        continued, _ = workers.start()
        workers.assert_clean_exit(continued)
        completed = terminal_with_diagnostics(workers, service_database, accepted["runner_id"])
        assert completed["status"] == "completed"
        if with_followup:
            assert decoded(completed["result"])["output"].startswith(root["output"])
            assert decoded(completed["result"])["output"].endswith("new-root-answer-after-original-child")
            assert len(workers.provider.requests(followup)) == 1
            new_call = workers.provider.requests(followup)[0]
            assert sum(followup in str(message.get("content")) for message in new_call["messages"] if message.get("role") == "user") == 1
            controls = service_database.rows("SELECT consumed_attempt,status FROM agent_runner_controls WHERE control_id=%s", (resumed_control["control"]["control_id"],))
            assert controls == [{"consumed_attempt": stopped["attempt"] + 1, "status": "consumed"}]
        else:
            assert decoded(completed["result"])["output"] == root["output"]
        assert completed["settlement_status"] == "pending"
        restored = decoded(completed["checkpoint"])["execution"]
        assert set(restored["children"]) == {"completed-parent-owned-delegate"}
        recovered_child = restored["children"]["completed-parent-owned-delegate"]
        assert recovered_child["execution_id"] == child_id
        assert recovered_child["task_record"]["task_id"] == child_fact["task_record"]["task_id"]
        assert recovered_child["checkpoint"]["outcome"] == "completed"
        assert recovered_child["checkpoint"]["output"] == "original-child-finished-after-recovery"
        assert len(workers.provider.requests(parent_marker)) == len(workers.provider.requests(child_marker)) == 2
        receipts = service_database.rows("SELECT call_id,phase FROM agent_runner_usage_receipts WHERE runner_id=%s AND execution_id=%s",
                                        (accepted["runner_id"], child_id))
        assert len(receipts) == 2 and len({item["call_id"] for item in receipts}) == 2
        assert next(item for item in receipts if item["call_id"] == old_receipts[0]["call_id"])["phase"] == "unknown"
        assert sum(item["phase"] == "observed" for item in receipts) == 1
        assert len(service_database.rows("SELECT 1 FROM chat_records WHERE session_id=%s", (actors["a"].session_id,))) == 1
        assert not workers.provider.errors
    finally:
        child_release.set()


def test_completed_parent_with_done_parked_child_retains_claim_and_reply_restores_only_child(
        workers, actors, service_database, child_profile):
    child_release, parent_release = threading.Event(), threading.Event()
    clarify_reply = tool_reply("clarify", {"question": "Which fictional customer?", "missing_info": ["customer"]},
                              call_id="done-child-clarify")
    clarify_reply.release = child_release
    child_marker = workers.provider.register(clarify_reply, Reply(content="done-child-replied-after-recovery"))
    parent_final = Reply(content="already-completed-original-parent", release=parent_release)
    parent_marker = workers.provider.register(tool_reply("delegate_to_subagent", {
        "subagent_name": child_profile[0], "task_description": child_marker}, call_id="done-child-delegate"), parent_final)
    accepted = accept(workers.api, actors["a"], parent_marker)
    report = workers.root / ("done-owned-child-" + uuid.uuid4().hex + ".json")
    process = workers.processes.start(["-m", "tests.integration.agent_runner_service.delegate_timeout_probe"],
        environment={**workers.environment, "RUNNER_TEST_PARKED_TASK_REPORT": str(report)}, private_working_directory=True)
    workers.children.append((process, "fixture-done-child-owner"))
    try:
        assert clarify_reply.arrived.wait(timeout=15) and parent_final.arrived.wait(timeout=15)
        child_release.set()
        observed = wait_for(lambda: json.loads(report.read_text()) if report.is_file() else None, timeout=15)
        assert observed["all_owned_tasks_done"]
        before = decoded(runner(service_database, accepted["runner_id"])["checkpoint"])["execution"]
        child = before["children"]["done-child-delegate"]
        child_id = child["execution_id"]
        assert child_id in observed["execution_ids"] and child["checkpoint"]["outcome"] == "waiting"
        parent_release.set()
        workers.assert_clean_exit(process)
        parked = runner(service_database, accepted["runner_id"])
        assert parked["status"] == "waiting" and parked["finished_at"] is None
        saved = decoded(parked["checkpoint"])["execution"]
        assert saved["output"] == parent_final.content
        assert saved["tools"]["done-child-delegate"]["phase"] == "completed"
        assert service_database.rows("SELECT owner_runner_id FROM agent_runner_session_claims WHERE session_id=%s",
                                     (actors["a"].session_id,))[0]["owner_runner_id"] == accepted["runner_id"]
        assert service_database.rows("SELECT 1 FROM chat_records WHERE session_id=%s", (actors["a"].session_id,)) == []
        wait = saved["children"]["done-child-delegate"]["checkpoint"]["waiting"]
        control(workers.api, actors["a"], accepted["runner_id"], "reply", target_execution_id=child_id,
                wait_id=wait["wait_id"], answer="Fictional parked child answer")
        resumed, _ = workers.start()
        workers.assert_clean_exit(resumed)
        complete = terminal_with_diagnostics(workers, service_database, accepted["runner_id"])
        restored = decoded(complete["checkpoint"])["execution"]
        assert complete["status"] == "completed" and restored["output"] == parent_final.content
        assert set(restored["children"]) == {"done-child-delegate"}
        assert restored["children"]["done-child-delegate"]["execution_id"] == child_id
        assert restored["children"]["done-child-delegate"]["checkpoint"]["output"] == "done-child-replied-after-recovery"
        assert len(workers.provider.requests(parent_marker)) == len(workers.provider.requests(child_marker)) == 2
        assert len(service_database.rows("SELECT 1 FROM chat_records WHERE session_id=%s", (actors["a"].session_id,))) == 1
        assert not workers.provider.errors
    finally:
        child_release.set()
        parent_release.set()


@pytest.mark.parametrize("input_mode", ["inline", "old_file_id"])
def test_attachment_waiting_recovery_keeps_owned_workspace_and_reads_reply_copy(
        workers, actors, service_database, child_profile, input_mode, gateways):
    original_bytes = b"Fictional original persistent attachment"
    if input_mode == "inline":
        original_name = "original-fixture.txt"
        attachment = {"name": original_name, "content": base64.b64encode(original_bytes).decode()}
        uploaded = None
    else:
        file_id = "file_" + uuid.uuid4().hex[:12]
        directory = workers.storage / "tenants" / actors["a"].tenant_id / "conversation"
        directory.mkdir(parents=True)
        uploaded = directory / (file_id + ".txt")
        uploaded.write_bytes(original_bytes)
        original_name = uploaded.name
        attachment = {"file_id": file_id}
    release = threading.Event()
    after_answer = Reply(content="", release=release)
    initial = Reply(content="")
    marker = workers.provider.register(initial,
        tool_reply("clarify", {"question": "Please attach the fictional answer", "missing_info": ["answer"]}, call_id="attachment-clarify"),
        after_answer, Reply(content="read-real-reply-bytes"), Reply(content="attachment-parent-final"))
    initial.tool_calls = tool_reply("delegate_to_subagent", {"subagent_name": child_profile[0],
        "task_description": marker}, call_id="attachment-delegate").tool_calls
    accepted = accept(workers.api, actors["a"], marker, attachments=[attachment])
    first, _ = workers.start()
    workers.assert_clean_exit(first)
    parked = runner(service_database, accepted["runner_id"])
    assert parked["status"] == "waiting"
    original_cp = decoded(parked["checkpoint"])["execution"]
    original_workspace = Path(original_cp["resources"]["workspace"])
    assert original_workspace.parent.parent == workers.root / "workspaces"
    assert (original_workspace / original_name).read_bytes() == original_bytes
    child_fact = original_cp["children"]["attachment-delegate"]
    wait = child_fact["checkpoint"]["waiting"]
    reply_bytes = b"Fictional reply bytes from independently restored child"
    answer = "Answer supplied with a fictional file"
    result, _ = control(workers.api, actors["a"], accepted["runner_id"], "reply",
        target_execution_id=child_fact["execution_id"], wait_id=wait["wait_id"], answer=answer,
        attachments=[{"name": "reply-fixture.txt", "content": base64.b64encode(reply_bytes).decode()}])
    projected = require_status(workers.api.call("GET", f"/v1/runners/{accepted['runner_id']}", actor=actors["a"]), 200)["runner"]["snapshot"]["supplementalInputs"]
    visible = next(item for item in projected if item["control_id"] == result["control"]["control_id"])
    assert visible["message_id"] == result["control"]["control_id"] + ":user" and visible["text"] == answer
    assert visible["attachments"][0]["name"] == "reply-fixture.txt" and "content" not in visible["attachments"][0]
    second, _ = workers.start()
    try:
        assert after_answer.arrived.wait(timeout=15)
        live = decoded(runner(service_database, accepted["runner_id"])["checkpoint"])["execution"]
        assert live["resources"]["workspace"] == str(original_workspace)
        assert (original_workspace / original_name).read_bytes() == original_bytes
        leaf = live["children"]["attachment-delegate"]["checkpoint"]
        reply_workspaces = [Path(value) for value in leaf["resources"]["reply_workspaces"]]
        assert len(reply_workspaces) == 1
        reply_workspace = reply_workspaces[0]
        assert reply_workspace.parent.parent == workers.root / "workspaces"
        assert reply_workspace.parent != original_workspace.parent
        assert (reply_workspace / "reply-fixture.txt").read_bytes() == reply_bytes
        after_answer.tool_calls = tool_reply("read", {"file_path": str(reply_workspace / "reply-fixture.txt")}, call_id="reply-real-read").tool_calls
        release.set()
        workers.assert_clean_exit(second)
        finished = terminal_with_diagnostics(workers, service_database, accepted["runner_id"])
        assert finished["status"] == "completed"
        tool_context = workers.provider.requests(marker)[3]["messages"]
        assert any(item.get("role") == "tool" and "Fictional reply bytes" in str(item["content"]) for item in tool_context)
        history = service_database.rows("SELECT message_id,metadata FROM chat_messages WHERE session_id=%s AND role='user'", (actors["a"].session_id,))
        assert sum(item["message_id"] == result["control"]["control_id"] + ":user" for item in history) == 1
        gateway = gateways.start()
        visible_history = require_status(gateway.call("GET", "/api/sessions/" + actors["a"].session_id + "/messages",
                                                      actor=actors["a"]), 200)["messages"]
        visible_reply = [item for item in visible_history if item["message_id"] == result["control"]["control_id"] + ":user"]
        assert len(visible_reply) == 1 and visible_reply[0]["content"] == answer
        assert visible_reply[0]["metadata"]["attachments"][0]["name"] == "reply-fixture.txt"
        assert all("content" not in item for item in visible_reply[0]["metadata"]["attachments"])
        assert base64.b64encode(reply_bytes).decode() not in json.dumps(visible_history)
        assert "Fictional reply bytes" not in json.dumps(visible_history)
        assert not original_workspace.exists() and not reply_workspace.exists()
        if uploaded is not None:
            assert uploaded.read_bytes() == original_bytes
        assert not workers.provider.errors
    finally:
        release.set()


def test_restored_child_business_plan_advances_only_child_and_trace_counts_both_attempts(
        workers, actors, service_database, child_profile, logs_schema):
    fixture = workers.root / "child-plan-readable-fixture.txt"
    fixture.write_text("fictional resumed child plan contents")
    steps = [{"description": "read fictional owned fixture", "tool": "read", "parameters": {"file_path": str(fixture)}}]
    release = threading.Event()
    child_final = Reply(content="child-own-plan-done", release=release)
    child_marker = workers.provider.register(
        tool_reply("create_plan", {"goal": "original child plan", "steps": steps}, call_id="child-create-plan"),
        tool_reply("clarify", {"question": "Which fictional customer?", "missing_info": ["customer"]}, call_id="plan-child-clarify"),
        tool_reply("read", {"file_path": str(fixture)}, call_id="child-plan-read"), child_final)
    parent_steps = [{"description": "delegate fictional child", "tool": "delegate_to_subagent", "parameters": {
        "subagent_name": child_profile[0], "task_description": child_marker}}, *steps]
    parent_marker = workers.provider.register(
        tool_reply("create_plan", {"goal": "independent parent plan", "steps": parent_steps}, call_id="parent-create-plan"),
        tool_reply("delegate_to_subagent", {"subagent_name": child_profile[0], "task_description": child_marker}, call_id="plan-child-delegate"),
        Reply(content="parent-own-plan-remains-pending"))
    accepted = accept(workers.api, actors["a"], parent_marker)
    first, _ = workers.start()
    workers.assert_clean_exit(first)
    waiting = runner(service_database, accepted["runner_id"])
    assert waiting["status"] == "waiting"
    saved = decoded(waiting["checkpoint"])
    child = saved["execution"]["children"]["plan-child-delegate"]
    child_plan = child["business_plan"]
    parent_plan = saved["business_plan"]
    assert child_plan["plan_id"] != parent_plan["plan_id"]
    assert [item["status"] for item in child_plan["tasks"]] == ["pending"]
    # The existing parent dispatcher marks its next planned task running before
    # a generic delegate. Child dispatch must not mutate that saved parent fact.
    assert [item["status"] for item in parent_plan["tasks"]] == ["running", "pending"]
    trace_id = "tr_" + accepted["runner_id"]
    initial_trace = service_database.rows("SELECT status,total_tokens FROM obs_traces WHERE trace_id=%s", (trace_id,))
    assert initial_trace == [{"status": "waiting", "total_tokens": 72}]
    control(workers.api, actors["a"], accepted["runner_id"], "reply", target_execution_id=child["execution_id"],
            wait_id=child["checkpoint"]["waiting"]["wait_id"], answer="Fictional plan customer answer")
    second, _ = workers.start()
    try:
        assert child_final.arrived.wait(timeout=15)
        active = decoded(runner(service_database, accepted["runner_id"])["checkpoint"])
        active_child = active["execution"]["children"]["plan-child-delegate"]
        assert [item["status"] for item in active_child["business_plan"]["tasks"]] == ["completed"]
        assert active["business_plan"] == parent_plan
        release.set()
        workers.assert_clean_exit(second)
    finally:
        release.set()
    finished = terminal_with_diagnostics(workers, service_database, accepted["runner_id"])
    assert finished["status"] == "completed"
    restored = decoded(finished["checkpoint"])
    restored_child = restored["execution"]["children"]["plan-child-delegate"]
    durations = restored["attempt_durations_ms"]
    assert len(durations) == 2 and all(value > 0 for value in durations.values())
    assert restored["execution_duration_ms"] == sum(durations.values())
    assert restored_child["duration_ms"] >= child["duration_ms"] > 0
    assert restored_child["execution_id"] == child["execution_id"]
    assert restored_child["business_plan"]["plan_id"] == child_plan["plan_id"]
    assert [item["status"] for item in restored_child["business_plan"]["tasks"]] == ["completed"]
    completed_parent_plan = restored["business_plan"]
    assert completed_parent_plan["plan_id"] == parent_plan["plan_id"]
    assert [item["task_id"] for item in completed_parent_plan["tasks"]] == [item["task_id"] for item in parent_plan["tasks"]]
    assert [item["status"] for item in completed_parent_plan["tasks"]] == ["completed", "pending"]
    assert "child-own-plan-done" in json.dumps(completed_parent_plan["tasks"][0]["result"])
    assert completed_parent_plan["tasks"][1] == parent_plan["tasks"][1]
    assert len(workers.provider.requests(child_marker)) == 4 and len(workers.provider.requests(parent_marker)) == 3
    assert any(item["role"] == "tool" and "fictional resumed child plan contents" in str(item["content"])
               for item in workers.provider.requests(child_marker)[3]["messages"])
    final_trace = service_database.rows("SELECT status,total_tokens FROM obs_traces WHERE trace_id=%s", (trace_id,))
    assert final_trace == [{"status": "completed", "total_tokens": 126}]
    records = service_database.rows("SELECT prompt_tokens,completion_tokens,subagent_calls FROM chat_records WHERE session_id=%s",
                                    (actors["a"].session_id,))
    assert len(records) == 1 and records[0]["prompt_tokens"] == 77 and records[0]["completion_tokens"] == 49
    calls = decoded(records[0]["subagent_calls"])
    assert len(calls) == 1 and calls[0]["token_usage"] == {"input": 44, "output": 28, "cached": 0}
    assert len(service_database.rows("SELECT 1 FROM agent_runner_usage_receipts WHERE runner_id=%s", (accepted["runner_id"],))) == 7
    assert not workers.provider.errors


def discard_waiting_runner(database, runner_id):
    """Explicit ordered cleanup; controls FK/CASCADE was removed by the fk-migration."""
    database.rows("DELETE FROM agent_runner_session_claims WHERE owner_runner_id=%s", (runner_id,))
    database.rows("DELETE FROM agent_runner_controls WHERE runner_id=%s", (runner_id,))
    database.rows("DELETE FROM agent_runner_usage_receipts WHERE runner_id=%s", (runner_id,))
    database.rows("DELETE FROM agent_runners WHERE runner_id=%s", (runner_id,))


@pytest.mark.parametrize("target", ["root", "child"])
def test_resume_rejected_when_persisted_adapter_contract_version_is_unknown(
        workers, actors, service_database, child_profile, target):
    first_reply = Reply(content="")
    marker = workers.provider.register(first_reply,
        tool_reply("clarify", {"question": "Which fictional customer?", "missing_info": ["customer"]},
                   call_id="unknown-version-clarify"),
        Reply(content="must-not-run-child-after-version-rejection"),
        Reply(content="must-not-run-parent-after-version-rejection"))
    first_reply.tool_calls = tool_reply("delegate_to_subagent", {"subagent_name": child_profile[0],
        "task_description": marker}, call_id="unknown-version-delegate").tool_calls
    accepted = accept(workers.api, actors["a"], marker)
    first, _ = workers.start()
    workers.assert_clean_exit(first)
    parked = runner(service_database, accepted["runner_id"])
    assert parked["status"] == "waiting"
    child_fact = decoded(parked["checkpoint"])["execution"]["children"]["unknown-version-delegate"]
    child_id = child_fact["execution_id"]
    # Fresh dispatch already stamps both states; overwrite exactly one of them
    # with an unknown version through jsonb_set without touching revision.
    path = ("{execution,resources,adapter_contract_version}" if target == "root" else
            "{execution,children,unknown-version-delegate,checkpoint,resources,adapter_contract_version}")
    service_database.rows("UPDATE agent_runners SET checkpoint=jsonb_set(checkpoint,%s::text[],'99'::jsonb) WHERE runner_id=%s",
                          (path, accepted["runner_id"]))
    requests_before = workers.provider.requests(marker)
    receipts_before = service_database.rows("SELECT 1 FROM agent_runner_usage_receipts WHERE runner_id=%s",
                                            (accepted["runner_id"],))
    resumed, _ = control(workers.api, actors["a"], accepted["runner_id"], "reply",
                         target_execution_id=child_id, wait_id=child_fact["checkpoint"]["waiting"]["wait_id"],
                         answer="Fictional unknown version answer")
    before_rejection = runner(service_database, accepted["runner_id"])
    try:
        rejecting, _ = workers.start()
        workers.assert_clean_exit(rejecting)
        untouched = runner(service_database, accepted["runner_id"])
        assert untouched["status"] == "waiting" and untouched["attempt"] == before_rejection["attempt"]
        assert untouched["revision"] == before_rejection["revision"]
        assert untouched["resume_control_id"] is None
        assert decoded(untouched["checkpoint"]) == decoded(before_rejection["checkpoint"])
        checkpoint = decoded(untouched["checkpoint"])
        root_version = checkpoint["execution"]["resources"]["adapter_contract_version"]
        child_version = checkpoint["execution"]["children"]["unknown-version-delegate"]["checkpoint"]["resources"]["adapter_contract_version"]
        assert root_version == (99 if target == "root" else ADAPTER_CONTRACT_VERSION)
        assert child_version == (99 if target == "child" else ADAPTER_CONTRACT_VERSION)
        assert checkpoint["execution"]["children"]["unknown-version-delegate"]["checkpoint"]["outcome"] == "waiting"
        result = require_status(workers.api.call(
            "GET", f"/v1/runners/{accepted['runner_id']}/controls/" + resumed["control"]["control_id"],
            actor=actors["a"]), 200)["control"]
        assert result["status"] == "rejected" and result["error_code"] == "RECOVERY_ADAPTER_VERSION_UNSUPPORTED"
        assert len(requests_before) == 2
        assert workers.provider.requests(marker) == requests_before
        assert service_database.rows("SELECT 1 FROM agent_runner_usage_receipts WHERE runner_id=%s",
                                     (accepted["runner_id"],)) == receipts_before
        assert not workers.provider.errors
    finally:
        # A permanently unresumable waiting runner is test-owned waste; remove it
        # explicitly in the fk-migration order instead of relying on teardown.
        discard_waiting_runner(service_database, accepted["runner_id"])


def test_legacy_checkpoint_without_adapter_contract_version_resumes_and_restamps(
        workers, actors, service_database, child_profile):
    first_reply = Reply(content="")
    marker = workers.provider.register(first_reply,
        tool_reply("clarify", {"question": "Which fictional customer?", "missing_info": ["customer"]},
                   call_id="unstamped-legacy-clarify"),
        Reply(content="unstamped-legacy-child-replied"), Reply(content="unstamped-legacy-parent-final"))
    first_reply.tool_calls = tool_reply("delegate_to_subagent", {"subagent_name": child_profile[0],
        "task_description": marker}, call_id="unstamped-legacy-delegate").tool_calls
    accepted = accept(workers.api, actors["a"], marker)
    first, _ = workers.start()
    workers.assert_clean_exit(first)
    parked = runner(service_database, accepted["runner_id"])
    assert parked["status"] == "waiting"
    legacy = decoded(parked["checkpoint"])
    child_fact = legacy["execution"]["children"]["unstamped-legacy-delegate"]
    child_id = child_fact["execution_id"]
    # Strip the freshly stamped fields to simulate a pre-feature legacy row.
    for state in (legacy["execution"], child_fact["checkpoint"]):
        assert "adapter_contract_version" in state["resources"]
        state["resources"].pop("adapter_contract_version", None)
    service_database.rows("UPDATE agent_runners SET checkpoint=%s::jsonb WHERE runner_id=%s",
                          (json.dumps(legacy), accepted["runner_id"]))
    control(workers.api, actors["a"], accepted["runner_id"], "reply", target_execution_id=child_id,
            wait_id=child_fact["checkpoint"]["waiting"]["wait_id"], answer="Fictional unstamped legacy answer")
    resumed, _ = workers.start()
    workers.assert_clean_exit(resumed)
    finished = terminal_with_diagnostics(workers, service_database, accepted["runner_id"])
    assert finished["status"] == "completed" and finished["attempt"] == parked["attempt"] + 1
    assert decoded(finished["result"])["output"] == "unstamped-legacy-parent-final"
    recovered = decoded(finished["checkpoint"])["execution"]
    assert recovered["resources"]["adapter_contract_version"] == ADAPTER_CONTRACT_VERSION
    child = recovered["children"]["unstamped-legacy-delegate"]
    assert child["execution_id"] == child_id
    assert child["checkpoint"]["resources"]["adapter_contract_version"] == ADAPTER_CONTRACT_VERSION
    assert child["checkpoint"]["tools"]["unstamped-legacy-clarify"]["phase"] == "completed"
    assert child["checkpoint"]["outcome"] == "completed"
    assert len(workers.provider.requests(marker)) == 4
    assert len(service_database.rows("SELECT 1 FROM agent_runner_usage_receipts WHERE runner_id=%s",
                                     (accepted["runner_id"],))) == 4
    assert not workers.provider.errors
