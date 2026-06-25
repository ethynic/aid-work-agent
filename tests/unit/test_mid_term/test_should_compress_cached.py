"""_should_compress 缓存优先逻辑测试（v3.1 Phase 3）

专门验证 cached_token_count > 0 时跳过 count_tokens 全量计算的设计。
"""

import pytest

from src.config.settings import MidTermMemoryConfig
from src.memory.mid_term import ContextCompressionService


@pytest.fixture
def service(mid_term_settings):
    return ContextCompressionService(settings_cfg=mid_term_settings)


def test_cached_overrides_count_tokens_when_above_threshold(service, monkeypatch):
    """缓存 > 0 且超阈值 → 用缓存触发，不调 count_tokens"""
    def _explode(messages):
        raise AssertionError("count_tokens should NOT be called when cache > 0")
    monkeypatch.setattr("src.memory.mid_term.count_tokens", _explode)

    # cached=8000, model_limit=10000, threshold=0.7*10000=7000, 8000>=7000 触发
    should, reason = service._should_compress([], 8000, model_limit=10_000)
    assert should is True
    assert "cached=True" in reason


def test_cached_below_threshold_no_trigger(service, monkeypatch):
    """缓存 > 0 但低于阈值 → False，不调 count_tokens"""
    def _explode(messages):
        raise AssertionError("count_tokens should NOT be called when cache > 0")
    monkeypatch.setattr("src.memory.mid_term.count_tokens", _explode)

    # cached=1000, model_limit=10000, threshold=7000, 1000<7000 且无消息 → False
    should, reason = service._should_compress([], 1000, model_limit=10_000)
    assert should is False
    assert reason == ""


def test_cached_zero_falls_back_to_count_tokens(service, monkeypatch):
    """缓存 = 0 → 回退到 count_tokens 全量计算"""
    called = {"count": 0}

    def _spy(messages):
        called["count"] += 1
        return 8000  # 假装 8000 token

    monkeypatch.setattr("src.memory.mid_term.count_tokens", _spy)

    # cached=0, model_limit=10000, count_tokens 返回 8000 >= 7000 触发
    should, reason = service._should_compress(
        [{"role": "user", "content": "x"}], 0, model_limit=10_000
    )
    assert should is True
    assert called["count"] >= 1, "count_tokens 必须被调用"
    assert "cached=False" in reason


def test_cached_zero_below_threshold(service, monkeypatch):
    """缓存 = 0 且 count_tokens 也不达阈值 → False"""
    def _stub(messages):
        return 100  # 远低于阈值

    monkeypatch.setattr("src.memory.mid_term.count_tokens", _stub)

    should, _ = service._should_compress(
        [{"role": "user", "content": "x"}], 0, model_limit=10_000
    )
    assert should is False


def test_cached_does_not_affect_message_count_threshold(service):
    """缓存不影响消息数阈值判断（仍用 len(messages)）"""
    # 缓存 = 0，但消息数 = 200 > 150，触发消息数阈值
    msgs = [{"role": "user", "content": "."} for _ in range(200)]
    should, reason = service._should_compress(msgs, 0, model_limit=1_000_000)
    assert should is True
    assert "message_threshold" in reason
