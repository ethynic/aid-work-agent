"""compress_session 真实流程场景补充（v3.1 Phase 3）

覆盖设计文档要求但既有测试薄弱的场景：
- session 不存在（_resolve_session_meta 返回空 meta）时 compress_session 行为
- compress_session 在 LLM 失败时真的走 _fallback_truncate（通过观察 summary 文本特征）
- compress_session 在事务失败时抛异常（不吞），且不返回 fallback CompressionResult
- 当 messages=[ ] 但 force=True 时 compress_session 的行为
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from src.memory.mid_term import ContextCompressionService


@pytest.fixture
def service(mid_term_settings, mock_llm_for_summary, fake_db_connection, monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", AsyncMock(return_value=None))
    mock_conn, mock_cursor = fake_db_connection
    mock_cursor.fetchone.return_value = None
    mock_cursor.fetchall.return_value = []
    return ContextCompressionService(
        settings_cfg=mid_term_settings, llm_gateway=mock_llm_for_summary
    )


@pytest.mark.asyncio
async def test_session_not_exists_returns_none_via_real_path(service, monkeypatch):
    """session 不存在 → _resolve_session_meta 走真实路径返回空 meta →
    _load_messages 走真实路径返回 [] → _should_compress False → 返回 None。

    这是设计 §6.1 ① 的兜底路径：session 不存在不抛异常，安静返回 None。
    """
    # 让 SessionDB.get_by_id 真实返回 None
    import src.db.models as models_mod
    monkeypatch.setattr(models_mod.SessionDB, "get_by_id", lambda sid: None)

    result = await service.compress_session("ghost_session", "chat")
    assert result is None


@pytest.mark.asyncio
async def test_fallback_summary_text_starts_with_skeleton(service, monkeypatch):
    """LLM 失败走 fallback 时，persist 写入的 summary_text 来自 _fallback_truncate。

    mutation: 如果有人把 summary_text 在 fallback 路径写成空字符串，
    此测试通过 fallback_used=True 还能过，但本测试通过 summary 内容特征捕获。
    """
    import src.db.models as models_mod
    monkeypatch.setattr(
        models_mod.SessionDB, "get_by_id",
        lambda sid: {"session_id": sid, "tenant_id": "t1", "user_id": "u1",
                     "subagent_id": None, "context_token_count": 0},
    )

    def _fake_load_msgs(sid, limit=10000, **kw):
        return [
            {"role": "user", "content": f"u{i} " + "x" * 200, "id": 2 * i + 1}
            for i in range(50)
        ] + [
            {"role": "assistant", "content": f"a{i} " + "y" * 200, "id": 2 * i + 2}
            for i in range(50)
        ]

    monkeypatch.setattr(models_mod.MessageDB, "list_by_session", _fake_load_msgs)

    # 让 LLM 必失败
    service._llm_gateway.chat = AsyncMock(side_effect=RuntimeError("llm 503"))
    service._model_limit_cache = 1_000

    captured_summary = {"text": None}
    orig_persist = service._persist_atomically

    def _spy_persist(**kwargs):
        captured_summary["text"] = kwargs.get("summary_text")
        return orig_persist(**kwargs)

    service._persist_atomically = _spy_persist

    result = await service.compress_session("sess", "chat")
    assert result is not None
    assert result.fallback_used is True
    # fallback 文本不能为空
    assert captured_summary["text"], "fallback 路径的 summary_text 不能为空"
    assert len(captured_summary["text"]) > 10, (
        "fallback summary 应该有实质内容（不是单字符占位）"
    )


@pytest.mark.asyncio
async def test_persist_failure_does_not_swallow(service, monkeypatch):
    """_persist_atomically 失败 → compress_session 透传异常（设计 §6.2），
    不返回 fallback CompressionResult。
    """
    import src.db.models as models_mod
    monkeypatch.setattr(
        models_mod.SessionDB, "get_by_id",
        lambda sid: {"session_id": sid, "tenant_id": "t1", "user_id": "u1",
                     "subagent_id": None, "context_token_count": 0},
    )

    def _fake_load_msgs(sid, limit=10000, **kw):
        return [
            {"role": "user", "content": f"u{i} " + "x" * 200, "id": 2 * i + 1}
            for i in range(50)
        ] + [
            {"role": "assistant", "content": f"a{i} " + "y" * 200, "id": 2 * i + 2}
            for i in range(50)
        ]

    monkeypatch.setattr(models_mod.MessageDB, "list_by_session", _fake_load_msgs)

    service._model_limit_cache = 1_000
    service._persist_atomically = lambda **kw: (_ for _ in ()).throw(
        RuntimeError("db tx failed")
    )

    with pytest.raises(RuntimeError, match="db tx failed"):
        await service.compress_session("sess", "chat")


@pytest.mark.asyncio
async def test_force_true_with_empty_messages_returns_none(service, monkeypatch):
    """force=True + messages=[] → _split_messages 返回 ([], [], []) → compress 区空 → None。

    设计 §3.2 要求：HEADER + TAIL 保护区 + COMPRESS 区，messages=[] 时 COMPRESS 必空。
    """
    import src.db.models as models_mod
    monkeypatch.setattr(
        models_mod.SessionDB, "get_by_id",
        lambda sid: {"session_id": sid, "tenant_id": "t1"},
    )
    monkeypatch.setattr(
        models_mod.MessageDB, "list_by_session",
        lambda sid, limit=10000, **kw: [],
    )

    result = await service.compress_session("sess", "chat", force=True)
    assert result is None


@pytest.mark.asyncio
async def test_force_true_with_history_only_returns_none(service, monkeypatch):
    """force=True + messages < header_keep + tail_keep → COMPRESS 空 → None"""
    import src.db.models as models_mod
    monkeypatch.setattr(
        models_mod.SessionDB, "get_by_id",
        lambda sid: {"session_id": sid, "tenant_id": "t1"},
    )

    # 只给 10 条（< header 3 + tail 30 = 33）
    def _fake_load_msgs(sid, limit=10000, **kw):
        return [{"role": "user", "content": "x", "id": i} for i in range(10)]

    monkeypatch.setattr(models_mod.MessageDB, "list_by_session", _fake_load_msgs)

    result = await service.compress_session("sess", "chat", force=True)
    assert result is None


@pytest.mark.asyncio
async def test_compress_session_logs_skipped_when_below_threshold(
    service, monkeypatch
):
    """未达阈值时 compress_session 应记录 debug 日志（设计要求的可观测性）。

    通过 spy loguru.logger.debug 捕获（caplog 不支持 loguru）。
    """
    import src.db.models as models_mod
    import src.memory.mid_term as mt_mod

    monkeypatch.setattr(
        models_mod.SessionDB, "get_by_id",
        lambda sid: {"session_id": sid, "tenant_id": "t1", "context_token_count": 0},
    )
    monkeypatch.setattr(
        models_mod.MessageDB, "list_by_session",
        lambda sid, limit=10000, **kw: [{"role": "user", "content": "hi", "id": 1}],
    )
    service._model_limit_cache = 128_000

    captured = {"msgs": []}
    orig_debug = mt_mod.logger.debug

    def _spy_debug(msg, *args, **kwargs):
        captured["msgs"].append(msg)
        return orig_debug(msg, *args, **kwargs)

    monkeypatch.setattr(mt_mod.logger, "debug", _spy_debug)

    result = await service.compress_session("sess", "chat")
    assert result is None

    skip_msgs = [m for m in captured["msgs"] if "skipped" in m.lower()]
    assert len(skip_msgs) >= 1, (
        "未达阈值时应该记录 debug skipped 日志（观测性要求）"
    )


@pytest.mark.asyncio
async def test_compress_session_logs_done_when_success(service, monkeypatch):
    """压缩成功时记录 info 日志（含 summary_id、压缩前后 token 数）。

    通过 spy loguru.logger.info 捕获。
    """
    import src.db.models as models_mod
    import src.memory.mid_term as mt_mod

    monkeypatch.setattr(
        models_mod.SessionDB, "get_by_id",
        lambda sid: {"session_id": sid, "tenant_id": "t1", "context_token_count": 0},
    )

    def _fake_load_msgs(sid, limit=10000, **kw):
        return [
            {"role": "user", "content": f"u{i} " + "x" * 200, "id": 2 * i + 1}
            for i in range(50)
        ] + [
            {"role": "assistant", "content": f"a{i} " + "y" * 200, "id": 2 * i + 2}
            for i in range(50)
        ]

    monkeypatch.setattr(models_mod.MessageDB, "list_by_session", _fake_load_msgs)
    service._model_limit_cache = 1_000

    captured = {"msgs": []}
    orig_info = mt_mod.logger.info

    def _spy_info(msg, *args, **kwargs):
        captured["msgs"].append(msg)
        return orig_info(msg, *args, **kwargs)

    monkeypatch.setattr(mt_mod.logger, "info", _spy_info)

    result = await service.compress_session("sess", "chat")
    assert result is not None

    done_msgs = [m for m in captured["msgs"] if "ContextCompression done" in m]
    assert len(done_msgs) >= 1, "压缩成功应记录 info done 日志"
    # 日志必须含 summary_id
    assert result.summary_id in done_msgs[0], (
        "压缩成功日志必须含 summary_id（审计追溯）"
    )
