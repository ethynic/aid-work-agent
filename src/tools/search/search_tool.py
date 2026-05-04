"""
搜索工具

使用 Tavily API 实现 AI 友好的网络搜索
Tavily 提供:
- 清洗后的正文内容（非 SEO 描述）
- AI 生成的答案摘要
- 自动处理反爬、验证码等
"""

import asyncio
from typing import Any, Dict, List, Optional

from loguru import logger
from pydantic import BaseModel, Field

from src.tools.base import BaseTool
from src.config.settings import settings


class WebSearchInput(BaseModel):
    """网络搜索参数"""
    keyword: str = Field(..., description="搜索关键词，必须与用户提问语言一致")
    max_results: Optional[int] = Field(None, description="返回结果数量，默认5条（建议3-5条以保证质量）")
    include_answer: Optional[bool] = Field(None, description="是否返回AI生成的答案摘要，默认true")
    include_domains: Optional[List[str]] = Field(None, description="限定搜索的域名列表（如政府官网）")
    exclude_domains: Optional[List[str]] = Field(None, description="排除的域名列表（如社交媒体）")
    search_depth: Optional[str] = Field(None, description="搜索深度：basic快速返回，advanced深度抓取但耗时更长")
    topic: Optional[str] = Field(None, description="搜索类型：general通用搜索，news新闻搜索（时效性强）")
    limit: Optional[int] = Field(5, description="返回结果数量")


class WebSearchTool(BaseTool):
    """网络搜索工具 - 基于 Tavily API"""

    name = "web_search"
    description = "在网络上搜索实时信息、新闻、数据等，返回清洗后的内容和AI生成的答案摘要。关键词必须与用户提问语言一致。"
    usage_guide = ""
    display_name = "网络搜索"
    category = "search"
    InputModel = WebSearchInput

    def get_display_name(self, tool_args: Optional[Dict[str, Any]] = None) -> str:
        """动态显示名，展示搜索关键词"""
        base = self.display_name
        if tool_args:
            keyword = tool_args.get("keyword", "")
            if keyword:
                return f"{base}「{keyword[:20]}...」" if len(keyword) > 20 else f"{base}「{keyword}」"
        return base

    async def execute(self, **kwargs) -> Dict[str, Any]:
        """
        执行网络搜索
        
        Args:
            keyword: 搜索关键词或问题
            max_results: 结果数量（默认使用配置值）
            include_answer: 是否返回答案摘要（默认true）
            include_domains: 限定域名列表
            exclude_domains: 排除域名列表
            search_depth: 搜索深度 basic/advanced
            topic: 搜索类型 general/news
        
        Returns:
            搜索结果，包含 AI 生成的答案摘要和清洗后的内容
        """
        keyword = kwargs.get("keyword", "")
        
        if not keyword:
            return {
                "success": False,
                "error": "请提供搜索关键词",
            }
        
        # 获取搜索配置
        config = settings.tools.search
        api_key = config.tavily_api_key
        
        if not api_key:
            return {
                "success": False,
                "error": "未配置 TAVILY_API_KEY，请在环境变量或配置文件中设置",
            }
        
        # 合并参数：用户参数 > 配置默认值
        max_results = kwargs.get("max_results", config.max_results)
        include_answer = kwargs.get("include_answer", config.include_answer)
        search_depth = kwargs.get("search_depth", config.search_depth)
        include_domains = kwargs.get("include_domains")
        exclude_domains = kwargs.get("exclude_domains")
        topic = kwargs.get("topic", "general")
        
        return await self._tavily_search(
            keyword=keyword,
            max_results=max_results,
            include_answer=include_answer,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
            search_depth=search_depth,
            topic=topic,
            api_key=api_key,
        )
    
    async def _tavily_search(
        self,
        keyword: str,
        max_results: int,
        include_answer: bool,
        include_domains: Optional[List[str]],
        exclude_domains: Optional[List[str]],
        search_depth: str,
        topic: str,
        api_key: str,
    ) -> Dict[str, Any]:
        """
        Tavily 搜索 - AI 友好的网络搜索
        
        Tavily 特点:
        - 返回清洗后的正文内容（300-800字符），不是 SEO 描述
        - 自动生成 AI 答案摘要
        - 内置反爬处理（无头浏览器、IP轮换、验证码破解）
        - 实时抓取，不是索引数据
        """
        try:
            from tavily import TavilyClient
            
            client = TavilyClient(api_key=api_key)
            
            # 构建搜索参数
            search_params = {
                "query": keyword,
                "max_results": max_results,
                "include_answer": include_answer,
                "search_depth": search_depth,
                "topic": topic,
            }
            
            # 添加可选参数
            if include_domains:
                search_params["include_domains"] = include_domains
            if exclude_domains:
                search_params["exclude_domains"] = exclude_domains
            
            # 执行搜索（Tavily SDK 是同步的，包装为异步）
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: client.search(**search_params)
            )
            
            # 解析结果
            results = []
            for item in response.get("results", []):
                results.append({
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "content": item.get("content", ""),  # 清洗后的正文，非 SEO 描述
                    "score": item.get("score", 0),
                })
            
            # 构建返回结果
            result = {
                "success": True,
                "results": results,
                "count": len(results),
                "message": f"找到{len(results)}条相关结果",
            }
            
            # 如果有 AI 生成的答案摘要
            if include_answer and response.get("answer"):
                result["answer"] = response.get("answer")
                result["message"] = f"找到{len(results)}条相关结果，已生成答案摘要"
            
            # 添加响应时间
            if response.get("response_time"):
                result["response_time"] = response.get("response_time")
            
            logger.info(f"Tavily搜索成功: {keyword}, 找到{len(results)}条结果")
            
            return result
        
        except Exception as e:
            logger.error(f"Tavily搜索失败: {e}")
            
            # 如果是 advanced 模式超时，尝试降级到 basic
            if search_depth == "advanced":
                logger.info("尝试降级到 basic 模式重试...")
                try:
                    from tavily import TavilyClient
                    client = TavilyClient(api_key=api_key)
                    
                    loop = asyncio.get_event_loop()
                    response = await loop.run_in_executor(
                        None,
                        lambda: client.search(
                            query=keyword,
                            max_results=max_results,
                            include_answer=include_answer,
                            search_depth="basic",
                            topic=topic,
                        )
                    )
                    
                    results = []
                    for item in response.get("results", []):
                        results.append({
                            "title": item.get("title", ""),
                            "url": item.get("url", ""),
                            "content": item.get("content", ""),
                            "score": item.get("score", 0),
                        })
                    
                    result = {
                        "success": True,
                        "results": results,
                        "count": len(results),
                        "message": f"找到{len(results)}条结果（basic模式降级）",
                    }
                    
                    if include_answer and response.get("answer"):
                        result["answer"] = response.get("answer")
                    
                    return result
                
                except Exception as retry_error:
                    logger.error(f"降级重试也失败: {retry_error}")
            
            return {
                "success": False,
                "error": f"Tavily搜索失败: {str(e)}",
            }
