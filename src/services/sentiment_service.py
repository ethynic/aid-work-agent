"""
通用情绪分析服务，基于 LLM 进行文本情绪分析。

复用场景：
- 投诉智能体的客户情绪识别与安抚策略选择
- 任何客服类智能体的用户情绪感知
- 会话质量评估
"""

import json
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

from loguru import logger


@dataclass
class SentimentResult:
    """情绪分析结果"""
    sentiment: str           # positive / neutral / negative / angry
    intensity: float         # 0.0 ~ 1.0，情绪强度
    urgency: str             # low / medium / high / critical
    key_emotions: List[str] = field(default_factory=list)
    confidence: float = 0.0
    suggested_response_tone: str = ""


class SentimentService:
    """
    通用情绪分析服务，基于 LLM 进行文本情绪分析。
    """

    SYSTEM_PROMPT = """你是一个专业的客户情绪分析引擎。分析用户文本的情绪状态，返回JSON结果。

分析维度：
1. sentiment: 情绪类型 — positive(积极), neutral(中性), negative(消极/不满), angry(愤怒/激动)
2. intensity: 情绪强度 — 0.0(平静) 到 1.0(极度激烈)
3. urgency: 紧急程度 — low(低), medium(中), high(高), critical(危急)
4. key_emotions: 识别到的关键情绪标签列表（如 ["愤怒", "失望", "焦虑"]）
5. confidence: 分析置信度 — 0.0 到 1.0
6. suggested_response_tone: 建议的回复语气策略（一句话）

请直接返回JSON，不要包含其他内容。格式如下：
{"sentiment": "xxx", "intensity": 0.0, "urgency": "xxx", "key_emotions": [...], "confidence": 0.0, "suggested_response_tone": "xxx"}"""

    async def analyze(self, text: str, context: str = "") -> SentimentResult:
        """分析单条文本的情绪"""
        try:
            from src.llm.gateway import llm_gateway

            user_msg = f"分析以下文本的情绪：\n\n{text}"
            if context:
                user_msg += f"\n\n上下文信息：{context}"

            messages = [
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ]

            response = await llm_gateway.chat(
                messages=messages,
                temperature=0.1,
                max_tokens=500,
            )

            content = response.get("content", "")
            if not content:
                return self._default_result()

            result = self._parse_json(content)
            if result is None:
                return self._default_result()

            return SentimentResult(
                sentiment=result.get("sentiment", "neutral"),
                intensity=float(result.get("intensity", 0.3)),
                urgency=result.get("urgency", "low"),
                key_emotions=result.get("key_emotions", []),
                confidence=float(result.get("confidence", 0.5)),
                suggested_response_tone=result.get("suggested_response_tone", ""),
            )
        except Exception as e:
            logger.error(f"情绪分析失败: {e}", exc_info=True)
            return self._default_result()

    async def analyze_batch(self, texts: List[str]) -> List[SentimentResult]:
        """批量分析"""
        return [await self.analyze(text) for text in texts]

    async def get_emotion_trend(self, messages: List[Dict]) -> List[Dict]:
        """分析对话中的情绪变化趋势"""
        trend = []
        for i, msg in enumerate(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, list):
                    content = " ".join(
                        c.get("text", "") for c in content if isinstance(c, dict)
                    )
                result = await self.analyze(content)
                trend.append({
                    "turn": i,
                    "sentiment": result.sentiment,
                    "intensity": result.intensity,
                })
        return trend

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

    def _default_result(self) -> SentimentResult:
        return SentimentResult(
            sentiment="neutral",
            intensity=0.3,
            urgency="low",
            key_emotions=[],
            confidence=0.0,
        )
