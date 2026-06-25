"""_load_messages 单测（v3.1 Phase 3.4）

直接验证 ContextCompressionService._load_messages 的路由 + 异常兜底。
覆盖：
- source_type='chat' → 调 MessageDB.list_by_session（默认过滤 compacted）
- 非 chat 源 → 调 channel_session_manager.get_messages（模块级单例，P1-4，默认过滤 compacted）
- DB 异常时返回空 list（不抛异常给上层）
"""

import pytest

from src.memory.mid_term import ContextCompressionService


@pytest.fixture
def service(mid_term_settings):
    return ContextCompressionService(settings_cfg=mid_term_settings)


@pytest.mark.asyncio
async def test_load_messages_chat_source(service, monkeypatch):
    """source_type='chat' → 调 MessageDB.list_by_session"""
    captured = {"called": False, "sid": None, "limit": None}

    def _fake_list(session_id, limit=100, **kwargs):
        captured["called"] = True
        captured["sid"] = session_id
        captured["limit"] = limit
        return [{"id": 1, "role": "user", "content": "x"}]

    import src.db.models as models_mod
    monkeypatch.setattr(models_mod.MessageDB, "list_by_session", _fake_list)

    msgs = await service._load_messages("sess_chat", "chat", tenant_id="t1")
    assert captured["called"] is True
    assert captured["sid"] == "sess_chat"
    # limit 给大值，避免默认 100 截断
    assert captured["limit"] == 10000
    assert msgs == [{"id": 1, "role": "user", "content": "x"}]


@pytest.mark.asyncio
async def test_load_messages_channel_source(service, monkeypatch):
    """非 chat 源 → 调 channel_session_manager.get_messages（模块级单例，P1-4）"""
    captured = {"called": False, "sid": None, "limit": None}

    class _FakeMgr:
        def get_messages(self, session_id, limit=100, **kwargs):
            captured["called"] = True
            captured["sid"] = session_id
            captured["limit"] = limit
            return [{"id": 1, "role": "user", "content": "y"}]

    import src.channels.session as session_mod
    # P1-4：_load_messages 复用模块级单例 channel_session_manager，测试替换单例对象
    monkeypatch.setattr(session_mod, "channel_session_manager", _FakeMgr())

    msgs = await service._load_messages("sess_kf", "wecom_kf", tenant_id=None)
    assert captured["called"] is True
    assert captured["sid"] == "sess_kf"
    assert captured["limit"] == 10000
    assert msgs == [{"id": 1, "role": "user", "content": "y"}]


@pytest.mark.asyncio
async def test_load_messages_chat_exception_returns_empty(service, monkeypatch):
    """chat 源 DB 异常 → 返回空 list，不抛"""
    def _boom(sid, **kwargs):
        raise RuntimeError("db down")

    import src.db.models as models_mod
    monkeypatch.setattr(models_mod.MessageDB, "list_by_session", _boom)

    msgs = await service._load_messages("sess_x", "chat", tenant_id=None)
    assert msgs == []


@pytest.mark.asyncio
async def test_load_messages_channel_exception_returns_empty(service, monkeypatch):
    """channel 源 DB 异常 → 返回空 list，不抛"""
    class _FakeMgr:
        def get_messages(self, *args, **kwargs):
            raise RuntimeError("channel db down")

    import src.channels.session as session_mod
    # P1-4：替换单例对象
    monkeypatch.setattr(session_mod, "channel_session_manager", _FakeMgr())

    msgs = await service._load_messages("sess_y", "feishu", tenant_id=None)
    assert msgs == []
