#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试Web Search工具
"""

import asyncio
from src.tools.search.search_tool import WebSearchTool


async def test_duckduckgo_search():
    """测试DuckDuckGo搜索"""
    tool = WebSearchTool()
    
    print("=" * 60)
    print("测试 DuckDuckGo 搜索功能")
    print("=" * 60)
    
    # 测试1：搜索2026年春节电影票房
    print("\n测试1: 搜索'2026年春节电影票房'")
    result = await tool.execute(keyword="2026年春节电影票房", limit=5)
    print(f"成功: {result.get('success')}")
    print(f"提供商: {result.get('provider', 'N/A')}")
    print(f"结果数: {result.get('count', 0)}")
    if result.get('results'):
        for i, item in enumerate(result['results'], 1):
            print(f"\n  结果{i}:")
            print(f"  标题: {item.get('title')}")
            print(f"  URL: {item.get('url')}")
            print(f"  摘要: {item.get('snippet', '')[:100]}...")
    
    # 测试2：搜索最新的AI新闻
    print("\n" + "=" * 60)
    print("测试2: 搜索'最新AI新闻'")
    result = await tool.execute(keyword="最新AI新闻", limit=3)
    print(f"成功: {result.get('success')}")
    print(f"结果数: {result.get('count', 0)}")
    if result.get('results'):
        for i, item in enumerate(result['results'], 1):
            print(f"\n  结果{i}: {item.get('title')}")
    
    print("\n" + "=" * 60)
    print("测试完成!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_duckduckgo_search())
