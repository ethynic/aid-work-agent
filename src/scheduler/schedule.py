"""调度推进计算（纯函数，无 DB/进程状态）。

认领事务（db.claim_due）与 Phase 3 的 dispatch 循环共用：occurrence、下次
执行时间、执行快照必须来自同一份锁定的任务定义（设计 §4.1/§4.2）——
- cron：now 之后的下一次触发（停机错过合并为一次，按 now 后节奏继续，
  不逐 tick 追赶补跑）；
- interval：原锚点（本次消费的 next_due_at）+ k×interval 中大于 now 的
  最小值（相位不漂移，手动执行不改节奏）；
- once：无下次（None），过期判定见 once_expired（20261026-1530 修复语义：
  超过宽限的 once 不执行直接作废）。

时间约定：任务行时间为 naive 的本地时间（Asia/Shanghai），cron 计算经
ZoneInfo 转换后还原为 naive。
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# Phase 3 接 configs scheduler.timezone；当前与旧 manager 一致固定上海时区
TIMEZONE = "Asia/Shanghai"

# once 过期宽限（run_at 已过仍超过该窗口 → 认领时作废不执行）
ONCE_EXPIRY_GRACE = timedelta(minutes=10)


def compute_next_due(task: dict, now: datetime = None) -> datetime:
    """按任务的当前调度定义计算认领后的下一次执行时间。

    - task 为 scheduled_tasks 行（dict）：schedule_type / cron_expression /
      interval_seconds / next_due_at（本次消费的 due，interval 的节奏锚点）；
    - now 缺省取本地当前时间；返回 naive 本地时间；once 返回 None；
    - 调度数据非法（interval<=0、cron 不可解析）抛 ValueError——认领事务
      中止不消费 occurrence，由调用方记录（创建端校验为正道）。
    """
    now = now or datetime.now()
    schedule_type = task.get("schedule_type")
    if schedule_type == "once":
        return None
    if schedule_type == "interval":
        seconds = task.get("interval_seconds") or 0
        if seconds <= 0:
            raise ValueError("INVALID_INTERVAL_SCHEDULE")
        advance = task["next_due_at"] + timedelta(seconds=seconds)
        while advance <= now:
            advance += timedelta(seconds=seconds)
        return advance
    # daily/weekly/monthly 均编译为 cron 表达式
    from apscheduler.triggers.cron import CronTrigger
    tz = ZoneInfo(TIMEZONE)
    trigger = CronTrigger.from_crontab(task["cron_expression"], timezone=tz)
    # APScheduler 以 now 起算可能返回恰好等于 now 的触发点；推进语义要求
    # 严格大于本次计算时刻（否则 next_due_at 仍到期 → 额外认领/幂等重提）。
    # cron 最小粒度 1 秒，+1µs 基准即保证严格推进
    base = now.replace(tzinfo=tz) + timedelta(microseconds=1)
    aware = trigger.get_next_fire_time(None, base)
    if aware is None:
        raise ValueError("INVALID_CRON_SCHEDULE")
    return aware.astimezone(tz).replace(tzinfo=None)


def once_expired(task: dict, now: datetime = None) -> bool:
    """once 任务是否已超过执行宽限（如长期暂停后恢复，run_at 早已过去）。"""
    if task.get("schedule_type") != "once":
        return False
    now = now or datetime.now()
    run_at = task.get("run_at") or task.get("next_due_at")
    return run_at is not None and now > run_at + ONCE_EXPIRY_GRACE
