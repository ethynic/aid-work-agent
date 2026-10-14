"""WP0 spike：root 执行（MASTER/STANDALONE）clarify 直接进入等待的内核级验证。

docs/plans/plan-ask-user-interactive-question.md §WP0 的临时用例：验证
controls.py root 分支（WP0 临时放开）产出的等待能走通
引擎 WAITING → wait 身份装饰 → reply 恢复应用 → 引擎续跑至 final_answer。
正式 ask_user 实现归 WP3，届时本文件用例随正式分支收敛重跑。

真实/假件边界：
- 真实：AgentEngine 循环、ControlToolAdapter（WP0 修改的分支）、
  ToolControlSet 装配的真实 ClarifyTool、DurableControl.decorate_wait
  （wait_id/target_execution_id/公开问题投影）、RecoveryCoordinator.prepare
  （reply 校验与应用）、ExecutionState checkpoint/restore。
- 假件：模型端口（罐头响应）、引擎 control/observer 端口（记录型）、
  ControlToolAdapter 的 skills/registry 等未涉分支依赖（clarify 路径不触达）、
  RecoveryCoordinator 的 authorizer/profiles（授权与 DB 边界由集成层覆盖）。
"""

import json
from types import SimpleNamespace

from src.core.agent_engine import AgentEngine, AgentMode, DispatchResult, ExecutionState, Outcome
from src.core.agent_engine.contracts import Identity
from src.services.agent_runner.durable_control import DurableControl
from src.services.agent_runner.recovery import RecoveryCoordinator
from src.services.agent_runner.runtime.controls import ControlToolAdapter
from src.tools.context import ToolExecutionContext
from src.tools.control_set import ControlToolDependencies, ToolControlSet

RUNNER_ID = "runner-wp0"
ANSWER = "Fictional city Shanghai"


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
        resolve=lambda profile: (None, "fingerprint")))
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
