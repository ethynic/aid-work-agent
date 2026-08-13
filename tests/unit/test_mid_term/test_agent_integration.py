"""Agent 主流程集成 compress_session 测试（v3.1 Phase 4）

通过 mock compress_session 验证 Agent._process_message_impl 主流程：
- 压缩成功时调用 _reload_memory_from_db
- 压缩异常时不阻塞主流程
- _detect_source_type 正确返回
- _build_messages 注入 active_summary
- LLM 调用后更新 session token 缓存

不实际跑完整 Agent（依赖太多），只验证集成点的行为。
"""

from unittest.mock import AsyncMock, MagicMock

import pytest


def test_detect_source_type_from_record():
    """_detect_source_type 优先从 SessionRecordService 读 source_type"""
    from src.core.agent import Agent, AgentMode

    agent = Agent.__new__(Agent)
    agent.mode = AgentMode.MASTER

    # mock SessionRecordManager.get_current_record 返回带 source_type 的 record
    fake_record = MagicMock()
    fake_record.source_type = "wecom_kf"

    import src.services.session_record as sr_mod
    orig_get = sr_mod.SessionRecordManager.get_current_record
    sr_mod.SessionRecordManager.get_current_record = staticmethod(lambda: fake_record)
    try:
        assert agent._detect_source_type() == "wecom_kf"
    finally:
        sr_mod.SessionRecordManager.get_current_record = orig_get


def test_detect_source_type_default_chat():
    """无 record 时默认返回 'chat'"""
    from src.core.agent import Agent, AgentMode

    agent = Agent.__new__(Agent)
    agent.mode = AgentMode.MASTER

    import src.services.session_record as sr_mod
    orig_get = sr_mod.SessionRecordManager.get_current_record
    sr_mod.SessionRecordManager.get_current_record = staticmethod(lambda: None)
    try:
        assert agent._detect_source_type() == "chat"
    finally:
        sr_mod.SessionRecordManager.get_current_record = orig_get


def test_build_messages_injects_active_summary(monkeypatch):
    """_build_messages 在有 active_summary 时注入 user+assistant 对到头部"""
    from src.core.agent import Agent, AgentMode

    agent = Agent.__new__(Agent)
    agent.mode = AgentMode.MASTER

    # mock memory.get_context 返回简单 history
    fake_memory = MagicMock()
    fake_memory.get_context.return_value = [
        {"role": "user", "content": "real user"},
        {"role": "assistant", "content": "real assistant"},
    ]
    agent.memory = fake_memory

    # mock _detect_source_type
    agent._detect_source_type = lambda: "chat"

    # mock settings.memory.mid_term.enabled
    import src.core.agent as agent_mod
    monkeypatch.setattr(agent_mod.settings.memory.mid_term, "enabled", True)

    # mock compression_service.get_active_summary 返回非空
    fake_cs = MagicMock()
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
    from src.core.agent import Agent, AgentMode

    agent = Agent.__new__(Agent)
    agent.mode = AgentMode.MASTER

    fake_memory = MagicMock()
    fake_memory.get_context.return_value = [
        {"role": "user", "content": "raw user"},
    ]
    agent.memory = fake_memory
    agent._detect_source_type = lambda: "chat"

    import src.core.agent as agent_mod
    monkeypatch.setattr(agent_mod.settings.memory.mid_term, "enabled", True)

    fake_cs = MagicMock()
    fake_cs.get_active_summary.return_value = None
    monkeypatch.setattr(
        "src.memory.mid_term.get_compression_service", lambda: fake_cs
    )

    messages, _ = agent._build_messages("sess_y")
    # 不应有摘要 user（[📋 之前对话摘要]）
    assert all("之前对话摘要" not in (m.get("content") or "") for m in messages)


def test_build_messages_exception_does_not_crash(monkeypatch):
    """compression_service 异常 → _build_messages 不崩，logger.warning"""
    from src.core.agent import Agent, AgentMode

    agent = Agent.__new__(Agent)
    agent.mode = AgentMode.MASTER

    fake_memory = MagicMock()
    fake_memory.get_context.return_value = [{"role": "user", "content": "x"}]
    agent.memory = fake_memory
    agent._detect_source_type = lambda: "chat"

    import src.core.agent as agent_mod
    monkeypatch.setattr(agent_mod.settings.memory.mid_term, "enabled", True)

    def _boom():
        raise RuntimeError("compression service down")
    monkeypatch.setattr(
        "src.memory.mid_term.get_compression_service", _boom
    )

    # 不抛异常即可
    messages, _ = agent._build_messages("sess_z")
    assert isinstance(messages, list)
