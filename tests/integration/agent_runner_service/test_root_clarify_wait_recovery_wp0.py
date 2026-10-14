"""WP0 spike：root clarification 等待的真实存储/受理/恢复链路（进程内，无子进程）。

docs/plans/plan-ask-user-interactive-question.md §WP0 的临时用例：controls.py
root 分支（WP0 临时放开）产出的等待，经真实 PostgreSQL 验证 park → reply 受理
（含幂等/stale）→ RecoveryCoordinator 恢复应用 → claim_resume 认领，以及等待态
cancel/pause/resume 的实际语义。正式 ask_user 实现归 WP3，届时随正式分支收敛。

真实/假件边界：
- 真实：AgentEngine 循环 + ControlToolAdapter（WP0 修改的 root 分支）产出等待、
  ExecutionRepository.park、ControlRepository 受理与幂等、RecoveryCoordinator
  恢复应用、RecoveryRepository.claim_resume/reject_resume、隔离 PG 全 schema。
- 假件：模型端口（罐头响应）、引擎 control/observer（记录型）、
  RecoveryCoordinator 的 authorizer/profiles（授权链路由既有套件覆盖）、
  HTTP API 与 fresh CLI worker 子进程依赖 POSIX 信号/进程组 API，Windows 宿主
  不可运行，完整子进程链路由容器环境的既有套件（test_worker_recovery.py 模式）
  覆盖，本文件不重复伪造。
"""

import json
import uuid
from types import SimpleNamespace

import pytest

from src.core.agent_engine import AgentEngine, AgentMode, DispatchResult, ExecutionState, Outcome
from src.core.agent_engine.contracts import Identity
from src.services.agent_runner.control_contracts import RunnerControl
from src.services.agent_runner.contracts import RunnerError
from src.services.agent_runner.durable_control import DurableControl
from src.services.agent_runner.recovery import RecoveryCoordinator
from src.services.agent_runner.recovery_repository import RecoveryRepository
from src.services.agent_runner.runtime.controls import ControlToolAdapter
from src.services.agent_runner.runtime_manifest import configuration_fingerprint
from src.tools.context import ToolExecutionContext
from src.tools.control_set import ControlToolDependencies, ToolControlSet

from .test_control_storage import command, controls, ledger
from .test_storage import attempt, storage

pytestmark = pytest.mark.integration

QUESTION = "Which fictional city?"
ANSWER = "Fictional city Shanghai"
CLARIFY_CALL = "root-wp0-clarify"


def completion(text="", calls=None):
    return {"content": text, "tool_calls": calls or [],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3}}


class Model:
    def __init__(self, *responses):
        self.responses = list(responses)

    async def complete(self, current, call_id, definitions):
        return self.responses.pop(0)


class Port:
    def __init__(self, adapter):
        self.adapter = adapter

    def definitions(self):
        return []

    def display_name(self, item):
        return item.name

    async def dispatch(self, state, call):
        async for item in self.adapter.execute(state, call):
            yield item

    async def recover(self, state, call):
        fact = state.tools[call.id]
        return DispatchResult(fact.result, success=fact.success)


class Recording:
    async def save(self, state, boundary):
        pass

    def command(self):
        return None

    async def event(self, state, event):
        pass

    async def model_usage(self, state, fact):
        pass


def wp0_adapter():
    controls = ToolControlSet(ControlToolDependencies(
        plan_manager=None, skill_registry=SimpleNamespace(), skill_executor=None,
        tool_registry=SimpleNamespace()))
    return ControlToolAdapter(
        controls=controls, skills=SimpleNamespace(check=lambda state, name: None),
        registry=SimpleNamespace(get=lambda name: None), plan_manager=None,
        clarification_store=SimpleNamespace(save=lambda identity, payload: None,
                                            clear=lambda identity: None),
        context=ToolExecutionContext(tenant_id="tenant-wp0", user_id="user-wp0",
                                     session_id="session-wp0", env_vars={}),
        user_input="wp0 request", task_record=None, durable_owner=True)


async def parked_root_clarify(storage, actor):
    """真实引擎产出 root clarification 等待并 park；返回（仓库三元组，parked 行，principal）。"""
    repository, execution, submit = storage
    row, principal = submit(actor)
    owned = execution.acquire("wp0-root-clarify-owner", 30)
    assert owned["runner_id"] == row["runner_id"]
    state = ExecutionState(Identity(actor.tenant_id, actor.user_id, actor.session_id),
                           row["runner_id"], AgentMode.MASTER, "wp0 spike",
                           [{"role": "user", "content": "wp0 request"}], initial_len=1)
    state.profile_id = row["profile_id"]
    # 与 RuntimeExecution durable_owner 路径一致：派发前落 configuration_fingerprint。
    state.resources["configuration_fingerprint"] = configuration_fingerprint(row["profile_fingerprint"])
    model = Model(completion("", [{"id": CLARIFY_CALL, "type": "function", "function": {
        "name": "clarify", "arguments": json.dumps({"question": QUESTION, "missing_info": ["city"]})}}]))
    recording = Recording()
    async for _ in AgentEngine().run(state, model, Port(wp0_adapter()), recording, recording):
        pass
    assert state.outcome == Outcome.WAITING
    DurableControl(SimpleNamespace(), SimpleNamespace(runner_id=row["runner_id"]),
                   {"revision": 1, "public_snapshot": {}, "cancel_requested": False,
                    "status": "running"}).decorate_wait(state)
    parked = execution.park(attempt(owned), owned["revision"], "waiting",
                            {"execution": state.checkpoint()}, {})
    return repository, execution, parked, principal


def root_reply(runner_id, wait_id):
    return RunnerControl(client_request_id=uuid.uuid4().hex, action="reply",
                         target_execution_id=runner_id, wait_id=wait_id, answer=ANSWER)


def recovery_for(principal, row):
    authorizer = SimpleNamespace(authorize_persisted=lambda persisted, **kwargs: principal,
                                 assert_credit=lambda principal: None)
    profiles = SimpleNamespace(resolve=lambda profile: (None, row["profile_fingerprint"]))
    return RecoveryCoordinator(authorizer, profiles)


async def test_root_clarify_wait_parks_and_reply_acceptance_and_recovery_apply(
        storage, controls, actors, service_database):
    repository, execution, parked, principal = await parked_root_clarify(storage, actors["a"])
    runner_id = parked["runner_id"]
    current = repository.get(runner_id)
    assert current["status"] == "waiting" and current["finished_at"] is None
    root = current["checkpoint"]["execution"]
    waiting = root["waiting"]
    assert root["role"] == "master" and root["execution_id"] == runner_id
    assert waiting["kind"] == "clarification" and waiting["tool_call_id"] == CLARIFY_CALL
    assert waiting["target_execution_id"] == runner_id and waiting["wait_id"]
    fact = root["tools"][CLARIFY_CALL]
    assert fact["phase"] == "waiting"
    assert fact["result"] == {"success": True, "question": QUESTION, "missing_info": ["city"]}
    with pytest.raises(RunnerError, match="CONTROL_WAIT_STALE"):
        controls.submit(principal, runner_id, root_reply(runner_id, "previous-wait"))
    assert ledger(service_database, runner_id) == []
    request = root_reply(runner_id, waiting["wait_id"])
    accepted, created = controls.submit(principal, runner_id, request)
    assert created and accepted["status"] == "accepted"
    duplicate, created_again = controls.submit(principal, runner_id, request)
    assert not created_again and duplicate["control_id"] == accepted["control_id"]
    assert len(ledger(service_database, runner_id)) == 1
    prepared = recovery_for(principal, current).prepare(
        repository.get(runner_id), controls.get(principal, runner_id, accepted["control_id"]))
    restored = prepared.states[runner_id]
    assert restored.outcome == Outcome.RUNNING and restored.waiting is None
    applied = restored.tools[CLARIFY_CALL]
    assert applied.phase == "completed" and applied.success is True
    assert applied.result == {"success": True, "answer": ANSWER, "attachments": [],
                              "wait_id": waiting["wait_id"]}
    assert restored.followup_messages[0]["content"] == ANSWER
    assert prepared.checkpoint["applied_control_id"] == accepted["control_id"]
    assert prepared.checkpoint["supplemental_inputs"][0]["text"] == ANSWER


async def test_waiting_root_clarify_cancel_blocks_controls_and_yields_cancel_only_claim(
        storage, controls, actors, service_database):
    repository, execution, parked, principal = await parked_root_clarify(storage, actors["a"])
    runner_id = parked["runner_id"]
    repository.cancel(principal, runner_id)
    with pytest.raises(RunnerError, match="CONTROL_EXECUTION_CLOSED"):
        controls.submit(principal, runner_id,
                        root_reply(runner_id, parked["checkpoint"]["execution"]["waiting"]["wait_id"]))
    with pytest.raises(RunnerError, match="CONTROL_EXECUTION_CLOSED"):
        controls.submit(principal, runner_id, command("pause"))
    assert ledger(service_database, runner_id) == []
    assert repository.get(runner_id)["cancel_requested"]
    sweeper = execution.acquire("wp0-cancel-sweeper", 30)
    assert sweeper["runner_id"] == runner_id and sweeper["cancel_only"]
    assert sweeper["checkpoint"]["finalization_intent"] == "cancel_only"
    assert execution.acquire("wp0-second-sweeper", 30) is None


async def test_waiting_root_clarify_pause_freezes_until_reply_then_claim_clears_pause(
        storage, controls, actors, service_database):
    repository, execution, parked, principal = await parked_root_clarify(storage, actors["a"])
    runner_id = parked["runner_id"]
    paused, created = controls.submit(principal, runner_id, command("pause"))
    assert created and paused["status"] == "consumed"
    frozen = repository.get(runner_id)
    assert frozen["status"] == "waiting" and frozen["pause_requested"]
    assert frozen["resume_control_id"] is None
    assert frozen["revision"] == parked["revision"]
    # 无 resume_control_id 的 waiting 行不会被认领：pause 冻结等待，直到下一条用户输入。
    assert execution.acquire("wp0-must-not-preclaim", 30) is None
    waiting = frozen["checkpoint"]["execution"]["waiting"]
    accepted, created = controls.submit(principal, runner_id, root_reply(runner_id, waiting["wait_id"]))
    assert created and accepted["status"] == "accepted"
    prepared = recovery_for(principal, frozen).prepare(
        repository.get(runner_id), controls.get(principal, runner_id, accepted["control_id"]))
    resumed = RecoveryRepository(service_database.connect).claim_resume(
        runner_id, "wp0-resume-worker", 30, revision=frozen["revision"],
        control_id=accepted["control_id"], checkpoint=prepared.checkpoint)
    assert resumed["status"] == "running" and resumed["attempt"] == frozen["attempt"] + 1
    assert not resumed["pause_requested"] and resumed["resume_control_id"] is None
    assert resumed["checkpoint"]["applied_control_id"] == accepted["control_id"]
    consumed = controls.get(principal, runner_id, accepted["control_id"])
    assert consumed["status"] == "consumed" and consumed["consumed_attempt"] == resumed["attempt"]


async def test_plain_resume_on_root_clarification_wait_is_rejected_and_preserves_wait(
        storage, controls, actors, service_database):
    repository, execution, parked, principal = await parked_root_clarify(storage, actors["a"])
    runner_id = parked["runner_id"]
    resume, created = controls.submit(principal, runner_id, command("resume"))
    assert created and resume["status"] == "accepted"
    with pytest.raises(RunnerError, match="RECOVERY_TOOL_VERIFICATION_REQUIRED"):
        recovery_for(principal, parked).prepare(
            repository.get(runner_id), controls.get(principal, runner_id, resume["control_id"]))
    recovery = RecoveryRepository(service_database.connect)
    assert recovery.reject_resume(runner_id, revision=repository.get(runner_id)["revision"],
                                  control_id=resume["control_id"],
                                  error_code="RECOVERY_TOOL_VERIFICATION_REQUIRED")
    current = repository.get(runner_id)
    assert current["status"] == "waiting" and current["resume_control_id"] is None
    rejected = controls.get(principal, runner_id, resume["control_id"])
    assert rejected["status"] == "rejected"
    waiting = current["checkpoint"]["execution"]["waiting"]
    retried, created = controls.submit(principal, runner_id, root_reply(runner_id, waiting["wait_id"]))
    assert created and retried["control_id"] != resume["control_id"]
