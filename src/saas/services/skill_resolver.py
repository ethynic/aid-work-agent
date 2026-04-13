"""
Skill 解析器

合并平台 skills + 租户自定义 skills，为租户实例提供隔离的 skill 列表。
"""

import json
import os
from pathlib import Path
from typing import Optional, List, Dict, Any

from loguru import logger

from src.config.settings import settings
from src.saas.db.agent_instance_db import AgentInstanceDB


class SkillResolver:
    """
    Skill 解析器

    根据 agent_instance 的 allowed_skills 配置和租户自定义 skills，
    返回合并后的 skill 列表。
    """

    @staticmethod
    def get_allowed_skills(instance_id: str) -> Optional[List[str]]:
        """
        获取实例允许的 skill 列表

        如果实例配置了 allowed_skills，使用实例配置；
        否则返回 None（表示不限制）。
        """
        instance = AgentInstanceDB.get_by_id(instance_id)
        if not instance:
            return None

        allowed = instance.get("allowed_skills")
        if allowed and isinstance(allowed, str):
            allowed = json.loads(allowed)

        return allowed if allowed else None

    @staticmethod
    def get_tenant_skills_dir(tenant_id: str) -> Path:
        """获取租户自定义 skills 存储目录"""
        base_dir = Path(settings.saas.tenant_skills_dir) / tenant_id / "skills"
        base_dir.mkdir(parents=True, exist_ok=True)
        return base_dir

    @staticmethod
    def list_tenant_skills(tenant_id: str) -> List[Dict[str, Any]]:
        """
        列出租户的自定义 skills

        扫描 storage/tenants/{tenant_id}/skills/ 目录下的 SKILL.md 文件。
        """
        skills_dir = SkillResolver.get_tenant_skills_dir(tenant_id)
        skills = []

        for skill_dir in skills_dir.iterdir():
            if skill_dir.is_dir():
                skill_md = skill_dir / "SKILL.md"
                if skill_md.exists():
                    # 解析基本信息
                    try:
                        content = skill_md.read_text(encoding="utf-8")
                        name = skill_dir.name
                        # 从 frontmatter 提取简单信息
                        description = ""
                        if "---" in content:
                            parts = content.split("---", 2)
                            if len(parts) >= 3:
                                import yaml
                                try:
                                    fm = yaml.safe_load(parts[1])
                                    description = fm.get("description", "") if fm else ""
                                except Exception:
                                    pass

                        skills.append({
                            "name": name,
                            "description": description,
                            "path": str(skill_dir),
                        })
                    except Exception as e:
                        logger.warning(f"Failed to read tenant skill {skill_dir}: {e}")

        return skills

    @staticmethod
    def save_tenant_skill(tenant_id: str, skill_name: str, content: str) -> bool:
        """
        保存租户自定义 Skill

        Args:
            tenant_id: 租户 ID
            skill_name: Skill 名称
            content: SKILL.md 文件内容

        Returns:
            是否保存成功
        """
        try:
            skills_dir = SkillResolver.get_tenant_skills_dir(tenant_id)
            skill_dir = skills_dir / skill_name
            skill_dir.mkdir(exist_ok=True)

            skill_md = skill_dir / "SKILL.md"
            skill_md.write_text(content, encoding="utf-8")

            logger.info(f"Tenant skill saved: {tenant_id}/{skill_name}")
            return True
        except Exception as e:
            logger.error(f"Failed to save tenant skill: {e}")
            return False

    @staticmethod
    def delete_tenant_skill(tenant_id: str, skill_name: str) -> bool:
        """删除租户自定义 Skill"""
        import shutil
        try:
            skills_dir = SkillResolver.get_tenant_skills_dir(tenant_id)
            skill_dir = skills_dir / skill_name
            if skill_dir.exists():
                shutil.rmtree(skill_dir)
                logger.info(f"Tenant skill deleted: {tenant_id}/{skill_name}")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to delete tenant skill: {e}")
            return False
