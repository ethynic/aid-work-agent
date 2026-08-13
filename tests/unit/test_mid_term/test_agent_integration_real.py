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


# ============== _detect_source_type 真实代码测试 ==============


def test_detect_source_type_real_code_priority_explicit_record():
    """验证 _detect_source_type 的优先级 1：_explicit_record_service 优先于 get_current_record

    mutation: 如果有人改顺序把 get_current_record 放前面，此测试会失败。
    """
    agent = _make_minimal_agent()

    fake_explicit = MagicMock()
    fake_explicit.source_type = "feishu"
    agent._explicit_record_service = fake_explicit

    fake_implicit = MagicMock()
    fake_implicit.source_type = "dingtalk"

    import src.services.session_record as sr_mod
    orig = sr_mod.SessionRecordManager.get_current_record
    sr_mod.SessionRecordManager.get_current_record = staticmethod(lambda: fake_implicit)
    try:
        result = agent._detect_source_type()
        # 显式 record 必须赢
        assert result == "feishu", (
            "_explicit_record_service 应优先于 get_current_record"
        )
    finally:
        sr_mod.SessionRecordManager.get_current_record = orig


def test_detect_source_type_real_code_no_explicit_uses_current_record():
    """验证优先级 2：无 _explicit_record_service 时，用 get_current_record"""
    agent = _make_minimal_agent()
    # 不设 _explicit_record_service
    if hasattr(agent, "_explicit_record_service"):
        del agent._explicit_record_service

    fake_record = MagicMock()
    fake_record.source_type = "wecom_kf"

    import src.services.session_record as sr_mod
    orig = sr_mod.SessionRecordManager.get_current_record
    sr_mod.SessionRecordManager.get_current_record = staticmethod(lambda: fake_record)
    try:
        assert agent._detect_source_type() == "wecom_kf"
    finally:
        sr_mod.SessionRecordManager.get_current_record = orig


def test_detect_source_type_real_code_session_record_exception_falls_back():
    """SessionRecordManager.get_current_record 抛异常 → 走 try/except 兜底 'chat'

    mutation: 如果有人删了 try/except，此测试会从「返回 chat」变成「抛异常」
    """
    agent = _make_minimal_agent()
    if hasattr(agent, "_explicit_record_service"):
        del agent._explicit_record_service

    import src.services.session_record as sr_mod
    orig = sr_mod.SessionRecordManager.get_current_record

    def _boom():
        raise RuntimeError("session record service unavailable")
    sr_mod.SessionRecordManager.get_current_record = staticmethod(_boom)
    try:
        result = agent._detect_source_type()
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
    agent._detect_source_type = lambda: "chat"

    import src.core.agent as agent_mod
    monkeypatch_target = pytest.MonkeyPatch()
    monkeypatch_target.setattr(agent_mod.settings.memory.mid_term, "enabled", True)

    fake_cs = MagicMock()
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
    agent._detect_source_type = lambda: "chat"

    import src.core.agent as agent_mod
    mp = pytest.MonkeyPatch()
    mp.setattr(agent_mod.settings.memory.mid_term, "enabled", True)

    fake_cs = MagicMock()
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
    agent._detect_source_type = lambda: "wecom_personal_rpa"

    import src.core.agent as agent_mod
    mp = pytest.MonkeyPatch()
    mp.setattr(agent_mod.settings.memory.mid_term, "enabled", True)

    fake_cs = MagicMock()
    def _capture(sid, st, tenant_id=None):
        captured["sid"] = sid
        captured["st"] = st
        return None
    fake_cs.get_active_summary = _capture
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
    agent._detect_source_type = lambda: "chat"

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
    agent._detect_source_type = lambda: "wecom_kf"

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
    agent._detect_source_type = lambda: "chat"

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
    agent._detect_source_type = lambda: "chat"

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
    agent._detect_source_type = lambda: "chat"

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


def test_agent_has_compress_call_site_attribute():
    """防御性：Agent 类必须仍定义 _detect_source_type 和 _reload_memory_from_db。

    mutation: 如果有人删了这两个方法（重构了 agent.py），调用点 compress_session 会崩。
    """
    from src.core.agent import Agent
    assert hasattr(Agent, "_detect_source_type"), (
        "Agent 必须实现 _detect_source_type（v3.1 Phase 4.1）"
    )
    assert hasattr(Agent, "_reload_memory_from_db"), (
        "Agent 必须实现 _reload_memory_from_db（v3.1 Phase 4.2）"
    )
    assert hasattr(Agent, "_build_messages"), (
        "Agent._build_messages 必须存在（v3.1 Phase 4.3 注入点）"
    )


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
