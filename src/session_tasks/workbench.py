"""Owner-only C4 projections and explicit fresh-baseline recovery.

No execution permission is inferred from a browser flag. Recovery locks the same
subject as authorization, checks persisted effects, and retires the old lease.
"""
from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal

from . import service
from .constants import SessionTaskError
from .texts import load_text, spec_digest


def capabilities(tenant_id):
    from src.weixin_conversation.config import scenario_enabled
    enabled = service.tenant_allowed(tenant_id) and scenario_enabled(tenant_id)
    return {"publish_enabled": enabled, "draft_enabled": True,
            "reason": None if enabled else "执行门禁未开放，目前可保存草稿和查看任务。"}


def input_version(conn, tenant_id, task_id):
    cursor = conn.cursor()
    cursor.execute("SELECT COALESCE(MAX(input_version),0) AS n FROM session_task_batches WHERE tenant_id=%s AND task_id=%s", (tenant_id, task_id))
    return int(cursor.fetchone()["n"])


def resume_task(tenant_id, user_id, task_id, expected_version, resume_from):
    if not isinstance(resume_from, dict) or resume_from.get("mode") != "fresh_baseline" or type(resume_from.get("expected_input_version")) is not int:
        raise SessionTaskError("必须显式选择新基线及当前消息版本；历史消息不会补发", "RESUME_BLOCKED_UNTIL_VERIFIED", 409)
    if not capabilities(tenant_id)["publish_enabled"]:
        raise SessionTaskError("执行门禁未开放", "FEATURE_DISABLED", 403)
    with service._conn() as conn:
        cursor = conn.cursor()
        # Same ordering as budget reservations, then subject → task → assignment.
        cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"st_budget:{tenant_id}",))
        cursor.execute("SELECT credit_balance FROM tenants WHERE tenant_id=%s FOR UPDATE", (tenant_id,))
        balance = cursor.fetchone()
        subject = service._lock_task_subject(conn, tenant_id, task_id)
        cursor.execute("SELECT * FROM session_tasks WHERE tenant_id=%s AND id=%s FOR UPDATE", (tenant_id, task_id))
        task = cursor.fetchone()
        if task is None or task["user_id"] != user_id:
            raise SessionTaskError("任务不存在或无权访问", "NOT_FOUND", 404)
        if task["version"] != expected_version or task["status"] not in ("paused", "human_required", "blocked"):
            raise SessionTaskError("版本或状态冲突，请刷新后选择恢复基线", "CONFLICT", 409)
        # Unknown effects and identity/coverage failures require their own evidence
        # resolution; clicking resume cannot wash them into an authorized state.
        if task["status"] == "blocked" and task["blocked_reason"] not in ("INSUFFICIENT_TENANT_CREDIT", "insufficient_tenant_credit"):
            raise SessionTaskError("当前阻断原因尚未核验解决", "RESUME_BLOCKED_UNTIL_VERIFIED", 409)
        if not subject or not task["current_spec_id"]:
            raise SessionTaskError("任务尚无有效发布授权", "CONFLICT", 409)
        version = input_version(conn, tenant_id, task_id)
        if resume_from["expected_input_version"] != version:
            raise SessionTaskError("消息水位已变化，请重新选择", "CONFLICT", 409)
        service._verify_bindings(tenant_id, user_id, task["device_id"], task["account_binding_id"], task["conversation_binding_id"], conn=conn)
        service._check_device_capabilities(conn, tenant_id, task["device_id"])
        spec = service._load_spec_by_spec_id(conn, tenant_id, task["current_spec_id"], task_id)
        if task["draft_digest"] != spec_digest(spec):
            raise SessionTaskError("草稿已变更，须先确认发布新版本", "CONFLICT", 409)
        from .models import validate_task_spec
        try:
            validate_task_spec(spec)
        except ValueError as exc:
            raise SessionTaskError("发布授权已过期或无效", "TASK_EXPIRED", 409) from exc
        cursor.execute("""SELECT 1 FROM session_task_execution_links l
            LEFT JOIN desktop_automation_deliveries d ON d.tenant_id=l.tenant_id AND d.id=l.delivery_id
            LEFT JOIN local_tool_invocations i ON i.tenant_id=l.tenant_id AND i.id=l.invocation_id
            WHERE l.tenant_id=%s AND l.task_id=%s AND
            (d.state IS NULL OR d.state NOT IN ('succeeded','failed','cancelled','skipped')
             OR (l.invocation_id IS NOT NULL AND (i.state IS NULL OR i.state NOT IN ('succeeded','failed','cancelled','expired')))) LIMIT 1""", (tenant_id, task_id))
        if cursor.fetchone():
            raise SessionTaskError("仍有未决或结果不明的发送，请先核对账本", "UNRESOLVED_SEND", 409)
        cursor.execute("SELECT 1 FROM session_task_decisions WHERE tenant_id=%s AND task_id=%s AND model_call_pending=TRUE LIMIT 1", (tenant_id, task_id))
        if cursor.fetchone():
            raise SessionTaskError("旧模型调用尚未确认结束", "MODEL_CALL_PENDING", 409)
        cursor.execute("SELECT COUNT(*) AS n FROM session_task_decisions WHERE tenant_id=%s AND task_id=%s AND model_attempts>0", (tenant_id, task_id))
        if int(cursor.fetchone()["n"]) >= spec["limits"]["max_decisions"]:
            raise SessionTaskError("决策次数已耗尽", "BUDGET_EXCEEDED", 409)
        cursor.execute("""SELECT COUNT(*) AS n FROM session_task_execution_links l
            LEFT JOIN desktop_automation_deliveries d ON d.tenant_id=l.tenant_id AND d.id=l.delivery_id
            WHERE l.tenant_id=%s AND l.task_id=%s AND d.state NOT IN ('failed','skipped')""", (tenant_id, task_id))
        if int(cursor.fetchone()["n"]) >= spec["limits"]["max_replies"]:
            raise SessionTaskError("回复次数已耗尽", "BUDGET_EXCEEDED", 409)
        settled, reserved = service.task_spend(conn, tenant_id, task_id)
        if settled + reserved >= Decimal(str(spec["limits"]["max_cost_units"])):
            raise SessionTaskError("任务费用预算已耗尽", "BUDGET_EXCEEDED", 409)
        cursor.execute("SELECT COALESCE(SUM(amount),0) AS n FROM session_task_cost_reservations WHERE tenant_id=%s AND state='reserved'", (tenant_id,))
        pending = Decimal(cursor.fetchone()["n"])
        if balance is None or Decimal(balance["credit_balance"] or 0) - pending < Decimal(str(service.get_session_tasks_config().decision_reserve_units)):
            raise SessionTaskError("租户可用积分不足，充值后需显式恢复", "INSUFFICIENT_TENANT_CREDIT", 409)
        # Retire all accepted historical input; no old decision can be regenerated.
        cursor.execute("UPDATE session_task_batches SET status='history_only' WHERE tenant_id=%s AND task_id=%s AND status='accepted'", (tenant_id, task_id))
        cursor.execute("UPDATE session_task_decisions SET status='superseded',lease_owner=NULL,lease_expires_at=NULL,updated_at=CURRENT_TIMESTAMP WHERE tenant_id=%s AND task_id=%s AND status IN ('pending','running','ready')", (tenant_id, task_id))
        epoch = task["control_epoch"] + 1
        cursor.execute("INSERT INTO session_task_batches (tenant_id,task_id,batch_id,input_version,synthetic,status) VALUES (%s,%s,%s,%s,TRUE,'resume_baseline')", (tenant_id, task_id, f"resume:{epoch}", version))
        cursor.execute("UPDATE session_task_assignments SET lease_expires_at=CURRENT_TIMESTAMP,server_control_seq=server_control_seq+1 WHERE tenant_id=%s AND task_id=%s AND is_current=TRUE", (tenant_id, task_id))
        cursor.execute("UPDATE session_tasks SET status='active',blocked_reason=NULL,completion_reason=NULL,version=version+1,control_epoch=control_epoch+1,server_control_seq=server_control_seq+1,updated_at=CURRENT_TIMESTAMP WHERE tenant_id=%s AND id=%s", (tenant_id, task_id))
        cursor.execute("UPDATE desktop_automation_subjects SET status='active',authorization_epoch=authorization_epoch+1,updated_at=CURRENT_TIMESTAMP WHERE id=%s AND tenant_id=%s", (subject["id"], tenant_id))
        conn.commit()
    return {"task_id": str(task_id), "status": "active", "version": expected_version+1, "control_epoch": epoch, "resume_from": resume_from}


def projection(conn, tenant_id, task):
    task_id = task["id"]
    cursor = conn.cursor()
    cursor.execute("SELECT conversation_label FROM bs_weixin_conversation_bindings WHERE tenant_id=%s AND id=%s", (tenant_id, task["conversation_binding_id"]))
    binding = cursor.fetchone()
    cursor.execute("SELECT last_seen_at FROM local_tool_devices WHERE tenant_id=%s AND id=%s", (tenant_id, task["device_id"]))
    device = cursor.fetchone()
    cursor.execute("""SELECT e.event_type,e.payload_text_id,e.received_at FROM session_task_events e
        JOIN session_task_assignments a ON a.tenant_id=e.tenant_id AND a.id=e.assignment_id
        WHERE e.tenant_id=%s AND e.task_id=%s AND a.is_current=TRUE
        AND e.event_type IN ('observation','baseline','batch')
        ORDER BY e.local_seq DESC LIMIT 1""", (tenant_id, task_id))
    observation = cursor.fetchone()
    observed = observation["received_at"] if observation else None
    phase = "ready"
    cursor.execute("""SELECT e.event_type,e.payload_text_id FROM session_task_events e
        JOIN session_task_assignments a ON a.tenant_id=e.tenant_id AND a.id=e.assignment_id
        WHERE e.tenant_id=%s AND e.task_id=%s AND a.is_current=TRUE
        AND e.event_type IN ('phase','decision_phase','execution_phase','recovery_blocked')
        ORDER BY e.local_seq DESC LIMIT 1""", (tenant_id, task_id))
    event = cursor.fetchone()
    if event:
        payload = load_text(conn, tenant_id, task_id, event["payload_text_id"], expected_purpose="event")
        phase = "blocked" if event["event_type"] == "recovery_blocked" else payload.get("phase_to", payload.get("to", "ready"))
    observation_gap = False
    if observation and observation["event_type"] == "observation":
        payload = load_text(conn, tenant_id, task_id, observation["payload_text_id"], expected_purpose="event")
        observation_gap = payload.get("outcome") == "gap"
    cursor.execute("""SELECT COUNT(*) FILTER (WHERE d.state='succeeded') AS replies,
        COUNT(*) FILTER (WHERE d.state='succeeded' AND dec.decision_kind='reply') AS rounds,
        COUNT(*) FILTER (WHERE d.state='unknown') AS unknowns
        FROM session_task_execution_links l LEFT JOIN desktop_automation_deliveries d ON d.tenant_id=l.tenant_id AND d.id=l.delivery_id
        LEFT JOIN session_task_decisions dec ON dec.tenant_id=l.tenant_id AND dec.id=l.decision_id
        WHERE l.tenant_id=%s AND l.task_id=%s""", (tenant_id, task_id))
    counts = cursor.fetchone()
    cursor.execute("SELECT COUNT(*) FILTER (WHERE model_attempts>0) AS n,COUNT(*) FILTER (WHERE status IN ('pending','running')) AS waiting FROM session_task_decisions WHERE tenant_id=%s AND task_id=%s", (tenant_id, task_id))
    decisions = cursor.fetchone()
    if decisions["waiting"] and phase != "blocked":
        phase = "decision_pending"
    if counts["unknowns"]:
        phase = "unknown_send"
    elif observation_gap:
        phase = "observation_gap"
    online = bool(device and device["last_seen_at"] and service._tz(device["last_seen_at"]) > service._now()-timedelta(seconds=90))
    return {"phase": phase, "input_version": input_version(conn, tenant_id, task_id),
            "binding_label": binding["conversation_label"] if binding else None,
            "device_online": online, "last_observed_at": observed,
            "replies_count": int(counts["replies"]), "rounds_count": int(counts["rounds"]), "decisions_count": int(decisions["n"])}


def timeline(tenant_id, user_id, task_id, limit=100, offset=0):
    with service._conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM session_tasks WHERE tenant_id=%s AND user_id=%s AND id=%s", (tenant_id, user_id, task_id))
        if not cursor.fetchone():
            raise SessionTaskError("任务不存在或无权访问", "NOT_FOUND", 404)
        params = (tenant_id, task_id, min(max(limit,1),100), max(offset,0))
        cursor.execute("SELECT batch_id,input_version,status,created_at,message_ids_json FROM session_task_batches WHERE tenant_id=%s AND task_id=%s ORDER BY created_at DESC LIMIT %s OFFSET %s", params)
        batches = [dict(r) for r in cursor.fetchall()]
        cursor.execute("SELECT id,batch_id,decision_kind,status,action,input_version,completion_evidence,failure_code,reply_text_id,created_at FROM session_task_decisions WHERE tenant_id=%s AND task_id=%s ORDER BY created_at DESC LIMIT %s OFFSET %s", params)
        decisions = [dict(r) for r in cursor.fetchall()]
        for d in decisions:
            ref = d.pop("reply_text_id")
            d["text"] = load_text(conn, tenant_id, task_id, ref, expected_purpose="decision").get("text") if ref else None
            d["completion_evidence"] = json.loads(d["completion_evidence"]) if d["completion_evidence"] else None
        cursor.execute("SELECT message_id,batch_id,input_version,sender,text_id,evidence_ref,created_at FROM session_task_messages WHERE tenant_id=%s AND task_id=%s ORDER BY created_at DESC LIMIT %s OFFSET %s", params)
        messages = [dict(r) for r in cursor.fetchall()]
        for m in messages:
            ref = m.pop("text_id")
            m["text"] = load_text(conn, tenant_id, task_id, ref, expected_purpose="message").get("text") if ref else None
        cursor.execute("""SELECT l.decision_id,l.occurrence_id,l.run_id,l.delivery_id,l.invocation_id,d.state AS delivery_state,d.phase AS delivery_phase,i.state AS invocation_state,d.created_at
            FROM session_task_execution_links l
            LEFT JOIN desktop_automation_deliveries d ON d.tenant_id=l.tenant_id AND d.id=l.delivery_id
            LEFT JOIN local_tool_invocations i ON i.tenant_id=l.tenant_id AND i.id=l.invocation_id
            WHERE l.tenant_id=%s AND l.task_id=%s ORDER BY d.created_at DESC LIMIT %s OFFSET %s""", params)
        executions = [dict(r) for r in cursor.fetchall()]
    return {"batches": batches, "decisions": decisions, "messages": messages, "executions": executions, "limit": params[2], "offset": params[3]}
