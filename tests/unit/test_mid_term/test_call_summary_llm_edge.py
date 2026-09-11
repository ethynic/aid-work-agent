"""_call_summary_llm 边界：纯空白返回 / 超长返回 / existing_summary 行为差异"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from src.memory.mid_term import ContextCompressionService


@pytest.fixture
def service(mid_term_settings, mock_llm_for_summary):
    return ContextCompressionService(settings_cfg=mid_term_settings, llm_gateway=mock_llm_for_summary)


@pytest.mark.asyncio
async def test_whitespace_only_content_treated_as_failure(service, mock_llm_for_summary, monkeypatch):
    """LLM 返回纯空白字符（空格/换行/Tab）→ strip 后为空，视为失败进入重试"""
    monkeypatch.setattr(asyncio, "sleep", AsyncMock(return_value=None))
    mock_llm_for_summary.chat_lite = AsyncMock(return_value={"content": "   \n\t  \n", "tool_calls": None})
    result = await service._call_summary_llm(None, [{"role": "user", "content": "x"}])
    assert result is None
    # 首次 + 重试 2 次 = 3 次
    assert mock_llm_for_summary.chat_lite.await_count == 3


@pytest.mark.asyncio
async def test_long_content_preserved(service, mock_llm_for_summary, monkeypatch):
    """LLM 返回超长字符串（>> summary_max_tokens）→ 当前实现不做硬截断，原样返回。
    验证：成功路径正常返回（不做长度检查），因为长度限制由 LLM 端 max_tokens 控制。"""
    monkeypatch.setattr(asyncio, "sleep", AsyncMock(return_value=None))
    long_summary = "x" * 10_000  # 远超 summary_max_tokens=1500
    mock_llm_for_summary.chat_lite = AsyncMock(return_value={"content": long_summary, "tool_calls": None})
    result = await service._call_summary_llm(None, [{"role": "user", "content": "x"}])
    assert result == long_summary
    assert mock_llm_for_summary.chat_lite.await_count == 1


@pytest.mark.asyncio
async def test_existing_summary_none_vs_nonempty_both_pass(
    service, mock_llm_for_summary, monkeypatch
):
    """existing_summary=None 和 非空字符串 都应正常调用 LLM 并返回"""
    monkeypatch.setattr(asyncio, "sleep", AsyncMock(return_value=None))

    # 1) None
    mock_llm_for_summary.chat_lite = AsyncMock(return_value={"content": "fresh summary", "tool_calls": None})
    result_none = await service._call_summary_llm(None, [{"role": "user", "content": "x"}])
    assert result_none == "fresh summary"

    # 2) 非空
    mock_llm_for_summary.chat_lite = AsyncMock(return_value={"content": "merged summary", "tool_calls": None})
    result_existing = await service._call_summary_llm(
        "## 用户与背景\n- prior", [{"role": "user", "content": "x"}]
    )
    assert result_existing == "merged summary"

    # 验证两次调用都传了 prompt（包含 existing 信息）
    # 第二次调用的 prompt 应包含 "prior" 关键词
    second_call_kwargs = mock_llm_for_summary.chat_lite.call_args.kwargs
    prompt_text = second_call_kwargs["messages"][0]["content"]
    assert "prior" in prompt_text, "prompt 应包含 existing_summary 内容"


@pytest.mark.asyncio
async def test_llm_returns_none_result_treated_as_failure(
    service, mock_llm_for_summary, monkeypatch
):
    """LLM 返回 None（不是 dict）→ (None or {}).get 不崩，视为失败"""
    monkeypatch.setattr(asyncio, "sleep", AsyncMock(return_value=None))
    mock_llm_for_summary.chat_lite = AsyncMock(return_value=None)
    result = await service._call_summary_llm(None, [{"role": "user", "content": "x"}])
    assert result is None
    assert mock_llm_for_summary.chat_lite.await_count == 3


@pytest.mark.asyncio
async def test_llm_result_missing_content_key_treated_as_failure(
    service, mock_llm_for_summary, monkeypatch
):
    """LLM 返回 dict 但缺 content 键 → 不崩，视为失败"""
    monkeypatch.setattr(asyncio, "sleep", AsyncMock(return_value=None))
    mock_llm_for_summary.chat_lite = AsyncMock(return_value={"tool_calls": None})  # 无 content
    result = await service._call_summary_llm(None, [{"role": "user", "content": "x"}])
    assert result is None
