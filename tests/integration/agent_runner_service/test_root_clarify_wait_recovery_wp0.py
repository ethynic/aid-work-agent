"""root clarification 等待的真实存储/受理/恢复链路（进程内，无子进程）。

docs/system/design-ask-user-interactive-question.md §11.1（WP0 已验证结论）的
正式链路用例（WP3 正式实现，文件名沿用 WP0 沉淀）：controls.py 的 clarify 与
ask_user 分支产出的等待，经真实 PostgreSQL 验证 park → reply 受理（含幂等/
stale）→ RecoveryCoordinator 恢复应用 → claim_resume 认领，以及等待态
cancel/pause/resume 的实际语义。ask_user 用例覆盖完整真实链路：工具生成 form
→ decorate 注入 form.wait_id → park → 结构化 answers 受理（canonical 权威生成）
→ canonical 回注工具结果 → 续跑至 final_answer。

真实/假件边界：
- 真实：AgentEngine 循环 + ControlToolAdapter 产出等待、ToolControlSet 装配的
  真实 ClarifyTool/AskUserTool、ExecutionRepository.park、ControlRepository
  受理与幂等、RecoveryCoordinator 恢复应用、RecoveryRepository
  .claim_resume/reject_resume、隔离 PG 全 schema。
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
from src.services.agent_runner import ask_form
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
ASK_CALL = "root-wp0-ask"

ASK_ARGUMENTS = {
    "title": "归档范围",
    "questions": [
        {"prompt": "以下哪些任务移入 ideas_finished.md？", "type": "multi",
         "required": True, "max_select": 2,
         "options": [{"label": "queue_order 序列自愈"},
                     {"label": "wecom_kf 限频修复"},
                     {"label": "file_assets 观察项"}]},
        {"prompt": "还有什么需要补充？", "type": "text"},
    ],
    "allow_skip": True,
}
ASK_ANSWERS = [{"question_id": "q1", "selected_keys": ["q1o2"],
                "text": "file_assets 建议观察一周再归档"}]


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
                           row["runner_id"], AgentMode.MASTER, "wp0 ask wait",
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
        storage, controls, actors, service_database, monkeypatch):
    import src.services.agent_runner.recovery_repository as rr_module
    claim_logs = []
    monkeypatch.setattr(rr_module, 'logger',
                        SimpleNamespace(info=lambda *a, **k: claim_logs.append(a)))
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
    # 生命周期埋点（八轮评审修正）：legacy clarify（纯文本 payload 无结构化键）
    # 认领后不产生 [ask] 埋点——不混入 ask 统计。
    claim = RecoveryRepository().claim_resume(
        runner_id, "wp0-legacy-worker", 30,
        revision=repository.get(runner_id)["revision"],
        control_id=accepted["control_id"], checkpoint=prepared.checkpoint)
    assert claim is not None and claim["status"] == "running"
    assert not [a for a in claim_logs if a and a[0].startswith('[ask] consumed')]


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


def ask_call():
    return {"id": ASK_CALL, "type": "function",
            "function": {"name": "ask_user", "arguments": json.dumps(ASK_ARGUMENTS)}}


async def parked_root_ask(storage, actor):
    """真实 ask_user 工具产出 root form 等待并 park；返回（仓库, parked 行, principal）。"""
    repository, execution, submit = storage
    row, principal = submit(actor)
    owned = execution.acquire("wp0-root-ask-owner", 30)
    assert owned["runner_id"] == row["runner_id"]
    state = ExecutionState(Identity(actor.tenant_id, actor.user_id, actor.session_id),
                           row["runner_id"], AgentMode.MASTER, "wp0 ask",
                           [{"role": "user", "content": "wp0 request"}], initial_len=1)
    state.profile_id = row["profile_id"]
    state.resources["configuration_fingerprint"] = configuration_fingerprint(row["profile_fingerprint"])
    model = Model(completion("", [ask_call()]))
    recording = Recording()
    async for _ in AgentEngine().run(state, model, Port(wp0_adapter()), recording, recording):
        pass
    assert state.outcome == Outcome.WAITING
    DurableControl(SimpleNamespace(), SimpleNamespace(runner_id=row["runner_id"]),
                   {"revision": 1, "public_snapshot": {}, "cancel_requested": False,
                    "status": "running"}).decorate_wait(state)
    waiting = state.waiting
    # form.wait_id 由 decorate_wait 注入（工具生成时不携带等待身份）
    assert waiting["kind"] == "clarification"
    assert waiting["form"]["form_id"].startswith("frm_")
    assert waiting["form"]["wait_id"] == waiting["wait_id"]
    parked = execution.park(attempt(owned), owned["revision"], "waiting",
                            {"execution": state.checkpoint()}, {})
    return repository, parked, principal


async def test_root_ask_user_form_wait_accepts_structured_reply_and_resumes(
        storage, controls, actors, service_database, monkeypatch):
    """root ask_user 全链路：真实工具 form → park → 结构化受理 → canonical 回注 → 续跑。"""
    import src.services.agent_runner.recovery_repository as rr_module
    claim_logs = []
    monkeypatch.setattr(rr_module, 'logger',
                        SimpleNamespace(info=lambda *a, **k: claim_logs.append(a)))
    repository, parked, principal = await parked_root_ask(storage, actors["a"])
    runner_id = parked["runner_id"]
    root = parked["checkpoint"]["execution"]
    waiting = root["waiting"]
    form = waiting["form"]
    # 服务端生成的 id 与 decorate 注入的 form.wait_id 一并持久化到 checkpoint
    assert form["wait_id"] == waiting["wait_id"]
    assert [question["id"] for question in form["questions"]] == ["q1", "q2"]
    assert [option["key"] for option in form["questions"][0]["options"]] == ["q1o1", "q1o2", "q1o3"]
    assert form["allow_skip"] is True and form["expires_at"] is None
    fact = root["tools"][ASK_CALL]
    assert fact["phase"] == "waiting"
    assert fact["result"]["form"]["form_id"] == form["form_id"]
    # 结构化 answers 受理：canonical 由服务端权威生成，写入既有 payload['answer'] 键
    canonical = ask_form.canonical_text(form, ask_form.normalize_answers(ASK_ANSWERS))
    request = RunnerControl(client_request_id=uuid.uuid4().hex, action="reply",
                            target_execution_id=runner_id, wait_id=waiting["wait_id"],
                            answers=ASK_ANSWERS)
    accepted, created = controls.submit(principal, runner_id, request)
    assert created and accepted["status"] == "accepted"
    assert accepted["payload"]["answer"] == canonical
    assert accepted["payload"]["form"]["form_id"] == form["form_id"]
    # 恢复应用：canonical 回注为 ask_user 工具结果
    prepared = recovery_for(principal, repository.get(runner_id)).prepare(
        repository.get(runner_id),
        controls.get(principal, runner_id, accepted["control_id"]))
    restored = prepared.states[runner_id]
    assert restored.outcome == Outcome.RUNNING and restored.waiting is None
    applied = restored.tools[ASK_CALL]
    assert applied.phase == "completed" and applied.success is True
    assert applied.result == {"success": True, "answer": canonical, "attachments": [],
                              "wait_id": waiting["wait_id"]}
    assert restored.followup_messages[0]["content"] == canonical
    # 续跑至 final_answer
    resumed = ExecutionState.restore(prepared.checkpoint["execution"])
    model = Model(completion("root-final-after-ask"))
    recording = Recording()
    async for _ in AgentEngine().run(resumed, model, Port(wp0_adapter()), recording, recording):
        pass
    assert resumed.outcome == Outcome.COMPLETED and resumed.output == "root-final-after-ask"
    tool_messages = [m for m in resumed.messages if m.get("role") == "tool"]
    assert len(tool_messages) == 1
    # 生命周期埋点（八轮评审修正）：认领消费事务提交后记录，仅真实 AskForm
    # 产生；skipped 用 payload.skip 事实（非答案文案推断），时长双指标非空。
    claim = RecoveryRepository().claim_resume(
        runner_id, "wp0-metrics-worker", 30,
        revision=repository.get(runner_id)["revision"],
        control_id=accepted["control_id"], checkpoint=prepared.checkpoint)
    assert claim is not None and claim["status"] == "running"
    ask_logs = [a for a in claim_logs if a and a[0].startswith('[ask] consumed')]
    assert len(ask_logs) == 1
    assert ask_logs[0][1] == waiting["wait_id"] and ask_logs[0][2] is False
    assert ask_logs[0][3] is not None  # recover_ms（DB 时钟，恢复处理延迟）


async def test_ask_skip_submission_logs_skipped_metric(
        storage, controls, actors, monkeypatch):
    """skip=true 提交：埋点 skipped=True 用 payload.skip 事实（非文案推断）。"""
    import src.services.agent_runner.recovery_repository as rr_module
    claim_logs = []
    monkeypatch.setattr(rr_module, 'logger',
                        SimpleNamespace(info=lambda *a, **k: claim_logs.append(a)))
    repository, parked, principal = await parked_root_ask(storage, actors["a"])
    runner_id = parked["runner_id"]
    waiting = parked["checkpoint"]["execution"]["waiting"]
    request = RunnerControl(client_request_id=uuid.uuid4().hex, action="reply",
                            target_execution_id=runner_id, wait_id=waiting["wait_id"],
                            skip=True)
    accepted, created = controls.submit(principal, runner_id, request)
    assert created and accepted["status"] == "accepted"
    assert accepted["payload"]["skip"] is True
    prepared = recovery_for(principal, repository.get(runner_id)).prepare(
        repository.get(runner_id),
        controls.get(principal, runner_id, accepted["control_id"]))
    claim = RecoveryRepository().claim_resume(
        runner_id, "wp0-skip-worker", 30,
        revision=repository.get(runner_id)["revision"],
        control_id=accepted["control_id"], checkpoint=prepared.checkpoint)
    assert claim is not None and claim["status"] == "running"
    ask_logs = [a for a in claim_logs if a and a[0].startswith('[ask] consumed')]
    assert len(ask_logs) == 1
    assert ask_logs[0][1] == waiting["wait_id"] and ask_logs[0][2] is True
    assert ask_logs[0][3] is not None  # recover_ms（DB 时钟，恢复处理延迟）


async def test_ask_plain_text_reply_still_counts_metric(
        storage, controls, actors, monkeypatch):
    # 九轮评审 P2-1：文字兼容作答同样统计——是否 AskForm 按消费前原 wait 判定，
    # 不按作答载体（结构化/文字）决定。
    import src.services.agent_runner.recovery_repository as rr_module
    claim_logs = []
    monkeypatch.setattr(rr_module, 'logger',
                        SimpleNamespace(info=lambda *a, **k: claim_logs.append(a)))
    repository, parked, principal = await parked_root_ask(storage, actors["a"])
    runner_id = parked["runner_id"]
    waiting = parked["checkpoint"]["execution"]["waiting"]
    request = RunnerControl(client_request_id=uuid.uuid4().hex, action="reply",
                            target_execution_id=runner_id, wait_id=waiting["wait_id"],
                            answer="q1 选前三项，其余观察")
    accepted, created = controls.submit(principal, runner_id, request)
    assert created and accepted["status"] == "accepted"
    prepared = recovery_for(principal, repository.get(runner_id)).prepare(
        repository.get(runner_id),
        controls.get(principal, runner_id, accepted["control_id"]))
    claim = RecoveryRepository().claim_resume(
        runner_id, "wp0-text-worker", 30,
        revision=repository.get(runner_id)["revision"],
        control_id=accepted["control_id"], checkpoint=prepared.checkpoint)
    assert claim is not None and claim["status"] == "running"
    ask_logs = [a for a in claim_logs if a and a[0].startswith('[ask] consumed')]
    assert len(ask_logs) == 1
    assert ask_logs[0][1] == waiting["wait_id"] and ask_logs[0][2] is False
    assert ask_logs[0][3] is not None  # recover_ms（DB 时钟，恢复处理延迟）


async def test_park_waiting_notification_failure_isolated_by_savepoint(
        storage, controls, actors, service_database, monkeypatch):
    """五轮评审 P1-1：通知与 park 共用事务——通知 SQL 失败使 PG 事务进入中止
    态，savepoint 必须回滚该失败并保留 park 更新正常提交；不得出现「返回
    waiting 而持久状态仍为 running」，也不得残留通知行。"""
    import src.notifications.producer as producer_module

    def broken_producer(cursor, runner_row, config=None):
        cursor.execute('THIS_IS_NOT_VALID_SQL')  # 令 PG 事务进入中止态
        raise RuntimeError('notification boom')

    monkeypatch.setattr(producer_module, 'publish_runner_waiting_in_tx', broken_producer)
    repository, execution, parked, principal = await parked_root_clarify(storage, actors["a"])
    runner_id = parked["runner_id"]
    assert parked["status"] == "waiting"
    committed = repository.get(runner_id)
    assert committed["status"] == "waiting" and committed["finished_at"] is None
    assert service_database.rows(
        "SELECT count(*) AS cnt FROM notification_messages "
        "WHERE producer='runner' AND resource_ref->>'id'=%s", (runner_id,))[
        0]["cnt"] == 0
