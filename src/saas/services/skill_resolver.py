"""
Skill 解析器

合并平台 skills + 租户自定义 skills，为租户实例提供隔离的 skill 列表。
"""

import os
from pathlib import Path
from typing import List, Dict, Any

from loguru import logger

from src.config.settings import settings


class SkillResolver:
    """
    Skill 解析器

    管理租户自定义 skills 的存储、加载、增删。
    """

    @staticmethod
    def get_tenant_skills_dir(tenant_id: str) -> Path:
        """获取租户自定义 skills 存储目录

        路径解析以项目根为基准（不依赖 cwd），避免容器内 cwd 不可写时
        mkdir 报 PermissionError。配置项支持相对路径（相对项目根）和绝对路径。
        """
        configured = Path(settings.saas.tenant_skills_dir)
        if configured.is_absolute():
            base_dir = configured / tenant_id / "skills"
        else:
            # skill_resolver.py 位于 src/saas/services/，上四级为项目根
            project_root = Path(__file__).parents[3]
            base_dir = project_root / configured / tenant_id / "skills"
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
