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
from .key_pool import KeyPool
from .providers.base import BaseLLMProvider
from .providers.deepseek import DeepSeekProvider
from .providers.qwen import QwenProvider
from .providers.zhipu import ZhipuProvider


def _build_key_pool(provider_name: str) -> KeyPool:
    """根据 provider 配置构建 KeyPool"""
    if provider_name == "qwen":
        cfg = settings.llm.qwen
    elif provider_name == "zhipu":
        cfg = settings.llm.zhipu
    elif provider_name == "deepseek":
        cfg = settings.llm.deepseek
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
    }

    def __init__(self, provider_name: Optional[str] = None, model_codes: Optional[Dict[str, str]] = None):
        """
        初始化LLM网关

        Args:
            provider_name: 指定提供者名称，默认使用配置中的提供者
            model_codes: 各 provider 的 model_code 覆盖，如 {'deepseek': 'deepseek-v4-pro', 'qwen': 'qwen3.7-plus'}。
                        未列出的 provider 使用全局默认 model。仅当指定 provider_name 时生效。
        """
        self.provider_name = provider_name or settings.llm.provider
        if self.provider_name not in self.PROVIDERS:
            raise ValueError(f"不支持的LLM提供者: {self.provider_name}")

        # 子智能体级别的 model_code 覆盖（仅与 provider_name 一起使用）
        self._model_codes: Dict[str, str] = model_codes or {}

        self._failover_enabled = (
            hasattr(settings.llm, 'failover')
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
            self._key_pool: KeyPool = _build_key_pool(self.provider_name)
            logger.info(
                f"LLM网关初始化完成，提供者: {self.provider_name}，"
                f"Key 池统计: {self._key_pool.stats()}，"
                f"model_codes 覆盖: {self._model_codes or '无'}"
            )

    # ------------------------------------------------------------------
    # 内部：从 Key 池获取 Provider 并执行调用
    # ------------------------------------------------------------------

    async def _call_with_pool(self, fn_name: str, **kwargs) -> Any:
        """
        非流式调用：从 Key 池获取一个 Key，构建 Provider 后执行。
        Key 在 async with 块结束时自动归还。
        """
        import time
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
            logger.error(f"[LLM] _call_with_pool error, provider={self.provider_name}, fn={fn_name}, duration={call_duration:.2f}s, error: {type(e).__name__}: {e}", exc_info=True)
            raise

    async def _stream_with_pool(self, fn_name: str, **kwargs) -> AsyncGenerator[str, None]:
        """
        流式调用：Key 在整个流结束后才归还，保证流的完整性。
        """
        stream_start = time.time()
        chunk_count = 0
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
                    logger.error(f"[LLM] _stream_with_pool error during iteration, provider={self.provider_name}, fn={fn_name}, duration={stream_duration:.2f}s, chunks={chunk_count}, error: {e}", exc_info=True)
                    raise
                    
        except asyncio.TimeoutError as e:
            stream_duration = time.time() - stream_start
            logger.error(f"[LLM] _stream_with_pool timeout (acquire key), provider={self.provider_name}, fn={fn_name}, duration={stream_duration:.2f}s")
            raise
        except Exception as e:
            stream_duration = time.time() - stream_start
            logger.error(f"[LLM] _stream_with_pool error (acquire key), provider={self.provider_name}, fn={fn_name}, duration={stream_duration:.2f}s, error: {e}", exc_info=True)
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
        max_tokens: int = 16384,
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
            logger.info(f"[LLM] chat() completed, duration={chat_duration:.2f}s, has_content={bool(result.get('content'))}, has_tool_calls={bool(result.get('tool_calls'))}")
            return result
            
        except Exception as e:
            chat_duration = time.time() - chat_start
            logger.error(f"[LLM] chat() failed, duration={chat_duration:.2f}s, error: {type(e).__name__}: {e}", exc_info=True)
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
        max_tokens: int = 16384,
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
            logger.error(f"[LLM] chat_with_tools() failed, duration={cwt_duration:.2f}s, error: {type(e).__name__}: {e}", exc_info=True)
            raise

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
        return "unknown"

    def key_pool_stats(self) -> List[dict]:
        """返回 Key 池使用统计（用于监控）"""
        if self._failover_enabled:
            return self._failover.health_status()
        return self._key_pool.stats()


# 全局LLM网关实例
llm_gateway = LLMGateway()
