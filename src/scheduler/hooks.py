"""scheduled runner 终态统计回写（设计 §5.4）。

挂在 event_repository.append_in_tx 的 terminal 分支（与通知生产者同一挂点）：
单事务内按任务会话回写 scheduled_tasks 统计/状态。失败抛 CheckpointFailure
使终态事务整体回滚——不出现"计了费没统计"。
"""

from loguru import logger

from src.core.agent_engine.contracts import CheckpointFailure

# 执行侧错误码 → 自动暂停原因（仅 recurring；once 额度已消费不经此路径）。
_PAUSE_REASON_BY_ERROR_CODE = {
    "CREDIT_BLOCKED": "credit_blocked",
    "PROFILE_FORBIDDEN": "profile_forbidden",
}


def _scheduled_task_in_tx(cursor, runner):
    """按 runner 会话唯一定位任务行（scheduled_tasks.session_id UNIQUE）。"""
    cursor.execute("""SELECT task_id,tenant_id,user_id,name,schedule_type,status,
        notification_target_id FROM scheduled_tasks WHERE session_id=%s""",
                   (runner["session_id"],))
    return cursor.fetchone()


def record_scheduled_run_in_tx(cursor, runner) -> None:
    """runner 终态事务尾调用：统计回写 + once 收尾 + recurring 自动暂停。

    - run_status 直接使用 Runner 终态（completed/failed/cancelled）；
    - trigger 取 input.request_data.scheduled（提交契约保证存在）；
    - once 仅 trigger='scheduled' 的终态消费额度（manual 只计统计）；
    - recurring + CREDIT_BLOCKED/PROFILE_FORBIDDEN → paused + reason；
    - 失败抛 CheckpointFailure 整体回滚终态事务。
    """
    from src.scheduler.db import ScheduledTaskDB

    if (runner.get("source") != "scheduled"
            or runner.get("session_kind") != "web"):
        return
    task = _scheduled_task_in_tx(cursor, runner)
    if task is None:
        # 任务已删除（删除守卫保证执行中不可删；此处为防御路径）：执行事实
        # 与计费保留，无任务行可回写。
        logger.warning("后端日志：scheduled 终态回写未找到任务行（任务已删除？） "
                       "runner_id={}, session_id={}", runner["runner_id"],
                       runner["session_id"])
        return
    scheduled_meta = ((runner.get("input") or {}).get("request_data") or {}).get("scheduled") or {}
    trigger = scheduled_meta.get("trigger") or "scheduled"
    error_code = ((runner.get("result") or {}).get("error_code")) or None
    try:
        recorded = ScheduledTaskDB._record_run_result_in_tx(
            cursor, task["task_id"], run_status=runner["status"], trigger=trigger,
            error_code=error_code, finished_at=runner.get("finished_at"))
    except Exception as error:
        raise CheckpointFailure("SCHEDULED_RUN_RECORD_FAILED") from error
    if not recorded:
        # 任务行刚在本事务内确认存在，零行更新只可能是绑定/条件错误——
        # 静默忽略会让终态事务提交但统计/once 消费/自动暂停全部丢失
        # （评审复现：参数错位时 UPDATE WHERE 命中 0 行仍被视为成功）。
        raise CheckpointFailure("SCHEDULED_RUN_RECORD_MISSED")
