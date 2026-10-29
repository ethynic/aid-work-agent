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


# ==================== 任务调度配置（创建/改期/恢复共用，Phase 4） ====================
# 评审要求：共享调度纯函数放入 scheduler.schedule，工具与 API 共用领域规则，
# 避免 API 反向依赖工具实现。

_VALID_SCHEDULE_TYPES = {"daily", "weekly", "monthly", "interval", "once"}

# weekly 的 cron day_of_week 映射——APScheduler CronTrigger.from_crontab 的
# 编号是 0=周一 … 6=周日（与标准 Unix cron 的 0=周日不同）
_WEEKDAY_CRON_MAP = {"mon": "0", "tue": "1", "wed": "2", "thu": "3",
                     "fri": "4", "sat": "5", "sun": "6"}
_WEEKDAY_NAMES = {"mon": "周一", "tue": "周二", "wed": "周三", "thu": "周四",
                  "fri": "周五", "sat": "周六", "sun": "周日"}


class ScheduleConfigError(ValueError):
    """调度配置不合法（用户可见 message，工具/API 层转为失败回执）"""


def _coerce_int_time_fields(time_config):
    """数值时间字段 str→int 容错（原位修改；仅接受整数或整数字符串）。

    转换失败（缺失值/非数字串/非整数浮点如 9.5）直接抛 ScheduleConfigError——
    时间必填不猜测，不容许默认值兜底（评审修正：hour=None/'abc' 曾静默
    回退 09:00）。"""
    for key in ("hour", "minute", "day"):
        if key not in time_config or time_config[key] is None or isinstance(time_config[key], bool):
            continue  # 缺失交由 _require 统一拒绝
        value = time_config[key]
        try:
            if isinstance(value, float) and not value.is_integer():
                raise ValueError
            time_config[key] = int(value)
        except (TypeError, ValueError):
            raise ScheduleConfigError(
                f"时间字段 {key} 的值不合法: {value!r}（须为整数），"
                "请向用户确认具体时间") from None


def parse_run_at(value):
    """解析一次性任务执行时间（"YYYY-MM-DD HH:MM:SS"，北京时间），不可解析返回 None"""
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _require(time_config, key, schedule_type):
    """必填时间字段缺失即拒绝：时间必填不猜测，引导先向用户确认具体时间"""
    value = time_config.get(key)
    if value is None:
        raise ScheduleConfigError(
            f"缺少 {schedule_type} 类型的时间配置字段 {key}，"
            "必须先向用户确认具体时间后再创建")
    return value


def generate_cron_expression(schedule_type, time_config):
    """根据调度类型和时间配置生成 cron 表达式（once/interval 返回 None）。

    缺少必填时间字段/取值越界抛 ScheduleConfigError。"""
    if schedule_type == "daily":
        hour, minute = (_require(time_config, "hour", "daily"),
                        _require(time_config, "minute", "daily"))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ScheduleConfigError("daily 的 hour/minute 取值越界（hour 0-23，minute 0-59）")
        return f"{minute} {hour} * * *"
    if schedule_type == "weekly":
        day_of_week = _require(time_config, "day_of_week", "weekly")
        hour, minute = (_require(time_config, "hour", "weekly"),
                        _require(time_config, "minute", "weekly"))
        cron_day = _WEEKDAY_CRON_MAP.get(str(day_of_week).lower())
        if cron_day is None:
            raise ScheduleConfigError(
                f"weekly 的 day_of_week 非法: {day_of_week}（可选 mon/tue/wed/thu/fri/sat/sun）")
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ScheduleConfigError("weekly 的 hour/minute 取值越界（hour 0-23，minute 0-59）")
        return f"{minute} {hour} * * {cron_day}"
    if schedule_type == "monthly":
        day = _require(time_config, "day", "monthly")
        hour, minute = (_require(time_config, "hour", "monthly"),
                        _require(time_config, "minute", "monthly"))
        if not (1 <= day <= 31):
            raise ScheduleConfigError("monthly 的 day 取值越界（1-31）")
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ScheduleConfigError("monthly 的 hour/minute 取值越界（hour 0-23，minute 0-59）")
        return f"{minute} {hour} {day} * *"
    return None


def build_schedule_fields(schedule_type, time_config, now=None):
    """完整校验并生成调度字段与首次到期时间（创建/改期共用）。

    返回 (cron_expression, interval_seconds, run_at, next_due_at)：
    - once：run_at 归一化字符串，next_due_at = run_at（datetime）；
    - interval：next_due_at = now + interval_seconds（首锚点；float 小时
      支持，非有限/非正数拒绝——评审修正：NaN/inf 曾抛未转换异常）；
    - daily/weekly/monthly：cron 分支 now 之后严格下一次（+1µs 精确边界）。
    """
    from math import isfinite
    now = now or datetime.now()
    if schedule_type not in _VALID_SCHEDULE_TYPES:
        raise ScheduleConfigError(
            f"不支持的调度类型: {schedule_type}，可选值：daily（每天）/ weekly（每周）/ "
            "monthly（每月）/ interval（间隔）/ once（一次性）")
    if not time_config:
        raise ScheduleConfigError("缺少时间配置（time_config），必须先向用户确认具体时间")
    _coerce_int_time_fields(time_config)

    if schedule_type == "once":
        run_at_dt = parse_run_at(time_config.get("run_at"))
        if run_at_dt is None:
            raise ScheduleConfigError(
                "一次性任务缺少有效执行时间：time_config.run_at 必填，"
                "格式 YYYY-MM-DD HH:MM:SS（北京时间），请向用户确认具体时间")
        if run_at_dt <= now:
            raise ScheduleConfigError(
                f"执行时间 {run_at_dt.strftime('%Y-%m-%d %H:%M:%S')} 已经过去，"
                "无法创建一次性任务，请向用户确认新的未来时间")
        return None, None, run_at_dt.strftime("%Y-%m-%d %H:%M:%S"), run_at_dt

    if schedule_type == "interval":
        interval_hours = _require(time_config, "interval_hours", "interval")
        try:
            hours = float(interval_hours)
        except (TypeError, ValueError):
            raise ScheduleConfigError(
                "interval 的 interval_hours 必须为数字（小时），"
                "请向用户确认具体间隔") from None
        if not (isfinite(hours) and hours > 0):
            raise ScheduleConfigError("interval 的 interval_hours 必须为正数（有限，单位小时）")
        interval_seconds = int(round(hours * 3600))
        if interval_seconds <= 0:
            raise ScheduleConfigError("interval 的 interval_hours 必须为正数（小时）")
        return None, interval_seconds, None, now + timedelta(seconds=interval_seconds)

    cron_expression = generate_cron_expression(schedule_type, time_config)
    try:
        next_due = compute_next_due({"schedule_type": schedule_type,
                                     "cron_expression": cron_expression,
                                     "next_due_at": None}, now)
    except ValueError:
        raise ScheduleConfigError("调度配置不可解析，请向用户确认具体时间") from None
    return cron_expression, None, None, next_due


def compute_resume_due(task, now=None):
    """恢复任务时按**当前任务行**重算 next_due_at（设计 §4.3：不补跑停用期间）。

    返回 (next_due_at, will_expire_once)：
    - cron：now 之后下一次；interval：now 为新锚点；once：保持 run_at，
      超过执行宽限时 will_expire_once=True（统一转换：resume 事务内置 expired）。
    在 resume_recompute 事务内对锁定行调用（评审修正：入口各自先算后写会
    覆盖并发改期）。"""
    now = now or datetime.now()
    schedule_type = task.get("schedule_type")
    if schedule_type == "interval":
        seconds = task.get("interval_seconds") or 0
        if seconds <= 0:
            raise ScheduleConfigError("任务的 interval_seconds 非法，无法恢复，请重建任务")
        return now + timedelta(seconds=seconds), False
    if schedule_type == "once":
        run_at = task.get("run_at") or task.get("next_due_at")
        return run_at, once_expired(task, now)
    next_due = compute_next_due(task, now)
    return next_due, False


def format_schedule_description(schedule_type, time_config):
    """生成调度描述文本（创建回执/列表摘要展示用）"""
    if not time_config:
        return schedule_type
    if schedule_type == "daily":
        return f"每天 {time_config.get('hour', 9):02d}:{time_config.get('minute', 0):02d}"
    if schedule_type == "weekly":
        day = _WEEKDAY_NAMES.get(str(time_config.get("day_of_week", "mon")).lower(), "周一")
        return f"每{day} {time_config.get('hour', 9):02d}:{time_config.get('minute', 0):02d}"
    if schedule_type == "monthly":
        return f"每月{time_config.get('day', 1)}日 {time_config.get('hour', 9):02d}:{time_config.get('minute', 0):02d}"
    if schedule_type == "interval":
        return f"每隔 {time_config.get('interval_hours', 1)} 小时"
    if schedule_type == "once":
        return f"一次性执行 {time_config.get('run_at', '未指定')}"
    return schedule_type


def validate_task_attachments(attachments):
    """校验任务附件引用——直接复用 Runner AttachmentRef 契约（评审修正：
    自维护近似规则会漂移：size=-1 曾可保存、size='abc' 曾抛未转换
    ValueError 致 500、file_id 超长曾被静默截断改变资源标识）。

    返回归一化列表（AttachmentRef.model_dump）；非法项转换为
    ScheduleConfigError（用户可见），不截断、不静默修复。"""
    if attachments is None:
        return []
    if not isinstance(attachments, list):
        raise ScheduleConfigError("attachments 必须为数组（受权资源引用列表）")
    from src.services.agent_runner.contracts import AttachmentRef
    normalized = []
    for item in attachments:
        if not isinstance(item, dict):
            raise ScheduleConfigError("附件项必须为对象（含 file_id/name/type）")
        try:
            ref = AttachmentRef(**{k: v for k, v in item.items()
                                   if k in ("file_id", "name", "type",
                                            "mime_type", "content", "size")})
        except Exception as error:
            raise ScheduleConfigError(
                f"附件引用不合法：{error}") from None
        normalized.append(ref.model_dump())
    return normalized


def max_tasks_per_user() -> int:
    """每用户 active 任务上限（现行产品配置，默认 5；创建与恢复共用）"""
    try:
        from src.config.settings import settings
        value = int(settings.scheduler.max_tasks_per_user)
        return value if value > 0 else 5
    except Exception:
        return 5
