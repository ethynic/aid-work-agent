"""compress_now 经济性闸门 + CompressionResult 真实用量透传测试（2026-09-30）

覆盖：
- 闸门命中：COMPRESS 区 < 4 万 token 且上下文未接近模型上限 → 跳过 LLM 走
  truncate 降级（fallback_used=True，不调 LLM，llm_* 全 0）
- 接近模型上限（>= 80%）时闸门不生效，正常调 LLM
- COMPRESS 区足够大时闸门不生效
- 真实 usage 透传 CompressionResult 的 llm_prompt/completion/cached_tokens
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

import src.memory.mid_term as mid_term_mod
from src.memory.mid_term import ContextCompressionService, SessionMeta


@pytest.fixture
def service(mid_term_settings, mock_llm_for_summary, fake_db_connection, monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", AsyncMock(return_value=None))
    monkeypatch.setattr(
        "src.services.session_record.record_background_llm_usage", lambda *a, **k: None
    )
    mock_conn, mock_cursor = fake_db_connection
    mock_cursor.fetchone.return_value = None
    mock_cursor.fetchall.return_value = []
    return ContextCompressionService(
        settings_cfg=mid_term_settings, llm_gateway=mock_llm_for_summary
    )


def _make_meta(session_id="sess", source_type="chat", context_token_count=1000):
    return SessionMeta(
        session_id=session_id,
        source_type=source_type,
        tenant_id="t1",
        user_id="u1",
        subagent_id=None,
        context_token_count=context_token_count,
    )


def _make_msgs(count=50):
    msgs = []
    for i in range(count):
        msgs.append({"role": "user", "content": f"u{i}", "id": 2 * i + 1})
        msgs.append({"role": "assistant", "content": f"a{i}", "id": 2 * i + 2})
    return msgs


def _patch_load(service, msgs):
    async def _fake(session_id, source_type, tenant_id):
        return msgs
    service._load_messages = _fake


@pytest.mark.asyncio
async def test_gate_skips_llm_for_small_zone(service, mock_llm_for_summary):
    """小区（< 4 万 token）+ 远离上限 → 闸门命中：truncate 降级，不调 LLM"""
    _patch_load(service, _make_msgs())
    service._model_limit_cache = 1_000_000

    result = await service.compress_now("sess", "chat", _make_meta())
    assert result is not None
    assert result.fallback_used is True
    assert mock_llm_for_summary.chat_lite.await_count == 0
    assert result.llm_prompt_tokens == 0
    assert result.llm_completion_tokens == 0
    assert result.llm_cached_tokens == 0
    assert result.summary_truncated is False


@pytest.mark.asyncio
async def test_gate_bypassed_when_near_model_limit(service, mock_llm_for_summary):
    """上下文接近模型上限（>= 80%）时闸门不生效，即使小区也调 LLM"""
    _patch_load(service, _make_msgs())
    service._model_limit_cache = 1_000_000

    result = await service.compress_now(
        "sess", "chat", _make_meta(context_token_count=900_000)
    )
    assert result is not None
    assert result.fallback_used is False
    assert mock_llm_for_summary.chat_lite.await_count == 1


@pytest.mark.asyncio
async def test_gate_bypassed_for_large_zone(service, mock_llm_for_summary, monkeypatch):
    """COMPRESS 区 >= 4 万 token 时闸门不生效"""
    _patch_load(service, _make_msgs())
    service._model_limit_cache = 1_000_000
    # 只放大摘要 prompt 估算，count_text_tokens 在闸门处被调用（compress 输入）
    monkeypatch.setattr(
        mid_term_mod, "count_text_tokens",
        lambda text: 50_000 if len(text) > 200 else 10,
    )

    result = await service.compress_now("sess", "chat", _make_meta())
    assert result is not None
    assert result.fallback_used is False
    assert mock_llm_for_summary.chat_lite.await_count == 1


@pytest.mark.asyncio
async def test_real_usage_passed_to_compression_result(service, mock_llm_for_summary, monkeypatch):
    """LLM 路径的真实 usage（含 cached_tokens）透传 CompressionResult"""
    _patch_load(service, _make_msgs())
    service._model_limit_cache = 1_000_000
    monkeypatch.setattr(
        mid_term_mod, "count_text_tokens",
        lambda text: 50_000 if len(text) > 200 else 10,
    )
    mock_llm_for_summary.chat_lite = AsyncMock(return_value={
        "content": "## 用户与背景\n- test",
        "finish_reason": "stop",
        "usage": {
            "prompt_tokens": 160000,
            "completion_tokens": 3000,
            "cached_tokens": 120000,
        },
    })

    result = await service.compress_now("sess", "chat", _make_meta())
    assert result is not None
    assert result.fallback_used is False
    assert result.llm_prompt_tokens == 160000
    assert result.llm_completion_tokens == 3000
    assert result.llm_cached_tokens == 120000
    assert result.summary_truncated is False
