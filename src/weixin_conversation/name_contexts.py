"""Name routing on whichever personal WeChat is currently logged in.

No account identification: the legacy account_binding_id column carries only a
device routing scope UUID. No marketing account row or verified identity is made.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from uuid import UUID, NAMESPACE_URL, uuid5

from src.session_tasks.constants import SessionTaskError

POLICY = "current_login_name"
RESOLVER = "current-login-name-v1"


def is_name_context(row):
    return row is not None and row.get("verifier_version") == RESOLVER


def name_context_valid(row):
    expiry = row.get("expires_at") if row else None
    if expiry and expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return bool(is_name_context(row) and row.get("verification_status") == "resolved"
                and int(row.get("identity_version") or 0) > 0 and expiry
                and expiry > datetime.now(timezone.utc))


def _json(value):
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (ValueError, TypeError) as exc:
        raise SessionTaskError("名称定位结果格式无效", "NAME_NOT_RESOLVED", 409) from exc
    if not isinstance(parsed, dict):
        raise SessionTaskError("名称定位结果格式无效", "NAME_NOT_RESOLVED", 409)
    return parsed


def from_resolution(conn, tenant_id, user_id, device_id, invocation_id):
    """Consume an owned real readonly result inside the draft creation transaction."""
    from .config import scenario_enabled
    from src.channels.wecom_personal_rpa.secret_crypto import encrypt_secret
    if not scenario_enabled(tenant_id):
        raise SessionTaskError("微信会话场景未启用", "FEATURE_DISABLED", 403)
    cur = conn.cursor()
    cur.execute("""SELECT id FROM local_tool_devices WHERE tenant_id=%s AND user_id=%s
        AND id=%s AND status='active' FOR UPDATE""", (tenant_id, user_id, device_id))
    if not cur.fetchone():
        raise SessionTaskError("设备不存在或不可用", "NOT_FOUND", 404)
    cur.execute("""SELECT tool_name,provider_key,state,effect,arguments_json,result_json,
        finished_at AT TIME ZONE current_setting('TimeZone') AS finished_at
        FROM local_tool_invocations WHERE tenant_id=%s AND user_id=%s AND device_id=%s AND id=%s""",
        (tenant_id, user_id, device_id, invocation_id))
    invocation = cur.fetchone()
    if not invocation:
        raise SessionTaskError("名称定位结果不存在", "NOT_FOUND", 404)
    now = datetime.now(timezone.utc)
    finished = invocation["finished_at"]
    if finished and finished.tzinfo is None:
        finished = finished.replace(tzinfo=timezone.utc)
    if (invocation["tool_name"] != "weixin_name_resolve" or invocation["provider_key"] != "weixin"
            or invocation["state"] != "succeeded" or invocation["effect"] != "none"
            or not finished or not now - timedelta(minutes=5) <= finished <= now):
        raise SessionTaskError("需要近期成功的只读名称定位结果", "NAME_NOT_RESOLVED", 409)
    args, result = _json(invocation["arguments_json"]), _json(invocation["result_json"])
    data = result.get("data") if isinstance(result, dict) else None
    target = args.get("target_name") if isinstance(args, dict) else None
    if (not isinstance(target, str) or not target.strip() or len(target) > 128
            or re.search(r"[\x00-\x1f\x7f]", target) or not isinstance(data, dict)
            or data.get("target_name") != target or data.get("title_exact") is not True
            or data.get("unique_match") is not True):
        raise SessionTaskError("联系人名称未唯一精确定位", "NAME_NOT_RESOLVED", 409)
    if not isinstance(data.get("evidence_ref"), str) or not re.fullmatch(r"dpapi:[A-Za-z0-9_-]{1,128}", data["evidence_ref"]):
        raise SessionTaskError("名称定位缺少受控证据", "NAME_NOT_RESOLVED", 409)
    scope_id = uuid5(NAMESPACE_URL, json.dumps([POLICY, tenant_id, user_id, str(device_id)]))
    binding_id = uuid5(scope_id, target)
    cur.execute("SELECT * FROM bs_weixin_conversation_bindings WHERE tenant_id=%s AND id=%s FOR UPDATE",
                (tenant_id, binding_id))
    existing = cur.fetchone()
    if existing and name_context_valid(existing):
        return str(scope_id), str(binding_id)
    if existing:
        if not is_name_context(existing) or existing["verification_status"] != "resolved":
            raise SessionTaskError("名称上下文已失效", "NAME_NOT_RESOLVED", 409)
        cur.execute("""SELECT id FROM session_tasks WHERE tenant_id=%s AND conversation_binding_id=%s
            AND status NOT IN ('draft','completed','stopped') LIMIT 1""", (tenant_id, binding_id))
        if cur.fetchone():
            raise SessionTaskError("名称上下文过期且仍有未终结任务", "CONVERSATION_IN_USE", 409)
    version = int(existing["identity_version"]) + 1 if existing else 1
    evidence = {"policy": POLICY, "invocation_id": str(invocation_id), "target_name": target,
                "evidence_ref": data["evidence_ref"], "resolved_at": finished.isoformat()}
    try:
        encrypted = encrypt_secret(json.dumps(evidence, ensure_ascii=False))
    except Exception as exc:
        raise SessionTaskError("名称定位证据加密不可用", "CRYPTO_UNAVAILABLE", 503) from exc
    # verified_at remains NULL: this is a resolved route, not account identity.
    cur.execute("""INSERT INTO bs_weixin_conversation_bindings
        (id,tenant_id,user_id,device_id,account_binding_id,conversation_type,conversation_label,
         identity_version,verification_status,encrypted_identity_evidence,verifier_version,expires_at)
        VALUES (%s,%s,%s,%s,%s,'direct',%s,%s,'resolved',%s,%s,%s)
        ON CONFLICT (id) DO UPDATE SET identity_version=EXCLUDED.identity_version,
          encrypted_identity_evidence=EXCLUDED.encrypted_identity_evidence,expires_at=EXCLUDED.expires_at,
          updated_at=CURRENT_TIMESTAMP""",
        (binding_id, tenant_id, user_id, device_id, scope_id, target, version, encrypted, RESOLVER, now + timedelta(hours=24)))
    return str(scope_id), str(binding_id)
