"""
意图理解引擎（⚠️ 未投入使用 — 仅被 planner.py 引用，planner.py 本身也是死代码）

解析用户输入，识别意图和提取实体
"""

import json
import re
from typing import Any, Dict, List, Optional

from loguru import logger

from src.llm.gateway import llm_gateway

# INTENT_PROMPT 已随 src/llm/prompts/system.py 一同删除
# 此文件为死代码，如需恢复请从 git 历史中找回 INTENT_PROMPT
INTENT_PROMPT = ""


class IntentResult:
    """
    意图识别结果
    
    封装意图识别的结果信息
    """
    
    def __init__(
        self,
        intent: str,
        confidence: float = 1.0,
        entities: Optional[Dict[str, Any]] = None,
        need_clarification: bool = False,
        clarification_question: str = "",
    ):
        """
        初始化意图结果
        
        Args:
            intent: 意图名称
            confidence: 置信度
            entities: 实体字典
            need_clarification: 是否需要澄清
            clarification_question: 澄清问题
        """
        self.intent = intent
        self.confidence = confidence
        self.entities = entities or {}
        self.need_clarification = need_clarification
        self.clarification_question = clarification_question
    
    def to_dict(self) -> Dict[str, Any]:
        """
        转换为字典
        
        Returns:
            字典表示
        """
        return {
            "intent": self.intent,
            "confidence": self.confidence,
            "entities": self.entities,
            "need_clarification": self.need_clarification,
            "clarification_question": self.clarification_question,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "IntentResult":
        """
        从字典创建
        
        Args:
            data: 字典数据
        
        Returns:
            IntentResult实例
        """
        return cls(
            intent=data.get("intent", "unknown"),
            confidence=data.get("confidence", 1.0),
            entities=data.get("entities", {}),
            need_clarification=data.get("need_clarification", False),
            clarification_question=data.get("clarification_question", ""),
        )


class IntentEngine:
    """
    意图理解引擎
    
    负责：
    - 解析用户自然语言输入
    - 识别用户意图
    - 提取关键实体和参数
    """
    
    # 支持的意图列表
    INTENTS = {
        # 邮件处理
        "email_send": {
            "description": "发送邮件",
            "required_entities": ["收件人", "主题"],
            "optional_entities": ["正文", "附件"],
        },
        "email_read": {
            "description": "读取邮件列表",
            "required_entities": [],
            "optional_entities": ["筛选条件", "数量"],
        },
        "email_search": {
            "description": "搜索邮件",
            "required_entities": ["关键词"],
            "optional_entities": ["发件人", "时间范围"],
        },
        # OCR识别
        "ocr_image": {
            "description": "图片文字识别",
            "required_entities": ["图片"],
            "optional_entities": [],
        },
        "ocr_pdf": {
            "description": "PDF文档识别",
            "required_entities": ["PDF文件"],
            "optional_entities": [],
        },
        # 信息检索
        "web_search": {
            "description": "网络搜索",
            "required_entities": ["关键词"],
            "optional_entities": ["数量"],
        },
        "kb_search": {
            "description": "知识库检索",
            "required_entities": ["关键词"],
            "optional_entities": ["分类"],
        },
        # 系统交互
        "help": {
            "description": "获取帮助",
            "required_entities": [],
            "optional_entities": ["主题"],
        },
        "settings": {
            "description": "系统设置",
            "required_entities": [],
            "optional_entities": ["设置项", "值"],
        },
        # 通用
        "greeting": {
            "description": "问候语",
            "required_entities": [],
            "optional_entities": [],
        },
        "unknown": {
            "description": "未知意图",
            "required_entities": [],
            "optional_entities": [],
        },
    }
    
    def __init__(self, confidence_threshold: float = 0.7):
        """
        初始化意图引擎
        
        Args:
            confidence_threshold: 置信度阈值
        """
        self.confidence_threshold = confidence_threshold
    
    async def recognize(
        self,
        user_input: str,
        context: Optional[List[Dict[str, str]]] = None,
    ) -> IntentResult:
        """
        识别用户意图
        
        Args:
            user_input: 用户输入
            context: 对话上下文
        
        Returns:
            意图识别结果
        """
        # 先尝试规则匹配
        result = self._rule_based_recognition(user_input)
        
        # 如果规则匹配置信度高，直接返回
        if result and result.confidence >= self.confidence_threshold:
            return result
        
        # 使用LLM进行意图识别
        llm_result = await self._llm_recognition(user_input, context)
        
        # 如果LLM结果更好，使用LLM结果
        if llm_result and (not result or llm_result.confidence > result.confidence):
            return llm_result
        
        return result or IntentResult(intent="unknown", confidence=0.0)
    
    def _rule_based_recognition(self, user_input: str) -> Optional[IntentResult]:
        """
        基于规则的意图识别
        
        Args:
            user_input: 用户输入
        
        Returns:
            意图结果或None
        """
        user_input = user_input.strip().lower()
        
        # 问候语
        greeting_patterns = [
            r"^(你好|您好|hi|hello|早上好|下午好|晚上好)",
            r"^(在吗|在不在)",
        ]
        for pattern in greeting_patterns:
            if re.match(pattern, user_input):
                return IntentResult(intent="greeting", confidence=0.95)
        
        # 帮助
        help_patterns = [
            r"(你能做什么|你有什么功能|帮助|help|怎么用)",
            r"(使用说明|功能列表)",
        ]
        for pattern in help_patterns:
            if re.search(pattern, user_input):
                return IntentResult(intent="help", confidence=0.9)
        
        # 发送邮件
        if re.search(r"(发.*邮件|发送邮件|写信|给.*发邮件)", user_input):
            entities = self._extract_email_entities(user_input)
            return IntentResult(
                intent="email_send",
                confidence=0.85,
                entities=entities,
            )
        
        # 读取邮件
        if re.search(r"(读.*邮件|查看邮件|收件箱|邮件列表)", user_input):
            return IntentResult(intent="email_read", confidence=0.85)
        
        # 搜索邮件
        if re.search(r"(搜索邮件|查找邮件|找.*邮件)", user_input):
            keyword = self._extract_keyword(user_input, ["搜索", "查找", "找"])
            return IntentResult(
                intent="email_search",
                confidence=0.85,
                entities={"关键词": keyword} if keyword else {},
            )

        # OCR识别
        if re.search(r"(识别|ocr|文字识别|图片.*文字)", user_input):
            if re.search(r"pdf", user_input):
                return IntentResult(intent="ocr_pdf", confidence=0.85)
            return IntentResult(intent="ocr_image", confidence=0.85)
        
        # 网络搜索
        if re.search(r"(搜索|查找|查一下|百度|google)", user_input):
            keyword = self._extract_keyword(user_input, ["搜索", "查找", "查一下", "百度", "google"])
            return IntentResult(
                intent="web_search",
                confidence=0.85,
                entities={"关键词": keyword} if keyword else {},
            )
        
        return None
    
    async def _llm_recognition(
        self,
        user_input: str,
        context: Optional[List[Dict[str, str]]] = None,
    ) -> Optional[IntentResult]:
        """
        基于LLM的意图识别
        
        Args:
            user_input: 用户输入
            context: 对话上下文
        
        Returns:
            意图结果或None
        """
        try:
            # 构建意图列表描述
            intent_list = "\n".join([
                f"- {name}: {info['description']}"
                for name, info in self.INTENTS.items()
            ])
            
            # 格式化提示词
            prompt = INTENT_PROMPT.format(user_input=user_input)
            prompt = prompt.replace("{intent_list}", intent_list)
            
            # 调用LLM
            messages = [{"role": "user", "content": prompt}]
            response = await llm_gateway.chat(
                messages=messages,
                temperature=0.3,
                max_tokens=500,
            )
            
            # 解析结果
            content = response.get("content", "")
            result = self._parse_llm_response(content)
            
            return result
        
        except Exception as e:
            logger.error(f"LLM意图识别失败: {e}")
            return None
    
    def _parse_llm_response(self, content: str) -> Optional[IntentResult]:
        """
        解析LLM响应
        
        Args:
            content: LLM响应内容
        
        Returns:
            意图结果或None
        """
        try:
            # 尝试提取JSON
            json_match = re.search(r'\{[^{}]*\}', content, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                return IntentResult.from_dict(data)
        except json.JSONDecodeError:
            pass
        
        return None
    
    def _extract_email_entities(self, text: str) -> Dict[str, str]:
        """
        提取邮件相关实体
        
        Args:
            text: 输入文本
        
        Returns:
            实体字典
        """
        entities = {}
        
        # 提取收件人
        recipient_match = re.search(r"给(.+?)(发邮件|写信|发送)", text)
        if recipient_match:
            entities["收件人"] = recipient_match.group(1).strip()
        
        # 提取主题
        subject_match = re.search(r"主题[是为：:]\s*(.+?)(?=\s|$)", text)
        if subject_match:
            entities["主题"] = subject_match.group(1).strip()
        
        return entities
    
    def _extract_keyword(self, text: str, triggers: List[str]) -> Optional[str]:
        """
        提取关键词
        
        Args:
            text: 输入文本
            triggers: 触发词列表
        
        Returns:
            关键词或None
        """
        for trigger in triggers:
            if trigger in text:
                parts = text.split(trigger, 1)
                if len(parts) > 1:
                    keyword = parts[1].strip()
                    # 清理常见停用词
                    keyword = re.sub(r"^(一下|关于|有关)\s*", "", keyword)
                    if keyword:
                        return keyword
        return None

    def get_intent_info(self, intent: str) -> Dict[str, Any]:
        """
        获取意图信息
        
        Args:
            intent: 意图名称
        
        Returns:
            意图信息字典
        """
        return self.INTENTS.get(intent, self.INTENTS["unknown"])
    
    def get_supported_intents(self) -> List[str]:
        """
        获取支持的意图列表
        
        Returns:
            意图名称列表
        """
        return list(self.INTENTS.keys())


# 全局意图引擎实例
intent_engine = IntentEngine()
