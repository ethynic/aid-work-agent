"""
租户级子智能体模板文件关联。

每个租户的每个子智能体可挂载多个模板文件（名称 + file_id + 元信息），
运行时注入 system prompt 末尾（### 相关模板位置信息），告知 LLM 可用模板位置。
与 subagent_knowledge_sources 同构：per-(tenant, subagent) JSONB 列表 + UNIQUE + UPSERT。
"""

from typing import List, Dict

import psycopg2.extras
from loguru import logger

from src.db.database import get_db_connection


class SubagentTemplateFileDB:
    """租户级子智能体模板文件关联"""

    @staticmethod
    def get(tenant_id: str, subagent_name: str) -> List[Dict]:
        """获取某租户某子智能体关联的模板文件列表"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT files FROM subagent_template_files WHERE tenant_id = %s AND subagent_name = %s",
                (tenant_id, subagent_name),
            )
            row = cursor.fetchone()
            if row and row["files"]:
                return row["files"]
            return []

    @staticmethod
    def set(tenant_id: str, subagent_name: str, files: List[Dict]) -> bool:
        """UPSERT 某租户某子智能体的模板文件关联（全量覆盖）"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """INSERT INTO subagent_template_files (tenant_id, subagent_name, files)
                       VALUES (%s, %s, %s)
                       ON CONFLICT (tenant_id, subagent_name)
                       DO UPDATE SET files = EXCLUDED.files,
                                     updated_at = CURRENT_TIMESTAMP""",
                    (tenant_id, subagent_name, psycopg2.extras.Json(files)),
                )
                conn.commit()
                return True
            except Exception as e:
                conn.rollback()
                logger.error(f"设置模板文件关联失败: {e}")
                return False

    @staticmethod
    def delete(tenant_id: str, subagent_name: str) -> bool:
        """删除某租户某子智能体的全部模板文件关联"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    "DELETE FROM subagent_template_files WHERE tenant_id = %s AND subagent_name = %s",
                    (tenant_id, subagent_name),
                )
                conn.commit()
                return cursor.rowcount > 0
            except Exception as e:
                conn.rollback()
                logger.error(f"删除模板文件关联失败: {e}")
                return False
