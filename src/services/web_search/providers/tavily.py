"""Tavily 适配器，保留既有 SDK 参数、返回格式与超时降级行为。"""

import asyncio
from typing import Any, Dict

from loguru import logger

from ..models import SearchRequest


def _is_timeout(error: Exception) -> bool:
    if isinstance(error, TimeoutError):
        return True
    try:
        from tavily.errors import TimeoutError as TavilyTimeoutError
    except ImportError:
        return False
    return isinstance(error, TavilyTimeoutError)


class TavilyProvider:
    def __init__(self, *, api_key: str, timeout_seconds: float):
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    async def search(self, request: SearchRequest) -> Dict[str, Any]:
        if not self.api_key:
            return {"success": False, "error": "未配置 TAVILY_API_KEY，请在环境变量或配置文件中设置"}
        params = {
            "query": request.keyword, "max_results": request.max_results,
            "include_answer": request.include_answer, "search_depth": request.search_depth,
            "topic": request.topic, "timeout": self.timeout_seconds,
        }
        for key in ("time_range", "include_domains", "exclude_domains"):
            value = getattr(request, key)
            if value:
                params[key] = value
        try:
            from tavily import TavilyClient
            client = TavilyClient(api_key=self.api_key)
            # 延续现有同步 SDK：取消停止异步等待，线程中的请求依赖 SDK 自身超时退出。
            response = await asyncio.get_running_loop().run_in_executor(None, lambda: client.search(**params))
            return self._result(response, request.include_answer)
        except Exception as error:
            logger.warning("Tavily搜索失败，类型={kind}", kind=type(error).__name__)
            if request.search_depth == "advanced" and _is_timeout(error):
                logger.info("尝试降级到 basic 模式重试...")
                try:
                    from tavily import TavilyClient
                    client = TavilyClient(api_key=self.api_key)
                    response = await asyncio.get_running_loop().run_in_executor(
                        None, lambda: client.search(**{**params, "search_depth": "basic"}),
                    )
                    return self._result(response, request.include_answer, fallback=True)
                except Exception as retry_error:
                    logger.warning("Tavily降级重试失败，类型={kind}", kind=type(retry_error).__name__)
            return {"success": False, "error": "Tavily搜索失败，请检查配置或稍后重试", "debug": type(error).__name__}

    @staticmethod
    def _result(response: Dict[str, Any], include_answer: bool, fallback: bool = False) -> Dict[str, Any]:
        results = [{"title": item.get("title", ""), "url": item.get("url", ""),
                    "content": item.get("content", ""), "score": item.get("score", 0)}
                   for item in response.get("results", [])]
        result = {"success": True, "results": results, "count": len(results),
                  "message": f"找到{len(results)}条结果（basic模式降级）" if fallback else f"找到{len(results)}条相关结果"}
        if include_answer and response.get("answer"):
            result["answer"] = response["answer"]
            if not fallback:
                result["message"] = f"找到{len(results)}条相关结果，已生成答案摘要"
        if not fallback and response.get("response_time"):
            result["response_time"] = response["response_time"]
        logger.info("Tavily搜索成功，找到{count}条结果", count=len(results))
        return result
