"""Trace 展示语义的集中兼容判定。"""

PERSISTED_CHANNEL_SOURCES = frozenset({
    "wecom", "wecom_kf", "dingtalk", "feishu",
})


def is_interrupted_trace(trace: dict) -> bool:
    metadata = trace.get("metadata") or {}
    if metadata.get("termination_reason") == "message_merged":
        return trace.get("status") not in {"failed", "error"}
    if trace.get("source_type") not in PERSISTED_CHANNEL_SOURCES:
        return False
    if trace.get("subagent_id"):
        return False
    # superseded（被新消息取代，2026-11-26）与 cancelled 同类：渠道轮次展示语义一致
    if trace.get("status") not in {"completed", "cancelled", "superseded"}:
        return False
    if trace.get("error_message") or trace.get("output"):
        return False
    return not trace.get("user_message_id")
