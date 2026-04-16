"""端到端测试 fixtures — 检查凭证可用性，自动 skip"""

import os

import pytest


def pytest_collection_modifyitems(config, items):
    """根据环境变量自动跳过缺少凭证的 e2e 测试"""
    skip_llm = pytest.mark.skip(reason="未配置 LLM API 密钥 (API_KEYS)")
    skip_email = pytest.mark.skip(reason="未配置邮件测试凭证 (TEST_SMTP_SERVER)")
    skip_search = pytest.mark.skip(reason="未配置搜索 API 密钥 (TAVILY_API_KEY)")
    skip_browser = pytest.mark.skip(reason="未安装 playwright")

    for item in items:
        if "llm" in item.keywords:
            if not os.getenv("API_KEYS"):
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


@pytest.fixture
def real_api_base() -> str:
    """运行中的测试 API 服务地址"""
    return os.getenv("TEST_API_BASE", "http://127.0.0.1:8001")
