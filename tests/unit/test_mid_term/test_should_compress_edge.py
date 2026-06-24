"""_should_compress 边界值测试（off-by-one / 极端配置）"""

import pytest

from src.config.settings import MidTermMemoryConfig
from src.memory.mid_term import ContextCompressionService


@pytest.fixture
def service(mid_term_settings):
    return ContextCompressionService(settings_cfg=mid_term_settings)


def test_empty_messages_returns_false(service):
    """空 messages 列表不应触发压缩（len=0 < 150, tokens=0 < threshold）"""
    should, reason = service._should_compress([], model_limit=128_000)
    assert should is False
    assert reason == ""


def test_single_short_message_returns_false(service):
    """单条短消息：既不到 token 阈值，也不到消息数阈值"""
    should, reason = service._should_compress([{"role": "user", "content": "hi"}], model_limit=128_000)
    assert should is False


def test_token_count_exactly_at_threshold_triggers(service):
    """token 数恰好等于阈值（边界值，>= 应触发）"""
    # 自己构造 settings 使阈值可控
    cfg = MidTermMemoryConfig(token_threshold_ratio=0.7, message_count_threshold=10_000)
    svc = ContextCompressionService(settings_cfg=cfg)
    # model_limit=10000 → token_threshold=7000
    # count_tokens 对 ASCII 字符按 other/4 估算：28000 字符 / 4 = 7000
    msgs = [{"role": "user", "content": "x" * 28_000}]
    should, reason = svc._should_compress(msgs, model_limit=10_000)
    # 边界值：>= 应触发
    assert should is True, "token count == threshold 应触发（>= 比较）"
    assert "token_threshold" in reason


def test_token_count_one_below_threshold_no_trigger(service):
    """token 数恰好比阈值少 1（边界值，off-by-one）"""
    cfg = MidTermMemoryConfig(token_threshold_ratio=0.7, message_count_threshold=10_000)
    svc = ContextCompressionService(settings_cfg=cfg)
    # ASCII 27996 字符 / 4 = 6999 < 7000
    msgs = [{"role": "user", "content": "x" * 27_996}]
    should, reason = svc._should_compress(msgs, model_limit=10_000)
    assert should is False, "token count = threshold - 1 不应触发"


def test_message_count_exactly_at_threshold_triggers(service):
    """消息条数恰好等于阈值（150）"""
    cfg = MidTermMemoryConfig(token_threshold_ratio=0.99, message_count_threshold=150)
    svc = ContextCompressionService(settings_cfg=cfg)
    # 消息极短，保证 token 不触发
    msgs = [{"role": "user", "content": "."} for _ in range(150)]
    should, reason = svc._should_compress(msgs, model_limit=1_000_000)
    assert should is True
    assert "message_threshold" in reason


def test_message_count_one_below_threshold_no_trigger(service):
    """消息条数恰好等于阈值 - 1（149）"""
    cfg = MidTermMemoryConfig(token_threshold_ratio=0.99, message_count_threshold=150)
    svc = ContextCompressionService(settings_cfg=cfg)
    msgs = [{"role": "user", "content": "."} for _ in range(149)]
    should, reason = svc._should_compress(msgs, model_limit=1_000_000)
    assert should is False


def test_model_limit_zero_does_not_crash(service):
    """model_limit=0 时不应抛异常（除零保护）"""
    # token_threshold = int(0 * 0.7) = 0
    # 任意非空消息 token_count >= 0 → 会触发 token 阈值
    # 但 pct 计算需要 model_limit > 0 保护
    should, reason = service._should_compress(
        [{"role": "user", "content": "x"}], model_limit=0
    )
    # threshold=0，token_count=0（"x" 1 字符 //3 = 0），0>=0 触发
    # 验证不抛异常 + reason 不含崩溃
    assert isinstance(should, bool)
    if should:
        assert "%" not in reason or "0%" in reason  # pct 计算不崩溃


def test_token_threshold_ratio_zero_triggers_immediately(service):
    """token_threshold_ratio=0 → token_threshold=0 → 任意 messages 立即触发"""
    cfg = MidTermMemoryConfig(token_threshold_ratio=0.0)
    svc = ContextCompressionService(settings_cfg=cfg)
    should, reason = svc._should_compress([{"role": "user", "content": "x"}], model_limit=10_000)
    assert should is True
    assert "token_threshold" in reason


def test_token_threshold_ratio_one_requires_full_context(service):
    """token_threshold_ratio=1.0 → 需 token 达 100% model_limit 才触发"""
    cfg = MidTermMemoryConfig(token_threshold_ratio=1.0, message_count_threshold=10_000)
    svc = ContextCompressionService(settings_cfg=cfg)
    # ASCII: 28000 字符 / 4 = 7000 token（70%）不应触发 100% 阈值
    msgs = [{"role": "user", "content": "x" * 28_000}]  # 7000 token
    should, _ = svc._should_compress(msgs, model_limit=10_000)
    assert should is False
    # ASCII: 40000 字符 / 4 = 10000 token 恰好触发
    msgs_full = [{"role": "user", "content": "x" * 40_000}]  # 10000 token
    should, reason = svc._should_compress(msgs_full, model_limit=10_000)
    assert should is True
    assert "token_threshold" in reason


def test_huge_token_count_does_not_overflow(service):
    """超大 token 数（INT_MAX 量级）不应溢出或抛异常"""
    # 构造 1M ASCII chars → 250K tokens (other/4)
    big = "x" * 1_000_000
    should, reason = service._should_compress(
        [{"role": "user", "content": big}], model_limit=100_000
    )
    assert should is True
    assert "token_threshold" in reason
    # pct 计算不崩
    assert "%" in reason
