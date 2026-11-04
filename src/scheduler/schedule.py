"""调度推进计算（纯函数，无 DB/进程状态）。

认领事务（db.claim_due）与 Phase 3 的 dispatch 循环共用：occurrence、下次
执行时间、执行快照必须来自同一份锁定的任务定义（设计 §4.1/§4.2）——
- cron：now 之后的下一次触发（停机错过合并为一次，按 now 后节奏继续，
  不逐 tick 追赶补跑）；
- interval：原锚点（本次消费的 next_due_at）+ k×interval 中大于 now 的
  最小值（相位不漂移，手动执行不改节奏）；
- once：无下次（None），过期判定见 once_expired（20261026-1530 修复语义：
  超过宽限的 once 不执行直接作废）。

时间窗（设计 §15.2）：循环任务可选 start_at/ends_at（naive 本地时间），
窗口判定 start_at <= 计划触发点 < ends_at（结束边界不含，避免 00:00 属于
两段）。compute_next_due 推进结果出窗（>= ends_at）时返回 None——无后续
计划点，认领侧保留 active 至该次执行终态再置 completed；手动执行不受窗口
限制。旧行两列为 NULL 表示无限期循环，行为不变。

时间约定：任务行时间为 naive 的本地时间（Asia/Shanghai），cron 计算经
ZoneInfo 转换后还原为 naive。
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# Phase 3 接 configs scheduler.timezone；当前与旧 manager 一致固定上海时区
TIMEZONE = "Asia/Shanghai"

# once 过期宽限（run_at 已过仍超过该窗口 → 认领时作废不执行）
ONCE_EXPIRY_GRACE = timedelta(minutes=10)

# interval 最短间隔硬下限（小时）：配置只能调高不能突破（设计 §15.2.5）
MIN_INTERVAL_HOURS_FLOOR = 1


def _window_next_due(advance, task):
    """推进结果按结束边界截断：出窗（>= ends_at）返回 None（无后续计划点）。"""
    ends_at = task.get("ends_at")
    if ends_at is not None and advance >= ends_at:
        return None
    return advance


def compute_next_due(task: dict, now: datetime = None):
    """按任务的当前调度定义计算认领后的下一次执行时间。

    - task 为 scheduled_tasks 行（dict）：schedule_type / cron_expression /
      interval_seconds / next_due_at（本次消费的 due，interval 的节奏锚点）/
      start_at / ends_at（可选时间窗）；
    - now 缺省取本地当前时间；返回 naive 本地时间；once 返回 None；
      窗口耗尽（推进结果 >= ends_at）同样返回 None；
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
        return _window_next_due(advance, task)
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
    advance = aware.astimezone(tz).replace(tzinfo=None)
    start_at = task.get("start_at")
    if start_at is not None and advance < start_at:
        # 窗口起点在未来：取窗内首个匹配点（起点恰好命中时允许该点，
        # -1µs 基准与 now 推进同技巧）；已过去的点不补跑由 now 基准保证
        aware = trigger.get_next_fire_time(
            None, start_at.replace(tzinfo=tz) - timedelta(microseconds=1))
        if aware is None:
            raise ValueError("INVALID_CRON_SCHEDULE")
        advance = aware.astimezone(tz).replace(tzinfo=None)
    return _window_next_due(advance, task)


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


def _parse_window_bounds(schedule_type, time_config):
    """解析并校验可选时间窗 start_at/ends_at（设计 §15.2.1/15.2.2）。

    返回 (start_at, ends_at) naive datetime（未填为 None）：
    - 仅循环类型接受窗口字段，once 一律拒绝（其 run_at 自带单次语义）；
    - 两端都填须 start_at < ends_at；格式与 run_at 一致
      （YYYY-MM-DD HH:MM:SS，北京时间），不可解析直接拒绝不猜测。
    """
    start_raw = time_config.get("start_at")
    ends_raw = time_config.get("ends_at")
    if schedule_type == "once" and (start_raw or ends_raw):
        raise ScheduleConfigError(
            "一次性任务（once）不支持 start_at/ends_at 时间窗，"
            "请只提供 run_at（未来具体时间）")
    start_at = ends_at = None
    if start_raw:
        start_at = parse_run_at(start_raw)
        if start_at is None:
            raise ScheduleConfigError(
                "start_at 格式不合法：须为 YYYY-MM-DD HH:MM:SS（北京时间），"
                "请向用户确认具体起止时间")
    if ends_raw:
        ends_at = parse_run_at(ends_raw)
        if ends_at is None:
            raise ScheduleConfigError(
                "ends_at 格式不合法：须为 YYYY-MM-DD HH:MM:SS（北京时间），"
                "请向用户确认具体起止时间")
    if start_at is not None and ends_at is not None and start_at >= ends_at:
        raise ScheduleConfigError(
            f"时间窗无效：start_at（{start_at.strftime('%Y-%m-%d %H:%M:%S')}）必须早于 "
            f"ends_at（{ends_at.strftime('%Y-%m-%d %H:%M:%S')}）")
    return start_at, ends_at


def _coerce_interval_hours(interval_hours):
    """interval_hours → 整数小时（设计 §15.2.5：只接受整数小时，数字或整数字符串；
    拒绝布尔、小数、分钟/秒等输入），非法抛 ScheduleConfigError。"""
    if isinstance(interval_hours, bool):
        raise ScheduleConfigError(
            "interval 的 interval_hours 必须为整数小时（如 1、2），不接受布尔值")
    if isinstance(interval_hours, float):
        if not interval_hours.is_integer():
            raise ScheduleConfigError(
                "interval 只支持整数小时（1、2、3……），不支持小数（如 1.5）或分钟/秒；"
                "请向用户确认具体间隔")
        return int(interval_hours)
    if isinstance(interval_hours, int):
        return interval_hours
    if isinstance(interval_hours, str):
        stripped = interval_hours.strip()
        try:
            return int(stripped)
        except ValueError:
            raise ScheduleConfigError(
                "interval 只支持整数小时（数字或整数字符串，如 2 或 \"2\"），"
                f"不接受: {interval_hours!r}") from None
    raise ScheduleConfigError(
        "interval 的 interval_hours 必须为整数小时，请向用户确认具体间隔")


def build_schedule_fields(schedule_type, time_config, now=None):
    """完整校验并生成调度字段与首次到期时间（创建/改期共用）。

    返回 (cron_expression, interval_seconds, run_at, next_due_at, start_at, ends_at)：
    - once：run_at 归一化字符串，next_due_at = run_at（datetime），无时间窗；
    - interval：interval_hours 只接受 >= 配置阈值（且 >=1 硬下限）的整数小时；
      设未来 start_at 时首点 = 开始时刻，否则首点 = 当前时刻（创建/改期即生效）；
    - daily/weekly/monthly：cron 分支 now 之后严格下一次（+1µs 精确边界），
      未来 start_at 时取窗内首个匹配点（起点恰好命中允许该点）；
    - 时间窗：循环类型可选 start_at/ends_at，必须从当前时间起、在窗内至少
      存在一个触发点（否则拒绝）；过去的点不补跑。
    """
    now = now or datetime.now()
    if schedule_type not in _VALID_SCHEDULE_TYPES:
        raise ScheduleConfigError(
            f"不支持的调度类型: {schedule_type}，可选值：daily（每天）/ weekly（每周）/ "
            "monthly（每月）/ interval（间隔）/ once（一次性）")
    if not time_config:
        raise ScheduleConfigError("缺少时间配置（time_config），必须先向用户确认具体时间")
    _coerce_int_time_fields(time_config)
    start_at, ends_at = _parse_window_bounds(schedule_type, time_config)

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
        return None, None, run_at_dt.strftime("%Y-%m-%d %H:%M:%S"), run_at_dt, None, None

    if schedule_type == "interval":
        hours = _coerce_interval_hours(_require(time_config, "interval_hours", "interval"))
        min_hours = min_interval_hours()
        if hours < MIN_INTERVAL_HOURS_FLOOR or hours < min_hours:
            raise ScheduleConfigError(
                f"interval 只支持整数小时，且不能低于当前最短间隔 {min_hours} 小时"
                "（硬下限 1 小时）；请向用户确认具体间隔")
        interval_seconds = hours * 3600
        # 开始时刻首次触发：未来 start_at 即首点；否则创建/改期时刻即首点
        #（start_at 已过去时同当前时间起算，不补跑过去的点）
        next_due = start_at if (start_at is not None and start_at > now) else now
        if ends_at is not None and next_due >= ends_at:
            raise ScheduleConfigError(
                "时间窗内没有可执行的触发点：首个触发点（"
                f"{next_due.strftime('%Y-%m-%d %H:%M:%S')}）已不在 ends_at 之前，"
                "请调整起止时间")
        return None, interval_seconds, None, next_due, start_at, ends_at

    cron_expression = generate_cron_expression(schedule_type, time_config)
    try:
        next_due = compute_next_due({"schedule_type": schedule_type,
                                     "cron_expression": cron_expression,
                                     "next_due_at": None,
                                     "start_at": start_at, "ends_at": ends_at}, now)
    except ValueError:
        raise ScheduleConfigError("调度配置不可解析，请向用户确认具体时间") from None
    if next_due is None:
        raise ScheduleConfigError(
            "时间窗内没有可执行的触发点：从当前时间起到 ends_at 之间不存在"
            "该调度规则的触发点，请调整起止时间或调度规则")
    return cron_expression, None, None, next_due, start_at, ends_at


def compute_resume_due(task, now=None):
    """恢复任务时按**当前任务行**重算 next_due_at（设计 §4.3/§15.2.3）。

    返回 (next_due_at, will_expire_once, window_finished)：
    - interval：恢复时刻为新首点（恰在恢复点重启周期，不补跑停用期间）；
      未来 start_at 在恢复时刻之后时取 start_at 为首点（窗口判定
      start_at <= 触发点对 interval 同样成立，CR P2-4）；
    - cron：now 之后下一次（未来 start_at 时取窗内首个匹配点）；
    - once：保持 run_at，超过执行宽限时 will_expire_once=True（统一转换：
      resume 事务内置 expired）；
    - 时间窗已结束（无窗内后续点）：window_finished=True，next_due_at=None
      ——resume_recompute 统一置 completed，不补跑（设计 §15.2.4）。
    在 resume_recompute 事务内对锁定行调用（评审修正：入口各自先算后写会
    覆盖并发改期）。"""
    now = now or datetime.now()
    schedule_type = task.get("schedule_type")
    if schedule_type == "interval":
        seconds = task.get("interval_seconds") or 0
        if seconds <= 0:
            raise ScheduleConfigError("任务的 interval_seconds 非法，无法恢复，请重建任务")
        ends_at = task.get("ends_at")
        if ends_at is not None and now >= ends_at:
            return None, False, True
        start_at = task.get("start_at")
        next_due = start_at if (start_at is not None and start_at > now) else now
        return next_due, False, False
    if schedule_type == "once":
        run_at = task.get("run_at") or task.get("next_due_at")
        return run_at, once_expired(task, now), False
    next_due = compute_next_due(task, now)
    if next_due is None:
        return None, False, True
    return next_due, False, False


def format_schedule_description(schedule_type, time_config):
    """生成调度描述文本（创建回执/列表摘要展示用）"""
    if not time_config:
        return schedule_type
    window = ""
    if schedule_type != "once":
        parts = []
        if time_config.get("start_at"):
            parts.append(f"{time_config['start_at']} 起")
        if time_config.get("ends_at"):
            parts.append(f"至 {time_config['ends_at']}")
        if parts:
            window = f"（{'，'.join(parts)}）"
    if schedule_type == "daily":
        return (f"每天 {time_config.get('hour', 9):02d}:{time_config.get('minute', 0):02d}"
                + window)
    if schedule_type == "weekly":
        day = _WEEKDAY_NAMES.get(str(time_config.get('day_of_week', 'mon')).lower(), "周一")
        return (f"每{day} {time_config.get('hour', 9):02d}:{time_config.get('minute', 0):02d}"
                + window)
    if schedule_type == "monthly":
        return (f"每月{time_config.get('day', 1)}日 "
                f"{time_config.get('hour', 9):02d}:{time_config.get('minute', 0):02d}" + window)
    if schedule_type == "interval":
        return f"每隔 {time_config.get('interval_hours', 1)} 小时" + window
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


def max_tasks_per_user(tenant_id: str = None) -> int:
    """每用户 active 任务上限的有效值（设计 §15.2.6，创建与恢复共用）。

    优先读取租户覆盖值 tenants.settings.scheduler.max_tasks_per_user
    （只接受正整数，非法/缺失回落），未设置时取 configs/config.yaml 的
    scheduler.max_tasks_per_user（默认 5）。tenant 覆盖值读取失败时回落
    配置默认值并记录日志——不因设置读取失败中断创建/恢复主流程。"""
    default = 5
    try:
        from src.config.settings import settings
        value = int(settings.scheduler.max_tasks_per_user)
        if value > 0:
            default = value
    except Exception:
        pass
    if not tenant_id:
        return default
    try:
        import json as _json
        from src.db.database import get_db_connection
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT settings FROM tenants WHERE tenant_id = %s", (tenant_id,))
            row = cursor.fetchone()
        settings_json = row["settings"] if row else None
        if isinstance(settings_json, str):
            settings_json = _json.loads(settings_json or "{}")
        override = ((settings_json or {}).get("scheduler") or {}).get("max_tasks_per_user")
        if isinstance(override, bool) or not isinstance(override, int) or override <= 0:
            return default
        return override
    except Exception:
        # 覆盖值读取失败按配置默认值处理（不中断创建/恢复主流程；租户覆盖值
        # 高于或低于默认均可能，回落值以更保守的全局默认为准）
        from loguru import logger
        logger.warning(f"后端日志：读取租户任务上限覆盖值失败，使用配置默认值 {default}")
        return default


def min_interval_hours() -> int:
    """interval 最短间隔（小时，设计 §15.2.5）：配置只能调高，硬下限 1。

    configs/config.yaml scheduler.min_interval_hours 只接受 >=1 的整数；
    缺失、非法（小数/布尔/小于 1）一律回落 1，不放大也不放宽。"""
    try:
        from src.config.settings import settings
        value = getattr(getattr(settings, "scheduler", None),
                        "min_interval_hours", MIN_INTERVAL_HOURS_FLOOR)
        if isinstance(value, bool) or not isinstance(value, int) \
                or value < MIN_INTERVAL_HOURS_FLOOR:
            return MIN_INTERVAL_HOURS_FLOOR
        return value
    except Exception:
        return MIN_INTERVAL_HOURS_FLOOR
