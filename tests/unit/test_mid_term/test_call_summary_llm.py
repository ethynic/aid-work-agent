"""_call_summary_llm 测试：成功 / 失败重试 / 重试耗尽 / 超时"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.memory.mid_term import ContextCompressionService


@pytest.fixture
def service(mid_term_settings, mock_llm_for_summary):
    svc = ContextCompressionService(settings_cfg=mid_term_settings, llm_gateway=mock_llm_for_summary)
    # 缩短重试间隔，加快测试
    return svc


@pytest.mark.asyncio
async def test_summary_llm_success(service, mock_llm_for_summary, monkeypatch):
    """mock LLM 成功返回摘要文本"""
    monkeypatch.setattr(asyncio, "sleep", AsyncMock(return_value=None))
    result = await service._call_summary_llm(
        existing_summary=None,
        new_messages=[{"role": "user", "content": "hello"}],
    )
    assert result is not None
    assert "test user" in result
    # 应该只调用 1 次（首次成功）
    assert mock_llm_for_summary.chat.await_count == 1


@pytest.mark.asyncio
async def test_summary_llm_retry_then_success(service, mock_llm_for_summary, monkeypatch):
    """首次失败，重试时成功"""
    monkeypatch.setattr(asyncio, "sleep", AsyncMock(return_value=None))
    call_count = [0]

    async def fake_chat(**kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            raise RuntimeError("transient error")
        return {"content": "recovered summary", "tool_calls": None}

    mock_llm_for_summary.chat = AsyncMock(side_effect=fake_chat)
    result = await service._call_summary_llm(None, [{"role": "user", "content": "x"}])
    assert result == "recovered summary"
    assert call_count[0] == 2  # 失败 1 次 + 成功 1 次


@pytest.mark.asyncio
async def test_summary_llm_exhausted_returns_none(service, mock_llm_for_summary, monkeypatch):
    """所有重试都失败 → 返回 None"""
    monkeypatch.setattr(asyncio, "sleep", AsyncMock(return_value=None))
    mock_llm_for_summary.chat = AsyncMock(side_effect=RuntimeError("always fails"))
    result = await service._call_summary_llm(None, [{"role": "user", "content": "x"}])
    assert result is None
    # 首次 + 重试 2 次 = 共 3 次
    assert mock_llm_for_summary.chat.await_count == 3


@pytest.mark.asyncio
async def test_summary_llm_timeout_returns_none(service, mock_llm_for_summary, monkeypatch):
    """超时（asyncio.wait_for 触发 TimeoutError）→ 重试耗尽后返回 None"""
    monkeypatch.setattr(asyncio, "sleep", AsyncMock(return_value=None))

    async def slow_chat(**kwargs):
        await asyncio.sleep(10)  # 远超 timeout

    # service._summary_timeout 默认 30s，测试中改小到 0.1s
    service._summary_timeout = 0.1
    mock_llm_for_summary.chat = AsyncMock(side_effect=slow_chat)
    result = await service._call_summary_llm(None, [{"role": "user", "content": "x"}])
    assert result is None
    assert mock_llm_for_summary.chat.await_count == 3


@pytest.mark.asyncio
async def test_summary_llm_empty_content_treated_as_failure(service, mock_llm_for_summary, monkeypatch):
    """空 content 也应视为失败，进入重试"""
    monkeypatch.setattr(asyncio, "sleep", AsyncMock(return_value=None))
    mock_llm_for_summary.chat = AsyncMock(return_value={"content": "", "tool_calls": None})
    result = await service._call_summary_llm(None, [{"role": "user", "content": "x"}])
    assert result is None
    assert mock_llm_for_summary.chat.await_count == 3
