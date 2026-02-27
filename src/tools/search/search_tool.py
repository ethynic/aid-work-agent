"""
搜索工具

实现网络搜索功能
"""

from typing import Any, Dict, List

import httpx
from loguru import logger

from src.tools.base import BaseTool
from src.config.settings import settings


class WebSearchTool(BaseTool):
    """网络搜索工具"""
    
    name = "web_search"
    description = "在网络上搜索信息"
    category = "search"
    parameters_schema = {
        "type": "object",
        "properties": {
            "keyword": {
                "type": "string",
                "description": "搜索关键词",
            },
            "limit": {
                "type": "integer",
                "description": "返回结果数量，默认5条",
            },
        },
        "required": ["keyword"],
    }
    
    async def execute(self, **kwargs) -> Dict[str, Any]:
        """
        执行网络搜索
        
        Args:
            keyword: 搜索关键词
            limit: 结果数量
        
        Returns:
            搜索结果
        """
        keyword = kwargs.get("keyword", "")
        limit = kwargs.get("limit", 5)
        
        if not keyword:
            return {
                "success": False,
                "error": "请提供搜索关键词",
            }
        
        # 获取搜索配置
        config = settings.tools.search
        
        if config.provider == "bing" and config.bing_api_key:
            return await self._bing_search(keyword, limit, config)
        else:
            # 返回模拟结果（演示模式）
            return await self._mock_search(keyword, limit)
    
    async def _bing_search(
        self,
        keyword: str,
        limit: int,
        config,
    ) -> Dict[str, Any]:
        """
        Bing搜索
        
        Args:
            keyword: 关键词
            limit: 数量限制
            config: 搜索配置
        
        Returns:
            搜索结果
        """
        try:
            async with httpx.AsyncClient() as client:
                url = "https://api.bing.microsoft.com/v7.0/search"
                response = await client.get(
                    url,
                    params={
                        "q": keyword,
                        "count": limit,
                        "mkt": "zh-CN",
                    },
                    headers={
                        "Ocp-Apim-Subscription-Key": config.bing_api_key,
                    }
                )
                
                data = response.json()
                
                results = []
                for item in data.get("webPages", {}).get("value", [])[:limit]:
                    results.append({
                        "title": item.get("name", ""),
                        "url": item.get("url", ""),
                        "snippet": item.get("snippet", ""),
                    })
                
                logger.info(f"Bing搜索成功: {keyword}, 找到{len(results)}条结果")
                
                return {
                    "success": True,
                    "results": results,
                    "count": len(results),
                    "message": f"找到{len(results)}条相关结果",
                }
        
        except Exception as e:
            logger.error(f"Bing搜索失败: {e}")
            return {
                "success": False,
                "error": f"搜索失败: {str(e)}",
            }
    
    async def _mock_search(
        self,
        keyword: str,
        limit: int,
    ) -> Dict[str, Any]:
        """
        模拟搜索（演示模式）
        
        Args:
            keyword: 关键词
            limit: 数量限制
        
        Returns:
            模拟搜索结果
        """
        # 返回模拟结果
        mock_results = [
            {
                "title": f"关于'{keyword}'的搜索结果1",
                "url": f"https://example.com/result1?q={keyword}",
                "snippet": f"这是关于{keyword}的第一条搜索结果摘要...",
            },
            {
                "title": f"关于'{keyword}'的搜索结果2",
                "url": f"https://example.com/result2?q={keyword}",
                "snippet": f"这是关于{keyword}的第二条搜索结果摘要...",
            },
            {
                "title": f"关于'{keyword}'的搜索结果3",
                "url": f"https://example.com/result3?q={keyword}",
                "snippet": f"这是关于{keyword}的第三条搜索结果摘要...",
            },
        ]
        
        results = mock_results[:limit]
        
        logger.info(f"模拟搜索: {keyword}, 返回{len(results)}条结果")
        
        return {
            "success": True,
            "results": results,
            "count": len(results),
            "message": f"找到{len(results)}条相关结果（演示模式）",
            "note": "当前为演示模式，请配置Bing API密钥以启用真实搜索",
        }
