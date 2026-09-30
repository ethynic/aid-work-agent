"""_call_summary_llm 自适应预算 + length 续写 + _normalize_summary_usage 测试（2026-09-30）

覆盖：
- 自适应 max_tokens：clamp(摘要输入 tokens ÷ 50, summary_max_tokens, 4096) 的
  下限 / 中间值 / 上限三档
- finish_reason=length 续写一次拼接成功
- 续写后仍 length → truncated=True
- _normalize_summary_usage 三种 cache-hit 形态规范化
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

import src.memory.mid_term as mid_term_mod
from src.memory.mid_term import (
    ContextCompressionService,
    _normalize_summary_usage,
    _SUMMARY_MAX_TOKENS_CAP,
)


@pytest.fixture
def service(mid_term_settings, mock_llm_for_summary, monkeypatch):
    # 屏蔽直连 key → 走 gateway fallback 路径
    monkeypatch.setattr(
        "src.memory.mid_term._get_provider_api_key", lambda provider: None
    )
    # 屏蔽计费落库，单测不写 chat_records
    monkeypatch.setattr(
        "src.services.session_record.record_background_llm_usage", lambda *a, **k: None
    )
    monkeypatch.setattr(asyncio, "sleep", AsyncMock(return_value=None))
    return ContextCompressionService(
        settings_cfg=mid_term_settings, llm_gateway=mock_llm_for_summary
    )


# ============== 自适应 max_tokens ==============

@pytest.mark.asyncio
async def test_adaptive_max_tokens_lower_bound(service, mock_llm_for_summary, monkeypatch):
    """zone ÷ 50 < summary_max_tokens 时取配置下限（1500）"""
    monkeypatch.setattr(
        mid_term_mod, "count_text_tokens", lambda text: 50_000
    )  # 50000 // 50 = 1000 < 1500
    summary, _, _ = await service._call_summary_llm(None, [{"role": "user", "content": "x"}])
    assert summary is not None
    assert mock_llm_for_summary.chat_lite.call_args.kwargs["max_tokens"] == 1500


@pytest.mark.asyncio
async def test_adaptive_max_tokens_middle(service, mock_llm_for_summary, monkeypatch):
    """zone ÷ 50 在 [1500, 4096] 区间时取 zone ÷ 50"""
    monkeypatch.setattr(
        mid_term_mod, "count_text_tokens", lambda text: 150_000
    )  # 150000 // 50 = 3000
    summary, _, _ = await service._call_summary_llm(None, [{"role": "user", "content": "x"}])
    assert summary is not None
    assert mock_llm_for_summary.chat_lite.call_args.kwargs["max_tokens"] == 3000


@pytest.mark.asyncio
async def test_adaptive_max_tokens_capped(service, mock_llm_for_summary, monkeypatch):
    """zone ÷ 50 超上限时截到 _SUMMARY_MAX_TOKENS_CAP（4096）"""
    monkeypatch.setattr(
        mid_term_mod, "count_text_tokens", lambda text: 1_000_000
    )  # 1000000 // 50 = 20000 > 4096
    summary, _, _ = await service._call_summary_llm(None, [{"role": "user", "content": "x"}])
    assert summary is not None
    assert mock_llm_for_summary.chat_lite.call_args.kwargs["max_tokens"] == _SUMMARY_MAX_TOKENS_CAP


# ============== length 续写兜底 ==============

@pytest.mark.asyncio
async def test_length_continuation_concatenates(service, mock_llm_for_summary, monkeypatch):
    """首段 finish_reason=length，续写成功 → 拼接两段，truncated=False"""
    monkeypatch.setattr(
        mid_term_mod, "count_text_tokens", lambda text: 50_000
    )
    responses = [
        {"content": "part one", "finish_reason": "length",
         "usage": {"prompt_tokens": 10, "completion_tokens": 5}},
        {"content": "part two", "finish_reason": "stop",
         "usage": {"prompt_tokens": 20, "completion_tokens": 8}},
    ]

    async def fake_chat(**kwargs):
        return responses.pop(0)

    mock_llm_for_summary.chat_lite = AsyncMock(side_effect=fake_chat)
    summary, usage, truncated = await service._call_summary_llm(
        None, [{"role": "user", "content": "x"}]
    )
    assert summary == "part one\npart two"
    assert truncated is False
    # 续写调用的 usage 累加
    assert usage["prompt_tokens"] == 30
    assert usage["completion_tokens"] == 13
    # 第二次调用的 messages 应含首段 assistant + 续写指令
    cont_messages = mock_llm_for_summary.chat_lite.call_args.kwargs["messages"]
    assert cont_messages[1] == {"role": "assistant", "content": "part one"}
    assert "继续" in cont_messages[2]["content"]


@pytest.mark.asyncio
async def test_length_continuation_still_truncated(service, mock_llm_for_summary, monkeypatch):
    """续写一次后仍 finish_reason=length → 接受并置 truncated=True"""
    monkeypatch.setattr(
        mid_term_mod, "count_text_tokens", lambda text: 50_000
    )
    responses = [
        {"content": "part one", "finish_reason": "length",
         "usage": {"prompt_tokens": 10, "completion_tokens": 5}},
        {"content": "part two", "finish_reason": "length",
         "usage": {"prompt_tokens": 20, "completion_tokens": 8}},
    ]

    async def fake_chat(**kwargs):
        return responses.pop(0)

    mock_llm_for_summary.chat_lite = AsyncMock(side_effect=fake_chat)
    summary, usage, truncated = await service._call_summary_llm(
        None, [{"role": "user", "content": "x"}]
    )
    assert summary == "part one\npart two"
    assert truncated is True
    assert usage["prompt_tokens"] == 30


# ============== _normalize_summary_usage ==============

def test_normalize_usage_prompt_tokens_details_form():
    """OpenAI 风格 cache-hit 藏在 prompt_tokens_details.cached_tokens"""
    out = _normalize_summary_usage({
        "prompt_tokens": 100,
        "completion_tokens": 30,
        "total_tokens": 130,
        "prompt_tokens_details": {"cached_tokens": 40},
    })
    assert out == {
        "prompt_tokens": 100,
        "completion_tokens": 30,
        "total_tokens": 130,
        "cached_tokens": 40,
    }


def test_normalize_usage_prompt_cache_hit_tokens_form():
    """DeepSeek 风格顶层 prompt_cache_hit_tokens"""
    out = _normalize_summary_usage({
        "prompt_tokens": 100,
        "completion_tokens": 30,
        "total_tokens": 130,
        "prompt_cache_hit_tokens": 25,
    })
    assert out["cached_tokens"] == 25


def test_normalize_usage_no_cache_fields():
    """无 cache 字段 → cached_tokens=0"""
    out = _normalize_summary_usage({"prompt_tokens": 5, "completion_tokens": 2})
    assert out["cached_tokens"] == 0
    assert out["prompt_tokens"] == 5


def test_normalize_usage_none_and_non_dict():
    assert _normalize_summary_usage(None) is None
    assert _normalize_summary_usage("garbage") == {}

@pytest.mark.asyncio
async def test_length_continuation_failure_marks_truncated(service, mock_llm_for_summary, monkeypatch):
    """续写调用抛异常 → 首段可用但仍置 truncated=True（首段确实被截断）"""
    monkeypatch.setattr(
        mid_term_mod, "count_text_tokens", lambda text: 50_000
    )
    calls = {"n": 0}

    async def fake_chat(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"content": "part one", "finish_reason": "length",
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
        raise RuntimeError("continuation network error")

    mock_llm_for_summary.chat_lite = AsyncMock(side_effect=fake_chat)
    summary, usage, truncated = await service._call_summary_llm(
        None, [{"role": "user", "content": "x"}]
    )
    assert summary == "part one"
    assert truncated is True
    assert usage["prompt_tokens"] == 10
