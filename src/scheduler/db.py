"""定时任务数据访问层（v2 Runner 化重建，设计 §9）

真源为 scheduled_tasks 单表：到期认领（claim_due 原子 UPDATE + 不可变提交
快照）、manual pending 槽、occurrence_key 条件化的 clear/backoff、删除守卫
事务、统计回写。执行历史不再有独立日志表——由 agent_runners + 任务会话
（chat_sessions.metadata.scheduled_task 标记）消息构成。
"""

import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger

from src.db.database import get_db_connection
from src.db.models import generate_session_id

# pause_reason 与 runner error_code 的映射（设计 §5.4：仅执行侧两类触发自动暂停）
_PAUSE_REASON_BY_ERROR_CODE = {
    "CREDIT_BLOCKED": "credit_blocked",
    "PROFILE_FORBIDDEN": "profile_forbidden",
}


class ScheduledTaskBusyError(RuntimeError):
    """任务会话存在非终态 runner，删除被守卫拒绝（调用方映射 409 引导重试）"""


class ScheduledTaskNotAcceptable(RuntimeError):
    """首次接单守卫失败（提交事务内，任务行 FOR UPDATE 锁下复核失配）。

    reason 取值：task_missing（任务行不存在）/ not_active（已暂停或终态）/
    pending_changed（pending 已被消费/替换为其他 occurrence）——与暂停/删除/
    认领在行锁上串行化，消灭"暂停后首次接单"竞态（设计 §4.3）。
    """

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def lock_for_acceptance_in_tx(cursor, task_id: str, occurrence_key: str) -> Dict[str, Any]:
    """提交事务内的首次接单守卫（设计 §4.1/§4.3，调用方持有同一事务）。

    - SELECT ... FOR UPDATE 锁任务行：与暂停/删除/认领（claim_due、
      delete_with_session 同样先锁该行）串行化——暂停先提交则接单被拒；
      接单先提交则暂停清 pending 属"已接单后清理"，语义正确；
    - 校验 status='active' AND pending_occurrence_key == occurrence_key，
      失配抛 ScheduledTaskNotAcceptable（携带原因）；
    - 必须先于 agent_runners 的 INSERT 执行（行锁与插入同事务）。
    """
    cursor.execute("""SELECT task_id, tenant_id, user_id, status, pending_occurrence_key
        FROM scheduled_tasks WHERE task_id = %s FOR UPDATE""", (task_id,))
    row = cursor.fetchone()
    if row is None:
        raise ScheduledTaskNotAcceptable("task_missing")
    task = dict(row)
    if task["status"] != "active":
        raise ScheduledTaskNotAcceptable("not_active")
    if task["pending_occurrence_key"] != occurrence_key:
        raise ScheduledTaskNotAcceptable("pending_changed")
    return task


class ScheduledTaskDB:
    """定时任务数据库访问类（手写 SQL DAL，全参数化）"""

    # ==================== 创建 / 查询 ====================

    @staticmethod
    def create(*, user_id: str, tenant_id: str, name: str, task_prompt: str,
               schedule_type: str, cron_expression: str = None,
               interval_seconds: int = None, run_at=None, attachments=None,
               profile_id: str = "main", instance_id: str = None,
               subagent_id: str = None, notification_target_id: str = None,
               next_due_at=None) -> Dict[str, Any]:
        """创建定时任务（同事务创建任务专属会话）。

        - task_prompt 为自包含任务定义；attachments 为受权资源引用（file_id 等），
          创建时固化，后续执行不再回读创建会话上下文；
        - 任务专属会话（chat_sessions）是执行结果存储容器：metadata 写
          {"scheduled_task": task_id} 标记，供任务视图识别与 chat 提交拒绝；
        - next_due_at 等调度值由调用方（工具/API，Phase 4）计算后传入。
        """
        task_id = f"sched_{uuid.uuid4().hex[:12]}"
        session_id = generate_session_id()

        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                # 1) 任务专属会话（与任务行同事务：任务落库则会话必然存在）
                cursor.execute("""
                    INSERT INTO chat_sessions
                    (session_id, user_id, tenant_id, subagent_id, instance_id, title, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                """, (session_id, user_id, tenant_id, subagent_id, instance_id,
                      name, json.dumps({"scheduled_task": task_id})))
                # 2) 任务行
                cursor.execute("""
                    INSERT INTO scheduled_tasks
                    (task_id, tenant_id, user_id, name, task_prompt, attachments,
                     profile_id, instance_id, schedule_type, cron_expression,
                     interval_seconds, run_at, session_id, notification_target_id,
                     status, next_due_at)
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s,
                            %s, %s, 'active', %s)
                """, (task_id, tenant_id, user_id, name, task_prompt,
                      json.dumps(attachments or []),
                      profile_id, instance_id, schedule_type, cron_expression,
                      interval_seconds, run_at, session_id, notification_target_id,
                      next_due_at))
                cursor.execute(
                    "SELECT * FROM scheduled_tasks WHERE task_id = %s", (task_id,))
                row = cursor.fetchone()
                conn.commit()
                logger.info(f"后端日志：定时任务创建 task_id={task_id}, "
                            f"user_id={user_id}, session_id={session_id}, name={name}")
                return dict(row) if row else {}
            except Exception as e:
                logger.opt(exception=True).error(f"后端日志：创建定时任务失败 {e}")
                raise

    @staticmethod
    def get_by_id(task_id: str, *, tenant_id: Optional[str] = None,
                  user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """根据任务 ID 获取定时任务。

        tenant_id/user_id 可选：传入时条件进 WHERE（越权行直接查不到，
        fail-closed 不泄露存在性）；调度侧内部调用不传。
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()
            conditions = ["task_id = %s"]
            params: List[Any] = [task_id]
            if tenant_id is not None:
                conditions.append("tenant_id = %s")
                params.append(tenant_id)
            if user_id is not None:
                conditions.append("user_id = %s")
                params.append(user_id)
            cursor.execute(
                f"SELECT * FROM scheduled_tasks WHERE {' AND '.join(conditions)}",
                tuple(params))
            row = cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    def list_by_user(user_id: str, *, tenant_id: Optional[str] = None,
                     status: Optional[str] = None,
                     limit: int = 100) -> List[Dict[str, Any]]:
        """获取用户的定时任务列表（tenant_id 可选：租户视图 fail-closed 过滤）"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            conditions = ["user_id = %s"]
            params: List[Any] = [user_id]
            if status:
                conditions.append("status = %s")
                params.append(status)
            if tenant_id is not None:
                conditions.append("tenant_id = %s")
                params.append(tenant_id)
            params.append(limit)
            cursor.execute(f"""
                SELECT * FROM scheduled_tasks
                WHERE {' AND '.join(conditions)}
                ORDER BY created_at DESC
                LIMIT %s
            """, tuple(params))
            return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def count_by_user(user_id: str, status: str = "active",
                      *, tenant_id: Optional[str] = None) -> int:
        """统计用户的定时任务数量（max_tasks_per_user 上限用）"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            conditions = ["user_id = %s", "status = %s"]
            params: List[Any] = [user_id, status]
            if tenant_id is not None:
                conditions.append("tenant_id = %s")
                params.append(tenant_id)
            cursor.execute(
                f"SELECT COUNT(*) AS cnt FROM scheduled_tasks WHERE {' AND '.join(conditions)}",
                tuple(params))
            row = cursor.fetchone()
            return row["cnt"] if row else 0

    # ==================== 到期认领与 pending 交接 ====================

    @staticmethod
    def list_due(now, limit: int = 20) -> List[Dict[str, Any]]:
        """列出已到期待认领任务（dispatch 循环扫描入口）。

        条件三要素：active + next_due_at 到期 + 无 pending 占用（认领中任务
        不重复进入候选）。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM scheduled_tasks
                WHERE status = 'active'
                  AND next_due_at <= %s
                  AND pending_occurrence_key IS NULL
                ORDER BY next_due_at
                LIMIT %s
            """, (now, limit))
            return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def claim_due(task_id: str, *, prompt_augmentations: Optional[list] = None,
                  now: datetime = None) -> Optional[Dict[str, Any]]:
        """原子认领到期任务（单短事务：锁行 → 校验/作废 → 派生参数 → 推进调度）。

        **occurrence、下次执行时间、执行快照全部派生自锁定的当前任务行**
        （同一份有效定义，调用方不传任何调度参数——扫描后发生改期的旧候选
        不可能覆盖新调度）：
        - SELECT 即校验（active + 到期 + 无 pending，DB 时钟）+ FOR UPDATE
          行锁：与编辑/改期/暂停/删除串行化；不满足返回 None；
        - once 已过执行宽限 → 置 expired 不执行（返回 None）；
        - occurrence_key = 锁定行的 next_due_at（本次消费的 due），幂等键 =
          '{task_id}:{occurrence_key}'；
        - advance = compute_next_due(锁定行)（cron 从 now 合并、interval 锚定
          相位、once 为 NULL）；
        - 快照从锁定行构建（含 prompt_augmentations 非交互指令），重试原样
          重建不重新生成。
        """
        from src.scheduler.schedule import compute_next_due, once_expired

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM scheduled_tasks
                WHERE task_id = %s
                  AND status = 'active'
                  AND next_due_at <= now()
                  AND pending_occurrence_key IS NULL
                FOR UPDATE
            """, (task_id,))
            row = cursor.fetchone()
            if not row:
                return None
            task = dict(row)
            now = now or datetime.now()
            if once_expired(task, now):
                cursor.execute("""
                    UPDATE scheduled_tasks SET status = 'expired', updated_at = now()
                    WHERE task_id = %s AND status = 'active'
                """, (task_id,))
                conn.commit()
                logger.info(f"后端日志：once 定时任务已过宽限作废 task_id={task_id}")
                return None
            occurrence_key = task["next_due_at"].isoformat()
            advance_to = compute_next_due(task, now)
            snapshot = {
                "text": task["task_prompt"],
                "attachments": task.get("attachments") or [],
                "profile_id": task["profile_id"],
                "instance_id": task.get("instance_id"),
                "prompt_augmentations": prompt_augmentations or [],
            }
            cursor.execute("""
                UPDATE scheduled_tasks SET
                    next_due_at = %s,
                    pending_occurrence_key = %s,
                    pending_trigger = 'scheduled',
                    pending_retry_at = now(),
                    pending_snapshot = %s::jsonb,
                    updated_at = now()
                WHERE task_id = %s
                RETURNING *
            """, (advance_to, occurrence_key,
                  json.dumps(snapshot, ensure_ascii=False), task_id))
            updated = cursor.fetchone()
            conn.commit()
            if not updated:
                return None
            logger.info(f"后端日志：定时任务认领 task_id={task_id}, "
                        f"occurrence_key={occurrence_key}, advance_to={advance_to}")
            return dict(updated)

    @staticmethod
    def submit_manual(task_id: str, *, occurrence_key: str,
                      prompt_augmentations: Optional[list] = None) -> Optional[Dict[str, Any]]:
        """写入 manual pending 槽（"立即执行一次"）。

        与 claim_due 同构：锁行校验（active + 无 pending）后在事务内从**当前
        任务行**固化快照，但不推进 next_due_at——手动执行是额外一次真实执行，
        recurring 不改节奏、once 不消费 run_at 额度；occurrence_key = uuid，
        由调用方生成。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM scheduled_tasks
                WHERE task_id = %s
                  AND status = 'active'
                  AND pending_occurrence_key IS NULL
                FOR UPDATE
            """, (task_id,))
            row = cursor.fetchone()
            if not row:
                return None
            task = dict(row)
            snapshot = {
                "text": task["task_prompt"],
                "attachments": task.get("attachments") or [],
                "profile_id": task["profile_id"],
                "instance_id": task.get("instance_id"),
                "prompt_augmentations": prompt_augmentations or [],
            }
            cursor.execute("""
                UPDATE scheduled_tasks SET
                    pending_occurrence_key = %s,
                    pending_trigger = 'manual',
                    pending_retry_at = now(),
                    pending_snapshot = %s::jsonb,
                    updated_at = now()
                WHERE task_id = %s
                RETURNING *
            """, (occurrence_key, json.dumps(snapshot, ensure_ascii=False), task_id))
            updated = cursor.fetchone()
            conn.commit()
            if not updated:
                return None
            logger.info(f"后端日志：定时任务手动执行入队 task_id={task_id}, "
                        f"occurrence_key={occurrence_key}")
            return dict(updated)

    @staticmethod
    def clear_pending(task_id: str, occurrence_key: str) -> bool:
        """清除 pending 槽（提交成功 202 / 确定性拒绝暂停路径）。

        必须带 occurrence_key 条件：迟到响应不得清掉他人刚写入的新 pending。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE scheduled_tasks SET
                    pending_occurrence_key = NULL,
                    pending_trigger = NULL,
                    pending_retry_at = NULL,
                    pending_snapshot = NULL,
                    updated_at = now()
                WHERE task_id = %s AND pending_occurrence_key = %s
            """, (task_id, occurrence_key))
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def backoff_pending(task_id: str, occurrence_key: str,
                        retry_seconds: int) -> bool:
        """提交重试退避（5xx/网络未知：不当作确定未接单，按间隔重提）。

        同样带 occurrence_key 条件，仅延长本次 pending 的重试时间。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE scheduled_tasks SET
                    pending_retry_at = now() + %s * interval '1 second',
                    updated_at = now()
                WHERE task_id = %s AND pending_occurrence_key = %s
            """, (retry_seconds, task_id, occurrence_key))
            conn.commit()
            return cursor.rowcount > 0

    # ==================== 生命周期操作 ====================

    @staticmethod
    def pause(task_id: str, *, tenant_id: Optional[str] = None,
              user_id: Optional[str] = None,
              pause_reason: Optional[str] = None) -> bool:
        """用户主动暂停任务：置 paused + pause_reason + 清空 pending 槽。

        - 仅 active 可暂停（条件进 UPDATE）：completed/expired 终态不得经
          暂停/恢复流程重新激活；
        - tenant_id/user_id 可选：传入时归属条件进 UPDATE（对象级权限防 TOCTOU）；
        - 已受理的 runner 继续执行到终态（persisted 重校验不要求 active）。
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()
            conditions = ["task_id = %s", "status = 'active'"]
            params: List[Any] = [pause_reason, task_id]
            if tenant_id is not None:
                conditions.append("tenant_id = %s")
                params.append(tenant_id)
            if user_id is not None:
                conditions.append("user_id = %s")
                params.append(user_id)
            cursor.execute(f"""
                UPDATE scheduled_tasks SET
                    status = 'paused',
                    pause_reason = %s,
                    pending_occurrence_key = NULL,
                    pending_trigger = NULL,
                    pending_retry_at = NULL,
                    pending_snapshot = NULL,
                    updated_at = now()
                WHERE {' AND '.join(conditions)}
            """, tuple(params))
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def pause_for_occurrence(task_id: str, occurrence_key: str,
                             pause_reason: str) -> bool:
        """提交结果触发的自动暂停（设计 §4.1 拒绝映射：402/403/422/409 等）。

        与用户主动暂停区分：带 occurrence_key + active 条件——仅当该 occurrence
        仍是当前 pending 时才暂停并清空。迟到的提交响应（用户已暂停→恢复→产生
        新 pending）既不得覆盖新 pending/新状态，也不得再发布新的暂停通知
        （返回 False 由调用方跳过通知）。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE scheduled_tasks SET
                    status = 'paused',
                    pause_reason = %s,
                    pending_occurrence_key = NULL,
                    pending_trigger = NULL,
                    pending_retry_at = NULL,
                    pending_snapshot = NULL,
                    updated_at = now()
                WHERE task_id = %s
                  AND status = 'active'
                  AND pending_occurrence_key = %s
            """, (pause_reason, task_id, occurrence_key))
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def resume(task_id: str, *, next_due_at, tenant_id: Optional[str] = None,
               user_id: Optional[str] = None) -> bool:
        """恢复任务：置 active + 清 pause_reason + 重算 next_due_at。

        仅 paused 可恢复（条件进 UPDATE）：completed/expired 终态不可复活，
        避免 once 已消费额度的任务经 pause→resume 再次进入调度。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            conditions = ["task_id = %s", "status = 'paused'"]
            params: List[Any] = [next_due_at, task_id]
            if tenant_id is not None:
                conditions.append("tenant_id = %s")
                params.append(tenant_id)
            if user_id is not None:
                conditions.append("user_id = %s")
                params.append(user_id)
            cursor.execute(f"""
                UPDATE scheduled_tasks SET
                    status = 'active',
                    pause_reason = NULL,
                    next_due_at = %s,
                    updated_at = now()
                WHERE {' AND '.join(conditions)}
            """, tuple(params))
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def update_schedule(task_id: str, *, schedule_type: str,
                        cron_expression: Optional[str],
                        interval_seconds: Optional[int], run_at,
                        next_due_at, tenant_id: Optional[str] = None,
                        user_id: Optional[str] = None) -> bool:
        """改期专用：仅更新调度字段（schedule_type/cron/interval/run_at/next_due_at）。

        编辑与调度解耦：任务内容编辑走 update_definition，不触碰调度字段。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            conditions = ["task_id = %s"]
            params: List[Any] = [schedule_type, cron_expression, interval_seconds,
                                 run_at, next_due_at, task_id]
            if tenant_id is not None:
                conditions.append("tenant_id = %s")
                params.append(tenant_id)
            if user_id is not None:
                conditions.append("user_id = %s")
                params.append(user_id)
            cursor.execute(f"""
                UPDATE scheduled_tasks SET
                    schedule_type = %s,
                    cron_expression = %s,
                    interval_seconds = %s,
                    run_at = %s,
                    next_due_at = %s,
                    updated_at = now()
                WHERE {' AND '.join(conditions)}
            """, tuple(params))
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def update_definition(task_id: str, *, name: Optional[str] = None,
                          task_prompt: Optional[str] = None,
                          attachments: Optional[list] = None,
                          notification_target_id: Optional[str] = None,
                          tenant_id: Optional[str] = None,
                          user_id: Optional[str] = None) -> bool:
        """编辑任务内容（name/task_prompt/attachments/notification_target_id 可选字段）。

        只更新显式传入的字段，不触碰任何调度字段（编辑不影响已认领执行的
        快照，也不重算 next_due_at）。"""
        set_clauses = []
        params: List[Any] = []
        if name is not None:
            set_clauses.append("name = %s")
            params.append(name)
        if task_prompt is not None:
            set_clauses.append("task_prompt = %s")
            params.append(task_prompt)
        if attachments is not None:
            set_clauses.append("attachments = %s::jsonb")
            params.append(json.dumps(attachments))
        if notification_target_id is not None:
            set_clauses.append("notification_target_id = %s")
            params.append(notification_target_id)
        if not set_clauses:
            return False
        set_clauses.append("updated_at = now()")
        conditions = ["task_id = %s"]
        params.append(task_id)
        if tenant_id is not None:
            conditions.append("tenant_id = %s")
            params.append(tenant_id)
        if user_id is not None:
            conditions.append("user_id = %s")
            params.append(user_id)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                UPDATE scheduled_tasks SET {', '.join(set_clauses)}
                WHERE {' AND '.join(conditions)}
            """, tuple(params))
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def mark_once_expired(task_id: str) -> bool:
        """once 任务过期作废（run_at 超过宽限仍未执行，认领时判定）"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE scheduled_tasks SET status = 'expired', updated_at = now()
                WHERE task_id = %s AND status = 'active'
            """, (task_id,))
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def mark_completed(task_id: str) -> bool:
        """显式置 completed（正常由 record_run_result 的 once 消费规则触发）"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE scheduled_tasks SET status = 'completed', updated_at = now()
                WHERE task_id = %s AND status = 'active'
            """, (task_id,))
            conn.commit()
            return cursor.rowcount > 0

    # ==================== 执行结果回写 ====================

    @staticmethod
    def _record_run_result_in_tx(cursor, task_id: str, *, run_status: str,
                                 trigger: str, error_code: Optional[str] = None,
                                 finished_at=None) -> bool:
        """统计回写（事务内版本，Phase 3 runner 终态事务复用）。

        规则（设计 §5.4）：
        - run_status 直接使用 Runner 终态（completed/failed/cancelled）：
          completed 计成功、failed 计失败、cancelled 两者皆不计（仅 total_runs
          与 last_status 呈现，取消不是失败也不是成功）；
        - last_run_at/last_status/total_runs 回写；
        - 仅 trigger=='scheduled' 且 schedule_type=='once' 置 completed
          （manual 终态只计统计，不消费 once 额度）；
        - recurring 且 error_code ∈ {CREDIT_BLOCKED, PROFILE_FORBIDDEN} 时置
          paused + 对应 pause_reason（once 额度已消费，失败经 last_status 呈现）。
        """
        pause_reason = _PAUSE_REASON_BY_ERROR_CODE.get(error_code or "")
        finished_at = finished_at or datetime.now()
        cursor.execute("""
            UPDATE scheduled_tasks SET
                total_runs = total_runs + 1,
                success_count = success_count + CASE WHEN %s = 'completed' THEN 1 ELSE 0 END,
                fail_count = fail_count + CASE WHEN %s = 'failed' THEN 1 ELSE 0 END,
                last_run_at = %s,
                last_status = %s,
                status = CASE
                    WHEN %s = 'scheduled' AND schedule_type = 'once' THEN 'completed'
                    WHEN schedule_type <> 'once' AND %s IN ('CREDIT_BLOCKED', 'PROFILE_FORBIDDEN')
                        THEN 'paused'
                    ELSE status END,
                pause_reason = CASE
                    WHEN schedule_type <> 'once' AND %s IN ('CREDIT_BLOCKED', 'PROFILE_FORBIDDEN')
                        THEN %s
                    ELSE pause_reason END,
                updated_at = now()
            WHERE task_id = %s
        """, (run_status, run_status, finished_at, run_status, trigger,
              error_code, error_code, pause_reason, task_id))
        return cursor.rowcount > 0

    @staticmethod
    def record_run_result(task_id: str, *, run_status: str, trigger: str,
                          error_code: Optional[str] = None,
                          finished_at=None) -> bool:
        """执行结果统计回写（连接版，包装事务内版本）"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            ok = ScheduledTaskDB._record_run_result_in_tx(
                cursor, task_id, run_status=run_status, trigger=trigger,
                error_code=error_code, finished_at=finished_at)
            conn.commit()
            if ok:
                logger.info(f"后端日志：定时任务执行回写 task_id={task_id}, "
                            f"run_status={run_status}, trigger={trigger}, "
                            f"error_code={error_code}")
            return ok

    # ==================== 删除（事务守卫） ====================

    @staticmethod
    def delete_with_session(task_id: str, *, tenant_id: str,
                            user_id: str) -> Optional[Dict[str, Any]]:
        """硬删除任务 + 任务会话 + 会话消息（单事务，设计 §4.3）。

        守卫顺序：锁任务行（FOR UPDATE）→ 查任务会话非终态 runner → 有则抛
        ScheduledTaskBusyError（409）；检查与删除同事务，接单事务持同一行锁
        串行化，无"检查时无 runner、删除后才接单"窗口。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM scheduled_tasks
                WHERE task_id = %s
                  AND tenant_id IS NOT DISTINCT FROM %s
                  AND user_id = %s
                FOR UPDATE
            """, (task_id, tenant_id, user_id))
            row = cursor.fetchone()
            if not row:
                return None
            task = dict(row)
            session_id = task["session_id"]

            cursor.execute("""
                SELECT runner_id FROM agent_runners
                WHERE session_id = %s
                  AND status NOT IN ('completed', 'failed', 'cancelled')
                LIMIT 1
            """, (session_id,))
            if cursor.fetchone():
                raise ScheduledTaskBusyError("任务正在执行，稍后再试")

            cursor.execute("DELETE FROM chat_messages WHERE session_id = %s", (session_id,))
            cursor.execute("DELETE FROM chat_sessions WHERE session_id = %s", (session_id,))
            cursor.execute("DELETE FROM scheduled_tasks WHERE task_id = %s", (task_id,))
            conn.commit()
            logger.info(f"后端日志：定时任务已删除 task_id={task_id}, "
                        f"session_id={session_id}")
            return task
