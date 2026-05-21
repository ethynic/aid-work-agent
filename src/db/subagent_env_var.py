"""
子智能体环境变量管理。

通用的子智能体级别环境变量存储，按租户隔离。
任何子智能体都可以定义自己的环境变量，运行时注入到 os.environ 供工具（如 http_api）使用。
"""

from typing import Optional, Dict, Any, List
from loguru import logger

from src.db.database import get_db_connection


class SubagentEnvVarDB:
    """子智能体环境变量管理"""

    @staticmethod
    def set_var(
        tenant_id: str,
        subagent_name: str,
        var_name: str,
        var_value: str,
        description: Optional[str] = None,
    ) -> bool:
        """UPSERT 一条环境变量"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """INSERT INTO subagent_env_vars (tenant_id, subagent_name, var_name, var_value, description)
                       VALUES (%s, %s, %s, %s, %s)
                       ON CONFLICT (tenant_id, subagent_name, var_name)
                       DO UPDATE SET var_value = EXCLUDED.var_value,
                                     description = EXCLUDED.description,
                                     updated_at = CURRENT_TIMESTAMP""",
                    (tenant_id, subagent_name, var_name, var_value, description),
                )
                conn.commit()
                return True
            except Exception as e:
                conn.rollback()
                logger.error(f"设置环境变量失败: {e}")
                return False

    @staticmethod
    def get_vars(tenant_id: str, subagent_name: str) -> List[Dict[str, Any]]:
        """获取某子智能体的所有环境变量"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT tenant_id, subagent_name, var_name, var_value, description,
                          created_at, updated_at
                   FROM subagent_env_vars
                   WHERE tenant_id = %s AND subagent_name = %s
                   ORDER BY var_name""",
                (tenant_id, subagent_name),
            )
            return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def get_all_vars_for_tenant(tenant_id: str) -> List[Dict[str, Any]]:
        """获取租户下所有子智能体的环境变量"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT tenant_id, subagent_name, var_name, var_value, description,
                          created_at, updated_at
                   FROM subagent_env_vars
                   WHERE tenant_id = %s
                   ORDER BY subagent_name, var_name""",
                (tenant_id,),
            )
            return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def delete_var(tenant_id: str, subagent_name: str, var_name: str) -> bool:
        """删除一条环境变量"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """DELETE FROM subagent_env_vars
                       WHERE tenant_id = %s AND subagent_name = %s AND var_name = %s""",
                    (tenant_id, subagent_name, var_name),
                )
                conn.commit()
                return cursor.rowcount > 0
            except Exception as e:
                conn.rollback()
                logger.error(f"删除环境变量失败: {e}")
                return False

    @staticmethod
    def delete_all_vars(tenant_id: str, subagent_name: str) -> bool:
        """删除某子智能体的所有环境变量"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """DELETE FROM subagent_env_vars
                       WHERE tenant_id = %s AND subagent_name = %s""",
                    (tenant_id, subagent_name),
                )
                conn.commit()
                return cursor.rowcount > 0
            except Exception as e:
                conn.rollback()
                logger.error(f"删除环境变量失败: {e}")
                return False

    @staticmethod
    def batch_set(tenant_id: str, subagent_name: str, vars: List[Dict[str, Any]]) -> bool:
        """批量设置某子智能体的环境变量（全量覆盖）"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                # 先删除该子智能体的所有环境变量
                cursor.execute(
                    """DELETE FROM subagent_env_vars
                       WHERE tenant_id = %s AND subagent_name = %s""",
                    (tenant_id, subagent_name),
                )
                # 再批量插入
                for var in vars:
                    cursor.execute(
                        """INSERT INTO subagent_env_vars (tenant_id, subagent_name, var_name, var_value, description)
                           VALUES (%s, %s, %s, %s, %s)""",
                        (
                            tenant_id,
                            subagent_name,
                            var.get("name", ""),
                            var.get("value", ""),
                            var.get("description"),
                        ),
                    )
                conn.commit()
                return True
            except Exception as e:
                conn.rollback()
                logger.error(f"批量设置环境变量失败: {e}")
                return False
