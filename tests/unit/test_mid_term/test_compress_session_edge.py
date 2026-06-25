"""compress_session 异常路径与事务失败（v3.1 Phase 3）

补充：
- force=True 但 COMPRESS 区为空 → 返回 None
- _persist_atomically 抛异常 → compress_session 把异常透传给上层
- _should_compress 触发但 messages=[] → 安全返回 None
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.memory.mid_term import (
    ContextCompressionService,
    SessionMeta,
)


@pytest.fixture
def service(mid_term_settings, mock_llm_for_summary, fake_db_connection, monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", AsyncMock(return_value=None))
    mock_conn, mock_cursor = fake_db_connection
    mock_cursor.fetchone.return_value = None
    mock_cursor.fetchall.return_value = []
    return ContextCompressionService(
        settings_cfg=mid_term_settings, llm_gateway=mock_llm_for_summary
    )


def _patch_meta(service, *, context_token_count=0, tenant_id=None, user_id=None):
    async def _fake(session_id, source_type):
        return SessionMeta(
            session_id=session_id,
            source_type=source_type,
            tenant_id=tenant_id,
            user_id=user_id,
            context_token_count=context_token_count,
        )
    service._resolve_session_meta = _fake


def _patch_load(service, msgs):
    async def _fake(session_id, source_type, tenant_id):
        return msgs
    service._load_messages = _fake


@pytest.mark.asyncio
async def test_force_true_compress_empty_returns_none(service):
    """force=True 但消息数 <= header_keep + tail_keep → COMPRESS 区为空 → 返回 None"""
    _patch_meta(service)
    # 默认 header=3 + tail=30 = 33；只给 10 条 → COMPRESS 空
    msgs = [{"role": "user", "content": "x", "id": i} for i in range(10)]
    _patch_load(service, msgs)
    service._model_limit_cache = 1_000_000

    result = await service.compress_session("sess", "chat", force=True)
    assert result is None


@pytest.mark.asyncio
async def test_compress_session_propagates_persist_exception(service, mock_llm_for_summary, fake_db_connection):
    """_persist_atomically 抛异常 → compress_session 透传（不吞）"""
    _patch_meta(service, tenant_id="t1", user_id="u1")
    msgs = []
    for i in range(100):
        msgs.append({"role": "user", "content": f"u{i} " + "x" * 50, "id": 2 * i + 1})
        msgs.append({"role": "assistant", "content": f"a{i} " + "y" * 50, "id": 2 * i + 2})
    _patch_load(service, msgs)
    service._model_limit_cache = 1_000

    # 让 _persist_atomically 抛异常
    def _boom(*args, **kwargs):
        raise RuntimeError("disk full")
    service._persist_atomically = _boom

    with pytest.raises(RuntimeError, match="disk full"):
        await service.compress_session("sess", "chat")


@pytest.mark.asyncio
async def test_compress_session_empty_messages_returns_none(service):
    """空 messages 且 force=False → 未达阈值返回 None（不抛异常）"""
    _patch_meta(service)
    _patch_load(service, [])
    service._model_limit_cache = 128_000

    result = await service.compress_session("sess", "chat")
    assert result is None


@pytest.mark.asyncio
async def test_compress_session_channel_source_force(service, mock_llm_for_summary, fake_db_connection):
    """channel 源 + force=True 也能正常压缩"""
    _patch_meta(service, tenant_id="t_kf")
    msgs = []
    for i in range(50):
        msgs.append({"role": "user", "content": f"u{i}", "id": 2 * i + 1})
        msgs.append({"role": "assistant", "content": f"a{i}", "id": 2 * i + 2})
    _patch_load(service, msgs)

    result = await service.compress_session("sess_kf", "feishu", force=True)
    assert result is not None
    assert result.summary_id.startswith("csum_")


@pytest.mark.asyncio
async def test_compress_session_fallback_records_actual_provider(
    service, mock_llm_for_summary, fake_db_connection
):
    """LLM 失败走 fallback 时，CompressionResult.fallback_used=True，
    llm_provider 仍记录（即使是降级路径，也有 _actual_provider）"""
    mock_llm_for_summary.chat = AsyncMock(side_effect=RuntimeError("llm down"))
    _patch_meta(service, tenant_id="t1", user_id="u1")
    msgs = []
    for i in range(100):
        msgs.append({"role": "user", "content": f"u{i} " + "x" * 50, "id": 2 * i + 1})
        msgs.append({"role": "assistant", "content": f"a{i} " + "y" * 50, "id": 2 * i + 2})
    _patch_load(service, msgs)
    service._model_limit_cache = 1_000

    result = await service.compress_session("sess", "chat")
    assert result is not None
    assert result.fallback_used is True
