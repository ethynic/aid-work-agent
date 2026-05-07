"""租户 Skills 缓存管理

按租户合并基础 skills + 租户自定义 skills，提供内存缓存。
Agent 首次处理某租户请求时通过此缓存按需加载。
"""

import time
from pathlib import Path
from typing import Dict, Optional, Set, Tuple

from loguru import logger

from src.core.skill_loader import SkillLoader, Skill
from src.saas.services.skill_resolver import SkillResolver


class TenantSkillCache:
    """租户 skills 缓存

    管理 tenant_id → 合并后 skills dict 的映射。
    缓存带 TTL（默认 5 分钟），过期后下次访问重新加载。
    """

    def __init__(self, ttl_seconds: int = 300):
        self._cache: Dict[str, Tuple[Dict[str, Skill], float]] = {}
        self._ttl_seconds = ttl_seconds

    def get_or_load(
        self,
        tenant_id: str,
        base_skills_dir: Path,
        allowed: Optional[Set[str]] = None,
    ) -> Dict[str, Skill]:
        """返回缓存的租户 skills，或从磁盘加载并合并

        Args:
            tenant_id: 租户 ID
            base_skills_dir: 基础 skills 目录（src/skills/）
            allowed: 允许的 skill 名称集合，None 表示不限制

        Returns:
            合并后的 {skill_name: Skill} 字典
        """
        # 检查缓存
        if tenant_id in self._cache:
            skills_dict, ts = self._cache[tenant_id]
            if time.time() - ts < self._ttl_seconds:
                logger.debug(f"[TenantSkillCache] 缓存命中: {tenant_id}, {len(skills_dict)} skills")
                return skills_dict

        # 从磁盘加载
        skills_dict = self._load_and_merge(tenant_id, base_skills_dir, allowed)
        self._cache[tenant_id] = (skills_dict, time.time())
        logger.info(f"[TenantSkillCache] 已加载租户 {tenant_id} 的 skills: {len(skills_dict)} 个")
        return skills_dict

    def invalidate(self, tenant_id: str) -> None:
        """清除某租户的全部缓存"""
        if tenant_id in self._cache:
            del self._cache[tenant_id]
            logger.info(f"[TenantSkillCache] 已清除租户 {tenant_id} 的缓存")

    def invalidate_skill(self, tenant_id: str, skill_name: str) -> None:
        """清除某租户的单个 skill 缓存

        直接清除整个租户缓存，下次访问时重新加载。
        """
        self.invalidate(tenant_id)

    def _load_and_merge(
        self,
        tenant_id: str,
        base_skills_dir: Path,
        allowed: Optional[Set[str]] = None,
    ) -> Dict[str, Skill]:
        """从磁盘加载基础 + 租户 skills 并合并"""
        try:
            combined: Dict[str, Skill] = {}

            # 1. 加载基础 skills
            if base_skills_dir.exists():
                base_loader = SkillLoader(base_skills_dir)
                combined.update(base_loader.skills)
                logger.debug(f"[TenantSkillCache] 基础 skills: {len(base_loader.skills)} 个")

            # 2. 加载租户 skills（覆盖同名基础 skill）
            tenant_dir = SkillResolver.get_tenant_skills_dir(tenant_id)
            if tenant_dir.exists():
                tenant_loader = SkillLoader(tenant_dir)
                tenant_count = len(tenant_loader.skills)
                if tenant_count > 0:
                    combined.update(tenant_loader.skills)
                    logger.info(f"[TenantSkillCache] 租户 {tenant_id} 自定义 skills: {tenant_count} 个")

            # 3. 应用 allowed 过滤
            if allowed is not None:
                combined = {k: v for k, v in combined.items() if k in allowed}

            return combined

        except Exception as e:
            logger.error(f"[TenantSkillCache] 加载租户 {tenant_id} skills 失败: {e}")
            # Fallback: 仅加载基础 skills
            try:
                if base_skills_dir.exists():
                    base_loader = SkillLoader(base_skills_dir)
                    fallback = dict(base_loader.skills)
                    if allowed is not None:
                        fallback = {k: v for k, v in fallback.items() if k in allowed}
                    return fallback
            except Exception:
                pass
            return {}


# 全局单例
tenant_skill_cache = TenantSkillCache()
