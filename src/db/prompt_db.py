"""
Prompt 版本管理数据库访问层

提供 prompt_registry、prompt_versions、prompt_labels、prompt_drafts 四张表的 CRUD 操作。
"""

import uuid
from typing import Optional, List, Dict, Any

from loguru import logger

from src.db.database import get_db_connection


# ============== PromptRegistryDB ==============

class PromptRegistryDB:
    """Prompt 注册表数据库访问"""

    @staticmethod
    def create(
        tenant_id: Optional[str],
        scope: str,
        scope_id: str,
        prompt_type: str = "normal",
        display_name: Optional[str] = None,
        description: Optional[str] = None,
        created_by: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    INSERT INTO prompt_registry (tenant_id, scope, scope_id, prompt_type,
                        display_name, description, created_by, updated_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (tenant_id, scope, scope_id, prompt_type,
                      display_name, description, created_by, created_by))
                conn.commit()
                return PromptRegistryDB.get_by_scope(tenant_id, scope, scope_id)
            except Exception as e:
                logger.error(f"Failed to create prompt registry: {e}")
                return None

    @staticmethod
    def get_by_id(prompt_id: str) -> Optional[Dict[str, Any]]:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM prompt_registry WHERE id = %s", (prompt_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    def get_by_scope(
        tenant_id: Optional[str], scope: str, scope_id: str
    ) -> Optional[Dict[str, Any]]:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            if tenant_id is None:
                cursor.execute(
                    "SELECT * FROM prompt_registry WHERE tenant_id IS NULL AND scope = %s AND scope_id = %s",
                    (scope, scope_id),
                )
            else:
                cursor.execute(
                    "SELECT * FROM prompt_registry WHERE tenant_id = %s AND scope = %s AND scope_id = %s",
                    (tenant_id, scope, scope_id),
                )
            row = cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    def list_prompts(
        tenant_id: Optional[str] = None,
        scope: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            conditions = []
            params: list = []

            if tenant_id is not None:
                conditions.append("tenant_id = %s")
                params.append(tenant_id)
            if scope is not None:
                conditions.append("scope = %s")
                params.append(scope)

            where = f" WHERE {' AND '.join(conditions)}" if conditions else ""

            cursor.execute(f"SELECT COUNT(*) AS total FROM prompt_registry{where}", params)
            total = cursor.fetchone()["total"]

            offset = (page - 1) * page_size
            cursor.execute(
                f"SELECT * FROM prompt_registry{where} ORDER BY created_at DESC LIMIT %s OFFSET %s",
                params + [page_size, offset],
            )
            items = [dict(row) for row in cursor.fetchall()]

            return {"total": total, "page": page, "page_size": page_size, "items": items}

    @staticmethod
    def update(prompt_id: str, **kwargs) -> Optional[Dict[str, Any]]:
        allowed = {"display_name", "description", "prompt_type", "updated_by"}
        updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
        if not updates:
            return PromptRegistryDB.get_by_id(prompt_id)

        set_parts = [f"{k} = %s" for k in updates] + ["updated_at = CURRENT_TIMESTAMP"]
        values = list(updates.values())

        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    f"UPDATE prompt_registry SET {', '.join(set_parts)} WHERE id = %s",
                    values + [prompt_id],
                )
                conn.commit()
                return PromptRegistryDB.get_by_id(prompt_id)
            except Exception as e:
                logger.error(f"Failed to update prompt registry: {e}")
                return None

    @staticmethod
    def delete(prompt_id: str) -> bool:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("DELETE FROM prompt_labels WHERE prompt_id = %s", (prompt_id,))
                cursor.execute("DELETE FROM prompt_drafts WHERE prompt_id = %s", (prompt_id,))
                cursor.execute("DELETE FROM prompt_versions WHERE prompt_id = %s", (prompt_id,))
                cursor.execute("DELETE FROM prompt_registry WHERE id = %s", (prompt_id,))
                conn.commit()
                return cursor.rowcount > 0
            except Exception as e:
                logger.error(f"Failed to delete prompt registry: {e}")
                return False


# ============== PromptVersionDB ==============

class PromptVersionDB:
    """Prompt 版本数据库访问"""

    @staticmethod
    def create(
        prompt_id: str,
        version: int,
        content: str,
        variables: Optional[Dict] = None,
        model_config: Optional[Dict] = None,
        commit_message: Optional[str] = None,
        content_hash: Optional[str] = None,
        parent_version: Optional[int] = None,
        created_by: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        version_id = str(uuid.uuid4())
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    INSERT INTO prompt_versions (id, prompt_id, version, content, variables,
                        model_config, commit_message, content_hash, parent_version, created_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (version_id, prompt_id, version, content, variables,
                      model_config, commit_message, content_hash, parent_version, created_by))
                conn.commit()
                return PromptVersionDB.get_by_id(version_id)
            except Exception as e:
                logger.error(f"Failed to create prompt version: {e}")
                return None

    @staticmethod
    def get_by_id(version_id: str) -> Optional[Dict[str, Any]]:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM prompt_versions WHERE id = %s", (version_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    def get_by_prompt_and_version(prompt_id: str, version: int) -> Optional[Dict[str, Any]]:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM prompt_versions WHERE prompt_id = %s AND version = %s",
                (prompt_id, version),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    def list_versions(
        prompt_id: str, page: int = 1, page_size: int = 20
    ) -> Dict[str, Any]:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) AS total FROM prompt_versions WHERE prompt_id = %s",
                (prompt_id,),
            )
            total = cursor.fetchone()["total"]

            offset = (page - 1) * page_size
            cursor.execute(
                "SELECT * FROM prompt_versions WHERE prompt_id = %s ORDER BY version DESC LIMIT %s OFFSET %s",
                (prompt_id, page_size, offset),
            )
            items = [dict(row) for row in cursor.fetchall()]
            return {"total": total, "page": page, "page_size": page_size, "items": items}

    @staticmethod
    def get_latest(prompt_id: str) -> Optional[Dict[str, Any]]:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM prompt_versions WHERE prompt_id = %s ORDER BY version DESC LIMIT 1",
                (prompt_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    def commit_version_atomic(
        prompt_id: str,
        content: str,
        variables: Optional[Dict] = None,
        model_config: Optional[Dict] = None,
        commit_message: Optional[str] = None,
        content_hash: Optional[str] = None,
        created_by: Optional[str] = None,
        parent_version: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """原子提交：同一事务内自增版本号 + 插入版本记录，避免并发竞态"""
        version_id = str(uuid.uuid4())
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                # 自增 latest_version 并获取新值
                cursor.execute("""
                    UPDATE prompt_registry
                    SET latest_version = latest_version + 1, updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                    RETURNING latest_version
                """, (prompt_id,))
                row = cursor.fetchone()
                if not row:
                    return None
                new_version = row["latest_version"]

                # parent_version 默认为新版本 - 1
                if parent_version is None:
                    parent_version = new_version - 1

                # 插入版本记录
                cursor.execute("""
                    INSERT INTO prompt_versions (id, prompt_id, version, content, variables,
                        model_config, commit_message, content_hash, parent_version, created_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (version_id, prompt_id, new_version, content, variables,
                      model_config, commit_message, content_hash, parent_version, created_by))

                conn.commit()
                return PromptVersionDB.get_by_id(version_id)
            except Exception as e:
                logger.error(f"Failed to commit version atomically: {e}")
                return None


# ============== PromptLabelDB ==============

class PromptLabelDB:
    """Prompt 标签数据库访问"""

    @staticmethod
    def upsert(
        prompt_id: str,
        version_id: str,
        label: str,
        created_by: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    INSERT INTO prompt_labels (prompt_id, version_id, label, created_by, updated_by)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (prompt_id, label) DO UPDATE SET
                        version_id = EXCLUDED.version_id,
                        updated_by = EXCLUDED.created_by,
                        updated_at = CURRENT_TIMESTAMP
                """, (prompt_id, version_id, label, created_by, created_by))
                conn.commit()
                return PromptLabelDB.get_by_label(prompt_id, label)
            except Exception as e:
                logger.error(f"Failed to upsert prompt label: {e}")
                return None

    @staticmethod
    def get_by_label(prompt_id: str, label: str) -> Optional[Dict[str, Any]]:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM prompt_labels WHERE prompt_id = %s AND label = %s",
                (prompt_id, label),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    def list_labels(prompt_id: str) -> List[Dict[str, Any]]:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM prompt_labels WHERE prompt_id = %s ORDER BY created_at",
                (prompt_id,),
            )
            return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def delete(prompt_id: str, label: str) -> bool:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM prompt_labels WHERE prompt_id = %s AND label = %s",
                (prompt_id, label),
            )
            conn.commit()
            return cursor.rowcount > 0


# ============== PromptDraftDB ==============

class PromptDraftDB:
    """Prompt 草稿数据库访问"""

    @staticmethod
    def upsert(
        prompt_id: str,
        content: str,
        variables: Optional[Dict] = None,
        base_version: Optional[int] = None,
        updated_by: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    INSERT INTO prompt_drafts (prompt_id, content, variables, base_version, updated_by)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (prompt_id) DO UPDATE SET
                        content = EXCLUDED.content,
                        variables = EXCLUDED.variables,
                        base_version = EXCLUDED.base_version,
                        updated_by = EXCLUDED.updated_by,
                        updated_at = CURRENT_TIMESTAMP
                """, (prompt_id, content, variables, base_version, updated_by))
                conn.commit()
                return PromptDraftDB.get(prompt_id)
            except Exception as e:
                logger.error(f"Failed to upsert prompt draft: {e}")
                return None

    @staticmethod
    def get(prompt_id: str) -> Optional[Dict[str, Any]]:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM prompt_drafts WHERE prompt_id = %s",
                (prompt_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    def delete(prompt_id: str) -> bool:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM prompt_drafts WHERE prompt_id = %s",
                (prompt_id,),
            )
            conn.commit()
            return cursor.rowcount > 0
