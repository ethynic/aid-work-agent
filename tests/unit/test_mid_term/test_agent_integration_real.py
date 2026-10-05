"""Agent 压缩集成点 mutation-strengthened 测试（v3.1 Phase 4）

既有 test_agent_compress_call_site.py 通过重构 agent.py 代码片段来「模拟」压缩调用，
这种测试对 agent.py 实际代码无防御能力（删了 agent.py 那段代码测试还能过）。

本文件通过 monkeypatch 让 Agent._process_message_impl 之外的部分方法变成可控的 stub，
但保留 _detect_source_type、_build_messages、_reload_memory_from_db 的真实代码。

目的：让 mutation testing 真正有效。
"""

from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_minimal_agent():
    """构造一个最小可用的 Agent 实例（绕过 __init__ 的复杂依赖）"""
    from src.core.agent import Agent, AgentMode
    agent = Agent.__new__(Agent)
    agent.mode = AgentMode.MASTER
    return agent


def _make_history(memory, source):
    from src.core.agent_engine.contracts import Identity
    from src.services.agent_runner.runtime.history import SessionHistory
    from src.services.agent_runner.runtime.legacy import LegacyHistoryReader
    return SessionHistory(memory, source,
        LegacyHistoryReader(Identity(None, None, "sess", source)), tolerate_read_failure=True)


@pytest.fixture(autouse=True)
def _legacy_session_registration(monkeypatch):
    monkeypatch.setattr(
        "src.services.agent_runner.runtime.history_repository.HistoryRepository.legacy_session_kind",
        lambda session_id, tenant_id=None: "web")


# ============== _detect_source_type 真实代码测试 ==============


def test_detect_source_type_real_code_uses_current_record():
    """验证 _detect_source_type 从 SessionRecordManager.get_current_record() 读当前请求 record

    mutation: 如果有人绕过请求级 ContextVar（如改回共享 Agent 实例属性，
    并发请求会互相覆盖），此测试会失败。
    """
    agent = _make_minimal_agent()

    fake_record = MagicMock()
    fake_record.source_type = "feishu"

    import src.services.session_record as sr_mod
    orig = sr_mod.SessionRecordManager.get_current_record
    sr_mod.SessionRecordManager.get_current_record = staticmethod(lambda: fake_record)
    try:
        result = agent._identity("session-source").source
        assert result == "feishu", "当前上下文 record 的 source_type 应被采用"
    finally:
        sr_mod.SessionRecordManager.get_current_record = orig


def test_detect_source_type_real_code_no_record_defaults_chat():
    """验证无当前 record 时走默认 'chat'"""
    agent = _make_minimal_agent()

    import src.services.session_record as sr_mod
    orig = sr_mod.SessionRecordManager.get_current_record
    sr_mod.SessionRecordManager.get_current_record = staticmethod(lambda: None)
    try:
        assert agent._identity("session-source").source == "chat"
    finally:
        sr_mod.SessionRecordManager.get_current_record = orig


def test_detect_source_type_real_code_session_record_exception_falls_back():
    """SessionRecordManager.get_current_record 抛异常 → 走 try/except 兜底 'chat'

    mutation: 如果有人删了 try/except，此测试会从「返回 chat」变成「抛异常」
    """
    agent = _make_minimal_agent()

    import src.services.session_record as sr_mod
    orig = sr_mod.SessionRecordManager.get_current_record

    def _boom():
        raise RuntimeError("session record service unavailable")
    sr_mod.SessionRecordManager.get_current_record = staticmethod(_boom)
    try:
        result = agent._identity("session-source").source
        assert result == "chat", (
            "SessionRecordManager 异常时必须降级 'chat'，不应抛给上层"
        )
    finally:
        sr_mod.SessionRecordManager.get_current_record = orig


# ============== _build_messages 注入摘要的真实代码测试 ==============


def test_build_messages_real_code_injects_summary_with_correct_prefix():
    """验证 _build_messages 注入的 user 消息前缀严格为 '[📋 之前对话摘要]'

    mutation: 如果有人把前缀改成 '[Summary]' 或其他形式，此测试会失败。
    """
    agent = _make_minimal_agent()
    fake_memory = MagicMock()
    fake_memory.get_context.return_value = []
    agent.memory = fake_memory
    agent = _make_history(fake_memory, "chat")

    import src.services.agent_runner.runtime.history as agent_mod
    monkeypatch_target = pytest.MonkeyPatch()
    monkeypatch_target.setattr(agent_mod.settings.memory.mid_term, "enabled", True)

    fake_cs = MagicMock()
    agent.reader.active_summary = fake_cs.get_active_summary
    fake_cs.get_active_summary.return_value = "## 摘要内容"
    monkeypatch_target.setattr(
        "src.memory.mid_term.get_compression_service", lambda: fake_cs
    )

    try:
        messages, _ = agent._build_messages("sess")
        # 注入的 user 消息前缀必须严格匹配设计 §3.5
        assert messages[0]["role"] == "user"
        assert messages[0]["content"].startswith("[📋 之前对话摘要]")
        # 第二条必须是固定占位
        assert messages[1]["role"] == "assistant"
        assert messages[1]["content"] == "好的，已了解之前对话的要点。"
    finally:
        monkeypatch_target.undo()


def test_build_messages_real_code_no_summary_returns_original_history():
    """无 active_summary → 不注入，返回原 history（顺序/内容不变）

    mutation: 如果有人把 if active_summary 改成 if not active_summary，
    此测试会失败（因为原本不该注入却注入了）。
    """
    agent = _make_minimal_agent()
    fake_memory = MagicMock()
    base_history = [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
    ]
    fake_memory.get_context.return_value = list(base_history)
    agent.memory = fake_memory
    agent = _make_history(fake_memory, "chat")

    import src.services.agent_runner.runtime.history as agent_mod
    mp = pytest.MonkeyPatch()
    mp.setattr(agent_mod.settings.memory.mid_term, "enabled", True)

    fake_cs = MagicMock()
    agent.reader.active_summary = fake_cs.get_active_summary
    fake_cs.get_active_summary.return_value = None
    mp.setattr("src.memory.mid_term.get_compression_service", lambda: fake_cs)

    try:
        messages, _ = agent._build_messages("sess")
        # 不应有「之前对话摘要」
        assert all(
            "之前对话摘要" not in (m.get("content") or "") for m in messages
        )
    finally:
        mp.undo()


def test_build_messages_real_code_call_args_to_get_active_summary():
    """验证 _build_messages 调用 get_active_summary 时传了正确的 session_id 和 source_type

    mutation: 如果有人改了 source_type 推导（比如传成 None 或 'unknown'），此测试会失败。
    """
    agent = _make_minimal_agent()
    fake_memory = MagicMock()
    fake_memory.get_context.return_value = []
    agent.memory = fake_memory

    # 验证 source_type 路由：返回特定值，看是否被传给 get_active_summary
    captured = {"sid": None, "st": None}
    agent = _make_history(fake_memory, "wecom_personal_rpa")

    import src.services.agent_runner.runtime.history as agent_mod
    mp = pytest.MonkeyPatch()
    mp.setattr(agent_mod.settings.memory.mid_term, "enabled", True)

    fake_cs = MagicMock()
    agent.reader.active_summary = fake_cs.get_active_summary
    def _capture(sid, st, tenant_id=None):
        captured["sid"] = sid
        captured["st"] = st
        return None
    fake_cs.get_active_summary = _capture
    agent.reader.active_summary = _capture
    mp.setattr("src.memory.mid_term.get_compression_service", lambda: fake_cs)

    try:
        agent._build_messages("my_sess")
        assert captured["sid"] == "my_sess"
        assert captured["st"] == "wecom_personal_rpa"
    finally:
        mp.undo()


# ============== _reload_memory_from_db 真实代码测试 ==============


def test_reload_memory_real_code_chat_source_clears_and_reloads(monkeypatch):
    """chat 源：memory.clear + memory.load_history 都被调用

    mutation: 如果有人删了 memory.clear，导致旧 memory 残留，此测试会失败。
    """
    agent = _make_minimal_agent()
    fake_memory = MagicMock()
    fake_memory.short_term.max_messages = 100
    agent.memory = fake_memory
    agent = _make_history(fake_memory, "chat")

    import src.db.models as models_mod
    monkeypatch.setattr(
        models_mod.MessageDB, "list_by_session",
        lambda sid, limit=100: [{"role": "user", "content": "x", "metadata": None}],
    )

    agent._reload_memory_from_db("sess")

    fake_memory.clear.assert_called_once_with("sess")
    fake_memory.load_history.assert_called_once()


def test_reload_memory_real_code_channel_source_uses_channel_loader(monkeypatch):
    """channel 源：走 _load_channel_history（不是 MessageDB.list_by_session）"""
    agent = _make_minimal_agent()
    fake_memory = MagicMock()
    agent.memory = fake_memory
    agent = _make_history(fake_memory, "wecom_kf")

    channel_called = {"n": 0}
    def _fake_channel_history(sid, current_input):
        channel_called["n"] += 1
        return [{"role": "user", "content": "from channel"}]
    agent._load_channel_history = _fake_channel_history

    agent._reload_memory_from_db("sess_kf")

    assert channel_called["n"] == 1
    fake_memory.clear.assert_called_once_with("sess_kf")
    fake_memory.load_history.assert_called_once_with(
        "sess_kf", [{"role": "user", "content": "from channel"}]
    )


def test_reload_memory_real_code_db_exception_does_not_propagate(monkeypatch):
    """MessageDB 异常 → _reload_memory_from_db 内部 try/except 兜底，不抛"""
    agent = _make_minimal_agent()
    fake_memory = MagicMock()
    agent.memory = fake_memory
    agent = _make_history(fake_memory, "chat")

    import src.db.models as models_mod

    def _boom(sid, limit=100):
        raise RuntimeError("db connection lost")
    monkeypatch.setattr(models_mod.MessageDB, "list_by_session", _boom)

    # 不应抛
    agent._reload_memory_from_db("sess")


def test_reload_memory_real_code_tool_message_metadata_extracted(monkeypatch):
    """tool 消息的 tool_call_id 从 metadata 提取（验证 tool 消息的格式转换）"""
    agent = _make_minimal_agent()
    fake_memory = MagicMock()
    fake_memory.short_term.max_messages = 100
    agent.memory = fake_memory
    agent = _make_history(fake_memory, "chat")

    captured = {"history": None}
    def _capture_load(sid, history):
        captured["history"] = history
    fake_memory.load_history = _capture_load

    import src.db.models as models_mod
    import json
    monkeypatch.setattr(
        models_mod.MessageDB, "list_by_session",
        lambda sid, limit=100: [
            {
                "role": "tool",
                "content": "tool result",
                "metadata": json.dumps({"tool_call_id": "tc_123"}),
            },
        ],
    )

    agent._reload_memory_from_db("sess")

    assert captured["history"] is not None
    assert len(captured["history"]) == 1
    msg = captured["history"][0]
    assert msg["role"] == "tool"
    assert msg["tool_call_id"] == "tc_123"
    assert msg["content"] == "tool result"


def test_reload_memory_real_code_assistant_with_tool_calls(monkeypatch):
    """assistant 消息带 tool_calls 时正确还原（验证 metadata.tool_calls 字段）"""
    agent = _make_minimal_agent()
    fake_memory = MagicMock()
    fake_memory.short_term.max_messages = 100
    agent.memory = fake_memory
    agent = _make_history(fake_memory, "chat")

    captured = {"history": None}
    fake_memory.load_history = lambda sid, h: captured.__setitem__("history", h)

    import src.db.models as models_mod
    import json
    tool_calls = [{"id": "c1", "function": {"name": "search", "arguments": "{}"}}]
    monkeypatch.setattr(
        models_mod.MessageDB, "list_by_session",
        lambda sid, limit=100: [
            {
                "role": "assistant",
                "content": "calling",
                "metadata": json.dumps({"tool_calls": tool_calls}),
            },
        ],
    )

    agent._reload_memory_from_db("sess")

    msg = captured["history"][0]
    assert msg["role"] == "assistant"
    assert msg["tool_calls"] == tool_calls


# ============== Agent 调用站点真实逻辑（不复刻 agent.py 代码）==============


@pytest.mark.asyncio
async def test_agent_has_compress_call_site_attribute(monkeypatch):
    """Actual context preparation calls compression before reloading history.

    Replaces the old assertion tying private methods to the facade with a
    behavior check on the new caller, without copying its implementation.
    """
    from src.core.agent_engine.contracts import AgentMode, Identity
    from src.services.agent_runner.runtime.context_assembler import ContextAssembler
    order = []
    compression = MagicMock()
    async def compress(session_id):
        order.append("compress")
    compression._run_compression_phase = compress
    compression.event = None
    history = _make_history(MagicMock(), "chat")
    history.memory.get_context.return_value = []
    def reload(*args):
        order.append("reload")
    history._reload_memory_from_db = reload
    history.active_summary = lambda session_id: None
    attachments = MagicMock()
    attachments.prepare.return_value = ("user input", "", None)
    attachments._build_multimodal_user_content.return_value = None
    visibility, remember = MagicMock(), MagicMock()
    visibility.prime = AsyncMock()
    remember._handle_remember_intent = AsyncMock()
    prompts = MagicMock()
    prompts._build_system_prompt.return_value = "system"
    assembler = ContextAssembler(identity=Identity(None, None, "sess"), role=AgentMode.MASTER,
        profile_config=None, history=history, compression=compression, prompt_sources=prompts,
        skills=MagicMock(), remember=remember, attachments=attachments, visibility=visibility)
    execution, _ = await assembler.prepare("user input")
    assert order == ["compress", "reload"]
    assert execution.messages[-1]["content"] == "user input"


def test_agent_compress_session_imports_correctly():
    """验证 compress_session 入口可以从 src.memory.mid_term 正常导入。

    mutation: 如果有人重命名了 compress_session，所有 Agent 调用站点会崩。
    """
    from src.memory.mid_term import ContextCompressionService
    assert hasattr(ContextCompressionService, "compress_session"), (
        "ContextCompressionService 必须有 compress_session 方法"
    )
    # 确认是 async 函数
    import inspect
    assert inspect.iscoroutinefunction(ContextCompressionService.compress_session)
