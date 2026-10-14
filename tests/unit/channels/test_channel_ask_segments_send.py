"""make_send_response 结构化提问分段发送接线测试（WP5，设计 §7.2 / 计划 §WP5 P1-2）。

覆盖：
- 分段→发送：逐段调用渠道对应发送方法（wecom=markdown / dingtalk=sampleMarkdown /
  RPA 兜底=send_message），分段信息经 UnifiedResponse 之外的 ask_segments 载荷
  传入，发送事实在实际发送确认处记录（成功一段记一段，失败停止并记原因）；
- 渠道配置覆盖：按 adapter 现有 max_bytes 重排分段；
- wecom_kf 预算核算：N×A（提问轮次不为 final 预留），N>A 紧凑重算，仍不足
  只发前段并附事实提示（不静默截断）；
- ask_delivery 发送事实：metadata 读改写保留其他键（fake manager 承载）。
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.channels.runner_agent import ChannelAskResponse
from src.channels.session import ChannelSessionManager
from src.channels.wecom_kf.budget import WeComKfReplyBudget

pytestmark = pytest.mark.channels


def _form(option_count=2, description="这是一段较长的选项描述用于撑大渲染字节"):
    return {
        "form_id": "frm_1", "title": "归档范围", "intro": "除成本治理外，以下开发已完成。",
        "allow_skip": True,
        "questions": [{
            "id": "q1", "tag": "归档范围", "prompt": "以下哪些任务移入 ideas_finished.md？",
            "type": "multi",
            "options": [{"key": f"q1o{i}", "label": f"任务 {i}", "description": description}
                        for i in range(1, option_count + 1)],
        }],
    }


def _payload(segments, *, resend=False, form=None, limit=4000, wait_id="wait_1",
             session_id="s-1"):
    return {
        "session_id": session_id, "wait_id": wait_id, "resend": resend,
        "form": form, "limit_bytes": limit,
        "segments": [{"index": i, "text": t} for i, t in enumerate(segments)],
    }


def _manager():
    return ChannelSessionManager.__new__(ChannelSessionManager)


def _facts_stub(monkeypatch):
    calls = {"init": [], "sent": [], "failed": [], "resend": 0}

    monkeypatch.setattr("src.channels.ask_delivery.init_facts",
                        lambda sid, wid, segs: calls["init"].append(list(segs)))
    monkeypatch.setattr("src.channels.ask_delivery.record_sent",
                        lambda sid, wid, idx: calls["sent"].append(idx))
    monkeypatch.setattr("src.channels.ask_delivery.record_failure",
                        lambda sid, wid, idx, reason: calls["failed"].append(idx))
    monkeypatch.setattr("src.channels.ask_delivery.record_resend",
                        lambda sid, wid: calls.__setitem__("resend", calls["resend"] + 1))
    return calls


# ==================== 通用分段发送（wecom=markdown） ====================


def _wecom_adapter():
    # spec 限定：真实 wecom adapter 没有 send_ask_segment / send_long_message
    # 公有签名混用问题，hasattr 分发必须与真实能力一致（MagicMock 会自动造属性）
    adapter = MagicMock(spec=["send_markdown", "channel_type", "_msg_config"])
    adapter.channel_type = "wecom"
    adapter._msg_config = {"max_bytes": 2048}
    adapter.send_markdown = AsyncMock(return_value=True)
    return adapter


async def test_segments_sent_one_by_one_with_facts(monkeypatch):
    calls = _facts_stub(monkeypatch)
    adapter = _wecom_adapter()

    send = _manager().make_send_response(adapter=adapter, message_id="m1",
                                         reply_to="user-1", log_tag="[T]")
    segments = ["第一段", "第二段", "第三段"]
    ok = await send(ChannelAskResponse("\n\n".join(segments),
                                       ask_segments=_payload(segments)), [])

    assert ok is True
    assert [call.args[0] for call in adapter.send_markdown.await_args_list] == segments
    assert calls["init"] == [segments]
    assert calls["sent"] == [0, 1, 2]


async def test_segment_failure_stops_and_records_reason(monkeypatch):
    calls = _facts_stub(monkeypatch)
    adapter = _wecom_adapter()
    adapter.send_markdown = AsyncMock(side_effect=[True, False, True])

    send = _manager().make_send_response(adapter=adapter, message_id="m1",
                                         reply_to="user-1")
    segments = ["seg-0", "seg-1", "seg-2"]
    ok = await send(ChannelAskResponse("x", ask_segments=_payload(segments)), [])

    assert ok is False
    # 失败停止：只发出前两段（第二段失败），第三段不发送
    assert adapter.send_markdown.await_count == 2
    assert calls["sent"] == [0]
    assert calls["failed"] == [1]


async def test_segments_rerendered_by_adapter_limit(monkeypatch):
    calls = _facts_stub(monkeypatch)
    adapter = _wecom_adapter()
    adapter._msg_config = {"max_bytes": 200}

    send = _manager().make_send_response(adapter=adapter, message_id="m1",
                                         reply_to="user-1")
    ok = await send(ChannelAskResponse("原始", ask_segments=_payload(
        ["原始"], form=_form(option_count=3), limit=4000)), [])

    assert ok is True
    # runner_agent 用渠道默认上限渲染，session 侧以 adapter 现有配置重排：
    # 每段实际字节 ≤ adapter max_bytes，且事实记录的是重排后的分段
    sent_texts = [call.args[0] for call in adapter.send_markdown.await_args_list]
    assert len(sent_texts) > 1
    for text in sent_texts:
        assert len(text.encode("utf-8")) <= 200
    assert calls["init"] == [sent_texts]
    assert "这是一段较长的选项描述" in "\n".join(sent_texts)  # 内容不丢失


async def test_resend_sends_only_missing_indexes(monkeypatch):
    calls = _facts_stub(monkeypatch)
    adapter = _wecom_adapter()

    send = _manager().make_send_response(adapter=adapter, message_id="m1",
                                         reply_to="user-1")
    payload = _payload([], resend=True)
    payload["segments"] = [{"index": 2, "text": "seg-2"}, {"index": 4, "text": "seg-4"}]
    ok = await send(ChannelAskResponse("seg-2\n\nseg-4", ask_segments=payload), [])

    assert ok is True
    texts = [call.args[0] for call in adapter.send_markdown.await_args_list]
    assert texts == ["seg-2", "seg-4"]
    # 重发不重排、不重复初始化事实，只记新确认段与重投次数
    assert calls["init"] == [] and calls["resend"] == 1
    assert calls["sent"] == [2, 4]


# ==================== 渠道分发（dingtalk / RPA 兜底） ====================


async def test_dingtalk_dispatch_passes_conversation_type(monkeypatch):
    _facts_stub(monkeypatch)
    adapter = MagicMock(spec=["send_long_message", "channel_type"])
    adapter.send_long_message = AsyncMock(return_value=True)

    send = _manager().make_send_response(
        adapter=adapter, message_id="m1", reply_to="cid-1",
        extra_content={"conversation_type": "2"})
    await send(ChannelAskResponse("x", ask_segments=_payload(["seg-a"])), [])

    adapter.send_long_message.assert_awaited_once_with("seg-a", "cid-1", "2")


async def test_rpa_fallback_uses_send_message_with_per_segment_context(monkeypatch):
    _facts_stub(monkeypatch)
    adapter = MagicMock(spec=["send_message", "channel_type"])
    adapter.send_message = AsyncMock(return_value=True)
    pre_send_ids = []

    send = _manager().make_send_response(
        adapter=adapter, message_id="evt-1", reply_to="user-1",
        pre_send=lambda delivery_id=None: pre_send_ids.append(delivery_id))
    await send(ChannelAskResponse("x", ask_segments=_payload(["s0", "s1"])), [])

    assert adapter.send_message.await_count == 2
    # 每段独立 delivery id（幂等键分离），且正文逐段传递
    assert pre_send_ids == ["evt-1:final:seg0", "evt-1:final:seg1"]
    contents = [call.args[0].content for call in adapter.send_message.await_args_list]
    assert [c["text"] for c in contents] == ["s0", "s1"]


# ==================== wecom_kf 预算核算 ====================


class _KfStub:
    """绑定真实 WeComKfAdapter.send_ask_segment 的最小桩（预算扣减逻辑真实执行）。"""

    send_ask_segment = __import__("src.channels.wecom_kf.adapter", fromlist=["WeComKfAdapter"]).WeComKfAdapter.send_ask_segment

    def __init__(self):
        self.api_client = MagicMock()
        self.api_client.send_msg = AsyncMock(return_value={"errcode": 0})
        self.current_open_kfid = "kf-1"


def _kf_adapter():
    return _KfStub()


async def test_kf_segments_use_plain_text_within_budget(monkeypatch):
    calls = _facts_stub(monkeypatch)
    adapter = _kf_adapter()
    budget = WeComKfReplyBudget(total=5)
    budget.consume(1)  # verbose 已占 1 → 可用 4

    send = _manager().make_send_response(adapter=adapter, message_id="m1",
                                         reply_to="wx-1", reply_budget=budget)
    segments = ["段一", "段二", "段三"]
    ok = await send(ChannelAskResponse("x", ask_segments=_payload(segments)), [])

    assert ok is True
    sent = [call.kwargs["content"]["content"]
            for call in adapter.api_client.send_msg.await_args_list]
    assert sent == segments
    assert [call.kwargs["msgtype"] for call in adapter.api_client.send_msg.await_args_list] == ["text"] * 3
    # 提问轮次不为 final 预留：3 段正好扣 3，预算 4→1
    assert budget.remaining == 1
    assert calls["sent"] == [0, 1, 2]


async def test_kf_compact_rerender_fits_budget(monkeypatch):
    calls = _facts_stub(monkeypatch)
    adapter = _kf_adapter()
    budget = WeComKfReplyBudget(total=5)
    budget.consume(3)  # 可用 2

    send = _manager().make_send_response(adapter=adapter, message_id="m1",
                                         reply_to="wx-1", reply_budget=budget)
    # 完整渲染含长 description 超出可用额度，紧凑模式（省 description）后可容纳
    ok = await send(ChannelAskResponse("x", ask_segments=_payload(
        ["占位"], form=_form(option_count=1, description="长" * 1800))), [])

    assert ok is True
    sent = [call.kwargs["content"]["content"]
            for call in adapter.api_client.send_msg.await_args_list]
    assert len(sent) <= 2
    assert all("这是一段较长的选项描述" not in t and "长长长" not in t for t in sent)
    # 事实记录的是紧凑后的实际分段
    assert calls["init"] == [sent]


async def test_kf_budget_insufficient_sends_prefix_with_hint(monkeypatch):
    calls = _facts_stub(monkeypatch)
    adapter = _kf_adapter()
    budget = WeComKfReplyBudget(total=5)
    budget.consume(4)  # 可用 1

    send = _manager().make_send_response(adapter=adapter, message_id="m1",
                                         reply_to="wx-1", reply_budget=budget)
    # 长题干：紧凑渲染仍 >1 段 → available-1=0 段正文，只发 1 条事实提示占最后额度
    long_prompt_form = _form(option_count=1)
    long_prompt_form["questions"][0]["prompt"] = "超长题干" + "内容继续" * 400
    ok = await send(ChannelAskResponse("x", ask_segments=_payload(
        ["占位"], form=long_prompt_form)), [])

    assert ok is True
    sent = [call.kwargs["content"]["content"]
            for call in adapter.api_client.send_msg.await_args_list]
    assert len(sent) == 1  # available-1=0 段正文 + 1 条提示
    assert "剩余部分未发送" in sent[0] and "重发" in sent[0]
    assert budget.remaining == 0
    # 事实登记完整分段全文（截断只影响本轮发送，剩余段可经「重发」续传）
    from src.channels import ask_render
    assert calls["init"] == [ask_render.render_segments(long_prompt_form, 4000)]


async def test_kf_budget_exhausted_sends_nothing(monkeypatch):
    calls = _facts_stub(monkeypatch)
    adapter = _kf_adapter()
    budget = WeComKfReplyBudget(total=5)
    budget.consume(5)  # 可用 0

    send = _manager().make_send_response(adapter=adapter, message_id="m1",
                                         reply_to="wx-1", reply_budget=budget)
    ok = await send(ChannelAskResponse("x", ask_segments=_payload(
        ["占位"], form=_form())), [])

    assert ok is False
    adapter.api_client.send_msg.assert_not_awaited()


# ==================== ask_delivery 发送事实（fake manager 承载 DB） ====================


class FakeSessionManager:
    def __init__(self):
        self.rows = {"s-1": {"metadata": {"service_state": {"q": 1}}}}
        self.updates = []

    def get_session_by_id(self, session_id):
        return self.rows.get(session_id)

    def update_session(self, session_id, metadata=None, **kwargs):
        self.rows[session_id]["metadata"] = metadata
        self.updates.append(session_id)
        return True


@pytest.fixture
def fake_db(monkeypatch):
    manager = FakeSessionManager()
    monkeypatch.setattr("src.channels.session.channel_session_manager", manager)
    return manager


def test_ask_delivery_facts_roundtrip_preserves_other_metadata(fake_db):
    from src.channels import ask_delivery

    ask_delivery.init_facts("s-1", "wait_1", ["a", "b", "c"])
    ask_delivery.record_sent("s-1", "wait_1", 0)
    ask_delivery.record_sent("s-1", "wait_1", 0)  # 重复确认去重
    ask_delivery.record_failure("s-1", "wait_1", 1, "adapter send failed")

    facts = ask_delivery.load_facts("s-1", "wait_1")
    assert facts["segments"] == ["a", "b", "c"]
    assert facts["sent"] == [0]
    assert "segment[1]" in facts["last_error"]
    # 同一会话的其他 metadata 键不被覆盖
    assert fake_db.rows["s-1"]["metadata"]["service_state"] == {"q": 1}

    assert [item["index"] for item in ask_delivery.missing_segments(facts)] == [1, 2]

    ask_delivery.record_submission("s-1", "wait_1", "c-1")
    ask_delivery.clear_submission("s-1", "wait_1")
    assert ask_delivery.load_facts("s-1", "wait_1")["submitted"] is None

    ask_delivery.clear_facts("s-1", "wait_1")
    assert ask_delivery.load_facts("s-1", "wait_1") is None
    assert fake_db.rows["s-1"]["metadata"]["service_state"] == {"q": 1}


def test_ask_delivery_tolerates_missing_session(fake_db):
    from src.channels import ask_delivery

    # 不存在的会话：全部操作静默（辅助事实不阻断主链路）
    ask_delivery.init_facts("missing", "wait_1", ["a"])
    assert ask_delivery.load_facts("missing", "wait_1") is None
    ask_delivery.record_sent("missing", "wait_1", 0)
    ask_delivery.clear_facts("missing", "wait_1")


# ==================== 净化与分段载荷的挂载契约 ====================


def test_sanitize_strips_ask_segments_and_carrier_restores_it():
    """process_and_persist 在 sanitize 前摘下 ask_segments、净化后重挂载的前提：
    sanitize 返回纯 str 会丢属性；ChannelAskResponse 重挂载后载荷可读。
    （重挂载代码在 process_and_persist 内，此处验证其依赖的两个机制。）"""
    from src.channels.runner_agent import ChannelAskResponse
    from src.core.llm_output_sanitizer import sanitize_llm_markdown

    quoted = "> 引用行一\n> 引用行二\n> 引用行三"
    payload = ChannelAskResponse(quoted, ask_segments={"wait_id": "w", "segments": []})
    sanitized = sanitize_llm_markdown(str(payload))
    assert getattr(sanitized, "ask_segments", None) is None  # 属性确实会丢

    restored = ChannelAskResponse(str(sanitized), ask_segments={"wait_id": "w", "segments": []})
    assert restored.ask_segments == {"wait_id": "w", "segments": []}
    assert restored.images == ()
