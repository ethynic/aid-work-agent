"""
租户数字员工实例 DB 访问。

统一模型：
- 直定制行：agent_id = base_agent_id（租户对基础员工的直接定制，无新 ID）
- 副本行：agent_id != base_agent_id（复制产生，全局唯一，纯连字符）

副本 system_prompt/工具/技能/模型运行时实时取自父定义，本表只存覆盖项。
"""

from typing import Any, Dict, List, Optional

import psycopg2.extras
from loguru import logger

from src.db.database import get_db_connection

_COLUMNS = (
    "id, tenant_id, agent_id, base_agent_id, base_source, display_name, description, "
    "extra_prompt, greeting_summary, greeting_prompts, status, created_by, "
    "created_at, updated_at"
)

# 允许租户覆盖的字段（update_overrides 白名单，表无工具/模型列即天然不可覆盖）
OVERRIDABLE_FIELDS = (
    "display_name",
    "description",
    "extra_prompt",
    "greeting_summary",
    "greeting_prompts",
)


def _row_to_dict(row) -> Optional[Dict[str, Any]]:
    if not row:
        return None
    data = dict(row)
    # JSONB 由 psycopg2 自动反序列化，此处仅兜底字符串形态
    gp = data.get("greeting_prompts")
    if isinstance(gp, str):
        import json
        try:
            data["greeting_prompts"] = json.loads(gp)
        except (json.JSONDecodeError, TypeError):
            data["greeting_prompts"] = None
    return data


class TenantAgentInstanceDB:
    """租户数字员工实例"""

    @staticmethod
    def get(tenant_id: str, agent_id: str) -> Optional[Dict[str, Any]]:
        """获取实例行（含 archived），租户不匹配返回 None（fail-closed）"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT {_COLUMNS} FROM tenant_agent_instances "
                "WHERE tenant_id = %s AND agent_id = %s",
                (tenant_id, agent_id),
            )
            return _row_to_dict(cursor.fetchone())

    @staticmethod
    def list_by_tenant(tenant_id: str, include_archived: bool = False) -> List[Dict[str, Any]]:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            sql = f"SELECT {_COLUMNS} FROM tenant_agent_instances WHERE tenant_id = %s"
            if not include_archived:
                sql += " AND status = 'active'"
            sql += " ORDER BY created_at DESC"
            cursor.execute(sql, (tenant_id,))
            return [r for r in (_row_to_dict(row) for row in cursor.fetchall()) if r]

    @staticmethod
    def list_active_by_tenant(tenant_id: str) -> List[Dict[str, Any]]:
        return TenantAgentInstanceDB.list_by_tenant(tenant_id, include_archived=False)

    @staticmethod
    def create(
        tenant_id: str,
        agent_id: str,
        base_agent_id: str,
        base_source: Optional[str] = None,
        display_name: Optional[str] = None,
        description: Optional[str] = None,
        extra_prompt: Optional[str] = None,
        greeting_summary: Optional[str] = None,
        greeting_prompts: Optional[List[Dict[str, str]]] = None,
        created_by: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """创建实例行。直定制行按 (tenant_id, agent_id) 复合唯一；副本行按部分唯一索引全局唯一，撞名抛异常由调用方转 400"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    f"""INSERT INTO tenant_agent_instances
                       (tenant_id, agent_id, base_agent_id, base_source, display_name,
                        description, extra_prompt, greeting_summary, greeting_prompts, created_by)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       RETURNING {_COLUMNS}""",
                    (
                        tenant_id, agent_id, base_agent_id, base_source, display_name,
                        description, extra_prompt, greeting_summary,
                        psycopg2.extras.Json(greeting_prompts) if greeting_prompts is not None else None,
                        created_by,
                    ),
                )
                row = cursor.fetchone()
                conn.commit()
                return _row_to_dict(row)
            except Exception as e:
                conn.rollback()
                logger.opt(exception=True).error(f"创建租户数字员工实例失败 agent={agent_id}: {e}")
                raise

    @staticmethod
    def update_overrides(tenant_id: str, agent_id: str, overrides: Dict[str, Any]) -> bool:
        """更新覆盖项（白名单字段），租户不匹配返回 False"""
        fields = [f for f in overrides if f in OVERRIDABLE_FIELDS]
        if not fields:
            return False
        sets, params = [], []
        for f in fields:
            sets.append(f"{f} = %s")
            value = overrides[f]
            if f == "greeting_prompts":
                value = psycopg2.extras.Json(value) if value is not None else None
            params.append(value)
        sets.append("updated_at = CURRENT_TIMESTAMP")
        params.extend([tenant_id, agent_id])
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    f"UPDATE tenant_agent_instances SET {', '.join(sets)} "
                    "WHERE tenant_id = %s AND agent_id = %s",
                    params,
                )
                conn.commit()
                return cursor.rowcount > 0
            except Exception as e:
                conn.rollback()
                logger.opt(exception=True).error(f"更新租户数字员工实例失败 agent={agent_id}: {e}")
                return False

    @staticmethod
    def soft_delete(tenant_id: str, agent_id: str) -> bool:
        """软删（archived）。直定制行（agent_id=base_agent_id）不允许删除"""
        row = TenantAgentInstanceDB.get(tenant_id, agent_id)
        if not row:
            return False
        if row["agent_id"] == row["base_agent_id"]:
            return False
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    "UPDATE tenant_agent_instances SET status = 'archived', updated_at = CURRENT_TIMESTAMP "
                    "WHERE tenant_id = %s AND agent_id = %s",
                    (tenant_id, agent_id),
                )
                conn.commit()
                return cursor.rowcount > 0
            except Exception as e:
                conn.rollback()
                logger.opt(exception=True).error(f"删除租户数字员工实例失败 agent={agent_id}: {e}")
                return False

    @staticmethod
    def count_active(tenant_id: str) -> int:
        """active 副本数（不含直定制行，用于副本上限）"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) AS cnt FROM tenant_agent_instances "
                "WHERE tenant_id = %s AND status = 'active' AND agent_id <> base_agent_id",
                (tenant_id,),
            )
            row = cursor.fetchone()
            return row["cnt"] if row else 0

    @staticmethod
    def exists_any(agent_id: str) -> bool:
        """agent_id 是否已被副本行占用（全局查重；直定制行 agent_id=base 名不占位）"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM tenant_agent_instances "
                "WHERE agent_id = %s AND agent_id <> base_agent_id LIMIT 1",
                (agent_id,),
            )
            return cursor.fetchone() is not None
