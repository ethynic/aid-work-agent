"""完成规则判定（C3，设计 §5/§13.2）。

- validate_peer_confirmation：peer_confirmed 模式的字段/枚举/原文引用/消息归属
  服务端硬校验（模型提取只作候选，不作为事实）。
- evaluate_completion：三模式统一判定——输入为已取好的任务事实（spec/链接/
  delivery 状态/最新决策证据/最后 peer 批次时间），输出终态提议；状态写入由
  session_tasks 决策 worker 在 task 行锁内执行（本模块无 DB 写）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .prompts import OutputInvalid


def validate_peer_confirmation(
    rule_fields: List[Dict[str, Any]],
    confirmation: Dict[str, Any],
    peer_messages: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """校验模型提取的对方确认（§13.2）。

    peer_messages：当前输入版本 peer 消息 {message_id: {text}}。全部通过返回
    {"values": [...]}；任何缺口/矛盾抛 OutputInvalid（调用方按"缺字段继续等待/
    矛盾转人工"语义处理——本函数只判定，不区分两者，由 reason 表达）。
    """
    values = confirmation.get("values")
    if not isinstance(values, list):
        raise OutputInvalid("peer_confirmation.values 非法")
    by_key = {f.get("key"): f for f in rule_fields}
    got_keys = set()
    for item in values:
        key = item.get("key")
        if key not in by_key:
            raise OutputInvalid(f"未知确认字段 key: {key}")
        if key in got_keys:
            raise OutputInvalid(f"确认字段 key 重复: {key}")
        got_keys.add(key)
        field = by_key[key]
        value = item.get("value")
        if value not in (field.get("allowed_values") or []):
            raise OutputInvalid(f"字段 {key} 取值不在 allowed_values")
        message_ids = item.get("message_ids") or []
        quotes = item.get("quotes") or []
        if not message_ids:
            raise OutputInvalid(f"字段 {key} 缺少 message_ids")
        # §13.2：每个必需字段必须携带非空消息引用与非空白原文片段——缺失/空白视同
        # 无证据，不得产生完成事实
        if not quotes or any(not str(q).strip() for q in quotes):
            raise OutputInvalid(f"字段 {key} 缺少非空白原文引用片段（quotes）")
        for mid in message_ids:
            if mid not in peer_messages:
                raise OutputInvalid(f"字段 {key} 引用了非当前版本对方消息: {mid}")
        for quote in quotes:
            hit = any(_is_continuous_fragment(peer_messages[m]["text"], q) for m in message_ids for q in [quote])
            if not hit:
                raise OutputInvalid(f"字段 {key} 的引用片段与对方消息原文不符")
    missing = [f.get("key") for f in rule_fields if f.get("key") not in got_keys]
    if missing:
        raise OutputInvalid(f"缺少确认字段: {missing}")
    if confirmation.get("contradicted"):
        raise OutputInvalid("对方确认存在矛盾（contradicted=true）")
    # 全部字段的取值必须落在 accepted_values（require_all=true）
    for item in values:
        field = by_key[item.get("key")]
        if item.get("value") not in (field.get("accepted_values") or []):
            raise OutputInvalid(f"字段 {item.get('key')} 取值 {item.get('value')} 不在接受值集合内")
    return {"values": values}


def _is_continuous_fragment(text: str, fragment: str) -> bool:
    """quote 必须是消息原文的连续片段（子串；空白差异容错）。"""
    if not fragment:
        return False
    if fragment in text:
        return True
    return _squeeze(fragment) in _squeeze(text)


def _squeeze(s: str) -> str:
    return "".join(s.split())


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
    """统一完成/终止判定（设计 §5）。返回 None=维持 active，否则 {"status","reason"}。

    判定顺序：unknown 发送 → blocked（调用方先行检查）；期限 → stopped；
    三模式完成 → completed；max_replies 用尽 → stopped；等待对方超时 → stopped。
    存在未决发送/未决决策时不宣布 completed（§5：有未决 reply delivery 不得完成）。
    """
    limits = spec.get("limits") or {}
    rule = spec.get("completion_rule") or {}
    mode = rule.get("mode")

    expires_at = limits.get("expires_at")
    if expires_at is not None and _lt(expires_at, now):
        return {"status": "stopped", "reason": "deadline_expired"}

    # 发送计数：delivery 非 failed/skipped 即占用（含 unknown 保留占用、无 delivery 的未决链接）；
    # opening 占用 max_replies 与费用但不计完成轮数（§5：仅服务端验证成功的回复计轮）
    occupied = [l for l in links if l.get("delivery_state") not in ("failed", "skipped")]
    verified_sends = [
        l for l in links
        if l.get("delivery_state") == "succeeded" and l.get("decision_kind") != "opening"
    ]
    max_replies = int(limits.get("max_replies") or 0)

    if not has_pending_sends and not pending_decision_count:
        if mode == "rounds":
            target = int(rule.get("rounds_target") or 0)
            if len(verified_sends) >= target:
                return {"status": "completed", "reason": "rounds_reached"}
        elif mode == "peer_confirmed":
            evidence = (latest_reply_evidence or {}).get("peer_confirmed")
            if evidence:
                return {"status": "completed", "reason": "peer_confirmed"}
        elif mode == "judged":
            if (
                review_decision
                and review_decision.get("action") == "done"
                and review_decision.get("current", True)
            ):
                return {"status": "completed", "reason": "goal_judged"}

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
