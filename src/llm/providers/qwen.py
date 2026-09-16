"""
通义千问LLM提供者

通过 OpenAI 兼容接口实现阿里云通义千问模型调用
"""

import json
import time
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx
from loguru import logger

from src.config.settings import settings
from .base import BaseLLMProvider
from ..error_detail import describe_exception
from ..llm_call_logger import generate_request_id, log_llm_invoke


def _clamp_max_tokens(model: str, max_tokens: int) -> int:
    """按模型 clamp max_tokens 到 API 上限（上限从 config.yaml llm.model_max_tokens 读取），避免触发 400"""
    limit = (settings.llm.model_max_tokens or {}).get(model)
    if limit and max_tokens > limit:
        logger.warning(
            f"qwen {model} max_tokens={max_tokens} 超过上限 {limit}，自动 clamp"
        )
        return limit
    return max_tokens


class QwenProvider(BaseLLMProvider):
    """
    通义千问LLM提供者（OpenAI 兼容模式）

    通过阿里云百炼 OpenAI 兼容接口调用通义千问系列模型，
    请求/响应格式与 OpenAI Chat Completions API 一致。
    """

    # 默认 OpenAI 兼容端点，可通过 base_url 覆盖
    DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    # 日志/错误信息中的 provider 标识（子类如 MoonshotProvider 覆写）
    PROVIDER_NAME = "qwen"
    DISPLAY_NAME = "通义千问"
    # content 为空时是否回退 reasoning_content：仅推理模型子类（如 MoonshotProvider）
    # 开启；默认 False 保持 qwen 存量行为（思考内容不混入 content）不变
    REASONING_CONTENT_FALLBACK = False

    def __init__(
        self,
        api_key: str,
        model: str = "qwen3.8-flash",
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
        # 按模型 clamp max_tokens，避免超出上限触发 400（qwen-vl-* 上限 8192）
        max_tokens = _clamp_max_tokens(self.model, max_tokens)
        request_body = {
            "model": self.model,
            "messages": self._format_messages(messages, use_cache=settings.llm.context_cache),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        # qwen 推理模型关闭思考（enable_thinking）+ 显式缓存仅对 qwen 系模型生效；
        # 百炼第三方模型（deepseek/kimi/glm 等）不写入，避免不支持参数触发 400
        if self._is_qwen_model() and settings.llm.enable_thinking is not None:
            request_body["enable_thinking"] = settings.llm.enable_thinking

        # 添加工具定义
        if tools:
            request_body["tools"] = self._format_tools(tools)
            if tool_choice:
                request_body["tool_choice"] = tool_choice

        # 合并额外参数
        request_body.update(kwargs)

        # 子类钩子：按 provider/模型特性最终调整请求体（如 moonshot 对 kimi 推理模型省略 temperature）
        self._adjust_request_body(request_body)

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
                if not isinstance(result, dict):
                    # 200 但 body 不是 JSON 对象（如字面量 null），带上响应体便于定位
                    raise RuntimeError(
                        f"{self.DISPLAY_NAME}响应非 JSON 对象: {response.text[:500]}"
                    )

            parsed = self._parse_response(result)

            # 后端日志：记录LLM调用
            duration_ms = (time.perf_counter() - start_time) * 1000
            log_llm_invoke(
                request_id=result.get("id", invoke_id),
                provider=self.PROVIDER_NAME,
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
                provider=self.PROVIDER_NAME,
                model=self.model,
                request_params=request_body,
                error=f"HTTP {e.response.status_code}: {e.response.text[:2000]}",
                duration_ms=round(duration_ms, 2),
            )
            # 注意：用 loguru 占位符而非 f-string 嵌入 {e}，否则响应体中的 {"error":...}
            # 会被 loguru 内部 message.format() 当占位符解析，抛 KeyError 遮蔽原始异常
            logger.error(
                "{name}API请求失败: {etype} | status={status} | body={body}",
                name=self.DISPLAY_NAME,
                etype=type(e).__name__,
                status=e.response.status_code,
                body=e.response.text[:1000],
            )
            raise RuntimeError(f"{self.DISPLAY_NAME}API请求失败: {e.response.text}")
        except Exception as e:
            duration_ms = (time.perf_counter() - start_time) * 1000
            log_llm_invoke(
                request_id=invoke_id,
                provider=self.PROVIDER_NAME,
                model=self.model,
                request_params=request_body,
                error=describe_exception(e),
                duration_ms=round(duration_ms, 2),
            )
            logger.error(
                "{name}调用异常: {err}",
                name=self.DISPLAY_NAME,
                err=describe_exception(e),
            )
            raise

    def _adjust_request_body(self, request_body: Dict[str, Any]) -> None:
        """请求体最终调整钩子（默认无操作，子类按 provider/模型特性覆盖）。

        在 request_body.update(kwargs) 之后调用，provider 级约束优先于调用方参数。
        """

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
        # 流式请求体（OpenAI 兼容格式）
        # 按模型 clamp max_tokens，避免超出上限触发 400（qwen-vl-* 上限 8192）
        max_tokens = _clamp_max_tokens(self.model, max_tokens)
        request_body = {
            "model": self.model,
            "messages": self._format_messages(messages, use_cache=settings.llm.context_cache),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }

        # qwen 推理模型关闭思考（enable_thinking）仅对 qwen 系模型生效；百炼第三方模型不写入
        if self._is_qwen_model() and settings.llm.enable_thinking is not None:
            request_body["enable_thinking"] = settings.llm.enable_thinking

        if tools:
            logger.warning("通义千问 OpenAI 兼容接口不支持 tools 与 stream 同时使用，tools 参数将被忽略")

        request_body.update(kwargs)

        # 子类钩子（同 chat，见 _adjust_request_body）
        self._adjust_request_body(request_body)

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
                provider=self.PROVIDER_NAME,
                model=self.model,
                request_params=request_body,
                response_data={"content": full_content, "stream": True},
                duration_ms=round(duration_ms, 2),
            )

        except httpx.HTTPStatusError as e:
            duration_ms = (time.perf_counter() - start_time) * 1000
            log_llm_invoke(
                request_id=invoke_id,
                provider=self.PROVIDER_NAME,
                model=self.model,
                request_params=request_body,
                error=f"HTTP {e.response.status_code}: {e.response.text[:2000]}",
                duration_ms=round(duration_ms, 2),
            )
            # 用 loguru 占位符而非 f-string 嵌入 {e}，避免响应体含 {"error":...} 触发 KeyError
            logger.error(
                "{name}流式API请求失败: {etype} | status={status} | body={body}",
                name=self.DISPLAY_NAME,
                etype=type(e).__name__,
                status=e.response.status_code,
                body=e.response.text[:1000],
            )
            raise RuntimeError(f"{self.DISPLAY_NAME}流式API请求失败: {e.response.text}")
        except Exception as e:
            duration_ms = (time.perf_counter() - start_time) * 1000
            log_llm_invoke(
                request_id=invoke_id,
                provider=self.PROVIDER_NAME,
                model=self.model,
                request_params=request_body,
                error=describe_exception(e),
                duration_ms=round(duration_ms, 2),
            )
            logger.error(
                "{name}流式调用异常: {err}",
                name=self.DISPLAY_NAME,
                err=describe_exception(e),
            )
            raise

    def _parse_response(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """
        解析 OpenAI 兼容格式响应为标准格式

        Args:
            response: API 响应（OpenAI 格式）

        Returns:
            标准化的响应字典
        """
        # API 可能返回 "usage": null / "message": null（key 存在但值为 None，
        # .get 的默认值不生效），统一兜底为空 dict，避免 AttributeError
        response = response if isinstance(response, dict) else {}
        choices = response.get("choices") or []
        usage = response.get("usage") or {}
        prompt_details = usage.get("prompt_tokens_details") or {}
        cached_tokens = (
            prompt_details.get("cached_tokens", 0)
            if isinstance(prompt_details, dict)
            else 0
        ) or usage.get("prompt_cache_hit_tokens", usage.get("cached_tokens", 0))
        # 显式缓存创建 token（cache_control 首条消息触发，按输入价 125% 计费，计费端用）
        cache_creation_tokens = (
            prompt_details.get("cache_creation_input_tokens", 0)
            if isinstance(prompt_details, dict)
            else 0
        ) or usage.get("cache_creation_input_tokens", 0)

        if choices:
            message = choices[0].get("message") or {}
            content = message.get("content", "")
            tool_calls = message.get("tool_calls", [])
            finish_reason = choices[0].get("finish_reason", "stop")
        else:
            content = ""
            tool_calls = []
            finish_reason = "stop"

        # 推理模型（kimi-k3 等）答案在 content，为空则回退 reasoning_content
        # （仅 REASONING_CONTENT_FALLBACK 开启的子类生效，qwen 存量行为不变）
        if not content and self.REASONING_CONTENT_FALLBACK:
            content = (choices[0].get("message", {}) if choices else {}).get("reasoning_content", "") or ""

        result = {
            "content": content,
            "finish_reason": finish_reason,
            "usage": {
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
                "cached_tokens": cached_tokens,
                "cache_creation_tokens": cache_creation_tokens,
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
