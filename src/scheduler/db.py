"""定时任务数据访问层"""

import json
import uuid
import re
from datetime import datetime
from typing import Optional, List, Dict, Any

from loguru import logger
from src.db.database import get_db_connection, get_current_timestamp


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
               interval_seconds: int = None, session_id: str = None,
               tenant_id: str = "") -> Optional[Dict[str, Any]]:
        """创建定时任务

        tenant_id：任务属主租户（''=公共用户/无租户上下文）。
        创建时必须落库，否则新任务在租户视图不可见。
        """
        task_id = f"sched_{uuid.uuid4().hex[:12]}"
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        placeholder = "%s"

        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(f"""
                    INSERT INTO scheduled_tasks
                    (task_id, tenant_id, user_id, name, description, task_prompt,
                     schedule_type, cron_expression, interval_seconds, session_id,
                     status, created_at, updated_at)
                    VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder},
                            {placeholder}, {placeholder}, {placeholder}, {placeholder}, 'active', {placeholder}, {placeholder})
                """, (task_id, tenant_id or "", user_id, name, description, task_prompt,
                      schedule_type, cron_expression, interval_seconds, session_id,
                      now, now))
                conn.commit()
                logger.info(f"后端日志：定时任务创建 task_id={task_id}, user_id={user_id}, name={name}")
                return ScheduledTaskDB.get_by_id(task_id)
            except Exception as e:
                logger.opt(exception=True).error(f"后端日志：创建定时任务失败 {e}")
                return None

    @staticmethod
    def get_by_id(task_id: str, tenant_id: Optional[str] = None,
                  user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """根据任务ID获取定时任务

        tenant_id 可选：请求/工具链传入时 SQL 加租户（可叠加用户）条件，
        越权行直接查不到（fail-closed，不泄露存在性）；
        background runner / executor 内部调用不传（无请求上下文，保持全量行为）。
        """
        placeholder = "%s"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            if tenant_id is not None and user_id is not None:
                cursor.execute(f"""
                    SELECT * FROM scheduled_tasks
                    WHERE task_id = {placeholder} AND tenant_id = {placeholder}
                      AND user_id = {placeholder}
                """, (task_id, tenant_id, user_id))
            elif tenant_id is not None:
                cursor.execute(f"""
                    SELECT * FROM scheduled_tasks
                    WHERE task_id = {placeholder} AND tenant_id = {placeholder}
                """, (task_id, tenant_id))
            else:
                cursor.execute(f"SELECT * FROM scheduled_tasks WHERE task_id = {placeholder}", (task_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    def list_by_user(user_id: str, status: str = None, limit: int = 50,
                     tenant_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取用户的定时任务列表

        tenant_id 可选：请求/工具链传入时 SQL 加租户条件（''=遗留未回填行，
        在具体租户视图 fail-closed 不可见）；内部调用不传保持原行为。
        """
        placeholder = "%s"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            conditions = [f"user_id = {placeholder}"]
            params = [user_id]
            if status:
                conditions.append(f"status = {placeholder}")
                params.append(status)
            if tenant_id is not None:
                conditions.append(f"tenant_id = {placeholder}")
                params.append(tenant_id)
            params.append(limit)
            cursor.execute(f"""
                SELECT * FROM scheduled_tasks
                WHERE {" AND ".join(conditions)}
                ORDER BY created_at DESC
                LIMIT {placeholder}
            """, tuple(params))
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
    def list_all_for_reconcile() -> List[Dict[str, Any]]:
        """获取所有未取消任务（active + paused），供 background reconcile 对账使用。

        返回字段包含 manual_trigger_at 和 updated_at，用于：
        - 按 updated_at 变化判断是否需要重注册到 APScheduler
        - 扫描 manual_trigger_at 非空的任务执行手动触发
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM scheduled_tasks
                WHERE status IN ('active', 'paused')
                ORDER BY updated_at ASC
            """)
            return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def request_manual_trigger(task_id: str, *, tenant_id: Optional[str] = None,
                               user_id: Optional[str] = None) -> bool:
        """标记任务为待手动触发（SET manual_trigger_at=NOW()）。

        由 API/工具调用，background reconcile ≤30s 内扫到并执行。
        请求/工具链必须成对传 tenant_id+user_id（只传其一视为调用方身份不完整，
        fail-closed 拒绝）；两者都不传走受信内部路径。
        """
        if (tenant_id is None) != (user_id is None):
            return False
        placeholder = "%s"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            conditions = [f"task_id = {placeholder}", "status IN ('active', 'paused')"]
            params = [task_id]
            if tenant_id is not None:
                conditions.extend([
                    f"tenant_id = {placeholder}", f"user_id = {placeholder}"
                ])
                params.extend([tenant_id, user_id])
            cursor.execute(f"""
                UPDATE scheduled_tasks SET manual_trigger_at = NOW()
                WHERE {" AND ".join(conditions)}
            """, tuple(params))
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def clear_manual_trigger(task_id: str) -> bool:
        """清除手动触发标记（执行完成后调用）"""
        placeholder = "%s"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                UPDATE scheduled_tasks
                SET manual_trigger_at = NULL
                WHERE task_id = {placeholder}
            """, (task_id,))
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def update_schedule(task_id: str, cron_expression: str = None,
                        interval_seconds: int = None, *,
                        tenant_id: Optional[str] = None,
                        user_id: Optional[str] = None) -> bool:
        """更新任务调度配置。

        请求/工具链必须成对传 tenant_id+user_id（只传其一 fail-closed 拒绝），
        使对象级权限条件进入 UPDATE 本身以防 TOCTOU；两者都不传走受信内部路径。
        """
        from src.tools.scheduler.scheduled_task_tool import generate_cron_expression
        if (tenant_id is None) != (user_id is None):
            return False
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        placeholder = "%s"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            conditions = [f"task_id = {placeholder}"]
            params = [cron_expression, interval_seconds, now, task_id]
            if tenant_id is not None:
                conditions.extend([
                    f"tenant_id = {placeholder}", f"user_id = {placeholder}"
                ])
                params.extend([tenant_id, user_id])
            cursor.execute(f"""
                UPDATE scheduled_tasks
                SET cron_expression = {placeholder}, interval_seconds = {placeholder}, updated_at = {placeholder}
                WHERE {" AND ".join(conditions)}
            """, tuple(params))
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def update_status(task_id: str, status: str, *, tenant_id: Optional[str] = None,
                      user_id: Optional[str] = None) -> bool:
        """更新任务状态。

        请求/工具链必须成对传 tenant_id+user_id（只传其一 fail-closed 拒绝），
        使对象级权限条件进入 UPDATE 本身以防 TOCTOU；
        background runner 两者都不传走明确受信路径。
        """
        if (tenant_id is None) != (user_id is None):
            return False
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        placeholder = "%s"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            if tenant_id is None:
                cursor.execute(f"""
                    UPDATE scheduled_tasks
                    SET status = {placeholder}, updated_at = {placeholder}
                    WHERE task_id = {placeholder}
                """, (status, now, task_id))
            else:
                cursor.execute(f"""
                    UPDATE scheduled_tasks
                    SET status = {placeholder}, updated_at = {placeholder}
                    WHERE task_id = {placeholder} AND tenant_id = {placeholder}
                      AND user_id = {placeholder}
                """, (status, now, task_id, tenant_id, user_id))
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def update_after_run(task_id: str, success: bool,
                         result_summary: str = None) -> bool:
        """任务执行后更新统计"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        placeholder = "%s"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                UPDATE scheduled_tasks
                SET total_runs = total_runs + 1,
                    success_count = success_count + {placeholder},
                    fail_count = fail_count + {placeholder},
                    last_run_at = {placeholder},
                    updated_at = {placeholder}
                WHERE task_id = {placeholder}
            """, (1 if success else 0, 0 if success else 1, now, now, task_id))
            conn.commit()
            success_count = cursor.rowcount > 0
            if success_count:
                status = "success" if success else "failed"
                logger.info(f"后端日志：定时任务执行{status} task_id={task_id}")
            return success_count

    @staticmethod
    def delete(task_id: str, *, tenant_id: Optional[str] = None,
               user_id: Optional[str] = None) -> bool:
        """软删除（状态改为 cancelled），租户/用户条件语义同 update_status"""
        return ScheduledTaskDB.update_status(
            task_id, "cancelled", tenant_id=tenant_id, user_id=user_id
        )

    @staticmethod
    def count_by_user(user_id: str, status: str = "active",
                      tenant_id: Optional[str] = None) -> int:
        """统计用户的定时任务数量（tenant_id 可选：请求/工具链传入时加租户条件）"""
        placeholder = "%s"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            conditions = [f"user_id = {placeholder}", f"status = {placeholder}"]
            params = [user_id, status]
            if tenant_id is not None:
                conditions.append(f"tenant_id = {placeholder}")
                params.append(tenant_id)
            cursor.execute(f"""
                SELECT COUNT(*) as cnt FROM scheduled_tasks
                WHERE {" AND ".join(conditions)}
            """, tuple(params))
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
               started_at: str = None, completed_at: str = None,
               tenant_id: str = "") -> Optional[Dict[str, Any]]:
        """创建执行日志

        tenant_id：随任务属主租户落库（''=公共用户或遗留未回填），
        保证日志查询可按租户过滤。
        """
        log_id = f"slog_{uuid.uuid4().hex[:12]}"
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        placeholder = "%s"

        # 过滤敏感信息
        safe_result = result_summary or ""
        safe_error = _sanitize_error(error_message or "")
        safe_trace = _sanitize_error(error_trace or "")

        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(f"""
                    INSERT INTO scheduled_task_logs
                    (log_id, tenant_id, task_id, user_id, session_id, status, trigger_type,
                     result_summary, result_detail, error_message, error_trace,
                     duration_ms, token_usage, started_at, completed_at, created_at)
                    VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder},
                            {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})
                """, (log_id, tenant_id or "", task_id, user_id, session_id, status, trigger_type,
                      safe_result, result_detail, safe_error, safe_trace,
                      duration_ms, token_usage, started_at or now, completed_at, now))
                conn.commit()
                logger.info(f"后端日志：定时任务日志创建 log_id={log_id}, task_id={task_id}, status={status}")
                return ScheduledTaskLogDB.get_by_id(log_id)
            except Exception as e:
                logger.opt(exception=True).error(f"后端日志：创建定时任务日志失败 {e}")
                return None

    @staticmethod
    def get_by_id(log_id: str) -> Optional[Dict[str, Any]]:
        """根据日志ID获取日志"""
        placeholder = "%s"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT * FROM scheduled_task_logs WHERE log_id = {placeholder}", (log_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    def list_by_task(task_id: str, limit: int = 100,
                     tenant_id: Optional[str] = None,
                     user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取任务的执行日志

        tenant_id 可选：请求/工具链传入时 SQL 加租户（可叠加用户）条件（fail-closed）；
        内部调用不传保持原行为。
        """
        placeholder = "%s"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            if tenant_id is not None and user_id is not None:
                cursor.execute(f"""
                    SELECT * FROM scheduled_task_logs
                    WHERE task_id = {placeholder} AND tenant_id = {placeholder}
                      AND user_id = {placeholder}
                    ORDER BY created_at DESC
                    LIMIT {placeholder}
                """, (task_id, tenant_id, user_id, limit))
            elif tenant_id is not None:
                cursor.execute(f"""
                    SELECT * FROM scheduled_task_logs
                    WHERE task_id = {placeholder} AND tenant_id = {placeholder}
                    ORDER BY created_at DESC
                    LIMIT {placeholder}
                """, (task_id, tenant_id, limit))
            else:
                cursor.execute(f"""
                    SELECT * FROM scheduled_task_logs
                    WHERE task_id = {placeholder}
                    ORDER BY created_at DESC
                    LIMIT {placeholder}
                """, (task_id, limit))
            return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def list_by_user(user_id: str, limit: int = 50,
                     tenant_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取用户的所有执行日志

        tenant_id 可选：请求/工具链传入时 SQL 加租户条件；内部调用不传保持原行为。
        """
        placeholder = "%s"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            if tenant_id is not None:
                cursor.execute(f"""
                    SELECT * FROM scheduled_task_logs
                    WHERE user_id = {placeholder} AND tenant_id = {placeholder}
                    ORDER BY created_at DESC
                    LIMIT {placeholder}
                """, (user_id, tenant_id, limit))
            else:
                cursor.execute(f"""
                    SELECT * FROM scheduled_task_logs
                    WHERE user_id = {placeholder}
                    ORDER BY created_at DESC
                    LIMIT {placeholder}
                """, (user_id, limit))
            return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def get_stats(task_id: str, *, tenant_id: Optional[str] = None,
                  user_id: Optional[str] = None) -> Dict[str, Any]:
        """获取任务的执行统计

        请求/工具链必须成对传 tenant_id+user_id（只传其一视为调用方身份不完整，
        fail-closed 返回零统计）；两者都不传走受信内部路径。
        """
        if (tenant_id is None) != (user_id is None):
            return {"total_runs": 0, "success_count": 0, "fail_count": 0,
                    "avg_duration_ms": 0, "success_rate": 0}
        placeholder = "%s"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            conditions = [f"task_id = {placeholder}"]
            params = [task_id]
            if tenant_id is not None:
                conditions.extend([
                    f"tenant_id = {placeholder}", f"user_id = {placeholder}"
                ])
                params.extend([tenant_id, user_id])
            cursor.execute(f"""
                SELECT
                    COUNT(*) as total_runs,
                    SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as success_count,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as fail_count,
                    COALESCE(AVG(CASE WHEN status = 'success' THEN duration_ms END), 0) as avg_duration_ms
                FROM scheduled_task_logs
                WHERE {" AND ".join(conditions)}
            """, tuple(params))
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
