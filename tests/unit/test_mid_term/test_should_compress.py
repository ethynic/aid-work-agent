"""_should_compress 双阈值判断测试（v3.1：cached_token_count 优先）"""

import pytest

from src.memory.mid_term import ContextCompressionService


@pytest.fixture
def service(mid_term_settings):
    return ContextCompressionService(settings_cfg=mid_term_settings)


def test_below_both_thresholds_returns_false(service, build_simple_messages):
    """未达任何阈值 → False"""
    # model_limit = 128K，10 条短消息远不到阈值，cached=0 走全量计算
    msgs = build_simple_messages(10)
    should, reason = service._should_compress(msgs, 0, model_limit=128_000)
    assert should is False
    assert reason == ""


def test_token_threshold_only_returns_true(service):
    """仅达 token 阈值（70%）→ True + reason 含 'token'"""
    # count_tokens 对 ASCII 按 other/4 估算：28000 字符 / 4 = 7000 token
    # model_limit=10000，70% = 7000，7000 >= 7000 触发
    big_content = "x" * 28_000
    msgs = [{"role": "user", "content": big_content}]
    should, reason = service._should_compress(msgs, 0, model_limit=10_000)
    assert should is True
    assert "token_threshold" in reason
    assert "token" in reason


def test_message_count_threshold_only_returns_true(service):
    """仅达消息数阈值（150）→ True + reason 含 'message'"""
    # 构造 150 条超短消息：每条 5 字符 → 每条 ~1 token，总 token ~150
    # model_limit 设很大：1_000_000，token 阈值 = 700_000，不触发
    # 但消息数 = 150 >= 150，触发消息数阈值
    msgs = [{"role": "user", "content": "short"} for _ in range(150)]
    should, reason = service._should_compress(msgs, 0, model_limit=1_000_000)
    assert should is True
    assert "message_threshold" in reason
    assert "message" in reason


def test_both_thresholds_token_wins(service):
    """两个都达 → True + reason 含 'token'（token 优先）"""
    # token 数足够触发：28000 字符单条 + model_limit=10000
    # 消息数 = 200 也超过 150
    big_content = "x" * 28_000
    msgs = [{"role": "user", "content": big_content} for _ in range(200)]
    should, reason = service._should_compress(msgs, 0, model_limit=10_000)
    assert should is True
    # token 判断在前，所以返回 token_threshold
    assert "token_threshold" in reason


def test_cached_token_count_preferred_over_count_tokens(service, monkeypatch):
    """v3.1: cached_token_count > 0 时优先用缓存，跳过 count_tokens 全量计算"""
    # mock count_tokens，若被调用说明缓存路径未生效
    def _explode(messages):
        raise AssertionError("count_tokens should not be called when cache > 0")
    monkeypatch.setattr(
        "src.memory.mid_term.count_tokens", _explode
    )
    # cached=10000, model_limit=10000, threshold=0.7*10000=7000, 10000>=7000 触发
    should, reason = service._should_compress([], 10000, model_limit=10000)
    assert should is True
    assert "token_threshold" in reason
    assert "cached=True" in reason


def test_cached_token_count_below_threshold_returns_false(service, monkeypatch):
    """v3.1: cached_token_count > 0 但低于阈值时返回 False，且不调 count_tokens"""
    def _explode(messages):
        raise AssertionError("count_tokens should not be called when cache > 0")
    monkeypatch.setattr(
        "src.memory.mid_term.count_tokens", _explode
    )
    # cached=1000, model_limit=10000, threshold=7000, 1000<7000 + 空消息 → False
    should, reason = service._should_compress([], 1000, model_limit=10000)
    assert should is False
    assert reason == ""
