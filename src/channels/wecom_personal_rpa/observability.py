"""企业微信个人账号 RPA 指标聚合 + 告警规则（纯逻辑，无 IO）

职责：
- ``build_metrics``：把 db 层返回的原始聚合（审计计数 / outbox 状态 / 客户端存活 /
  账号状态 / 绑定状态）组装成对外的指标 dict。
- ``evaluate_alerts``：基于当前态 + 最近 outbox，按需评估活跃告警。

设计：纯函数，所有数据由调用方（admin 端点）从 db 取好后传入，便于单元测试
（无需 mock 数据库）。时间以 ``datetime`` 传入；``now`` 可注入以便测试确定性。

在线判定：客户端 ``last_seen_at`` 在 ``CLIENT_ONLINE_THRESHOLD_SECONDS`` 秒内视为在线。
该阈值目前为模块常量，后续可提升到 settings。
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

# 客户端在线心跳阈值（秒）。客户端每次回调/WS 帧都会刷新 last_seen_at。
CLIENT_ONLINE_THRESHOLD_SECONDS = 300

# 连续动作失败告警阈值（次）
DEFAULT_FAILURE_THRESHOLD = 3


# ===========================================================================
# 内部工具
# ===========================================================================


def _parse_dt(val: Any) -> Optional[datetime]:
    """容忍 datetime / 字符串 / None。"""
    if not val:
        return None
    if isinstance(val, datetime):
        return val
    try:
        return datetime.strptime(str(val)[:19], "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None


def _is_client_online(last_seen: Any, now: datetime) -> bool:
    ls = _parse_dt(last_seen)
    if ls is None:
        return False
    return (now - ls).total_seconds() <= CLIENT_ONLINE_THRESHOLD_SECONDS


def _fmt_ts(val: Any) -> str:
    dt = _parse_dt(val)
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else "从未"


def _rate(num: int, denom: int) -> float:
    """安全比率，denom=0 时返回 0.0，保留 4 位小数。"""
    if denom <= 0:
        return 0.0
    return round(num / denom, 4)


# ===========================================================================
# 指标
# ===========================================================================


def build_metrics(
    *,
    client_liveness: List[Dict[str, Any]],
    account_states: List[Dict[str, Any]],
    audit_counts: Dict[str, int],
    outcome_counts: Dict[str, int],
    binding_counts: Dict[str, int],
    window_hours: int,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """组装指标 dict。入参均为 db 层原始聚合结果。"""
    now = now or datetime.now()

    active_clients = [c for c in client_liveness if c.get("status") == "active"]
    online_clients = [
        c for c in active_clients if _is_client_online(c.get("last_seen_at"), now)
    ]
    clients_total = len(active_clients)

    accounts_total = len(account_states)
    accounts_by_status: Dict[str, int] = {}
    for a in account_states:
        s = a.get("status") or "unknown"
        accounts_by_status[s] = accounts_by_status.get(s, 0) + 1
    accounts_online = accounts_by_status.get("online", 0)

    succeeded = int(outcome_counts.get("succeeded", 0))
    failed = int(outcome_counts.get("failed", 0))
    actions_denom = succeeded + failed

    return {
        "window_hours": window_hours,
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "clients": {
            "total": clients_total,
            "online": len(online_clients),
            "online_rate": _rate(len(online_clients), clients_total),
        },
        "accounts": {
            "total": accounts_total,
            "online": accounts_online,
            "online_rate": _rate(accounts_online, accounts_total),
            "by_status": accounts_by_status,
        },
        "actions": {
            "by_status": dict(outcome_counts),
            "succeeded": succeeded,
            "failed": failed,
            "success_rate": _rate(succeeded, actions_denom),
        },
        "audit_counts": dict(audit_counts),
        "bindings": {
            "needs_review": int(binding_counts.get("needs_review", 0)),
            "by_status": dict(binding_counts),
        },
    }


# ===========================================================================
# 告警（按需评估）
# ===========================================================================


def evaluate_alerts(
    *,
    client_liveness: List[Dict[str, Any]],
    account_states: List[Dict[str, Any]],
    recent_outcomes: List[Dict[str, Any]],
    binding_counts: Dict[str, int],
    now: Optional[datetime] = None,
    failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
) -> List[Dict[str, Any]]:
    """评估活跃告警，返回 list[{rule, severity, entity_type, entity_id, entity_name, message}]。

    规则：
    1. client_offline（danger）：active 客户端心跳过期或从未上报。
    2. account_not_logged_in（warning）：账号 offline/need_login 且归属客户端在线
       （客户端活着但账号没登，避免整租户未启用场景误报）。
    3. consecutive_action_failures（danger）：某账号最近 outbox 末尾连续 failed ≥ 阈值。
    4. needs_review_backlog（info）：存在待复核绑定。
    """
    now = now or datetime.now()
    alerts: List[Dict[str, Any]] = []

    # 1. 客户端离线 + 收集在线客户端 id（供规则 2 使用）
    live_client_ids = set()
    for c in client_liveness:
        if c.get("status") != "active":
            continue
        cid = c.get("id")
        name = c.get("name") or cid
        if _is_client_online(c.get("last_seen_at"), now):
            if cid:
                live_client_ids.add(cid)
        else:
            alerts.append({
                "rule": "client_offline",
                "severity": "danger",
                "entity_type": "client",
                "entity_id": cid,
                "entity_name": name,
                "message": f"客户端「{name}」未上报心跳（最后活跃：{_fmt_ts(c.get('last_seen_at'))}）",
            })

    # 2. 账号未登录（仅当归属客户端在线）
    for a in account_states:
        status = a.get("status")
        if status in ("offline", "need_login") and a.get("client_id") in live_client_ids:
            aid = a.get("id")
            name = a.get("display_name") or aid
            alerts.append({
                "rule": "account_not_logged_in",
                "severity": "warning",
                "entity_type": "account",
                "entity_id": aid,
                "entity_name": name,
                "message": f"账号「{name}」状态为 {status}（客户端在线但账号未登录）",
            })

    # 3. 连续动作失败（recent_outcomes 按 created_at DESC，按账号分组数末尾 failed）
    grouped: Dict[str, List[str]] = {}
    for o in recent_outcomes:
        aid = o.get("account_id")
        if aid is None:
            continue
        grouped.setdefault(aid, []).append(o.get("status"))
    account_name = {a.get("id"): (a.get("display_name") or a.get("id")) for a in account_states}
    for aid, statuses in grouped.items():
        streak = 0
        for s in statuses:  # 最新在前
            if s == "failed":
                streak += 1
            else:
                break
        if streak >= failure_threshold:
            name = account_name.get(aid, aid)
            alerts.append({
                "rule": "consecutive_action_failures",
                "severity": "danger",
                "entity_type": "account",
                "entity_id": aid,
                "entity_name": name,
                "message": f"账号「{name}」连续 {streak} 次动作失败",
            })

    # 4. 待复核绑定积压
    needs_review = int(binding_counts.get("needs_review", 0))
    if needs_review > 0:
        alerts.append({
            "rule": "needs_review_backlog",
            "severity": "info",
            "entity_type": "binding",
            "entity_id": "",
            "entity_name": "",
            "message": f"有 {needs_review} 个会话绑定待人工复核（needs_review）",
        })

    return alerts
