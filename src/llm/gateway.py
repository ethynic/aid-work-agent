"""
LLM网关

统一管理LLM提供者，提供模型路由和调用接口，
集成 KeyPool 支持多 API Key 轮询与并发控制。
"""

import asyncio
import time
from typing import Any, AsyncGenerator, Dict, List, Optional

from loguru import logger

from src.config.settings import settings
from src.core.text_sanitizer import sanitize_messages
from .error_detail import describe_exception
from .key_pool import KeyPool
from .providers.base import BaseLLMProvider
from .providers.deepseek import DeepSeekProvider
from .providers.moonshot import MoonshotProvider
from .providers.qwen import QwenProvider
from .providers.zhipu import ZhipuProvider


# 未在 llm.model_max_tokens 配置表中的模型，其默认 max_tokens 兜底值
DEFAULT_MAX_TOKENS = 16384

# chat_lite 关闭思考的参数（按目标 provider 区分，三通道统一语义：主链路默认思考开启，lite 关闭）
_LITE_THINKING_OFF_PARAMS = {
    "deepseek": {"thinking": {"type": "disabled"}},
    "qwen": {"enable_thinking": False},
    "zhipu": {"reasoning_effort": "low"},
}


def _lite_thinking_off_params(provider_name: str) -> Dict[str, Any]:
    """返回指定 provider 的 lite 关思考参数（无对应参数的 provider 返回空 dict）"""
    return dict(_LITE_THINKING_OFF_PARAMS.get(provider_name, {}))


def _build_key_pool(provider_name: str) -> KeyPool:
    """根据 provider 配置构建 KeyPool"""
    if provider_name == "qwen":
        cfg = settings.llm.qwen
    elif provider_name == "zhipu":
        cfg = settings.llm.zhipu
    elif provider_name == "deepseek":
        cfg = settings.llm.deepseek
    elif provider_name == "moonshot":
        cfg = settings.llm.moonshot
    else:
        raise ValueError(f"不支持的LLM提供者: {provider_name}")

    keys = cfg.get_effective_keys()
    if not keys:
        raise ValueError(
            f"LLM提供者 [{provider_name}] 未配置 API Key，"
            f"请在 .env 中设置 {provider_name.upper()}_API_KEYS"
        )

    return KeyPool(
        keys=keys,
        max_concurrent_per_key=cfg.max_concurrent_per_key,
        queue_timeout=cfg.queue_timeout,
    )


def _build_provider(provider_name: str, api_key: str, model: Optional[str] = None) -> BaseLLMProvider:
    """用给定 key 构建 Provider 实例（轻量，不缓存）。

    Args:
        provider_name: provider 名称
        api_key: API key
        model: 可选 model 覆盖，未传入则用 settings.llm.{provider}.model
    """
    if provider_name == "qwen":
        return QwenProvider(
            api_key=api_key,
            model=model or settings.llm.qwen.model,
            base_url=settings.llm.qwen.base_url,
        )
    elif provider_name == "zhipu":
        return ZhipuProvider(
            api_key=api_key,
            model=model or settings.llm.zhipu.model,
            base_url=settings.llm.zhipu.base_url,
        )
    elif provider_name == "deepseek":
        return DeepSeekProvider(
            api_key=api_key,
            model=model or settings.llm.deepseek.model,
            base_url=settings.llm.deepseek.base_url,
        )
    elif provider_name == "moonshot":
        return MoonshotProvider(
            api_key=api_key,
            model=model or settings.llm.moonshot.model,
            base_url=settings.llm.moonshot.base_url,
        )
    raise ValueError(f"不支持的LLM提供者: {provider_name}")


class LLMGateway:
    """
    LLM网关

    统一管理多个LLM提供者，提供：
    - 模型路由：根据配置选择合适的提供者
    - Key 池：多 API Key 轮询，Semaphore 控制每 Key 并发
    - 统一接口：屏蔽不同提供者的差异
    - 错误处理：统一的异常处理
    - Failover：主 provider 失败时自动切换到备用 provider
    """

    # 提供者注册表
    PROVIDERS = {
        "qwen": QwenProvider,
        "zhipu": ZhipuProvider,
        "deepseek": DeepSeekProvider,
        "moonshot": MoonshotProvider,
    }

    def __init__(self, provider_name: Optional[str] = None, model_codes: Optional[Dict[str, str]] = None,
                 use_failover: bool = True):
        """
        初始化LLM网关

        Args:
            provider_name: 指定提供者名称，默认使用配置中的提供者
            model_codes: 各 provider 的 model_code 覆盖，如 {'deepseek': 'deepseek-v4-pro', 'qwen': 'qwen3.7-plus'}。
                        未列出的 provider 使用全局默认 model。仅当指定 provider_name 时生效。
            use_failover: 是否启用全局 failover 链。模型绑定型调用（如客户端代理指定 kimi-k3
                        视觉模型）必须传 False——跨 provider 降级到文本模型既无法完成视觉
                        任务，又会按错误模型计价。
        """
        self.provider_name = provider_name or settings.llm.provider
        if self.provider_name not in self.PROVIDERS:
            raise ValueError(f"不支持的LLM提供者: {self.provider_name}")

        # 子智能体级别的 model_code 覆盖（仅与 provider_name 一起使用）
        self._model_codes: Dict[str, str] = model_codes or {}

        self._failover_enabled = (
            use_failover
            and hasattr(settings.llm, 'failover')
            and settings.llm.failover.enabled
        )

        if self._failover_enabled:
            from .failover import FailoverGateway
            self._failover = FailoverGateway(
                primary_name=self.provider_name,
                fallback_names=settings.llm.failover.providers,
                model_codes=self._model_codes,
            )
            logger.info(
                f"LLM网关初始化完成（Failover 模式），主提供者: {self.provider_name}，"
                f"备用: {settings.llm.failover.providers}，"
                f"model_codes 覆盖: {self._model_codes or '无'}"
            )
        else:
            # 无 key 时（如打包 CLI 无 .env）不阻断模块加载——CLI 走 ProxyLLMGateway
            # 不调用本实例；真正调用时由 _call_with_pool fail-loud。
            try:
                self._key_pool: KeyPool = _build_key_pool(self.provider_name)
                logger.info(
                    f"LLM网关初始化完成，提供者: {self.provider_name}，"
                    f"Key 池统计: {self._key_pool.stats()}，"
                    f"model_codes 覆盖: {self._model_codes or '无'}"
                )
            except ValueError as exc:
                logger.warning(
                    f"LLM网关未配置 provider={self.provider_name} 的 Key（{exc}）；"
                    f"模块加载不阻断，真正调用时再报错。"
                )
                self._key_pool = None

    # ------------------------------------------------------------------
    # 内部：从 Key 池获取 Provider 并执行调用
    # ------------------------------------------------------------------

    async def _call_with_pool(self, fn_name: str, **kwargs) -> Any:
        """
        非流式调用：从 Key 池获取一个 Key，构建 Provider 后执行。
        Key 在 async with 块结束时自动归还。
        """
        import time
        if "messages" in kwargs:
            kwargs["messages"] = sanitize_messages(kwargs["messages"])
        if self._key_pool is None:
            raise RuntimeError(
                f"LLM网关未配置 provider={self.provider_name} 的 Key，无法调用 {fn_name}；"
                f"请在 .env 配置 {self.provider_name.upper()}_API_KEYS。"
            )
        call_start = time.time()
        logger.info(f"[LLM] _call_with_pool started, provider={self.provider_name}, fn={fn_name}")
        
        try:
            async with self._key_pool.acquire() as api_key:
                model_override = self._model_codes.get(self.provider_name)
                provider = _build_provider(self.provider_name, api_key, model=model_override)
                method = getattr(provider, fn_name)
                logger.debug(f"[LLM] Provider built, calling {fn_name}")
                
                result = await method(**kwargs)
                
                call_duration = time.time() - call_start
                logger.info(f"[LLM] _call_with_pool completed, provider={self.provider_name}, fn={fn_name}, duration={call_duration:.2f}s")
                return result
                
        except asyncio.TimeoutError as e:
            call_duration = time.time() - call_start
            logger.error(f"[LLM] _call_with_pool timeout, provider={self.provider_name}, fn={fn_name}, duration={call_duration:.2f}s")
            raise
        except Exception as e:
            call_duration = time.time() - call_start
            # 用 loguru 占位符而非 f-string 嵌入 {e}，避免异常消息含 {"error":...} 时
            # loguru 内部 message.format() 把 {error} 当占位符解析抛 KeyError，遮蔽原始异常
            # err 用 describe_exception 提取，避免 httpx.ConnectError 等 str(e) 为空导致日志空白
            logger.opt(exception=True).error(
                "[LLM] _call_with_pool error, provider={p}, fn={fn}, duration={d:.2f}s, error: {err}",
                p=self.provider_name, fn=fn_name, d=call_duration,
                err=describe_exception(e),
            )
            raise

    async def _stream_with_pool(self, fn_name: str, **kwargs) -> AsyncGenerator[str, None]:
        """
        流式调用：Key 在整个流结束后才归还，保证流的完整性。
        """
        stream_start = time.time()
        chunk_count = 0
        if "messages" in kwargs:
            kwargs["messages"] = sanitize_messages(kwargs["messages"])
        logger.info(f"[LLM] _stream_with_pool started, provider={self.provider_name}, fn={fn_name}")
        
        try:
            async with self._key_pool.acquire() as api_key:
                model_override = self._model_codes.get(self.provider_name)
                provider = _build_provider(self.provider_name, api_key, model=model_override)
                method = getattr(provider, fn_name)
                logger.debug(f"[LLM] Provider built for streaming, calling {fn_name}")
                
                try:
                    async for chunk in method(**kwargs):
                        chunk_count += 1
                        yield chunk
                        
                        # 每100个chunk记录一次进度
                        if chunk_count % 100 == 0:
                            logger.debug(f"[LLM] Stream progress, chunks={chunk_count}")
                    
                    stream_duration = time.time() - stream_start
                    logger.info(f"[LLM] _stream_with_pool completed, provider={self.provider_name}, fn={fn_name}, duration={stream_duration:.2f}s, chunks={chunk_count}")
                    
                except asyncio.TimeoutError as e:
                    stream_duration = time.time() - stream_start
                    logger.error(f"[LLM] _stream_with_pool timeout during iteration, provider={self.provider_name}, fn={fn_name}, duration={stream_duration:.2f}s, chunks={chunk_count}")
                    raise
                except Exception as e:
                    stream_duration = time.time() - stream_start
                    logger.opt(exception=True).error(
                        "[LLM] _stream_with_pool error during iteration, provider={p}, fn={fn}, duration={d:.2f}s, chunks={c}, error: {err}",
                        p=self.provider_name, fn=fn_name, d=stream_duration, c=chunk_count,
                        err=describe_exception(e),
                    )
                    raise

        except asyncio.TimeoutError as e:
            stream_duration = time.time() - stream_start
            logger.error(f"[LLM] _stream_with_pool timeout (acquire key), provider={self.provider_name}, fn={fn_name}, duration={stream_duration:.2f}s")
            raise
        except Exception as e:
            stream_duration = time.time() - stream_start
            logger.opt(exception=True).error(
                "[LLM] _stream_with_pool error (acquire key), provider={p}, fn={fn}, duration={d:.2f}s, error: {err}",
                p=self.provider_name, fn=fn_name, d=stream_duration,
                err=describe_exception(e),
            )
            raise

    # ------------------------------------------------------------------
    # 公共接口（与旧版完全兼容）
    # ------------------------------------------------------------------

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
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
            响应字典
        """
        import time
        chat_start = time.time()
        max_tokens = self._resolve_max_tokens(max_tokens)
        logger.info(f"[LLM] chat() called, provider={self.provider_name}, messages_count={len(messages)}, has_tools={tools is not None}")

        try:
            if self._failover_enabled:
                result = await self._failover.chat(
                    messages=messages,
                    tools=tools,
                    tool_choice=tool_choice,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **kwargs,
                )
            else:
                result = await self._call_with_pool(
                    "chat",
                    messages=messages,
                    tools=tools,
                    tool_choice=tool_choice,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **kwargs,
                )
            
            chat_duration = time.time() - chat_start
            try:
                from src.services.llm_usage_meter import record_response_usage
                record_response_usage(result)
            except Exception:
                # Usage accounting is observational and must not break LLM calls.
                pass
            logger.info(f"[LLM] chat() completed, duration={chat_duration:.2f}s, has_content={bool(result.get('content'))}, has_tool_calls={bool(result.get('tool_calls'))}")
            return result
            
        except Exception as e:
            chat_duration = time.time() - chat_start
            # 用 loguru 占位符而非 f-string 嵌入 {e}，避免异常消息含 {"error":...} 触发 KeyError
            logger.opt(exception=True).error(
                "[LLM] chat() failed, duration={d:.2f}s, error: {err}",
                d=chat_duration, err=describe_exception(e),
            )
            raise

    async def chat_lite(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """用轻量小模型（lite_model）调用，支持跨 provider。

        lite_model 配置格式见 LLMConfig.get_lite_target：
        - "provider/model"（如 "qwen/qwen3.8-flash"）：跨 provider 调用，用该 provider 的
          key/base_url 构建独立 Provider 直连，不参与主链路 failover（指定即专用）
        - 纯模型名 / 未配置：走当前 provider 的完整链路（含 failover），显式传 model 覆盖

        对目标 provider 自动加关思考参数（三通道统一：主链路默认思考开启，lite 关闭）：
        deepseek -> thinking={"type": "disabled"}；qwen -> enable_thinking=False；
        zhipu -> reasoning_effort="low"（GLM Flash 始终思考，low 档 reasoning 归零）。

        Returns:
            与 chat() 一致的响应字典
        """
        import time
        from src.config.settings import settings as _settings

        lite_provider, lite_model = _settings.llm.get_lite_target()
        if not lite_model:
            raise ValueError(
                f"lite_model 解析失败：provider={lite_provider}，模型名为空；"
                f"请检查 LITE_MODEL_CODE / llm.lite_model 配置"
            )

        # max_tokens 是 chat_lite 显式形参，无需从 kwargs 取出；
        # kwargs 中若有调用方误传的 model，丢弃（模型由 lite_model 决定）
        kwargs.pop("model", None)

        if lite_provider == self.provider_name:
            # 当前 provider：走完整链路（含 failover）
            for k, v in _lite_thinking_off_params(lite_provider).items():
                kwargs.setdefault(k, v)
            return await self.chat(
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
                temperature=temperature,
                max_tokens=max_tokens,
                model=lite_model,
                **kwargs,
            )

        # 跨 provider：指定即专用，不参与 failover
        max_tokens = self._resolve_max_tokens(max_tokens, model=lite_model)
        for k, v in _lite_thinking_off_params(lite_provider).items():
            kwargs.setdefault(k, v)
        chat_start = time.time()
        key_pool = _build_key_pool(lite_provider)
        try:
            async with key_pool.acquire() as api_key:
                provider = _build_provider(lite_provider, api_key, model=lite_model)
                result = await provider.chat(
                    messages=messages,
                    tools=tools,
                    tool_choice=tool_choice,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **kwargs,
                )
        except Exception as e:
            logger.opt(exception=True).error(
                "[LLM] chat_lite() failed (cross-provider), provider={p}, model={m}, error: {err}",
                p=lite_provider, m=lite_model, err=describe_exception(e),
            )
            raise
        chat_duration = time.time() - chat_start
        try:
            from src.services.llm_usage_meter import record_response_usage
            record_response_usage(result)
        except Exception:
            # Usage accounting is observational and must not break LLM calls.
            pass
        logger.info(
            "[LLM] chat_lite() completed, provider={p}, model={m}, duration={d:.2f}s, "
            "has_content={c}",
            p=lite_provider, m=lite_model, d=chat_duration, c=bool(result.get("content")),
        )
        return result

    async def stream_chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> AsyncGenerator[str, None]:
        """
        流式对话

        # TODO(billing): 启用流式前需在 chunk 累积 loop 末尾收集 usage 并调用 record_background_llm_usage

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
        max_tokens = self._resolve_max_tokens(max_tokens)
        async for chunk in (
            self._failover.stream_chat(
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs,
            )
            if self._failover_enabled
            else self._stream_with_pool(
                "stream_chat",
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs,
            )
        ):
            yield chunk

    async def chat_with_tools(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        tool_choice: str = "auto",
        system_prompt: Optional[str] = None,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        带工具的对话（支持工具调用）

        Args:
            messages: 消息列表
            tools: 工具定义列表
            tool_choice: 工具选择策略
            system_prompt: 系统提示词
            max_tokens: 最大生成token数
            **kwargs: 其他参数

        Returns:
            响应字典，包含:
            - content: 文本内容
            - tool_calls: 工具调用列表（如果有）
        """
        import time
        cwt_start = time.time()
        max_tokens = self._resolve_max_tokens(max_tokens)
        logger.info(f"[LLM] chat_with_tools() called, provider={self.provider_name}, messages_count={len(messages)}, tools_count={len(tools) if tools else 0}, has_system_prompt={system_prompt is not None}")

        try:
            if system_prompt:
                messages = [{"role": "system", "content": system_prompt}] + messages

            if self._failover_enabled:
                result = await self._failover.chat(
                    messages=messages,
                    tools=tools,
                    tool_choice=tool_choice,
                    max_tokens=max_tokens,
                    **kwargs,
                )
            else:
                result = await self.chat(
                    messages=messages,
                    tools=tools,
                    tool_choice=tool_choice,
                    max_tokens=max_tokens,
                    **kwargs,
                )
            
            cwt_duration = time.time() - cwt_start
            logger.info(f"[LLM] chat_with_tools() completed, duration={cwt_duration:.2f}s")
            return result
            
        except Exception as e:
            cwt_duration = time.time() - cwt_start
            # 用 loguru 占位符而非 f-string 嵌入 {e}，避免异常消息含 {"error":...} 触发 KeyError
            logger.opt(exception=True).error(
                "[LLM] chat_with_tools() failed, duration={d:.2f}s, error: {err}",
                d=cwt_duration, err=describe_exception(e),
            )
            raise

    def _resolve_max_tokens(self, max_tokens: Optional[int], model: Optional[str] = None) -> int:
        """未显式指定 max_tokens 时，按指定模型从配置表取上限作为默认值。

        Args:
            max_tokens: 调用方显式指定值
            model: 用于查表的模型名，未指定时用当前生效模型（get_model_name）

        配置表 llm.model_max_tokens 未覆盖的模型回退到 DEFAULT_MAX_TOKENS。
        """
        if max_tokens is not None:
            return max_tokens
        model = model or self.get_model_name()
        limit = settings.llm.model_max_tokens.get(model)
        if limit:
            logger.info(f"[LLM] model={model} 未指定 max_tokens，使用配置上限 {limit}")
            return limit
        return DEFAULT_MAX_TOKENS

    def get_provider_name(self) -> str:
        """获取当前提供者名称"""
        return self.provider_name

    def get_model_name(self) -> str:
        """获取当前模型名称。

        优先返回子智能体配置的 model_code 覆盖，未配置时返回全局默认。
        """
        # 优先使用子智能体配置的 model_code 覆盖
        override = self._model_codes.get(self.provider_name)
        if override:
            return override
        if self.provider_name == "qwen":
            return settings.llm.qwen.model
        elif self.provider_name == "zhipu":
            return settings.llm.zhipu.model
        elif self.provider_name == "deepseek":
            return settings.llm.deepseek.model
        elif self.provider_name == "moonshot":
            return settings.llm.moonshot.model
        return "unknown"

    def key_pool_stats(self) -> List[dict]:
        """返回 Key 池使用统计（用于监控）"""
        if self._failover_enabled:
            return self._failover.health_status()
        return self._key_pool.stats()


# 全局LLM网关实例
llm_gateway = LLMGateway()
