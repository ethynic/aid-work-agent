"""
通义千问LLM提供者

实现阿里云通义千问API的对接
"""

import json
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx
from loguru import logger

from .base import BaseLLMProvider


class QwenProvider(BaseLLMProvider):
    """
    通义千问LLM提供者
    
    支持通义千问系列模型的API调用
    """
    
    # API端点
    API_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation"
    
    # 支持的模型
    SUPPORTED_MODELS = [
        "qwen-turbo",
        "qwen-plus",
        "qwen-max",
        "qwen-max-longcontext",
    ]
    
    def __init__(
        self,
        api_key: str,
        model: str = "qwen-plus",
        **kwargs
    ):
        """
        初始化通义千问提供者
        
        Args:
            api_key: 阿里云API Key
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
        # 构建请求体
        request_body = {
            "model": self.model,
            "input": {
                "messages": self._format_messages_qwen(messages),
            },
            "parameters": {
                "temperature": temperature,
                "max_tokens": max_tokens,
                "result_format": "message",
            },
        }
        
        # 添加工具定义
        if tools:
            request_body["parameters"]["tools"] = self._format_tools_qwen(tools)
            if tool_choice:
                request_body["parameters"]["tool_choice"] = tool_choice
        
        # 合并额外参数
        request_body["parameters"].update(kwargs)
        
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    self.API_URL,
                    headers=self.headers,
                    json=request_body,
                )
                response.raise_for_status()
                result = response.json()
            
            return self._parse_response(result)
        
        except httpx.HTTPStatusError as e:
            logger.error(f"通义千问API请求失败: {e}")
            raise RuntimeError(f"通义千问API请求失败: {e.response.text}")
        except Exception as e:
            logger.error(f"通义千问调用异常: {e}")
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
            "input": {
                "messages": self._format_messages_qwen(messages),
            },
            "parameters": {
                "temperature": temperature,
                "max_tokens": max_tokens,
                "result_format": "message",
                "incremental_output": True,
            },
        }
        
        if tools:
            request_body["parameters"]["tools"] = self._format_tools_qwen(tools)
        
        request_body["parameters"].update(kwargs)
        
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
                            if data:
                                try:
                                    chunk = json.loads(data)
                                    content = self._extract_stream_content(chunk)
                                    if content:
                                        yield content
                                except json.JSONDecodeError:
                                    continue
        
        except httpx.HTTPStatusError as e:
            logger.error(f"通义千问流式API请求失败: {e}")
            raise RuntimeError(f"通义千问流式API请求失败: {e.response.text}")
        except Exception as e:
            logger.error(f"通义千问流式调用异常: {e}")
            raise
    
    def _format_messages_qwen(
        self,
        messages: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        格式化消息为通义千问格式
        
        Args:
            messages: 原始消息列表
        
        Returns:
            通义千问格式的消息列表
        """
        formatted = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            
            # 通义千问使用 system/user/assistant 角色
            if role == "system":
                formatted.append({
                    "role": role,
                    "content": str(content) if not isinstance(content, str) else content,
                })
            elif role == "user":
                formatted.append({
                    "role": role,
                    "content": str(content) if not isinstance(content, str) else content,
                })
            elif role == "assistant":
                # Assistant消息可能包含tool_calls
                assistant_msg = {
                    "role": role,
                    "content": str(content) if not isinstance(content, str) else content,
                }
                # 如果有tool_calls，也要包含
                if "tool_calls" in msg:
                    assistant_msg["tool_calls"] = msg["tool_calls"]
                formatted.append(assistant_msg)
            elif role == "tool":
                # 工具响应消息 - 必须包含tool_call_id
                tool_msg = {
                    "role": "tool",
                    "content": str(content),
                    "tool_call_id": msg.get("tool_call_id", ""),
                }
                formatted.append(tool_msg)
        
        return formatted
    
    def _format_tools_qwen(
        self,
        tools: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        格式化工具定义为通义千问格式
        
        Args:
            tools: 原始工具定义列表
        
        Returns:
            通义千问格式的工具定义列表
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
        解析通义千问响应为标准格式
        
        Args:
            response: 通义千问API响应
        
        Returns:
            标准化的响应字典
        """
        output = response.get("output", {})
        usage = response.get("usage", {})
        
        # 获取消息内容
        choices = output.get("choices", [])
        if choices:
            message = choices[0].get("message", {})
            content = message.get("content", "")
            tool_calls = message.get("tool_calls", [])
            finish_reason = choices[0].get("finish_reason", "stop")
        else:
            # 兼容旧格式
            content = output.get("text", "")
            tool_calls = output.get("tool_calls", [])
            finish_reason = output.get("finish_reason", "stop")
        
        result = {
            "content": content,
            "finish_reason": finish_reason,
            "usage": {
                "prompt_tokens": usage.get("input_tokens", 0),
                "completion_tokens": usage.get("output_tokens", 0),
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
        output = chunk.get("output", {})
        choices = output.get("choices", [])
        
        if choices:
            delta = choices[0].get("delta", {})
            return delta.get("content", "")
        
        return None
