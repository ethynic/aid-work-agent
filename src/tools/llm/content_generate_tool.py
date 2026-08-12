"""
内容生成工具

调用大模型生成指定内容，用于：
- 生成客户列表
- 撰写多语言邮件
- 生成市场分析报告等
"""

from typing import Any, Dict, Optional

from loguru import logger
from pydantic import BaseModel, Field

from src.tools.base import BaseTool
from src.llm.gateway import llm_gateway


class ContentGenerateInput(BaseModel):
    """生成内容参数"""
    prompt: str = Field(..., description=(
        "完整的内容生成指令，由调用方组织。"
        "应包含：(1)角色定义——以什么身份/专业视角生成内容 "
        "(2)任务描述——生成什么内容、给谁看、达到什么目的 "
        "(3)输入素材——相关的背景信息、数据、上下文要点，直接嵌入文本中 "
        "(4)输出格式——结构要求（段落、列表、表格、JSON、Markdown等）、长度限制 "
        "(5)质量标准——语气（正式/亲切）、风格、专业度要求。"
        "写得越详细具体，生成质量越高。"
    ))
    language: Optional[str] = Field("zh", description="生成内容的语言，如：zh、en、ru、de、ja、ko等")
    content_type: Optional[str] = Field("", description=(
        "内容类型标签。已知预设可自动获得专业系统提示："
        "customer_list、email、market_report、outline、article、report、polish。"
        "非预设场景留空即可，工具会使用通用模式，完全依赖 prompt 参数来指导生成。"
    ))


class ContentGenerateTool(BaseTool):
    """
    内容生成工具

    通过大模型生成各类内容，智能体组织提示词，此工具负责调用大模型并返回结果
    """

    name = "content_generate"
    description = (
        "通用内容生成工具。可生成任意类型的文本内容：邮件、报告、文章、大纲、客户列表、"
        "方案、摘要、翻译润色等。支持多语言输出。"
        "调用方需在 prompt 中提供完整的生成指令（角色、任务、素材、格式、质量要求），"
        "工具负责调用大模型并返回结果。"
        "content_type 参数提供常用场景的快捷预设（如 email、report），非预设场景留空即可。"
    )
    usage_guide = ""
    display_name = "生成内容"
    category = "llm"
    InputModel = ContentGenerateInput

    def get_display_name(self, tool_args: Optional[Dict[str, Any]] = None) -> str:
        """动态显示名，展示内容类型"""
        base = self.display_name
        if tool_args:
            content_type = tool_args.get("content_type", "")
            if content_type:
                return f"{base}「{content_type}」"
        return base

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
                max_tokens=65536
            )

            # 累加 LLM 用量到当前 SessionRecordService（对话内后台 LLM 调用计费）
            from src.services.session_record import record_background_llm_usage
            record_background_llm_usage(response.get("usage") if isinstance(response, dict) else None)

            # 提取生成的content（DeepSeek 思考模型可能返回 content: null）
            generated_content = ""
            if isinstance(response, dict):
                generated_content = response.get("content") or ""
            elif isinstance(response, str):
                generated_content = response

            if not generated_content:
                logger.warning("内容生成返回空内容，可能是思考模型 token 不足")
                return {
                    "success": False,
                    "error": "生成内容为空，可能是 max_tokens 不足以覆盖思考+输出",
                    "language": language,
                    "content_type": content_type,
                }

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
        base_prompt = (
            "你是一个专业的内容生成助手。请严格遵循用户的指令生成高质量内容。\n"
            "规则：\n"
            "1. 如果用户指令中指定了角色（如「你是外贸专家」），立即采纳该角色并以其专业视角生成内容\n"
            "2. 如果用户指令中指定了输出格式（如JSON、表格、Markdown、邮件格式），严格按照该格式输出\n"
            "3. 如果用户指令中包含了具体素材（数据、背景信息、要点），将这些素材准确融入生成内容，不要遗漏\n"
            "4. 如果用户指令中指定了语言、语气、长度等质量标准，严格遵守\n"
            "5. 如果用户指令中未指定某些维度（如未说格式），根据内容类型自动选择最合适的呈现方式\n"
            "6. 生成的内容应直接可用，不要添加「以下是为您生成的内容」等元描述\n"
            "7. 确保内容完整，不要截断或用省略号代替"
        )

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
