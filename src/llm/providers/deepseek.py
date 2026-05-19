"""
DeepSeek LLM提供者

通过 OpenAI 兼容接口实现 DeepSeek 模型调用
"""

import json
import time
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx
from loguru import logger

from .base import BaseLLMProvider
from ..llm_call_logger import generate_request_id, log_llm_invoke


class DeepSeekProvider(BaseLLMProvider):
    """
    DeepSeek LLM提供者（OpenAI 兼容模式）

    通过 DeepSeek OpenAI 兼容接口调用 DeepSeek 系列模型，
    请求/响应格式与 OpenAI Chat Completions API 一致。
    """

    DEFAULT_BASE_URL = "https://api.deepseek.com"

    def __init__(
        self,
        api_key: str,
        model: str = "deepseek-chat",
        base_url: Optional[str] = None,
        **kwargs
    ):
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
        max_tokens: int = 4096,
        **kwargs
    ) -> Dict[str, Any]:
        request_body = {
            "model": self.model,
            "messages": self._format_messages(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        if tools:
            request_body["tools"] = self._format_tools(tools)
            if tool_choice:
                request_body["tool_choice"] = tool_choice

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

            duration_ms = (time.perf_counter() - start_time) * 1000
            log_llm_invoke(
                request_id=result.get("id", invoke_id),
                provider="deepseek",
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
                provider="deepseek",
                model=self.model,
                request_params=request_body,
                error=f"HTTP {e.response.status_code}: {e.response.text[:2000]}",
                duration_ms=round(duration_ms, 2),
            )
            logger.error("DeepSeek API请求失败: {}: {}", type(e).__name__, e)
            raise RuntimeError(f"DeepSeek API请求失败: {e.response.text}") from None
        except Exception as e:
            duration_ms = (time.perf_counter() - start_time) * 1000
            log_llm_invoke(
                request_id=invoke_id,
                provider="deepseek",
                model=self.model,
                request_params=request_body,
                error=str(e),
                duration_ms=round(duration_ms, 2),
            )
            logger.error("DeepSeek 调用异常: {}: {}", type(e).__name__, e)
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
        request_body = {
            "model": self.model,
            "messages": self._format_messages(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }

        if tools:
            request_body["tools"] = self._format_tools(tools)
            if tool_choice:
                request_body["tool_choice"] = tool_choice

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

            duration_ms = (time.perf_counter() - start_time) * 1000
            log_llm_invoke(
                request_id=invoke_id,
                provider="deepseek",
                model=self.model,
                request_params=request_body,
                response_data={"content": full_content, "stream": True},
                duration_ms=round(duration_ms, 2),
            )

        except httpx.HTTPStatusError as e:
            duration_ms = (time.perf_counter() - start_time) * 1000
            log_llm_invoke(
                request_id=invoke_id,
                provider="deepseek",
                model=self.model,
                request_params=request_body,
                error=f"HTTP {e.response.status_code}: {e.response.text[:2000]}",
                duration_ms=round(duration_ms, 2),
            )
            logger.error("DeepSeek 流式API请求失败: {}: {}", type(e).__name__, e)
            raise RuntimeError(f"DeepSeek 流式API请求失败: {e.response.text}") from None
        except Exception as e:
            duration_ms = (time.perf_counter() - start_time) * 1000
            log_llm_invoke(
                request_id=invoke_id,
                provider="deepseek",
                model=self.model,
                request_params=request_body,
                error=str(e),
                duration_ms=round(duration_ms, 2),
            )
            logger.error("DeepSeek 流式调用异常: {}: {}", type(e).__name__, e)
            raise

    def _parse_response(self, response: Dict[str, Any]) -> Dict[str, Any]:
        choices = response.get("choices", [])
        usage = response.get("usage", {})

        if choices:
            message = choices[0].get("message", {})
            content = message.get("content", "")
            reasoning_content = message.get("reasoning_content", "")
            tool_calls = message.get("tool_calls", [])
            finish_reason = choices[0].get("finish_reason", "stop")
        else:
            content = ""
            reasoning_content = ""
            tool_calls = []
            finish_reason = "stop"

        result = {
            "content": content,
            "finish_reason": finish_reason,
            "usage": {
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
                "cached_tokens": usage.get("prompt_tokens_details", {}).get("cached_tokens", 0),
            },
            "request_id": response.get("id", ""),
        }

        if reasoning_content:
            result["reasoning_content"] = reasoning_content

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
        choices = chunk.get("choices", [])

        if choices:
            delta = choices[0].get("delta", {})
            return delta.get("content", "")

        return None

    def _format_messages(
        self,
        messages: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        格式化消息，保留 reasoning_content 以兼容 DeepSeek 思考模式。

        DeepSeek 思考模式要求：如果 assistant 消息包含 reasoning_content，
        必须在后续请求中原样传回，否则 API 返回 400 错误。
        """
        formatted = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if role == "tool":
                formatted.append({
                    "role": "tool",
                    "tool_call_id": msg.get("tool_call_id", ""),
                    "content": json.dumps(content, ensure_ascii=False) if isinstance(content, dict) else (content if isinstance(content, str) else str(content)),
                })
            elif role == "assistant" and "tool_calls" in msg:
                assistant_msg = {
                    "role": "assistant",
                    "content": str(content) if not isinstance(content, str) else content,
                    "tool_calls": msg.get("tool_calls", [])
                }
                if msg.get("reasoning_content"):
                    assistant_msg["reasoning_content"] = msg["reasoning_content"]
                formatted.append(assistant_msg)
            elif role == "assistant" and msg.get("reasoning_content"):
                formatted.append({
                    "role": "assistant",
                    "content": str(content) if not isinstance(content, str) else content,
                    "reasoning_content": msg["reasoning_content"],
                })
            elif isinstance(content, list):
                formatted.append({"role": role, "content": content})
            else:
                formatted.append({"role": role, "content": str(content)})

        return formatted
