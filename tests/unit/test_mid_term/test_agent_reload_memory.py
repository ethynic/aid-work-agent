"""Agent _reload_memory_from_db 与压缩异常隔离测试（v3.1 Phase 4）

覆盖：
- _reload_memory_from_db 在 chat 源正常重载（mock MessageDB.list_by_session）
- _reload_memory_from_db 在异常时不抛（仅 warning）
- _build_messages 在 get_active_summary 抛异常时不崩
- _build_messages 在 get_active_summary 返回空字符串时（falsy）不注入
- _detect_source_type 在 SessionRecordManager 抛异常时降级 'chat'
"""

from unittest.mock import MagicMock

import pytest


def test_reload_memory_chat_source_reloads(monkeypatch):
    """chat 源：MessageDB.list_by_session 返回消息 → memory.load_history 被调用"""
    from src.core.agent import Agent, AgentMode

    agent = Agent.__new__(Agent)
    agent.mode = AgentMode.MASTER

    fake_memory = MagicMock()
    fake_memory.short_term.max_messages = 100
    agent.memory = fake_memory

    agent._detect_source_type = lambda: "chat"

    fake_msgs = [
        {"role": "user", "content": "hi", "metadata": None},
        {"role": "assistant", "content": "hello", "metadata": None},
    ]
    import src.db.models as models_mod
    monkeypatch.setattr(models_mod.MessageDB, "list_by_session", lambda sid, limit=100: fake_msgs)

    agent._reload_memory_from_db("sess_x")

    # memory 被清空 + 重新加载
    fake_memory.clear.assert_called_once_with("sess_x")
    fake_memory.load_history.assert_called_once()
    # 第二个参数是 history_messages 列表
    args = fake_memory.load_history.call_args.args
    assert args[0] == "sess_x"
    assert len(args[1]) == 2


def test_reload_memory_db_exception_does_not_crash(monkeypatch):
    """MessageDB 异常 → _reload_memory_from_db 不抛（仅 warning）"""
    from src.core.agent import Agent, AgentMode

    agent = Agent.__new__(Agent)
    agent.mode = AgentMode.MASTER

    fake_memory = MagicMock()
    agent.memory = fake_memory
    agent._detect_source_type = lambda: "chat"

    import src.db.models as models_mod
    monkeypatch.setattr(
        models_mod.MessageDB, "list_by_session",
        lambda sid, limit=100: (_ for _ in ()).throw(RuntimeError("db down"))
    )

    # 不应抛
    agent._reload_memory_from_db("sess_x")


def test_reload_memory_empty_db_messages_clears_memory(monkeypatch):
    """chat 源 + DB 无消息 → memory.clear 被调用，load_history 不被调用"""
    from src.core.agent import Agent, AgentMode

    agent = Agent.__new__(Agent)
    agent.mode = AgentMode.MASTER
    fake_memory = MagicMock()
    agent.memory = fake_memory
    agent._detect_source_type = lambda: "chat"

    import src.db.models as models_mod
    monkeypatch.setattr(models_mod.MessageDB, "list_by_session", lambda sid, limit=100: [])

    agent._reload_memory_from_db("sess_x")

    fake_memory.clear.assert_called_once_with("sess_x")
    fake_memory.load_history.assert_not_called()


def test_build_messages_empty_string_summary_not_injected(monkeypatch):
    """active_summary = '' (falsy) → 不注入 user+assistant 占位对"""
    from src.core.agent import Agent, AgentMode

    agent = Agent.__new__(Agent)
    agent.mode = AgentMode.MASTER
    fake_memory = MagicMock()
    fake_memory.get_context.return_value = [{"role": "user", "content": "real"}]
    agent.memory = fake_memory
    agent._detect_source_type = lambda: "chat"

    import src.core.agent as agent_mod
    monkeypatch.setattr(agent_mod.settings.memory.mid_term, "enabled", True)

    fake_cs = MagicMock()
    fake_cs.get_active_summary.return_value = ""  # 空字符串
    monkeypatch.setattr("src.memory.mid_term.get_compression_service", lambda: fake_cs)

    messages, _ = agent._build_messages("sess")
    # 不应包含 [📋 之前对话摘要]
    assert all("之前对话摘要" not in (m.get("content") or "") for m in messages)


def test_build_messages_get_active_summary_exception_no_crash(monkeypatch):
    """get_active_summary 抛异常 → _build_messages 不崩，正常返回原 history"""
    from src.core.agent import Agent, AgentMode

    agent = Agent.__new__(Agent)
    agent.mode = AgentMode.MASTER
    fake_memory = MagicMock()
    fake_memory.get_context.return_value = [{"role": "user", "content": "ok"}]
    agent.memory = fake_memory
    agent._detect_source_type = lambda: "chat"

    import src.core.agent as agent_mod
    monkeypatch.setattr(agent_mod.settings.memory.mid_term, "enabled", True)

    fake_cs = MagicMock()
    fake_cs.get_active_summary.side_effect = RuntimeError("cs down")
    monkeypatch.setattr("src.memory.mid_term.get_compression_service", lambda: fake_cs)

    messages, _ = agent._build_messages("sess")
    assert isinstance(messages, list)
    # 不应注入摘要对
    assert all("之前对话摘要" not in (m.get("content") or "") for m in messages)


def test_detect_source_type_record_exception_defaults_chat():
    """SessionRecordManager.get_current_record 抛异常 → 返回默认 'chat'"""
    from src.core.agent import Agent, AgentMode

    agent = Agent.__new__(Agent)
    agent.mode = AgentMode.MASTER

    import src.services.session_record as sr_mod
    orig = sr_mod.SessionRecordManager.get_current_record

    def _boom():
        raise RuntimeError("record svc down")
    sr_mod.SessionRecordManager.get_current_record = staticmethod(_boom)
    try:
        assert agent._detect_source_type() == "chat"
    finally:
        sr_mod.SessionRecordManager.get_current_record = orig


def test_detect_source_type_record_service_wins():
    """请求级 record（ContextVar）的 source_type 优先于默认 'chat'"""
    from src.core.agent import Agent, AgentMode
    from src.services.session_record import SessionRecordManager

    agent = Agent.__new__(Agent)
    agent.mode = AgentMode.MASTER

    fake_record = MagicMock()
    fake_record.source_type = "feishu"
    token = SessionRecordManager.set_current_record(fake_record)
    try:
        assert agent._detect_source_type() == "feishu"
    finally:
        SessionRecordManager.reset_current_record(token)


def test_detect_source_type_record_without_source_type_falls_back():
    """record 存在但 source_type=None → 降级 'chat'"""
    from src.core.agent import Agent, AgentMode

    agent = Agent.__new__(Agent)
    agent.mode = AgentMode.MASTER

    fake_record = MagicMock()
    fake_record.source_type = None  # 空 source_type
    import src.services.session_record as sr_mod
    orig = sr_mod.SessionRecordManager.get_current_record
    sr_mod.SessionRecordManager.get_current_record = staticmethod(lambda: fake_record)
    try:
        assert agent._detect_source_type() == "chat"
    finally:
        sr_mod.SessionRecordManager.get_current_record = orig
