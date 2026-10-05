"""Legacy source mapping and SessionHistory summary behavior after owner migration.

The facade maps legacy record source to Identity; SessionHistory owns summary
assembly. Actual RuntimeExecution assembly has separate acceptance coverage.
"""

from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _legacy_session_registration(monkeypatch):
    monkeypatch.setattr(
        "src.services.agent_runner.runtime.history_repository.HistoryRepository.legacy_session_kind",
        lambda session_id, tenant_id=None: "web")


def test_detect_source_type_from_record():
    """_detect_source_type 优先从 SessionRecordService 读 source_type"""
    from src.core.agent import Agent

    agent = Agent.__new__(Agent)

    # mock SessionRecordManager.get_current_record 返回带 source_type 的 record
    fake_record = MagicMock()
    fake_record.source_type = "wecom_kf"

    import src.services.session_record as sr_mod
    orig_get = sr_mod.SessionRecordManager.get_current_record
    sr_mod.SessionRecordManager.get_current_record = staticmethod(lambda: fake_record)
    try:
        assert agent._identity("session-source").source == "wecom_kf"
    finally:
        sr_mod.SessionRecordManager.get_current_record = orig_get


def test_detect_source_type_default_chat():
    """无 record 时默认返回 'chat'"""
    from src.core.agent import Agent

    agent = Agent.__new__(Agent)

    import src.services.session_record as sr_mod
    orig_get = sr_mod.SessionRecordManager.get_current_record
    sr_mod.SessionRecordManager.get_current_record = staticmethod(lambda: None)
    try:
        assert agent._identity("session-source").source == "chat"
    finally:
        sr_mod.SessionRecordManager.get_current_record = orig_get


def test_build_messages_injects_active_summary(monkeypatch):
    """_build_messages 在有 active_summary 时注入 user+assistant 对到头部"""

    # mock memory.get_context 返回简单 history
    fake_memory = MagicMock()
    fake_memory.get_context.return_value = [
        {"role": "user", "content": "real user"},
        {"role": "assistant", "content": "real assistant"},
    ]

    # History source is explicitly supplied by the execution identity.
    from src.services.agent_runner.runtime.history import SessionHistory
    reader = MagicMock()
    reader.active_summary.return_value = None
    agent = SessionHistory(fake_memory, "chat", reader, tolerate_read_failure=True)

    # mock settings.memory.mid_term.enabled
    from src.config.settings import settings
    monkeypatch.setattr(settings.memory.mid_term, "enabled", True)

    # mock compression_service.get_active_summary 返回非空
    fake_cs = MagicMock()
    agent.reader.active_summary = fake_cs.get_active_summary
    fake_cs.get_active_summary.return_value = "## 用户与背景\n- 是个测试用户"
    monkeypatch.setattr(
        "src.memory.mid_term.get_compression_service", lambda: fake_cs
    )

    messages, _ = agent._build_messages("sess_x")

    # 验证：第一条是摘要 user，第二条是占位 assistant，之后是 real history
    assert "之前对话摘要" in messages[0]["content"]
    assert messages[1]["role"] == "assistant"
    assert messages[1]["content"] == "好的，已了解之前对话的要点。"
    # 后续是 real history（reorder 后）
    contents = [m.get("content", "") for m in messages[2:]]
    assert any("real user" in c for c in contents if isinstance(c, str))


def test_build_messages_no_summary_passthrough(monkeypatch):
    """无 active_summary 时 _build_messages 直接走原 history（不注入）"""

    fake_memory = MagicMock()
    fake_memory.get_context.return_value = [
        {"role": "user", "content": "raw user"},
    ]
    from src.services.agent_runner.runtime.history import SessionHistory
    reader = MagicMock()
    reader.active_summary.return_value = None
    agent = SessionHistory(fake_memory, "chat", reader, tolerate_read_failure=True)

    from src.config.settings import settings
    monkeypatch.setattr(settings.memory.mid_term, "enabled", True)

    fake_cs = MagicMock()
    agent.reader.active_summary = fake_cs.get_active_summary
    fake_cs.get_active_summary.return_value = None
    monkeypatch.setattr(
        "src.memory.mid_term.get_compression_service", lambda: fake_cs
    )

    messages, _ = agent._build_messages("sess_y")
    # 不应有摘要 user（[📋 之前对话摘要]）
    assert all("之前对话摘要" not in (m.get("content") or "") for m in messages)


def test_build_messages_exception_does_not_crash(monkeypatch):
    """compression_service 异常 → _build_messages 不崩，logger.warning"""

    fake_memory = MagicMock()
    fake_memory.get_context.return_value = [{"role": "user", "content": "x"}]
    from src.services.agent_runner.runtime.history import SessionHistory
    reader = MagicMock()
    reader.active_summary.return_value = None
    agent = SessionHistory(fake_memory, "chat", reader, tolerate_read_failure=True)

    from src.config.settings import settings
    monkeypatch.setattr(settings.memory.mid_term, "enabled", True)

    def _boom():
        raise RuntimeError("compression service down")
    reader.active_summary.side_effect = RuntimeError("compression service down")
    monkeypatch.setattr(
        "src.memory.mid_term.get_compression_service", _boom
    )

    # 不抛异常即可
    messages, _ = agent._build_messages("sess_z")
    assert isinstance(messages, list)
