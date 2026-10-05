"""SessionHistory 重载与 legacy 来源映射的兼容回归（原断言保留）。

覆盖：
- _reload_memory_from_db 在 chat 源正常重载（mock 仓储 reader）
- _reload_memory_from_db 在异常时不抛（仅 warning）
- _build_messages 在 get_active_summary 抛异常时不崩
- _build_messages 在 get_active_summary 返回空字符串时（falsy）不注入
- _detect_source_type 在 SessionRecordManager 抛异常时降级 'chat'
"""

from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _legacy_session_registration(monkeypatch):
    monkeypatch.setattr(
        "src.services.agent_runner.runtime.history_repository.HistoryRepository.legacy_session_kind",
        lambda session_id, tenant_id=None: "web")


def test_reload_memory_chat_source_reloads(monkeypatch):
    """chat 源：MessageDB.list_by_session 返回消息 → memory.load_history 被调用"""

    fake_memory = MagicMock()
    fake_memory.short_term.max_messages = 100

    from src.services.agent_runner.runtime.history import SessionHistory
    reader = MagicMock()
    reader.active_summary.return_value = None
    agent = SessionHistory(fake_memory, "chat", reader, tolerate_read_failure=True)

    fake_msgs = [
        {"role": "user", "content": "hi", "metadata": None},
        {"role": "assistant", "content": "hello", "metadata": None},
    ]
    reader.read_web.return_value = fake_msgs

    agent._reload_memory_from_db("sess_x")

    # memory 被清空 + 重新加载
    fake_memory.clear.assert_called_once_with("sess_x")
    fake_memory.load_history.assert_called_once()
    reader.read_web.assert_called_once_with("sess_x", limit=100)
    # 第二个参数是 history_messages 列表
    args = fake_memory.load_history.call_args.args
    assert args[0] == "sess_x"
    assert len(args[1]) == 2


def test_reload_memory_db_exception_does_not_crash(monkeypatch):
    """MessageDB 异常 → _reload_memory_from_db 不抛（仅 warning）"""

    fake_memory = MagicMock()
    from src.services.agent_runner.runtime.history import SessionHistory
    reader = MagicMock()
    reader.active_summary.return_value = None
    agent = SessionHistory(fake_memory, "chat", reader, tolerate_read_failure=True)

    reader.read_web.side_effect = RuntimeError("db down")

    # 不应抛
    agent._reload_memory_from_db("sess_x")


def test_reload_memory_empty_db_messages_clears_memory(monkeypatch):
    """chat 源 + DB 无消息 → memory.clear 被调用，load_history 不被调用"""
    fake_memory = MagicMock()
    from src.services.agent_runner.runtime.history import SessionHistory
    reader = MagicMock()
    reader.active_summary.return_value = None
    agent = SessionHistory(fake_memory, "chat", reader, tolerate_read_failure=True)

    reader.read_web.return_value = []

    agent._reload_memory_from_db("sess_x")

    fake_memory.clear.assert_called_once_with("sess_x")
    fake_memory.load_history.assert_not_called()


def test_build_messages_empty_string_summary_not_injected(monkeypatch):
    """active_summary = '' (falsy) → 不注入 user+assistant 占位对"""
    fake_memory = MagicMock()
    fake_memory.get_context.return_value = [{"role": "user", "content": "real"}]
    from src.services.agent_runner.runtime.history import SessionHistory
    reader = MagicMock()
    reader.active_summary.return_value = None
    agent = SessionHistory(fake_memory, "chat", reader, tolerate_read_failure=True)

    from src.config.settings import settings
    monkeypatch.setattr(settings.memory.mid_term, "enabled", True)

    fake_cs = MagicMock()
    agent.reader.active_summary = fake_cs.get_active_summary
    fake_cs.get_active_summary.return_value = ""  # 空字符串
    monkeypatch.setattr("src.memory.mid_term.get_compression_service", lambda: fake_cs)

    messages, _ = agent._build_messages("sess")
    # 不应包含 [📋 之前对话摘要]
    assert all("之前对话摘要" not in (m.get("content") or "") for m in messages)


def test_build_messages_get_active_summary_exception_no_crash(monkeypatch):
    """get_active_summary 抛异常 → _build_messages 不崩，正常返回原 history"""
    fake_memory = MagicMock()
    fake_memory.get_context.return_value = [{"role": "user", "content": "ok"}]
    from src.services.agent_runner.runtime.history import SessionHistory
    reader = MagicMock()
    reader.active_summary.return_value = None
    agent = SessionHistory(fake_memory, "chat", reader, tolerate_read_failure=True)

    from src.config.settings import settings
    monkeypatch.setattr(settings.memory.mid_term, "enabled", True)

    fake_cs = MagicMock()
    agent.reader.active_summary = fake_cs.get_active_summary
    fake_cs.get_active_summary.side_effect = RuntimeError("cs down")
    monkeypatch.setattr("src.memory.mid_term.get_compression_service", lambda: fake_cs)

    messages, _ = agent._build_messages("sess")
    assert isinstance(messages, list)
    # 不应注入摘要对
    assert all("之前对话摘要" not in (m.get("content") or "") for m in messages)


def test_detect_source_type_record_exception_defaults_chat():
    """SessionRecordManager.get_current_record 抛异常 → 返回默认 'chat'"""
    from src.core.agent import Agent

    agent = Agent.__new__(Agent)

    import src.services.session_record as sr_mod
    orig = sr_mod.SessionRecordManager.get_current_record

    def _boom():
        raise RuntimeError("record svc down")
    sr_mod.SessionRecordManager.get_current_record = staticmethod(_boom)
    try:
        assert agent._identity("session-source").source == "chat"
    finally:
        sr_mod.SessionRecordManager.get_current_record = orig


def test_detect_source_type_record_service_wins():
    """请求级 record（ContextVar）的 source_type 优先于默认 'chat'"""
    from src.core.agent import Agent
    from src.services.session_record import SessionRecordManager

    agent = Agent.__new__(Agent)

    fake_record = MagicMock()
    fake_record.source_type = "feishu"
    token = SessionRecordManager.set_current_record(fake_record)
    try:
        assert agent._identity("session-source").source == "feishu"
    finally:
        SessionRecordManager.reset_current_record(token)


def test_detect_source_type_record_without_source_type_falls_back():
    """record 存在但 source_type=None → 降级 'chat'"""
    from src.core.agent import Agent

    agent = Agent.__new__(Agent)

    fake_record = MagicMock()
    fake_record.source_type = None  # 空 source_type
    import src.services.session_record as sr_mod
    orig = sr_mod.SessionRecordManager.get_current_record
    sr_mod.SessionRecordManager.get_current_record = staticmethod(lambda: fake_record)
    try:
        assert agent._identity("session-source").source == "chat"
    finally:
        sr_mod.SessionRecordManager.get_current_record = orig


def test_new_runner_history_failure_is_fail_closed():
    """New trusted history failures stop execution; only legacy policy tolerates them."""
    from src.services.agent_runner.runtime.history import SessionHistory
    memory, reader = MagicMock(), MagicMock()
    reader.read_web.side_effect = RuntimeError("db down")
    history = SessionHistory(memory, "chat", reader)
    with pytest.raises(RuntimeError, match="db down"):
        history._reload_memory_from_db("sess_x")
    memory.load_history.assert_not_called()
    reader.read_channel.assert_not_called()
