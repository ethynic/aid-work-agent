"""compress_now 行为测试（v3.2 新增；v3.2.1 P1-2 后调整）

覆盖：
- compress_now 不再做阈值检查（mutation 测试：如果重新加了阈值检查应被捕获）
- compress_now 接受 session_meta 参数（避免重复查询）
- session_meta=None 时内部重新解析
- force=True 路径
- trigger_reason 透传路径（v3.2.1 P0-1）
- compress_now 透传 _persist_atomically 异常
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


def _make_meta(session_id="sess", source_type="chat", context_token_count=999999):
    return SessionMeta(
        session_id=session_id,
        source_type=source_type,
        tenant_id="t1",
        user_id="u1",
        subagent_id=None,
        context_token_count=context_token_count,
    )


def _patch_load(service, msgs):
    async def _fake(session_id, source_type, tenant_id):
        return msgs
    service._load_messages = _fake


@pytest.mark.asyncio
async def test_compress_now_does_not_call_eval_threshold(
    service, mock_llm_for_summary, fake_db_connection, monkeypatch
):
    """compress_now 不应做阈值检查（v3.2.1 P1-2：_should_compress 已删除，
    mutation 测试改为 spy _eval_threshold，若被调用则 fail）。

    compress_now 接受调用方已 check_threshold 完成的语义，不再独立判断阈值。
    """
    meta = _make_meta()
    # 构造足够的 messages（> header_keep + tail_keep）使 COMPRESS 非空
    msgs = []
    for i in range(50):
        msgs.append({"role": "user", "content": f"u{i}", "id": 2 * i + 1})
        msgs.append({"role": "assistant", "content": f"a{i}", "id": 2 * i + 2})
    _patch_load(service, msgs)
    service._model_limit_cache = 1_000_000  # 即使阈值高也应压缩（compress_now 不做阈值检查）

    # spy _eval_threshold，若被调用则 fail
    call_count = {"n": 0}
    orig = service._eval_threshold

    def _spy(cached_tokens, msg_count, model_limit):
        call_count["n"] += 1
        return orig(cached_tokens, msg_count, model_limit)
    service._eval_threshold = _spy

    # compress_now 应直接进入压缩流程（不查阈值）
    result = await service.compress_now("sess", "chat", meta)
    assert result is not None
    assert isinstance(result, CompressionResult)
    assert call_count["n"] == 0, "compress_now 不应调用 _eval_threshold"


@pytest.mark.asyncio
async def test_compress_now_trigger_reason_passed_through(service):
    """v3.2.1 P0-1：compress_now 透传调用方的 trigger_reason，而不是硬编码 threshold_passed"""
    meta = _make_meta()
    msgs = []
    for i in range(50):
        msgs.append({"role": "user", "content": f"u{i}", "id": 2 * i + 1})
        msgs.append({"role": "assistant", "content": f"a{i}", "id": 2 * i + 2})
    _patch_load(service, msgs)

    result = await service.compress_now(
        "sess", "chat", meta,
        trigger_reason="token_threshold(9000/7000, 90%, cached=True)",
    )
    assert result is not None
    assert result.trigger_reason == "token_threshold(9000/7000, 90%, cached=True)"


@pytest.mark.asyncio
async def test_compress_now_trigger_reason_default_threshold_passed(service):
    """v3.2.1 P0-1：trigger_reason 未传时回退到通用 'threshold_passed'"""
    meta = _make_meta()
    msgs = []
    for i in range(50):
        msgs.append({"role": "user", "content": f"u{i}", "id": 2 * i + 1})
        msgs.append({"role": "assistant", "content": f"a{i}", "id": 2 * i + 2})
    _patch_load(service, msgs)

    result = await service.compress_now("sess", "chat", meta)
    assert result is not None
    assert result.trigger_reason == "threshold_passed"


@pytest.mark.asyncio
async def test_compress_now_force_overrides_trigger_reason(service):
    """v3.2.1 P0-1：force=True 时 trigger_reason 强制为 'force'，忽略 trigger_reason 参数"""
    meta = _make_meta()
    msgs = []
    for i in range(50):
        msgs.append({"role": "user", "content": f"u{i}", "id": 2 * i + 1})
        msgs.append({"role": "assistant", "content": f"a{i}", "id": 2 * i + 2})
    _patch_load(service, msgs)

    result = await service.compress_now(
        "sess", "chat", meta, force=True,
        trigger_reason="token_threshold(...)",
    )
    assert result is not None
    assert result.trigger_reason == "force"


@pytest.mark.asyncio
async def test_compress_now_with_meta_skips_resolve(service, monkeypatch):
    """session_meta 传入时 compress_now 不应再调 _resolve_session_meta"""
    meta = _make_meta()
    msgs = []
    for i in range(50):
        msgs.append({"role": "user", "content": f"u{i}", "id": 2 * i + 1})
        msgs.append({"role": "assistant", "content": f"a{i}", "id": 2 * i + 2})
    _patch_load(service, msgs)

    async def _explode(session_id, source_type):
        raise AssertionError("_resolve_session_meta 不应在 meta 已传入时被调用")
    monkeypatch.setattr(service, "_resolve_session_meta", _explode)

    result = await service.compress_now("sess", "chat", meta)
    assert result is not None


@pytest.mark.asyncio
async def test_compress_now_meta_none_re_resolves(service, monkeypatch):
    """session_meta=None 时 compress_now 内部重新解析"""
    msgs = []
    for i in range(50):
        msgs.append({"role": "user", "content": f"u{i}", "id": 2 * i + 1})
        msgs.append({"role": "assistant", "content": f"a{i}", "id": 2 * i + 2})
    _patch_load(service, msgs)

    called = {"n": 0}

    async def _fake(session_id, source_type):
        called["n"] += 1
        return _make_meta()
    monkeypatch.setattr(service, "_resolve_session_meta", _fake)

    result = await service.compress_now("sess", "chat", None)
    assert result is not None
    assert called["n"] == 1


@pytest.mark.asyncio
async def test_compress_now_force_true(service):
    """force=True 时 trigger_reason='force'"""
    meta = _make_meta()
    msgs = []
    for i in range(50):
        msgs.append({"role": "user", "content": f"u{i}", "id": 2 * i + 1})
        msgs.append({"role": "assistant", "content": f"a{i}", "id": 2 * i + 2})
    _patch_load(service, msgs)

    result = await service.compress_now("sess", "chat", meta, force=True)
    assert result is not None
    assert result.trigger_reason == "force"


@pytest.mark.asyncio
async def test_compress_now_propagates_persist_exception(service):
    """_persist_atomical 抛异常 → compress_now 透传"""
    meta = _make_meta()
    msgs = []
    for i in range(50):
        msgs.append({"role": "user", "content": f"u{i}", "id": 2 * i + 1})
        msgs.append({"role": "assistant", "content": f"a{i}", "id": 2 * i + 2})
    _patch_load(service, msgs)

    def _boom(*args, **kwargs):
        raise RuntimeError("disk full")
    service._persist_atomically = _boom

    with pytest.raises(RuntimeError, match="disk full"):
        await service.compress_now("sess", "chat", meta)


@pytest.mark.asyncio
async def test_compress_now_compress_empty_returns_none(service):
    """COMPRESS 区为空 → 返回 None"""
    meta = _make_meta()
    # 仅 10 条消息 < header_keep(3) + tail_keep(30)
    msgs = [{"role": "user", "content": "x", "id": i} for i in range(10)]
    _patch_load(service, msgs)

    result = await service.compress_now("sess", "chat", meta, force=True)
    assert result is None


@pytest.mark.asyncio
async def test_compress_session_compat_calls_check_then_compress(service, monkeypatch):
    """compress_session 兼容入口内部串联 check_threshold + compress_now"""
    msgs = []
    for i in range(50):
        msgs.append({"role": "user", "content": f"u{i}", "id": 2 * i + 1})
        msgs.append({"role": "assistant", "content": f"a{i}", "id": 2 * i + 2})
    _patch_load(service, msgs)
    service._model_limit_cache = 1_000  # 强制 token 阈值触发

    # patch _resolve_session_meta 返回高缓存
    async def _meta(session_id, source_type):
        return _make_meta(context_token_count=999999)
    service._resolve_session_meta = _meta

    # patch COUNT 查询
    import src.db.models as models_mod
    models_mod.MessageDB.count_messages_by_session = staticmethod(
        lambda session_id, include_compacted=False: 100
    )

    result = await service.compress_session("sess", "chat")
    assert result is not None
    # reason 应被 check_threshold 的精确原因覆盖
    assert "token_threshold" in result.trigger_reason


@pytest.mark.asyncio
async def test_compress_session_compat_below_threshold_returns_none(service, monkeypatch):
    """compress_session 兼容入口：未达阈值返回 None"""
    service._model_limit_cache = 1_000_000

    async def _meta(session_id, source_type):
        return _make_meta(context_token_count=100)
    service._resolve_session_meta = _meta

    import src.db.models as models_mod
    models_mod.MessageDB.count_messages_by_session = staticmethod(
        lambda session_id, include_compacted=False: 50
    )

    # compress_now 不应被调用
    async def _explode(*args, **kwargs):
        raise AssertionError("compress_now 不应在未达阈值时被调用")
    monkeypatch.setattr(service, "compress_now", _explode)

    result = await service.compress_session("sess", "chat")
    assert result is None
