"""Actual API/CLI worker/Engine/Gateway/finalize acceptance with local model IO."""
import base64
from decimal import Decimal
import json
import re
from pathlib import Path
import secrets
import shutil
import socket
import threading
import uuid

import bcrypt
import httpx
from psycopg2 import sql
import pytest

from .conftest import wait_for
from .provider import Reply, tool_reply
from .test_api import ApiPair, api_pair, body, require_status
from .test_usage_storage import prices

pytestmark = pytest.mark.integration


def decoded(value):
    return json.loads(value) if isinstance(value, str) else value


class Workers:
    def __init__(self, processes, api, provider, models):
        self.processes, self.api, self.provider, self.models = processes, api, provider, models
        self.root = processes.root / ("worker-resources-" + uuid.uuid4().hex)
        self.root.mkdir()
        self.storage = self.root / "uploads"
        self.storage.mkdir()
        self.children = []
        self.environment = {**provider.environment, "QWEN_MODEL_CODE": models[0],
            "AGENT_RUNNER_ENABLED": "true", "AGENT_RUNNER_SERVICE_ID": api.service_id,
            "AGENT_RUNNER_SERVICE_TOKEN_HASH": bcrypt.hashpw(api._service_token.encode(), bcrypt.gensalt(rounds=4)).decode(),
            "AGENT_RUNNER_SERVICE_SOURCES": "chat,wecom_kf,feishu,dingtalk",
            "AGENT_RUNNER_RESOURCE_DIR": str(self.root), "AGENT_RUNNER_STORAGE_ROOT": str(self.storage),
            "REDIS_ENABLED": "false"}

    def start(self, *, once=True, maximum=None):
        identifier = "worker-test-" + uuid.uuid4().hex
        arguments = ["-m", "src.services.agent_runner.worker", "--worker-id", identifier]
        if once:
            arguments.append("--once")
        if maximum is not None:
            arguments.extend(["--max-tasks", str(maximum)])
        child = self.processes.start(arguments, environment=self.environment, private_working_directory=True)
        self.children.append((child, identifier))
        return child, identifier

    def assert_clean_exit(self, child):
        code = child.wait(timeout=25)
        if code != 0:
            log = (self.processes.root / f"child-{self.processes.children.index(child)}.log").read_text()
            locations = re.findall(r'File "([^"]+)", line (\d+)', log)
            classes = re.findall(r'^([A-Za-z_.]+(?:Error|Exception|Failure|Violation))(?::|$)', log, re.MULTILINE)
            # Configuration validation messages can contain secrets. Retain only
            # stack locations/classes; never echo the complete private log.
            diagnosis = {"exit":code,"classes":classes,"locations":locations[-12:],
                         "codes": sorted(set(re.findall(r"\b(?:RUNNER|RECOVERY|CHECKPOINT|FINALIZE)_[A-Z0-9_]+\b", log)))}
            # This path also exists in the Linux test container; host-only
            # /private/tmp would mask the actual subprocess diagnosis.
            (self.processes.root / ('worker-failure-'+uuid.uuid4().hex+'.json')).write_text(json.dumps(diagnosis))
            raise AssertionError(f"CLI worker failed; sanitized diagnosis={diagnosis}")

    def close(self):
        for child, _ in reversed(self.children):
            self.processes.stop(child)
        shutil.rmtree(self.root)


@pytest.fixture
def workers(service_processes, api_pair, provider_peer, prices):
    fleet = Workers(service_processes, api_pair, provider_peer, prices)
    # Same resolved modules/configuration as the actual worker, verified in a
    # separate fresh child; never pre-initialize its business pool or replace its
    # bootstrap, authorizer, runtime, Engine, Gateway or repository.
    script = """
import runpy, sys
verify = runpy.run_path(sys.argv[1])["verify_resource_targets"]
verify(database_name=sys.argv[2], provider_port=int(sys.argv[3]))
"""
    from urllib.parse import urlsplit
    probe = service_processes.start(["-c", script, str(Path(__file__).with_name("safety.py")),
                                    service_processes.database.name,
                                    str(urlsplit(provider_peer.environment["QWEN_BASE_URL"]).port)],
                                    environment=fleet.environment, private_working_directory=True)
    try:
        assert probe.wait(timeout=15) == 0, "Actual effective resource isolation preflight failed"
        yield fleet
    finally:
        service_processes.stop(probe)
        fleet.close()


def accept(api, actor, marker, **extra):
    return require_status(api.call("POST", "/v1/runners", actor=actor,
                                   json=body(actor, text=marker, **extra)), 202)["runner"]


def runner(database, identifier):
    return database.rows("SELECT * FROM agent_runners WHERE runner_id=%s", (identifier,))[0]


def terminal(database, identifier, *, timeout=25):
    return wait_for(lambda: (row if (row := runner(database, identifier))["status"] in
                            {"completed", "failed", "cancelled"} else None), timeout=timeout)


def assert_one_committed_completion(database, actor, identifier, output):
    row = runner(database, identifier)
    assert row["status"] == "completed" and row["settlement_status"] == "settled"
    assert decoded(row["result"])["output"] == output
    receipts = database.rows("SELECT * FROM agent_runner_usage_receipts WHERE runner_id=%s", (identifier,))
    assert len(receipts) == 1 and receipts[0]["phase"] == "observed" and receipts[0]["applied"]
    assert receipts[0]["record_id"] == row["record_id"]
    records = database.rows("SELECT * FROM chat_records WHERE session_id=%s", (actor.session_id,))
    assert len(records) == 1 and records[0]["record_id"] == row["record_id"]
    assert records[0]["prompt_tokens"] == 11 and records[0]["completion_tokens"] == 7
    assert records[0]["total_token_count"] == 18 and records[0]["credit_cost"] == Decimal("0.01")
    balance = database.rows("SELECT credit_balance FROM tenants WHERE tenant_id=%s", (actor.tenant_id,))[0]["credit_balance"]
    assert balance == Decimal("999.99")
    messages = database.rows("SELECT role,content,metadata FROM chat_messages WHERE session_id=%s ORDER BY created_at", (actor.session_id,))
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[-1]["content"] == output
    assert all(decoded(message["metadata"])["runner_id"] == identifier for message in messages)
    assert database.rows("SELECT 1 FROM agent_runner_session_claims WHERE owner_runner_id=%s", (identifier,)) == []


def test_actual_cli_worker_commits_one_message_record_receipt_and_debit(workers, actors, service_database):
    output = "actual-worker-final-result"
    marker = workers.provider.register(Reply(content=output))
    accepted = accept(workers.api, actors["a"], marker)
    child, _ = workers.start()
    finished = terminal(service_database, accepted["runner_id"])
    assert finished["status"] == "completed"
    workers.assert_clean_exit(child)
    assert_one_committed_completion(service_database, actors["a"], accepted["runner_id"], output)
    assert len(workers.provider.requests(marker)) == 1 and not workers.provider.errors
    repeat, _ = workers.start()
    workers.assert_clean_exit(repeat)
    assert len(workers.provider.requests(marker)) == 1
    assert_one_committed_completion(service_database, actors["a"], accepted["runner_id"], output)


def test_closed_observer_connection_and_stopped_accepting_api_do_not_stop_worker(workers, actors, service_database):
    release = threading.Event()
    reply = Reply(content="independent-after-page-close", release=release)
    marker = workers.provider.register(reply)
    # Own a separate accepting API, so terminating it cannot poison the shared
    # module fixture used by later cases. The existing second API reads the same DB.
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
    accepting = workers.processes.start(
        ["-m", "src.services.agent_runner.bootstrap", "--host", "127.0.0.1", "--port", str(port)],
        environment=workers.environment, private_working_directory=True)
    url = f"http://127.0.0.1:{port}"
    def ready():
        assert accepting.poll() is None, "Owned accepting API exited during startup"
        try:
            return httpx.get(url + "/health", timeout=0.5).status_code == 200
        except httpx.TransportError:
            return False
    wait_for(ready, timeout=15)
    transient_api = ApiPair([url, workers.api.urls[1]], workers.api.service_id,
                            workers.api._service_token, workers.processes, [accepting])
    accepted = accept(transient_api, actors["a"], marker)
    child, _ = workers.start()
    try:
        assert reply.arrived.wait(timeout=15), "Actual worker did not reach the local model"
        # Open and close the observing request without consuming its body. The
        # accepting API is also terminated; the other actual API remains to poll.
        with httpx.stream("GET", url + "/v1/runners/" + accepted["runner_id"],
                          headers=workers.api.headers(actors["a"]), timeout=5) as response:
            assert response.status_code == 200
        workers.processes.stop(accepting)
        assert child.poll() is None
        assert service_database.rows("SELECT 1 FROM chat_records WHERE session_id=%s", (actors["a"].session_id,)) == []
        release.set()
        terminal(service_database, accepted["runner_id"])
        workers.assert_clean_exit(child)
        fetched = require_status(workers.api.call("GET", "/v1/runners/" + accepted["runner_id"],
                                                 actor=actors["a"], api=1), 200)
        assert fetched["runner"]["status"] == "completed"
        assert_one_committed_completion(service_database, actors["a"], accepted["runner_id"], reply.content)
    finally:
        release.set()
        workers.processes.stop(accepting)


def test_two_real_workers_run_different_sessions_and_preserve_same_session_fifo(workers, actors, service_database):
    release_a, release_b = threading.Event(), threading.Event()
    first_reply = Reply(content="first-fifo-result", release=release_a)
    other_reply = Reply(content="other-session-result", release=release_b)
    first_marker = workers.provider.register(first_reply)
    second_reply = Reply(content="second-fifo-result")
    second_marker = workers.provider.register(second_reply)
    other_marker = workers.provider.register(other_reply)
    first = accept(workers.api, actors["a"], first_marker)
    second = accept(workers.api, actors["a"], second_marker)
    unrelated = accept(workers.api, actors["b"], other_marker)
    worker_a, _ = workers.start()
    worker_b, _ = workers.start()
    try:
        assert first_reply.arrived.wait(timeout=15) and other_reply.arrived.wait(timeout=15)
        assert not second_reply.arrived.is_set()
        assert runner(service_database, second["runner_id"])["status"] == "queued"
        assert runner(service_database, first["runner_id"])["status"] == "running"
        assert runner(service_database, unrelated["runner_id"])["status"] == "running"
        release_a.set()
        release_b.set()
        workers.assert_clean_exit(worker_a)
        workers.assert_clean_exit(worker_b)
        assert runner(service_database, second["runner_id"])["status"] == "queued"
        worker_second, _ = workers.start()
        workers.assert_clean_exit(worker_second)
        assert terminal(service_database, second["runner_id"])["status"] == "completed"
        assert len(workers.provider.requests(first_marker)) == len(workers.provider.requests(second_marker)) == len(workers.provider.requests(other_marker)) == 1
        messages = service_database.rows("SELECT content FROM chat_messages WHERE session_id=%s ORDER BY created_at", (actors["a"].session_id,))
        assert [row["content"] for row in messages] == [first_marker, first_reply.content, second_marker, second_reply.content]
    finally:
        release_a.set()
        release_b.set()


def test_late_terminal_db_fault_rolls_back_history_record_debit_receipts_and_claim_release(workers, actors, service_database):
    marker = workers.provider.register(Reply(content="original-completed-intent"))
    accepted = accept(workers.api, actors["a"], marker)
    constraint = "runner_test_terminal_fault_" + uuid.uuid4().hex
    with service_database.connect() as connection, connection.cursor() as cursor:
        cursor.execute(sql.SQL("ALTER TABLE agent_runners ADD CONSTRAINT {} CHECK (runner_id <> {} OR status NOT IN ('completed','failed','cancelled'))").format(
            sql.Identifier(constraint), sql.Literal(accepted["runner_id"])))
    try:
        child, _ = workers.start()
        workers.assert_clean_exit(child)
        failed_commit = runner(service_database, accepted["runner_id"])
        assert failed_commit["status"] == "finalizing" and failed_commit["result"] is None
        assert decoded(failed_commit["checkpoint"])["pending_finalization"]["status"] == "completed"
        assert service_database.rows("SELECT 1 FROM chat_messages WHERE session_id=%s", (actors["a"].session_id,)) == []
        assert service_database.rows("SELECT 1 FROM chat_records WHERE session_id=%s", (actors["a"].session_id,)) == []
        assert service_database.rows("SELECT credit_balance FROM tenants WHERE tenant_id=%s", (actors["a"].tenant_id,))[0]["credit_balance"] == Decimal("1000")
        receipts = service_database.rows("SELECT phase,applied,record_id FROM agent_runner_usage_receipts WHERE runner_id=%s", (accepted["runner_id"],))
        assert receipts == [{"phase": "observed", "applied": False, "record_id": None}]
        assert service_database.rows("SELECT owner_runner_id FROM agent_runner_session_claims WHERE owner_runner_id=%s", (accepted["runner_id"],)) == [{"owner_runner_id": accepted["runner_id"]}]
    finally:
        with service_database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql.SQL("ALTER TABLE agent_runners DROP CONSTRAINT IF EXISTS {}").format(sql.Identifier(constraint)))
    # Model execution is finished. Lease expiry is injected as a recovery clock
    # boundary; the new real CLI must only retry the original transaction.
    service_database.rows("UPDATE agent_runners SET lease_until=clock_timestamp()-INTERVAL '1 second' WHERE runner_id=%s", (accepted["runner_id"],))
    retry, _ = workers.start()
    workers.assert_clean_exit(retry)
    assert len(workers.provider.requests(marker)) == 1
    assert_one_committed_completion(service_database, actors["a"], accepted["runner_id"], "original-completed-intent")


@pytest.mark.parametrize("reported", [{"total_tokens": 18}, {"prompt_tokens": 11}, {}, None])
def test_real_provider_partial_usage_stays_unknown_and_financially_pending(workers, actors, service_database, reported):
    marker = workers.provider.register(Reply(content="usage-fields-missing", reported_usage=reported))
    accepted = accept(workers.api, actors["a"], marker)
    child, _ = workers.start()
    workers.assert_clean_exit(child)
    finished = runner(service_database, accepted["runner_id"])
    assert finished["status"] == "completed" and finished["settlement_status"] == "pending"
    receipt = service_database.rows("SELECT phase,usage,applied FROM agent_runner_usage_receipts WHERE runner_id=%s", (accepted["runner_id"],))
    assert receipt == [{"phase": "unknown", "usage": None, "applied": False}]
    assert len(workers.provider.requests(marker)) == 1


def test_known_zero_usage_is_distinct_from_missing_usage(workers, actors, service_database):
    marker = workers.provider.register(Reply(content="known-zero", reported_usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}))
    accepted = accept(workers.api, actors["a"], marker)
    child, _ = workers.start()
    workers.assert_clean_exit(child)
    finished = runner(service_database, accepted["runner_id"])
    assert finished["status"] == "completed" and finished["settlement_status"] == "settled"
    receipt = service_database.rows("SELECT phase,applied,usage FROM agent_runner_usage_receipts WHERE runner_id=%s", (accepted["runner_id"],))[0]
    assert receipt["phase"] == "observed" and receipt["applied"]
    assert receipt["usage"]["prompt_tokens"] == receipt["usage"]["completion_tokens"] == 0


def test_sigterm_preserves_committed_attachment_workspace_and_is_not_user_cancel(workers, actors, service_database):
    release = threading.Event()
    reply = Reply(content="should-not-finalize-during-stop", release=release)
    marker = workers.provider.register(reply)
    accepted = accept(workers.api, actors["a"], marker,
                      attachments=[{"name": "fixture.txt", "content": base64.b64encode(b"workspace-file-remains").decode()}])
    child, _ = workers.start()
    try:
        assert reply.arrived.wait(timeout=15)
        checkpoint = decoded(runner(service_database, accepted["runner_id"])["checkpoint"])
        workspace = Path(checkpoint["execution"]["resources"]["workspace"])
        assert (workspace / "fixture.txt").read_bytes() == b"workspace-file-remains"
        workers.processes.stop(child)
        stopped = runner(service_database, accepted["runner_id"])
        assert stopped["status"] == "interrupted" and not stopped["cancel_requested"]
        assert decoded(stopped["checkpoint"])["execution"]["resources"]["workspace"] == str(workspace)
        assert (workspace / "fixture.txt").read_bytes() == b"workspace-file-remains"
        assert service_database.rows("SELECT 1 FROM chat_records WHERE session_id=%s", (actors["a"].session_id,)) == []
        assert service_database.rows("SELECT owner_runner_id FROM agent_runner_session_claims WHERE owner_runner_id=%s", (accepted["runner_id"],))
    finally:
        release.set()


def test_actual_tool_pair_and_every_provider_call_are_saved_once(workers, actors, service_database):
    fixture = workers.root / "tool-input.txt"
    fixture.write_text("actual-read-tool-file")
    call_id = "fixture-read-call"
    marker = workers.provider.register(tool_reply("read", {"file_path": str(fixture)}, call_id=call_id), Reply(content="tool-read-complete"))
    accepted = accept(workers.api, actors["a"], marker)
    child, _ = workers.start()
    workers.assert_clean_exit(child)
    assert terminal(service_database, accepted["runner_id"])["status"] == "completed"
    assert len(workers.provider.requests(marker)) == 2
    receipts = service_database.rows("SELECT phase,applied FROM agent_runner_usage_receipts WHERE runner_id=%s", (accepted["runner_id"],))
    assert len(receipts) == 2 and all(receipt["phase"] == "observed" and receipt["applied"] for receipt in receipts)
    second_context = workers.provider.requests(marker)[1]["messages"]
    tools = [message for message in second_context if message.get("role") == "tool"]
    assert any(message["tool_call_id"] == call_id and "actual-read-tool-file" in message["content"] for message in tools)
    stored = service_database.rows("SELECT role,metadata FROM chat_messages WHERE session_id=%s ORDER BY created_at", (actors["a"].session_id,))
    assistant_calls = [call["id"] for message in stored for call in decoded(message["metadata"]).get("tool_calls", [])]
    tool_ids = [decoded(message["metadata"])["tool_call_id"] for message in stored if message["role"] == "tool"]
    assert assistant_calls == tool_ids == [call_id]
    record = service_database.rows("SELECT agent_iterations,prompt_tokens,completion_tokens,credit_cost FROM chat_records WHERE session_id=%s", (actors["a"].session_id,))[0]
    assert record["agent_iterations"] == 2 and record["prompt_tokens"] == 22 and record["completion_tokens"] == 14
    assert record["credit_cost"] == Decimal("0.01")
