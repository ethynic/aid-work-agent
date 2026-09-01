# -*- coding: utf-8 -*-
"""Phase 2：Web SSE verbose 透传与 metadata 合并单元测试（设计 §8 / §10）

覆盖范围（计划「Phase 2：Web 展示和 metadata」必测项）：
- /api/chat/stream 使用 process_message_with_feedback(surface="web") 包装入口；
- verbose 事件作为独立 SSE 帧原样透传，且不混入 progressMessages；
- enabled=false 时零行为变化：无 verbose 帧、metadata 无 verboseMessages 键；
- metadata 合并保留 progressMessages / downloadableFiles，verboseMessages 按
  eventId 去重，delivery 冻结为 "streamed"；
- browser continuation 路径只透传原生 verbose 事件，不新开 wrapper。

测试策略：直接调用 chat_stream 协程，monkeypatch sse_manager / session_queue /
SessionRecordManager / MessageDB / agent_router 为内存 stub，fake agent 用真实
``iter_with_verbose_feedback`` 包装自己的事件流（模拟 Phase 1 内核真实行为），
避免真实 LLM / DB / Redis 依赖。（2026-09-01 产品决策：system watchdog 删除，
verbose 仅来自策略。）
"""

import asyncio
import json
from types import SimpleNamespace

import pytest

import src.main as main_module
from src.config.settings import settings
from src.core.agent_events import make_verbose_event
from src.core.verbose_feedback import (
    VerboseFeedbackConfig,
    VerboseFeedbackState,
    iter_with_verbose_feedback,
)

pytestmark = pytest.mark.agent

SESSION_ID = "sess_web_verbose_test"

VALID_TEXT = "正在生成报价单，请耐心等待。"

# ============================================================
# 内存 stub：替代 Redis / DB / record 依赖
# ============================================================

class FakeSSEManager:
    """sse_manager 内存替身：不触 Redis。"""

    def __init__(self):
        self.history = []
        self.broadcasts = []

    def get_or_create_session(self, session_id=None):
        return session_id or SESSION_ID

    def add_sse_client(self, session_id):
        return object()

    def remove_sse_client(self, session_id, client_queue):
        pass

    def add_to_history(self, session_id, role, content):
        self.history.append((role, content))

    def clear_cancelled(self, session_id):
        pass

    def is_cancelled(self, session_id):
        return False

    def cancel_session(self, session_id):
        pass

    def broadcast(self, session_id, event):
        self.broadcasts.append(event)

class FakeSessionQueue:
    def __init__(self):
        self.responding = []
        self.idled = []

    def mark_responding(self, session_id):
        self.responding.append(session_id)

    def mark_idle(self, session_id):
        self.idled.append(session_id)

    def check_cancel(self, session_id):
        return False

class FakeRecordService:
    def __init__(self):
        self.progress_events = []
        self.completed = None

    def set_model(self, model):
        pass

    def set_provider(self, provider):
        pass

    def handle_progress_event(self, event):
        self.progress_events.append(event)

    def complete(self, assistant_message):
        self.completed = assistant_message

class FakeRecordManager:
    current = None

    @classmethod
    def start_record(cls, **kwargs):
        cls.current = FakeRecordService()
        return cls.current

    @classmethod
    def get_current_record(cls):
        return None

    @classmethod
    def end_record(cls):
        pass

    @classmethod
    def reset(cls):
        cls.current = None

class FakeMessageDB:
    batches = []

    @classmethod
    def create_batch_transactional(cls, session_id, messages):
        cls.batches.append(list(messages))
        return list(messages)

    @classmethod
    def reset(cls):
        cls.batches = []

class FakeAgent:
    """agent_router.get_agent 的替身：用真实内核包装 scripted 事件流。

    - ``process_message`` 依次 yield 预置事件；若 verbose_config 生效且事件为
      verbose，则先在本轮 state 注册（模拟 Phase 1 policy 注入路径）；
    - ``process_message_with_feedback`` 记录调用并委托真实
      ``iter_with_verbose_feedback``，验证 main.py 走的是 Phase 1 包装入口。
    """

    def __init__(self, events, pre_response_delay=0.0):
        self.events = events
        self.pre_response_delay = pre_response_delay
        self.llm = SimpleNamespace(
            get_model_name=lambda: "fake-model",
            get_provider_name=lambda: "fake-provider",
        )
        self.mode = SimpleNamespace(value="agent")
        self._init_tenant_id = None
        self.pmfb_calls = []
        self.process_message_calls = []

    async def process_message(self, **kwargs):
        self.process_message_calls.append(kwargs)
        config = kwargs.get("verbose_config")
        state = kwargs.get("verbose_state") or VerboseFeedbackState()
        for event in self.events:
            if self.pre_response_delay:
                await asyncio.sleep(self.pre_response_delay)
            if event.get("type") == "verbose":
                # 仅当 verbose 生效时才产生事件（模拟真实编排层门控）；
                # 注册到 state 是 wrapper 透传该事件的前提
                if not (config and config.effective_enabled):
                    continue
                if state.event is None:
                    state.try_emit(event)
            yield event

    def process_message_with_feedback(self, *, surface, config=None, state=None,
                                      observer=None, **kwargs):
        self.pmfb_calls.append({"surface": surface, "config": config})
        # 与真实 Agent.process_message_with_feedback 一致：缺省时新建本轮 state
        turn_state = state if state is not None else VerboseFeedbackState()
        inner = self.process_message(
            verbose_config=config, verbose_state=turn_state,
            verbose_observer=observer, **kwargs)
        return iter_with_verbose_feedback(
            inner, surface=surface, config=config, state=turn_state,
            observer=observer)

# ============================================================
# 环境装配
# ============================================================

def _force_config(monkeypatch, *, enabled):
    """显式固定全局 settings.agent.verbose_feedback（避免依赖本机 yaml）。"""
    verbose_cfg = settings.agent.verbose_feedback
    monkeypatch.setattr(verbose_cfg, "enabled", enabled)
    monkeypatch.setattr(verbose_cfg, "force_disabled", False)

@pytest.fixture
def web_env(monkeypatch):
    """装配 chat_stream 的全部外部依赖 stub。"""
    FakeRecordManager.reset()
    FakeMessageDB.reset()
    sse = FakeSSEManager()
    queue = FakeSessionQueue()
    monkeypatch.setattr(main_module, "sse_manager", sse)
    monkeypatch.setattr(main_module, "session_queue", queue)
    monkeypatch.setattr(main_module, "SessionRecordManager", FakeRecordManager)
    monkeypatch.setattr(main_module, "MessageDB", FakeMessageDB)
    yield SimpleNamespace(sse=sse, queue=queue)

def _make_request(message="帮我出一份报价单"):
    return main_module.ChatRequest(message=message, session_id=SESSION_ID)

def _make_http_request():
    # auth.get_current_user 读取 headers（无 Authorization → None），state 供租户解析
    return SimpleNamespace(headers={}, state=SimpleNamespace(tenant_id=None))

async def _run_chat_stream(agent, monkeypatch=None):
    # monkeypatch 注入时自动还原，避免污染同进程后续测试的模块属性
    if monkeypatch is not None:
        monkeypatch.setattr(
            main_module, "agent_router",
            SimpleNamespace(get_agent=lambda *args, **kwargs: agent))
    else:
        main_module.agent_router = SimpleNamespace(
            get_agent=lambda *args, **kwargs: agent)  # noqa: B010 - 测试内替换模块属性
    response = await main_module.chat_stream(_make_http_request(), _make_request())
    frames = []
    async for chunk in response.body_iterator:
        frames.append(chunk)
    return [json.loads(frame[len("data: "):]) for frame in frames
            if isinstance(frame, str) and frame.startswith("data: ")]

def _events_of_types(parsed, event_type):
    return [event for event in parsed if event.get("type") == event_type]

# ============================================================
# SSE 透传 + metadata 合并（enabled）
# ============================================================

class TestWebVerboseStreamEnabled:
    """enabled=true：verbose 独立 SSE 帧透传 + metadata 合并。"""

    @pytest.mark.asyncio
    async def test_verbose_frame_passthrough_single_policy_frame(
        self, web_env, monkeypatch
    ):
        """policy verbose 作为独立 SSE 帧原样透传，恰好一帧。

        （2026-09-01 产品决策：system watchdog 已删除；慢 turn 无策略命中时
        不产生任何 verbose 帧，见 test_no_verbose_turn_leaves_metadata_without_key。）
        """
        _force_config(monkeypatch, enabled=True)
        verbose_event = make_verbose_event(
            event_id="verbose_web_policy_0", data=VALID_TEXT, source="policy"
        )
        progress_event = {"type": "progress", "data": "🔧 正在执行", "timestamp": 1}
        response_event = {"type": "response", "data": "最终回复", "timestamp": 2}
        agent = FakeAgent(
            events=[progress_event, verbose_event, response_event], pre_response_delay=0.05
        )

        parsed = await _run_chat_stream(agent, monkeypatch)

        # 走的是 Phase 1 显式包装入口，surface="web"，配置来自全局解析
        assert len(agent.pmfb_calls) == 1
        assert agent.pmfb_calls[0]["surface"] == "web"
        assert isinstance(agent.pmfb_calls[0]["config"], VerboseFeedbackConfig)
        assert agent.pmfb_calls[0]["config"].effective_enabled is True

        verbose_frames = _events_of_types(parsed, "verbose")
        assert len(verbose_frames) == 1, "每轮最多一条 verbose SSE 帧"
        frame = verbose_frames[0]
        assert frame["source"] == "policy", "verbose 仅来自策略"
        assert frame["data"] == VALID_TEXT
        assert set(frame.keys()) == {"type", "eventId", "data", "source", "timestamp"}

        # 其余帧不受影响：progress/response/complete 原样透传
        assert len(_events_of_types(parsed, "progress")) == 1
        assert _events_of_types(parsed, "response")[0]["data"] == "最终回复"
        assert _events_of_types(parsed, "complete")

    @pytest.mark.asyncio
    async def test_policy_verbose_streamed_and_metadata_merged(
        self, web_env, monkeypatch
    ):
        """policy verbose 原样透传；metadata 合并保留既有键且 delivery 冻结。"""
        _force_config(monkeypatch, enabled=True)
        verbose_event = make_verbose_event(
            event_id="verbose_web_policy_1", data=VALID_TEXT, source="policy"
        )
        tool_result_event = {
            "type": "tool_result", "toolName": "mock_tool", "success": True,
            "timestamp": 2,
            "result": {
                "success": True, "file_id": "file_web_1",
                "file_name": "quote.xlsx", "download_file_name": "报价单.xlsx",
                "download_url": "/api/files/file_web_1/download",
                "file_size": 1024, "mime_type": "application/vnd.ms-excel",
            },
        }
        progress_event = {"type": "progress", "data": "🔧 正在执行", "timestamp": 1}
        response_event = {"type": "response", "data": "报价单已生成", "timestamp": 3}
        agent = FakeAgent(
            events=[progress_event, verbose_event, tool_result_event, response_event]
        )

        parsed = await _run_chat_stream(agent, monkeypatch)

        verbose_frames = _events_of_types(parsed, "verbose")
        assert len(verbose_frames) == 1
        assert verbose_frames[0]["eventId"] == "verbose_web_policy_1"
        assert verbose_frames[0]["data"] == VALID_TEXT
        assert verbose_frames[0]["source"] == "policy"

        # 持久化 batch：user + assistant，metadata 完整合并
        assert len(FakeMessageDB.batches) == 1
        roles = [m["role"] for m in FakeMessageDB.batches[0]]
        assert roles.count("user") == 1
        assistant = [m for m in FakeMessageDB.batches[0] if m["role"] == "assistant" and m["content"]]
        assert len(assistant) == 1
        metadata = assistant[0]["metadata"]

        # 既有键全部保留，verboseMessages 为新增键
        assert set(metadata.keys()) == {
            "progressMessages", "downloadableFiles", "verboseMessages",
        }
        # progressMessages 不混入 verbose（技术事件隔离）
        assert all(e.get("type") != "verbose" for e in metadata["progressMessages"])
        assert len(metadata["progressMessages"]) == 2  # progress + tool_result
        # downloadableFiles 保留
        assert metadata["downloadableFiles"][0]["file_id"] == "file_web_1"
        # verboseMessages 形状（设计 §10 Web 端条目：delivery 冻结 "streamed"）
        assert len(metadata["verboseMessages"]) == 1
        entry = metadata["verboseMessages"][0]
        assert set(entry.keys()) == {"eventId", "data", "source", "timestamp", "delivery"}
        assert entry["delivery"] == "streamed"
        assert entry["eventId"] == "verbose_web_policy_1"
        assert entry["data"] == VALID_TEXT
        assert entry["source"] == "policy"

    @pytest.mark.asyncio
    async def test_no_verbose_turn_leaves_metadata_without_key(
        self, web_env, monkeypatch
    ):
        """enabled 但快 turn 无 verbose：metadata 不写 verboseMessages 键。"""
        _force_config(monkeypatch, enabled=True)
        agent = FakeAgent(events=[{"type": "response", "data": "秒回", "timestamp": 1}])

        parsed = await _run_chat_stream(agent, monkeypatch)

        assert _events_of_types(parsed, "verbose") == []
        assistant = [
            m for m in FakeMessageDB.batches[0]
            if m["role"] == "assistant" and m["content"]
        ]
        assert "verboseMessages" not in assistant[0]["metadata"]
        assert assistant[0]["metadata"]["progressMessages"] == []

# ============================================================
# enabled=false：零行为变化
# ============================================================

class TestWebVerboseDisabledZeroBehaviorChange:
    """enabled=false：不产生 verbose 帧，metadata 无 verboseMessages 键。"""

    @pytest.mark.asyncio
    async def test_disabled_no_verbose_frames_and_no_metadata_key(
        self, web_env, monkeypatch
    ):
        _force_config(monkeypatch, enabled=False)
        # fake agent 在配置无效时不产生 verbose（模拟真实编排层门控）
        agent = FakeAgent(
            events=[
                {"type": "progress", "data": "🔧 正在执行", "timestamp": 1},
                {"type": "response", "data": "普通回复", "timestamp": 2},
            ],
            pre_response_delay=0.05,  # 慢 turn 也不得有 verbose（纯透传）

        )

        parsed = await _run_chat_stream(agent, monkeypatch)

        # 包装入口仍被调用（唯一入口），但配置未生效
        assert len(agent.pmfb_calls) == 1
        assert agent.pmfb_calls[0]["config"].effective_enabled is False

        assert _events_of_types(parsed, "verbose") == [], "关闭时不得有 verbose 帧"
        types = [e.get("type") for e in parsed]
        assert types == ["connected", "progress", "response", "complete"], (
            "关闭时事件帧序列与既有行为完全一致"
        )

        assistant = [
            m for m in FakeMessageDB.batches[0]
            if m["role"] == "assistant" and m["content"]
        ]
        metadata = assistant[0]["metadata"]
        assert "verboseMessages" not in metadata, (
            "关闭时 metadata 不含 verboseMessages 键（历史消息结构不变）"
        )
        assert set(metadata.keys()) == {"progressMessages"}

# ============================================================
# metadata 合并纯函数：eventId 去重 + 键形状
# ============================================================

class TestBuildVerboseMetadataEntries:
    def _entry(self, event_id, data=VALID_TEXT, source="policy"):
        return {
            "type": "verbose", "eventId": event_id, "data": data,
            "source": source, "timestamp": 1788144000000,
        }

    def test_dedup_by_event_id_keeps_first(self):
        first = self._entry("verbose_dup", data="第一条文案。")
        duplicate = self._entry("verbose_dup", data="后来的重复帧。")
        other = self._entry("verbose_other")
        entries = main_module._build_verbose_metadata_entries(
            [first, duplicate, other])
        assert [e["eventId"] for e in entries] == ["verbose_dup", "verbose_other"]
        assert entries[0]["data"] == "第一条文案。", "同 eventId 保留首条"

    def test_entry_shape_and_delivery_frozen(self):
        entries = main_module._build_verbose_metadata_entries(
            [self._entry("verbose_shape")])
        entry = entries[0]
        assert set(entry.keys()) == {
            "eventId", "data", "source", "timestamp", "delivery",
        }, "只保留协议五字段 + delivery，不透传内部扩展字段"
        assert entry["delivery"] == main_module.WEB_VERBOSE_DELIVERY
        assert main_module.WEB_VERBOSE_DELIVERY == "streamed"

    def test_empty_and_malformed_inputs(self):
        assert main_module._build_verbose_metadata_entries([]) == []
        assert main_module._build_verbose_metadata_entries(None) == []
        # 非 dict / 缺 eventId 的条目被防御性丢弃
        assert main_module._build_verbose_metadata_entries(["junk", {}]) == []

# ============================================================
# browser continuation：只透传原生 verbose，不新开 wrapper
# ============================================================

class FakeResumeStore:
    """ResumeStore 内存替身（真实类要求 Redis 可用）。"""

    def __init__(self):
        self.events = []

    async def append_event(self, tenant_id, continuation_id, event):
        self.events.append(event)

class ContinuationAgent:
    """continue_tool_call 直接走 process_message（无 verbose 包装）的替身。"""

    def __init__(self, events):
        self.events = events
        self.pmfb_calls = []
        self.process_message_calls = []

    def continue_tool_call(self, **kwargs):
        # 与真实签名一致：async generator 函数（调用即返回 async gen，无 await）
        async def gen():
            for event in self.events:
                yield event
        return gen()

    def process_message(self, **kwargs):
        self.process_message_calls.append(kwargs)
        raise AssertionError("continuation 路径不得直接调用 process_message")

    def process_message_with_feedback(self, **kwargs):
        self.pmfb_calls.append(kwargs)
        raise AssertionError("continuation 路径不得新开 verbose wrapper")

class TestBrowserContinuationPassthrough:
    @pytest.mark.asyncio
    async def test_verbose_event_streamed_without_new_wrapper(self, monkeypatch):
        from src.tools.browser import resume_store as resume_store_module

        verbose_event = make_verbose_event(
            event_id="verbose_cont_1", data=VALID_TEXT, source="policy"
        )
        agent = ContinuationAgent(events=[
            verbose_event,
            {"type": "response", "data": "续传回复", "timestamp": 2},
        ])
        store = FakeResumeStore()

        monkeypatch.setattr(
            main_module, "agent_router",
            SimpleNamespace(get_agent=lambda *a, **k: agent))
        monkeypatch.setattr(resume_store_module, "ResumeStore", lambda: store)
        sse = FakeSSEManager()
        monkeypatch.setattr(main_module, "sse_manager", sse)
        FakeMessageDB.reset()
        monkeypatch.setattr(main_module, "MessageDB", FakeMessageDB)

        record = SimpleNamespace(
            agent_name="master", session_id="sess_cont", tenant_id=None,
            user_id="user_cont", tool_call_id="tc_cont", continuation_id="cont_1",
        )
        await main_module._continue_browser_agent(record, {"status": "ok"})

        # 原生 verbose 事件原样写入 continuation stream（前端按 eventId 消费）
        assert verbose_event in store.events
        # 不新开 wrapper：process_message_with_feedback 从未被调用
        assert agent.pmfb_calls == []

        # 持久化 batch 保持既有结构：assistant metadata 只有 continued_from，
        # 无 verboseMessages（continuation 不做 metadata 合并）
        assistant_rows = [
            m for m in FakeMessageDB.batches[0]
            if m["role"] == "assistant" and m["content"]
        ]
        assert assistant_rows[0]["metadata"] == {"continued_from": "cont_1"}

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
