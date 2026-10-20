"""防滥用定时扫描任务（Phase 1 观察闭环驱动，设计 §5.2）。

每轮：chat_records 聚合窗口内有消耗的用户 -> 解析生效绑定 -> 评估
credit_budget -> 命中阈值写 abuse_events（1h 同键去重）+ 租户管理员与平台管理员
通知（发布前 24h 节流预检 + publish_in_tx 幂等键，IM/邮箱外发由既有 dispatcher
轮询完成）。

Phase 1 不接执行链拦截点；warn 延迟为分钟级（扫描间隔），观察场景可接受。
"""
import asyncio
from datetime import datetime, timedelta
from typing import Any, Dict, List

from loguru import logger

from src.config.settings import settings
from src.db.database import get_db_connection
from src.saas.models.enums import UserStatus
from src.notifications.contracts import Subject
from src.notifications.events import publish_in_tx
from src.notifications.service import notification_repository
from .rules import SESSION_SOURCE_TYPES
from .service import AntiAbuseService

PRODUCER = "abuse_scanner"
# 同一 (tenant, user, rule) 24h 内只通知一次：发布前按 event_key 前缀预检未过期
# 通知实现节流；publish_in_tx 唯一约束只防同 event_key 重复行（TTL 不参与判重）
NOTIFY_TTL_SECONDS = 86400
NOTIFY_CATEGORY = "abuse"


# users.source -> 通知中的用户类型前缀（未映射/空 = 内部用户）
SOURCE_TYPE_LABELS = {
    "wecom_kf": "微信用户",
    "wecom": "企微用户",
    "wecom_personal_rpa": "企微用户",
    "dingtalk": "钉钉用户",
    "feishu": "飞书用户",
}

_PERIOD_INTERVALS = {"hour": "1 hour", "day": "1 day"}


def _subject_label(user_row: Dict[str, Any], user_id: str) -> str:
    prefix = SOURCE_TYPE_LABELS.get(user_row.get("source") or "", "内部用户")
    for key in ("nickname", "username", "phone"):
        value = user_row.get(key)
        if value:
            return f"{prefix} {str(value)[:120]}"[:128]
    return f"{prefix} {user_id[:120]}"[:128]


def _tenant_name(cursor, tenant_id: str) -> str:
    cursor.execute("SELECT company_name FROM tenants WHERE tenant_id = %s", (tenant_id,))
    row = cursor.fetchone()
    return (row["company_name"] or tenant_id)[:128] if row else tenant_id[:128]


def _scan_candidates(cursor) -> List[Dict[str, Any]]:
    """最大窗口（day）内有会话类消耗的 (tenant_id, user_id) 聚合"""
    cursor.execute(
        """SELECT tenant_id, user_id, SUM(credit_cost) AS total
           FROM chat_records
           WHERE created_at >= NOW() - interval '1 day'
             AND credit_cost > 0 AND user_id IS NOT NULL AND tenant_id IS NOT NULL
             AND source_type = ANY(%s)
           GROUP BY tenant_id, user_id""",
        (list(SESSION_SOURCE_TYPES),),
    )
    return [dict(row) for row in cursor.fetchall()]


def _load_users(cursor, user_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    if not user_ids:
        return {}
    cursor.execute(
        """SELECT user_id, username, nickname, phone, source, status
           FROM users WHERE user_id = ANY(%s)""",
        (user_ids,),
    )
    return {row["user_id"]: dict(row) for row in cursor.fetchall()}


def _publish_admin_notifications(
    cursor,
    tenant_id: str,
    tenant_name: str,
    admins: List[str],
    platform_admins: List[str],
    *,
    event_id: int,
    user_id: str,
    subject_label: str,
    rule_name: str,
    window_credits: float,
    threshold_credits: float,
    observe_mode: bool,
    period: str = "day",
) -> int:
    """租户管理员 + 平台管理员逐人发布 abuse.rule_triggered（幂等键含 user，24h TTL）。

    发布前按 (scope_key, recipient) 预检同前缀未过期通知，实现「同一
    (tenant, user, rule) 24h 内只通知一次」；hourly 分桶的 event_key 仅作
    唯一约束行隔离，不承担通知节流（expires_at 不参与 ON CONFLICT 判重）。

    租户管理员按租户 scope 投递（走租户级外发 target）；平台管理员无租户属性，
    按 global scope 投递（仅站内信/个人 web_push，不触发租户级 IM 外发）。
    """
    cursor.execute("SELECT clock_timestamp() AS now")
    now = cursor.fetchone()["now"]
    # 24h 通知节流预检：同前缀（abuse:{tenant}:{user}:credit_budget:）且未过期的
    # 既有通知 = 该告警 24h 内已通知过。LIKE 通配符转义，防止租户/用户 ID 含 % _。
    key_prefix = f"abuse:{tenant_id}:{user_id}:credit_budget:"
    like_pattern = "".join(
        "\\" + ch if ch in ("%", "_", "\\") else ch for ch in key_prefix
    ) + "%"
    cursor.execute(
        """SELECT scope_key, recipient_user_id FROM notification_messages
           WHERE producer = %s AND event_key LIKE %s AND expires_at > %s""",
        (PRODUCER, like_pattern, now),
    )
    throttled = {(r["scope_key"], r["recipient_user_id"]) for r in cursor.fetchall()}
    # event_key 按小时分桶：唯一约束行在 retention 清理前长期存在，
    # 不分桶会让同键后续告警命中旧行被静默吞掉（与 abuse_events 的 1h 去重对齐）
    event_key = f"abuse:{tenant_id}:{user_id}:credit_budget:{now:%Y%m%d%H}"
    window_hours = 1 if period == "hour" else 24
    window_start = (now - timedelta(hours=window_hours)).strftime("%Y-%m-%d %H:%M")
    base_parameters = {
        "tenant_id": tenant_id,
        "tenant_name": tenant_name,
        "rule_name": rule_name[:128],
        "subject_label": subject_label,
        "user_id": user_id,
        "period": period if period in _PERIOD_INTERVALS else "day",
        "window_start": window_start,
        "window_end": now.strftime("%Y-%m-%d %H:%M"),
        "window_credits": round(window_credits, 2),
        "threshold_credits": round(threshold_credits, 2),
        "observe_mode": observe_mode,
    }
    published = 0
    # (admin_id, tenant scope, parameters)；平台管理员 tenant_id=None -> scope 'global'，
    # 站内信可达；个人 web_push 外发需 category='abuse' 偏好行（当前偏好仅开放 task，暂不可达）。
    # 平台管理员无租户上下文，通知中保留租户名；租户管理员即当前租户，不显示
    for admin_id, admin_tenant, parameters in (
        [(a, tenant_id, base_parameters) for a in admins]
        + [(a, None, {**base_parameters, "show_tenant": True}) for a in platform_admins]
    ):
        subject = Subject(user_id=admin_id, tenant_id=admin_tenant)
        if (subject.scope_key, admin_id) in throttled:
            continue  # 24h 内已通知过该接收者，跳过
        # 平台副本无租户 scope，用 about_tenant 标注告警所属租户：
        # 平台管理员代管租户时 inbox 按此隔离，只见本租户相关的 global 副本
        resource_ref = {"type": "abuse_event", "id": str(event_id)}
        if admin_tenant is None:
            resource_ref["about_tenant"] = tenant_id
        ref = publish_in_tx(
            cursor,
            subject=subject,
            producer=PRODUCER,
            event_type="abuse.rule_triggered",
            event_key=event_key,
            resource_ref=resource_ref,
            parameters=parameters,
            occurred_at=now,
            accepted_at=now,
            expires_at=now + timedelta(seconds=NOTIFY_TTL_SECONDS),
            category=NOTIFY_CATEGORY,
            priority=0,
            policy_key="tenant_admin_event",
        )
        if ref:
            published += 1
    return published


def run_abuse_scan_once(repository=None) -> Dict[str, int]:
    """执行一轮扫描。同步函数，调用方负责线程化（APScheduler 回调线程）。"""
    if repository is None:
        repository = notification_repository()
    stats = {"scanned": 0, "events": 0, "notified": 0}
    aa_config = settings.anti_abuse
    if not aa_config.scanner_enabled:
        return stats
    notify_enabled = bool(repository.config.tenant_events_enabled)

    with get_db_connection() as cursor:
        candidates = _scan_candidates(cursor)
        if not candidates:
            return stats
        users = _load_users(cursor, [c["user_id"] for c in candidates])
        tenants = sorted({c["tenant_id"] for c in candidates})
        cursor.execute(
            """SELECT user_id FROM users
               WHERE role = 'platform_admin' AND status = %s""",
            (UserStatus.ACTIVE.value,),
        )
        platform_admins = [row["user_id"] for row in cursor.fetchall()]
        for tenant_id in tenants:
            try:
                cursor.execute(
                    """SELECT user_id FROM users
                       WHERE tenant_id = %s AND role = 'tenant_admin' AND status = %s""",
                    (tenant_id, UserStatus.ACTIVE.value),
                )
                admins = [row["user_id"] for row in cursor.fetchall()]
                tenant_name = _tenant_name(cursor, tenant_id)
                for candidate in candidates:
                    if candidate["tenant_id"] != tenant_id:
                        continue
                    user_id = candidate["user_id"]
                    user_row = users.get(user_id)
                    # 已停用用户跳过（处置已生效，残余窗口消耗不再告警）；
                    # users 表无此 user（未映射外部用户）也跳过——预算无法按用户累计
                    if not user_row or user_row.get("status") != UserStatus.ACTIVE.value:
                        continue
                    binding = AntiAbuseService.resolve_binding(tenant_id, user_id,
                                                               source=user_row.get("source"))
                    budget = next(
                        (p for p in (binding.get("policies") or [])
                         if p.get("rule_id") == "credit_budget"),
                        None,
                    )
                    if not budget:
                        continue
                    params = budget.get("params") or {}
                    period = params.get("period", "day")
                    warn_threshold = params.get("warn_threshold")
                    hard_threshold = params.get("hard_threshold")
                    if hard_threshold is None and warn_threshold is None:
                        continue
                    actual = AntiAbuseService.evaluate_credit_budget(tenant_id, user_id, period)
                    stats["scanned"] += 1
                    hit_hard = hard_threshold is not None and actual >= float(hard_threshold)
                    hit_warn = warn_threshold is not None and actual >= float(warn_threshold)
                    if not (hit_hard or hit_warn):
                        continue
                    mode = binding.get("mode", "observe")
                    # Phase 1 全观察：任一阈值命中记 warned 事件（detail 标注 level）
                    event_id = AntiAbuseService.record_event(
                        cursor,
                        tenant_id=tenant_id,
                        scope_type=binding.get("scope_type", "tenant"),
                        scope_id=binding.get("scope_id", tenant_id),
                        rule_id="credit_budget",
                        action="warned",
                        user_id=user_id,
                        channel_type=user_row.get("source"),
                        detail={
                            "period": period,
                            "warn_threshold": warn_threshold,
                            "hard_threshold": hard_threshold,
                            "actual_credits": round(actual, 2),
                            "level": "hard" if hit_hard else "warn",
                            "mode": mode,
                        },
                    )
                    if event_id is None:
                        continue  # 1h 内已有同键事件，跳过重复通知
                    pending_events = 1
                    pending_notified = 0
                    if notify_enabled and (admins or platform_admins):
                        pending_notified = _publish_admin_notifications(
                            cursor,
                            tenant_id,
                            tenant_name,
                            admins,
                            platform_admins,
                            event_id=event_id,
                            user_id=user_id,
                            subject_label=_subject_label(user_row, user_id),
                            rule_name="时间窗积分预算",
                            window_credits=actual,
                            threshold_credits=float(
                                hard_threshold if hit_hard else warn_threshold
                            ),
                            observe_mode=(mode == "observe"),
                            period=period,
                        )
                    # 计数在 commit 成功后累加：publish 异常 rollback 时事件也随之回滚，不虚计
                    cursor.commit()
                    stats["events"] += pending_events
                    stats["notified"] += pending_notified
                # 无事件命中的租户也无需提交（只读查询）
            except Exception:
                cursor.rollback()
                logger.opt(exception=True).error(
                    f"后端日志：防滥用扫描租户 {tenant_id} 评估失败")
    if stats["events"] or stats["notified"]:
        logger.info(f"后端日志：防滥用扫描完成 {stats}")
    return stats


class AntiAbuseScanScheduler:
    """挂 background_runner 生命周期入口的占位说明——实际注册在
    ScheduledTaskManager._register_system_jobs（APScheduler IntervalTrigger），
    此类仅保留异步包装供手动触发/测试使用。"""

    @staticmethod
    async def run_once() -> Dict[str, int]:
        return await asyncio.to_thread(run_abuse_scan_once)
