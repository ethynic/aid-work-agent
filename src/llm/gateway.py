"""
LLM网关

统一管理LLM提供者，提供模型路由和调用接口
"""

from typing import Any, AsyncGenerator, Dict, List, Optional

from loguru import logger

from src.config.settings import settings
from .providers.base import BaseLLMProvider
from .providers.qwen import QwenProvider
from .providers.zhipu import ZhipuProvider


class LLMGateway:
    """
    LLM网关
    
    统一管理多个LLM提供者，提供：
    - 模型路由：根据配置选择合适的提供者
    - 统一接口：屏蔽不同提供者的差异
    - 错误处理：统一的异常处理和重试机制
    """
    
    # 提供者注册表
    PROVIDERS = {
        "qwen": QwenProvider,
        "zhipu": ZhipuProvider,
    }
    
    def __init__(self, provider_name: Optional[str] = None):
        """
        初始化LLM网关
        
        Args:
            provider_name: 指定提供者名称，默认使用配置中的提供者
        """
        self.provider_name = provider_name or settings.llm.provider
        self._provider: Optional[BaseLLMProvider] = None
        self._init_provider()
    
    def _init_provider(self) -> None:
        """初始化LLM提供者"""
        if self.provider_name not in self.PROVIDERS:
            raise ValueError(f"不支持的LLM提供者: {self.provider_name}")
        
        provider_class = self.PROVIDERS[self.provider_name]
        
        # 获取提供者配置
        if self.provider_name == "qwen":
            config = settings.llm.qwen
            self._provider = provider_class(
                api_key=config.api_key,
                model=config.model,
            )
        elif self.provider_name == "zhipu":
            config = settings.llm.zhipu
            self._provider = provider_class(
                api_key=config.api_key,
                model=config.model,
            )
        
        logger.info(f"LLM网关初始化完成，提供者: {self.provider_name}")
    
    @property
    def provider(self) -> BaseLLMProvider:
        """获取当前提供者"""
        if self._provider is None:
            raise RuntimeError("LLM提供者未初始化")
        return self._provider
    
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
            响应字典
        """
        try:
            return await self.provider.chat(
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs
            )
        except Exception as e:
            logger.error(f"LLM调用失败: {e}")
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
        async for chunk in self.provider.stream_chat(
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs
        ):
            yield chunk
    
    async def chat_with_tools(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        tool_choice: str = "auto",
        system_prompt: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        带工具的对话（支持工具调用）
        
        Args:
            messages: 消息列表
            tools: 工具定义列表
            tool_choice: 工具选择策略
            system_prompt: 系统提示词
            **kwargs: 其他参数
        
        Returns:
            响应字典，包含:
            - content: 文本内容
            - tool_calls: 工具调用列表（如果有）
        """
        # If system_prompt provided, prepend to messages
        if system_prompt:
            messages = [{"role": "system", "content": system_prompt}] + messages
        
        return await self.chat(
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            **kwargs
        )
    
    def get_provider_name(self) -> str:
        """
        获取当前提供者名称
        
        Returns:
            提供者名称
        """
        return self.provider_name
    
    def get_model_name(self) -> str:
        """
        获取当前模型名称
        
        Returns:
            模型名称
        """
        if self.provider_name == "qwen":
            return settings.llm.qwen.model
        elif self.provider_name == "zhipu":
            return settings.llm.zhipu.model
        return "unknown"


# 全局LLM网关实例
llm_gateway = LLMGateway()
