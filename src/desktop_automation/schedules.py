"""desktop_automation schedules（时间/事件订阅行与时间槽计算）

- interval 第 n 次 = anchor + n×interval_seconds；停机后直接算最近可用槽，
  不循环展开积压（【计划 §3.1】）。
- cron 使用项目 APScheduler 3.x CronTrigger 的计算能力；星期用 mon..sun 字符串。
- 事件订阅行（kind=event）保存 source_ref/event_type/condition_ref/delay，
  不进入时间扫描。
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from apscheduler.triggers.cron import CronTrigger
from loguru import logger

from src.db.database import get_db_connection
from src.desktop_automation.constants import (
    SCHEDULE_KIND_EVENT,
    SCHEDULE_KIND_TIME,
)

_SCHEDULE_COLUMNS = """
    id, tenant_id, scenario_key, task_ref, revision_ref, user_id, kind, trigger_key,
    timezone, anchor_at, interval_seconds, cron_expr, day_of_week, next_fire_at, ends_at,
    max_count, run_count, grace_seconds, miss_policy, one_shot, consumed,
    source_ref, event_type, condition_ref, delay_seconds, status, created_at, updated_at
"""


def build_cron_trigger(
    spec: Dict[str, Any],
) -> CronTrigger:
    """按冻结 spec 构建 CronTrigger（cron_expr 五段或字段式含 mon..sun day_of_week）"""
    tz = spec.get("timezone") or "UTC"
    if spec.get("cron_expr"):
        return CronTrigger.from_crontab(spec["cron_expr"], timezone=tz)
    fields = {}
    for key in ("year", "month", "day", "week", "day_of_week", "hour", "minute", "second"):
        if spec.get(key) is not None:
            fields[key] = spec[key]
    if not fields:
        raise ValueError("cron schedule 缺少 cron_expr 或字段式配置")
    return CronTrigger(timezone=tz, **fields)


def default_trigger_key(kind: str, spec: Dict[str, Any]) -> str:
    """schedule 行 trigger_key（revision 内局部触发标识，R4）：
    time → 'time'（每 revision 一条时间 schedule）；event → event:{source_ref}:{event_type}
    """
    if kind == SCHEDULE_KIND_TIME:
        return "time"
    if kind == SCHEDULE_KIND_EVENT:
        source_ref = spec.get("source_ref")
        event_type = spec.get("event_type") or "*"
        if not source_ref:
            raise ValueError("event schedule 缺少 source_ref")
        return f"event:{source_ref}:{event_type}"
    raise ValueError(f"未知 schedule kind: {kind}")


def initial_next_fire_at(spec: Dict[str, Any]) -> Optional[datetime]:
    """建行时的首个 next_fire_at：interval→anchor；cron→now 起首个槽；event→None（不进时间扫描）"""
    kind = spec.get("kind", SCHEDULE_KIND_TIME)
    if kind == SCHEDULE_KIND_EVENT:
        return None
    if spec.get("interval_seconds"):
        anchor = spec.get("anchor_at")
        if anchor is None:
            raise ValueError("interval schedule 缺少 anchor_at")
        return ensure_utc(anchor)
    if spec.get("cron_expr") or spec.get("day_of_week") is not None:
        trigger = build_cron_trigger(spec)
        nxt = trigger.get_next_fire_time(None, datetime.now(timezone.utc))
        if nxt is None:
            raise ValueError("cron schedule 无可用触发时刻（ends_at/起止配置非法）")
        return ensure_utc(nxt)
    # 一次性无重复：anchor 即唯一槽
    anchor = spec.get("anchor_at")
    if anchor is None:
        raise ValueError("time schedule 缺少 anchor_at/interval_seconds/cron_expr")
    return ensure_utc(anchor)


def ensure_utc(dt: datetime) -> datetime:
    """naive 按 UTC 解释（测试假时钟统一 UTC；生产 DB TIMESTAMPTZ 返回 aware）"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ==================== interval 槽计算（纯函数，假时钟可测） ====================


def next_slot_on_or_after(anchor: datetime, interval_seconds: int, at: datetime) -> datetime:
    """最近可用槽：第一个 >= at 的网格槽（停机重启不积压，直接算最近槽）"""
    anchor, at = ensure_utc(anchor), ensure_utc(at)
    if at <= anchor:
        return anchor
    delta = (at - anchor).total_seconds()
    n = -(-delta // interval_seconds)  # ceil
    return anchor + timedelta(seconds=n * interval_seconds)


def next_slot_strictly_after(anchor: datetime, interval_seconds: int, slot: datetime) -> datetime:
    """接纳某槽后的下一个槽（严格 > slot）"""
    nxt = next_slot_on_or_after(anchor, interval_seconds, slot + timedelta(seconds=1))
    return nxt


def latest_slot_at_or_before(
    anchor: datetime, interval_seconds: int, at: datetime
) -> Optional[datetime]:
    """<= at 的最大网格槽（at 早于 anchor 时 None）——「宽限内最近一次」的候选"""
    anchor, at = ensure_utc(anchor), ensure_utc(at)
    if at < anchor:
        return None
    delta = (at - anchor).total_seconds()
    n = int(delta // interval_seconds)
    return anchor + timedelta(seconds=n * interval_seconds)


def slots_between(anchor: datetime, interval_seconds: int, first: datetime, last: datetime) -> int:
    """[first, last) 区间内网格槽数（迟到 missed 计数：first 起到被接纳的最近槽之前；
    两端须为网格槽，latest==first 时为 0）"""
    seconds = (ensure_utc(last) - ensure_utc(first)).total_seconds()
    return int(seconds // interval_seconds)


def next_cron_fire(
    spec: Dict[str, Any], previous_or_now: datetime, *, strictly_after: bool = False
) -> Optional[datetime]:
    """cron 下一槽：strictly_after=False 返回 >= 时刻的首槽（重启补位），
    True 返回 > 时刻的下一槽（接纳后前移）。ends_at 之外返回 None。"""
    trigger = build_cron_trigger(spec)
    at = ensure_utc(previous_or_now)
    nxt = trigger.get_next_fire_time(at if strictly_after else None, at)
    if nxt is None:
        return None
    nxt = ensure_utc(nxt)
    ends_at = spec.get("ends_at")
    if ends_at is not None and nxt > ensure_utc(ends_at):
        return None
    return nxt


# ==================== 查询 ====================


def get_schedule(schedule_id: str, tenant_id: str) -> Optional[Dict[str, Any]]:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT {_SCHEDULE_COLUMNS} FROM desktop_automation_schedules "
            "WHERE id = %s AND tenant_id = %s",
            (schedule_id, tenant_id),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def list_schedules(
    tenant_id: str,
    scenario_key: Optional[str] = None,
    task_ref: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """按租户（+可选 scenario/task）列 schedule（对账/测试；必带 tenant_id）"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        sql = f"SELECT {_SCHEDULE_COLUMNS} FROM desktop_automation_schedules WHERE tenant_id = %s"
        params: List[Any] = [tenant_id]
        if scenario_key:
            sql += " AND scenario_key = %s"
            params.append(scenario_key)
        if task_ref:
            sql += " AND task_ref = %s"
            params.append(task_ref)
        sql += " ORDER BY created_at"
        cursor.execute(sql, tuple(params))
        return [dict(r) for r in cursor.fetchall()]


def find_due_candidates(
    now: datetime,
    limit: int = 100,
    *,
    scenario_key: Optional[str] = None,
    tenant_allowlist: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """时间扫描候选（非锁定读取，只作扫描线索；接纳在 accept_time_slot 短事务内，
    锁顺序 subject(task)→schedule→occurrence/run，R12）。

    scenario_key / tenant_allowlist（R53 过滤下推）：SQL WHERE 内过滤（LIMIT 之前），
    白名单外的 due schedule 不占用 LIMIT 配额——允许任务当轮必被扫到（无饿死）。
    tenant_allowlist=None 不过滤；空列表语义（不限制）由调用方归一为 None 后传入。
    """
    filters = [
        "kind = 'time'",
        "status = 'active'",
        "consumed = FALSE",
        "next_fire_at IS NOT NULL",
        "next_fire_at <= %s",
    ]
    params: List[Any] = [ensure_utc(now)]
    if scenario_key is not None:
        filters.append("scenario_key = %s")
        params.append(scenario_key)
    if tenant_allowlist is not None:
        filters.append("tenant_id = ANY(%s)")
        params.append(list(tenant_allowlist))
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT id, tenant_id, scenario_key, task_ref, revision_ref, next_fire_at
            FROM desktop_automation_schedules
            WHERE {" AND ".join(filters)}
            ORDER BY next_fire_at
            LIMIT %s
            """,
            (*params, limit),
        )
        return [dict(r) for r in cursor.fetchall()]


def lock_schedule(cursor, schedule_id: str, tenant_id: str) -> Optional[Dict[str, Any]]:
    """锁定 schedule 行（R12 锁顺序第二环，阻塞 FOR UPDATE）"""
    cursor.execute(
        f"SELECT {_SCHEDULE_COLUMNS} FROM desktop_automation_schedules "
        "WHERE id = %s AND tenant_id = %s FOR UPDATE",
        (schedule_id, tenant_id),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def mark_schedule_finished(cursor, schedule_id: str, tenant_id: str, reason: str) -> None:
    cursor.execute(
        """
        UPDATE desktop_automation_schedules
        SET status = 'finished', updated_at = NOW()
        WHERE id = %s AND tenant_id = %s AND status = 'active'
        """,
        (schedule_id, tenant_id),
    )
    logger.info(f"后端日志：desktop_automation schedule 终止 id={schedule_id} reason={reason}")
