"""_get_model_limit 单测（v3.1 Phase 3）

覆盖：
- 已知 model_code 返回正确 context_length
- 未知 model_code 返回保守值 + warning
- 测试注入优先（_model_limit_cache 注入值优先于 settings 解析）
- 不同 provider 切换
- P1-2 修复：生产路径不缓存，provider 切换立即生效
"""

import pytest

from src.config.settings import LLMConfig, LLMProviderConfig
from src.memory.mid_term import ContextCompressionService, _DEFAULT_MODEL_LIMIT


@pytest.fixture
def service(mid_term_settings):
    return ContextCompressionService(settings_cfg=mid_term_settings)


def _set_llm(monkeypatch, provider, model):
    """patch settings.llm 为指定 provider + model"""
    fake_llm = LLMConfig(provider=provider)
    # 给对应 provider 字段填上 model
    setattr(fake_llm, provider, LLMProviderConfig(api_keys=["fake"], model=model))
    monkeypatch.setattr("src.memory.mid_term.settings.llm", fake_llm)


def test_known_model_deepseek(service, monkeypatch):
    """deepseek-chat → 128K"""
    _set_llm(monkeypatch, "deepseek", "deepseek-chat")
    service._model_limit_cache = None  # 重置缓存
    assert service._get_model_limit() == 128_000


def test_known_model_qwen(service, monkeypatch):
    """qwen-max → 32K"""
    _set_llm(monkeypatch, "qwen", "qwen-max")
    service._model_limit_cache = None
    assert service._get_model_limit() == 32_768


def test_known_model_zhipu(service, monkeypatch):
    """glm-4 → 128K"""
    _set_llm(monkeypatch, "zhipu", "glm-4")
    service._model_limit_cache = None
    assert service._get_model_limit() == 128_000


def test_unknown_model_returns_default(service, monkeypatch, capsys):
    """未知 model_code → 返回 _DEFAULT_MODEL_LIMIT"""
    _set_llm(monkeypatch, "deepseek", "totally-unknown-model-xyz")
    service._model_limit_cache = None
    limit = service._get_model_limit()
    assert limit == _DEFAULT_MODEL_LIMIT


def test_empty_model_code_returns_default(service, monkeypatch):
    """model_code 为空字符串 → 返回 _DEFAULT_MODEL_LIMIT"""
    _set_llm(monkeypatch, "deepseek", "")
    service._model_limit_cache = None
    assert service._get_model_limit() == _DEFAULT_MODEL_LIMIT


def test_injected_cache_overrides_settings(service, monkeypatch):
    """_model_limit_cache 注入的值优先于 settings 解析（测试用途，P1-2）"""
    _set_llm(monkeypatch, "deepseek", "deepseek-chat")
    service._model_limit_cache = 999_999  # 测试注入
    assert service._get_model_limit() == 999_999


def test_provider_switch_reflects_immediately(service, monkeypatch):
    """P1-2：生产路径不缓存，provider failover 后切换 model_code 立即生效。

    场景：第一次解析 deepseek-chat=128K，第二次切换 settings 为 unknown-model，
    _get_model_limit 应返回 _DEFAULT_MODEL_LIMIT（而非缓存的 128K）。
    """
    _set_llm(monkeypatch, "deepseek", "deepseek-chat")
    service._model_limit_cache = None  # 生产路径
    assert service._get_model_limit() == 128_000

    # 模拟 provider failover：切换 model_code 到未知
    _set_llm(monkeypatch, "deepseek", "totally-unknown-model-xyz")
    assert service._get_model_limit() == _DEFAULT_MODEL_LIMIT
