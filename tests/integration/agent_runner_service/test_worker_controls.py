"""Actual worker child permissions, user cancellation, parked and killed attempts."""
import base64
import asyncio
import json
import shlex
from decimal import Decimal
from pathlib import Path
import threading
import uuid

import pytest

from .conftest import wait_for
from .provider import Reply, tool_reply
from .test_api import require_status
from .test_worker import workers, api_pair, prices, accept, decoded, runner, terminal
from .test_web_gateway import gateways

pytestmark = pytest.mark.integration


@pytest.fixture
def child_profile(actors, service_database):
    profile = "customer-followup"
    subscription = "runner-child-subscription-" + uuid.uuid4().hex
    service_database.rows("INSERT INTO subscriptions(subscription_id,tenant_id,subagent_type,status,payment_status) VALUES (%s,%s,%s,'active','paid')",
                          (subscription, actors["a"].tenant_id, profile))
    service_database.rows("INSERT INTO user_agent_permissions(user_id,tenant_id,agent_id) VALUES (%s,%s,%s)",
                          (actors["a"].user_id, actors["a"].tenant_id, profile))
    try:
        yield profile, subscription
    finally:
        service_database.rows("DELETE FROM subscriptions WHERE subscription_id=%s", (subscription,))
        service_database.rows("DELETE FROM user_agent_permissions WHERE user_id=%s AND tenant_id=%s AND agent_id=%s",
                              (actors["a"].user_id, actors["a"].tenant_id, profile))


def delegate_conversation(workers, profile, child_reply, parent_reply=None):
    first = Reply()
    replies = [first, child_reply]
    if parent_reply:
        replies.append(parent_reply)
    marker = workers.provider.register(*replies)
    first.content = ""
    first.tool_calls = tool_reply("delegate_to_subagent", {"subagent_name": profile, "task_description": marker},
                                  call_id="actual-child-delegate").tool_calls
    return marker


def test_actual_child_engine_checkpoint_and_usage_share_parent_record_without_double_iterations(workers, actors, service_database, child_profile):
    marker = delegate_conversation(workers, child_profile[0], Reply(content="actual-child-result"), Reply(content="actual-parent-result"))
    accepted = accept(workers.api, actors["a"], marker)
    child, _ = workers.start()
    workers.assert_clean_exit(child)
    finished = terminal(service_database, accepted["runner_id"])
    assert finished["status"] == "completed"
    checkpoint = decoded(finished["checkpoint"])["execution"]
    owned_child = checkpoint["children"]["actual-child-delegate"]
    assert owned_child["checkpoint"]["profile_id"] == child_profile[0]
    assert owned_child["checkpoint"]["outcome"] == "completed"
    identity = owned_child["checkpoint"]["identity"]
    assert identity["tenant_id"] == actors["a"].tenant_id and identity["user_id"] == actors["a"].user_id
    receipts = service_database.rows("SELECT execution_id,phase,applied FROM agent_runner_usage_receipts WHERE runner_id=%s", (accepted["runner_id"],))
    assert len(receipts) == 3 and all(row["phase"] == "observed" and row["applied"] for row in receipts)
    assert {row["execution_id"] for row in receipts} == {accepted["runner_id"], owned_child["execution_id"]}
    records = service_database.rows("SELECT agent_iterations,prompt_tokens,completion_tokens,credit_cost FROM chat_records WHERE user_id=%s", (actors["a"].user_id,))
    assert records == [{"agent_iterations": 2, "prompt_tokens": 33, "completion_tokens": 21, "credit_cost": Decimal("0.01")}]
    assert len(workers.provider.requests(marker)) == 3


def test_child_subscription_revoked_mid_call_prevents_next_child_side_effect_without_revoking_main(workers, actors, service_database, child_profile):
    destination = workers.root / "must-not-write-after-child-revoke.txt"
    release = threading.Event()
    child_reply = tool_reply("write", {"file_path": str(destination), "content": "forbidden-side-effect"}, call_id="child-write-after-revoke")
    child_reply.release = release
    marker = delegate_conversation(workers, child_profile[0], child_reply, Reply(content="child-permission-rejected-main-can-explain"))
    accepted = accept(workers.api, actors["a"], marker)
    child, _ = workers.start()
    try:
        assert child_reply.arrived.wait(timeout=20)
        service_database.rows("UPDATE subscriptions SET status='cancelled' WHERE subscription_id=%s", (child_profile[1],))
        release.set()
        child.wait(timeout=20)
        stopped = runner(service_database, accepted["runner_id"])
        assert stopped["status"] == "completed"
        assert decoded(stopped["result"])["output"] == "child-permission-rejected-main-can-explain"
        assert not destination.exists()
        assert len(workers.provider.requests(marker)) == 3
        owned_child = decoded(stopped["checkpoint"])["execution"]["children"]["actual-child-delegate"]
        receipts = service_database.rows("SELECT execution_id FROM agent_runner_usage_receipts WHERE runner_id=%s", (accepted["runner_id"],))
        assert len(receipts) == 3
        assert sum(row["execution_id"] == owned_child["execution_id"] for row in receipts) == 1
        parent_context = workers.provider.requests(marker)[2]["messages"]
        assert any(message.get("role") == "tool" and "PROFILE_FORBIDDEN" in message["content"] for message in parent_context)
        # Main is independently allowed to accept another runner. This proves
        # rejection is for the actual child profile rather than the parent route.
        next_marker = workers.provider.register(Reply(content="main-still-allowed"))
        assert accept(workers.api, actors["a"], next_marker)["status"] == "queued"
    finally:
        release.set()


def test_parked_child_cancel_uses_new_worker_only_for_finalization_without_reentering_engine(workers, actors, service_database, child_profile):
    marker = delegate_conversation(workers, child_profile[0], tool_reply("clarify", {"question": "fixture-question", "missing_info": ["fixture-field"]}, call_id="child-clarify"))
    accepted = accept(workers.api, actors["a"], marker)
    child, _ = workers.start()
    workers.assert_clean_exit(child)
    parked = runner(service_database, accepted["runner_id"])
    assert parked["status"] == "waiting" and parked["finished_at"] is None
    assert len(workers.provider.requests(marker)) == 2
    response = workers.api.call("POST", "/v1/runners/" + accepted["runner_id"] + "/cancel", actor=actors["a"])
    assert response.status_code == 200
    sweeper, _ = workers.start()
    workers.assert_clean_exit(sweeper)
    cancelled = terminal(service_database, accepted["runner_id"])
    assert cancelled["status"] == "cancelled" and cancelled["cancel_requested"]
    assert len(workers.provider.requests(marker)) == 2 and not workers.provider.errors
    assert service_database.rows("SELECT 1 FROM agent_runner_session_claims WHERE owner_runner_id=%s", (accepted["runner_id"],)) == []
    assert len(service_database.rows("SELECT 1 FROM chat_records WHERE session_id=%s", (actors["a"].session_id,))) == 1


def test_explicit_user_cancel_is_terminal_and_cleans_attachment_after_commit(workers, actors, service_database):
    release = threading.Event()
    reply = Reply(content="cancel-observed-provider-response", release=release)
    marker = workers.provider.register(reply)
    accepted = accept(workers.api, actors["a"], marker, attachments=[{"name":"cancel.txt", "content":base64.b64encode(b"fixture-cancel-file").decode()}])
    child, _ = workers.start()
    try:
        assert reply.arrived.wait(timeout=15)
        workspace = Path(decoded(runner(service_database, accepted["runner_id"])["checkpoint"])["execution"]["resources"]["workspace"])
        assert workspace.exists()
        response = workers.api.call("POST", "/v1/runners/" + accepted["runner_id"] + "/cancel", actor=actors["a"])
        assert response.status_code == 200
        release.set()
        workers.assert_clean_exit(child)
        finished = terminal(service_database, accepted["runner_id"])
        assert finished["status"] == "cancelled" and finished["cancel_requested"]
        assert not workspace.exists()
        assert len(workers.provider.requests(marker)) == 1
    finally:
        release.set()


def test_sigkill_reaper_preserves_checkpoint_claim_and_workspace_without_automatic_model_replay(workers, actors, service_database):
    release = threading.Event()
    reply = Reply(release=release)
    marker = workers.provider.register(reply)
    accepted = accept(workers.api, actors["a"], marker, attachments=[{"name":"killed.txt", "content":base64.b64encode(b"fixture-killed-file").decode()}])
    child, _ = workers.start()
    try:
        assert reply.arrived.wait(timeout=15)
        before = runner(service_database, accepted["runner_id"])
        workspace = Path(decoded(before["checkpoint"])["execution"]["resources"]["workspace"])
        workers.processes.stop(child, force=True)
        assert child.returncode < 0
        service_database.rows("UPDATE agent_runners SET lease_until=clock_timestamp()-INTERVAL '1 second' WHERE runner_id=%s", (accepted["runner_id"],))
        reaper, _ = workers.start()
        workers.assert_clean_exit(reaper)
        stopped = runner(service_database, accepted["runner_id"])
        assert stopped["status"] == "interrupted" and not stopped["cancel_requested"]
        assert decoded(stopped["checkpoint"]) == decoded(before["checkpoint"])
        assert (workspace / "killed.txt").read_bytes() == b"fixture-killed-file"
        assert len(workers.provider.requests(marker)) == 1
        assert service_database.rows("SELECT 1 FROM agent_runner_session_claims WHERE owner_runner_id=%s", (accepted["runner_id"],))
        assert service_database.rows("SELECT 1 FROM chat_records WHERE session_id=%s", (actors["a"].session_id,)) == []
    finally:
        release.set()


def test_finalized_public_record_masks_nested_sensitive_args_without_mutating_real_tool_input(workers, actors, service_database, gateways):
    fixture = workers.root / "mask-tool-input.txt"
    fixture.write_text("actual-tool-readable-result")
    original = {"file_path": str(fixture), "nested": {"password":"fictional-password-value", "access-token":"fictional-token-value"},
                "items":[{"api_key":"fictional-api-key"}], "oversized":"q"*500}
    first_reply = tool_reply("read", original, call_id="record-mask-call")
    first_reply.reasoning_content = "fictional-private-model-reasoning"
    marker = workers.provider.register(first_reply, Reply(content="masked-record-result"))
    encoded = base64.b64encode(b"fictional-private-inline-attachment-bytes").decode()
    accepted = accept(workers.api, actors["a"], marker,
                      attachments=[{"name":"private-inline.txt","content":encoded}])
    child, _ = workers.start()
    workers.assert_clean_exit(child)
    finished = terminal(service_database, accepted["runner_id"])
    private = decoded(finished["checkpoint"])["execution"]["tools"]["record-mask-call"]["call"]["arguments"]
    assert private == original
    details = service_database.rows("SELECT execution_details FROM chat_records WHERE session_id=%s", (actors["a"].session_id,))[0]["execution_details"]
    public_args = decoded(details)["tool_executions"][0]["tool_args"]
    assert public_args["file_path"] == original["file_path"]
    assert public_args["nested"] == {"password":"***", "access-token":"***"}
    assert public_args["items"] == [{"api_key":"***"}]
    assert len(public_args["oversized"]) < len(original["oversized"])
    results = [message for message in workers.provider.requests(marker)[1]["messages"] if message.get("role") == "tool"]
    assert any("actual-tool-readable-result" in message["content"] for message in results)
    # Public polling/list projections must not expose private history, raw tool
    # arguments or attachment payloads. The private checkpoint/input retain the
    # original values, so this cannot pass by corrupting actual execution facts.
    assert decoded(finished["input"])["attachments"][0]["content"] == encoded
    fetched = workers.api.call("GET","/v1/runners/"+accepted["runner_id"],actor=actors["a"])
    listed = workers.api.call("GET","/v1/sessions/web/"+actors["a"].session_id+"/runners?source=chat",actor=actors["a"])
    assert fetched.status_code == listed.status_code == 200
    public = [fetched.json()["runner"], next(item for item in listed.json()["runners"] if item["runner_id"] == accepted["runner_id"])]
    for projection in [accepted,*public]:
        assert "content" not in projection["snapshot"]["input"]["attachments"][0]
        if projection.get("result"):
            assert "messages" not in projection["result"]
        serialized = json.dumps(projection)
        assert all(value not in serialized for value in (encoded,"fictional-password-value","fictional-token-value","fictional-api-key"))
    private_history = service_database.rows("SELECT message_id,role,content,metadata FROM chat_messages WHERE session_id=%s ORDER BY created_at",
                                            (actors["a"].session_id,))
    assert any(item["role"] == "tool" and "actual-tool-readable-result" in item["content"] for item in private_history)
    assert any(decoded(item["metadata"]).get("tool_calls") for item in private_history)
    # Qwen's real adapter does not return reasoning_content. Seed a historical
    # private field on an actually finalized internal assistant row to exercise
    # the reader policy, without claiming the current Qwen call produced it.
    service_database.rows("UPDATE chat_messages SET metadata=(metadata::jsonb || %s::jsonb)::text WHERE session_id=%s AND role='assistant' AND metadata::jsonb ? 'tool_calls'",
                          (json.dumps({"reasoning_content": first_reply.reasoning_content}), actors["a"].session_id))
    private_history = service_database.rows("SELECT message_id,role,content,metadata FROM chat_messages WHERE session_id=%s ORDER BY created_at",
                                            (actors["a"].session_id,))
    assert any(decoded(item["metadata"]).get("reasoning_content") == first_reply.reasoning_content for item in private_history)
    gateway = gateways.start()
    response = require_status(gateway.call("GET", "/api/sessions/" + actors["a"].session_id + "/messages", actor=actors["a"]), 200)
    history = response["messages"]
    assert [(item["role"], item["content"]) for item in history] == [("user", marker), ("assistant", "masked-record-result")]
    assert history[0]["message_id"] == accepted["runner_id"] + ":user"
    assert history[0]["metadata"]["attachments"][0]["name"] == "private-inline.txt"
    serialized = json.dumps(history)
    assert all(value not in serialized for value in (encoded,"fictional-private-inline-attachment-bytes", first_reply.reasoning_content,
        "actual-tool-readable-result", "fictional-password-value", "fictional-token-value", "fictional-api-key"))
    assert all("tool_calls" not in item["metadata"] and "reasoning_content" not in item["metadata"] for item in history)
    assert private_history == service_database.rows("SELECT message_id,role,content,metadata FROM chat_messages WHERE session_id=%s ORDER BY created_at",
                                                   (actors["a"].session_id,))


def test_real_shared_parent_child_checkpoint_owner_serializes_waiting_sibling_snapshots(service_database, api_pair, actors):
    from src.core.agent_engine.contracts import ExecutionState, Identity, AgentMode
    from src.services.agent_runner.durable_control import DurableControl
    from src.services.agent_runner.execution_repository import ExecutionRepository
    from src.services.agent_runner.ownership import Attempt
    from src.services.agent_runner.runtime.child import ChildControl
    accepted = accept(api_pair, actors["a"], "fixture-checkpoint-only-no-provider-dispatch")
    repository = ExecutionRepository(service_database.connect)
    owned = repository.acquire("fixture-parent-child-checkpoint-owner", 30)
    assert owned["runner_id"] == accepted["runner_id"]
    identity = Identity(actors["a"].tenant_id, actors["a"].user_id, actors["a"].session_id, "chat", "web")
    root_state = ExecutionState(identity=identity, execution_id=accepted["runner_id"], role=AgentMode.MASTER,
                                system_prompt="fixture", messages=[])
    child_a = ExecutionState(identity=identity, execution_id="fixture-owned-child-a", role=AgentMode.SUBAGENT,
                             system_prompt="fixture", messages=[])
    child_b = ExecutionState(identity=identity, execution_id="fixture-owned-child-b", role=AgentMode.SUBAGENT,
                             system_prompt="fixture", messages=[])
    control = DurableControl(repository, Attempt(owned["runner_id"], owned["worker_id"], owned["attempt"]), owned)

    async def overlapping_checkpoint_saves():
        await control.save(root_state, "fixture.initial")
        port_a, port_b = ChildControl(root_state, control, "delegate-a"), ChildControl(root_state, control, "delegate-b")
        await control.lock.acquire()
        try:
            child_a.output = "older-child-a"
            first = asyncio.create_task(port_a.save(child_a, "fixture.snapshot"))
            await asyncio.sleep(0)  # Queue at the real shared owner lock.
            child_a.output = "newer-child-a"
            second = asyncio.create_task(port_a.save(child_a, "fixture.snapshot"))
            child_b.output = "sibling-child-b"
            sibling = asyncio.create_task(port_b.save(child_b, "fixture.snapshot"))
            await asyncio.sleep(0)
        finally:
            control.lock.release()
        await asyncio.gather(first, second, sibling)
    asyncio.run(overlapping_checkpoint_saves())
    persisted = decoded(runner(service_database, accepted["runner_id"])["checkpoint"])["execution"]
    assert persisted["children"]["delegate-a"]["checkpoint"]["output"] == "newer-child-a"
    assert persisted["children"]["delegate-b"]["checkpoint"]["output"] == "sibling-child-b"
    assert runner(service_database, accepted["runner_id"])["revision"] == owned["revision"] + 4
    child_a.output = "mutated-after-save"
    root_state.children.clear()
    assert decoded(runner(service_database, accepted["runner_id"])["checkpoint"])["execution"] == persisted


def test_real_child_public_runtime_with_empty_profile_environment_does_not_inherit_parent_mapping(workers, actors, service_database, child_profile):
    proof = workers.root / "child-env-proof.json"
    command = workers.root / "child-env-probe.py"
    command.write_text("import os,json\nfrom pathlib import Path\nPath(" + repr(str(proof))
        + ").write_text(json.dumps({'parent_value_present':'RUNNER_FIXTURE_PARENT_PROFILE_VALUE' in os.environ}))\n")
    marker = workers.provider.register(tool_reply("use_skill", {"skill":"followup-tracking"}, call_id="env-child-load"),
        tool_reply("skill_execute", {"skill":"followup-tracking", "command":"python " + shlex.quote(str(command))}, call_id="env-child-execute"),
        Reply(content="child-environment-isolated"))
    actor = actors["a"]
    script = """
import asyncio,sys
from pathlib import Path
from src.db.database import init_postgres_pool,close_postgres_pool
from src.core.agent_engine.contracts import Identity
from src.subagents.registry import SubagentRegistry
from src.services.agent_runner.runtime.child import ChildExecution
from src.tools.context import ToolExecutionContext,tool_execution_scope
init_postgres_pool()
try:
    config=SubagentRegistry(Path(sys.argv[1])/'subagents').get(sys.argv[2])
    identity=Identity(sys.argv[3],sys.argv[4],sys.argv[5],'chat','web')
    parent=ToolExecutionContext(tenant_id=identity.tenant_id,user_id=identity.user_id,session_id=identity.session_id,
        subagent_id='fixture-parent-with-private-vars',env_vars={'RUNNER_FIXTURE_PARENT_PROFILE_VALUE':'fictional-parent-only-value'})
    child=ChildExecution(identity,config,'fixture-env-child',resource_directory=sys.argv[7])
    async def execute():
        with tool_execution_scope(parent):
            result=await child.execute_as_subagent(sys.argv[6],identity.session_id)
        assert result['summary']=='child-environment-isolated'
    asyncio.run(execute())
finally:
    close_postgres_pool()
"""
    child = workers.processes.start(["-c", script, str(Path(__file__).resolve().parents[3]), child_profile[0],
        actor.tenant_id, actor.user_id, actor.session_id, marker, str(workers.root)],
        environment=workers.environment, private_working_directory=True)
    try:
        assert child.wait(timeout=25) == 0, "Real public child runtime/skill chain failed; private log retained until cleanup"
        assert json.loads(proof.read_text()) == {"parent_value_present":False}
        assert len(workers.provider.requests(marker)) == 3 and not workers.provider.errors
    finally:
        workers.processes.stop(child)


def test_real_worker_provider_failure_drains_live_owned_child_without_finalizing_forced_child_cancellation(workers, actors, service_database, child_profile):
    # A normal RuntimeFactory DI starts a child via the actual public executor
    # API while the parent model is in flight. Only the two external model IOs
    # are controlled; no runtime/loop/worker method or new tool is substituted.
    parent_release, child_release = threading.Event(), threading.Event()
    parent_reply = Reply(status=400,release=parent_release)
    child_reply = Reply(content="live-owned-child-never-returned",release=child_release)
    parent_marker, child_marker = workers.provider.register(parent_reply), workers.provider.register(child_reply)
    accepted = accept(workers.api,actors["a"],parent_marker,
                      attachments=[{"name":"live-child-stop.txt","content":base64.b64encode(b"fixture-live-child-preserved").decode()}])
    proof = workers.root / "owned-child-drain-proof.json"
    script = """
import asyncio,json,sys
from pathlib import Path
from src.config.settings import settings
from src.db.database import init_postgres_pool,close_postgres_pool,init_logs_pool,close_logs_pool,get_db_connection
from src.services.agent_runner.worker import RunnerWorker,RuntimeFactory
from src.tools.context import ToolExecutionContext,tool_execution_scope
class Factory(RuntimeFactory):
    async def create(self,row,principal,control):
        runtime=await super().create(row,principal,control)
        self.runtime=runtime
        async def delegate_while_parent_io_runs():
            def parent_started():
                with get_db_connection() as conn:
                    cur=conn.cursor()
                    cur.execute("SELECT 1 FROM agent_runner_usage_receipts WHERE runner_id=%s AND execution_id=%s",(row['runner_id'],row['runner_id']))
                    return cur.fetchone() is not None
            while not await asyncio.to_thread(parent_started):
                await asyncio.sleep(.025)
            identity=principal.identity
            context=ToolExecutionContext(tenant_id=identity.tenant_id,user_id=identity.user_id,session_id=identity.session_id,
                channel=identity.source,agent_execution_id=row['runner_id'],tool_call_id='fixture-public-owned-delegate',env_vars={})
            with tool_execution_scope(context):
                response=await runtime.resources.subagent_executor.delegate(task_id='fixture-live-owned-task',
                    subagent_name=sys.argv[1],task_description=sys.argv[2],session_id=identity.session_id,user_id=identity.user_id)
            assert response.success
        self.monitor=asyncio.create_task(delegate_while_parent_io_runs())
        return runtime
init_postgres_pool(); init_logs_pool()
try:
    factory=Factory()
    worker=RunnerWorker(settings.agent_runner,worker_id='fixture-live-child-parent-failure',runtime_factory=factory)
    async def run():
        await worker.run(once=True)
        await factory.monitor
        executor=factory.runtime.resources.subagent_executor
        assert not executor.has_active_tasks()
        Path(sys.argv[3]).write_text(json.dumps({'active_children':executor.has_active_tasks()}))
    asyncio.run(run())
finally:
    close_logs_pool(); close_postgres_pool()
"""
    process = workers.processes.start(["-c",script,child_profile[0],child_marker,str(proof)],
                                      environment=workers.environment,private_working_directory=True)
    try:
        assert parent_reply.arrived.wait(timeout=15) and child_reply.arrived.wait(timeout=15)
        before = decoded(runner(service_database,accepted["runner_id"])["checkpoint"])["execution"]
        child_before = before["children"]["fixture-public-owned-delegate"]
        assert child_before["checkpoint"]["outcome"] == "running"
        workspace = Path(before["resources"]["workspace"])
        parent_release.set()
        workers.assert_clean_exit(process)
        stopped = runner(service_database,accepted["runner_id"])
        assert stopped["status"] == "interrupted" and stopped["finished_at"] is None and not stopped["cancel_requested"]
        after = decoded(stopped["checkpoint"])["execution"]
        assert after["children"]["fixture-public-owned-delegate"] == child_before
        assert (workspace/"live-child-stop.txt").read_bytes() == b"fixture-live-child-preserved"
        assert json.loads(proof.read_text()) == {"active_children":False}
        assert service_database.rows("SELECT 1 FROM chat_records WHERE session_id=%s",(actors["a"].session_id,)) == []
        assert service_database.rows("SELECT 1 FROM agent_runner_session_claims WHERE owner_runner_id=%s",(accepted["runner_id"],))
        assert len(workers.provider.requests(parent_marker)) == len(workers.provider.requests(child_marker)) == 1
        assert not workers.provider.errors
    finally:
        parent_release.set(); child_release.set()
        workers.processes.stop(process)
