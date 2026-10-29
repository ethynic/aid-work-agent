"""task.paused 通知生产者（调度侧，设计 §8.1）。

pause 状态变更与通知写账本**同一事务**（评审修正：分两事务时进程在两步
之间退出或通知写失败，任务已暂停且无重新生产路径——站内信同样依赖该账本
记录，不存在兜底）。实际外发复用通知系统的 expand/claim/dispatch 异步重试，
此处只保证账本行与状态同生共死。
"""

from datetime import timedelta

from loguru import logger

from src.db.database import get_db_connection


def _event_key(task_id: str, pause_reason: str, *, occurrence_key=None,
               attempt: int = None, runner_id: str = None) -> str:
    """幂等键：occurrence（提交拒绝）优先；恢复耗尽用 attempt 标识（含
    runner_id——停车事件的幂等单位是 (runner, attempt)，用户恢复任务后重新
    认领的新 runner 首次中断 attempt 可能与上一 runner 相同，缺 runner_id 会
    被 ON CONFLICT 误去重吞掉第二次暂停通知）；用户手动语义兜底 'manual'
    ——ON CONFLICT (scope_key,recipient,producer,event_key) 去重，同原因同
    发生的重复发布不重复投递。"""
    if occurrence_key:
        tail = occurrence_key
    elif attempt is not None:
        tail = (f"attempt:{runner_id}:{attempt}" if runner_id
                else f"attempt:{attempt}")
    else:
        tail = "manual"
    return f"paused:{task_id}:{pause_reason}:{tail}"


def publish_task_paused_in_tx(cursor, task, reason_label, *, occurrence_key=None,
                              attempt=None, runner_id=None, pause_reason=None):
    """事务内版本：调用方持有 pause 状态变更的同一事务，commit 由调用方负责。

    - task：任务行 dict（含 task_id/tenant_id/user_id/name/notification_target_id）；
    - notify_target_id 事件生成时快照（设计 §3.3）：从任务行读取存入参数，
      任务后续改目标不影响已生成事件；
    - 接收人 = 任务属主 Subject(user_id, tenant_id)。
    """
    from src.config.settings import settings
    from src.notifications.contracts import Subject
    from src.notifications.events import publish_in_tx

    pause_reason = pause_reason or task.get("pause_reason") or "paused"
    subject = Subject(task["user_id"], task.get("tenant_id"))
    parameters = {
        "task_name": str(task.get("name") or "")[:80] or task["task_id"],
        "reason_label": str(reason_label or "")[:120] or "任务已暂停",
        "task_id": task["task_id"],
    }
    target_id = task.get("notification_target_id")
    if target_id:
        parameters["notify_target_id"] = str(target_id)[:128]
    cursor.execute("SELECT clock_timestamp() AS now")
    now = cursor.fetchone()["now"]
    ref = publish_in_tx(
        cursor, subject=subject, producer="scheduler", event_type="task.paused",
        event_key=_event_key(task["task_id"], pause_reason,
                             occurrence_key=occurrence_key, attempt=attempt,
                             runner_id=runner_id),
        resource_ref={"type": "scheduled_task", "id": task["task_id"]},
        parameters=parameters, occurred_at=now, accepted_at=now,
        expires_at=now + timedelta(seconds=settings.notifications.ttl_seconds))
    logger.info(f"后端日志：task.paused 通知已入账 task_id={task['task_id']}, "
                f"pause_reason={pause_reason}, ref={ref}")
    return ref


def publish_task_paused(task, reason_label, **kwargs):
    """独立事务便捷入口（无状态变更需要绑定时使用）。"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        ref = publish_task_paused_in_tx(cursor, task, reason_label, **kwargs)
        conn.commit()
        return ref
