#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UseSkillTool - 加载技能内容

加载技能的操作指南并返回给 LLM。
"""

from typing import Dict, Any

from loguru import logger

from src.tools.base import BaseTool


class UseSkillTool(BaseTool):
    """加载技能工具"""

    name = "use_skill"
    description = "加载技能的操作指南（SKILL.md 正文）。加载后根据指南决定下一步：脚本执行类调用 skill_execute，引导式技能调用 content_generate 等工具。流程：use_skill → 按指南执行 → skill_complete 标记完成。"
    usage_guide = ""
    display_name = "加载技能"
    category = "skill"

    def __init__(self, skill_registry):
        """
        Args:
            skill_registry: SkillRegistry 实例
        """
        self.skill_registry = skill_registry

    async def execute(self, **kwargs) -> Dict[str, Any]:
        """
        加载技能内容

        Args:
            skill: 技能名称
            _substitutions: 内部参数，AgentSkills 标准字符串替换上下文

        Returns:
            技能内容字典
        """
        skill_name = kwargs.get("skill", "")
        substitutions = kwargs.get("_substitutions")  # 内部参数，不来自 LLM

        if not skill_name:
            return {
                "success": False,
                "error": "No skill name provided",
                "available_skills": self.skill_registry.list_skills() if self.skill_registry else []
            }

        if not self.skill_registry:
            return {
                "success": False,
                "error": "Skill registry not initialized"
            }

        # 检查 skill 是否在允许列表中
        if hasattr(self.skill_registry, 'is_allowed') and not self.skill_registry.is_allowed(skill_name):
            allowed = self.skill_registry.get_allowed_list()
            return {
                "success": False,
                "error": f"Skill '{skill_name}' not allowed. Available: {allowed or 'all'}",
                "available_skills": allowed
            }

        skill = self.skill_registry.get(skill_name)
        skill_content = self.skill_registry.get_content(skill_name, substitutions=substitutions)

        if skill_content is None:
            available = self.skill_registry.list_skills()
            return {
                "success": False,
                "error": f"Skill '{skill_name}' not found",
                "available_skills": available
            }

        logger.info(f"后端日志：UseSkillTool 加载技能", extra={
            "skill_name": skill_name,
            "skill_content_length": len(skill_content) if skill_content else 0
        })

        # 增强引导：在技能内容后附加执行指引
        guidance_suffix = f"""

---
**⚠️ 以上是技能「{skill_name}」的完整操作指南。请严格按照指南中的步骤执行：**
- 如果指南中有命令/脚本要执行 → 调用 `skill_execute`
- 如果指南中要求生成内容 → 调用 `content_generate`
- 如果指南中要求搜索信息 → 调用 `web_search`
- 如果指南中有多个步骤 → 逐步执行，不要跳过
- 所有步骤完成后，调用 `skill_complete(skill="{skill_name}", summary="结果摘要")` 标记完成
- 不要直接回复用户"正在执行"，而是立即开始执行第一步"""

        enhanced_content = skill_content + guidance_suffix

        return {
            "success": True,
            "skill_name": skill_name,
            "content": enhanced_content,
            "message": f"✅ Skill '{skill_name}' loaded."
        }
