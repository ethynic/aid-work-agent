"""
端到端测试：真实网络搜索

需要环境变量：TAVILY_API_KEY 或既有 ZHIPU_API_KEYS
运行：pytest -m e2e tests/e2e/test_search_real.py
"""

import os

import pytest

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.search,
    pytest.mark.slow,
]


@pytest.mark.skipif(
    not os.getenv("TAVILY_API_KEY"),
    reason="需要 TAVILY_API_KEY",
)
class TestSearchReal:
    """真实搜索测试"""

    @pytest.mark.asyncio
    async def test_search(self, monkeypatch):
        """执行真实搜索"""
        from src.tools.search.search_tool import WebSearchTool
        from src.config.settings import settings

        monkeypatch.setattr(settings.tools.search, "provider", "tavily")

        tool = WebSearchTool()
        result = await tool.execute(keyword="Python programming", max_results=3)
        assert result.get("success") is True
        results = result["results"]
        assert 0 < len(results) <= 3
        assert result["count"] == len(results)
        assert all(item["url"] for item in results)


@pytest.mark.skipif(not os.getenv("ZHIPU_API_KEYS"), reason="需要既有 ZHIPU_API_KEYS")
class TestZhipuSearchReal:
    @pytest.mark.asyncio
    async def test_search_reuses_llm_credentials(self, monkeypatch):
        from src.config.settings import settings
        from src.tools.search.search_tool import WebSearchTool

        monkeypatch.setattr(settings.tools.search, "provider", "zhipu")
        result = await WebSearchTool().execute(keyword="智谱开放平台官方文档", max_results=3)
        assert result.get("success") is True, result.get("debug")
        assert result["provider"] == "zhipu"
        assert 0 < result["count"] <= 3
        assert result["count"] == len(result["results"])
        assert all(item["url"].startswith(("https://", "http://")) for item in result["results"])
