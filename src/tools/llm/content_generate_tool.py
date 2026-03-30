"""
内容生成工具

调用大模型生成指定内容，用于：
- 生成客户列表
- 撰写多语言邮件
- 生成市场分析报告等
"""

from typing import Any, Dict

from loguru import logger

from src.tools.base import BaseTool
from src.llm.gateway import llm_gateway


class ContentGenerateTool(BaseTool):
    """
    内容生成工具

    通过大模型生成各类内容，智能体组织提示词，此工具负责调用大模型并返回结果
    """

    name = "content_generate"
    description = "调用大模型生成内容，用于生成客户列表、撰写多语言邮件等。智能体需要提供详细的提示词来指导大模型生成所需内容。"
    category = "llm"
    parameters_schema = {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "生成内容的提示词，由智能体组织。提示词应包含：1)角色/身份 2)任务描述 3)输入信息 4)输出格式要求 5)语言要求等"
            },
            "language": {
                "type": "string",
                "description": "生成内容的语言，如：zh（中文）、en（英语）、ru（俄语）、de（德语）、ja（日语）、ko（韩语）等"
            },
            "content_type": {
                "type": "string",
                "description": "内容类型，用于选择合适的提示词模板。可选值：customer_list（客户列表）、email（邮件）、market_report（市场报告）、outline（大纲）、article（文章）、report（报告）、polish（润色）等。也可以使用自定义类型名称。"
            }
        },
        "required": ["prompt"]
    }

    def __init__(self):
        """初始化内容生成工具"""
        self.llm = llm_gateway

    async def execute(self, **kwargs) -> Dict[str, Any]:
        """
        执行内容生成

        Args:
            prompt: 生成内容的提示词
            language: 生成内容的语言
            content_type: 内容类型

        Returns:
            执行结果，包含生成的内容
        """
        prompt = kwargs.get("prompt", "")
        language = kwargs.get("language", "zh")
        content_type = kwargs.get("content_type", "")

        if not prompt:
            return {
                "success": False,
                "error": "提示词不能为空"
            }

        try:
            logger.info(f"内容生成请求: 类型={content_type}, 语言={language}")

            # 构建系统提示词
            system_prompt = self._get_system_prompt(language, content_type)

            # 调用大模型
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ]

            response = await self.llm.chat(
                messages=messages,
                temperature=0.7,
                max_tokens=4096
            )

            # 提取生成的content
            generated_content = ""
            if isinstance(response, dict):
                generated_content = response.get("content", "")
            elif isinstance(response, str):
                generated_content = response

            logger.info(f"内容生成成功: 长度={len(generated_content)}")

            return {
                "success": True,
                "content": generated_content,
                "language": language,
                "content_type": content_type,
                "message": "内容生成成功"
            }

        except Exception as e:
            logger.error(f"内容生成失败: {e}")
            return {
                "success": False,
                "error": f"内容生成失败: {str(e)}"
            }

    def _get_system_prompt(self, language: str, content_type: str) -> str:
        """
        根据语言和内容类型获取系统提示词

        Args:
            language: 语言代码
            content_type: 内容类型

        Returns:
            系统提示词
        """
        base_prompt = "你是一个专业的内容生成助手。请根据用户提供的提示词生成高质量的内容。"

        # 根据内容类型添加特定指导
        type_guidance = {
            "customer_list": "你是一个专业的外贸客户开发专家。请生成符合外贸业务需求的客户信息列表，包括公司名称、联系人、邮箱、国家、行业等信息。",
            "email": "你是一个专业的外贸邮件营销专家。请撰写专业的商务邮件，内容要真实、自然，避免明显的模板感。",
            "market_report": "你是一个专业的市场分析师。请生成专业的市场分析报告。",
            "outline": "你是一个专业的内容策划师。请根据主题生成结构清晰的文章大纲，包含主要章节和子要点。",
            "article": "你是一个资深的内容创作者。请根据大纲或主题撰写高质量的章节内容，语言专业但不晦涩。",
            "report": "你是一个专业的报告撰写专家。请撰写结构清晰、数据详实的专业报告。",
            "polish": "你是一个专业的内容编辑。请对文章进行润色优化，确保逻辑连贯、语言流畅、表达准确。",
        }

        if content_type in type_guidance:
            base_prompt = type_guidance[content_type]

        # 根据语言添加指导
        language_guidance = {
            "zh": "请使用简体中文回复。",
            "en": "Please respond in English.",
            "ru": "Пожалуйста, отвечайте на русском языке.",
            "de": "Bitte antworten Sie auf Deutsch.",
            "ja": "日本語でお答えください。",
            "ko": "한국어로 답변해 주세요.",
            "es": "Por favor responda en español."
        }

        if language in language_guidance:
            base_prompt += f" {language_guidance[language]}"

        return base_prompt


def create_content_generate_tool() -> ContentGenerateTool:
    """
    创建内容生成工具实例

    Returns:
        ContentGenerateTool实例
    """
    return ContentGenerateTool()
