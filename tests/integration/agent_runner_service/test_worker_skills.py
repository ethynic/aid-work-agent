"""Actual trusted skill child composition roots and provider receipt ownership."""
from decimal import Decimal
from pathlib import Path
import shlex
import stat
import threading
import uuid

from psycopg2 import sql
import pytest

from .provider import Reply, tool_reply
from .test_worker import workers, api_pair, prices, accept, decoded, runner, terminal

pytestmark = pytest.mark.integration


def skill_script(workers, marker, producer):
    repository = Path(__file__).resolve().parents[3]
    script = workers.root / ("skill-provider-" + uuid.uuid4().hex + ".py")
    if producer == "followup":
        source = repository / "src/skills/followup-tracking-1.0.0/scripts/followup_manager.py"
        script.write_text("import os, runpy\n"
            + "evaluate = runpy.run_path(" + repr(str(source)) + ")[\"_evaluate_with_llm\"]\n"
            + "score, feedback = evaluate({\"content\":" + repr(marker)
            + ",\"tenant_id\":os.environ['AID_TENANT_ID'],\"user_id\":os.environ['AID_USER_ID']})\n"
            + "print('child-evaluated', score)\n")
    else:
        pipeline = repository / "src/skills/excel-to-template-1.0.0/scripts/pipeline.py"
        script.write_text("import runpy\nfrom src.tools.excel.excel_template_ai import _default_llm\n"
            + "content, usage = _default_llm(" + repr(marker) + ", return_usage=True)\n"
            + "metering = runpy.run_path(" + repr(str(pipeline)) + ")[\"Metering\"](record_usage=True)\n"
            + "metering.on_usage(usage, 'fixture_extract')\nprint('child-evaluated', content)\n")
    return script


def script_conversation(workers, producer, *, child_reply):
    first = tool_reply("use_skill", {"skill": "excel-to-template"}, call_id="fixture-load-skill")
    second = Reply()
    last = Reply(content="parent-after-skill-result")
    marker = workers.provider.register(first, second, child_reply, last)
    script = skill_script(workers, marker, producer)
    # A normal shell masks the Python exit. Real guard/owned-group handling must
    # still detect provider storage failures even when a business helper catches.
    second.content = ""
    second.tool_calls = tool_reply("skill_execute", {"skill": "excel-to-template",
        "command": "python " + shlex.quote(str(script)) + "; true"}, call_id="fixture-execute-skill").tool_calls
    return marker


@pytest.mark.parametrize("producer", ["followup", "excel"])
def test_actual_gateway_or_excel_sdk_child_has_one_skill_receipt_and_no_background_double_bill(workers, actors, service_database, producer):
    marker = script_conversation(workers, producer, child_reply=Reply(content='{"score":8,"feedback":"fixture-success"}'))
    accepted = accept(workers.api, actors["a"], marker)
    child, _ = workers.start()
    workers.assert_clean_exit(child)
    finished = terminal(service_database, accepted["runner_id"])
    assert finished["status"] == "completed" and finished["settlement_status"] == "settled"
    receipts = service_database.rows("SELECT phase,applied,billing_boundary,tool_call_id FROM agent_runner_usage_receipts WHERE runner_id=%s", (accepted["runner_id"],))
    assert len(receipts) == 4 and all(row["phase"] == "observed" and row["applied"] for row in receipts)
    skill = [row for row in receipts if row["billing_boundary"].startswith("skill-call:")]
    assert len(skill) == 1 and skill[0]["tool_call_id"] == "fixture-execute-skill"
    assert service_database.rows("SELECT credit_cost FROM chat_records WHERE user_id=%s", (actors["a"].user_id,)) == [{"credit_cost": Decimal("0.02")}]
    assert service_database.rows("SELECT credit_balance FROM tenants WHERE tenant_id=%s", (actors["a"].tenant_id,))[0]["credit_balance"] == Decimal("999.98")
    assert len(workers.provider.requests(marker)) == 4 and not workers.provider.errors


@pytest.mark.parametrize("reported", [None, {"total_tokens":18}])
def test_actual_excel_sdk_missing_usage_and_real_metering_callback_preserve_known_result_unknown_finance(workers, actors, service_database, reported):
    marker = script_conversation(workers, "excel", child_reply=Reply(content="sdk-known-result", reported_usage=reported))
    accepted = accept(workers.api, actors["a"], marker)
    child, _ = workers.start()
    workers.assert_clean_exit(child)
    finished = terminal(service_database, accepted["runner_id"])
    assert finished["status"] == "completed" and finished["settlement_status"] == "pending"
    receipts = service_database.rows("SELECT phase,applied,billing_boundary,usage FROM agent_runner_usage_receipts WHERE runner_id=%s", (accepted["runner_id"],))
    skill = [row for row in receipts if row["billing_boundary"].startswith("skill-call:")]
    assert len(receipts) == 4 and len(skill) == 1
    assert skill[0]["phase"] == "unknown" and skill[0]["usage"] is None and not skill[0]["applied"]
    assert service_database.rows("SELECT credit_cost FROM chat_records WHERE user_id=%s", (actors["a"].user_id,)) == [{"credit_cost": Decimal("0.01")}]
    assert len(workers.provider.requests(marker)) == 4


@pytest.mark.parametrize("marker_failure", [False, True, "full"])
def test_caught_child_usage_storage_failure_cannot_be_hidden_by_shell_or_concurrent_cancel(workers, actors, service_database, marker_failure):
    release = threading.Event()
    child_reply = Reply(content='{"score":8,"feedback":"cannot-complete-after-storage-fault"}', release=release)
    marker = script_conversation(workers, "followup", child_reply=child_reply)
    accepted = accept(workers.api, actors["a"], marker)
    constraint = "runner_test_child_usage_fault_" + uuid.uuid4().hex
    with service_database.connect() as connection, connection.cursor() as cursor:
        cursor.execute(sql.SQL("ALTER TABLE agent_runner_usage_receipts ADD CONSTRAINT {} CHECK (runner_id <> {} OR billing_boundary='main' OR phase <> 'observed')").format(
            sql.Identifier(constraint), sql.Literal(accepted["runner_id"])))
    child, _ = workers.start()
    try:
        assert child_reply.arrived.wait(timeout=20), "Actual registered skill child did not reach provider gate"
        if marker_failure:
            guards = list((workers.root / "guards").glob(accepted["runner_id"] + ".*.failure"))
            assert len(guards) == 1 and guards[0].is_file()
            guards[0].unlink()
            if marker_failure == "full":
                device = Path("/dev/full")
                assert stat.S_ISCHR(device.stat().st_mode) and device.stat().st_size == 0
                guards[0].symlink_to(device)
                assert guards[0].stat().st_size == 0, "Parent marker stat must not itself imply a failure"
            else:
                guards[0].mkdir()  # Guaranteed write failure even under privileged test UID.
            response = workers.api.call("POST", "/v1/runners/" + accepted["runner_id"] + "/cancel", actor=actors["a"])
            assert response.status_code == 200
        release.set()
        child.wait(timeout=20)
        stopped = runner(service_database, accepted["runner_id"])
        assert stopped["status"] == "interrupted", "Authority failure cannot be ordinary success/user-cancel completion"
        assert stopped["finished_at"] is None
        assert len(workers.provider.requests(marker)) == 3, "No parent provider call may follow child authority failure"
        assert service_database.rows("SELECT 1 FROM chat_records WHERE user_id=%s", (actors["a"].user_id,)) == []
        assert service_database.rows("SELECT 1 FROM agent_runner_session_claims WHERE owner_runner_id=%s", (accepted["runner_id"],))
        receipts = service_database.rows("SELECT phase,applied FROM agent_runner_usage_receipts WHERE runner_id=%s", (accepted["runner_id"],))
        assert len(receipts) == 3 and not any(row["applied"] for row in receipts)
    finally:
        release.set()
        workers.processes.stop(child)
        with service_database.connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql.SQL("ALTER TABLE agent_runner_usage_receipts DROP CONSTRAINT IF EXISTS {}").format(sql.Identifier(constraint)))
