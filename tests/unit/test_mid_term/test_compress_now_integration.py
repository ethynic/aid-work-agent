"""compress_now 端到端集成测试（Phase 1+2 同步路径）"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config.settings import MidTermMemoryConfig
from src.memory.mid_term import ContextCompressionService, count_tokens


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


@pytest.mark.asyncio
async def test_compress_now_end_to_end_success(service, mock_llm_for_summary, fake_db_connection):
    """端到端：100 条消息触发压缩，验证 DB 状态正确写入"""
    mock_conn, mock_cursor = fake_db_connection

    # 100 轮对话 = 200 messages，model_limit 小一点确保 token 触发
    # 让 token_threshold_ratio=0.1，model_limit=1000 → threshold=100
    # 200 条 * ~35 token ≈ 7000 token >> 100，必触发
    msgs = _build_large_messages(100)
    result = await service.compress_now(
        session_id="sess_e2e",
        source_type="chat",
        tenant_id="tenant_1",
        user_id="user_1",
        subagent_id=None,
        model_limit=1_000,
        messages=msgs,
    )

    assert result["compressed"] is True
    assert result["summary_id"] is not None
    assert result["summary_id"].startswith("csum_")
    assert result["fallback_used"] is False
    assert result["compressed_message_count"] > 0
    assert result["original_token_count"] > result["compressed_token_count"]

    # 验证 DB：INSERT 执行过
    all_sql = " ".join(str(c.args[0]) for c in mock_cursor.execute.call_args_list)
    assert "INSERT INTO chat_context_summaries" in all_sql
    assert mock_conn.commit.called


@pytest.mark.asyncio
async def test_compress_now_below_threshold_skips(service, mock_llm_for_summary):
    """未达阈值：compressed=False，不写 DB，不调 LLM"""
    msgs = [{"role": "user", "content": "hi", "id": 1}]
    result = await service.compress_now(
        session_id="sess_skip",
        source_type="chat",
        tenant_id=None,
        user_id=None,
        subagent_id=None,
        model_limit=128_000,
        messages=msgs,
    )
    assert result["compressed"] is False
    assert result["summary_id"] is None
    # 不应调用 LLM
    assert mock_llm_for_summary.chat.await_count == 0


@pytest.mark.asyncio
async def test_compress_now_fallback_when_llm_fails(
    service, mock_llm_for_summary, fake_db_connection
):
    """LLM 重试耗尽 → 走 _fallback_truncate，fallback_used=True，llm_tokens_used=0"""
    mock_llm_for_summary.chat = AsyncMock(side_effect=RuntimeError("llm down"))
    msgs = _build_large_messages(100)

    result = await service.compress_now(
        session_id="sess_fb",
        source_type="chat",
        tenant_id=None,
        user_id=None,
        subagent_id=None,
        model_limit=1_000,
        messages=msgs,
    )
    assert result["compressed"] is True
    assert result["fallback_used"] is True

    # 验证 INSERT 参数里 fallback_used=True
    # P1-3 后参数顺序：
    # 0:summary_id 1:session_id 2:source_type 3:tenant_id 4:user_id 5:subagent_id
    # 6:summary_text 7:session_id(max) 8:source_type(max)
    # 9:ids 10:count 11:orig_tok 12:comp_tok 13:ratio
    # 14:llm_provider 15:llm_model 16:llm_tokens_used 17:fallback_used 18:status
    mock_conn, mock_cursor = fake_db_connection
    for call in mock_cursor.execute.call_args_list:
        sql = str(call.args[0])
        if "INSERT INTO chat_context_summaries" in sql:
            insert_args = call.args[1]
            assert insert_args[17] is True, "INSERT 参数 fallback_used 必须为 True"
            return
    pytest.fail("未执行 INSERT")


@pytest.mark.asyncio
async def test_compress_now_none_messages_returns_not_compressed(service):
    """messages=None（主流程未传入）→ compressed=False，不抛异常"""
    result = await service.compress_now(
        session_id="sess_none",
        source_type="chat",
        tenant_id=None,
        user_id=None,
        subagent_id=None,
        model_limit=128_000,
        messages=None,
    )
    assert result["compressed"] is False
    assert result["summary_id"] is None
    assert result["reason"] == "no messages provided"


@pytest.mark.asyncio
async def test_compress_now_compress_section_empty_skips(service):
    """触发阈值但 COMPRESS 区为空（总数 <= header+tail）→ compressed=False"""
    # 构造 150 条短消息触发消息数阈值，但 header(3)+tail(30)=33 < 150，COMPRESS 必有内容
    # 此测试用反向构造：把 header_keep/tail_keep 调大，使 COMPRESS 为空
    cfg = MidTermMemoryConfig(header_keep=80, tail_keep=80, message_count_threshold=150)
    svc = ContextCompressionService(
        settings_cfg=cfg, llm_gateway=service._llm_gateway
    )
    msgs = [{"role": "user", "content": ".", "id": i} for i in range(150)]
    # 触发消息数阈值（150），但 150 > 80+80=160？不，150 < 160 → COMPRESS 为空
    result = await svc.compress_now(
        session_id="sess_empty_compress",
        source_type="chat",
        tenant_id=None,
        user_id=None,
        subagent_id=None,
        model_limit=1_000_000,
        messages=msgs,
    )
    assert result["compressed"] is False
    assert result["reason"] == "compress_section_empty"


@pytest.mark.asyncio
async def test_compress_now_two_rounds_incremental(
    service, mock_llm_for_summary, fake_db_connection
):
    """连续两次 compress_now：第二次 get_active_summary 返回非空，
    该 existing_summary 会被注入 prompt（增量合并）"""
    mock_conn, mock_cursor = fake_db_connection

    # 第一次：get_active_by_session 返回 None（无前驱）
    mock_cursor.fetchone.return_value = None

    msgs1 = _build_large_messages(100)
    r1 = await service.compress_now(
        session_id="sess_inc",
        source_type="chat",
        tenant_id=None,
        user_id=None,
        subagent_id=None,
        model_limit=1_000,
        messages=msgs1,
    )
    assert r1["compressed"] is True

    # 第二次：模拟已有 active summary（返回非空 dict）
    mock_cursor.fetchone.return_value = {
        "summary_id": "csum_prev",
        "summary_text": "## 用户与背景\n- prior context",
        "status": "active",
    }
    # reset mock 以观察第二次的 LLM 调用 prompt
    mock_llm_for_summary.chat = AsyncMock(return_value={"content": "merged", "tool_calls": None})

    msgs2 = _build_large_messages(120)  # 更多消息
    r2 = await service.compress_now(
        session_id="sess_inc",
        source_type="chat",
        tenant_id=None,
        user_id=None,
        subagent_id=None,
        model_limit=1_000,
        messages=msgs2,
    )
    assert r2["compressed"] is True

    # 验证第二次 LLM 调用的 prompt 包含 prior context（existing_summary 注入）
    call_kwargs = mock_llm_for_summary.chat.call_args.kwargs
    prompt_text = call_kwargs["messages"][0]["content"]
    assert "prior context" in prompt_text, "第二次压缩应把 existing_summary 注入 prompt"


@pytest.mark.asyncio
async def test_compress_now_compressed_message_ids_collected(
    service, mock_llm_for_summary, fake_db_connection
):
    """compress_now 应收集 COMPRESS 区所有消息的 id 传给 _persist_atomically"""
    mock_conn, mock_cursor = fake_db_connection
    mock_cursor.fetchone.return_value = None
    mock_cursor.fetchall.return_value = []

    msgs = _build_large_messages(100)
    result = await service.compress_now(
        session_id="sess_ids",
        source_type="chat",
        tenant_id=None,
        user_id=None,
        subagent_id=None,
        model_limit=1_000,
        messages=msgs,
    )
    assert result["compressed"] is True
    # compressed_message_count 应等于 COMPRESS 区消息数（有 id 的）
    # P1-3 后 INSERT...SELECT 参数索引：
    # 9:compressed_message_ids, 10:compressed_message_count
    for call in mock_cursor.execute.call_args_list:
        sql = str(call.args[0])
        if "INSERT INTO chat_context_summaries" in sql:
            insert_args = call.args[1]
            ids_list = insert_args[9]
            ids_count = insert_args[10]
            assert isinstance(ids_list, list)
            assert ids_count == len(ids_list)
            assert ids_count > 0
            return
    pytest.fail("未执行 INSERT")
