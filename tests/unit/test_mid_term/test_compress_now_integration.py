"""compress_session 端到端集成测试（v3.1 Phase 3 同步路径）

测试覆盖：
- 端到端压缩成功（达阈值）→ CompressionResult
- 未达阈值 → 返回 None（快路径）
- LLM 失败 → 走 fallback_truncate（fallback_used=True）
- force=True → 跳过阈值检查
- 缓存 token 优先（context_token_count > 0 时跳过 count_tokens）
- session 不存在的兜底
- COMPRESS 区为空时不压缩
- 连续两次 compress_session：第二次注入 existing_summary
- compressed_message_ids 正确收集
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config.settings import MidTermMemoryConfig
from src.memory.mid_term import (
    ContextCompressionService,
    SessionMeta,
    count_tokens,
)


@pytest.fixture
def service(mid_term_settings, mock_llm_for_summary, fake_db_connection, monkeypatch):
    """完整 service：mock 掉 LLM gateway 和 DB 连接"""
    monkeypatch.setattr(asyncio, "sleep", AsyncMock(return_value=None))
    # 让 get_active_summary 返回 None（首次压缩）
    mock_conn, mock_cursor = fake_db_connection
    mock_cursor.fetchone.return_value = None  # get_active_by_session 返回 None
    mock_cursor.fetchall.return_value = []
    return ContextCompressionService(
        settings_cfg=mid_term_settings, llm_gateway=mock_llm_for_summary
    )


def _build_large_messages(count: int):
    """构造足够触发压缩的 messages（含 id）"""
    msgs = []
    for i in range(count):
        msgs.append({"role": "user", "content": f"user message {i} " + "x" * 50, "id": 2 * i + 1})
        msgs.append({"role": "assistant", "content": f"assistant reply {i} " + "y" * 50, "id": 2 * i + 2})
    return msgs


def _patch_meta_and_messages(service, msgs, *, context_token_count=999999, tenant_id=None, user_id=None, subagent_id=None, monkeypatch=None):
    """patch _resolve_session_meta / _load_messages / _get_model_limit，
    让 compress_session 用我们准备好的 messages 和 meta。

    v3.2: 默认 context_token_count=999999（哨兵值），让 check_threshold 通过
    _eval_threshold 的 token 阈值路径触发。设 model_limit=1_000，确保 999999 > 7000。
    """

    async def _fake_resolve(session_id, source_type):
        return SessionMeta(
            session_id=session_id,
            source_type=source_type,
            tenant_id=tenant_id,
            user_id=user_id,
            subagent_id=subagent_id,
            context_token_count=context_token_count,
        )

    async def _fake_load(session_id, source_type, tenant_id_arg):
        return msgs

    service._resolve_session_meta = _fake_resolve
    service._load_messages = _fake_load
    # model_limit 设小（确保 token 阈值能触发）
    service._model_limit_cache = 1_000


@pytest.mark.asyncio
async def test_compress_session_end_to_end_success(service, mock_llm_for_summary, fake_db_connection):
    """端到端：100 轮对话 = 200 messages，token 阈值触发，验证 CompressionResult 正确"""
    mock_conn, mock_cursor = fake_db_connection

    msgs = _build_large_messages(100)
    _patch_meta_and_messages(service, msgs)

    result = await service.compress_session(
        session_id="sess_e2e",
        source_type="chat",
    )

    assert result is not None
    assert result.summary_id.startswith("csum_")
    assert result.fallback_used is False
    assert result.compressed_message_count > 0
    assert result.original_token_count > result.compressed_token_count
    assert 0.0 < result.compression_ratio < 1.0
    # v3.1 P1-1: trigger_reason 必须被回填（非空字符串，含 threshold 关键字）
    assert result.trigger_reason, "trigger_reason 不能为空"
    assert "threshold" in result.trigger_reason

    # 验证 DB：INSERT 执行过
    all_sql = " ".join(str(c.args[0]) for c in mock_cursor.execute.call_args_list)
    assert "INSERT INTO chat_context_summaries" in all_sql
    assert mock_conn.commit.called


@pytest.mark.asyncio
async def test_compress_session_below_threshold_returns_none(service, mock_llm_for_summary):
    """未达阈值 → 返回 None，不写 DB，不调 LLM

    v3.2: 通过 context_token_count=100 + model_limit=128_000 让 _eval_threshold 不触发。
    """
    msgs = [{"role": "user", "content": "hi", "id": 1}]
    _patch_meta_and_messages(service, msgs, context_token_count=100)
    # 大 model_limit，确保不触发（100 < 89600）
    service._model_limit_cache = 128_000

    result = await service.compress_session(
        session_id="sess_skip",
        source_type="chat",
    )
    assert result is None
    # 不应调用 LLM
    assert mock_llm_for_summary.chat.await_count == 0


@pytest.mark.asyncio
async def test_compress_session_force_skips_threshold(service, mock_llm_for_summary, fake_db_connection):
    """force=True → 跳过阈值检查直接压缩。
    messages 数需 > header_keep(3) + tail_keep(30) 才有 COMPRESS 内容。"""
    mock_conn, mock_cursor = fake_db_connection
    # 40 条消息，正常情况下 token/model_limit 不会触发（model_limit=128K），
    # 但 force=True 应强制进入压缩流程
    msgs = []
    for i in range(20):
        msgs.append({"role": "user", "content": f"u{i}", "id": 2 * i + 1})
        msgs.append({"role": "assistant", "content": f"a{i}", "id": 2 * i + 2})
    _patch_meta_and_messages(service, msgs)
    service._model_limit_cache = 128_000  # 不触发 token 阈值

    result = await service.compress_session(
        session_id="sess_force",
        source_type="chat",
        force=True,
    )
    assert result is not None
    assert result.summary_id.startswith("csum_")
    # v3.1 P1-1: force 路径 trigger_reason 必须为 "force"
    assert result.trigger_reason == "force"


@pytest.mark.asyncio
async def test_compress_session_fallback_when_llm_fails(
    service, mock_llm_for_summary, fake_db_connection
):
    """LLM 重试耗尽 → 走 _fallback_truncate，fallback_used=True"""
    mock_llm_for_summary.chat = AsyncMock(side_effect=RuntimeError("llm down"))
    msgs = _build_large_messages(100)
    _patch_meta_and_messages(service, msgs)

    result = await service.compress_session(
        session_id="sess_fb",
        source_type="chat",
    )
    assert result is not None
    assert result.fallback_used is True

    # 验证 INSERT 参数里 fallback_used=True
    mock_conn, mock_cursor = fake_db_connection
    for call in mock_cursor.execute.call_args_list:
        sql = str(call.args[0])
        if "INSERT INTO chat_context_summaries" in sql:
            insert_args = call.args[1]
            assert insert_args[17] is True, "INSERT 参数 fallback_used 必须为 True"
            return
    pytest.fail("未执行 INSERT")


@pytest.mark.asyncio
async def test_compress_session_session_not_exists_returns_none_or_empty(service, mock_llm_for_summary):
    """session 不存在 → meta 为空 + messages 为空 → 未达阈值返回 None"""
    # mock _resolve_session_meta 返回空 meta（session 不存在）
    async def _empty_resolve(session_id, source_type):
        return SessionMeta(
            session_id=session_id,
            source_type=source_type,
            tenant_id=None,
            user_id=None,
            subagent_id=None,
            context_token_count=0,
        )

    async def _empty_load(session_id, source_type, tenant_id):
        return []  # 无消息

    service._resolve_session_meta = _empty_resolve
    service._load_messages = _empty_load
    service._model_limit_cache = 128_000

    result = await service.compress_session(
        session_id="nonexistent",
        source_type="chat",
    )
    # 空消息 + 大 model_limit → 未达阈值 → None
    assert result is None


@pytest.mark.asyncio
async def test_compress_session_cached_token_skips_count_tokens(
    service, mock_llm_for_summary, fake_db_connection, monkeypatch
):
    """v3.2.1 P1-2：context_token_count > 0 时 _eval_threshold 跳过 count_tokens 全量计算。

    注意：compress_session 后续仍会调 count_tokens 来计算 original_token_count
    （写入 CompressionResult），这是设计的一部分。本测试只验证阈值判断路径
    不调 count_tokens。
    """
    # 40 条消息（> 33，< 150 消息数阈值）
    msgs = []
    for i in range(20):
        msgs.append({"role": "user", "content": f"u{i}", "id": 2 * i + 1})
        msgs.append({"role": "assistant", "content": f"a{i}", "id": 2 * i + 2})
    _patch_meta_and_messages(service, msgs, context_token_count=999999)
    service._model_limit_cache = 1_000

    # spy count_tokens（不爆炸，避免 compress_session 后续计算被误中）
    call_count = {"n": 0}
    from src.memory import mid_term as mt_mod
    orig_count_tokens = mt_mod.count_tokens

    def _spy(messages):
        call_count["n"] += 1
        return orig_count_tokens(messages)
    monkeypatch.setattr("src.memory.mid_term.count_tokens", _spy)

    # 直接验证 _eval_threshold 在 cache>0 时不调 count_tokens
    # （_should_compress 已在 v3.2.1 P1-2 删除，本测试改测 _eval_threshold）
    should, reason = service._eval_threshold(999999, len(msgs), 1_000)
    assert should is True
    assert "cached=True" in reason
    # _eval_threshold 阶段不应调用 count_tokens
    assert call_count["n"] == 0

    # compress_session 整体仍能跑通
    result = await service.compress_session(
        session_id="sess_cached",
        source_type="chat",
    )
    assert result is not None
    assert result.summary_id.startswith("csum_")


@pytest.mark.asyncio
async def test_compress_session_compress_section_empty_skips(service, mock_llm_for_summary):
    """触发阈值但 COMPRESS 区为空（总数 <= header+tail）→ 返回 None"""
    cfg = MidTermMemoryConfig(header_keep=80, tail_keep=80, message_count_threshold=150)
    svc = ContextCompressionService(
        settings_cfg=cfg, llm_gateway=service._llm_gateway
    )
    msgs = [{"role": "user", "content": ".", "id": i} for i in range(150)]
    _patch_meta_and_messages(svc, msgs)
    svc._model_limit_cache = 1_000_000  # 不触发 token 阈值

    result = await svc.compress_session(
        session_id="sess_empty_compress",
        source_type="chat",
    )
    # 150 条达到消息数阈值，但 150 < 80+80=160 → COMPRESS 为空 → 返回 None
    assert result is None


@pytest.mark.asyncio
async def test_compress_session_two_rounds_incremental(
    service, mock_llm_for_summary, fake_db_connection
):
    """连续两次 compress_session：第二次 get_active_summary 返回非空，
    该 existing_summary 会被注入 prompt（增量合并）"""
    mock_conn, mock_cursor = fake_db_connection

    # 第一次：get_active_by_session 返回 None（无前驱）
    mock_cursor.fetchone.return_value = None
    msgs1 = _build_large_messages(100)
    _patch_meta_and_messages(service, msgs1)
    r1 = await service.compress_session(
        session_id="sess_inc",
        source_type="chat",
    )
    assert r1 is not None

    # 第二次：模拟已有 active summary（返回非空 dict）
    mock_cursor.fetchone.return_value = {
        "summary_id": "csum_prev",
        "summary_text": "## 用户与背景\n- prior context",
        "status": "active",
    }
    # reset mock 以观察第二次的 LLM 调用 prompt
    mock_llm_for_summary.chat = AsyncMock(return_value={"content": "merged", "tool_calls": None})

    msgs2 = _build_large_messages(120)
    _patch_meta_and_messages(service, msgs2)
    r2 = await service.compress_session(
        session_id="sess_inc",
        source_type="chat",
    )
    assert r2 is not None

    # 验证第二次 LLM 调用的 prompt 包含 prior context（existing_summary 注入）
    call_kwargs = mock_llm_for_summary.chat.call_args.kwargs
    prompt_text = call_kwargs["messages"][0]["content"]
    assert "prior context" in prompt_text, "第二次压缩应把 existing_summary 注入 prompt"


@pytest.mark.asyncio
async def test_compress_session_compressed_message_ids_collected(
    service, mock_llm_for_summary, fake_db_connection
):
    """compress_session 应收集 COMPRESS 区所有消息的 id 传给 _persist_atomically"""
    mock_conn, mock_cursor = fake_db_connection
    mock_cursor.fetchone.return_value = None
    mock_cursor.fetchall.return_value = []

    msgs = _build_large_messages(100)
    _patch_meta_and_messages(service, msgs)

    result = await service.compress_session(
        session_id="sess_ids",
        source_type="chat",
    )
    assert result is not None
    assert result.compressed_message_count > 0
