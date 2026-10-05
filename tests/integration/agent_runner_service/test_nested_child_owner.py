"""Private descendant checkpoint/authority ports with real PG, not public delegate.

The public child model catalog excludes delegation. These tests exercise a
trusted imported nested checkpoint shape and real owner ports; they do not
claim a user can create a grandchild through a model tool or default CLI.
"""
import asyncio

import pytest

from src.core.agent_engine.contracts import AgentMode, ExecutionState, Outcome, ToolCall, ToolFact
from src.services.agent_runner.aggregate_tree import child_tree_finished, park_unfinished_tree
from src.services.agent_runner.authorization import RunnerAuthorizer
from src.services.agent_runner.contracts import RunnerError
from src.services.agent_runner.durable_control import DurableControl
from src.services.agent_runner.runtime.child import ChildControl

from .test_storage import storage, attempt
from .test_worker_controls import child_profile

pytestmark = pytest.mark.integration


def owned_ports(storage, actor, profile):
    repository, execution, submit = storage
    row, principal = submit(actor)
    acquired = execution.acquire("nested-private-owner", 60)
    owner = DurableControl(execution, attempt(acquired), acquired)
    root = ExecutionState(principal.identity, row["runner_id"], AgentMode.MASTER, "fixture", [], outcome=Outcome.COMPLETED)
    middle = ExecutionState(principal.identity, "fixture-original-middle", AgentMode.SUBAGENT, "fixture", [],
                            profile_id=profile, outcome=Outcome.COMPLETED)
    leaf = ExecutionState(principal.identity, "fixture-original-leaf", AgentMode.SUBAGENT, "fixture", [],
                          profile_id=profile, outcome=Outcome.WAITING)
    leaf.waiting = {"kind": "clarification", "question": "Fixture private descendant question", "tool_call_id": "leaf-clarify"}
    for state, call_id in ((root, "root-middle-call"), (middle, "middle-leaf-call")):
        call = ToolCall(call_id, "delegate_to_subagent", {"subagent_name": profile})
        state.tools[call_id] = ToolFact(call, phase="completed", success=True, result_recorded=True)
    parent = ChildControl(root, owner, "root-middle-call")
    parent.profile_id = profile
    leaf_port = ChildControl(middle, parent, "middle-leaf-call")
    leaf_port.profile_id = profile
    return row, principal, owner, root, middle, leaf, leaf_port


def test_real_nested_child_ports_persist_leaf_wait_and_aggregate_cannot_finalize_completed_ancestors(
        storage, actors, service_database, child_profile):
    repository, _, _ = storage
    row, _, owner, root, middle, leaf, port = owned_ports(storage, actors["a"], child_profile[0])
    asyncio.run(port.save(leaf, "fixture.imported_wait"))
    persisted = repository.get(row["runner_id"])["checkpoint"]["execution"]
    descendant = persisted["children"]["root-middle-call"]["checkpoint"]["children"]["middle-leaf-call"]["checkpoint"]
    assert descendant["execution_id"] == leaf.execution_id
    assert descendant["waiting"]["wait_id"] and descendant["waiting"]["target_execution_id"] == leaf.execution_id
    assert persisted["outcome"] == middle.outcome.value == "completed"
    assert not child_tree_finished(persisted)
    restored = ExecutionState.restore(persisted)
    assert park_unfinished_tree(restored) == "waiting"
    assert restored.resources["root_execution_outcome"] == "completed"
    assert restored.waiting["child_wait"]["target_execution_id"] == leaf.execution_id
    assert restored.waiting["tool_call_id"] == "root-middle-call"
    assert owner.snapshot["clarificationQuestions"][0]["wait_id"] == descendant["waiting"]["wait_id"]
    assert service_database.rows("SELECT 1 FROM agent_runner_usage_receipts WHERE runner_id=%s", (row["runner_id"],)) == []


def test_nested_dispatch_forwards_actual_leaf_permission_to_root_pg_authority_and_rejects_revocation(
        storage, actors, service_database, child_profile):
    repository, _, _ = storage
    row, principal, owner, root, middle, leaf, port = owned_ports(storage, actors["a"], child_profile[0])
    authorizer = RunnerAuthorizer(None, service_database.connect)
    checked_profiles = []
    async def fresh_authority(profile_id):
        checked_profiles.append(profile_id)
        await asyncio.to_thread(authorizer.authorize_child_profile, principal, profile_id)
    owner.authorization_check = fresh_authority
    asyncio.run(port.authorize_dispatch(child_profile[0]))
    assert checked_profiles == [child_profile[0]]
    original = repository.get(row["runner_id"])
    service_database.rows("UPDATE subscriptions SET status='cancelled' WHERE subscription_id=%s", (child_profile[1],))
    with pytest.raises(RunnerError, match="PROFILE_FORBIDDEN"):
        asyncio.run(port.authorize_dispatch(child_profile[0]))
    assert checked_profiles == [child_profile[0], child_profile[0]]
    current = repository.get(row["runner_id"])
    assert current["revision"] == original["revision"] and current["checkpoint"] == original["checkpoint"]
    assert service_database.rows("SELECT 1 FROM agent_runner_usage_receipts WHERE runner_id=%s", (row["runner_id"],)) == []
