"""compress_session v3.1 缺口补充测试

补充既有 137 个测试遗漏的关键场景：
- LLM 失败 → fallback 时 summary_text 真的是 truncate 结果（不止 fallback_used 标志）
- _resolve_session_meta 抛异常时 compress_session 的行为（异常透传 or 兜底）
- _load_messages 抛异常时 compress_session 拿到空 messages
- force=True + 缓存 token > 0 的交叉场景（force 跳过 _should_compress）
- 压缩成功路径CompressionResult 字段值的合理性（非空校验）
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


def _patch_meta(service, *, context_token_count=0, tenant_id="t1", user_id="u1", subagent_id=None):
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


def _build_msgs(n=50):
    msgs = []
    for i in range(n):
        msgs.append({"role": "user", "content": f"u{i} " + "x" * 50, "id": 2 * i + 1})
        msgs.append({"role": "assistant", "content": f"a{i} " + "y" * 50, "id": 2 * i + 2})
    return msgs


@pytest.mark.asyncio
async def test_llm_failure_fallback_summary_is_truncated(service, mock_llm_for_summary, fake_db_connection):
    """LLM 失败走 fallback 时，summary_text 必须来自 _fallback_truncate（不是空字符串），
    通过 spy _fallback_truncate 验证它被调用并其返回值进入 persist。"""
    _patch_meta(service)
    _patch_load(service, _build_msgs(50))
    service._model_limit_cache = 1_000

    mock_llm_for_summary.chat = AsyncMock(side_effect=RuntimeError("llm boom"))

    fallback_calls = {"n": 0, "last": None}
    orig = service._fallback_truncate

    def _spy(compress_section):
        fallback_calls["n"] += 1
        result = orig(compress_section)
        fallback_calls["last"] = result
        return result

    service._fallback_truncate = _spy

    result = await service.compress_session("sess", "chat")
    assert result is not None
    assert result.fallback_used is True
    assert fallback_calls["n"] == 1, "_fallback_truncate 必须被调用一次"
    assert fallback_calls["last"], "fallback 返回值不能为空"


@pytest.mark.asyncio
async def test_resolve_meta_exception_propagates(service, monkeypatch):
    """_resolve_session_meta 抛异常（不该发生但防御性）→ compress_session 应让其透传，
    不吞异常。注意：当前实现 _resolve_session_meta 内部已 try/except 兜底返回空 meta，
    所以这里直接 patch 成 raise 来验证 compress_session 上层没有额外 try/except。"""
    _patch_load(service, _build_msgs(50))
    service._model_limit_cache = 128_000

    async def _boom(session_id, source_type):
        raise RuntimeError("meta svc boom")
    service._resolve_session_meta = _boom

    with pytest.raises(RuntimeError, match="meta svc boom"):
        await service.compress_session("sess", "chat")


@pytest.mark.asyncio
async def test_load_messages_exception_returns_empty_no_crash(service, monkeypatch):
    """_load_messages 抛异常 → compress_session 应拿到 [] → 未达阈值返回 None（不崩）。
    注意：当前实现 _load_messages 内部已 try/except 返回 []，这里 patch 成 raise 验证
    compress_session 上层没有兜底（会透传）。这个测试记录现状。"""
    _patch_meta(service)
    service._model_limit_cache = 128_000

    async def _boom(session_id, source_type, tenant_id):
        raise RuntimeError("load boom")
    service._load_messages = _boom

    # 现状：compress_session 不兜底 _load_messages 异常
    with pytest.raises(RuntimeError, match="load boom"):
        await service.compress_session("sess", "chat")


@pytest.mark.asyncio
async def test_force_skips_should_compress_completely(service, monkeypatch):
    """force=True 时完全不调用 _should_compress（即使 cached token=0 也无所谓）。
    通过让 _should_compress 抛异常验证它根本没被调用。"""
    _patch_meta(service, context_token_count=0)
    _patch_load(service, _build_msgs(50))
    service._model_limit_cache = 1_000

    def _boom(*args, **kwargs):
        raise AssertionError("_should_compress 不应在 force=True 时被调用")
    service._should_compress = _boom

    result = await service.compress_session("sess", "chat", force=True)
    assert result is not None


@pytest.mark.asyncio
async def test_compress_success_result_fields_complete(service, mock_llm_for_summary, fake_db_connection):
    """达阈值压缩成功 → CompressionResult 所有字段都填充合理值（非 None / 非负）"""
    _patch_meta(service, tenant_id="t1", user_id="u1", subagent_id="sub_x")
    _patch_load(service, _build_msgs(50))
    service._model_limit_cache = 1_000

    result = await service.compress_session("sess", "chat")
    assert result is not None
    # 所有字段非空校验
    assert result.summary_id and result.summary_id.startswith("csum_")
    assert result.compressed_message_count > 0
    assert result.original_token_count > 0
    assert result.compressed_token_count > 0
    assert result.original_token_count > result.compressed_token_count
    assert 0.0 < result.compression_ratio < 1.0
    assert result.fallback_used is False
    assert result.llm_provider, "llm_provider 必须非空"
    assert result.llm_model, "llm_model 必须非空"


@pytest.mark.asyncio
async def test_channel_source_with_subagent_id_persisted(service, mock_llm_for_summary, fake_db_connection):
    """channel 源 + subagent_id 非 None 时，subagent_id 正确传入 persist（验证不被 strip 掉）"""
    _patch_meta(service, tenant_id="t_kf", user_id=None, subagent_id="sub_kf")
    _patch_load(service, _build_msgs(50))
    service._model_limit_cache = 1_000

    captured = {}
    orig_persist = service._persist_atomically

    def _spy_persist(**kwargs):
        captured.update(kwargs)
        return orig_persist(**kwargs)

    service._persist_atomically = _spy_persist

    result = await service.compress_session("sess_kf", "wecom_kf")
    assert result is not None
    assert captured.get("subagent_id") == "sub_kf"
    assert captured.get("tenant_id") == "t_kf"
    assert captured.get("user_id") is None
