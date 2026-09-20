"""BOSS 完成规则判定（B2，设计 §5.5：V1 仅 rounds；计轮继承微信语义）。

输入为已取好的任务事实（spec/链接/delivery 状态等），输出终态提议；状态写入由
session_tasks 决策 worker 在 task 行锁内执行（本模块无 DB 写）。
submitted 计轮：delivery_state='succeeded' 即计（applied+submitted 经通用映射
即为 succeeded；UI 对 submitted 显示"已执行发送（未核验送达）"）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def evaluate_completion(
    *,
    spec: Dict[str, Any],
    links: List[Dict[str, Any]],
    has_pending_sends: bool,
    latest_reply_evidence: Optional[Dict[str, Any]],
    review_decision: Optional[Dict[str, Any]],
    last_peer_activity_at: Optional[Any],
    now: Any,
    pending_decision_count: int,
) -> Optional[Dict[str, str]]:
    """rounds 完成判定。返回 None=维持 active，否则 {"status","reason"}。

    判定顺序：期限 → stopped；rounds 达标 → completed；max_replies 用尽 →
    stopped；等待对方超时 → stopped。存在未决发送/未决决策时不宣布 completed。"""
    limits = spec.get("limits") or {}
    rule = spec.get("completion_rule") or {}

    expires_at = limits.get("expires_at")
    if expires_at is not None and _lt(expires_at, now):
        return {"status": "stopped", "reason": "deadline_expired"}

    occupied = [l for l in links if l.get("delivery_state") not in ("failed", "skipped")]
    # submitted 计轮（继承微信语义）：delivery_state=succeeded 且非 opening
    completed_sends = [
        l for l in links
        if l.get("delivery_state") == "succeeded" and l.get("decision_kind") != "opening"
    ]
    max_replies = int(limits.get("max_replies") or 0)

    if not has_pending_sends and not pending_decision_count:
        target = int(rule.get("rounds_target") or 0)
        if target and len(completed_sends) >= target:
            return {"status": "completed", "reason": "rounds_reached"}
        if max_replies and len(occupied) >= max_replies:
            return {"status": "stopped", "reason": "max_replies_exhausted"}
        timeout = limits.get("peer_wait_timeout_seconds")
        if (
            last_peer_activity_at is not None
            and timeout
            and not has_pending_sends
            and pending_decision_count == 0
        ):
            from datetime import timedelta

            deadline = last_peer_activity_at + timedelta(seconds=int(timeout))
            if _lt(deadline, now):
                return {"status": "stopped", "reason": "peer_wait_timeout"}
    return None


def _lt(a: Any, b: Any) -> bool:
    """时区/形态安全比较 a < b（DB 可能给 naive datetime，spec JSON 里是 ISO 字符串）。"""
    import datetime as _dt

    def norm(x: Any) -> Any:
        if isinstance(x, str):
            try:
                x = _dt.datetime.fromisoformat(x.replace("Z", "+00:00"))
            except ValueError:
                return None  # 不可解析的时间不参与比较（不触发终止）
        if isinstance(x, _dt.datetime) and x.tzinfo is None:
            return x.replace(tzinfo=_dt.timezone.utc)
        return x

    a, b = norm(a), norm(b)
    if not isinstance(a, _dt.datetime) or not isinstance(b, _dt.datetime):
        return False
    return a < b
