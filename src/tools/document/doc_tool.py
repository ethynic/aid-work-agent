"""
文档工具

实现文档摘要和翻译功能
"""

from typing import Any, Dict

from loguru import logger

from src.tools.base import BaseTool
from src.llm.gateway import llm_gateway


class DocSummarizeTool(BaseTool):
    """文档摘要工具"""
    
    name = "doc_summarize"
    description = "对文档内容进行摘要总结"
    category = "document"
    parameters_schema = {
        "type": "object",
        "properties": {
            "document": {
                "type": "string",
                "description": "文档内容",
            },
            "length": {
                "type": "string",
                "description": "摘要长度：short（简短）、medium（中等）、long（详细）",
            },
        },
        "required": ["document"],
    }
    
    async def execute(self, **kwargs) -> Dict[str, Any]:
        """
        执行文档摘要
        
        Args:
            document: 文档内容
            length: 摘要长度
        
        Returns:
            摘要结果
        """
        document = kwargs.get("document", "")
        length = kwargs.get("length", "medium")
        
        if not document:
            return {
                "success": False,
                "error": "请提供文档内容",
            }
        
        # 根据长度设置提示词
        length_prompts = {
            "short": "请用1-2句话简要总结以下内容：",
            "medium": "请用3-5句话总结以下内容，突出重点：",
            "long": "请详细总结以下内容，包括主要观点和关键细节：",
        }
        
        prompt = length_prompts.get(length, length_prompts["medium"])
        
        try:
            # 使用LLM生成摘要
            messages = [
                {"role": "system", "content": "你是一个专业的文档摘要助手，擅长提取关键信息并生成简洁的摘要。"},
                {"role": "user", "content": f"{prompt}\n\n{document}"},
            ]
            
            response = await llm_gateway.chat(
                messages=messages,
                temperature=0.3,
                max_tokens=500,
            )
            
            summary = response.get("content", "")
            
            logger.info(f"文档摘要成功，原文长度: {len(document)}, 摘要长度: {len(summary)}")
            
            return {
                "success": True,
                "summary": summary,
                "original_length": len(document),
                "summary_length": len(summary),
                "message": "摘要生成成功",
            }
        
        except Exception as e:
            logger.error(f"文档摘要失败: {e}")
            return {
                "success": False,
                "error": f"摘要生成失败: {str(e)}",
            }


class DocTranslateTool(BaseTool):
    """文档翻译工具"""
    
    name = "doc_translate"
    description = "将文档内容翻译成指定语言"
    category = "document"
    parameters_schema = {
        "type": "object",
        "properties": {
            "document": {
                "type": "string",
                "description": "文档内容",
            },
            "target_language": {
                "type": "string",
                "description": "目标语言，如：英语、日语、法语等",
            },
            "source_language": {
                "type": "string",
                "description": "源语言（可选，默认自动检测）",
            },
        },
        "required": ["document", "target_language"],
    }
    
    async def execute(self, **kwargs) -> Dict[str, Any]:
        """
        执行文档翻译
        
        Args:
            document: 文档内容
            target_language: 目标语言
            source_language: 源语言
        
        Returns:
            翻译结果
        """
        document = kwargs.get("document", "")
        target_language = kwargs.get("target_language", "英语")
        source_language = kwargs.get("source_language", "")
        
        if not document:
            return {
                "success": False,
                "error": "请提供文档内容",
            }
        
        try:
            # 构建翻译提示词
            if source_language:
                prompt = f"请将以下{source_language}内容翻译成{target_language}，保持原文的格式和语气：\n\n{document}"
            else:
                prompt = f"请将以下内容翻译成{target_language}，保持原文的格式和语气：\n\n{document}"
            
            messages = [
                {"role": "system", "content": "你是一个专业的翻译助手，擅长准确、流畅地翻译各种语言。"},
                {"role": "user", "content": prompt},
            ]
            
            response = await llm_gateway.chat(
                messages=messages,
                temperature=0.3,
                max_tokens=2000,
            )
            
            translation = response.get("content", "")
            
            logger.info(f"文档翻译成功，原文长度: {len(document)}, 译文长度: {len(translation)}")
            
            return {
                "success": True,
                "translation": translation,
                "source_language": source_language or "自动检测",
                "target_language": target_language,
                "original_length": len(document),
                "translation_length": len(translation),
                "message": f"翻译成功（{target_language}）",
            }
        
        except Exception as e:
            logger.error(f"文档翻译失败: {e}")
            return {
                "success": False,
                "error": f"翻译失败: {str(e)}",
            }
