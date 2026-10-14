"""ChannelRunnerAgent 结构化提问入站单管线与出站分段单元测试（WP5，设计 §7.2）。

真实/假件边界：
- 真实：ChannelRunnerAgent.process_message_sync 的入站分流（跳过/Q 编号/重发/
  自然语言三分法）、422 逐题明细转述、轮询中的应用边界清理、form 渲染分段出站。
- 假件：RunnerServiceClient 注入假件（ canned 响应 + 调用记录）；ask_delivery
  的 DB 事实函数打桩（单独在 test_channel_ask_segments_send.py 覆盖真实读写）。
"""

import pytest

from src.channels.runner_agent import ChannelAskResponse, ChannelRunnerAgent
from src.services.agent_runner.contracts import RunnerError

pytestmark = pytest.mark.channels


def _form():
    return {
        "form_id": "frm_1",
        "title": "归档范围",
        "intro": "除成本治理外，以下开发已完成。",
        "allow_skip": True,
        "questions": [
            {
                "id": "q1",
                "tag": "归档范围",
                "prompt": "以下哪些任务移入 ideas_finished.md？",
                "type": "multi",
                "options": [
                    {"key": "q1o1", "label": "queue_order 序列自愈", "description": ""},
                    {"key": "q1o2", "label": "wecom_kf 限频修复", "description": ""},
                    {"key": "q1o3", "label": "飞书钉钉接入 AgentRunner", "description": ""},
                ],
            },
        ],
    }


def _waiting_row(wait=None):
    wait = wait if wait is not None else {
        "kind": "clarification", "question": "请选择归档范围",
        "wait_id": "wait_1", "target_execution_id": "exec_1", "form": _form(),
    }
    return {
        "runner_id": "r-1", "status": "waiting",
        "snapshot": {"waiting": wait},
    }


class FakeRunnerClient:
    """RunnerServiceClient 假件：按 (method, path 前缀) 路由，记录全部调用。"""

    def __init__(self):
        self.calls = []
        self.control_result = {
            "runner": {"runner_id": "r-1", "status": "running", "snapshot": {}},
            "control": {"control_id": "c-1", "status": "accepted"},
        }
        self.control_error = None
        self.control_status = {}
        self.runner_pages = [
            {"runner": {"runner_id": "r-1", "status": "completed",
                        "result": {"output": "done"}, "snapshot": {}}},
        ]

    async def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs.get("body")))
        if path.startswith("/v1/sessions/channel/"):
            return {"active_runners": self.active_runners}
        if method == "POST" and "/controls" in path:
            if self.control_error is not None:
                raise self.control_error
            return self.control_result
        if method == "GET" and "/controls/" in path:
            control_id = path.rsplit("/", 1)[-1]
            return {"control": {"control_id": control_id,
                                "status": self.control_status.get(control_id, "accepted")}}
        if method == "GET" and path.startswith("/v1/runners/r-1"):
            page = self.runner_pages.pop(0) if self.runner_pages else self.runner_pages
            return dict(page)
        raise AssertionError(f"unexpected request {method} {path}")

    @property
    def active_runners(self):
        return self._active

    def with_waiting(self, row):
        self._active = [row]
        return self

    _active = []


def _make_agent(client):
    return ChannelRunnerAgent(source="wecom", session_id="s-1", channel_user_id="u-1",
                              channel_chat_id=None, client=client)


@pytest.fixture
def facts(monkeypatch):
    """ask_delivery 打桩：事实函数换内存记录，不触库。"""
    state = {"facts": None, "submissions": [], "cleared_submissions": [],
             "cleared_facts": [], "resends": 0}

    def load_facts(session_id, wait_id):
        return state["facts"]

    def record_submission(session_id, wait_id, control_id):
        state["submissions"].append((session_id, wait_id, control_id))

    def clear_submission(session_id, wait_id):
        state["cleared_submissions"].append(wait_id)

    def clear_facts(session_id, wait_id):
        state["cleared_facts"].append(wait_id)
        state["facts"] = None

    def record_resend(session_id, wait_id):
        state["resends"] += 1

    monkeypatch.setattr("src.channels.ask_delivery.load_facts", load_facts)
    monkeypatch.setattr("src.channels.ask_delivery.record_submission", record_submission)
    monkeypatch.setattr("src.channels.ask_delivery.clear_submission", clear_submission)
    monkeypatch.setattr("src.channels.ask_delivery.clear_facts", clear_facts)
    monkeypatch.setattr("src.channels.ask_delivery.record_resend", record_resend)
    return state


# ==================== 入站三分法 ====================


async def test_skip_submits_structured_skip(facts):
    client = FakeRunnerClient().with_waiting(_waiting_row())
    response = await _make_agent(client).process_message_sync(
        user_input="跳过", session_id="s-1")

    posts = [call for call in client.calls if call[0] == "POST" and "/controls" in call[1]]
    assert len(posts) == 1
    body = posts[0][2]
    assert body["skip"] is True and body["answer"] == ""
    assert body["wait_id"] == "wait_1" and body["target_execution_id"] == "exec_1"
    assert "answers" not in body
    assert response == "done"
    assert facts["submissions"] == [("s-1", "wait_1", "c-1")]


async def test_numbered_reply_submits_mapped_answers(facts):
    client = FakeRunnerClient().with_waiting(_waiting_row())
    await _make_agent(client).process_message_sync(
        user_input="Q1：1、3", session_id="s-1")

    body = next(call[2] for call in client.calls
                if call[0] == "POST" and "/controls" in call[1])
    assert body["answer"] == "" and "skip" not in body
    assert body["answers"] == [
        {"question_id": "q1", "selected_keys": ["q1o1", "q1o3"], "text": None},
    ]


async def test_protocol_error_corrects_once_without_submitting(facts):
    client = FakeRunnerClient().with_waiting(_waiting_row())
    response = await _make_agent(client).process_message_sync(
        user_input="Q9：1", session_id="s-1")

    # 不提交、wait 不消费：controls 零 POST，回复带纠正文案
    assert not [call for call in client.calls if call[0] == "POST"]
    assert "回复格式未能识别" in str(response)
    assert "Q9 不在当前问题中" in str(response)
    assert facts["submissions"] == []


async def test_natural_language_passes_through_as_answer(facts):
    client = FakeRunnerClient().with_waiting(_waiting_row())
    await _make_agent(client).process_message_sync(
        user_input="按默认处理，不要继续问了", session_id="s-1")

    body = next(call[2] for call in client.calls
                if call[0] == "POST" and "/controls" in call[1])
    # 永不吞：其余一切文本原样作为 answer 提交（自由文本兼容路径）
    assert body["answer"] == "按默认处理，不要继续问了"
    assert "answers" not in body and "skip" not in body
    assert facts["submissions"] == [("s-1", "wait_1", "c-1")]


async def test_legacy_clarify_without_form_passes_through(facts):
    wait = {"kind": "clarification", "question": "旧式追问", "wait_id": "wait_0",
            "target_execution_id": "exec_0"}
    client = FakeRunnerClient().with_waiting(_waiting_row(wait))
    await _make_agent(client).process_message_sync(user_input="继续", session_id="s-1")

    body = next(call[2] for call in client.calls
                if call[0] == "POST" and "/controls" in call[1])
    assert body["answer"] == "继续"
    # 旧 clarify 无 form：不登记发送事实（重发边界只针对结构化提问）
    assert facts["submissions"] == []


async def test_server_422_errors_rendered_as_correction(facts):
    # 本地解析通过（Q1 已答），但 form 含必答的 Q2 未答 → 服务端受理 422 + errors；
    # 渠道转述逐题明细，wait 不消费，用户修正后可直接重提。
    form = _form()
    form["questions"].append({"id": "q2", "tag": "后续", "prompt": "补充说明？",
                              "type": "text", "required": True})
    client = FakeRunnerClient().with_waiting(_waiting_row())
    client.control_error = RunnerError("FORM_ANSWER_INVALID", 422, errors=[
        {"question_id": "q2", "code": "FORM_ANSWER_INVALID", "message": "该题为必答"}])
    response = await _make_agent(client).process_message_sync(
        user_input="Q1：1", session_id="s-1")

    assert "提交的答案未通过校验" in str(response)
    assert "该题为必答" in str(response)
    # 提交确实发出过（服务端权威拒绝，非本地拦截）
    assert [call for call in client.calls if call[0] == "POST"]


# ==================== 重发指令与受理/应用边界 ====================


async def test_resend_without_facts_returns_hint(facts):
    client = FakeRunnerClient().with_waiting(_waiting_row())
    response = await _make_agent(client).process_message_sync(
        user_input="重发", session_id="s-1")
    assert not [call for call in client.calls if call[0] == "POST"]
    assert "没有可重发的未发送内容" in str(response)


async def test_resend_sends_only_missing_segments(facts):
    facts["facts"] = {"segments": ["seg-0", "seg-1", "seg-2"], "sent": [0],
                      "last_error": None, "retries": 0, "submitted": None}
    client = FakeRunnerClient().with_waiting(_waiting_row())
    response = await _make_agent(client).process_message_sync(
        user_input="重发", session_id="s-1")

    # 该条不作答案：不提交 control，只续传缺段（index 对齐首次分段）
    assert not [call for call in client.calls if call[0] == "POST"]
    assert isinstance(response, ChannelAskResponse)
    payload = response.ask_segments
    assert payload["resend"] is True and payload["wait_id"] == "wait_1"
    assert [item["index"] for item in payload["segments"]] == [1, 2]
    assert "seg-0" not in str(response) and "seg-1" in str(response)
    assert facts["resends"] == 1


async def test_resend_paused_while_control_accepted_not_applied(facts):
    facts["facts"] = {"segments": ["seg-0", "seg-1"], "sent": [0], "last_error": None,
                      "retries": 0, "submitted": {"control_id": "c-9"}}
    client = FakeRunnerClient().with_waiting(_waiting_row())
    client.control_status["c-9"] = "accepted"
    response = await _make_agent(client).process_message_sync(
        user_input="简化", session_id="s-1")

    # 受理未应用期间不执行重发（答案可能即将应用），也不清除事实
    assert "暂不重发" in str(response)
    assert facts["resends"] == 0 and facts["cleared_submissions"] == []


async def test_resend_allowed_after_control_rejected(facts):
    facts["facts"] = {"segments": ["seg-0", "seg-1"], "sent": [], "last_error": None,
                      "retries": 0, "submitted": {"control_id": "c-9"}}
    client = FakeRunnerClient().with_waiting(_waiting_row())
    client.control_status["c-9"] = "rejected"
    response = await _make_agent(client).process_message_sync(
        user_input="重发", session_id="s-1")

    assert isinstance(response, ChannelAskResponse)
    assert [item["index"] for item in response.ask_segments["segments"]] == [0, 1]
    assert facts["cleared_submissions"] == ["wait_1"]


async def test_resend_after_consumed_clears_facts(facts):
    facts["facts"] = {"segments": ["seg-0"], "sent": [], "last_error": None,
                      "retries": 0, "submitted": {"control_id": "c-9"}}
    client = FakeRunnerClient().with_waiting(_waiting_row())
    client.control_status["c-9"] = "consumed"
    response = await _make_agent(client).process_message_sync(
        user_input="重发", session_id="s-1")

    assert "无需重发" in str(response)
    assert facts["cleared_facts"] == ["wait_1"]


# ==================== 出站分段与应用边界 ====================


async def test_new_question_during_poll_renders_segments(facts):
    client = FakeRunnerClient().with_waiting(_waiting_row())
    new_wait = {
        "kind": "clarification", "question": "第二轮", "wait_id": "wait_2",
        "target_execution_id": "exec_2", "form": _form(),
    }
    client.runner_pages = [
        {"runner": {"runner_id": "r-1", "status": "waiting",
                    "snapshot": {"waiting": new_wait}}},
    ]
    response = await _make_agent(client).process_message_sync(
        user_input="好的", session_id="s-1")

    # 新一轮提问（form）渲染为分段结构出站，不预判送达
    assert isinstance(response, ChannelAskResponse)
    payload = response.ask_segments
    assert payload["wait_id"] == "wait_2" and payload["resend"] is False
    assert payload["form"]["form_id"] == "frm_1"
    assert payload["segments"] and all(item["text"] for item in payload["segments"])
    assert "Q1【归档范围】" in str(response)


async def test_wait_without_form_falls_back_to_question_text(facts):
    wait = {"kind": "clarification", "question": "旧式追问", "wait_id": "wait_3",
            "target_execution_id": "exec_3"}
    client = FakeRunnerClient().with_waiting(_waiting_row())
    client.runner_pages = [
        {"runner": {"runner_id": "r-1", "status": "waiting",
                    "snapshot": {"waiting": wait}}},
    ]
    response = await _make_agent(client).process_message_sync(
        user_input="好的", session_id="s-1")
    assert str(response) == "旧式追问"
    assert getattr(response, "ask_segments", None) is None


async def test_send_facts_cleared_once_wait_applied(facts):
    client = FakeRunnerClient().with_waiting(_waiting_row())
    facts["facts"] = {"segments": ["seg-0"], "sent": [], "last_error": None,
                      "retries": 0, "submitted": None}
    await _make_agent(client).process_message_sync(user_input="跳过", session_id="s-1")

    # 答案应用成功（轮询发现离开 waiting）→ 清理该 form 剩余发送记录
    assert facts["cleared_facts"] == ["wait_1"]


async def test_rejected_reply_clears_inflight_submission(facts):
    client = FakeRunnerClient().with_waiting(_waiting_row())
    client.control_result = {
        "runner": {"runner_id": "r-1", "status": "waiting",
                   "snapshot": {"waiting": _waiting_row()["snapshot"]["waiting"]}},
        "control": {"control_id": "c-1", "status": "accepted"},
    }
    client.runner_pages = []
    # 控制 GET 返回 rejected → 沿既有分支抛 CHANNEL_REPLY_REJECTED，
    # 且在途提交标记被清除（允许修正/重发）
    class RejectedClient(FakeRunnerClient):
        async def request(self, method, path, **kwargs):
            if method == "GET" and "/controls/c-1" in path:
                self.calls.append((method, path, kwargs.get("body")))
                return {"control": {"control_id": "c-1", "status": "rejected"}}
            return await super().request(method, path, **kwargs)

    replaced = RejectedClient()
    replaced._active = client._active
    replaced.control_result = client.control_result
    with pytest.raises(RunnerError):
        await _make_agent(replaced).process_message_sync(user_input="跳过", session_id="s-1")
    assert facts["cleared_submissions"] == ["wait_1"]
