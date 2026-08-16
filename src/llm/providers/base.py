"""
LLM提供者基类

定义所有LLM提供者必须实现的接口
"""

import json
from abc import ABC, abstractmethod
from typing import Any, AsyncGenerator, Dict, List, Optional


class BaseLLMProvider(ABC):
    """
    LLM提供者抽象基类
    
    所有LLM提供者（通义千问、智谱GLM等）都需要继承此类并实现抽象方法
    """
    
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: Optional[str] = None,
        **kwargs
    ):
        """
        初始化LLM提供者
        
        Args:
            api_key: API密钥
            model: 模型名称
            base_url: API基础URL（可选）
            **kwargs: 其他配置参数
        """
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.config = kwargs
    
    @abstractmethod
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
            messages: 消息列表，格式为[{"role": "user/assistant", "content": "..."}]
            tools: 工具定义列表（可选）
            tool_choice: 工具选择策略（可选）
            temperature: 温度参数
            max_tokens: 最大生成token数
            **kwargs: 其他参数
        
        Returns:
            响应字典，格式为{
                "content": "回复内容",
                "tool_calls": [...],  # 可选
                "finish_reason": "stop/tool_calls",
                "usage": {"prompt_tokens": 0, "completion_tokens": 0}
            }
        """
        pass
    
    @abstractmethod
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
            tools: 工具定义列表（可选）
            tool_choice: 工具选择策略（可选）
            temperature: 温度参数
            max_tokens: 最大生成token数
            **kwargs: 其他参数
        
        Yields:
            流式输出的文本片段
        """
        pass
    
    def _is_qwen_model(self) -> bool:
        """判断当前模型是否为 qwen 系文本模型（model 名 qwen 前缀，排除视觉模型），
        用于显式缓存/思考开关判定。

        排除 qwen-vl / qwen3-vl 视觉模型：
        - 视觉模型无思考模式，写入 enable_thinking 可能被 API 拒绝（400）
        - qwen-vl-max / qwen-vl-plus 不在显式缓存（cache_control）官方支持列表，
          对其加缓存标记同样有 400 风险（视频提示词路径默认用 qwen-vl-plus）

        百炼 provider 同时承载 deepseek/kimi/glm 等第三方模型，其缓存参数语义与
        qwen 系不同，必须按 model 名前缀判定，不能按 provider 判定。
        """
        name = str(self.model or "").lower()
        return name.startswith("qwen") and "vl" not in name

    def _format_messages(
        self,
        messages: List[Dict[str, Any]],
        use_cache: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        格式化消息（子类可覆盖）

        Args:
            messages: 原始消息列表
            use_cache: 是否启用显式缓存。仅 qwen provider 传入 True（且 model 为 qwen 系时生效），
                把第一条 system 消息 content 转数组 + cache_control: ephemeral。
                其他 provider 保持默认 False，行为与改动前完全一致。

        Returns:
            格式化后的消息列表
        """
        formatted = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            # 处理不同类型的消息
            if role == "tool":
                # 工具结果消息 - 必须包含tool_call_id
                formatted.append({
                    "role": "tool",
                    "tool_call_id": msg.get("tool_call_id", ""),
                    "content": json.dumps(content, ensure_ascii=False) if isinstance(content, dict) else (content if isinstance(content, str) else str(content)),
                })
            elif role == "assistant" and "tool_calls" in msg:
                # 包含工具调用的assistant消息
                assistant_msg = {
                    "role": "assistant",
                    "content": str(content) if not isinstance(content, str) else content,
                    "tool_calls": msg.get("tool_calls", [])
                }
                formatted.append(assistant_msg)
            elif isinstance(content, list):
                # 处理多模态内容
                formatted.append({"role": role, "content": content})
            elif use_cache and self._is_qwen_model() and role == "system" and not formatted:
                # 显式缓存：仅 qwen 系模型第一条 system 消息加 cache_control（命中按 10% 计费）
                formatted.append({"role": "system", "content": [
                    {"type": "text", "text": str(content), "cache_control": {"type": "ephemeral"}}
                ]})
            else:
                formatted.append({"role": role, "content": str(content)})

        return formatted
    
    def _format_tools(
        self,
        tools: Optional[List[Dict[str, Any]]]
    ) -> Optional[List[Dict[str, Any]]]:
        """
        格式化工具定义（子类可覆盖）

        支持两种输入格式：
        1. 内部格式: {"name": "...", "description": "...", "input_schema": {...}}
        2. OpenAI 格式: {"type": "function", "function": {"name": "...", ...}}

        Args:
            tools: 原始工具定义列表

        Returns:
            统一为 OpenAI function calling 格式
        """
        if not tools:
            return None

        formatted_tools = []
        for tool in tools:
            # 已经是 OpenAI 格式（有 type="function" 和 function 字段），直接使用
            if tool.get("type") == "function" and "function" in tool:
                formatted_tools.append(tool)
            else:
                # 内部格式，转换为 OpenAI 格式
                formatted_tools.append({
                    "type": "function",
                    "function": {
                        "name": tool.get("name", ""),
                        "description": tool.get("description", ""),
                        "parameters": tool.get("input_schema", tool.get("parameters", {})),
                    }
                })

        return formatted_tools
    
    def _parse_tool_calls(
        self,
        response: Dict[str, Any]
    ) -> Optional[List[Dict[str, Any]]]:
        """
        解析工具调用（子类可覆盖）
        
        Args:
            response: 原始响应
        
        Returns:
            标准化的工具调用列表
        """
        return response.get("tool_calls")
    
    @property
    def provider_name(self) -> str:
        """
        获取提供者名称
        
        Returns:
            提供者名称
        """
        return self.__class__.__name__.replace("Provider", "").lower()
