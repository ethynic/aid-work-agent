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
    def get(tenant_id: str, subagent_name: str) -> List[Dict[str, Any]]:
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
    def set(tenant_id: str, subagent_name: str, sources: List[Dict[str, Any]]) -> bool:
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
    def append_own_source(tenant_id: str, subagent_name: str,
                          source_type: str, display_name: str) -> bool:
        """原子追加一项本租户自有知识库来源（单条 UPSERT + jsonb 合并）。

        语义：
        - sources 中已存在同 source_type 且 owner_tenant_id 为空/缺失（本租户自有项）
          时不改动（幂等，保留人工调整过的 display_name）
        - 否则在现有数组末尾追加 {"source_type", "display_name"}；已有其他项
          （含共享来源项 owner_tenant_id 非空）原样保留

        与 set（全量覆盖）的区别：合并在单条 SQL 内完成（行级原子），避免
        "读旧值 → 应用层合并 → 全量写回" 与其他并发写（如数字员工配置页保存
        sources）相互覆盖丢项。

        Returns: True 成功（含已存在跳过）；False DB 异常（已回滚）
        """
        item = psycopg2.extras.Json([{"source_type": source_type, "display_name": display_name}])
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """INSERT INTO subagent_knowledge_sources (tenant_id, subagent_name, sources)
                       VALUES (%s, %s, %s)
                       ON CONFLICT (tenant_id, subagent_name)
                       DO UPDATE SET
                           sources = CASE
                               WHEN EXISTS (
                                   SELECT 1
                                   FROM jsonb_array_elements(
                                       COALESCE(subagent_knowledge_sources.sources, '[]'::jsonb)
                                   ) AS e
                                   WHERE e->>'source_type' = %s
                                     AND COALESCE(e->>'owner_tenant_id', '') = ''
                               ) THEN subagent_knowledge_sources.sources
                               ELSE COALESCE(subagent_knowledge_sources.sources, '[]'::jsonb) || %s::jsonb
                           END,
                           updated_at = CURRENT_TIMESTAMP""",
                    (tenant_id, subagent_name, item, source_type, item),
                )
                conn.commit()
                return True
            except Exception as e:
                conn.rollback()
                logger.error(f"原子追加知识库关联失败: {e}")
                return False

    @staticmethod
    def remove_source_type(source_type: str, owner_tenant_id: str, conn=None) -> int:
        """级联清理引用某栏目的授权项（栏目被删除时调用，同事务执行由调用方传入 conn）。

        清理两类引用：
        - owner_tenant_id 租户的自有项：tenant_id = owner_tenant_id 且项内 owner_tenant_id 为空
        - 其他租户的共享项：项内 owner_tenant_id = owner_tenant_id（来源栏目已不存在）

        注意：同名 source_type 的他租户自有项不受影响（靠 owner_tenant_id 条件区分）。

        Returns: 受影响行数（非清理项数）；DB 异常时由调用方回滚。
        """
        def _run(cursor) -> int:
            cursor.execute(
                """UPDATE subagent_knowledge_sources s
                   SET sources = (
                       SELECT COALESCE(jsonb_agg(e), '[]'::jsonb)
                       FROM jsonb_array_elements(COALESCE(s.sources, '[]'::jsonb)) AS e
                       WHERE NOT COALESCE(
                           e->>'source_type' = %s
                           AND ((COALESCE(e->>'owner_tenant_id', '') = '' AND s.tenant_id = %s)
                                OR e->>'owner_tenant_id' = %s),
                           FALSE)
                   ),
                   updated_at = CURRENT_TIMESTAMP
                   WHERE EXISTS (
                       SELECT 1
                       FROM jsonb_array_elements(COALESCE(s.sources, '[]'::jsonb)) AS e
                       WHERE COALESCE(
                           e->>'source_type' = %s
                           AND ((COALESCE(e->>'owner_tenant_id', '') = '' AND s.tenant_id = %s)
                                OR e->>'owner_tenant_id' = %s),
                           FALSE)
                   )""",
                (source_type, owner_tenant_id, owner_tenant_id) * 2,
            )
            return cursor.rowcount

        if conn is not None:
            return _run(conn.cursor())
        with get_db_connection() as own_conn:
            cursor = own_conn.cursor()
            try:
                affected = _run(cursor)
                own_conn.commit()
                return affected
            except Exception as e:
                own_conn.rollback()
                logger.error(f"级联清理知识库授权项失败: {e}")
                return -1

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
