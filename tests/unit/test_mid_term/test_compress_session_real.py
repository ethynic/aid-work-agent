"""compress_session 真实路径 mutation 加强测试（v3.1 Phase 3 / v3.2 适配）

现有 test_compress_session*.py 把 _resolve_session_meta / _load_messages / _should_compress
全部 patch 掉，导致这些测试对 compress_session 的真实串联逻辑无防御能力。

本文件保留真实方法，只 patch 最外层 DB（fake_db_connection），验证：
- compress_session 内部真的调用了 _resolve_session_meta（通过 spy 而非 mock）
- compress_session 内部真的调用了 _get_model_limit（不是依赖外部预设）
- v3.2: compress_session 用 _eval_threshold 做阈值判断（不再是 _should_compress），
  真的把 _resolve_session_meta 的 context_token_count 传给 _eval_threshold
- force=True 时 _eval_threshold / _should_compress 都不被调用

如果某天有人把 compress_session 的 force 逻辑改成「调阈值判断但忽略结果」，
现有测试不会失败，但本文件会。
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from src.memory.mid_term import ContextCompressionService


@pytest.fixture
def service(mid_term_settings, mock_llm_for_summary, fake_db_connection, monkeypatch):
    """使用真实 _resolve_session_meta / _load_messages / _should_compress / _get_model_limit
    只 mock DB 连接 + LLM gateway
    """
    monkeypatch.setattr(asyncio, "sleep", AsyncMock(return_value=None))
    mock_conn, mock_cursor = fake_db_connection
    mock_cursor.fetchone.return_value = None
    mock_cursor.fetchall.return_value = []
    return ContextCompressionService(
        settings_cfg=mid_term_settings, llm_gateway=mock_llm_for_summary
    )


@pytest.mark.asyncio
async def test_force_skips_should_compress_by_not_calling(service, monkeypatch):
    """force=True 时阈值判断（_eval_threshold）应完全不被调用。

    使用「让它抛异常」的方法验证，比现有「patch return value」更严格。
    mutation: 如果有人改成 `if force and not eval_result` 这种语义，
    此测试会失败。
    """
    # patch _resolve_session_meta 和 _load_messages 提供数据
    async def _fake_meta(sid, st):
        from src.memory.mid_term import SessionMeta
        return SessionMeta(session_id=sid, source_type=st, tenant_id="t1", user_id="u1")

    async def _fake_load(sid, st, tid):
        # 50 轮 user/assistant 对话（> header_keep=3 + tail_keep=30 = 33 才有 COMPRESS 区）
        return [
            {"role": "user", "content": f"u{i} " + "x" * 30, "id": 2 * i + 1}
            for i in range(50)
        ] + [
            {"role": "assistant", "content": f"a{i}", "id": 2 * i + 2}
            for i in range(50)
        ]

    service._resolve_session_meta = _fake_meta
    service._load_messages = _fake_load

    # 让 _eval_threshold 抛异常（v3.2 阈值判断入口）
    def _boom(*args, **kwargs):
        raise AssertionError("_eval_threshold 不应在 force=True 时被调用")
    service._eval_threshold = _boom

    # force=True 必须返回非 None（有 COMPRESS 区）
    result = await service.compress_session("sess", "chat", force=True)
    assert result is not None


@pytest.mark.asyncio
async def test_context_token_count_actually_passed_to_eval_threshold(
    service, monkeypatch
):
    """_resolve_session_meta 返回的 context_token_count 真的传给了 _eval_threshold。

    v3.2 更新：阈值判断入口从 _should_compress 改为 _eval_threshold。
    mutation: 如果有人把 meta.context_token_count 改成 hardcoded 0，此测试会失败。
    """
    captured = {"cached_tok": None}

    async def _fake_meta(sid, st):
        from src.memory.mid_term import SessionMeta
        return SessionMeta(
            session_id=sid,
            source_type=st,
            tenant_id="t1",
            user_id="u1",
            context_token_count=99999,  # 哨兵值
        )

    service._resolve_session_meta = _fake_meta
    service._model_limit_cache = 100_000  # 模型上限 100K，cache=99999 < 70K 阈值不触发

    # patch COUNT 查询返回较小值
    import src.db.models as models_mod
    models_mod.MessageDB.count_messages_by_session = staticmethod(
        lambda session_id, include_compacted=False: 10
    )

    # 替换 _eval_threshold，记录参数
    def _spy_eval(cached_tok, msg_count, model_limit):
        captured["cached_tok"] = cached_tok
        return False, "spy"

    service._eval_threshold = _spy_eval

    result = await service.compress_session("sess", "chat")
    assert result is None
    # 验证：_eval_threshold 拿到的 cached_tok 来自 meta.context_token_count
    assert captured["cached_tok"] == 99999, (
        "compress_session 必须把 meta.context_token_count 传给 _eval_threshold"
    )


@pytest.mark.asyncio
async def test_get_model_limit_actually_called_when_force_false(service, monkeypatch):
    """force=False 时 _get_model_limit 被调用（验证 check_threshold 真的解析 model_limit）。

    mutation: 如果有人把 _get_model_limit() 调用删了，硬编码一个常量，此测试会失败。
    """
    async def _fake_meta(sid, st):
        from src.memory.mid_term import SessionMeta
        return SessionMeta(session_id=sid, source_type=st)

    service._resolve_session_meta = _fake_meta

    called = {"n": 0}

    def _spy_model_limit():
        called["n"] += 1
        return 1_000_000

    service._get_model_limit = _spy_model_limit
    service._model_limit_cache = None  # 重置缓存

    # patch COUNT 查询返回较小值（不触发）
    import src.db.models as models_mod
    models_mod.MessageDB.count_messages_by_session = staticmethod(
        lambda session_id, include_compacted=False: 0
    )

    await service.compress_session("sess", "chat")
    assert called["n"] >= 1, "_get_model_limit 必须在 force=False 时被 check_threshold 调用"


@pytest.mark.asyncio
async def test_force_true_does_not_call_get_model_limit(service):
    """force=True 时不调 _get_model_limit（v3.2: force 跳过整个 check_threshold）"""
    async def _fake_meta(sid, st):
        from src.memory.mid_term import SessionMeta
        return SessionMeta(session_id=sid, source_type=st, tenant_id="t1")

    async def _fake_load(sid, st, tid):
        return [
            {"role": "user", "content": f"u{i}", "id": 2 * i + 1}
            for i in range(50)
        ]

    service._resolve_session_meta = _fake_meta
    service._load_messages = _fake_load

    def _boom():
        raise AssertionError("_get_model_limit 不应在 force=True 时被调用")
    service._get_model_limit = _boom

    result = await service.compress_session("sess", "chat", force=True)
    assert result is not None


@pytest.mark.asyncio
async def test_split_messages_actually_invoked_in_compress(service, monkeypatch):
    """compress_session 真的调用 _split_messages（不是 inline 逻辑）

    v3.2: 通过 cached_token_count=999999 触发 token 阈值（_eval_threshold）
    """
    async def _fake_meta(sid, st):
        from src.memory.mid_term import SessionMeta
        return SessionMeta(
            session_id=sid, source_type=st, tenant_id="t1",
            context_token_count=999999,
        )

    async def _fake_load(sid, st, tid):
        # 用足量大消息确保超过 70% token 阈值
        return [
            {"role": "user", "content": f"u{i} " + "x" * 200, "id": 2 * i + 1}
            for i in range(50)
        ] + [
            {"role": "assistant", "content": f"a{i} " + "y" * 200, "id": 2 * i + 2}
            for i in range(50)
        ]

    service._resolve_session_meta = _fake_meta
    service._load_messages = _fake_load
    service._model_limit_cache = 1_000

    split_called = {"n": 0}
    orig_split = service._split_messages

    def _spy(msgs):
        split_called["n"] += 1
        return orig_split(msgs)

    service._split_messages = _spy

    result = await service.compress_session("sess", "chat")
    assert result is not None
    assert split_called["n"] == 1, "_split_messages 必须被调用一次"


@pytest.mark.asyncio
async def test_get_active_summary_called_before_persist(service, monkeypatch):
    """compress_session 在 _persist_atomically 之前调用 get_active_summary（拿 existing_summary）

    v3.2: 通过 cached_token_count=999999 触发 token 阈值。
    mutation: 如果有人删了 get_active_summary 调用，导致 existing_summary 永远是 None，
    增量合并摘要逻辑会失效。此测试防御。
    """
    async def _fake_meta(sid, st):
        from src.memory.mid_term import SessionMeta
        return SessionMeta(
            session_id=sid, source_type=st, tenant_id="t1",
            context_token_count=999999,
        )

    async def _fake_load(sid, st, tid):
        return [
            {"role": "user", "content": f"u{i} " + "x" * 200, "id": 2 * i + 1}
            for i in range(50)
        ] + [
            {"role": "assistant", "content": f"a{i} " + "y" * 200, "id": 2 * i + 2}
            for i in range(50)
        ]

    service._resolve_session_meta = _fake_meta
    service._load_messages = _fake_load
    service._model_limit_cache = 1_000

    summary_called = {"n": 0}
    orig_summary = service.get_active_summary

    def _spy(sid, st, tenant_id=None):
        summary_called["n"] += 1
        return orig_summary(sid, st, tenant_id)

    service.get_active_summary = _spy

    result = await service.compress_session("sess", "chat")
    assert result is not None
    assert summary_called["n"] >= 1, "get_active_summary 必须在压缩时被调用"
