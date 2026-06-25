"""compress_session 关键路径测试（v3.1 Phase 3）

覆盖设计 §5.1 中的关键场景：
- 未达阈值返回 None
- 达阈值返回 CompressionResult
- force=True 跳过阈值检查
- session 不存在的兜底（空 meta）
- 缓存 token 优先
- compress_section 为空时不压缩
- v3.1 CompressionResult 字段完整性
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from src.memory.mid_term import (
    CompressionResult,
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


def _patch_meta(service, *, context_token_count=0, tenant_id=None, user_id="u1", subagent_id=None):
    """patch _resolve_session_meta 返回指定 meta"""
    async def _fake(session_id, source_type):
        return SessionMeta(
            session_id=session_id,
            source_type=source_type,
            tenant_id=tenant_id,
            user_id=user_id,
            subagent_id=subagent_id,
            context_token_count=context_token_count,
        )
    service._resolve_session_meta = _fake


def _patch_load(service, msgs):
    async def _fake(session_id, source_type, tenant_id):
        return msgs
    service._load_messages = _fake


@pytest.mark.asyncio
async def test_below_threshold_returns_none(service):
    """未达阈值 → None"""
    _patch_meta(service)
    _patch_load(service, [{"role": "user", "content": "hi", "id": 1}])
    service._model_limit_cache = 128_000

    result = await service.compress_session("sess", "chat")
    assert result is None


@pytest.mark.asyncio
async def test_above_threshold_returns_result(service, mock_llm_for_summary, fake_db_connection):
    """达阈值 → CompressionResult，所有字段填充"""
    _patch_meta(service, tenant_id="t1", user_id="u1")
    msgs = []
    for i in range(100):
        msgs.append({"role": "user", "content": f"u{i} " + "x" * 50, "id": 2 * i + 1})
        msgs.append({"role": "assistant", "content": f"a{i} " + "y" * 50, "id": 2 * i + 2})
    _patch_load(service, msgs)
    service._model_limit_cache = 1_000  # 强制 token 阈值触发

    result = await service.compress_session("sess", "chat")
    assert result is not None
    assert isinstance(result, CompressionResult)
    assert result.summary_id.startswith("csum_")
    assert result.compressed_message_count > 0
    assert result.original_token_count > 0
    assert result.compressed_token_count > 0
    assert result.original_token_count > result.compressed_token_count
    assert 0.0 < result.compression_ratio < 1.0
    assert result.fallback_used is False
    assert result.llm_provider is not None


@pytest.mark.asyncio
async def test_force_skips_threshold_check(service, mock_llm_for_summary, fake_db_connection):
    """force=True → 跳过阈值检查。需要 messages 数 > header_keep + tail_keep 才有 COMPRESS 内容"""
    _patch_meta(service)
    # 默认 header=3, tail=30，至少 34 条才有 COMPRESS 内容
    msgs = []
    for i in range(20):
        msgs.append({"role": "user", "content": f"u{i}", "id": 2 * i + 1})
        msgs.append({"role": "assistant", "content": f"a{i}", "id": 2 * i + 2})
    _patch_load(service, msgs)
    service._model_limit_cache = 1_000_000  # 不触发 token 阈值

    result = await service.compress_session("sess", "chat", force=True)
    assert result is not None


@pytest.mark.asyncio
async def test_empty_meta_session_not_exists_skipped(service):
    """session 不存在 → 空 meta + 空 messages → 未达阈值返回 None"""
    async def _empty_meta(session_id, source_type):
        return SessionMeta(session_id=session_id, source_type=source_type)

    async def _empty_msgs(session_id, source_type, tenant_id):
        return []

    service._resolve_session_meta = _empty_meta
    service._load_messages = _empty_msgs
    service._model_limit_cache = 128_000

    result = await service.compress_session("ghost", "chat")
    assert result is None


@pytest.mark.asyncio
async def test_cached_token_triggers_without_count_tokens(
    service, mock_llm_for_summary, fake_db_connection, monkeypatch
):
    """缓存 context_token_count=999999 → _should_compress 不调 count_tokens，
    但 compress_session 后续仍会用 count_tokens 算 original/compressed_token_count
    （这是设计的一部分，不算缓存路径的失效）。
    此测试改为：spy count_tokens，验证 _should_compress 路径下不调用它。"""
    _patch_meta(service, context_token_count=999999)
    # 40 条消息（> header=3 + tail=30 = 33，确保 COMPRESS 非空；< 150 消息数阈值）
    msgs = []
    for i in range(20):
        msgs.append({"role": "user", "content": f"u{i}", "id": 2 * i + 1})
        msgs.append({"role": "assistant", "content": f"a{i}", "id": 2 * i + 2})
    _patch_load(service, msgs)
    service._model_limit_cache = 1_000

    # 改为 spy：记录调用次数（但不爆炸）
    call_count = {"n": 0}
    from src.memory import mid_term as mt_mod
    orig_count_tokens = mt_mod.count_tokens

    def _spy(messages):
        call_count["n"] += 1
        return orig_count_tokens(messages)
    monkeypatch.setattr("src.memory.mid_term.count_tokens", _spy)

    # 直接测 _should_compress 路径（这是 v3.1 缓存优化的目标）
    should, reason = service._should_compress(msgs, 999999, 1_000)
    assert should is True
    assert "cached=True" in reason
    # _should_compress 内部不应调用 count_tokens
    assert call_count["n"] == 0, "_should_compress 在 cache>0 时不应调 count_tokens"


@pytest.mark.asyncio
async def test_compress_section_empty_returns_none(service):
    """触发消息数阈值但 COMPRESS 为空 → None"""
    _patch_meta(service)
    # header_keep=80 + tail_keep=80 = 160 > 消息数 150 → COMPRESS 空
    from src.config.settings import MidTermMemoryConfig
    svc = ContextCompressionService(
        settings_cfg=MidTermMemoryConfig(header_keep=80, tail_keep=80, message_count_threshold=150),
        llm_gateway=service._llm_gateway,
    )
    msgs = [{"role": "user", "content": ".", "id": i} for i in range(150)]
    _patch_meta(svc)
    _patch_load(svc, msgs)
    svc._model_limit_cache = 1_000_000

    result = await svc.compress_session("sess", "chat")
    assert result is None


@pytest.mark.asyncio
async def test_channel_source_type_works(service, mock_llm_for_summary, fake_db_connection):
    """source_type='wecom_kf' 同样工作（meta 用 channel 数据）"""
    _patch_meta(service, tenant_id="t_kf", user_id=None, subagent_id="sub_x")
    msgs = []
    for i in range(100):
        msgs.append({"role": "user", "content": f"u{i} " + "x" * 50, "id": 2 * i + 1})
        msgs.append({"role": "assistant", "content": f"a{i} " + "y" * 50, "id": 2 * i + 2})
    _patch_load(service, msgs)
    service._model_limit_cache = 1_000

    result = await service.compress_session("sess_kf", "wecom_kf")
    assert result is not None
    assert result.summary_id.startswith("csum_")
