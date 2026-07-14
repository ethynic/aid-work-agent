"""
Prompt 版本管理服务层

编排 DB 访问层和缓存失效，提供完整的注册、版本、草稿、标签管理能力。
"""

import hashlib
from typing import Optional, Dict, Any, List

from loguru import logger

from src.db.prompt_db import (
    PromptRegistryDB,
    PromptVersionDB,
    PromptLabelDB,
    PromptDraftDB,
)
from src.prompts.prompt_resolver import prompt_resolver


# 高危 Prompt scope：修改影响全局所有租户
HIGH_RISK_SCOPES = {"subagent", "system_template"}


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

    # ========== 草稿管理 ==========

    @staticmethod
    def get_draft(prompt_id: str) -> Optional[Dict[str, Any]]:
        return PromptDraftDB.get(prompt_id)

    @staticmethod
    def save_draft(
        prompt_id: str,
        content: str,
        variables: Optional[Dict] = None,
        base_version: Optional[int] = None,
        updated_by: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        return PromptDraftDB.upsert(prompt_id, content, variables, base_version, updated_by)

    @staticmethod
    def delete_draft(prompt_id: str) -> bool:
        return PromptDraftDB.delete(prompt_id)

    @staticmethod
    def commit_draft(
        prompt_id: str,
        commit_message: Optional[str] = None,
        created_by: Optional[str] = None,
        variables: Optional[Dict] = None,
        model_config: Optional[Dict] = None,
    ) -> Optional[Dict[str, Any]]:
        draft = PromptDraftDB.get(prompt_id)
        if not draft:
            logger.warning(f"No draft found for prompt {prompt_id}")
            return None

        result = PromptRegistryService.commit_version(
            prompt_id=prompt_id,
            content=draft["content"],
            variables=variables or draft.get("variables"),
            model_config=model_config,
            commit_message=commit_message,
            created_by=created_by,
        )

        if result.get("version"):
            PromptDraftDB.delete(prompt_id)

        return result

    # ========== 标签管理 ==========

    @staticmethod
    def get_label(prompt_id: str, label: str) -> Optional[Dict[str, Any]]:
        label_record = PromptLabelDB.get_by_label(prompt_id, label)
        if not label_record:
            return None
        # 补充版本详情
        version_record = PromptVersionDB.get_by_id(str(label_record["version_id"]))
        return {
            "label": label_record["label"],
            "version_id": str(label_record["version_id"]),
            "version": version_record["version"] if version_record else None,
            "created_at": str(label_record.get("created_at", "")),
            "updated_at": str(label_record.get("updated_at", "")),
        }

    @staticmethod
    def set_label(
        prompt_id: str,
        label: str,
        version: int,
        created_by: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        version_record = PromptVersionDB.get_by_prompt_and_version(prompt_id, version)
        if not version_record:
            logger.warning(f"Version {version} not found for prompt {prompt_id}")
            return None

        # 高危 Prompt staging 校验
        if label == "production" and prompt_id:
            registry = PromptRegistryDB.get_by_id(prompt_id)
            if registry and registry["scope"] in HIGH_RISK_SCOPES:
                staging = PromptLabelDB.get_by_label(prompt_id, "staging")
                if staging and str(staging["version_id"]) != str(version_record["id"]):
                    logger.warning(
                        f"High-risk prompt {prompt_id} (scope={registry['scope']}) "
                        f"promoted to production without staging validation"
                    )
                    # 不阻塞，仅记录 warning。首次部署可能无 staging。

        result = PromptLabelDB.upsert(
            prompt_id=prompt_id,
            version_id=str(version_record["id"]),
            label=label,
            created_by=created_by,
        )

        if result:
            # 失效缓存
            prompt_resolver.cache.invalidate_label(prompt_id, label)
            if label == "production":
                # production 变更需要失效内容缓存
                prompt_resolver.cache.invalidate_prompt(prompt_id)

        return result

    @staticmethod
    def delete_label(prompt_id: str, label: str) -> bool:
        if label == "production":
            labels = PromptLabelDB.list_labels(prompt_id)
            prod_labels = [l for l in labels if l["label"] == "production"]
            if len(prod_labels) == 1 and len(labels) <= 1:
                logger.warning(f"Cannot delete the only label 'production' for prompt {prompt_id}")
                return False

        result = PromptLabelDB.delete(prompt_id, label)
        if result:
            prompt_resolver.cache.invalidate_label(prompt_id, label)
        return result

    @staticmethod
    def list_labels(prompt_id: str) -> List[Dict[str, Any]]:
        labels = PromptLabelDB.list_labels(prompt_id)
        result = []
        for label_record in labels:
            version_record = PromptVersionDB.get_by_id(str(label_record["version_id"]))
            result.append({
                "label": label_record["label"],
                "version_id": str(label_record["version_id"]),
                "version": version_record["version"] if version_record else None,
                "created_by": label_record.get("created_by"),
                "updated_by": label_record.get("updated_by"),
                "created_at": str(label_record.get("created_at", "")),
                "updated_at": str(label_record.get("updated_at", "")),
            })
        return result
