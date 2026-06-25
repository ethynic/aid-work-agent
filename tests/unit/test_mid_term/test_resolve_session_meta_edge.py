"""_resolve_session_meta 边界场景（v3.1 Phase 3）

补充：
- channel 源的 DB 异常兜底（之前只测了 chat 源异常）
- subagent_id 字段正确读取
- context_token_count=0 字段（显式 0）
"""

import pytest

from src.memory.mid_term import ContextCompressionService


@pytest.fixture
def service(mid_term_settings):
    return ContextCompressionService(settings_cfg=mid_term_settings)


@pytest.mark.asyncio
async def test_resolve_channel_db_exception_returns_empty_meta(service, monkeypatch):
    """channel 源 DB 异常 → 返回空 meta（不抛给上层）"""
    class _FakeMgr:
        def get_session_by_id(self, session_id):
            raise RuntimeError("channel db down")

    import src.channels.session as session_mod
    # P1-4：替换单例对象
    monkeypatch.setattr(session_mod, "channel_session_manager", _FakeMgr())

    meta = await service._resolve_session_meta("sess_x", "wecom_kf")
    assert meta.session_id == "sess_x"
    assert meta.source_type == "wecom_kf"
    assert meta.tenant_id is None
    assert meta.user_id is None
    assert meta.subagent_id is None
    assert meta.context_token_count == 0


@pytest.mark.asyncio
async def test_resolve_subagent_id_correctly_read(service, monkeypatch):
    """subagent_id 字段正确读取"""
    fake_row = {
        "session_id": "sess_sub",
        "tenant_id": "t1",
        "user_id": "u1",
        "subagent_id": "sub_trade",
        "context_token_count": 0,
    }

    import src.db.models as models_mod
    monkeypatch.setattr(models_mod.SessionDB, "get_by_id", lambda sid: fake_row)

    meta = await service._resolve_session_meta("sess_sub", "chat")
    assert meta.subagent_id == "sub_trade"


@pytest.mark.asyncio
async def test_resolve_explicit_zero_token_count(service, monkeypatch):
    """context_token_count=0 显式字段 → meta.context_token_count=0"""
    fake_row = {
        "session_id": "x",
        "tenant_id": None,
        "user_id": None,
        "subagent_id": None,
        "context_token_count": 0,
    }
    import src.db.models as models_mod
    monkeypatch.setattr(models_mod.SessionDB, "get_by_id", lambda sid: fake_row)

    meta = await service._resolve_session_meta("x", "chat")
    assert meta.context_token_count == 0


@pytest.mark.asyncio
async def test_resolve_empty_string_subagent_id(service, monkeypatch):
    """subagent_id='' 空字符串 → 转 None"""
    fake_row = {
        "session_id": "x",
        "tenant_id": "t1",
        "user_id": "u1",
        "subagent_id": "",
        "context_token_count": 5,
    }
    import src.db.models as models_mod
    monkeypatch.setattr(models_mod.SessionDB, "get_by_id", lambda sid: fake_row)

    meta = await service._resolve_session_meta("x", "chat")
    assert meta.subagent_id is None
    assert meta.context_token_count == 5
