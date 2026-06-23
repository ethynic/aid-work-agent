#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Skill Registry - Skill注册表

管理已加载的Skill，提供查询、匹配和访问接口。

主要功能:
1. Skill注册和注销
2. 按名称、文件类型、关键词匹配Skill
3. 生成Skill描述供LLM使用
4. 管理Skill生命周期
"""

from pathlib import Path
from typing import Dict, List, Optional, Set
from loguru import logger

from src.core.skill_loader import SkillLoader, Skill


class SkillRegistry:
    """
    Skill注册表
    
    管理所有已加载的Skill，提供统一的访问接口。
    
    使用示例:
        registry = SkillRegistry()
        registry.load_from_directory(Path("skills"))

        # 获取Skill
        skill = registry.get("pdf")

        # 匹配Skill（基于 paths 字段的文件类型匹配）
        skill_name = registry.match_by_file("document.pdf")

        # 获取描述
        descriptions = registry.get_descriptions()
    """
    
    def __init__(self, skills_dir: Optional[Path] = None):
        """
        初始化Skill注册表

        Args:
            skills_dir: Skill目录路径，如果提供则自动加载
        """
        self._skills: Dict[str, Skill] = {}
        self._loader: Optional[SkillLoader] = None
        self._loaders: Dict[str, SkillLoader] = {}  # skill_name -> source loader（多目录支持）
        self._allowed: Optional[Set[str]] = None  # allow 名单

        if skills_dir:
            self.load_from_directory(skills_dir)

    def load_from_directory(
        self,
        skills_dir: Path,
        allowed: Optional[List[str]] = None,
    ) -> int:
        """
        从目录加载 Skill

        Args:
            skills_dir: Skill 目录路径
            allowed: 允许加载的 skill 名称列表，None 或空列表表示不限制

        Returns:
            加载的Skill数量
        """
        self._loader = SkillLoader(skills_dir)
        self._allowed = set(allowed) if allowed else None

        # 只加载允许的 skills
        all_skills = self._loader.skills
        if self._allowed is not None:
            self._skills = {
                name: skill
                for name, skill in all_skills.items()
                if name in self._allowed
            }
            logger.info(f"SkillRegistry loaded {len(self._skills)} skills (filtered by allowed list: {self._allowed})")
        else:
            self._skills = all_skills
            logger.info(f"SkillRegistry loaded {len(self._skills)} skills from {skills_dir}")

        # 填充 _loaders 映射
        self._loaders = {name: self._loader for name in self._skills}

        return len(self._skills)

    def load_from_directories(
        self,
        dirs: List[Path],
        allowed: Optional[List[str]] = None,
    ) -> int:
        """
        从多个目录加载 Skill，按优先级从低到高加载，高优先级目录覆盖同名 Skill。

        AgentSkills 标准支持多级发现（企业 > 个人 > 项目 > 插件）。
        服务器端映射：src/skills/ (基础) → storage/skills/enterprise/ (企业)。

        Args:
            dirs: Skill 目录列表（优先级从低到高）
            allowed: 允许加载的 skill 名称列表，None 或空列表表示不限制

        Returns:
            加载的 Skill 总数
        """
        self._allowed = set(allowed) if allowed else None
        combined_skills: Dict[str, Skill] = {}
        combined_loaders: Dict[str, SkillLoader] = {}

        for skills_dir in dirs:
            if not skills_dir.exists():
                continue
            loader = SkillLoader(skills_dir)
            # 后加载的目录覆盖先加载的同名 skill（高优先级覆盖低优先级）
            for name, skill in loader.skills.items():
                combined_skills[name] = skill
                combined_loaders[name] = loader

        # 过滤
        if self._allowed is not None:
            self._skills = {
                name: skill
                for name, skill in combined_skills.items()
                if name in self._allowed
            }
        else:
            self._skills = combined_skills

        # 保存 loader 映射和最后一个有效 loader（向后兼容）
        self._loaders = combined_loaders
        self._loader = loader if dirs else None

        logger.info(f"SkillRegistry loaded {len(self._skills)} skills from {len(dirs)} directories")
        return len(self._skills)

    def register(self, skill: Skill) -> bool:
        """
        注册一个Skill

        Args:
            skill: Skill对象

        Returns:
            是否注册成功
        """
        if skill.name in self._skills:
            logger.warning(f"Skill already registered: {skill.name}")
            return False

        self._skills[skill.name] = skill

        logger.info(f"Registered skill: {skill.name}")
        return True
    
    def unregister(self, name: str) -> bool:
        """
        注销一个Skill

        Args:
            name: Skill名称

        Returns:
            是否注销成功
        """
        if name not in self._skills:
            logger.warning(f"Skill not found: {name}")
            return False

        del self._skills[name]

        logger.info(f"Unregistered skill: {name}")
        return True
    
    def get(self, name: str) -> Optional[Skill]:
        """
        获取Skill
        
        Args:
            name: Skill名称
            
        Returns:
            Skill对象，如果不存在返回None
        """
        return self._skills.get(name)
    
    def get_content(self, name: str, substitutions: Optional[Dict[str, str]] = None) -> Optional[str]:
        """
        获取Skill内容

        Args:
            name: Skill名称
            substitutions: 可选的替换上下文，用于 $ARGUMENTS 等变量替换

        Returns:
            Skill内容字符串
        """
        # 优先使用 _loaders 精确定位（多目录场景）
        loader = self._loaders.get(name)
        if loader:
            return loader.get_skill_content(name, substitutions=substitutions)

        # Fallback 到单一 _loader（向后兼容）
        if self._loader:
            return self._loader.get_skill_content(name, substitutions=substitutions)

        # Last resort: 直接使用 skill body
        skill = self.get(name)
        if skill:
            return f"# Skill: {skill.name}\n\n{skill.body}"
        return None
    
    def get_descriptions(self) -> str:
        """
        获取所有Skill的描述
        
        用于LLM系统提示中展示可用Skill。
        
        Returns:
            Skill描述字符串
        """
        if not self._skills:
            return "(no skills available)"

        return "\n".join(
            f"- {name} (v{skill.version}): {skill.description}"
            for name, skill in self._skills.items()
        )
    
    def list_skills(self) -> List[str]:
        """
        列出所有Skill名称
        
        Returns:
            Skill名称列表
        """
        return list(self._skills.keys())
    
    def match_by_file(self, filename: str) -> Optional[str]:
        """
        根据文件名匹配Skill（基于 paths 字段）

        Args:
            filename: 文件名

        Returns:
            匹配的Skill名称，如果没有匹配返回None
        """
        # 多 loader 场景：遍历所有 loader 查找匹配
        for loader in set(self._loaders.values()):
            result = loader.match_by_file(filename)
            if result and result in self._skills:
                return result

        # Fallback 到单一 loader
        if self._loader:
            return self._loader.match_by_file(filename)
        return None

    def is_allowed(self, skill_name: str) -> bool:
        """检查 skill 是否在允许列表中"""
        if self._allowed is None:
            return True  # 无限制时都允许
        return skill_name in self._allowed

    def get_allowed_list(self) -> Optional[List[str]]:
        """获取允许的 skills 列表"""
        return list(self._allowed) if self._allowed is not None else None

    def get_skill_tool_definition(self) -> Dict:
        """
        获取Skill工具定义（仅包含允许的 skills）

        用于LLM function calling。

        Returns:
            工具定义字典
        """
        if not self._skills:
            return {
                "name": "use_skill",
                "description": "暂无可用技能",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "skill": {
                            "type": "string",
                            "description": "要加载的技能名称"
                        }
                    },
                    "required": ["skill"],
                },
            }

        # 只生成允许的 skills 描述（含 argument_hint）
        skill_list = "\n".join(
            f"- {name}: {skill.description}" + (f" (args: {skill.argument_hint})" if skill.argument_hint else "")
            for name, skill in self._skills.items()
        )

        return {
            "name": "use_skill",
            "description": f"""加载技能，获取完整的操作指南（SKILL.md 正文）。技能本质是给 LLM 的操作手册，加载后根据手册指引决定下一步操作。

**使用流程：**
1. 调用 use_skill(skill="技能名") 加载技能，获取操作指南
2. 仔细阅读返回的操作指南，根据其中的指引执行下一步：
   - 手册要求执行脚本/命令 → 调用 skill_execute
   - 手册要求生成内容 → 调用 content_generate
   - 手册要求搜索信息 → 调用 web_search
   - 手册给出多步骤工作流 → 按步骤逐步执行
3. 所有步骤完成后，调用 skill_complete(skill="技能名", summary="结果摘要") 标记完成

**注意：** 不要跳过步骤，严格按操作指南执行。不同技能的行为完全由其操作指南决定（有些需要执行脚本，有些是纯引导式的工作流）。

可用技能：
{skill_list}""",
            "input_schema": {
                "type": "object",
                "properties": {
                    "skill": {
                        "type": "string",
                        "description": "要加载的技能名称"
                    }
                },
                "required": ["skill"],
            },
        }
    
    def reload(self) -> int:
        """
        重新加载所有Skill

        Returns:
            加载的Skill数量
        """
        if self._loader:
            self._loader.reload_skills()
            self._skills = self._loader.skills
            logger.info(f"SkillRegistry reloaded {len(self._skills)} skills")
        return len(self._skills)
    
    def __len__(self) -> int:
        return len(self._skills)
    
    def __contains__(self, name: str) -> bool:
        return name in self._skills
    
    def __iter__(self):
        return iter(self._skills.items())


# 全局Skill注册表实例
skill_registry = SkillRegistry()
