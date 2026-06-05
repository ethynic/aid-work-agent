"""
租户级子智能体知识库关联。

每个租户的每个子智能体可以关联一组知识库（source_type + display_name），
运行时注入 system prompt，指导 LLM 检索正确的知识库。
"""

from typing import Optional, Dict, Any, List

import psycopg2.extras
from loguru import logger

from src.db.database import get_db_connection


class SubagentKnowledgeSourceDB:
    """租户级子智能体知识库关联"""

    @staticmethod
    def get(tenant_id: str, subagent_name: str) -> List[Dict[str, str]]:
        """获取某租户某子智能体关联的知识库列表"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT sources FROM subagent_knowledge_sources WHERE tenant_id = %s AND subagent_name = %s",
                (tenant_id, subagent_name),
            )
            row = cursor.fetchone()
            if row and row["sources"]:
                return row["sources"]
            return []

    @staticmethod
    def set(tenant_id: str, subagent_name: str, sources: List[Dict[str, str]]) -> bool:
        """UPSERT 某租户某子智能体的知识库关联（全量覆盖）"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """INSERT INTO subagent_knowledge_sources (tenant_id, subagent_name, sources)
                       VALUES (%s, %s, %s)
                       ON CONFLICT (tenant_id, subagent_name)
                       DO UPDATE SET sources = EXCLUDED.sources,
                                     updated_at = CURRENT_TIMESTAMP""",
                    (tenant_id, subagent_name, psycopg2.extras.Json(sources)),
                )
                conn.commit()
                return True
            except Exception as e:
                conn.rollback()
                logger.error(f"设置知识库关联失败: {e}")
                return False

    @staticmethod
    def delete(tenant_id: str, subagent_name: str) -> bool:
        """删除某租户某子智能体的知识库关联"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    "DELETE FROM subagent_knowledge_sources WHERE tenant_id = %s AND subagent_name = %s",
                    (tenant_id, subagent_name),
                )
                conn.commit()
                return cursor.rowcount > 0
            except Exception as e:
                conn.rollback()
                logger.error(f"删除知识库关联失败: {e}")
                return False
