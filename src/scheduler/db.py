"""定时任务数据访问层"""

import json
import uuid
import re
from datetime import datetime
from typing import Optional, List, Dict, Any

from loguru import logger
from src.db.database import get_db_connection


def _sanitize_error(error_msg: str) -> str:
    """过滤敏感信息"""
    if not error_msg:
        return error_msg
    patterns = [
        r'password["\s:=]+\S+',
        r'passwd["\s:=]+\S+',
        r'secret["\s:=]+\S+',
        r'token["\s:=]+\S+',
        r'api[_-]?key["\s:=]+\S+',
        r'access[_-]?key["\s:=]+\S+',
        r'private[_-]?key["\s:=]+\S+',
        r'auth[_-]?token["\s:=]+\S+',
    ]
    sanitized = error_msg
    for pattern in patterns:
        sanitized = re.sub(pattern, lambda m: m.group(0).split('=')[0] + '=***',
                          sanitized, flags=re.IGNORECASE)
    return sanitized


class ScheduledTaskDB:
    """定时任务数据库访问类"""

    @staticmethod
    def create(user_id: str, name: str, description: str, task_prompt: str,
               schedule_type: str, cron_expression: str = None,
               interval_seconds: int = None, session_id: str = None) -> Optional[Dict[str, Any]]:
        """创建定时任务"""
        task_id = f"sched_{uuid.uuid4().hex[:12]}"
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    INSERT INTO scheduled_tasks 
                    (task_id, user_id, name, description, task_prompt, 
                     schedule_type, cron_expression, interval_seconds, session_id,
                     status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
                """, (task_id, user_id, name, description, task_prompt,
                      schedule_type, cron_expression, interval_seconds, session_id,
                      now, now))
                conn.commit()
                logger.info(f"后端日志：定时任务创建 task_id={task_id}, user_id={user_id}, name={name}")
                return ScheduledTaskDB.get_by_id(task_id)
            except Exception as e:
                logger.error(f"后端日志：创建定时任务失败 {e}", exc_info=True)
                return None

    @staticmethod
    def get_by_id(task_id: str) -> Optional[Dict[str, Any]]:
        """根据任务ID获取定时任务"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM scheduled_tasks WHERE task_id = ?", (task_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    def list_by_user(user_id: str, status: str = None, limit: int = 50) -> List[Dict[str, Any]]:
        """获取用户的定时任务列表"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            if status:
                cursor.execute("""
                    SELECT * FROM scheduled_tasks
                    WHERE user_id = ? AND status = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (user_id, status, limit))
            else:
                cursor.execute("""
                    SELECT * FROM scheduled_tasks
                    WHERE user_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (user_id, limit))
            return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def list_active() -> List[Dict[str, Any]]:
        """获取所有活跃任务（启动时加载）"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM scheduled_tasks
                WHERE status = 'active'
                ORDER BY next_run_at ASC
            """)
            return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def update_status(task_id: str, status: str) -> bool:
        """更新任务状态"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE scheduled_tasks
                SET status = ?, updated_at = ?
                WHERE task_id = ?
            """, (status, now, task_id))
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def update_after_run(task_id: str, success: bool,
                         result_summary: str = None) -> bool:
        """任务执行后更新统计"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE scheduled_tasks
                SET total_runs = total_runs + 1,
                    success_count = success_count + CASE WHEN ? THEN 1 ELSE 0 END,
                    fail_count = fail_count + CASE WHEN ? THEN 1 ELSE 0 END,
                    last_run_at = ?,
                    updated_at = ?
                WHERE task_id = ?
            """, (1 if success else 0, 0 if success else 1, now, now, task_id))
            conn.commit()
            success_count = cursor.rowcount > 0
            if success_count:
                status = "success" if success else "failed"
                logger.info(f"后端日志：定时任务执行{status} task_id={task_id}")
            return success_count

    @staticmethod
    def delete(task_id: str) -> bool:
        """软删除（状态改为 cancelled）"""
        return ScheduledTaskDB.update_status(task_id, "cancelled")

    @staticmethod
    def count_by_user(user_id: str, status: str = "active") -> int:
        """统计用户的定时任务数量"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(*) as cnt FROM scheduled_tasks
                WHERE user_id = ? AND status = ?
            """, (user_id, status))
            row = cursor.fetchone()
            return row["cnt"] if row else 0


class ScheduledTaskLogDB:
    """定时任务执行日志数据库访问类"""

    @staticmethod
    def create(task_id: str, user_id: str, session_id: str = None,
               status: str = "success", trigger_type: str = "scheduled",
               result_summary: str = None, result_detail: str = None,
               error_message: str = None, error_trace: str = None,
               duration_ms: int = 0, token_usage: int = 0,
               started_at: str = None, completed_at: str = None) -> Optional[Dict[str, Any]]:
        """创建执行日志"""
        log_id = f"slog_{uuid.uuid4().hex[:12]}"
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 过滤敏感信息
        safe_result = result_summary or ""
        safe_error = _sanitize_error(error_message or "")
        safe_trace = _sanitize_error(error_trace or "")

        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    INSERT INTO scheduled_task_logs
                    (log_id, task_id, user_id, session_id, status, trigger_type,
                     result_summary, result_detail, error_message, error_trace,
                     duration_ms, token_usage, started_at, completed_at, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (log_id, task_id, user_id, session_id, status, trigger_type,
                      safe_result, result_detail, safe_error, safe_trace,
                      duration_ms, token_usage, started_at or now, completed_at, now))
                conn.commit()
                logger.info(f"后端日志：定时任务日志创建 log_id={log_id}, task_id={task_id}, status={status}")
                return ScheduledTaskLogDB.get_by_id(log_id)
            except Exception as e:
                logger.error(f"后端日志：创建定时任务日志失败 {e}", exc_info=True)
                return None

    @staticmethod
    def get_by_id(log_id: str) -> Optional[Dict[str, Any]]:
        """根据日志ID获取日志"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM scheduled_task_logs WHERE log_id = ?", (log_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    def list_by_task(task_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """获取任务的执行日志"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM scheduled_task_logs
                WHERE task_id = ?
                ORDER BY created_at DESC
                LIMIT ?
            """, (task_id, limit))
            return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def list_by_user(user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """获取用户的所有执行日志"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM scheduled_task_logs
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT ?
            """, (user_id, limit))
            return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def get_stats(task_id: str) -> Dict[str, Any]:
        """获取任务的执行统计"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    COUNT(*) as total_runs,
                    SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as success_count,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as fail_count,
                    COALESCE(AVG(CASE WHEN status = 'success' THEN duration_ms END), 0) as avg_duration_ms
                FROM scheduled_task_logs
                WHERE task_id = ?
            """, (task_id,))
            row = cursor.fetchone()
            if row:
                return {
                    "total_runs": row["total_runs"],
                    "success_count": row["success_count"],
                    "fail_count": row["fail_count"],
                    "avg_duration_ms": round(row["avg_duration_ms"], 2),
                    "success_rate": round(row["success_count"] / row["total_runs"] * 100, 1)
                    if row["total_runs"] > 0 else 0
                }
            return {"total_runs": 0, "success_count": 0, "fail_count": 0,
                    "avg_duration_ms": 0, "success_rate": 0}
