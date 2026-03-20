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
        
        # 匹配Skill
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
        self._allowed: Optional[Set[str]] = None  # allow 名单

        # 文件扩展名索引
        self._extension_index: Dict[str, str] = {}
        # 关键词索引
        self._keyword_index: Dict[str, Set[str]] = {}

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

        # 构建索引
        self._build_indices()
        return len(self._skills)
    
    def _build_indices(self):
        """构建加速查找的索引"""
        self._extension_index.clear()
        self._keyword_index.clear()
        
        for name, skill in self._skills.items():
            for trigger in skill.triggers:
                if trigger.type == "file_extension":
                    ext = trigger.pattern.lower()
                    self._extension_index[ext] = name
                elif trigger.type == "keyword":
                    keyword = trigger.pattern.lower()
                    if keyword not in self._keyword_index:
                        self._keyword_index[keyword] = set()
                    self._keyword_index[keyword].add(name)
    
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
        
        # 更新索引
        for trigger in skill.triggers:
            if trigger.type == "file_extension":
                ext = trigger.pattern.lower()
                self._extension_index[ext] = skill.name
            elif trigger.type == "keyword":
                keyword = trigger.pattern.lower()
                if keyword not in self._keyword_index:
                    self._keyword_index[keyword] = set()
                self._keyword_index[keyword].add(skill.name)
        
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
        
        skill = self._skills.pop(name)
        
        # 更新索引
        for trigger in skill.triggers:
            if trigger.type == "file_extension":
                ext = trigger.pattern.lower()
                self._extension_index.pop(ext, None)
            elif trigger.type == "keyword":
                keyword = trigger.pattern.lower()
                if keyword in self._keyword_index:
                    self._keyword_index[keyword].discard(name)
                    if not self._keyword_index[keyword]:
                        del self._keyword_index[keyword]
        
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
    
    def get_content(self, name: str) -> Optional[str]:
        """
        获取Skill内容
        
        Args:
            name: Skill名称
            
        Returns:
            Skill内容字符串
        """
        if self._loader:
            return self._loader.get_skill_content(name)
        
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
            f"- {name}: {skill.description}"
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
        根据文件名匹配Skill
        
        Args:
            filename: 文件名
            
        Returns:
            匹配的Skill名称，如果没有匹配返回None
        """
        # 首先检查扩展名索引
        filename_lower = filename.lower()
        for ext, skill_name in self._extension_index.items():
            if filename_lower.endswith(ext):
                return skill_name
        
        # 使用loader的匹配方法（支持正则）
        if self._loader:
            return self._loader.match_skill_by_file(filename)
        
        return None
    
    def match_by_keyword(self, text: str) -> List[str]:
        """
        根据关键词匹配Skill
        
        Args:
            text: 输入文本
            
        Returns:
            匹配的Skill名称列表
        """
        matched: Set[str] = set()
        text_lower = text.lower()
        
        # 检查关键词索引
        for keyword, skill_names in self._keyword_index.items():
            if keyword in text_lower:
                matched.update(skill_names)
        
        # 使用loader的匹配方法（支持正则）
        if self._loader:
            skill_name = self._loader.match_skill_by_keyword(text)
            if skill_name:
                matched.add(skill_name)
        
        return list(matched)
    
    def match_skill(self, context: Dict) -> Optional[str]:
        """
        综合匹配Skill
        
        根据上下文信息（文件名、文本内容等）匹配最合适的Skill。
        
        Args:
            context: 上下文信息，包含:
                - filename: 文件名
                - text: 文本内容
                - intent: 意图
                
        Returns:
            匹配的Skill名称，如果没有匹配返回None
        """
        # 优先匹配文件类型
        filename = context.get("filename")
        if filename:
            skill_name = self.match_by_file(filename)
            if skill_name:
                return skill_name
        
        # 匹配关键词
        text = context.get("text", "")
        if text:
            matched = self.match_by_keyword(text)
            if matched:
                # 返回第一个匹配的
                return matched[0]
        
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

        # 只生成允许的 skills 描述
        skill_list = "\n".join(
            f"- {name}: {skill.description}"
            for name, skill in self._skills.items()
        )

        return {
            "name": "use_skill",
            "description": f"""当任务需要特定技能支持时使用此工具。

适用场景：
- 处理文件（PDF/Word/Excel）时
- 需要翻译、总结、OCR 等能力时
- 需要发送邮件、搜索信息时

可用技能：
{skill_list}

请描述你的任务，系统会自动为你匹配合适的技能。""",
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
            self._build_indices()
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
