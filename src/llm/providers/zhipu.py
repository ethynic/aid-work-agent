"""
智谱GLM LLM提供者

实现智谱AI GLM系列模型的API对接
"""

import json
import time
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx
from loguru import logger

from .base import BaseLLMProvider
from ..llm_call_logger import generate_request_id, log_llm_invoke


class ZhipuProvider(BaseLLMProvider):
    """
    智谱GLM LLM提供者
    
    支持智谱GLM系列模型的API调用
    """
    
    # API端点
    API_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    
    # 支持的模型
    SUPPORTED_MODELS = [
        "glm-4",
        "glm-4-air",
        "glm-4-airx",
        "glm-4-flash",
        "glm-3-turbo",
    ]
    
    def __init__(
        self,
        api_key: str,
        model: str = "glm-4",
        **kwargs
    ):
        """
        初始化智谱GLM提供者
        
        Args:
            api_key: 智谱AI API Key
            model: 模型名称
            **kwargs: 其他配置参数
        """
        super().__init__(api_key, model, **kwargs)
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        
        if model not in self.SUPPORTED_MODELS:
            logger.warning(f"模型 {model} 可能不被支持，支持的模型: {self.SUPPORTED_MODELS}")
    
    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs
    ) -> Dict[str, Any]:
        """
        发送对话请求
        
        Args:
            messages: 消息列表
            tools: 工具定义列表
            tool_choice: 工具选择策略
            temperature: 温度参数
            max_tokens: 最大生成token数
            **kwargs: 其他参数
        
        Returns:
            标准化的响应字典
        """
        # 构建请求体（智谱GLM使用OpenAI兼容格式）
        request_body = {
            "model": self.model,
            "messages": self._format_messages(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        
        # 添加工具定义
        if tools:
            request_body["tools"] = self._format_tools_zhipu(tools)
            if tool_choice:
                request_body["tool_choice"] = tool_choice
        
        # 合并额外参数
        request_body.update(kwargs)
        
        invoke_id = generate_request_id()
        start_time = time.perf_counter()
        parsed = None
        
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    self.API_URL,
                    headers=self.headers,
                    json=request_body,
                )
                response.raise_for_status()
                result = response.json()
            
            parsed = self._parse_response(result)

            # 后端日志：记录LLM调用
            duration_ms = (time.perf_counter() - start_time) * 1000
            log_llm_invoke(
                request_id=result.get("id", invoke_id),
                provider="zhipu",
                model=self.model,
                request_params=request_body,
                response_data=result,
                usage=parsed.get("usage"),
                duration_ms=round(duration_ms, 2),
            )
            
            return parsed
        
        except httpx.HTTPStatusError as e:
            duration_ms = (time.perf_counter() - start_time) * 1000
            log_llm_invoke(
                request_id=invoke_id,
                provider="zhipu",
                model=self.model,
                request_params=request_body,
                error=f"HTTP {e.response.status_code}: {e.response.text[:2000]}",
                duration_ms=round(duration_ms, 2),
            )
            logger.error(f"智谱GLM API请求失败: {e}")
            raise RuntimeError(f"智谱GLM API请求失败: {e.response.text}")
        except Exception as e:
            duration_ms = (time.perf_counter() - start_time) * 1000
            log_llm_invoke(
                request_id=invoke_id,
                provider="zhipu",
                model=self.model,
                request_params=request_body,
                error=str(e),
                duration_ms=round(duration_ms, 2),
            )
            logger.error(f"智谱GLM调用异常: {e}")
            raise
    
    async def stream_chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs
    ) -> AsyncGenerator[str, None]:
        """
        流式对话
        
        Args:
            messages: 消息列表
            tools: 工具定义列表
            tool_choice: 工具选择策略
            temperature: 温度参数
            max_tokens: 最大生成token数
            **kwargs: 其他参数
        
        Yields:
            流式输出的文本片段
        """
        request_body = {
            "model": self.model,
            "messages": self._format_messages(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        
        if tools:
            request_body["tools"] = self._format_tools_zhipu(tools)
        
        request_body.update(kwargs)
        
        invoke_id = generate_request_id()
        start_time = time.perf_counter()
        full_content = ""
        
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream(
                    "POST",
                    self.API_URL,
                    headers=self.headers,
                    json=request_body,
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if line.startswith("data:"):
                            data = line[5:].strip()
                            if data == "[DONE]":
                                break
                            if data:
                                try:
                                    chunk = json.loads(data)
                                    content = self._extract_stream_content(chunk)
                                    if content:
                                        full_content += content
                                        yield content
                                except json.JSONDecodeError:
                                    continue
            
            # 后端日志：记录流式LLM调用成功
            duration_ms = (time.perf_counter() - start_time) * 1000
            log_llm_invoke(
                request_id=invoke_id,
                provider="zhipu",
                model=self.model,
                request_params=request_body,
                response_data={"content": full_content, "stream": True},
                duration_ms=round(duration_ms, 2),
            )
        
        except httpx.HTTPStatusError as e:
            duration_ms = (time.perf_counter() - start_time) * 1000
            log_llm_invoke(
                request_id=invoke_id,
                provider="zhipu",
                model=self.model,
                request_params=request_body,
                error=f"HTTP {e.response.status_code}: {e.response.text[:2000]}",
                duration_ms=round(duration_ms, 2),
            )
            logger.error(f"智谱GLM流式API请求失败: {e}")
            raise RuntimeError(f"智谱GLM流式API请求失败: {e.response.text}")
        except Exception as e:
            duration_ms = (time.perf_counter() - start_time) * 1000
            log_llm_invoke(
                request_id=invoke_id,
                provider="zhipu",
                model=self.model,
                request_params=request_body,
                error=str(e),
                duration_ms=round(duration_ms, 2),
            )
            logger.error(f"智谱GLM流式调用异常: {e}")
            raise
    
    def _format_tools_zhipu(
        self,
        tools: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        格式化工具定义为智谱GLM格式（OpenAI兼容）
        
        Args:
            tools: 原始工具定义列表
        
        Returns:
            智谱GLM格式的工具定义列表
        """
        formatted_tools = []
        for tool in tools:
            tool_def = {
                "type": "function",
                "function": {
                    "name": tool.get("name", ""),
                    "description": tool.get("description", ""),
                }
            }
            
            # 处理参数定义
            input_schema = tool.get("input_schema", tool.get("parameters", {}))
            if input_schema:
                tool_def["function"]["parameters"] = input_schema
            
            formatted_tools.append(tool_def)
        
        return formatted_tools
    
    def _parse_response(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """
        解析智谱GLM响应为标准格式
        
        Args:
            response: 智谱GLM API响应
        
        Returns:
            标准化的响应字典
        """
        choices = response.get("choices", [])
        usage = response.get("usage", {})
        
        if choices:
            message = choices[0].get("message", {})
            content = message.get("content", "")
            tool_calls = message.get("tool_calls", [])
            finish_reason = choices[0].get("finish_reason", "stop")
        else:
            content = ""
            tool_calls = []
            finish_reason = "stop"
        
        result = {
            "content": content,
            "finish_reason": finish_reason,
            "usage": {
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            },
        }
        
        # 处理工具调用
        if tool_calls:
            result["tool_calls"] = [
                {
                    "id": tc.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": tc.get("function", {}).get("name", ""),
                        "arguments": tc.get("function", {}).get("arguments", ""),
                    }
                }
                for tc in tool_calls
            ]
            result["finish_reason"] = "tool_calls"
        
        return result
    
    def _extract_stream_content(self, chunk: Dict[str, Any]) -> Optional[str]:
        """
        从流式响应块中提取内容
        
        Args:
            chunk: 流式响应块
        
        Returns:
            文本内容或None
        """
        choices = chunk.get("choices", [])
        
        if choices:
            delta = choices[0].get("delta", {})
            return delta.get("content", "")
        
        return None
