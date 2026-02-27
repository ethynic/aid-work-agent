"""
提示词模板

提供可复用的提示词模板类
"""

from typing import Any, Dict, List, Optional


class PromptTemplate:
    """
    提示词模板类
    
    用于构建和管理提示词模板
    """
    
    def __init__(
        self,
        template: str,
        input_variables: Optional[List[str]] = None,
    ):
        """
        初始化提示词模板
        
        Args:
            template: 模板字符串，使用{variable}格式占位
            input_variables: 输入变量列表
        """
        self.template = template
        self.input_variables = input_variables or []
    
    def format(self, **kwargs) -> str:
        """
        格式化模板
        
        Args:
            **kwargs: 模板变量值
        
        Returns:
            格式化后的字符串
        """
        return self.template.format(**kwargs)
    
    def format_partial(self, **kwargs) -> str:
        """
        部分格式化模板
        
        Args:
            **kwargs: 部分模板变量值
        
        Returns:
            部分格式化后的字符串
        """
        result = self.template
        for key, value in kwargs.items():
            result = result.replace(f"{{{key}}}", str(value))
        return result
    
    def validate_inputs(self, **kwargs) -> bool:
        """
        验证输入是否完整
        
        Args:
            **kwargs: 输入变量值
        
        Returns:
            是否包含所有必需变量
        """
        for var in self.input_variables:
            if var not in kwargs:
                return False
        return True


class MessageTemplate:
    """
    消息模板类
    
    用于构建LLM消息格式
    """
    
    @staticmethod
    def user_message(content: str) -> Dict[str, str]:
        """
        创建用户消息
        
        Args:
            content: 消息内容
        
        Returns:
            消息字典
        """
        return {"role": "user", "content": content}
    
    @staticmethod
    def assistant_message(content: str) -> Dict[str, str]:
        """
        创建助手消息
        
        Args:
            content: 消息内容
        
        Returns:
            消息字典
        """
        return {"role": "assistant", "content": content}
    
    @staticmethod
    def system_message(content: str) -> Dict[str, str]:
        """
        创建系统消息
        
        Args:
            content: 消息内容
        
        Returns:
            消息字典
        """
        return {"role": "system", "content": content}
    
    @staticmethod
    def tool_result_message(
        tool_call_id: str,
        tool_name: str,
        result: Any,
    ) -> Dict[str, Any]:
        """
        创建工具结果消息
        
        Args:
            tool_call_id: 工具调用ID
            tool_name: 工具名称
            result: 工具执行结果
        
        Returns:
            消息字典
        """
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "content": str(result) if not isinstance(result, str) else result,
        }


class FewShotPromptTemplate(PromptTemplate):
    """
    少样本提示词模板
    
    包含示例的提示词模板
    """
    
    def __init__(
        self,
        template: str,
        examples: List[Dict[str, str]],
        input_variables: Optional[List[str]] = None,
        example_separator: str = "\n\n",
    ):
        """
        初始化少样本提示词模板
        
        Args:
            template: 模板字符串
            examples: 示例列表
            input_variables: 输入变量列表
            example_separator: 示例分隔符
        """
        super().__init__(template, input_variables)
        self.examples = examples
        self.example_separator = example_separator
    
    def format(self, **kwargs) -> str:
        """
        格式化模板（包含示例）
        
        Args:
            **kwargs: 模板变量值
        
        Returns:
            格式化后的字符串
        """
        # 格式化示例
        formatted_examples = []
        for example in self.examples:
            formatted = self.template.format(**example)
            formatted_examples.append(formatted)
        
        # 格式化当前输入
        formatted_input = self.template.format(**kwargs)
        
        # 组合示例和输入
        all_parts = formatted_examples + [formatted_input]
        return self.example_separator.join(all_parts)


# 预定义模板

TOOL_USE_PROMPT = PromptTemplate(
    template="""你是一个工具使用助手。请根据用户需求选择合适的工具并调用。

可用工具：
{tools_description}

用户需求：{user_request}

请选择合适的工具并提供调用参数。""",
    input_variables=["tools_description", "user_request"],
)

CLARIFICATION_PROMPT = PromptTemplate(
    template="""用户的需求不够明确，需要进一步澄清。

原始输入：{original_input}
识别的意图：{intent}
缺失的信息：{missing_info}

请生成一个友好的澄清问题，帮助用户提供缺失的信息。""",
    input_variables=["original_input", "intent", "missing_info"],
)

SUMMARY_PROMPT = PromptTemplate(
    template="""请总结以下对话内容：

{conversation}

请用简洁的语言总结：
1. 用户的主要需求
2. 已完成的任务
3. 重要的结果或结论""",
    input_variables=["conversation"],
)
