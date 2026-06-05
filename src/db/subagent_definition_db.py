"""
子智能体定义数据库访问层

提供 subagent_definitions 表的 CRUD 操作。
system_prompt 由 prompt_versions 管理，不在此表中。
"""

import json
import uuid
from typing import Optional, List, Dict, Any

import psycopg2.extras
from loguru import logger

from src.db.database import get_db_connection


def _json(val):
    """包装 Python 对象为 psycopg2 可接受的 JSONB 值"""
    if val is None:
        return None
    return psycopg2.extras.Json(val)


class SubagentDefinitionDB:
    """子智能体定义数据库访问"""

    @staticmethod
    def create(
        agent_id: str,
        name: str,
        description: Optional[str] = None,
        version: str = "1.0.0",
        author: Optional[str] = None,
        triggers: Optional[dict] = None,
        tools: Optional[dict] = None,
        skills: Optional[dict] = None,
        context: Optional[dict] = None,
        delegatable_to: Optional[list] = None,
        allow_delegation: bool = True,
        llm_provider: Optional[str] = None,
        reply_style: Optional[str] = None,
        business_pages: Optional[list] = None,
        knowledge_sources: Optional[list] = None,
        created_by: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        row_id = str(uuid.uuid4())
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    INSERT INTO subagent_definitions (
                        id, agent_id, name, description, version, author,
                        triggers, tools, skills, context,
                        delegatable_to, allow_delegation,
                        llm_provider, reply_style, business_pages, knowledge_sources,
                        created_by, updated_by
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s,
                        %s, %s, %s, %s,
                        %s, %s
                    )
                """, (
                    row_id, agent_id, name, description, version, author,
                    _json(triggers or {}),
                    _json(tools or {}), _json(skills or {}),
                    _json(context or {}),
                    _json(delegatable_to or []), allow_delegation,
                    llm_provider, reply_style, _json(business_pages),
                    _json(knowledge_sources or []),
                    created_by, created_by,
                ))
                conn.commit()
                return SubagentDefinitionDB.get_by_agent_id(agent_id)
            except Exception as e:
                logger.error(f"Failed to create subagent definition: {e}")
                return None

    @staticmethod
    def get_by_id(row_id: str) -> Optional[Dict[str, Any]]:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM subagent_definitions WHERE id = %s", (row_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    def get_by_agent_id(agent_id: str) -> Optional[Dict[str, Any]]:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM subagent_definitions WHERE agent_id = %s",
                (agent_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    def list_definitions(
        status: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            conditions = []
            params: list = []

            if status is not None:
                conditions.append("status = %s")
                params.append(status)

            where = f" WHERE {' AND '.join(conditions)}" if conditions else ""

            cursor.execute(
                f"SELECT COUNT(*) AS total FROM subagent_definitions{where}",
                params,
            )
            total = cursor.fetchone()["total"]

            offset = (page - 1) * page_size
            cursor.execute(
                f"SELECT * FROM subagent_definitions{where} ORDER BY created_at DESC LIMIT %s OFFSET %s",
                params + [page_size, offset],
            )
            items = [dict(row) for row in cursor.fetchall()]

            return {"total": total, "page": page, "page_size": page_size, "items": items}

    @staticmethod
    def list_active() -> List[Dict[str, Any]]:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM subagent_definitions WHERE status = 'active' ORDER BY name"
            )
            return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def update(agent_id: str, **kwargs) -> Optional[Dict[str, Any]]:
        allowed = {
            "name", "description", "version", "author",
            "triggers", "tools", "skills", "context",
            "delegatable_to", "allow_delegation",
            "llm_provider", "reply_style", "business_pages",
            "knowledge_sources",
            "status", "updated_by",
        }
        jsonb_fields = {
            "triggers", "tools", "skills", "context",
            "delegatable_to", "business_pages", "knowledge_sources",
        }
        updates = {}
        for k, v in kwargs.items():
            if k in allowed and v is not None:
                updates[k] = _json(v) if k in jsonb_fields else v
        if not updates:
            return SubagentDefinitionDB.get_by_agent_id(agent_id)

        set_parts = [f"{k} = %s" for k in updates] + ["updated_at = CURRENT_TIMESTAMP"]
        values = list(updates.values())

        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    f"UPDATE subagent_definitions SET {', '.join(set_parts)} WHERE agent_id = %s",
                    values + [agent_id],
                )
                conn.commit()
                return SubagentDefinitionDB.get_by_agent_id(agent_id)
            except Exception as e:
                logger.error(f"Failed to update subagent definition: {e}")
                return None

    @staticmethod
    def delete(agent_id: str) -> bool:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    "DELETE FROM subagent_definitions WHERE agent_id = %s",
                    (agent_id,),
                )
                conn.commit()
                return cursor.rowcount > 0
            except Exception as e:
                logger.error(f"Failed to delete subagent definition: {e}")
                return False
