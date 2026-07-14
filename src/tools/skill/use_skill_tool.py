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
    description = "加载技能的操作指南（SKILL.md 正文）。加载后根据指南决定下一步：脚本执行类调用 skill_execute，引导式技能调用 content_generate 等工具。流程：use_skill → 按指南执行 → 直接给最终回复。"
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

        skill_version = skill.version if skill else "unknown"

        logger.info(f"后端日志：UseSkillTool 加载技能", extra={
            "skill_name": skill_name,
            "skill_version": skill_version,
            "skill_content_length": len(skill_content) if skill_content else 0
        })

        # 增强引导：在技能内容后附加执行指引
        guidance_suffix = f"""

---
**⚠️ 以上是技能「{skill_name}」的完整操作指南。请严格按照指南中的步骤执行。**

**通用文件工具**：`read` / `write` / `edit` / `cp` 是系统提供的通用文件工具，**不限于 skill 场景**——任何需要读、写、改、复制文件的任务都用它们。下面是技能指南中常见指令到工具的映射，方便你快速选择：

| 指南中的指令 | 调用工具 |
|---|---|
| `cp 源文件 目标`、"复制模板"、"基于模板创建副本" | `cp`（source_file_path 支持 `<SKILL_ROOT>` 占位符） |
| `Read 文件`、"读模板"、"看 references"、"查看文件内容" | `read` |
| `Edit 文件`、"修改 title"、"`:root` 主题色"、"替换占位符"、"填充内容到 `<section>`" | `edit` |
| 要求生成纯文本新文件（报告、邮件、Markdown 等非基于模板的全新内容） | `write` |
| `mkdir` / `touch` / `ls` / `rm` 等其他 shell 命令 | 无对应专用工具，用 `skill_execute` 执行或跳过 |

**其他工具**：
- 指南中有脚本/可执行命令要运行（如 `node xxx.mjs`、`python xxx.py`） → 调用 `skill_execute`
- 指南中要求调用大模型生成片段内容（如单页文案、广告语） → 调用 `content_generate`
- 指南中要求联网搜索 → 调用 `web_search`

**执行规则**：
- 如果指南中有多个步骤 → 逐步执行，不要跳过
- 大文件（HTML PPT 100KB+）**不要**把完整内容塞进 `write` 的 content 参数；改用 `cp` 复制模板 + `edit` 替换占位区域的策略
- 不要直接回复用户"正在执行"，而是立即开始执行第一步"""

        enhanced_content = skill_content + guidance_suffix

        return {
            "success": True,
            "skill_name": skill_name,
            "skill_version": skill_version,
            "content": enhanced_content,
            "message": f"✅ Skill '{skill_name}' (v{skill_version}) loaded."
        }
