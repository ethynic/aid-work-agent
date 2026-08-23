"""
租户间知识库共享授权（租户级 A -> B）。

授权粒度是租户级：一条 (from_tenant_id, to_tenant_id) 记录表示 A 租户整个知识库
对 B 租户可见；具体共享哪些分类由第二步 subagent_knowledge_sources.sources 的
owner_tenant_id 决定，不在本表记录。无快照/缓存，一律读权威表，撤销立即生效。
"""

from typing import List, Optional, Dict, Any

from loguru import logger

from src.db.database import get_db_connection


class TenantKnowledgeShareDB:
    """租户知识库共享授权"""

    @staticmethod
    def list_for_to_tenant(to_tenant_id: str) -> List[Dict[str, Any]]:
        """返回 B 租户已接入的来源租户记录列表"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT from_tenant_id, to_tenant_id, created_at FROM tenant_knowledge_shares "
                "WHERE to_tenant_id = %s ORDER BY id",
                (to_tenant_id,),
            )
            return [dict(r) for r in cursor.fetchall()]

    @staticmethod
    def list_from_tenant_ids(to_tenant_id: str) -> List[str]:
        """返回 B 租户已接入的来源租户 ID 列表"""
        records = TenantKnowledgeShareDB.list_for_to_tenant(to_tenant_id)
        return [r["from_tenant_id"] for r in records]

    @staticmethod
    def has_share(from_tenant_id: str, to_tenant_id: str) -> bool:
        """判断 A->B 租户级授权是否存在"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM tenant_knowledge_shares "
                "WHERE from_tenant_id = %s AND to_tenant_id = %s",
                (from_tenant_id, to_tenant_id),
            )
            return cursor.fetchone() is not None

    @staticmethod
    def set(to_tenant_id: str, from_tenant_ids: List[str], created_by: Optional[str] = None) -> bool:
        """全量覆盖 B 租户已接入的来源租户列表（事务内先删后插）"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    "DELETE FROM tenant_knowledge_shares WHERE to_tenant_id = %s",
                    (to_tenant_id,),
                )
                for from_tenant_id in from_tenant_ids:
                    cursor.execute(
                        """INSERT INTO tenant_knowledge_shares (from_tenant_id, to_tenant_id, created_by)
                           VALUES (%s, %s, %s)
                           ON CONFLICT (from_tenant_id, to_tenant_id) DO NOTHING""",
                        (from_tenant_id, to_tenant_id, created_by),
                    )
                conn.commit()
                return True
            except Exception as e:
                conn.rollback()
                logger.error(f"设置知识库共享授权失败: {e}")
                return False
