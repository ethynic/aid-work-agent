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
        instance_name: Optional[str] = None,
        avatar: str = "🤖",
        description: Optional[str] = None,
        personality_traits: Optional[List[str]] = None,
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
                         display_name, instance_name, avatar, description,
                         personality_traits, config, bound_channel_type, allowed_skills,
                         status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'idle')
                """, (
                    instance_id, tenant_id, subscription_id, subagent_type,
                    display_name,
                    instance_name or display_name,  # 默认用 display_name
                    avatar,
                    description,
                    json.dumps(personality_traits or [], ensure_ascii=False),
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
            cursor.execute("SELECT * FROM agent_instances WHERE instance_id = %s", (instance_id,))
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
                    "SELECT * FROM agent_instances WHERE tenant_id = %s AND status = %s ORDER BY created_at DESC",
                    (tenant_id, status),
                )
            else:
                cursor.execute(
                    "SELECT * FROM agent_instances WHERE tenant_id = %s ORDER BY created_at DESC",
                    (tenant_id,),
                )
            return [AgentInstanceDB._row_to_dict(row) for row in cursor.fetchall()]

    @staticmethod
    def update(instance_id: str, **kwargs) -> bool:
        """更新实例"""
        allowed_fields = {
            "display_name", "instance_name", "avatar", "description",
            "personality_traits", "status", "config", "bound_channel_type",
            "allowed_skills", "reply_style_id"
        }
        updates = {}
        for k, v in kwargs.items():
            if k in allowed_fields and v is not None:
                if k in ("config",) and isinstance(v, dict):
                    v = json.dumps(v, ensure_ascii=False)
                elif k in ("allowed_skills", "personality_traits") and isinstance(v, list):
                    v = json.dumps(v, ensure_ascii=False)
                updates[k] = v

        if not updates:
            return False

        set_clause = ", ".join(f"{k} = %s" for k in updates)
        values = list(updates.values())

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"UPDATE agent_instances SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE instance_id = %s",
                (*values, instance_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def delete(instance_id: str) -> bool:
        """删除实例"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM agent_instances WHERE instance_id = %s", (instance_id,))
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def list_running_by_tenant(tenant_id: str) -> List[Dict[str, Any]]:
        """列出租户所有 running 状态的实例（已弃用，返回空列表）"""
        from loguru import logger
        logger.warning(f"list_running_by_tenant called for tenant {tenant_id}: running status no longer supported")
        return []

    @staticmethod
    def list_all_running() -> List[Dict[str, Any]]:
        """列出所有 running 状态的实例（已弃用，返回空列表）"""
        from loguru import logger
        logger.warning("list_all_running called: running status no longer supported")
        return []

    @staticmethod
    def _row_to_dict(row) -> Dict[str, Any]:
        """将数据库行转换为字典，解析 JSON 字段"""
        d = dict(row)
        if d.get("config"):
            d["config"] = json.loads(d["config"])
        if d.get("allowed_skills"):
            d["allowed_skills"] = json.loads(d["allowed_skills"])
        if d.get("personality_traits"):
            d["personality_traits"] = json.loads(d["personality_traits"])
        return d
