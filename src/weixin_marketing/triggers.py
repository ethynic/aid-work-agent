"""微信触发配置 → P1-A schedules 冻结 spec 编译（once/interval/calendar/event）

复用底座 schedules 的 next-fire 计算能力（interval 锚点网格 / APScheduler CronTrigger /
mon..sun 星期），本模块只做配置合法性校验与 spec 冻结；宽限/miss 策略从 revision
冻结配置读取（trigger_json），运行期不再解析用户输入。
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from src.desktop_automation import schedules as da_schedules
from src.weixin_marketing.config import WeixinMarketingConfig, get_weixin_marketing_config
from src.weixin_marketing.constants import BLOCK_KIND_IMAGE
from src.weixin_marketing.models import (
    CalendarTriggerConfig,
    EventTriggerConfig,
    IntervalTriggerConfig,
    OnceTriggerConfig,
    TriggerConfig,
    parse_trigger,
)


class TriggerConfigError(ValueError):
    """触发配置非法（服务层 422 语义）"""


def normalize_cron_expr(cron_expr: str) -> str:
    """五段 cron 的 day 段 ``L``/``l``（月末）归一为 APScheduler 的 ``last``。

    归一后的形态直接冻结进 schedule 行（底座 build_cron_trigger 原样可解析）。
    """
    parts = cron_expr.split()
    if len(parts) == 5 and parts[2] in ("L", "l"):
        parts[2] = "last"
    return " ".join(parts)


def validate_blocks(blocks: List[Dict[str, Any]], config: Optional[WeixinMarketingConfig] = None) -> None:
    """块数量/类型门禁：max_blocks 上限；image 块须 images_enabled（P4 前默认关闭）"""
    config = config or get_weixin_marketing_config()
    if not blocks:
        raise TriggerConfigError("内容块不能为空")
    if len(blocks) > config.max_blocks:
        raise TriggerConfigError(f"内容块数量超过上限 {config.max_blocks}")
    for block in blocks:
        if block.get("kind") == BLOCK_KIND_IMAGE and not config.images_enabled:
            raise TriggerConfigError("图片内容未启用（images_enabled=false），不允许 image 块")


def compile_trigger_specs(
    trigger: TriggerConfig,
    *,
    config: Optional[WeixinMarketingConfig] = None,
) -> List[Dict[str, Any]]:
    """触发配置 → 底座 schedule specs（发布事务内冻结）。

    once → 单条 one_shot time spec；interval → anchor 网格；calendar → cron 字段式
    （五段 cron_expr 或 day_of_week=mon..sun 字段集）；event → 事件订阅行（不进时间扫描）。
    非法配置（缺字段/无 next fire/间隔低于频率上限）抛 TriggerConfigError。
    """
    config = config or get_weixin_marketing_config()
    if isinstance(trigger, OnceTriggerConfig):
        if not config.time_triggers_enabled:
            raise TriggerConfigError("时间触发未启用（time_triggers_enabled=false）")
        spec = {
            "kind": "time",
            "trigger_key": "time",
            "anchor_at": trigger.run_at,
            "one_shot": True,
            "grace_seconds": trigger.grace_seconds,
            "miss_policy": "skip_overlap",
            "timezone": trigger.timezone,
        }
        _assert_initial_fire(spec)
        return [spec]
    if isinstance(trigger, IntervalTriggerConfig):
        if not config.time_triggers_enabled:
            raise TriggerConfigError("时间触发未启用（time_triggers_enabled=false）")
        if trigger.interval_seconds < config.min_interval_seconds:
            raise TriggerConfigError(
                f"触发间隔低于频率上限（最小 {config.min_interval_seconds}s，"
                f"实际 {trigger.interval_seconds}s）"
            )
        if trigger.ends_at is not None and trigger.ends_at <= trigger.start_at:
            raise TriggerConfigError("ends_at 必须晚于 start_at")
        spec = {
            "kind": "time",
            "trigger_key": "time",
            "anchor_at": trigger.start_at,
            "interval_seconds": trigger.interval_seconds,
            "grace_seconds": trigger.grace_seconds,
            "miss_policy": trigger.miss_policy,
            "ends_at": trigger.ends_at,
            "max_count": trigger.max_count,
            "timezone": trigger.timezone,
        }
        _assert_initial_fire(spec)
        return [spec]
    if isinstance(trigger, CalendarTriggerConfig):
        if not config.time_triggers_enabled:
            raise TriggerConfigError("时间触发未启用（time_triggers_enabled=false）")
        fields = {
            "year": trigger.year, "month": trigger.month, "day": trigger.day,
            "week": trigger.week, "day_of_week": trigger.day_of_week,
            "hour": trigger.hour, "minute": trigger.minute, "second": trigger.second,
        }
        if not trigger.cron_expr and not any(v is not None for v in fields.values()):
            raise TriggerConfigError("calendar 触发缺少 cron_expr 或字段式配置")
        spec: Dict[str, Any] = {
            "kind": "time",
            "trigger_key": "time",
            "grace_seconds": trigger.grace_seconds,
            "miss_policy": trigger.miss_policy,
            "ends_at": trigger.ends_at,
            "max_count": trigger.max_count,
            "timezone": trigger.timezone,
        }
        if trigger.cron_expr:
            spec["cron_expr"] = normalize_cron_expr(trigger.cron_expr)
        else:
            spec.update({k: v for k, v in fields.items() if v is not None})
        _assert_initial_fire(spec)
        return [spec]
    if isinstance(trigger, EventTriggerConfig):
        if not config.event_triggers_enabled:
            raise TriggerConfigError("事件触发未启用（event_triggers_enabled=false）")
        return [
            {
                "kind": "event",
                "source_ref": trigger.source_ref,
                "event_type": trigger.event_type or "*",
                "delay_seconds": trigger.delay_seconds,
            }
        ]
    raise TriggerConfigError(f"未知触发类型: {trigger!r}")


def _assert_initial_fire(spec: Dict[str, Any]) -> None:
    """建行前校验 spec 至少存在一个 next fire（缺 anchor/cron 无解即拒绝发布）"""
    try:
        next_fire = da_schedules.initial_next_fire_at(spec)
    except Exception as e:  # noqa: BLE001 底座 ValueError 统一转场景校验错误
        raise TriggerConfigError(f"触发配置非法: {e}") from e
    if next_fire is None:
        raise TriggerConfigError("触发配置无可用触发时刻")


def preview_next_fires(
    trigger: TriggerConfig,
    *,
    count: int = 5,
    now: Optional[datetime] = None,
) -> List[datetime]:
    """未来 n 次触发预览（validate 端点静态预检；无发送副作用）。

    once → 至多 1 个（已过期则空）；interval → anchor 网格逐槽；
    calendar → CronTrigger 逐槽；event → 空列表（不进时间扫描）。
    """
    now = now or datetime.now(timezone.utc)
    specs = compile_trigger_specs(trigger, config=_permissive_time_config())
    fires: List[datetime] = []
    for spec in specs:
        if spec.get("kind") != "time":
            continue
        cursor_time = da_schedules.ensure_utc(now)
        first = True
        for _ in range(count):
            if spec.get("interval_seconds"):
                slot = da_schedules.next_slot_on_or_after(
                    da_schedules.ensure_utc(spec["anchor_at"]), spec["interval_seconds"], cursor_time
                )
            elif spec.get("one_shot"):
                slot = da_schedules.ensure_utc(spec["anchor_at"])
                if slot < cursor_time:
                    break
            else:
                slot = da_schedules.next_cron_fire(spec, cursor_time, strictly_after=not first)
                if slot is None:
                    break
            if slot is None or slot <= (fires[-1] if fires else cursor_time - timedelta(seconds=1)):
                break
            fires.append(slot)
            # 后续槽严格晚于已预览槽（interval 网格/cron 均以 >= 语义取槽）
            cursor_time = slot + timedelta(seconds=1)
            first = False
        if len(fires) >= count:
            break
    return fires[:count]


def _permissive_time_config() -> WeixinMarketingConfig:
    """预览用宽松配置：不因开关关闭/频率上限拒绝静态预览（validate 端点语义）"""
    from dataclasses import replace

    base = get_weixin_marketing_config()
    return replace(
        base, time_triggers_enabled=True, event_triggers_enabled=True, min_interval_seconds=1
    )


def spec_from_stored_trigger(trigger_json: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """revision 冻结 trigger_json → specs（运行路径：发布/执行共用同一冻结配置）"""
    if not trigger_json:
        return []
    return compile_trigger_specs(parse_trigger(dict(trigger_json)))
