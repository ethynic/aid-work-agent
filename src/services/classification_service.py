"""
通用文本分类服务，基于 LLM + 预定义分类体系。

复用场景：
- 投诉智能体的投诉类型分类
- 工单系统的工单分类
- 知识库文档自动分类
- 客户意图识别
"""

import json
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

from loguru import logger


@dataclass
class ClassificationResult:
    """分类结果"""
    category: str
    sub_category: str = ""
    confidence: float = 0.0
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


class ClassificationService:
    """
    通用文本分类服务，基于 LLM + 预定义分类体系。
    """

    async def classify(
        self,
        text: str,
        categories: List[str],
        context: str = "",
        sub_categories: Dict[str, List[str]] = None,
    ) -> ClassificationResult:
        """
        对文本进行分类。

        Args:
            text: 待分类文本
            categories: 预定义分类列表
            context: 可选的上下文信息
            sub_categories: 可选的二级分类映射
        """
        try:
            from src.llm.gateway import llm_gateway

            prompt = self._build_prompt(text, categories, context, sub_categories)

            messages = [
                {"role": "system", "content": "你是一个专业的文本分类引擎。严格按照提供的分类体系进行分类，返回JSON结果。"},
                {"role": "user", "content": prompt},
            ]

            response = await llm_gateway.chat_lite(
                messages=messages,
                temperature=0.1,
                max_tokens=500,
            )

            # 累加 LLM 用量到当前 SessionRecordService（对话内后台 LLM 调用计费）
            from src.config.settings import settings
            from src.services.session_record import record_background_llm_usage
            record_background_llm_usage(
                response.get("usage") if isinstance(response, dict) else None,
                model=settings.llm.get_lite_model(),  # chat_lite 实际消耗 lite 模型，按 lite 单价计费
            )

            content = response.get("content", "")
            if not content:
                return ClassificationResult(category=categories[0] if categories else "其他", confidence=0.0)

            result = self._parse_json(content)
            if result is None:
                return ClassificationResult(category=categories[0] if categories else "其他", confidence=0.0)

            category = result.get("category", "")
            if category not in categories:
                category = categories[0] if categories else "其他"

            return ClassificationResult(
                category=category,
                sub_category=result.get("sub_category", ""),
                confidence=float(result.get("confidence", 0.5)),
                tags=result.get("tags", []),
            )
        except Exception as e:
            logger.opt(exception=True).error(f"文本分类失败: {e}")
            return ClassificationResult(category="其他", confidence=0.0)

    async def classify_with_confidence(
        self,
        text: str,
        categories: List[str],
        context: str = "",
        threshold: float = 0.6,
    ) -> ClassificationResult:
        """分类并返回置信度，低于阈值时标记为 uncertain"""
        result = await self.classify(text, categories, context)
        if result.confidence < threshold:
            result.metadata["uncertain"] = True
        return result

    def _build_prompt(
        self,
        text: str,
        categories: List[str],
        context: str,
        sub_categories: Optional[Dict[str, List[str]]],
    ) -> str:
        prompt = f"请对以下文本进行分类。\n\n"
        prompt += f"预定义分类列表：{json.dumps(categories, ensure_ascii=False)}\n"

        if sub_categories:
            prompt += f"\n二级分类映射：\n"
            for cat, subs in sub_categories.items():
                prompt += f"  {cat}: {json.dumps(subs, ensure_ascii=False)}\n"

        if context:
            prompt += f"\n上下文信息：{context}\n"

        prompt += f"\n待分类文本：{text}\n"
        prompt += f"\n请返回JSON：{{\"category\": \"一级分类\", \"sub_category\": \"二级分类\", \"confidence\": 0.0, \"tags\": [\"标签1\"]}}\n"
        prompt += f"只返回JSON，不要包含其他内容。"
        return prompt

    def _parse_json(self, text: str) -> Optional[Dict[str, Any]]:
        """从LLM响应中提取JSON"""
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1:
                try:
                    return json.loads(text[start:end + 1])
                except json.JSONDecodeError:
                    return None
            return None
