"""_resolve_session_meta 单测（v3.1 Phase 3）

覆盖：
- chat 源从 chat_sessions 查
- channel 源从 channel_sessions 查
- 找不到时返回空 meta
- DB 异常时返回空 meta
- context_token_count 字段正确解析（含 NULL/不存在/异常值兜底）
"""

import pytest

from src.memory.mid_term import ContextCompressionService, SessionMeta


@pytest.fixture
def service(mid_term_settings):
    return ContextCompressionService(settings_cfg=mid_term_settings)


@pytest.mark.asyncio
async def test_resolve_chat_session(service, monkeypatch):
    """source_type='chat' → 调 SessionDB.get_by_id"""
    fake_row = {
        "session_id": "sess_chat_1",
        "tenant_id": "tenant_1",
        "user_id": "user_1",
        "subagent_id": None,
        "context_token_count": 12345,
    }

    def _fake_get_by_id(session_id):
        assert session_id == "sess_chat_1"
        return fake_row

    # SessionDB 在 _resolve_session_meta 内部延迟 import
    import src.db.models as models_mod
    monkeypatch.setattr(models_mod.SessionDB, "get_by_id", _fake_get_by_id)

    meta = await service._resolve_session_meta("sess_chat_1", "chat")
    assert meta.session_id == "sess_chat_1"
    assert meta.source_type == "chat"
    assert meta.tenant_id == "tenant_1"
    assert meta.user_id == "user_1"
    assert meta.subagent_id is None
    assert meta.context_token_count == 12345


@pytest.mark.asyncio
async def test_resolve_channel_session(service, monkeypatch):
    """source_type='wecom_kf' → 调 channel_session_manager.get_session_by_id（模块级单例，P1-4）"""
    fake_row = {
        "session_id": "sess_kf_1",
        "tenant_id": "tenant_kf",
        "user_id": "",
        "subagent_id": "sub_x",
        "channel_type": "wecom_kf",
        "context_token_count": 50,
    }

    class _FakeMgr:
        def get_session_by_id(self, session_id):
            assert session_id == "sess_kf_1"
            return fake_row

    import src.channels.session as session_mod
    # P1-4：_resolve_session_meta 复用模块级单例 channel_session_manager，
    # 测试需直接替换单例对象，而非替换类构造函数
    monkeypatch.setattr(session_mod, "channel_session_manager", _FakeMgr())

    meta = await service._resolve_session_meta("sess_kf_1", "wecom_kf")
    assert meta.tenant_id == "tenant_kf"
    # user_id 为空字符串 → 转 None
    assert meta.user_id is None
    assert meta.subagent_id == "sub_x"
    assert meta.context_token_count == 50


@pytest.mark.asyncio
async def test_resolve_nonexistent_session_returns_empty_meta(service, monkeypatch):
    """session 不存在 → 返回空 meta（不抛异常）"""
    import src.db.models as models_mod
    monkeypatch.setattr(models_mod.SessionDB, "get_by_id", lambda sid: None)

    meta = await service._resolve_session_meta("ghost", "chat")
    assert meta.session_id == "ghost"
    assert meta.source_type == "chat"
    assert meta.tenant_id is None
    assert meta.user_id is None
    assert meta.subagent_id is None
    assert meta.context_token_count == 0


@pytest.mark.asyncio
async def test_resolve_db_exception_returns_empty_meta(service, monkeypatch):
    """DB 异常 → 返回空 meta（不抛给上层）"""
    def _boom(sid):
        raise RuntimeError("db down")

    import src.db.models as models_mod
    monkeypatch.setattr(models_mod.SessionDB, "get_by_id", _boom)

    meta = await service._resolve_session_meta("sess_x", "chat")
    assert meta.session_id == "sess_x"
    assert meta.tenant_id is None
    assert meta.context_token_count == 0


@pytest.mark.asyncio
async def test_resolve_context_token_count_null_or_missing(service, monkeypatch):
    """context_token_count 字段为 None / 不存在 / 异常值 → 兜底为 0"""
    cases = [
        {"session_id": "x", "tenant_id": None, "context_token_count": None},
        {"session_id": "x", "tenant_id": None},  # 字段缺失
        {"session_id": "x", "tenant_id": None, "context_token_count": "abc"},  # 异常值
    ]

    import src.db.models as models_mod
    for row in cases:
        monkeypatch.setattr(models_mod.SessionDB, "get_by_id", lambda sid, r=row: r)
        meta = await service._resolve_session_meta("x", "chat")
        assert meta.context_token_count == 0, f"failed for row={row}"
