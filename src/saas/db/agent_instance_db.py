"""
智能体实例 CRUD 操作
"""

import json
import uuid
from typing import Optional, List, Dict, Any

from loguru import logger

from src.db.database import get_db_connection


class AgentInstanceDB:
    """智能体实例数据库访问类"""

    @staticmethod
    def create(
        tenant_id: str,
        subagent_type: str,
        display_name: str,
        subscription_id: Optional[str] = None,
        config: Optional[dict] = None,
        bound_channel_type: Optional[str] = None,
        allowed_skills: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """创建实例"""
        instance_id = f"inst_{uuid.uuid4().hex[:12]}"

        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    INSERT INTO agent_instances
                        (instance_id, tenant_id, subscription_id, subagent_type,
                         display_name, config, bound_channel_type, allowed_skills)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    instance_id, tenant_id, subscription_id, subagent_type,
                    display_name,
                    json.dumps(config or {}, ensure_ascii=False),
                    bound_channel_type,
                    json.dumps(allowed_skills or [], ensure_ascii=False),
                ))
                conn.commit()
                logger.info(f"Agent instance created: {instance_id} ({subagent_type})")
                return AgentInstanceDB.get_by_id(instance_id)
            except Exception as e:
                logger.error(f"Failed to create agent instance: {e}")
                return None

    @staticmethod
    def get_by_id(instance_id: str) -> Optional[Dict[str, Any]]:
        """根据 ID 获取实例"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM agent_instances WHERE instance_id = ?", (instance_id,))
            row = cursor.fetchone()
            if row:
                return AgentInstanceDB._row_to_dict(row)
            return None

    @staticmethod
    def list_by_tenant(tenant_id: str, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """列出租户的实例"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            if status:
                cursor.execute(
                    "SELECT * FROM agent_instances WHERE tenant_id = ? AND status = ? ORDER BY created_at DESC",
                    (tenant_id, status),
                )
            else:
                cursor.execute(
                    "SELECT * FROM agent_instances WHERE tenant_id = ? ORDER BY created_at DESC",
                    (tenant_id,),
                )
            return [AgentInstanceDB._row_to_dict(row) for row in cursor.fetchall()]

    @staticmethod
    def update(instance_id: str, **kwargs) -> bool:
        """更新实例"""
        allowed_fields = {"display_name", "status", "config", "bound_channel_type", "allowed_skills"}
        updates = {}
        for k, v in kwargs.items():
            if k in allowed_fields and v is not None:
                if k in ("config",) and isinstance(v, dict):
                    v = json.dumps(v, ensure_ascii=False)
                elif k == "allowed_skills" and isinstance(v, list):
                    v = json.dumps(v, ensure_ascii=False)
                updates[k] = v

        if not updates:
            return False

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values())

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"UPDATE agent_instances SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE instance_id = ?",
                (*values, instance_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def delete(instance_id: str) -> bool:
        """删除实例"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM agent_instances WHERE instance_id = ?", (instance_id,))
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def list_running_by_tenant(tenant_id: str) -> List[Dict[str, Any]]:
        """列出租户所有 running 状态的实例"""
        return AgentInstanceDB.list_by_tenant(tenant_id, status="running")

    @staticmethod
    def list_all_running() -> List[Dict[str, Any]]:
        """列出所有 running 状态的实例"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM agent_instances WHERE status = 'running' ORDER BY tenant_id",
            )
            return [AgentInstanceDB._row_to_dict(row) for row in cursor.fetchall()]

    @staticmethod
    def _row_to_dict(row) -> Dict[str, Any]:
        """将数据库行转换为字典，解析 JSON 字段"""
        d = dict(row)
        if d.get("config"):
            d["config"] = json.loads(d["config"])
        if d.get("allowed_skills"):
            d["allowed_skills"] = json.loads(d["allowed_skills"])
        return d
