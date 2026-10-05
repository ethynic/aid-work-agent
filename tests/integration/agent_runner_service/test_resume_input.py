"""New user input continues the original runner through fresh HTTP/CLI owners."""
import base64
from decimal import Decimal
import copy
import json
import threading
import uuid

import pytest

from .provider import Reply, tool_reply
from .test_api import require_status
from .test_worker import workers, prices, api_pair, accept, decoded, runner
from .test_worker_recovery import control, terminal_with_diagnostics
from .test_web_gateway import gateways

pytestmark = pytest.mark.integration


def text_in(request):
    return json.dumps(request["messages"], ensure_ascii=False)


@pytest.mark.parametrize("unstarted", [True, False])
def test_resume_input_retries_original_key_then_runs_same_runner_with_paired_old_facts(
        workers, actors, service_database, gateways, unstarted):
    original_gate = threading.Event()
    fixture = workers.root / "resume-original-read.txt"
    fixture.write_text("fictional original tool bytes")
    initial = tool_reply("read", {"file_path": str(fixture)}, call_id="resume-old-read")
    initial.release = original_gate
    original_marker = workers.provider.register(initial)
    accepted = accept(workers.api, actors["a"], original_marker)
    immutable = runner(service_database, accepted["runner_id"])
    if unstarted:
        control(workers.api, actors["a"], accepted["runner_id"], "pause")
        process, _ = workers.start()
        workers.assert_clean_exit(process)
    else:
        process, _ = workers.start()
        try:
            assert initial.arrived.wait(15)
            control(workers.api, actors["a"], accepted["runner_id"], "pause")
            original_gate.set()
            workers.assert_clean_exit(process)
        finally:
            original_gate.set()
    parked = runner(service_database, accepted["runner_id"])
    assert parked["status"] == "paused"
    assert bool(decoded(parked["checkpoint"]).get("unstarted")) == unstarted
    followup_release = threading.Event()
    followup_read = tool_reply("read", {"file_path": "fixture-locator-not-yet-observed"}, call_id="resume-supplement-read")
    followup_read.release = followup_release
    followup_marker = workers.provider.register(followup_read, Reply(content="fictional resumed final response"))
    attachment = {"name": "resume-supplement.txt", "type": "file", "mime_type": "text/plain",
                  "content": base64.b64encode(b"fictional resumed attachment bytes").decode()}
    key = uuid.uuid4().hex
    received, intent = control(workers.api, actors["a"], accepted["runner_id"], "resume", key=key,
                              answer=followup_marker, attachments=[attachment])
    repeated = require_status(workers.api.call("POST", f"/v1/runners/{accepted['runner_id']}/controls",
                                               actor=actors["a"], json=intent), 202)
    assert not repeated["created"] and repeated["control"]["control_id"] == received["control"]["control_id"]
    changed = dict(intent, answer="different immutable followup")
    require_status(workers.api.call("POST", f"/v1/runners/{accepted['runner_id']}/controls",
                                  actor=actors["a"], json=changed), 409)
    public = require_status(workers.api.call("GET", f"/v1/runners/{accepted['runner_id']}", actor=actors["a"]), 200)["runner"]
    supplement = public["snapshot"]["supplementalInputs"]
    assert len(supplement) == 1 and supplement[0]["text"] == followup_marker
    assert "content" not in supplement[0]["attachments"][0]
    resumed, _ = workers.start()
    try:
        assert followup_read.arrived.wait(15)
        owned_files = [item for item in workers.root.rglob("*resume-supplement.txt") if item.is_file()]
        assert len(owned_files) == 1 and owned_files[0].read_bytes() == b"fictional resumed attachment bytes"
        request = workers.provider.requests(followup_marker)[0]
        assert str(owned_files[0]) in text_in(request)
        followup_read.tool_calls[0]["function"]["arguments"] = json.dumps({"file_path": str(owned_files[0])})
        followup_release.set()
        workers.assert_clean_exit(resumed)
    finally:
        followup_release.set()
    finished = terminal_with_diagnostics(workers, service_database, accepted["runner_id"])
    assert finished["status"] == "completed" and finished["attempt"] == parked["attempt"] + 1
    for field in ("runner_id", "queue_order", "input", "input_digest", "accepted_at", "profile_fingerprint"):
        assert finished[field] == immutable[field]
    calls = workers.provider.requests(followup_marker)
    assert len(calls) == 2 and "fictional resumed attachment bytes" in text_in(calls[1])
    assert text_in(calls[0]).count(followup_marker) == 1
    if unstarted:
        assert workers.provider.requests(original_marker) == []
        assert original_marker in text_in(calls[0])
    else:
        assert len(workers.provider.requests(original_marker)) == 1
        messages = calls[0]["messages"]
        assistant = next(index for index, message in enumerate(messages) if message.get("tool_calls"))
        tool = next(index for index, message in enumerate(messages) if message.get("tool_call_id") == "resume-old-read")
        answer = next(index for index, message in enumerate(messages) if message.get("role") == "user" and followup_marker in str(message.get("content")))
        assert assistant < tool < answer
        fact = decoded(finished["checkpoint"])["execution"]["tools"]["resume-old-read"]
        assert fact["phase"] == "completed" and fact["result_recorded"]
    control_id = received["control"]["control_id"]
    status = require_status(workers.api.call("GET", f"/v1/runners/{accepted['runner_id']}/controls/{control_id}", actor=actors["a"]), 200)["control"]
    assert status["status"] == "consumed"
    retried_after = require_status(workers.api.call("POST", f"/v1/runners/{accepted['runner_id']}/controls",
                                                   actor=actors["a"], json=intent), 202)
    assert not retried_after["created"] and retried_after["control"]["control_id"] == control_id
    rows = service_database.rows("SELECT message_id,role,content FROM chat_messages WHERE session_id=%s ORDER BY created_at", (actors["a"].session_id,))
    own = [row for row in rows if row["message_id"] == control_id + ":user"]
    assert len(own) == 1 and own[0]["content"] == followup_marker
    gateway = gateways.start()
    history = require_status(gateway.call("GET", f"/api/sessions/{actors['a'].session_id}/messages", actor=actors["a"]), 200)["messages"]
    assert len([item for item in history if item["message_id"] == control_id + ":user" and item["content"] == followup_marker]) == 1
    assert any(item["role"] == "assistant" and "fictional resumed final response" in item["content"] for item in history)
    assert service_database.rows("SELECT count(*) AS n FROM agent_runners WHERE session_id=%s", (actors["a"].session_id,)) == [{"n": 1}]
    physical = 2 if unstarted else 3
    receipts = service_database.rows("SELECT call_id,phase,applied FROM agent_runner_usage_receipts WHERE runner_id=%s", (accepted["runner_id"],))
    assert len(receipts) == physical and len({item["call_id"] for item in receipts}) == physical
    assert all(item["phase"] == "observed" and item["applied"] for item in receipts)
    record = service_database.rows("SELECT prompt_tokens,completion_tokens,credit_cost FROM chat_records WHERE session_id=%s", (actors["a"].session_id,))
    assert record == [{"prompt_tokens": 11 * physical, "completion_tokens": 7 * physical, "credit_cost": Decimal("0.01")}]
    assert service_database.rows("SELECT credit_balance FROM tenants WHERE tenant_id=%s", (actors["a"].tenant_id,)) == [{"credit_balance": Decimal("999.99")}]
    assert not workers.provider.errors


@pytest.mark.parametrize("rejection", ["budget", "unknown_effect", "foreign_attachment"])
def test_resume_input_rejects_before_consuming_or_dispatching(
        workers, actors, service_database, rejection):
    gate = threading.Event()
    fixture = workers.root / "budget-original-read.txt"
    fixture.write_text("fictional old read")
    initial = tool_reply("read", {"file_path": str(fixture)}, call_id="budget-old-read")
    initial.release = gate
    marker = workers.provider.register(initial)
    accepted = accept(workers.api, actors["a"], marker)
    process, _ = workers.start()
    try:
        assert initial.arrived.wait(15)
        control(workers.api, actors["a"], accepted["runner_id"], "pause")
        gate.set()
        workers.assert_clean_exit(process)
    finally:
        gate.set()
    parked = runner(service_database, accepted["runner_id"])
    checkpoint = copy.deepcopy(decoded(parked["checkpoint"]))
    values = {"answer": "fictional rejected input"}
    if rejection == "budget":
        checkpoint["execution"]["iteration"] = checkpoint["execution"]["max_iterations"]
        expected = "CONTINUATION_LIMIT_REACHED"
    elif rejection == "unknown_effect":
        checkpoint["execution"]["tools"]["budget-old-read"]["phase"] = "dispatching"
        expected = "RECOVERY_TOOL_VERIFICATION_REQUIRED"
    else:
        foreign_id = uuid.uuid4().hex
        foreign = workers.storage / "tenants" / actors["b"].tenant_id / "uploads" / (foreign_id + ".txt")
        foreign.parent.mkdir(parents=True)
        foreign.write_text("fictional foreign attachment bytes")
        values["attachments"] = [{"file_id": foreign_id, "name": "foreign-fixture.txt", "type": "file", "mime_type": "text/plain"}]
        expected = "ATTACHMENT_NOT_FOUND"
    service_database.rows("UPDATE agent_runners SET checkpoint=%s::jsonb WHERE runner_id=%s", (json.dumps(checkpoint), accepted["runner_id"]))
    accepted_control, _ = control(workers.api, actors["a"], accepted["runner_id"], "resume", **values)
    retry, _ = workers.start()
    workers.assert_clean_exit(retry)
    after = runner(service_database, accepted["runner_id"])
    assert after["status"] == "paused" and after["attempt"] == parked["attempt"]
    assert decoded(after["checkpoint"]) == checkpoint
    stored = service_database.rows("SELECT status,error_code,consumed_attempt FROM agent_runner_controls WHERE control_id=%s", (accepted_control["control"]["control_id"],))[0]
    assert stored == {"status": "rejected", "error_code": expected, "consumed_attempt": None}
    assert len(workers.provider.requests(marker)) == 1
    assert service_database.rows("SELECT count(*) AS n FROM agent_runner_usage_receipts WHERE runner_id=%s", (accepted["runner_id"],)) == [{"n": 1}]


def test_saved_failed_root_interrupted_before_stage_resumes_only_original_terminal_fact(
        workers, actors, service_database):
    from src.services.agent_runner.execution_repository import ExecutionRepository
    marker = workers.provider.register(Reply(status=400))
    accepted = accept(workers.api, actors["a"], marker)
    report = workers.root / "failed-stage-proof.json"
    observed = workers.processes.start(["-m", "tests.integration.agent_runner_service.failed_stage_probe", "--once"],
        environment={**workers.environment, "RUNNER_TEST_FAILED_STAGE_REPORT": str(report)}, private_working_directory=True)
    workers.children.append((observed, "fixture-failed-stage-owner"))
    workers.assert_clean_exit(observed)
    assert json.loads(report.read_text()) == {"saved_outcome": "failed", "before_staging": True}
    ExecutionRepository(service_database.connect).reap_expired()
    interrupted = runner(service_database, accepted["runner_id"])
    assert interrupted["status"] == "interrupted"
    original = decoded(interrupted["checkpoint"])["execution"]
    assert original["outcome"] == "failed" and not original["pending"]
    prior_calls = len(workers.provider.requests(marker))
    prior_receipts = service_database.rows("SELECT receipt_id,phase FROM agent_runner_usage_receipts WHERE runner_id=%s ORDER BY receipt_id", (accepted["runner_id"],))
    control(workers.api, actors["a"], accepted["runner_id"], "resume")
    resumed, _ = workers.start()
    workers.assert_clean_exit(resumed)
    closed = terminal_with_diagnostics(workers, service_database, accepted["runner_id"])
    assert closed["status"] == "failed" and closed["attempt"] == interrupted["attempt"] + 1
    assert decoded(closed["checkpoint"])["execution"]["model_calls"] == original["model_calls"]
    assert len(workers.provider.requests(marker)) == prior_calls
    assert service_database.rows("SELECT receipt_id,phase FROM agent_runner_usage_receipts WHERE runner_id=%s ORDER BY receipt_id", (accepted["runner_id"],)) == prior_receipts
    assert len(service_database.rows("SELECT 1 FROM chat_records WHERE session_id=%s", (actors["a"].session_id,))) == 1
    assert not workers.provider.errors
