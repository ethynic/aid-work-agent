"""_get_model_limit 配置异常兜底（v3.1 Phase 3）"""

import pytest

from src.memory.mid_term import ContextCompressionService, _DEFAULT_MODEL_LIMIT


@pytest.fixture
def service(mid_term_settings):
    return ContextCompressionService(settings_cfg=mid_term_settings)


def test_provider_cfg_missing_returns_default(service, monkeypatch):
    """settings.llm 中 provider 字段不存在（getattr 返回 None）→ 默认值"""
    class _FakeLLM:
        provider = "totally_unknown_provider"

    monkeypatch.setattr("src.memory.mid_term.settings.llm", _FakeLLM())
    service._model_limit_cache = None

    # provider_cfg = None → model_code = "" → 走 _DEFAULT_MODEL_LIMIT
    limit = service._get_model_limit()
    assert limit == _DEFAULT_MODEL_LIMIT


def test_provider_cfg_model_empty_returns_default(service, monkeypatch):
    """provider_cfg.model = '' 空字符串 → model_code 空字符串 → 默认值"""
    from src.config.settings import LLMConfig, LLMProviderConfig
    fake_llm = LLMConfig(provider="deepseek")
    fake_llm.deepseek = LLMProviderConfig(api_keys=["x"], model="")
    monkeypatch.setattr("src.memory.mid_term.settings.llm", fake_llm)
    service._model_limit_cache = None

    limit = service._get_model_limit()
    assert limit == _DEFAULT_MODEL_LIMIT


def test_known_model_returns_exact_value(service, monkeypatch):
    """deepseek-reasoner → 64000（验证不是默认值，而是映射表里精确的值）"""
    from src.config.settings import LLMConfig, LLMProviderConfig
    fake_llm = LLMConfig(provider="deepseek")
    fake_llm.deepseek = LLMProviderConfig(api_keys=["x"], model="deepseek-reasoner")
    monkeypatch.setattr("src.memory.mid_term.settings.llm", fake_llm)
    service._model_limit_cache = None

    limit = service._get_model_limit()
    assert limit == 64_000


def test_settings_change_reflects_without_injection(service, monkeypatch):
    """P1-2：生产路径（_model_limit_cache=None）不缓存，settings 切换立即生效"""
    from src.config.settings import LLMConfig, LLMProviderConfig
    fake_llm = LLMConfig(provider="deepseek")
    fake_llm.deepseek = LLMProviderConfig(api_keys=["x"], model="deepseek-chat")
    monkeypatch.setattr("src.memory.mid_term.settings.llm", fake_llm)
    service._model_limit_cache = None  # 生产路径，不注入

    first = service._get_model_limit()
    assert first == 128_000

    # 切换到完全不同的 provider（模拟 failover）
    class _Empty:
        provider = "nothing"

    monkeypatch.setattr("src.memory.mid_term.settings.llm", _Empty())
    second = service._get_model_limit()
    # failover 后 provider=nothing，model_code="" → 走默认值
    assert second == _DEFAULT_MODEL_LIMIT
