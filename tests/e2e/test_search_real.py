"""
端到端测试：真实网络搜索

需要环境变量：TAVILY_API_KEY
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
    async def test_search(self):
        """执行真实搜索"""
        from src.tools.search.search_tool import WebSearchTool

        tool = WebSearchTool()
        result = await tool.execute(keyword="Python programming", limit=3)
        assert result.get("success") is True
        assert result.get("count", 0) > 0
