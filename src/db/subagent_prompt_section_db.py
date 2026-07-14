"""
子智能体 Prompt 分段数据库访问层

分段值作为模板变量，运行时由 render_sections() 填充到 prompt 模板中。
模板中使用 {{section_key}} 双花括号占位符（Phase 4.0 起）。
"""
from typing import Optional, List, Dict, Any
from loguru import logger
from src.db.database import get_db_connection


class SubagentPromptSectionDB:
    @staticmethod
    def get_sections(agent_id: str) -> List[Dict[str, Any]]:
        """获取某智能体的所有分段"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM subagent_prompt_sections WHERE agent_id = %s ORDER BY section_key",
                (agent_id,),
            )
            return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def get_sections_map(agent_id: str) -> Dict[str, str]:
        """获取分段值的 {section_key: content} 映射，供运行时 render_sections 渲染用"""
        sections = SubagentPromptSectionDB.get_sections(agent_id)
        return {s["section_key"]: s["content"] or "" for s in sections}

    @staticmethod
    def get_section(agent_id: str, section_key: str) -> Optional[Dict[str, Any]]:
        """获取单个分段"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM subagent_prompt_sections WHERE agent_id = %s AND section_key = %s",
                (agent_id, section_key),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    def upsert_section(agent_id: str, section_key: str, content: str, updated_by: str = None) -> Optional[Dict[str, Any]]:
        """插入或更新一个分段"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    INSERT INTO subagent_prompt_sections (agent_id, section_key, content, updated_by)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (agent_id, section_key)
                    DO UPDATE SET content = EXCLUDED.content,
                                  updated_by = EXCLUDED.updated_by,
                                  updated_at = CURRENT_TIMESTAMP
                """, (agent_id, section_key, content, updated_by))
                conn.commit()
                return SubagentPromptSectionDB.get_section(agent_id, section_key)
            except Exception as e:
                logger.error(f"Failed to upsert section: {e}")
                return None

    @staticmethod
    def delete_sections(agent_id: str) -> bool:
        """删除某智能体的所有分段"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("DELETE FROM subagent_prompt_sections WHERE agent_id = %s", (agent_id,))
                conn.commit()
                return True
            except Exception as e:
                logger.error(f"Failed to delete sections: {e}")
                return False
