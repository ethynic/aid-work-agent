"""root 执行（MASTER/STANDALONE）clarification 等待的内核级行为用例。

docs/system/design-ask-user-interactive-question.md §11.1（WP0 已验证结论）的
正式行为用例（WP3 正式实现，文件名沿用 WP0 沉淀）：controls.py 的 clarify 与
ask_user 分支产出的等待能走通引擎 WAITING → wait 身份装饰（含 form.wait_id
注入）→ reply 恢复应用 → 引擎续跑至 final_answer。

真实/假件边界：
- 真实：AgentEngine 循环、ControlToolAdapter、ToolControlSet 装配的真实
  ClarifyTool/AskUserTool、DurableControl.decorate_wait（wait_id/
  target_execution_id/form.wait_id/公开问题投影）、RecoveryCoordinator.prepare
  （reply 校验与应用）、ExecutionState checkpoint/restore。
- 假件：模型端口（罐头响应）、引擎 control/observer 端口（记录型）、
  ControlToolAdapter 的 skills/registry 等未涉分支依赖（clarify/ask_user 路径
  不触达）、RecoveryCoordinator 的 authorizer/profiles（授权与 DB 边界由集成层
  test_root_clarify_wait_recovery_wp0 覆盖）。
"""

import json
from types import SimpleNamespace

from src.core.agent_engine import AgentEngine, AgentMode, DispatchResult, ExecutionState, Outcome
from src.core.agent_engine.contracts import Identity
from src.services.agent_runner import ask_form
from src.services.agent_runner.durable_control import DurableControl
from src.services.agent_runner.recovery import RecoveryCoordinator
from src.services.agent_runner.runtime.controls import ControlToolAdapter
from src.tools.context import ToolExecutionContext
from src.tools.control_set import ControlToolDependencies, ToolControlSet

RUNNER_ID = "runner-wp0"
ANSWER = "Fictional city Shanghai"
CLARIFY_CALL = "root-wp0-clarify"
ASK_CALL = "root-wp0-ask"

ASK_ARGUMENTS = {
    "title": "归档范围",
    "intro": "请确认归档范围。",
    "questions": [
        {"prompt": "以下哪些任务移入 ideas_finished.md？", "type": "multi",
         "required": True, "max_select": 2,
         "options": [{"label": "queue_order 序列自愈", "description": "代码+测试+CR 完成"},
                     {"label": "wecom_kf 限频修复"},
                     {"label": "file_assets 观察项"}],
         "allow_custom_text": True},
        {"prompt": "还有什么需要补充？", "type": "text", "placeholder": "输入你的回答…"},
    ],
    "allow_skip": True,
}
ASK_ANSWERS = [
    {"question_id": "q1", "selected_keys": ["q1o2"],
     "text": "file_assets 建议观察一周再归档"},
]


def completion(text="", calls=None):
    return {"content": text, "tool_calls": calls or [],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3}}


def tool_call(call_id, name, arguments):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)}}


class Model:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    async def complete(self, current, call_id, definitions):
        self.requests.append((current.checkpoint(), call_id))
        return self.responses.pop(0)


class Port:
    """把 ControlToolAdapter 暴露成引擎 tools 端口（clarify 路径不触达其余分支）。"""

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
    def __init__(self):
        self.saved = []
        self.events = []

    async def save(self, state, boundary):
        self.saved.append(boundary)

    def command(self):
        return None

    async def event(self, state, event):
        self.events.append(event)

    async def model_usage(self, state, fact):
        pass


def wp0_adapter(*, role=AgentMode.MASTER, task_record=None):
    controls = ToolControlSet(ControlToolDependencies(
        plan_manager=None, skill_registry=SimpleNamespace(), skill_executor=None,
        tool_registry=SimpleNamespace()))
    context = ToolExecutionContext(tenant_id="tenant-wp0", user_id="user-wp0",
                                   session_id="session-wp0", env_vars={})
    return ControlToolAdapter(
        controls=controls, skills=SimpleNamespace(check=lambda state, name: None),
        registry=SimpleNamespace(get=lambda name: None), plan_manager=None,
        clarification_store=SimpleNamespace(save=lambda identity, payload: None,
                                            clear=lambda identity: None),
        context=context, user_input="wp0 request", task_record=task_record,
        durable_owner=True)


def root_state(role=AgentMode.MASTER):
    state = ExecutionState(identity=Identity("tenant-wp0", "user-wp0", "session-wp0"),
                           execution_id=RUNNER_ID, role=role, system_prompt="wp0",
                           messages=[{"role": "user", "content": "wp0 request"}], initial_len=1)
    # 生产 root 状态由 RuntimeExecution 装配时携带 profile 标识，恢复校验依赖它。
    state.profile_id = "main"
    return state


async def run_clarify_turn(state, adapter):
    model = Model(completion("", [tool_call("root-wp0-clarify", "clarify",
                                            {"question": "Which fictional city?",
                                             "missing_info": ["city"]})]))
    recording = Recording()
    async for _ in AgentEngine().run(state, model, Port(adapter), recording, recording):
        pass
    return model


async def test_root_clarify_enters_waiting_without_touching_task_record():
    """a) root（MASTER）clarify 产生 clarification wait；不写子执行任务状态。"""
    clarifications = []
    record = SimpleNamespace(request_clarification=clarifications.append)
    state = root_state(role=AgentMode.MASTER)
    model = await run_clarify_turn(state, wp0_adapter(role=AgentMode.MASTER, task_record=record))
    assert state.outcome == Outcome.WAITING
    assert state.waiting == {"kind": "clarification", "question": "Which fictional city?",
                             "missing_info": ["city"], "tool_call_id": "root-wp0-clarify"}
    fact = state.tools["root-wp0-clarify"]
    assert fact.phase == "waiting" and fact.success is True
    assert fact.result == {"success": True, "question": "Which fictional city?",
                           "missing_info": ["city"]}
    # 原有条件保持：request_clarification 仅属于子执行路径，root 不得调用
    assert clarifications == []
    assert len(model.requests) == 1


async def test_standalone_role_behaves_identically_to_master():
    """a) STANDALONE root 与 MASTER 同样等待（设计覆盖两种 root 角色）。"""
    state = root_state(role=AgentMode.STANDALONE)
    await run_clarify_turn(state, wp0_adapter(role=AgentMode.STANDALONE))
    assert state.outcome == Outcome.WAITING
    assert state.waiting["kind"] == "clarification"
    assert state.waiting["tool_call_id"] == "root-wp0-clarify"


async def test_subagent_clarify_still_records_task_clarification():
    """原有子执行路径不回归：task_record.request_clarification 照常调用。"""
    clarifications = []
    record = SimpleNamespace(request_clarification=clarifications.append)
    state = root_state(role=AgentMode.SUBAGENT)
    await run_clarify_turn(state, wp0_adapter(role=AgentMode.SUBAGENT, task_record=record))
    assert state.outcome == Outcome.WAITING
    assert clarifications == ["Which fictional city?"]


async def test_legacy_non_durable_root_clarify_keeps_continuing_model():
    """意外发现收敛：legacy（非 durable）链路没有 reply 恢复消费方，root clarify
    保持原「继续模型」行为，不产生 WAITING。"""
    state = root_state(role=AgentMode.MASTER)
    adapter = wp0_adapter(role=AgentMode.MASTER)
    adapter.durable_owner = False
    model = Model(completion("", [tool_call("root-wp0-clarify", "clarify",
                                            {"question": "Which fictional city?",
                                             "missing_info": ["city"]})]),
                  completion("legacy-root-answers-directly"))
    recording = Recording()
    async for _ in AgentEngine().run(state, model, Port(adapter), recording, recording):
        pass
    assert state.outcome == Outcome.COMPLETED
    assert state.waiting is None
    assert state.output == "legacy-root-answers-directly"
    assert len(model.requests) == 2


def ask_call(call_id=ASK_CALL):
    return {"id": call_id, "type": "function",
            "function": {"name": "ask_user", "arguments": json.dumps(ASK_ARGUMENTS)}}


async def run_ask_turn(state, adapter):
    model = Model(completion("", [ask_call()]))
    recording = Recording()
    async for _ in AgentEngine().run(state, model, Port(adapter), recording, recording):
        pass
    return model


async def test_root_ask_user_enters_waiting_with_generated_form():
    """a) root（MASTER）ask_user 产生 clarification wait 且 wait 附服务端生成的 form。"""
    state = root_state(role=AgentMode.MASTER)
    model = await run_ask_turn(state, wp0_adapter())
    assert state.outcome == Outcome.WAITING
    waiting = state.waiting
    # 等待问题取 title（缺省取首题 prompt），截断到 50 字
    assert waiting["question"] == "归档范围"
    assert waiting["tool_call_id"] == ASK_CALL
    form = waiting["form"]
    assert form["form_id"].startswith("frm_")
    assert [question["id"] for question in form["questions"]] == ["q1", "q2"]
    assert [option["key"] for option in form["questions"][0]["options"]] == ["q1o1", "q1o2", "q1o3"]
    assert form["allow_skip"] is True and form["expires_at"] is None
    assert len(model.requests) == 1
    # 工具结果携带同一 form；阻塞等待语义由 controls.py 赋予
    fact = state.tools[ASK_CALL]
    assert fact.phase == "waiting" and fact.success is True
    assert fact.result["form"]["form_id"] == form["form_id"]


async def test_root_ask_user_does_not_touch_task_record():
    """a) root ask_user 不写子执行澄清任务状态（与 clarify root 语义一致，§11.1）。"""
    clarifications = []
    record = SimpleNamespace(request_clarification=clarifications.append)
    state = root_state(role=AgentMode.MASTER)
    await run_ask_turn(state, wp0_adapter(role=AgentMode.MASTER, task_record=record))
    assert state.outcome == Outcome.WAITING
    assert clarifications == []


async def test_subagent_ask_user_waits_and_records_task_clarification():
    """a) 子执行 ask_user 同样等待并附 form；task_record.request_clarification 照常调用。"""
    clarifications = []
    record = SimpleNamespace(request_clarification=clarifications.append)
    state = root_state(role=AgentMode.SUBAGENT)
    await run_ask_turn(state, wp0_adapter(role=AgentMode.SUBAGENT, task_record=record))
    assert state.outcome == Outcome.WAITING
    assert state.waiting["form"]["form_id"].startswith("frm_")
    assert clarifications == ["归档范围"]


async def test_legacy_non_durable_root_ask_user_keeps_continuing_model():
    """a) legacy 非 durable 链路没有 reply 恢复消费方，root ask_user 保持「继续模型」。"""
    state = root_state(role=AgentMode.MASTER)
    adapter = wp0_adapter(role=AgentMode.MASTER)
    adapter.durable_owner = False
    model = Model(completion("", [ask_call()]), completion("legacy-root-answers-ask-directly"))
    recording = Recording()
    async for _ in AgentEngine().run(state, model, Port(adapter), recording, recording):
        pass
    assert state.outcome == Outcome.COMPLETED
    assert state.waiting is None
    assert state.output == "legacy-root-answers-ask-directly"
    assert len(model.requests) == 2


def decorated_control():
    return DurableControl(SimpleNamespace(), SimpleNamespace(runner_id=RUNNER_ID),
                          {"revision": 1, "public_snapshot": {}, "cancel_requested": False,
                           "status": "running"})


async def test_durable_wait_identity_and_public_question_apply_to_root():
    """a) wait_id/target_execution_id 装饰与公开问题投影对 root 照常生效。"""
    state = root_state()
    await run_clarify_turn(state, wp0_adapter())
    control = decorated_control()
    control.decorate_wait(state)
    waiting = state.waiting
    assert waiting["wait_id"].startswith("wait_")
    assert waiting["target_execution_id"] == RUNNER_ID
    questions = control.snapshot["clarificationQuestions"]
    assert len(questions) == 1
    assert questions[0]["wait_id"] == waiting["wait_id"]
    assert questions[0]["text"] == "Which fictional city?"


async def test_decorate_injects_wait_id_into_root_ask_form():
    """a) form.wait_id 由 decorate_wait 统一注入；无 form 的旧 clarify 零变化。"""
    state = root_state()
    await run_ask_turn(state, wp0_adapter())
    control = decorated_control()
    control.decorate_wait(state)
    waiting = state.waiting
    assert waiting["form"]["wait_id"] == waiting["wait_id"]
    assert control.snapshot["clarificationQuestions"][0]["form_id"] == waiting["form"]["form_id"]
    state = root_state()
    await run_clarify_turn(state, wp0_adapter())
    control = decorated_control()
    control.decorate_wait(state)
    assert "form" not in state.waiting


async def recovered_state():
    """b) 引擎产出 → 装饰 → reply 恢复应用，返回（准备结果, wait_id）。"""
    state = root_state()
    await run_clarify_turn(state, wp0_adapter())
    control = decorated_control()
    control.decorate_wait(state)
    wait_id = state.waiting["wait_id"]
    # 生产 durable_owner 路径在派发前落 configuration_fingerprint，恢复校验依赖它。
    from src.services.agent_runner.runtime_manifest import configuration_fingerprint
    state.resources["configuration_fingerprint"] = configuration_fingerprint("fingerprint")
    checkpoint = {"execution": state.checkpoint()}
    row = {"runner_id": RUNNER_ID, "profile_id": "main", "profile_fingerprint": "fingerprint",
           "checkpoint": checkpoint}
    reply_control = {"control_id": "reply-control", "action": "reply", "accepted_at": "2026-10-14",
                     "payload": {"action": "reply", "answer": ANSWER, "attachments": [],
                                 "wait_id": wait_id, "target_execution_id": RUNNER_ID}}
    principal = SimpleNamespace(identity=state.identity)
    authorizer = SimpleNamespace(authorize_persisted=lambda row, **kwargs: principal,
                                 assert_credit=lambda principal: None)
    coordinator = RecoveryCoordinator(authorizer, SimpleNamespace(
        resolve=lambda profile, tenant=None: (None, "fingerprint")))
    prepared = coordinator.prepare(row, reply_control)
    return prepared, wait_id


async def test_reply_application_on_root_matches_child_contract():
    """b) 恢复校验与应用路径与子执行一致：answer 回注为 clarify 工具结果。"""
    prepared, wait_id = await recovered_state()
    root = prepared.states[RUNNER_ID]
    assert root.outcome == Outcome.RUNNING and root.waiting is None
    fact = root.tools["root-wp0-clarify"]
    assert fact.phase == "completed" and fact.success is True
    assert fact.result == {"success": True, "answer": ANSWER, "attachments": [],
                           "wait_id": wait_id}
    assert root.followup_messages[0]["content"] == ANSWER
    assert prepared.checkpoint["applied_control_id"] == "reply-control"
    assert prepared.checkpoint["supplemental_inputs"][0]["text"] == ANSWER


async def test_root_resumes_to_final_answer_after_reply():
    """b)+d) 续跑至完成：clarify 工具结果与答案进入对话，final_answer 输出不受影响。"""
    prepared, _ = await recovered_state()
    resumed = ExecutionState.restore(prepared.checkpoint["execution"])
    model = Model(completion("root-final-after-clarify"))
    recording = Recording()
    async for _ in AgentEngine().run(resumed, model, Port(wp0_adapter()), recording, recording):
        pass
    assert resumed.outcome == Outcome.COMPLETED
    assert resumed.output == "root-final-after-clarify"
    assert len(model.requests) == 1
    messages = model.requests[0][0]["messages"]
    tool_messages = [m for m in messages if m.get("role") == "tool"]
    assert len(tool_messages) == 1
    payload = json.loads(tool_messages[0]["content"])
    assert payload == {"success": True, "answer": ANSWER, "attachments": [], "wait_id": payload["wait_id"]}
    assert any(m.get("role") == "user" and m.get("content") == ANSWER for m in messages)


async def recovered_ask_state():
    """b) ask_user 引擎产出 → 装饰（含 form.wait_id）→ 受理后 canonical 回注。

    返回（准备结果, wait_id, canonical）。canonical 由 ask_form 权威生成——与
    真实受理层（control_repository）写入 reply 载荷 answer 键的文本同源。
    """
    state = root_state()
    await run_ask_turn(state, wp0_adapter())
    control = decorated_control()
    control.decorate_wait(state)
    form = state.waiting["form"]
    wait_id = state.waiting["wait_id"]
    # 生产 durable_owner 路径在派发前落 configuration_fingerprint，恢复校验依赖它。
    from src.services.agent_runner.runtime_manifest import configuration_fingerprint
    state.resources["configuration_fingerprint"] = configuration_fingerprint("fingerprint")
    checkpoint = {"execution": state.checkpoint()}
    row = {"runner_id": RUNNER_ID, "profile_id": "main", "profile_fingerprint": "fingerprint",
           "checkpoint": checkpoint}
    canonical = ask_form.canonical_text(form, ask_form.normalize_answers(ASK_ANSWERS))
    reply_control = {"control_id": "reply-control", "action": "reply", "accepted_at": "2026-10-14",
                     "payload": {"action": "reply", "answer": canonical, "attachments": [],
                                 "wait_id": wait_id, "target_execution_id": RUNNER_ID}}
    principal = SimpleNamespace(identity=state.identity)
    authorizer = SimpleNamespace(authorize_persisted=lambda row, **kwargs: principal,
                                 assert_credit=lambda principal: None)
    coordinator = RecoveryCoordinator(authorizer, SimpleNamespace(
        resolve=lambda profile, tenant=None: (None, "fingerprint")))
    prepared = coordinator.prepare(row, reply_control)
    return prepared, wait_id, canonical


async def test_structured_reply_applies_canonical_to_ask_user_result():
    """b) 结构化受理生成的 canonical 经 reply 载荷 answer 键回注为 ask_user 工具结果。"""
    prepared, wait_id, canonical = await recovered_ask_state()
    root = prepared.states[RUNNER_ID]
    assert root.outcome == Outcome.RUNNING and root.waiting is None
    fact = root.tools[ASK_CALL]
    assert fact.phase == "completed" and fact.success is True
    assert fact.result == {"success": True, "answer": canonical, "attachments": [],
                           "wait_id": wait_id}
    assert root.followup_messages[0]["content"] == canonical
    assert prepared.checkpoint["applied_control_id"] == "reply-control"
    assert prepared.checkpoint["supplemental_inputs"][0]["text"] == canonical


async def test_root_resumes_to_final_answer_after_structured_ask_reply():
    """b)+d) 续跑至完成：ask_user 工具结果为 canonical，final_answer 输出不受影响。"""
    prepared, _, canonical = await recovered_ask_state()
    resumed = ExecutionState.restore(prepared.checkpoint["execution"])
    model = Model(completion("root-final-after-ask"))
    recording = Recording()
    async for _ in AgentEngine().run(resumed, model, Port(wp0_adapter()), recording, recording):
        pass
    assert resumed.outcome == Outcome.COMPLETED
    assert resumed.output == "root-final-after-ask"
    assert len(model.requests) == 1
    messages = model.requests[0][0]["messages"]
    tool_messages = [m for m in messages if m.get("role") == "tool"]
    assert len(tool_messages) == 1
    payload = json.loads(tool_messages[0]["content"])
    assert payload == {"success": True, "answer": canonical, "attachments": [],
                       "wait_id": payload["wait_id"]}
    assert any(m.get("role") == "user" and m.get("content") == canonical for m in messages)
