"""端到端测试 fixtures — 检查凭证可用性，自动 skip"""

import os

import pytest


def pytest_collection_modifyitems(config, items):
    """根据环境变量自动跳过缺少凭证的 e2e 测试"""
    skip_llm = pytest.mark.skip(reason="当前 LLM provider 链未配置 API 密钥")
    skip_email = pytest.mark.skip(reason="未配置邮件测试凭证 (TEST_SMTP_SERVER)")
    skip_search = pytest.mark.skip(reason="未配置搜索 API 密钥 (TAVILY_API_KEY)")
    skip_browser = pytest.mark.skip(reason="未安装 playwright")

    for item in items:
        if "e2e" not in item.keywords:
            continue
        if "llm" in item.keywords:
            if not _has_llm_credentials():
                item.add_marker(skip_llm)
        if "email" in item.keywords:
            if not os.getenv("TEST_SMTP_SERVER"):
                item.add_marker(skip_email)
        if "search" in item.keywords:
            if not os.getenv("TAVILY_API_KEY"):
                item.add_marker(skip_search)
        if "browser" in item.keywords:
            try:
                import playwright  # noqa: F401
            except ImportError:
                item.add_marker(skip_browser)


def _has_llm_credentials() -> bool:
    """与 Gateway 一致，读取当前 provider（含启用的备用链）的实际 Key 配置。"""
    from src.config.settings import settings

    providers = [settings.llm.provider]
    if settings.llm.failover.enabled:
        providers.extend(settings.llm.failover.providers)
    return any(
        cfg is not None and bool(cfg.get_effective_keys())
        for cfg in (getattr(settings.llm, provider, None) for provider in providers)
    )


@pytest.fixture
def real_api_base() -> str:
    """运行中的测试 API 服务地址"""
    return os.getenv("TEST_API_BASE", "http://127.0.0.1:8001")
