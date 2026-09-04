"""BillingConfig 单元测试（Phase 0：video_gen_usage_factor）"""

import pytest

from src.config.settings import BillingConfig, create_settings


def test_billing_video_gen_usage_factor_default():
    """默认 video_gen_usage_factor=33"""
    cfg = BillingConfig()
    assert cfg.video_gen_usage_factor == 33
    # 主业务 usage_factor 保持原值
    assert cfg.usage_factor == 100


def test_billing_video_gen_usage_factor_env_override(monkeypatch):
    """VIDEO_GEN_USAGE_FACTOR env 覆盖生效"""
    monkeypatch.setenv("VIDEO_GEN_USAGE_FACTOR", "50")
    settings = create_settings()
    assert settings.billing.video_gen_usage_factor == 50


def test_billing_video_gen_usage_factor_invalid_env_fallback(monkeypatch):
    """非法 env 值时回退到 yaml/默认值"""
    monkeypatch.setenv("VIDEO_GEN_USAGE_FACTOR", "not-a-number")
    settings = create_settings()
    # 非法值不覆盖，沿用 yaml 中的 33
    assert settings.billing.video_gen_usage_factor == 33


def test_billing_usage_factor_unchanged(monkeypatch):
    """主业务 usage_factor 不受 video_gen_usage_factor 影响"""
    monkeypatch.delenv("VIDEO_GEN_USAGE_FACTOR", raising=False)
    settings = create_settings()
    assert settings.billing.usage_factor == 100


def test_billing_usage_factor_env_override(monkeypatch):
    """USAGE_FACTOR env 覆盖生效"""
    monkeypatch.setenv("USAGE_FACTOR", "200")
    settings = create_settings()
    assert settings.billing.usage_factor == 200


def test_billing_usage_factor_invalid_env_fallback(monkeypatch):
    """非法 USAGE_FACTOR env 值时回退到 yaml/默认值"""
    monkeypatch.setenv("USAGE_FACTOR", "not-a-number")
    settings = create_settings()
    # 非法值不覆盖，沿用 yaml 中的 100
    assert settings.billing.usage_factor == 100


# ---------------------------------------------------------------------------
# LLM_PROVIDER 链式写法（"a/b/c" 完整优先级链）
# ---------------------------------------------------------------------------

def test_llm_provider_chain_parsed(monkeypatch):
    """LLM_PROVIDER=a/b/c：首段为主 provider，其余为 failover 链，failover 自动启用"""
    monkeypatch.setenv("LLM_PROVIDER", "deepseek/qwen/zhipu")
    settings = create_settings()
    assert settings.llm.provider == "deepseek"
    assert settings.llm.failover.providers == ["qwen", "zhipu"]
    assert settings.llm.failover.enabled is True


def test_llm_provider_chain_reorder(monkeypatch):
    """调整顺序即时生效：qwen/deepseek/zhipu"""
    monkeypatch.setenv("LLM_PROVIDER", "qwen/deepseek/zhipu")
    settings = create_settings()
    assert settings.llm.provider == "qwen"
    assert settings.llm.failover.providers == ["deepseek", "zhipu"]


def test_llm_provider_single_value_means_no_failover(monkeypatch):
    """单一 provider 写法 = 不启用 failover（开关由写法自推导，无独立配置）"""
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    settings = create_settings()
    assert settings.llm.provider == "deepseek"
    assert settings.llm.failover.enabled is False
    assert settings.llm.failover.providers == []


def test_llm_provider_chain_invalid_name_raises(monkeypatch):
    """链式配置含非法 provider 名时 fail-fast"""
    monkeypatch.setenv("LLM_PROVIDER", "deepseek/foo/zhipu")
    with pytest.raises(ValueError, match="非法 provider"):
        create_settings()


def test_llm_provider_chain_duplicate_raises(monkeypatch):
    """链式配置存在重复 provider 时 fail-fast"""
    monkeypatch.setenv("LLM_PROVIDER", "qwen/qwen/zhipu")
    with pytest.raises(ValueError, match="重复"):
        create_settings()


def test_llm_provider_chain_single_segment_raises(monkeypatch):
    """链式写法只有一段（如 "qwen/"）时 fail-fast"""
    monkeypatch.setenv("LLM_PROVIDER", "qwen/")
    with pytest.raises(ValueError, match="至少需要 2 个"):
        create_settings()
