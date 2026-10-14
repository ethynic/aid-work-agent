"""make_send_response 结构化提问发送接线测试（WP5，设计 §7.2 / 计划 §WP5 P1-2）。

覆盖：
- 分段渠道（wecom=markdown / dingtalk=sampleMarkdown / RPA 兜底=send_message）：
  逐段调用渠道对应发送方法，发送事实在实际发送确认处记录（成功一段记一段，
  失败停止并记原因）；渠道配置覆盖按 adapter 现有 max_bytes 重排分段；
- 单条渠道（kf/RPA，评审修正 §7.2）：一表单合并为一条消息，kf 预算核算 N=1
  （零预算登记事实不发送，预算恢复后可整条重发），超单条硬限走渠道既有长文本
  兜底（kf 截断+Web 端提示；RPA send_message 既有超长分段）；
- ask_delivery 发送事实：分段/单条两种粒度，metadata 读改写保留其他键
  （fake manager 承载）。
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
             session_id="s-1", single=False):
    return {
        "session_id": session_id, "wait_id": wait_id, "resend": resend,
        "single": single, "form": form, "limit_bytes": limit,
        "segments": [{"index": i, "text": t} for i, t in enumerate(segments)],
    }


def _manager():
    return ChannelSessionManager.__new__(ChannelSessionManager)


def _facts_stub(monkeypatch):
    calls = {"init": [], "sent": [], "failed": [], "resend": 0,
             "init_single": [], "single_sent": [], "single_failed": []}

    monkeypatch.setattr("src.channels.ask_delivery.init_facts",
                        lambda sid, wid, segs: calls["init"].append(list(segs)))
    monkeypatch.setattr("src.channels.ask_delivery.record_sent",
                        lambda sid, wid, idx: calls["sent"].append(idx))
    monkeypatch.setattr("src.channels.ask_delivery.record_failure",
                        lambda sid, wid, idx, reason: calls["failed"].append(idx))
    monkeypatch.setattr("src.channels.ask_delivery.record_resend",
                        lambda sid, wid: calls.__setitem__("resend", calls["resend"] + 1))
    monkeypatch.setattr("src.channels.ask_delivery.init_single_facts",
                        lambda sid, wid, text: calls["init_single"].append(text))
    monkeypatch.setattr("src.channels.ask_delivery.record_single_sent",
                        lambda sid, wid: calls["single_sent"].append(True))
    monkeypatch.setattr("src.channels.ask_delivery.record_single_failure",
                        lambda sid, wid, reason: calls["single_failed"].append(reason))
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


async def test_rpa_per_segment_fallback_uses_send_message(monkeypatch):
    # 分段分发机制兜底：仅有 send_message 的 adapter 按段构造 UnifiedResponse，
    # 每段独立 delivery id（幂等键分离，后段不被 outbox 去重吞掉）
    _facts_stub(monkeypatch)
    adapter = MagicMock(spec=["send_message", "channel_type"])
    adapter.send_message = AsyncMock(return_value=True)
    pre_send_ids = []

    send = _manager().make_send_response(
        adapter=adapter, message_id="evt-1", reply_to="user-1",
        pre_send=lambda delivery_id=None: pre_send_ids.append(delivery_id))
    await send(ChannelAskResponse("x", ask_segments=_payload(["s0", "s1"])), [])

    assert adapter.send_message.await_count == 2
    assert pre_send_ids == ["evt-1:final:seg0", "evt-1:final:seg1"]
    contents = [call.args[0].content for call in adapter.send_message.await_args_list]
    assert [c["text"] for c in contents] == ["s0", "s1"]


async def test_rpa_single_message_keeps_full_text_for_adapter_split(monkeypatch):
    # RPA 单条路径（评审修正 §7.2）：一表单一条 send_message；超过 2000 字的
    # 超长兜底由 RPA adapter 既有 _segment_text 自动分段 actions 完成，
    # session 侧不预截断（内容不丢）
    calls = _facts_stub(monkeypatch)
    adapter = MagicMock(spec=["send_message", "channel_type"])
    adapter.send_message = AsyncMock(return_value=True)
    pre_send_ids = []

    send = _manager().make_send_response(
        adapter=adapter, message_id="evt-1", reply_to="user-1",
        pre_send=lambda delivery_id=None: pre_send_ids.append(delivery_id))
    long_text = "表单内容" * 600  # 2400 字符 > 2000 字符单条上限
    ok = await send(ChannelAskResponse(long_text, ask_segments=_payload(
        [long_text], limit=2000, single=True)), [])

    assert ok is True
    assert adapter.send_message.await_count == 1
    assert pre_send_ids == ["evt-1:final:seg0"]
    sent_text = adapter.send_message.await_args.args[0].content["text"]
    assert sent_text == long_text  # 不预截断
    assert calls["init_single"] == [long_text] and calls["single_sent"] == [True]


# ==================== wecom_kf 单条发送与预算核算（N=1，评审修正 §7.2） ====================


class _KfStub:
    """绑定真实 WeComKfAdapter 方法的最小桩（预算扣减/截断逻辑真实执行）。"""

    _adapter_cls = __import__("src.channels.wecom_kf.adapter", fromlist=["WeComKfAdapter"]).WeComKfAdapter
    send_ask_segment = _adapter_cls.send_ask_segment
    send_ask_file = _adapter_cls.send_ask_file

    def __init__(self):
        self.api_client = MagicMock()
        self.api_client.send_msg = AsyncMock(return_value={"errcode": 0})
        self.current_open_kfid = "kf-1"
        self._max_bytes = 2048

    def _truncate_to_single_message(self, text_content: str) -> str:
        # 委托真实实现（超长单条截断 + 「请在 Web 端查看」提示）
        return self._adapter_cls._truncate_to_single_message(self, text_content)


def _kf_adapter():
    return _KfStub()


def _kf_sent_texts(adapter):
    return [call.kwargs["content"]["content"]
            for call in adapter.api_client.send_msg.await_args_list]


async def test_kf_single_message_one_send_within_budget(monkeypatch):
    calls = _facts_stub(monkeypatch)
    adapter = _kf_adapter()
    budget = WeComKfReplyBudget(total=5)
    budget.consume(1)  # verbose 已占 1 → 可用 4

    send = _manager().make_send_response(adapter=adapter, message_id="m1",
                                         reply_to="wx-1", reply_budget=budget)
    form = _form(option_count=2)
    ok = await send(ChannelAskResponse("x", ask_segments=_payload(
        ["占位"], form=form, limit=2048, single=True)), [])

    assert ok is True
    # 一表单一条消息（不按题拆多条），预算 N=1：只扣 1（4→3）
    sent = _kf_sent_texts(adapter)
    assert len(sent) == 1
    assert sent[0].startswith("【归档范围】除成本治理外")  # 完整渲染未触发精简
    assert "Q1【归档范围】" in sent[0] and "回复编号或文字作答" in sent[0]
    assert budget.remaining == 3
    assert calls["init_single"] == [sent[0]] and calls["single_sent"] == [True]


async def test_kf_single_message_compacts_when_over_hard_limit(monkeypatch):
    calls = _facts_stub(monkeypatch)
    adapter = _kf_adapter()
    budget = WeComKfReplyBudget(total=5)

    send = _manager().make_send_response(adapter=adapter, message_id="m1",
                                         reply_to="wx-1", reply_budget=budget)
    # 长 description 撑爆 2048B → 依次精简后单条仍完整承载问题/选项/编号
    form = _form(option_count=3, description="长" * 400)
    ok = await send(ChannelAskResponse("x", ask_segments=_payload(
        ["占位"], form=form, limit=2048, single=True)), [])

    assert ok is True
    sent = _kf_sent_texts(adapter)
    assert len(sent) == 1
    text = sent[0]
    assert len(text.encode("utf-8")) <= 2048
    assert "这是一段较长的选项描述" not in text  # description 已精简
    assert "除成本治理外" not in text  # intro 已精简
    assert "Q1【归档范围】以下哪些任务移入" in text  # 问题与编号保留
    assert "1. 任务 1" in text and "3. 任务 3" in text
    assert "回复编号或文字作答" in text
    assert calls["init_single"] == [text] and calls["single_sent"] == [True]


async def test_kf_zero_budget_registers_facts_and_resend_after_recovery(monkeypatch):
    calls = _facts_stub(monkeypatch)
    adapter = _kf_adapter()
    budget = WeComKfReplyBudget(total=5)
    budget.consume(5)  # 可用 0

    send = _manager().make_send_response(adapter=adapter, message_id="m1",
                                         reply_to="wx-1", reply_budget=budget)
    ok = await send(ChannelAskResponse("x", ask_segments=_payload(
        ["占位"], form=_form(option_count=1), limit=2048, single=True)), [])

    # 零预算不发送，但事实已登记（sent=否 + 失败原因），预算恢复后可整条重发
    assert ok is False
    adapter.api_client.send_msg.assert_not_awaited()
    assert len(calls["init_single"]) == 1
    assert calls["single_failed"] == ["reply budget exhausted"]

    # 预算恢复后「重发」：整条重发一次成功
    text = calls["init_single"][0]
    recovered = WeComKfReplyBudget(total=5)
    send = _manager().make_send_response(adapter=adapter, message_id="m2",
                                         reply_to="wx-1", reply_budget=recovered)
    ok = await send(ChannelAskResponse(text, ask_segments=_payload(
        [text], resend=True, limit=2048, single=True)), [])

    assert ok is True
    assert _kf_sent_texts(adapter) == [text]
    assert recovered.remaining == 4
    assert calls["resend"] == 1 and calls["single_sent"] == [True]


async def test_kf_over_hard_limit_sends_full_content_as_file(monkeypatch):
    # 三轮评审修正：合并精简后仍超单条硬限 → 生成 Markdown 文件以 file 消息
    # 发送（内容不受单条文本/长图高度限制，题号/选项完整）；成功记 sent 扣预算。
    calls = _facts_stub(monkeypatch)
    adapter = _kf_adapter()
    uploads = []

    async def _upload(file_path, media_type, **kwargs):
        with open(file_path, encoding="utf-8") as f:
            uploads.append((f.read(), media_type, kwargs.get("display_name")))
        return {"errcode": 0, "media_id": "MEDIA_FILE"}

    adapter.api_client.upload_media = _upload
    budget = WeComKfReplyBudget(total=5)

    send = _manager().make_send_response(adapter=adapter, message_id="m1",
                                         reply_to="wx-1", reply_budget=budget)
    form = _form(option_count=1)
    form["questions"][0]["prompt"] = "超长题干" + "内容继续" * 400
    ok = await send(ChannelAskResponse("x", ask_segments=_payload(
        ["占位"], form=form, limit=2048, single=True)), [])

    assert ok is True
    # 上传的是完整未截断全文（>2048 字节），file 类型 + 客户可见文件名。
    assert len(uploads) == 1
    uploaded_text, media_type, display_name = uploads[0]
    assert len(uploaded_text.encode("utf-8")) > 2048
    assert "（内容较长，请在 Web 端查看）" not in uploaded_text
    assert media_type == "file" and display_name == "提问确认单.md"
    sent_files = [call.kwargs["content"]["media_id"]
                  for call in adapter.api_client.send_msg.await_args_list
                  if call.kwargs.get("msgtype") == "file"]
    assert sent_files == ["MEDIA_FILE"]
    assert budget.remaining == 4
    assert calls["single_sent"] == [True]


async def test_kf_over_hard_limit_file_failure_records_failure(monkeypatch):
    # 文件上传/发送失败：如实记失败事实（可「重发」重试），不扣预算、不记成功。
    calls = _facts_stub(monkeypatch)
    adapter = _kf_adapter()

    async def _upload_fail(file_path, media_type, **kwargs):
        return {"errcode": 40029}

    adapter.api_client.upload_media = _upload_fail
    budget = WeComKfReplyBudget(total=5)
    send = _manager().make_send_response(adapter=adapter, message_id="m1",
                                         reply_to="wx-1", reply_budget=budget)
    form = _form(option_count=1)
    form["questions"][0]["prompt"] = "超长题干" + "内容继续" * 400
    ok = await send(ChannelAskResponse("x", ask_segments=_payload(
        ["占位"], form=form, limit=2048, single=True)), [])

    assert ok is False
    assert budget.remaining == 5
    assert calls["single_sent"] == []
    assert calls["single_failed"] == ["ask file upload/send failed"]


async def test_kf_over_hard_limit_without_file_fallback_records_failure(monkeypatch):
    # 无文件发送能力：不发送、不截断冒充完整，事实记失败允许「重发」重试。
    calls = _facts_stub(monkeypatch)
    # 无文件发送能力的 kf 形态（防御分支，真实 adapter 恒有 send_ask_file）：
    # 不发送、不截断冒充完整，事实记失败允许「重发」重试。
    adapter = MagicMock(spec=["send_ask_segment", "channel_type", "_max_bytes"])
    adapter.send_ask_segment = AsyncMock(return_value=True)
    adapter._max_bytes = 2048
    budget = WeComKfReplyBudget(total=5)
    send = _manager().make_send_response(adapter=adapter, message_id="m1",
                                         reply_to="wx-1", reply_budget=budget)
    form = _form(option_count=1)
    form["questions"][0]["prompt"] = "超长题干" + "内容继续" * 400
    ok = await send(ChannelAskResponse("x", ask_segments=_payload(
        ["占位"], form=form, limit=2048, single=True)), [])

    assert ok is False
    adapter.send_ask_segment.assert_not_awaited()
    assert calls["single_failed"] == ["over channel limit and no file fallback"]


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


def test_ask_delivery_single_facts_roundtrip(fake_db):
    from src.channels import ask_delivery

    # 单条粒度（kf/RPA）：sent 是/否 + 失败原因，重发=整条重发
    ask_delivery.init_single_facts("s-1", "wait_2", "整条表单文本")
    ask_delivery.init_single_facts("s-1", "wait_2", "重跑不覆盖")
    assert ask_delivery.load_facts("s-1", "wait_2")["message"] == "整条表单文本"
    assert ask_delivery.load_facts("s-1", "wait_2")["sent"] is False

    ask_delivery.record_single_failure("s-1", "wait_2", "reply budget exhausted")
    facts = ask_delivery.load_facts("s-1", "wait_2")
    assert facts["sent"] is False and facts["last_error"] == "reply budget exhausted"
    assert ask_delivery.single_message(facts) == "整条表单文本"

    ask_delivery.record_single_sent("s-1", "wait_2")
    facts = ask_delivery.load_facts("s-1", "wait_2")
    assert facts["sent"] is True and facts["last_error"] is None

    # 分段事实（segments 形状）不被 single_message 误读
    assert ask_delivery.single_message({"segments": ["a"], "sent": [0]}) is None
    assert ask_delivery.single_message(None) is None

    ask_delivery.clear_facts("s-1", "wait_2")
    assert ask_delivery.load_facts("s-1", "wait_2") is None
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
