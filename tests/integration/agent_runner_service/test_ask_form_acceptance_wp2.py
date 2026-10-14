"""WP2 受理/恢复层：结构化提问的真实数据库受理、幂等、canonical 回注与兼容。

docs/plans/plan-ask-user-interactive-question.md §WP2 验收（数据库真实链路）：
- 合法结构化答案实际受理并经 recovery 进入 tool result（canonical 落既有
  payload['answer'] 键）；skip 权威跳过文案同链路；
- 受理幂等两例（自 WP1 分配至本层）：同键重试命中已受理控制；已受理后同键
  改答案 IDEMPOTENCY_INPUT_MISMATCH；
- 判定表失败同步 422 + errors 逐题明细：不创建 control、不消费 wait、不改
  快照（校验错误只在 HTTP 响应），修正后可用原键重提；
- wait 含 form 的纯文本 reply 走兼容路径（§4.2 自由文本政策）；wait 无 form
  的旧 clarify 全路径回归由 WP0 集成用例（test_root_clarify_wait_recovery_wp0）
  与本文件回归基线共同覆盖。

真实/假件边界：真实 ControlRepository/ExecutionRepository/隔离 PG/恢复协调器；
假件为引擎端口与授权桩（与 WP0 集成用例同构）。API/BFF 的 422 errors 透传
为纯 HTTP 结构，不依赖数据库，由 tests/unit/.../test_ask_form_error_chain.py
覆盖（其响应体与本文件断言的 RunnerError.errors 同构）。
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

from .test_control_storage import controls, ledger  # noqa: F401  fixture/helper 复用
from .test_storage import attempt, storage  # noqa: F401  fixture 复用

pytestmark = pytest.mark.integration

CLARIFY_CALL = "wp2-ask-form-call"
SKIP_TEXT = ask_form.SKIP_TEXT
EXPECTED_CANONICAL = ("Q1【归档范围】queue_order 序列自愈、wecom_kf 限频修复；"
                      "自定义：file_assets 建议观察一周再归档\n"
                      "Q2【后续独立事项】飞书钉钉接入 AgentRunner\n"
                      "Q3【补充说明】（未回答）")


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


def ask_adapter():
    controls = ToolControlSet(ControlToolDependencies(
        plan_manager=None, skill_registry=SimpleNamespace(), skill_executor=None,
        tool_registry=SimpleNamespace()))
    return ControlToolAdapter(
        controls=controls, skills=SimpleNamespace(check=lambda state, name: None),
        registry=SimpleNamespace(get=lambda name: None), plan_manager=None,
        clarification_store=SimpleNamespace(save=lambda identity, payload: None,
                                            clear=lambda identity: None),
        context=ToolExecutionContext(tenant_id="tenant-wp2", user_id="user-wp2",
                                     session_id="session-wp2", env_vars={}),
        user_input="wp2 request", task_record=None, durable_owner=True)


def ask_form_fixture(*, allow_skip=True, required_q1=True):
    """与 contracts/runtime-host/v1/fixtures/ask-form.json 首例同构的混合题型表单。"""
    return {
        "form_id": "frm_wp2-1", "wait_id": "wait_wp2-1", "title": "归档范围",
        "intro": "请确认归档范围。",
        "questions": [
            {"id": "q1", "tag": "归档范围", "prompt": "以下哪些任务移入 ideas_finished.md？",
             "type": "multi", "required": required_q1, "max_select": 3,
             "options": [
                 {"key": "q1o1", "label": "queue_order 序列自愈"},
                 {"key": "q1o2", "label": "wecom_kf 限频修复"},
                 {"key": "q1o3", "label": "file_assets 观察项"}],
             "allow_custom_text": True},
            {"id": "q2", "tag": "后续独立事项", "prompt": "哪个事项需要单独立任务？",
             "type": "single",
             "options": [{"key": "q2o1", "label": "飞书钉钉接入 AgentRunner"},
                         {"key": "q2o2", "label": "暂无"}]},
            {"id": "q3", "tag": "补充说明", "prompt": "还有什么需要补充？", "type": "text"},
        ],
        "allow_skip": allow_skip, "expires_at": None}


async def parked_ask_wait(storage, actor, *, form=None):
    """真实引擎产出 root clarification 等待；form 直接构造到 wait 上（正式挂载归 WP3）。"""
    repository, execution, submit = storage
    row, principal = submit(actor)
    owned = execution.acquire("wp2-ask-form-owner", 30)
    assert owned["runner_id"] == row["runner_id"]
    state = ExecutionState(Identity(actor.tenant_id, actor.user_id, actor.session_id),
                           row["runner_id"], AgentMode.MASTER, "wp2 ask form",
                           [{"role": "user", "content": "wp2 request"}], initial_len=1)
    state.profile_id = row["profile_id"]
    # 与 RuntimeExecution durable_owner 路径一致：恢复校验依赖该指纹。
    state.resources["configuration_fingerprint"] = configuration_fingerprint(
        row["profile_fingerprint"])
    model = Model(completion("", [{"id": CLARIFY_CALL, "type": "function", "function": {
        "name": "clarify", "arguments": json.dumps({"question": "请确认归档范围"})}}]))
    recording = Recording()
    async for _ in AgentEngine().run(state, model, Port(ask_adapter()), recording, recording):
        pass
    assert state.outcome == Outcome.WAITING
    DurableControl(SimpleNamespace(), SimpleNamespace(runner_id=row["runner_id"]),
                   {"revision": 1, "public_snapshot": {}, "cancel_requested": False,
                    "status": "running"}).decorate_wait(state)
    if form is not None:
        state.waiting = {**state.waiting, "question": "请确认归档范围",
                         "form": {**form, "wait_id": state.waiting["wait_id"]}}
    parked = execution.park(attempt(owned), owned["revision"], "waiting",
                            {"execution": state.checkpoint()}, {})
    return parked, principal


def runner_row(service_database, runner_id):
    return service_database.rows("SELECT * FROM agent_runners WHERE runner_id=%s",
                                 (runner_id,))[0]


def wait_id_of(parked):
    return parked["checkpoint"]["execution"]["waiting"]["wait_id"]


def structured(target, wait_id, *, answers=None, skip=False, key=None):
    values = {}
    if answers is not None:
        values["answers"] = answers
    if skip:
        values["skip"] = True
    return RunnerControl(client_request_id=key or uuid.uuid4().hex, action="reply",
                         target_execution_id=target, wait_id=wait_id, **values)


def answer(qid, keys=(), text=None):
    return {"question_id": qid, "selected_keys": list(keys), "text": text}


def recovery_for(principal, row):
    authorizer = SimpleNamespace(authorize_persisted=lambda persisted, **kwargs: principal,
                                 assert_credit=lambda principal: None)
    profiles = SimpleNamespace(resolve=lambda profile: (None, row["profile_fingerprint"]))
    return RecoveryCoordinator(authorizer, profiles)


async def test_structured_acceptance_writes_canonical_into_answer_key_and_recovers(
        storage, controls, actors, service_database):
    parked, principal = await parked_ask_wait(storage, actors["a"], form=ask_form_fixture())
    runner_id = parked["runner_id"]
    request = structured(runner_id, wait_id_of(parked), answers=[
        answer("q1", ["q1o1", "q1o2"], "file_assets 建议观察一周再归档"),
        answer("q2", ["q2o1"])])
    accepted, created = controls.submit(principal, runner_id, request)
    assert created and accepted["status"] == "accepted"
    # canonical 写入既有 payload['answer'] 键（recovery._apply_reply 读该键）；
    # form 快照与客户端原始意图答案仅审计附加，不参与 digest。
    assert accepted["payload"]["answer"] == EXPECTED_CANONICAL
    assert accepted["payload"]["form"]["form_id"] == "frm_wp2-1"
    assert accepted["payload"]["answers"] == [answer("q1", ["q1o1", "q1o2"],
                                                     "file_assets 建议观察一周再归档"),
                                              answer("q2", ["q2o1"])]
    # supplementalInputs 扩展投影：text 与 tool result 同源（canonical）。
    inputs = runner_row(service_database, runner_id)["public_snapshot"]["supplementalInputs"]
    assert len(inputs) == 1
    assert inputs[0]["text"] == EXPECTED_CANONICAL
    assert inputs[0]["answers"] == [
        {"question_id": "q1", "selected_keys": ["q1o1", "q1o2"],
         "text": "file_assets 建议观察一周再归档"},
        {"question_id": "q2", "selected_keys": ["q2o1"], "text": None}]
    assert inputs[0]["form"]["form_id"] == "frm_wp2-1"
    # 真实恢复：canonical 经既有 answer 键回注为 tool result 与用户消息。
    prepared = recovery_for(principal, runner_row(service_database, runner_id)).prepare(
        runner_row(service_database, runner_id),
        controls.get(principal, runner_id, accepted["control_id"]))
    restored = prepared.states[runner_id]
    assert restored.outcome == Outcome.RUNNING and restored.waiting is None
    applied = restored.tools[CLARIFY_CALL]
    wait_id = parked["checkpoint"]["execution"]["waiting"]["wait_id"]
    assert applied.result == {"success": True, "answer": EXPECTED_CANONICAL,
                              "attachments": [], "wait_id": wait_id}
    assert restored.followup_messages[0]["content"] == EXPECTED_CANONICAL
    assert prepared.checkpoint["supplemental_inputs"][0]["text"] == EXPECTED_CANONICAL
    # claim_resume 复核 control payload（含 canonical answer 与 form/answers
    # 审计键）：认领只取等待定位字段，不得重过入口模型校验而炸掉 worker 认领。
    resumed = RecoveryRepository(service_database.connect).claim_resume(
        runner_id, "wp2-claim-worker", 30,
        revision=runner_row(service_database, runner_id)["revision"],
        control_id=accepted["control_id"], checkpoint=prepared.checkpoint)
    assert resumed["status"] == "running"
    consumed = controls.get(principal, runner_id, accepted["control_id"])
    assert consumed["status"] == "consumed"


async def test_same_key_retry_hits_accepted_control_without_new_mutation(
        storage, controls, actors, service_database):
    parked, principal = await parked_ask_wait(storage, actors["a"], form=ask_form_fixture())
    runner_id = parked["runner_id"]
    request = structured(runner_id, wait_id_of(parked), answers=[answer("q1", ["q1o1"])], key="wp2-idem-1")
    accepted, created = controls.submit(principal, runner_id, request)
    after = runner_row(service_database, runner_id)
    duplicate, created_again = controls.submit(principal, runner_id, request)
    assert created and not created_again
    assert duplicate["control_id"] == accepted["control_id"]
    assert duplicate["payload"]["answer"] == accepted["payload"]["answer"]
    assert runner_row(service_database, runner_id)["public_snapshot"] == after["public_snapshot"]
    assert len(ledger(service_database, runner_id)) == 1


async def test_same_key_changed_answers_rejected_after_acceptance(
        storage, controls, actors, service_database):
    parked, principal = await parked_ask_wait(storage, actors["a"], form=ask_form_fixture())
    runner_id = parked["runner_id"]
    accepted, _ = controls.submit(principal, runner_id,
                                  structured(runner_id, wait_id_of(parked), answers=[answer("q1", ["q1o1"])],
                                             key="wp2-idem-2"))
    with pytest.raises(RunnerError, match="IDEMPOTENCY_INPUT_MISMATCH") as caught:
        controls.submit(principal, runner_id,
                        structured(runner_id, wait_id_of(parked), answers=[answer("q1", ["q1o2"])],
                                   key="wp2-idem-2"))
    assert caught.value.status == 409
    assert [item["control_id"] for item in ledger(service_database, runner_id)] == \
        [accepted["control_id"]]


async def test_invalid_structured_reply_is_422_with_details_and_no_side_effects(
        storage, controls, actors, service_database):
    parked, principal = await parked_ask_wait(storage, actors["a"], form=ask_form_fixture())
    runner_id = parked["runner_id"]
    with pytest.raises(RunnerError, match="FORM_ANSWER_INVALID") as caught:
        controls.submit(principal, runner_id,
                        structured(runner_id, wait_id_of(parked), answers=[answer("q1", ["q1o9"])],
                                   key="wp2-invalid-1"))
    assert caught.value.status == 422
    # errors 逐题明细结构化保留（Runner API 以该异常输出 422 响应体）。
    assert caught.value.errors == [
        {"question_id": "q1", "code": "FORM_ANSWER_INVALID", "message": "选择的选项不属于该题"}]
    # 不创建 control、不消费 wait、不写任何持久状态。
    assert ledger(service_database, runner_id) == []
    current = runner_row(service_database, runner_id)
    assert current["status"] == "waiting" and current["resume_control_id"] is None
    assert current["checkpoint"] == parked["checkpoint"]
    assert current["public_snapshot"] == parked["public_snapshot"]
    # 校验失败未落库即无意图冲突：修正后可用原 client_request_id 重提。
    accepted, created = controls.submit(principal, runner_id,
                                        structured(runner_id, wait_id_of(parked), answers=[answer("q1", ["q1o1"])],
                                                   key="wp2-invalid-1"))
    assert created
    assert accepted["payload"]["answer"] == (
        "Q1【归档范围】queue_order 序列自愈\nQ2【后续独立事项】（未回答）\nQ3【补充说明】（未回答）")


async def test_structural_and_required_violations_report_per_question(
        storage, controls, actors, service_database):
    parked, principal = await parked_ask_wait(storage, actors["a"], form=ask_form_fixture())
    runner_id = parked["runner_id"]
    with pytest.raises(RunnerError) as caught:
        controls.submit(principal, runner_id, structured(runner_id, wait_id_of(parked), answers=[
            answer("q9"), answer("q3", ["q3o1"]), answer("q2", ["q2o1", "q2o2"])]))
    assert [(item["question_id"], item["message"]) for item in caught.value.errors] == [
        ("q9", "问题不存在于当前表单"), ("q3", "该题为自由文本，不能携带选项"),
        ("q2", "该题为单选，最多选择一项")]
    # 全空提交触达 required 判定行：缺失题指明 question_id。
    with pytest.raises(RunnerError) as caught:
        controls.submit(principal, runner_id, structured(runner_id, wait_id_of(parked), answers=[]))
    assert caught.value.errors == [
        {"question_id": "q1", "code": "FORM_ANSWER_INVALID", "message": "该题为必答"}]
    assert ledger(service_database, runner_id) == []


async def test_skip_not_allowed_rejected_but_authoritative_skip_accepted(
        storage, controls, actors, service_database):
    parked, principal = await parked_ask_wait(
        storage, actors["a"], form=ask_form_fixture(allow_skip=False))
    runner_id = parked["runner_id"]
    with pytest.raises(RunnerError, match="FORM_SKIP_NOT_ALLOWED") as caught:
        controls.submit(principal, runner_id, structured(runner_id, wait_id_of(parked), skip=True))
    assert caught.value.status == 422
    assert caught.value.errors == [{"question_id": "form", "code": "FORM_SKIP_NOT_ALLOWED",
                                    "message": "该表单不允许整体跳过"}]
    assert ledger(service_database, runner_id) == []
    # allow_skip=true：跳过为服务端权威跳过，required 未答不阻断，固定文案回注。
    parked_skip, principal_skip = await parked_ask_wait(
        storage, actors["b"], form=ask_form_fixture(allow_skip=True))
    runner_id_skip = parked_skip["runner_id"]
    accepted, created = controls.submit(principal_skip, runner_id_skip,
                                        structured(runner_id_skip, wait_id_of(parked_skip), skip=True))
    assert created and accepted["payload"]["answer"] == SKIP_TEXT
    prepared = recovery_for(principal_skip, runner_row(service_database, runner_id_skip)).prepare(
        runner_row(service_database, runner_id_skip),
        controls.get(principal_skip, runner_id_skip, accepted["control_id"]))
    restored = prepared.states[runner_id_skip]
    assert restored.tools[CLARIFY_CALL].result["answer"] == SKIP_TEXT
    assert restored.followup_messages[0]["content"] == SKIP_TEXT


async def test_plain_text_reply_on_form_wait_stays_legacy_compatible(
        storage, controls, actors, service_database):
    parked, principal = await parked_ask_wait(storage, actors["a"], form=ask_form_fixture())
    runner_id = parked["runner_id"]
    request = RunnerControl(client_request_id="wp2-plain-1", action="reply",
                            target_execution_id=runner_id, wait_id=wait_id_of(parked),
                            answer="我想直接文字说明：只归档第一项")
    accepted, created = controls.submit(principal, runner_id, request)
    assert created
    # 纯文本兼容路径：answer 直纳，不做表单校验，payload 与投影不新增表单键。
    assert accepted["payload"]["answer"] == "我想直接文字说明：只归档第一项"
    assert "form" not in accepted["payload"] and "answers" not in accepted["payload"]
    prepared = recovery_for(principal, runner_row(service_database, runner_id)).prepare(
        runner_row(service_database, runner_id),
        controls.get(principal, runner_id, accepted["control_id"]))
    restored = prepared.states[runner_id]
    assert restored.tools[CLARIFY_CALL].result["answer"] == "我想直接文字说明：只归档第一项"
    inputs = runner_row(service_database, runner_id)["public_snapshot"]["supplementalInputs"]
    assert inputs[0]["text"] == "我想直接文字说明：只归档第一项"
    assert "form" not in inputs[0] and "answers" not in inputs[0]


async def test_all_empty_answers_is_legitimate_submission_when_nothing_required(
        storage, controls, actors, service_database):
    parked, principal = await parked_ask_wait(
        storage, actors["a"], form=ask_form_fixture(required_q1=False))
    runner_id = parked["runner_id"]
    accepted, created = controls.submit(principal, runner_id,
                                        structured(runner_id, wait_id_of(parked), answers=[]))
    assert created
    assert accepted["payload"]["answer"] == (
        "Q1【归档范围】（未回答）\nQ2【后续独立事项】（未回答）\nQ3【补充说明】（未回答）")


async def test_structured_submission_on_formless_clarify_is_422_without_side_effects(
        storage, controls, actors, service_database):
    """无 form 的旧 clarify 上的结构化提交：422 拒绝，不按空 reply 静默受理。

    原 clarify 路径只认非空 answer 文本；answers/skip 在此无法解释，若静默
    受理会丢弃结构化答案并消费 wait，客户端失去用原键重提的机会（§4.2/§5.4）。
    """
    parked, principal = await parked_ask_wait(storage, actors["a"], form=None)
    runner_id = parked["runner_id"]
    wait_id = wait_id_of(parked)
    with pytest.raises(RunnerError, match="FORM_ANSWER_INVALID") as caught:
        controls.submit(principal, runner_id,
                        structured(runner_id, wait_id, answers=[answer("q1", ["q1o1"])],
                                   key="wp2-formless-answers"))
    assert caught.value.status == 422
    assert caught.value.errors == [{"question_id": "form", "code": "FORM_ANSWER_INVALID",
                                    "message": "当前等待不包含提问表单，不能提交结构化答案"}]
    with pytest.raises(RunnerError, match="FORM_SKIP_NOT_ALLOWED") as caught:
        controls.submit(principal, runner_id,
                        structured(runner_id, wait_id, skip=True, key="wp2-formless-skip"))
    assert caught.value.status == 422
    assert caught.value.errors == [{"question_id": "form", "code": "FORM_SKIP_NOT_ALLOWED",
                                    "message": "当前等待不包含提问表单，不能整体跳过"}]
    # 不创建 control、不消费 wait、不改任何持久状态；原键仍可用于纯文本重提。
    assert ledger(service_database, runner_id) == []
    current = runner_row(service_database, runner_id)
    assert current["status"] == "waiting" and current["resume_control_id"] is None
    assert current["checkpoint"] == parked["checkpoint"]
    accepted, created = controls.submit(
        principal, runner_id,
        RunnerControl(client_request_id="wp2-formless-answers", action="reply",
                      target_execution_id=runner_id, wait_id=wait_id, answer="纯文本重提"))
    assert created and accepted["status"] == "accepted"


async def test_acceptance_normalizes_question_id_and_duplicate_keys(
        storage, controls, actors, service_database):
    parked, principal = await parked_ask_wait(storage, actors["a"], form=ask_form_fixture())
    runner_id = parked["runner_id"]
    accepted, created = controls.submit(principal, runner_id, structured(
        runner_id, wait_id_of(parked), answers=[answer("Q1", ["q1o2", "q1o2", "q1o2"])]))
    assert created
    snapshot = runner_row(service_database, runner_id)["public_snapshot"]
    entry = snapshot["supplementalInputs"][0]
    # 归一化投影：qid 小写、重复 key 去重；canonical 同源只写一次 label。
    assert entry["answers"] == [{"question_id": "q1", "selected_keys": ["q1o2"], "text": None}]
    assert entry["text"] == ("Q1【归档范围】wecom_kf 限频修复\nQ2【后续独立事项】（未回答）\n"
                             "Q3【补充说明】（未回答）")
    assert accepted["payload"]["answer"] == entry["text"]
