"""Durable business plans, upload scope and late settlement with actual workers."""
from decimal import Decimal
from pathlib import Path
import threading
import uuid

import pytest

from src.services.agent_runner.finalizer import RunnerFinalizer
from src.services.agent_runner.ownership import Attempt
from src.services.agent_runner.usage_repository import UsageRepository

from .provider import Reply, tool_reply
from .test_worker import workers, api_pair, prices, accept, decoded, runner, terminal

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("location", ["tenant", "legacy", "anonymous"])
@pytest.mark.parametrize("input_mode", ["file_id_only", "named"])
def test_old_file_id_resolves_scoped_disk_across_real_process_without_redis(workers, actors, service_database, location, input_mode):
    actor = actors["global"] if location == "anonymous" else actors["a"]
    tenant = actor.tenant_id or "_anonymous"
    directory = workers.storage / ("uploads" if location == "legacy" else "tenants") / tenant / "conversation"
    directory.mkdir(parents=True)
    file_id = "file_" + uuid.uuid4().hex[:12]
    source = directory / (file_id + ".txt")
    source.write_bytes(b"old-upload-owned-content")
    release = threading.Event()
    first_reply = Reply(release=release)
    marker = workers.provider.register(first_reply, Reply(content="old-upload-result"))
    attachment = {"file_id": file_id}
    if input_mode == "named":
        attachment["name"] = source.name
    accepted = accept(workers.api, actor, marker, attachments=[attachment])
    child, _ = workers.start()
    try:
        assert first_reply.arrived.wait(timeout=15)
        checkpoint = decoded(runner(service_database, accepted["runner_id"])["checkpoint"])
        workspace = Path(checkpoint["execution"]["resources"]["workspace"])
        assert (workspace / source.name).read_bytes() == b"old-upload-owned-content"
        first_reply.content = ""
        first_reply.tool_calls = tool_reply("read", {"file_path": str(workspace / source.name)}, call_id="old-file-read").tool_calls
        release.set()
        workers.assert_clean_exit(child)
    finally:
        release.set()
    finished = terminal(service_database, accepted["runner_id"])
    assert finished["status"] == "completed"
    context = workers.provider.requests(marker)[0]["messages"]
    assert any(file_id + ".txt" in str(message.get("content")) for message in context)
    tool_results = [message for message in workers.provider.requests(marker)[1]["messages"] if message.get("role") == "tool"]
    assert any("old-upload-owned-content" in message["content"] for message in tool_results)
    assert not workspace.exists(), "Committed terminal runner must reclaim its own attachment workspace"
    assert source.read_bytes() == b"old-upload-owned-content", "Original upload is not execution scratch space"


@pytest.mark.parametrize("location", ["foreign", "outside", "symlink"])
def test_file_id_cannot_escape_current_tenant_roots_in_actual_worker(workers, actors, service_database, location):
    file_id = "file_" + uuid.uuid4().hex[:12]
    directory = (workers.storage / "tenants" / actors["b"].tenant_id / "conversation"
                 if location != "outside" else workers.root / "outside-uploads")
    directory.mkdir(parents=True)
    source = directory / (file_id + ".txt")
    source.write_text("foreign-fixture-content-must-not-enter-prompt")
    if location == "symlink":
        own = workers.storage / "tenants" / actors["a"].tenant_id / "conversation"
        own.mkdir(parents=True)
        (own / source.name).symlink_to(source)
    marker = workers.provider.register(Reply(content="must-not-dispatch"))
    accepted = accept(workers.api, actors["a"], marker, attachments=[{"file_id": file_id}])
    child, _ = workers.start()
    workers.assert_clean_exit(child)
    finished = terminal(service_database, accepted["runner_id"])
    assert finished["status"] == "failed"
    assert workers.provider.requests(marker) == []
    assert service_database.rows("SELECT 1 FROM agent_runner_usage_receipts WHERE runner_id=%s", (accepted["runner_id"],)) == []
    assert source.read_text() == "foreign-fixture-content-must-not-enter-prompt"


def test_two_continuous_runners_restore_business_plan_from_pg_and_advance_only_current_task(workers, actors, service_database):
    fixture = workers.root / "plan-input.txt"
    fixture.write_text("plan-read-result")
    marker = workers.provider.register(tool_reply("create_plan", {"goal": "durable plan", "steps": [
        {"description": "read fixture", "tool": "read", "parameters": {"file_path": str(fixture)}},
        {"description": "read again", "tool": "read", "parameters": {"file_path": str(fixture)}}]}, call_id="plan-create"),
        Reply(content="plan-created-for-next-turn"))
    first = accept(workers.api, actors["a"], marker)
    child, _ = workers.start()
    workers.assert_clean_exit(child)
    first_plan = decoded(terminal(service_database, first["runner_id"])["checkpoint"])["business_plan"]
    assert [task["status"] for task in first_plan["tasks"]] == ["pending", "pending"]
    second_marker = workers.provider.register(tool_reply("read", {"file_path": str(fixture)}, call_id="plan-read"),
                                               Reply(content="first-task-done"))
    second = accept(workers.api, actors["a"], second_marker)
    second_child, _ = workers.start()
    workers.assert_clean_exit(second_child)
    second_plan = decoded(terminal(service_database, second["runner_id"])["checkpoint"])["business_plan"]
    assert second_plan["plan_id"] == first_plan["plan_id"]
    assert [task["status"] for task in second_plan["tasks"]] == ["completed", "pending"]
    assert decoded(runner(service_database, first["runner_id"])["checkpoint"])["business_plan"] == first_plan
    assert len(workers.provider.requests(marker)) == len(workers.provider.requests(second_marker)) == 2


def test_late_actual_call_usage_reconciles_original_record_delta_once_without_reexecuting_worker(workers, actors, service_database):
    marker = workers.provider.register(Reply(content="pending-fee-result", reported_usage=None))
    accepted = accept(workers.api, actors["a"], marker)
    child, identifier = workers.start()
    workers.assert_clean_exit(child)
    before = terminal(service_database, accepted["runner_id"])
    assert before["settlement_status"] == "pending"
    receipt = service_database.rows("SELECT * FROM agent_runner_usage_receipts WHERE runner_id=%s", (accepted["runner_id"],))[0]
    assert receipt["phase"] == "unknown"
    # Reconciliation supplies a late external billing fact for the original
    # actual dispatched call. It neither re-runs the model nor grants execution.
    usage = UsageRepository(service_database.connect)
    authority = Attempt(accepted["runner_id"], identifier, receipt["authorized_attempt"])
    usage.observe(authority, receipt["receipt_id"], {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
                  provider=receipt["provider"], model=receipt["model"], request_id="fixture-late-original-call")
    finalizer = RunnerFinalizer(service_database.connect)
    after = finalizer.settle_pending_usage(accepted["runner_id"])
    repeated = finalizer.settle_pending_usage(accepted["runner_id"])
    assert after["record_id"] == repeated["record_id"] == before["record_id"]
    assert after["status"] == repeated["status"] == "completed"
    assert after["settlement_status"] == repeated["settlement_status"] == "settled"
    records = service_database.rows("SELECT credit_cost FROM chat_records WHERE session_id=%s", (actors["a"].session_id,))
    assert records == [{"credit_cost": Decimal("0.01")}]
    assert service_database.rows("SELECT credit_balance FROM tenants WHERE tenant_id=%s", (actors["a"].tenant_id,))[0]["credit_balance"] == Decimal("999.99")
    assert len(workers.provider.requests(marker)) == 1
    assert len(service_database.rows("SELECT 1 FROM chat_messages WHERE session_id=%s", (actors["a"].session_id,))) == 2
