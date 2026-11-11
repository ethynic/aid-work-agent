"""
Prompt 版本管理服务层

编排 DB 访问层和缓存失效，提供注册、版本管理与一键回退上一版能力。
保存即生效：提交版本后最新版本即为运行时生效版本，无标签第二指针。
"""

import hashlib
from typing import Optional, Dict, Any

from loguru import logger

from src.db.prompt_db import (
    PromptRegistryDB,
    PromptVersionDB,
)
from src.prompts.prompt_resolver import prompt_resolver


class PromptRegistryService:
    """Prompt 注册与版本管理服务"""

    # ========== 注册管理 ==========

    @staticmethod
    def register_prompt(
        scope: str,
        scope_id: str,
        tenant_id: Optional[str] = None,
        prompt_type: str = "normal",
        display_name: Optional[str] = None,
        description: Optional[str] = None,
        created_by: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        existing = PromptRegistryDB.get_by_scope(tenant_id, scope, scope_id)
        if existing:
            return existing
        return PromptRegistryDB.create(
            tenant_id=tenant_id,
            scope=scope,
            scope_id=scope_id,
            prompt_type=prompt_type,
            display_name=display_name,
            description=description,
            created_by=created_by,
        )

    @staticmethod
    def get_prompt(prompt_id: str) -> Optional[Dict[str, Any]]:
        return PromptRegistryDB.get_by_id(prompt_id)

    @staticmethod
    def get_prompt_by_scope(
        tenant_id: Optional[str], scope: str, scope_id: str
    ) -> Optional[Dict[str, Any]]:
        return PromptRegistryDB.get_by_scope(tenant_id, scope, scope_id)

    @staticmethod
    def list_prompts(
        tenant_id: Optional[str] = None,
        scope: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        return PromptRegistryDB.list_prompts(tenant_id, scope, page, page_size)

    @staticmethod
    def update_prompt(prompt_id: str, **kwargs) -> Optional[Dict[str, Any]]:
        return PromptRegistryDB.update(prompt_id, **kwargs)

    @staticmethod
    def delete_prompt(prompt_id: str) -> bool:
        result = PromptRegistryDB.delete(prompt_id)
        if result:
            prompt_resolver.cache.invalidate_prompt(prompt_id)
        return result

    # ========== 版本管理 ==========

    @staticmethod
    def commit_version(
        prompt_id: str,
        content: str,
        variables: Optional[Dict] = None,
        model_config: Optional[Dict] = None,
        commit_message: Optional[str] = None,
        created_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        提交新版本。

        返回 {"version": ..., "dedup": bool}。
        dedup=True 表示内容与最新版本相同，返回已有版本。
        """
        content_hash = hashlib.sha256(content.encode()).hexdigest()

        # 去重检查：与最新版本的 content_hash 比较
        latest = PromptVersionDB.get_latest(prompt_id)
        if latest and latest.get("content_hash") == content_hash:
            return {"version": latest, "dedup": True}

        parent_version = latest["version"] if latest else None

        result = PromptVersionDB.commit_version_atomic(
            prompt_id=prompt_id,
            content=content,
            variables=variables,
            model_config=model_config,
            commit_message=commit_message,
            content_hash=content_hash,
            created_by=created_by,
            parent_version=parent_version,
        )

        if result:
            # 失效内容缓存（新版本号还没缓存过，但以防万一）
            prompt_resolver.cache.invalidate_prompt(prompt_id)

        return {"version": result, "dedup": False}

    @staticmethod
    def get_version(prompt_id: str, version: int) -> Optional[Dict[str, Any]]:
        return PromptVersionDB.get_by_prompt_and_version(prompt_id, version)

    @staticmethod
    def list_versions(
        prompt_id: str, page: int = 1, page_size: int = 20
    ) -> Dict[str, Any]:
        return PromptVersionDB.list_versions(prompt_id, page, page_size)

    @staticmethod
    def diff_versions(
        prompt_id: str, from_version: int, to_version: int
    ) -> Optional[Dict[str, Any]]:
        from_record = PromptVersionDB.get_by_prompt_and_version(prompt_id, from_version)
        to_record = PromptVersionDB.get_by_prompt_and_version(prompt_id, to_version)
        if not from_record or not to_record:
            return None
        return {
            "from": {"version": from_record["version"], "content": from_record["content"],
                     "created_at": str(from_record["created_at"]), "created_by": from_record.get("created_by")},
            "to": {"version": to_record["version"], "content": to_record["content"],
                   "created_at": str(to_record["created_at"]), "created_by": to_record.get("created_by")},
        }

    # ========== 回退 ==========

    @staticmethod
    def get_previous_version(prompt_id: str) -> Optional[Dict[str, Any]]:
        """获取最新版本的上一版记录（回退预览与回退执行共用）。

        优先取 version-1；版本可能被清理，取不到时按 latest.parent_version
        再试一次，都没有则返回 None（无可回退版本）。
        """
        latest = PromptVersionDB.get_latest(prompt_id)
        if not latest or latest["version"] <= 1:
            return None

        previous = PromptVersionDB.get_by_prompt_and_version(prompt_id, latest["version"] - 1)
        if not previous and latest.get("parent_version"):
            previous = PromptVersionDB.get_by_prompt_and_version(
                prompt_id, latest["parent_version"]
            )
        return previous

    @staticmethod
    def rollback_to_previous(
        prompt_id: str, created_by: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """回退上一版：上一版内容走同一条保存链存为新版本（保持「最新即生效」不变式，
        回退本身可再撤销）。

        仅一版或上一版记录已不存在时返回 None，调用方转「无可回退版本」。
        commit_version 自带 dedup：内容与最新版相同则返回已有版本（幂等）。
        """
        previous = PromptRegistryService.get_previous_version(prompt_id)
        if not previous:
            return None

        result = PromptRegistryService.commit_version(
            prompt_id=prompt_id,
            content=previous["content"],
            commit_message="回退上一版",
            created_by=created_by,
        )
        if result.get("version"):
            logger.info(
                f"回退 Prompt 版本: prompt_id={prompt_id}, "
                f"回退至内容版本={previous['version']}, 新版本={result['version']['version']}"
            )
        return result
