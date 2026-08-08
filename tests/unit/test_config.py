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
