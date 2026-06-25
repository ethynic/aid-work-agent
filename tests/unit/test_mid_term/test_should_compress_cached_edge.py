"""_should_compress 缓存路径边界场景

补充测试 cached_token_count 与消息数阈值（150）交叉时的行为。
"""

import pytest

from src.memory.mid_term import ContextCompressionService


@pytest.fixture
def service(mid_term_settings):
    return ContextCompressionService(settings_cfg=mid_term_settings)


def test_cached_high_token_but_below_message_threshold_still_triggers(service, monkeypatch):
    """缓存 token 超阈值但消息数 < 150：token 阈值先触发"""
    def _explode(messages):
        raise AssertionError("count_tokens 不应被调用")
    monkeypatch.setattr("src.memory.mid_term.count_tokens", _explode)

    # cached=8000, model_limit=10000, threshold=7000, 8000>=7000 触发
    should, reason = service._should_compress([], 8000, model_limit=10_000)
    assert should is True
    assert "token_threshold" in reason
    assert "cached=True" in reason


def test_cached_low_token_but_many_messages_triggers_message_threshold(service, monkeypatch):
    """缓存 token 低 + 消息数 >= 150：触发消息数阈值（cached 不影响）"""
    def _explode(messages):
        raise AssertionError("count_tokens 不应被调用")
    monkeypatch.setattr("src.memory.mid_term.count_tokens", _explode)

    # cached=100（远低于阈值），但消息数 = 200
    msgs = [{"role": "user", "content": "."} for _ in range(200)]
    should, reason = service._should_compress(msgs, 100, model_limit=10_000)
    assert should is True
    assert "message_threshold" in reason
    # 缓存被读但没参与判断（消息数阈值不走 token 路径）
    # reason 中不含 cached=True（消息数阈值的 reason 模板）


def test_cached_zero_messages_below_both_thresholds(service, monkeypatch):
    """cached=0 + 空消息 → False"""
    def _spy(messages):
        return 0
    monkeypatch.setattr("src.memory.mid_term.count_tokens", _spy)

    should, reason = service._should_compress([], 0, model_limit=10_000)
    assert should is False
    assert reason == ""


def test_cached_negative_value_treated_as_zero(service, monkeypatch):
    """cached_token_count = -1（异常数据）→ 应当作 0 处理，回退到 count_tokens"""
    called = {"n": 0}

    def _spy(messages):
        called["n"] += 1
        return 100  # 远低于阈值

    monkeypatch.setattr("src.memory.mid_term.count_tokens", _spy)

    # cached=-1（异常），代码判断 > 0 为假，回退 count_tokens
    should, _ = service._should_compress(
        [{"role": "user", "content": "x"}], -1, model_limit=10_000
    )
    assert should is False
    # count_tokens 被调用（说明走了回退路径）
    assert called["n"] >= 1
