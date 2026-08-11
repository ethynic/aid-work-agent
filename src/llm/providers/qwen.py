"""
通义千问LLM提供者

通过 OpenAI 兼容接口实现阿里云通义千问模型调用
"""

import json
import time
import traceback
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx
from loguru import logger

from src.core.temp_logger import tlog
from .base import BaseLLMProvider
from ..llm_call_logger import generate_request_id, log_llm_invoke


class QwenProvider(BaseLLMProvider):
    """
    通义千问LLM提供者（OpenAI 兼容模式）

    通过阿里云百炼 OpenAI 兼容接口调用通义千问系列模型，
    请求/响应格式与 OpenAI Chat Completions API 一致。
    """

    # 默认 OpenAI 兼容端点，可通过 base_url 覆盖
    DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    def __init__(
        self,
        api_key: str,
        model: str = "qwen-plus",
        base_url: Optional[str] = None,
        **kwargs
    ):
        """
        初始化通义千问提供者

        Args:
            api_key: 阿里云 API Key
            model: 模型名称
            base_url: OpenAI 兼容 API 基础 URL
            **kwargs: 其他配置参数
        """
        super().__init__(api_key, model, base_url=base_url, **kwargs)
        self.api_url = f"{self.base_url or self.DEFAULT_BASE_URL}/chat/completions"
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 16384,
        **kwargs
    ) -> Dict[str, Any]:
        """
        发送对话请求（非流式）

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
        # 构建请求体（OpenAI 兼容格式）
        request_body = {
            "model": self.model,
            "messages": self._format_messages(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        # 添加工具定义
        if tools:
            request_body["tools"] = self._format_tools(tools)
            if tool_choice:
                request_body["tool_choice"] = tool_choice

        # 合并额外参数
        request_body.update(kwargs)

        invoke_id = generate_request_id()
        start_time = time.perf_counter()
        parsed = None

        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                response = await client.post(
                    self.api_url,
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
                provider="qwen",
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
                provider="qwen",
                model=self.model,
                request_params=request_body,
                error=f"HTTP {e.response.status_code}: {e.response.text[:2000]}",
                duration_ms=round(duration_ms, 2),
            )
            logger.error(f"通义千问API请求失败: {type(e).__name__}: {e}")
            # 临时调试：记录 400 响应体，定位视频创作对话报错
            tlog(
                "视频对话错误",
                "qwen.chat HTTPStatusError 状态={status} 响应体={body}",
                status=e.response.status_code,
                body=e.response.text[:3000],
                level="ERROR",
            )
            raise RuntimeError(f"通义千问API请求失败: {e.response.text}")
        except Exception as e:
            duration_ms = (time.perf_counter() - start_time) * 1000
            log_llm_invoke(
                request_id=invoke_id,
                provider="qwen",
                model=self.model,
                request_params=request_body,
                error=str(e),
                duration_ms=round(duration_ms, 2),
            )
            logger.error(f"通义千问调用异常: {type(e).__name__}: {e}")
            # 临时调试：记录非 HTTPStatusError 异常的完整 traceback
            tlog(
                "视频对话错误",
                "qwen.chat 非HTTP异常 类型={etype} 消息={emsg}\n{tb}",
                etype=type(e).__name__,
                emsg=str(e),
                tb=traceback.format_exc(),
                level="ERROR",
            )
            raise

    async def stream_chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 16384,
        **kwargs
    ) -> AsyncGenerator[str, None]:
        """
        流式对话（OpenAI 兼容格式）

        注意：阿里云百炼 OpenAI 兼容接口当前不支持 tools 与 stream 同时使用，
        调用方应确保流式模式下不传入 tools。

        Args:
            messages: 消息列表
            tools: 工具定义列表（流式模式下不支持）
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
            logger.warning("通义千问 OpenAI 兼容接口不支持 tools 与 stream 同时使用，tools 参数将被忽略")

        request_body.update(kwargs)

        invoke_id = generate_request_id()
        start_time = time.perf_counter()
        full_content = ""

        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                async with client.stream(
                    "POST",
                    self.api_url,
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
                provider="qwen",
                model=self.model,
                request_params=request_body,
                response_data={"content": full_content, "stream": True},
                duration_ms=round(duration_ms, 2),
            )

        except httpx.HTTPStatusError as e:
            duration_ms = (time.perf_counter() - start_time) * 1000
            log_llm_invoke(
                request_id=invoke_id,
                provider="qwen",
                model=self.model,
                request_params=request_body,
                error=f"HTTP {e.response.status_code}: {e.response.text[:2000]}",
                duration_ms=round(duration_ms, 2),
            )
            logger.error(f"通义千问流式API请求失败: {type(e).__name__}: {e}")
            raise RuntimeError(f"通义千问流式API请求失败: {e.response.text}")
        except Exception as e:
            duration_ms = (time.perf_counter() - start_time) * 1000
            log_llm_invoke(
                request_id=invoke_id,
                provider="qwen",
                model=self.model,
                request_params=request_body,
                error=str(e),
                duration_ms=round(duration_ms, 2),
            )
            logger.error(f"通义千问流式调用异常: {type(e).__name__}: {e}")
            raise

    def _parse_response(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """
        解析 OpenAI 兼容格式响应为标准格式

        Args:
            response: API 响应（OpenAI 格式）

        Returns:
            标准化的响应字典
        """
        choices = response.get("choices", [])
        usage = response.get("usage", {})
        prompt_details = usage.get("prompt_tokens_details", {})
        cached_tokens = (
            prompt_details.get("cached_tokens", 0)
            if isinstance(prompt_details, dict)
            else 0
        ) or usage.get("prompt_cache_hit_tokens", usage.get("cached_tokens", 0))

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
                "cached_tokens": cached_tokens,
            },
            "request_id": response.get("id", ""),
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
        从流式响应块中提取内容（OpenAI 兼容格式）

        Args:
            chunk: 流式响应块

        Returns:
            文本内容或 None
        """
        choices = chunk.get("choices", [])

        if choices:
            delta = choices[0].get("delta", {})
            return delta.get("content", "")

        return None
