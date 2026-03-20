#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Skill Matcher - 基于 LLM 的 Skill 语义匹配器

核心思路：不再依赖关键词，而是让 LLM 根据用户意图和 skill 描述来匹配合适的 skill
"""

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from src.core.skill_registry import SkillRegistry


@dataclass
class SkillMatch:
    """Skill 匹配结果"""
    name: str
    reason: str
    confidence: float


class SkillMatcher:
    """
    基于 LLM 的 Skill 语义匹配器

    核心思路：不再依赖关键词，而是让 LLM 根据用户意图和 skill 描述来匹配合适的 skill
    """

    def __init__(
        self,
        llm: Any,  # BaseLLM，但避免循环导入
        skill_registry: "SkillRegistry",
    ):
        self.llm = llm
        self.skill_registry = skill_registry

    async def match(
        self,
        user_intent: str,
        context: Optional[Dict[str, Any]] = None,
        allowed_skills: Optional[List[str]] = None,
    ) -> List[SkillMatch]:
        """
        根据用户意图匹配合适的 skills

        Args:
            user_intent: 用户的原始输入或意图描述
            context: 上下文信息（上传文件、会话历史等）
            allowed_skills: 允许匹配的 skills 列表（用于子智能体约束）

        Returns:
            匹配的 skills 列表，按相关度排序
        """
        context = context or {}

        # 1. 获取可用的 skill 描述
        available_skills = self._get_available_skill_descriptions(allowed_skills)

        if not available_skills:
            logger.info("No available skills to match")
            return []

        # 2. 构建匹配提示
        prompt = self._build_matching_prompt(user_intent, context, available_skills)

        # 3. 调用 LLM 匹配
        try:
            response = await self.llm.agenerate([prompt])
            matches = self._parse_matching_response(response)
            logger.info(f"LLM matched {len(matches)} skills for intent: {user_intent[:50]}...")
            return matches
        except Exception as e:
            logger.error(f"LLM matching failed: {e}")
            return []

    def _get_available_skill_descriptions(
        self,
        allowed_skills: Optional[List[str]] = None,
    ) -> List[Dict[str, str]]:
        """
        获取可用的 skill 描述列表

        Args:
            allowed_skills: 允许的 skills 列表

        Returns:
            可用 skill 的描述列表
        """
        descriptions = []
        for name, skill in self.skill_registry:
            # 应用 allow 过滤
            if allowed_skills is not None and name not in allowed_skills:
                continue
            descriptions.append({
                "name": name,
                "description": skill.description or "",
            })
        return descriptions

    def _build_matching_prompt(
        self,
        user_intent: str,
        context: Dict[str, Any],
        available_skills: List[Dict[str, str]],
    ) -> str:
        """
        构建匹配提示

        Args:
            user_intent: 用户意图
            context: 上下文信息
            available_skills: 可用的 skill 描述

        Returns:
            构建好的提示词
        """
        # 提取上下文中的文件信息
        file_info = ""
        if context.get("attachments"):
            files = []
            for att in context["attachments"]:
                name = att.get("name", "unknown")
                mime_type = att.get("mime_type", "unknown")
                files.append(f"- {name} ({mime_type})")
            file_info = "\n用户上传的文件：\n" + "\n".join(files)

        # 构建 skill 列表
        skill_list = "\n".join([
            f"- **{s['name']}**: {s['description']}"
            for s in available_skills
        ])

        prompt = f"""你是一个技能匹配专家。根据用户的意图，从以下可用技能中选择最合适的技能。

用户意图：{user_intent}
{file_info}

可用技能：
{skill_list}

请分析用户意图，选择最合适的技能（可以选0个或多个）。

输出格式（JSON数组）：
[
  {{"name": "技能名", "reason": "匹配原因", "confidence": 0.95}},
  ...
]

规则：
1. 只选择与用户意图明确相关的技能
2. confidence 是 0-1 之间的置信度
3. 如果没有合适的技能，返回空数组 []
4. 匹配原因要简短说明为什么这个技能适合当前任务
5. 只返回 JSON 数组，不要有其他文字
"""
        return prompt

    def _parse_matching_response(self, response: str) -> List[SkillMatch]:
        """
        解析 LLM 返回的匹配结果

        Args:
            response: LLM 返回的原始文本

        Returns:
            解析后的匹配结果列表
        """
        try:
            # 尝试解析 JSON
            matches = json.loads(response)
            if not isinstance(matches, list):
                logger.warning(f"Unexpected response format: {type(matches)}")
                return []

            result = []
            for m in matches:
                if isinstance(m, dict) and "name" in m:
                    result.append(SkillMatch(
                        name=m["name"],
                        reason=m.get("reason", ""),
                        confidence=m.get("confidence", 0.5),
                    ))

            # 按置信度排序
            result.sort(key=lambda x: x.confidence, reverse=True)
            return result

        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse JSON response: {e}")
            return []
        except Exception as e:
            logger.error(f"Error parsing matching response: {e}")
            return []

    def match_sync(
        self,
        user_intent: str,
        context: Optional[Dict[str, Any]] = None,
        allowed_skills: Optional[List[str]] = None,
    ) -> List[SkillMatch]:
        """
        同步版本的匹配（不推荐，但在某些场景下需要）

        Args:
            user_intent: 用户意图
            context: 上下文信息
            allowed_skills: 允许的 skills 列表

        Returns:
            匹配的 skills 列表
        """
        import asyncio
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        return loop.run_until_complete(
            self.match(user_intent, context, allowed_skills)
        )