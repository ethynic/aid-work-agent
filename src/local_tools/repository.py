"""本地工具 DB 访问层（同步 psycopg2，全部 SQL 参数化）

调用方（FastAPI async endpoint）必须用 asyncio.to_thread 包裹，禁止在事件循环内直接调用。
所有查询/更新必带 tenant_id（claim 同时带 device_id）。
"""

import math
import json
import hmac
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger
from psycopg2.extras import Json

from src.core.cache_utils import invalidate_tenant_cache
from src.db.client_binding_db import ClientUsageLogDB
from src.db.database import get_db_connection
from src.local_tools.pricing import tool_credit_price
from src.local_tools.security import sha256_hex

# 终态：写 result 时遇到终态直接幂等返回
TERMINAL_STATES = ("succeeded", "failed", "cancelled", "unknown", "expired")


class ResumeContinuationConflict(ValueError):
    def __init__(self, invocation_id):
        super().__init__('STALE_CONTINUATION')
        self.invocation_id = str(invocation_id)


class DeviceOperationUnresolved(ValueError):
    """A detached cloud wait grants no permission to reuse an unsettled desktop."""


PREEXECUTION_RESULT_CODES = ('NOT_LOGGED_IN', 'TOOL_NOT_ALLOWED', 'PROVIDER_NOT_AVAILABLE',
    'PROTOCOL_NOT_SUPPORTED', 'DESKTOP_NOT_INTERACTIVE', 'DESKTOP_RESOURCE_BUSY')
COMPLETED_NONE_RESULT_CODES = (*PREEXECUTION_RESULT_CODES, 'INVALID_ARGUMENT', 'CHROME_UNAVAILABLE',
    'MEMBERSHIP_REQUIRED', 'NO_MATCHING_CANDIDATES', 'RECOMMENDATIONS_BOUNDARY',
    'WRONG_PAGE', 'JOB_CHANGED', 'BUSY', 'PAYWALL', 'UI_CHANGED', 'TOOL_NOT_FOUND', 'UNKNOWN_TOOL')


def result_proves_completion(success, code, effect):
    # Abort/timeout/ProviderCrash can return while a child is still running.
    return success is True and effect in {'none', 'applied'} or (
        success is False and effect == 'none' and code in COMPLETED_NONE_RESULT_CODES)


def owned_invocation_ids(execution):
    """References identify ownership; receipts separately prove resource completion."""
    pending, identifiers = [execution], set()
    while pending:
        node = pending.pop()
        if not isinstance(node, dict):
            continue
        for ref in (node.get('resources', {}).get('local_invocations') or {}).values():
            tool = node.get('tools', {}).get(ref.get('tool_call_id')) or {}
            if (ref.get('execution_id') == node.get('execution_id')
                    and (tool.get('call') or {}).get('id') == ref.get('tool_call_id')):
                identifiers.add(ref['invocation_id'])
        pending.extend(child.get('checkpoint') for child in node.get('children', {}).values())
    return identifiers


def operation_completion_known(invocation):
    if invocation.get('state') == 'cancelled' and invocation.get('claimed_at') is None:
        return True
    observed = observed_invocation(invocation)
    result = observed.get('result_json') or {}
    return result_proves_completion(result.get('success'),
        observed.get('error_code') or result.get('code'), observed.get('effect'))


def lock_operation_admission(cursor, tenant_id):
    """Short transaction fence shared by detachment and all new device IO."""
    cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        ('local-operation-admission:' + tenant_id,))


def assert_device_stop_available(cursor, tenant_id, device_id, *, exclude=None, stop_only=False):
    """Short admission fence; queued recovery blocks new business, actual live work blocks stop."""
    cursor.execute("""SELECT i.id FROM local_tool_invocations i
        JOIN local_tool_devices original ON original.id=i.device_id
        JOIN local_tool_devices target ON target.id=%s AND target.tenant_id=i.tenant_id AND target.user_id=i.user_id
        WHERE i.tenant_id=%s AND (%s IS NULL OR i.id::text<>%s)
          AND (i.device_id=target.id OR original.machine_fingerprint_hash IS NOT NULL
            AND original.machine_fingerprint_hash<>'' AND target.machine_fingerprint_hash=original.machine_fingerprint_hash)
          AND ((i.business_kind='agent_runner_recovery' AND i.business_ref->>'version'='3'
                AND i.state IN ('queued','claimed','running') AND i.deadline_at>NOW())
            OR (NOT %s AND i.state IN ('claimed','running','cancel_requested')))
        LIMIT 1""", (device_id, tenant_id, exclude, exclude, stop_only))
    if cursor.fetchone():
        raise DeviceOperationUnresolved('LOCAL_RECOVERY_BUSY')


def assert_device_operations_settled(cursor, tenant_id, device_id, *, observation=None):
    if observation is not None:
        if observation.get('version') == 3:
            assert_device_stop_available(cursor, tenant_id, device_id, exclude=observation.get('invocation_id'))
        # Only observation_scope has validated this server-created Host request.
        # Its strict producer only reads CDP; it never grants a write or
        # claims the original unknown operation stopped.
        return
    assert_device_stop_available(cursor, tenant_id, device_id, stop_only=True)
    # Only explicit end-waiting holds participate; legacy unknown history does
    # not unexpectedly quarantine a device. The caller holds the device lock.
    cursor.execute("""SELECT i.id,i.state,i.effect,i.result_json,i.error_code,i.claimed_at,r.checkpoint
        FROM local_tool_invocations i
        JOIN agent_runners r ON r.runner_id=i.business_ref->>'runner_id'
        AND r.tenant_id=i.tenant_id
        JOIN local_tool_devices original ON original.id=i.device_id
        JOIN local_tool_devices target ON target.id=%s AND target.tenant_id=i.tenant_id
          AND target.user_id=i.user_id
        WHERE i.tenant_id=%s AND i.business_kind='agent_runner'
          AND (i.device_id=target.id OR original.machine_fingerprint_hash IS NOT NULL
            AND original.machine_fingerprint_hash<>''
            AND target.machine_fingerprint_hash=original.machine_fingerprint_hash)
          AND r.checkpoint->>'detached_local_wait'='true'
          AND (NOT r.checkpoint ? 'detached_local_invocations'
            OR r.checkpoint->'detached_local_invocations' ? i.id::text)
        """, (device_id, tenant_id))
    for invocation in cursor.fetchall():
        checkpoint = invocation['checkpoint']
        held = checkpoint.get('detached_local_invocations')
        if not isinstance(held, list):
            # Compatibility for older accepted end-waiting checkpoints: derive
            # only validated ownership references; classify each actual receipt
            # below, including completed Engine facts with an unresolved transport.
            held = owned_invocation_ids(checkpoint.get('execution') or {})
        if str(invocation['id']) in checkpoint.get('verified_local_invocations', []):
            continue
        if (observation and checkpoint.get('execution', {}).get('execution_id') == observation.get('runner_id')
                and str(invocation['id']) in observation['original_invocation_ids']):
            continue
        if str(invocation['id']) in held and not operation_completion_known(invocation):
            raise DeviceOperationUnresolved('LOCAL_DEVICE_STOP_VERIFICATION_REQUIRED')


def observed_invocation(invocation):
    """Trusted stored receipt view; keep the persisted unknown fact unchanged."""
    late = (invocation.get('result_json') or {}).get('_late_result')
    if invocation.get('state') != 'unknown' or not isinstance(late, dict):
        return invocation
    if not result_proves_completion(late.get('success'), late.get('code'), late.get('effect')):
        return invocation
    return {**invocation, 'state': 'succeeded' if late['success'] else
        'unknown' if late.get('code') == 'EXECUTION_UNKNOWN' else 'failed',
        'effect': late.get('effect'), 'result_json': late,
        'error_code': None if late['success'] else late.get('code'),
        'error_message': None if late['success'] else late.get('message')}


# CLI 回执固定附加的诊断元数据键（与业务读取成果无关）：
# - operation_recovery / visual_recovery：withLocalRecovery 对每个结果无条件附加；
# - page_operation：旧版 page_state 回执的等价事实键（operationRecoveryFailure）；
# - browser_target_ref：runBossOperation 的绑定目标标识；
# - run_id：历史允许的调用标识。
# 方向选择：识别「已知诊断键」为非成果，其余键一律视为业务成果（fail-closed）——
# 若改用业务键白名单，未来 CLI 回执新增成果字段会被误判为元数据而放行重试，
# 造成已读成果重复读取/重复计费；诊断键集合由 CLI 回执构造处稳定产出，漏新
# 诊断键的后果只是多拦一次重试（安全侧），绝不放行真成果。
CONTINUATION_DIAGNOSTIC_KEYS = frozenset({'run_id', 'operation_recovery', 'visual_recovery',
    'page_operation', 'browser_target_ref'})

# 「确认为空才算无成果」的只读结果字段（boss_resume_batch/next 失败回执的真实形态）。
# 「没有简历读取成果」以 resumes 为空列表为准（第八轮评审：取卡即标记含失败路径，
# processed_names 有名字/attempted>0 不代表有读取成果——成果 payload 只进 resumes）；
# processed_names/attempted/failures/stop_reason 不再作为拦截必要条件。
# 判定方向保持 fail-closed：resumes 非空或类型异常、以及集合外的任何未知新键，
# 一律视为业务成果拦截重试，防重复读取/重复计费。
NO_OUTCOME_RESULT_KEYS = frozenset({'resumes', 'processed_names', 'attempted',
    'failures', 'stop_reason'})


def _confirmed_no_read_outcomes(data):
    """只读批读取失败回执「确认为空」才视为无成果：resumes 为空列表（或键
    缺省）通过；非空或类型异常拦截（已读简历 payload 只出现在 resumes）。"""
    resumes = data.get('resumes')
    return resumes is None or (isinstance(resumes, list) and not resumes)


def continuation_retry_safe(invocation):
    """失败调用是否允许沿原游标重试（不构成重复读取/计费）。

    两类放行（评审第七/八轮，人工修复分支）：
    1. 纯诊断回执：data 仅含已知诊断元数据键——明确未执行类失败
       （NOT_LOGGED_IN/WRONG_PAGE 等），问题解决后重试；
    2. 无成果页面失败：data 额外携带「确认为空」的结果字段与失败诊断——
       「没有简历读取成果」以 resumes 为空列表为准（取卡即标记含失败路径，
       processed_names/attempted 不代表有成果）；UI_CHANGED（页面操作失败）
       与 NOT_LOGGED_IN/WRONG_PAGE 在「无成果」语义下同等对待，人工修复页面
       后沿原游标重试，不返回 STALE_CONTINUATION。
    """
    invocation = observed_invocation(invocation)
    result = invocation.get('result_json') or {}
    data = result.get('data')
    if (invocation.get('state') == 'unknown' and invocation.get('effect') == 'none'
            and invocation.get('tool_name') == 'boss_resume_next'
            and result.get('success') is False and result.get('code') == 'EXECUTION_UNKNOWN'
            and isinstance(data, dict) and data.get('resumes') == []):
        facts = data.get('operation_recovery') or {}
        operation = facts.get('operation') if isinstance(facts, dict) else None
        # A returned readonly command receipt is different from a claim lost
        # without any result. The next CLI verifies the original job/page again.
        return (isinstance(facts, dict) and facts.get('operation_class') == 'readonly'
            and isinstance(operation, dict) and operation.get('name') == 'boss_resume_next'
            and data.get('stop_reason') == 'page_operation_failed'
            and not (data.keys() - CONTINUATION_DIAGNOSTIC_KEYS - NO_OUTCOME_RESULT_KEYS - {'job_name'}))
    if (invocation.get('state') != 'failed' or invocation.get('effect') != 'none'
            or (invocation.get('error_code') or result.get('code')) not in {
                'TOOL_NOT_ALLOWED','TOOL_NOT_FOUND','UNKNOWN_TOOL','JOB_CHANGED',
                'WRONG_PAGE','INVALID_ARGUMENT','CHROME_UNAVAILABLE','NOT_LOGGED_IN',
                'UI_CHANGED'}):
        return False
    if data is None:
        return True
    if not isinstance(data, dict):
        return False
    if not (data.keys() - CONTINUATION_DIAGNOSTIC_KEYS):
        return True
    return (_confirmed_no_read_outcomes(data)
        and not (data.keys() - CONTINUATION_DIAGNOSTIC_KEYS - NO_OUTCOME_RESULT_KEYS))


class DeviceReplacementError(ValueError):
    """Fixed public error code; never carries a ticket, token or SQL parameters."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _lock_pairing_user(cursor, tenant_id: str, user_id: str) -> None:
    digest = sha256_hex(json.dumps(["local-tools-pairing", tenant_id, user_id]))
    key = int(digest[:16], 16)
    if key >= 2 ** 63:
        key -= 2 ** 64
    cursor.execute("SELECT pg_advisory_xact_lock(%s)", (key,))


def lock_active_device(cursor, tenant_id: str, device_id: str) -> bool:
    """Take before invocation/subject locks; serialize new authority with revoke.

    Device replacement/revocation never takes task locks. FOR SHARE blocks the
    status update through transaction end; a waiter sees the committed status.
    """
    cursor.execute(
        "SELECT id FROM local_tool_devices WHERE id = %s AND tenant_id = %s AND status = 'active' FOR SHARE",
        (device_id, tenant_id),
    )
    return cursor.fetchone() is not None


def issue_pairing_ticket(
    tenant_id: str, user_id: str, code_hash: str, expires_at: datetime,
    replacement_device_id: Optional[str] = None,
) -> str:
    """Invalidate old codes and issue the bound code in one transaction."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        _lock_pairing_user(cursor, tenant_id, user_id)
        if replacement_device_id is not None:
            cursor.execute(
                """SELECT id FROM local_tool_devices
                   WHERE id = %s AND tenant_id = %s AND user_id = %s
                     AND status IN ('active', 'revoked') FOR SHARE""",
                (replacement_device_id, tenant_id, user_id),
            )
            if not cursor.fetchone():
                raise DeviceReplacementError("REPLACEMENT_TARGET_NOT_FOUND")
        cursor.execute(
            """UPDATE local_tool_pairing_tickets SET used_at = NOW()
               WHERE tenant_id = %s AND user_id = %s AND used_at IS NULL""",
            (tenant_id, user_id),
        )
        cursor.execute(
            """INSERT INTO local_tool_pairing_tickets
               (tenant_id, user_id, code_hash, expires_at, replacement_device_id)
               VALUES (%s, %s, %s, %s, %s) RETURNING id""",
            (tenant_id, user_id, code_hash, expires_at, replacement_device_id),
        )
        ticket_id = str(cursor.fetchone()["id"])
        conn.commit()
        return ticket_id


def pair_device_from_ticket(
    code_hash: str, token_hash: str, name: Optional[str] = None,
    platform: Optional[str] = None, runtime_version: Optional[str] = None,
    capabilities: Optional[Dict[str, Any]] = None,
    machine_fingerprint_hash: Optional[str] = None,
    pairing_protocol: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """Atomically validate/consume a ticket, create a device and bind replacement.

    A lost response never revokes the old device. Its relation can only be
    completed later using the authenticated new device token.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """SELECT id, tenant_id, user_id, replacement_device_id
               FROM local_tool_pairing_tickets
               WHERE code_hash = %s AND used_at IS NULL AND expires_at > NOW()
               FOR UPDATE""", (code_hash,),
        )
        ticket = cursor.fetchone()
        if not ticket:
            return None
        target = ticket.get("replacement_device_id")
        if target is not None:
            if type(pairing_protocol) is not int or pairing_protocol != 2:
                raise DeviceReplacementError("PAIRING_PROTOCOL_REQUIRED")
            cursor.execute(
                """SELECT id FROM local_tool_devices
                   WHERE id = %s AND tenant_id = %s AND user_id = %s
                     AND status IN ('active', 'revoked') FOR SHARE""",
                (target, ticket["tenant_id"], ticket["user_id"]),
            )
            if not cursor.fetchone():
                raise DeviceReplacementError("REPLACEMENT_TARGET_NOT_FOUND")
        cursor.execute(
            """INSERT INTO local_tool_devices
               (tenant_id, user_id, name, platform, runtime_version, token_hash,
                machine_fingerprint_hash, capabilities_json, last_seen_at,
                replacement_device_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s)
               RETURNING id, tenant_id, user_id, name, platform, runtime_version,
                         capabilities_json, selected, status, last_seen_at, created_at,
                         replacement_device_id""",
            (ticket["tenant_id"], ticket["user_id"], name, platform, runtime_version,
             token_hash, machine_fingerprint_hash,
             Json(capabilities) if capabilities is not None else None, target),
        )
        device = dict(cursor.fetchone())
        cursor.execute(
            """UPDATE local_tool_pairing_tickets SET used_at = NOW()
               WHERE id = %s AND tenant_id = %s AND user_id = %s""",
            (ticket["id"], ticket["tenant_id"], ticket["user_id"]),
        )
        conn.commit()
        return device


def register_device_from_ticket(
    code_hash: Optional[str], token_hash: str, registration_id: str, request_hash: str,
    previous_token_hash: Optional[str] = None, name: Optional[str] = None,
    platform: Optional[str] = None, runtime_version: Optional[str] = None,
    capabilities: Optional[Dict[str, Any]] = None,
    machine_fingerprint_hash: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Protocol 3: durable request identity, authorized replacement and selection.

    Recovery needs only the client-protected token and immutable intent, never
    the OTP. Retries cannot select, revoke or revive a device a second time.
    All locks follow registration -> user -> ticket/device order.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        _lock_pairing_user(cursor, "registration-v3", registration_id)
        cursor.execute("SELECT * FROM local_tool_devices WHERE registration_id = %s", (registration_id,))
        existing = cursor.fetchone()
        if existing:
            if not (hmac.compare_digest(existing["token_hash"], token_hash)
                    and hmac.compare_digest(existing["registration_request_hash"], request_hash)
                    and (code_hash is None or hmac.compare_digest(existing["registration_code_hash"], code_hash))):
                raise DeviceReplacementError("REGISTRATION_CONFLICT")
            _lock_pairing_user(cursor, existing["tenant_id"], existing["user_id"])
            cursor.execute(
                """SELECT * FROM local_tool_devices
                   WHERE registration_id = %s AND tenant_id = %s AND user_id = %s FOR UPDATE""",
                (registration_id, existing["tenant_id"], existing["user_id"]),
            )
            existing = cursor.fetchone()
            if not existing or existing["status"] != "active":
                raise DeviceReplacementError("REGISTRATION_REVOKED")
            conn.commit()
            return dict(existing)
        if code_hash is None:
            raise DeviceReplacementError("PAIRING_CODE_REQUIRED")
        # Discover ownership without a ticket lock: issuing a code takes the
        # user lock first, so locking the ticket here would invert that order.
        cursor.execute("SELECT tenant_id, user_id FROM local_tool_pairing_tickets WHERE code_hash = %s", (code_hash,))
        owner = cursor.fetchone()
        if not owner:
            return None
        tenant, user = owner["tenant_id"], owner["user_id"]
        _lock_pairing_user(cursor, tenant, user)
        cursor.execute(
            """SELECT id, replacement_device_id FROM local_tool_pairing_tickets
               WHERE code_hash = %s AND tenant_id = %s AND user_id = %s
                 AND used_at IS NULL AND expires_at > NOW() FOR UPDATE""", (code_hash, tenant, user),
        )
        ticket = cursor.fetchone()
        if not ticket:
            return None
        target = ticket.get("replacement_device_id")
        if previous_token_hash:
            # Token itself is an ownership proof. Unknown/malformed or revoked
            # legacy credentials never block a clean registration.
            cursor.execute("SELECT id, tenant_id, user_id, status FROM local_tool_devices WHERE token_hash = %s", (previous_token_hash,))
            previous = cursor.fetchone()
            if (previous and previous["tenant_id"] == tenant and previous["user_id"] == user
                    and target is None and previous["status"] == "active"):
                target = previous["id"]
            # Changing account on the same computer is a valid clean pair. A
            # credential from the former account is ignored, never revoked.
        if target is not None:
            cursor.execute(
                """SELECT id, status, replaced_by_device_id FROM local_tool_devices
                   WHERE id = %s AND tenant_id = %s AND user_id = %s FOR UPDATE""", (target, tenant, user),
            )
            old = cursor.fetchone()
            if not old or old["status"] not in ("active", "revoked"):
                raise DeviceReplacementError("REPLACEMENT_TARGET_NOT_FOUND")
            if old.get("replaced_by_device_id") is not None:
                raise DeviceReplacementError("REPLACEMENT_ALREADY_COMPLETED")
        cursor.execute(
            """INSERT INTO local_tool_devices
               (tenant_id, user_id, name, platform, runtime_version, token_hash,
                machine_fingerprint_hash, capabilities_json, last_seen_at,
                replacement_device_id, replacement_completed_at,
                registration_id, registration_request_hash, registration_code_hash)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s,
                       CASE WHEN %s IS NOT NULL THEN NOW() END, %s, %s, %s)
               RETURNING *""",
            (tenant, user, name, platform, runtime_version, token_hash,
             machine_fingerprint_hash, Json(capabilities) if capabilities is not None else None,
             target, str(target) if target is not None else None, registration_id, request_hash, code_hash),
        )
        device = dict(cursor.fetchone())
        if target is not None:
            cursor.execute(
                """UPDATE local_tool_devices SET status = 'revoked', selected = FALSE,
                   replaced_by_device_id = %s, updated_at = NOW()
                   WHERE id = %s AND tenant_id = %s AND user_id = %s""",
                (device["id"], target, tenant, user),
            )
        cursor.execute("UPDATE local_tool_devices SET selected = FALSE WHERE tenant_id = %s AND user_id = %s", (tenant, user))
        cursor.execute("UPDATE local_tool_devices SET selected = TRUE, updated_at = NOW() WHERE id = %s AND tenant_id = %s AND user_id = %s", (device["id"], tenant, user))
        cursor.execute("UPDATE local_tool_pairing_tickets SET used_at = NOW() WHERE id = %s AND tenant_id = %s AND user_id = %s", (ticket["id"], tenant, user))
        conn.commit()
        device["selected"] = True
        return device


def complete_device_replacement(tenant_id: str, user_id: str, device_id: str) -> bool:
    """Only the authenticated new device can revoke its server-bound old device.

    The old row's durable winner arbitrates concurrent replacements. No task,
    fact or other device changes; new devices are never automatically selected.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        _lock_pairing_user(cursor, tenant_id, user_id)
        cursor.execute(
            """SELECT id, replacement_device_id, replacement_completed_at
               FROM local_tool_devices
               WHERE id = %s AND tenant_id = %s AND user_id = %s AND status = 'active'
               FOR UPDATE""", (device_id, tenant_id, user_id),
        )
        device = cursor.fetchone()
        if not device:
            raise DeviceReplacementError("REPLACEMENT_DEVICE_REVOKED")
        target = device.get("replacement_device_id")
        if target is None:
            return False
        if str(target) == str(device_id):
            raise DeviceReplacementError("REPLACEMENT_TARGET_NOT_FOUND")
        if device.get("replacement_completed_at") is not None:
            return True
        cursor.execute(
            """SELECT id, status, replaced_by_device_id FROM local_tool_devices
               WHERE id = %s AND tenant_id = %s AND user_id = %s
               FOR UPDATE""", (target, tenant_id, user_id),
        )
        old = cursor.fetchone()
        if not old or old["status"] not in ("active", "revoked"):
            raise DeviceReplacementError("REPLACEMENT_TARGET_NOT_FOUND")
        winner = old.get("replaced_by_device_id")
        if winner is not None and str(winner) != str(device_id):
            raise DeviceReplacementError("REPLACEMENT_ALREADY_COMPLETED")
        cursor.execute(
            """UPDATE local_tool_devices
               SET status = 'revoked', selected = FALSE, replaced_by_device_id = %s,
                   updated_at = NOW()
               WHERE id = %s AND tenant_id = %s AND user_id = %s""",
            (device_id, target, tenant_id, user_id),
        )
        cursor.execute(
            """UPDATE local_tool_devices
               SET replacement_completed_at = NOW(), updated_at = NOW()
               WHERE id = %s AND tenant_id = %s AND user_id = %s""",
            (device_id, tenant_id, user_id),
        )
        conn.commit()
        return True


# ==================== 配对码 ====================


def create_ticket(tenant_id: str, user_id: str, code_hash: str, expires_at: datetime) -> str:
    """创建配对码（库存 hash），返回 ticket id"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO local_tool_pairing_tickets (tenant_id, user_id, code_hash, expires_at)
            VALUES (%s, %s, %s, %s)
            RETURNING id
            """,
            (tenant_id, user_id, code_hash, expires_at),
        )
        ticket_id = str(cursor.fetchone()["id"])
        conn.commit()
        return ticket_id


def invalidate_unused_tickets(tenant_id: str, user_id: str) -> int:
    """作废同一用户所有未使用的旧配对码，防止多码并存"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE local_tool_pairing_tickets
            SET used_at = NOW()
            WHERE tenant_id = %s AND user_id = %s AND used_at IS NULL
            """,
            (tenant_id, user_id),
        )
        count = cursor.rowcount
        conn.commit()
        return count


def consume_ticket(code_hash: str) -> Optional[Dict[str, Any]]:
    """原子单次消费配对码：未使用且未过期才命中，返回 ticket 否则 None"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE local_tool_pairing_tickets
            SET used_at = NOW()
            WHERE code_hash = %s AND used_at IS NULL AND expires_at > NOW()
              AND replacement_device_id IS NULL
            RETURNING id, tenant_id, user_id, expires_at, used_at, created_at
            """,
            (code_hash,),
        )
        row = cursor.fetchone()
        conn.commit()
        return dict(row) if row else None


# ==================== 设备 ====================


def create_device(
    tenant_id: str,
    user_id: str,
    token_hash: str,
    name: Optional[str] = None,
    platform: Optional[str] = None,
    runtime_version: Optional[str] = None,
    capabilities: Optional[Dict[str, Any]] = None,
    machine_fingerprint_hash: Optional[str] = None,
) -> Dict[str, Any]:
    """配对成功后创建设备（只存 token hash）"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO local_tool_devices
                (tenant_id, user_id, name, platform, runtime_version, token_hash,
                 machine_fingerprint_hash, capabilities_json, last_seen_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
            RETURNING id, tenant_id, user_id, name, platform, runtime_version,
                      capabilities_json, selected, status, last_seen_at, created_at
            """,
            (
                tenant_id,
                user_id,
                name,
                platform,
                runtime_version,
                token_hash,
                machine_fingerprint_hash,
                Json(capabilities) if capabilities is not None else None,
            ),
        )
        row = dict(cursor.fetchone())
        conn.commit()
        return row


def get_device_by_token_hash(token_hash: str) -> Optional[Dict[str, Any]]:
    """按 token hash 查 active 设备（Runtime API 鉴权用）"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, tenant_id, user_id, name, platform, runtime_version,
                   capabilities_json, manifest_digest, selected, status, last_seen_at
            FROM local_tool_devices
            WHERE token_hash = %s AND status = 'active'
            """,
            (token_hash,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def list_devices(tenant_id: str, user_id: str) -> List[Dict[str, Any]]:
    """当前 tenant+user 的设备列表（最新在前）"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, tenant_id, user_id, name, platform, runtime_version,
                   capabilities_json, selected, status, last_seen_at, created_at
            FROM local_tool_devices
            WHERE tenant_id = %s AND user_id = %s
            ORDER BY created_at DESC
            """,
            (tenant_id, user_id),
        )
        return [dict(r) for r in cursor.fetchall()]


def revoke_device(tenant_id: str, user_id: str, device_id: str) -> bool:
    """撤销设备（status=revoked，selected 清除），返回是否命中"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        _lock_pairing_user(cursor, tenant_id, user_id)
        cursor.execute(
            """
            UPDATE local_tool_devices
            SET status = 'revoked', selected = FALSE, updated_at = NOW()
            WHERE id = %s AND tenant_id = %s AND user_id = %s AND status = 'active'
            """,
            (device_id, tenant_id, user_id),
        )
        ok = cursor.rowcount > 0
        conn.commit()
        return ok


def touch_device_seen(
    device_id: str,
    runtime_version: Optional[str] = None,
    capabilities: Optional[Dict[str, Any]] = None,
    manifest_digest: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """心跳：更新 last_seen / 版本 / 能力 / manifest 摘要，返回最新 selected/status"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE local_tool_devices
            SET last_seen_at = NOW(),
                runtime_version = COALESCE(%s, runtime_version),
                capabilities_json = COALESCE(%s, capabilities_json),
                manifest_digest = COALESCE(%s, manifest_digest),
                updated_at = NOW()
            WHERE id = %s
            RETURNING selected, status, last_seen_at
            """,
            (
                runtime_version,
                Json(capabilities) if capabilities is not None else None,
                manifest_digest,
                device_id,
            ),
        )
        row = cursor.fetchone()
        conn.commit()
        return dict(row) if row else None


def select_device(tenant_id: str, user_id: str, device_id: str) -> bool:
    """选定设备（单选）：事务内先清后设，目标设备必须归属该用户且 active"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        _lock_pairing_user(cursor, tenant_id, user_id)
        cursor.execute(
            "SELECT id FROM local_tool_devices WHERE id = %s AND tenant_id = %s AND user_id = %s AND status = 'active' FOR UPDATE",
            (device_id, tenant_id, user_id),
        )
        if not cursor.fetchone():
            return False
        cursor.execute(
            "UPDATE local_tool_devices SET selected = FALSE WHERE tenant_id = %s AND user_id = %s",
            (tenant_id, user_id),
        )
        cursor.execute(
            """
            UPDATE local_tool_devices
            SET selected = TRUE, updated_at = NOW()
            WHERE id = %s AND tenant_id = %s AND user_id = %s AND status = 'active'
            """,
            (device_id, tenant_id, user_id),
        )
        ok = cursor.rowcount > 0
        conn.commit()
        return ok


# ==================== Invocation ====================


def create_invocation(
    tenant_id: str,
    user_id: str,
    device_id: str,
    tool_name: str,
    arguments: Dict[str, Any],
    session_id: Optional[str] = None,
    *,
    provider_key: Optional[str] = None,
    business_kind: Optional[str] = None,
    business_ref: Optional[Dict[str, Any]] = None,
    dedupe_key: Optional[str] = None,
    deadline_at: Optional[datetime] = None,
    authorization_epoch: Optional[int] = None,
    execution_lane: str = "standard",
) -> str:
    """创建 invocation（state=queued），返回 id。

    - 聊天链路（proxy_tool）：session_id 计费台账归属（P2 客户端计费统一接入，设计 §4.2）；
      无会话来源（联调/API 直建）为 NULL。
    - v2 场景链路（desktop_automation）：business_kind/business_ref/dedupe_key/deadline_at/
      authorization_epoch/provider_key 扩展列；UNIQUE(tenant_id,business_kind,dedupe_key)
      幂等——同键冲突返回已有 invocation id（不产生新行）。
    - execution_lane（设计 §10）：仅服务端设置；'session_task' 道只被会话任务定向
      claim 领取，通用 claim 在 SQL 层排除。旧行为 default 'standard'。
    """
    with get_db_connection() as conn:
        invocation_id = create_invocation_in_tx(
            conn.cursor(), tenant_id, user_id, device_id, tool_name, arguments, session_id,
            provider_key=provider_key, business_kind=business_kind, business_ref=business_ref,
            dedupe_key=dedupe_key, deadline_at=deadline_at,
            authorization_epoch=authorization_epoch, execution_lane=execution_lane,
        )
        conn.commit()
        return invocation_id


def create_invocation_in_tx(
    cursor,
    tenant_id: str,
    user_id: str,
    device_id: str,
    tool_name: str,
    arguments: Dict[str, Any],
    session_id: Optional[str] = None,
    *,
    provider_key: Optional[str] = None,
    business_kind: Optional[str] = None,
    business_ref: Optional[Dict[str, Any]] = None,
    dedupe_key: Optional[str] = None,
    deadline_at: Optional[datetime] = None,
    authorization_epoch: Optional[int] = None,
    execution_lane: str = "standard",
    verify_binding: bool = False,
) -> str:
    """Write through the caller's transaction; never open or commit a connection.

    Legacy callers retain their existing dedupe behavior. Durable execution
    owners enable verify_binding to reject a key bound to a different request.
    The caller owns authorization, execution fencing and transaction rollback.
    """
    if verify_binding and (not business_kind or not dedupe_key):
        raise ValueError("LOCAL_INVOCATION_BINDING_REQUIRES_DEDUPE")
    lock_operation_admission(cursor, tenant_id)
    if not lock_active_device(cursor, tenant_id, device_id):
        raise DeviceOperationUnresolved('LOCAL_DEVICE_NOT_READY')
    observation = None
    if business_kind == 'agent_runner_recovery':
        from .recovery_observation import observation_scope
        observation = observation_scope(cursor, dict(tenant_id=tenant_id,user_id=user_id,session_id=session_id,
            device_id=device_id,tool_name=tool_name,arguments_json=arguments,provider_key=provider_key,business_kind=business_kind,business_ref=business_ref))
        if not observation:
            raise DeviceOperationUnresolved('LOCAL_VERIFICATION_BINDING_INVALID')
    assert_device_operations_settled(cursor, tenant_id, device_id, observation=observation)
    if tool_name == 'boss_resume_next':
        # Lock the parent in the same transaction as insertion. Competing calls
        # cannot fork the cursor, without changing the Runner's own dedupe key.
        cursor.execute("""SELECT * FROM local_tool_invocations
            WHERE id=%s AND tenant_id=%s AND user_id=%s AND session_id=%s AND device_id=%s
              AND tool_name IN ('boss_resume_batch','boss_resume_next')
              FOR UPDATE""",
            (arguments.get('continue_from'), tenant_id, user_id, session_id, device_id))
        parent = cursor.fetchone()
        parent = observed_invocation(dict(parent)) if parent else {}
        if parent.get('state') != 'succeeded' or parent.get('effect') != 'none':
            raise ValueError('INVALID_CONTINUATION')
        cursor.execute("""SELECT id,business_kind,dedupe_key,state,effect,result_json,error_code FROM local_tool_invocations
            WHERE tenant_id=%s AND user_id=%s AND session_id=%s AND device_id=%s
              AND tool_name='boss_resume_next' AND arguments_json->>'continue_from'=%s
            ORDER BY created_at DESC, id DESC""",
            (tenant_id, user_id, session_id, device_id, arguments.get('continue_from')))
        successor = next((record for record in cursor.fetchall() if not continuation_retry_safe(dict(record))), None)
        if successor and not (business_kind is not None and dedupe_key is not None
            and successor['business_kind'] == business_kind and successor['dedupe_key'] == dedupe_key):
            raise ResumeContinuationConflict(successor['id'])
    conflict = """
        ON CONFLICT (tenant_id, business_kind, dedupe_key)
            WHERE business_kind IS NOT NULL AND dedupe_key IS NOT NULL
            DO NOTHING
    """ if business_kind is not None and dedupe_key is not None else ""
    cursor.execute(
        """
        INSERT INTO local_tool_invocations
            (tenant_id, user_id, device_id, tool_name, arguments_json, session_id,
             provider_key, business_kind, business_ref, dedupe_key, deadline_at,
             authorization_epoch, execution_lane)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """ + conflict + " RETURNING id",
        (tenant_id, user_id, device_id, tool_name, Json(arguments), session_id,
         provider_key, business_kind, Json(business_ref) if business_ref else None,
         dedupe_key, deadline_at, authorization_epoch, execution_lane),
    )
    row = cursor.fetchone()
    if row is not None:
        return str(row["id"])
    # The conflicting insert must have committed before DO NOTHING returns.
    # Lock the actual fact before comparing its complete immutable binding.
    cursor.execute(
        """SELECT id, tenant_id, user_id, device_id, tool_name, arguments_json,
                  session_id, provider_key, business_kind, business_ref,
                  dedupe_key, deadline_at, authorization_epoch,
                  COALESCE(execution_lane, 'standard') AS execution_lane
           FROM local_tool_invocations
           WHERE tenant_id = %s AND business_kind = %s AND dedupe_key = %s
           FOR UPDATE""",
        (tenant_id, business_kind, dedupe_key),
    )
    row = cursor.fetchone()
    if row is None:
        raise RuntimeError("LOCAL_INVOCATION_DEDUPE_NOT_FOUND")
    if verify_binding:
        expected = dict(
            tenant_id=tenant_id, user_id=user_id, device_id=device_id,
            tool_name=tool_name, arguments_json=arguments, session_id=session_id,
            provider_key=provider_key, business_kind=business_kind,
            business_ref=business_ref or None, dedupe_key=dedupe_key,
            deadline_at=deadline_at, authorization_epoch=authorization_epoch,
            execution_lane=execution_lane,
        )
        # UUID columns are decoded as UUID or str depending on connection setup.
        for key, value in expected.items():
            actual = row[key]
            if key in ("device_id", "user_id", "session_id", "tenant_id"):
                actual = str(actual) if actual is not None else None
                value = str(value) if value is not None else None
            if actual != value:
                raise ValueError("LOCAL_INVOCATION_BINDING_CHANGED")
    return str(row["id"])


def set_invocation_credit_cost(invocation_id: str, credit_cost: float) -> None:
    """计费成功后把实扣积分回写 invocation 行（与 client_usage_logs.detail 的 invocation_id 双向对账）。

    invocation id 是 UUID 字符串（create_invocation 返回 str(row["id"])），绝不能 int() 转换——
    历史bug：int(UUID) 恒抛 ValueError 被调用方吞掉，导致该对账回写从未成功过。
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE local_tool_invocations SET credit_cost = %s WHERE id = %s",
            (credit_cost, invocation_id),
        )
        conn.commit()


def list_resume_next_attempts(parent_id: str, tenant_id: str, user_id: str,
                              session_id: str, device_id: str) -> List[Dict[str, Any]]:
    """列出沿同一游标（continue_from=parent）的全部 boss_resume_next 记录。

    与 create_invocation_in_tx 的后继扫描同口径（tenant/user/session/device 归属）。
    供续筛游标合成使用：其中「失败且可重试」的尝试已标记处理过的候选人
    （取卡即标记，含失败路径）沿用游标排除语义合并进新的 exclude_names，
    人工修复页面后的重试不再重复尝试这些失败候选人。
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """SELECT id, state, effect, result_json, error_code
            FROM local_tool_invocations
            WHERE tenant_id=%s AND user_id=%s AND session_id=%s AND device_id=%s
              AND tool_name='boss_resume_next' AND arguments_json->>'continue_from'=%s
            ORDER BY created_at DESC, id DESC""",
            (tenant_id, user_id, session_id, device_id, parent_id),
        )
        return [dict(row) for row in cursor.fetchall()]


def get_invocation(invocation_id: str, tenant_id: str) -> Optional[Dict[str, Any]]:
    """按 id+tenant 查询 invocation（含 v2 扩展列）。M0.5 proxy 轮询终态 / v2 链路使用"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, tenant_id, user_id, device_id, tool_name, arguments_json, session_id,
                   state, effect, result_json, error_code, error_message, credit_cost,
                   created_at, claimed_at, started_at, finished_at,
                   provider_key, business_kind, business_ref, dedupe_key, deadline_at,
                   authorization_epoch, write_phase, COALESCE(execution_lane, 'standard') AS execution_lane
            FROM local_tool_invocations
            WHERE id = %s AND tenant_id = %s
            """,
            (invocation_id, tenant_id),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def list_events(invocation_id: str, since_seq: int = 0) -> List[Dict[str, Any]]:
    """按 seq 游标拉取新事件（升序）。M0.5 proxy 进度转发使用"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT seq, stage, current, total, message, created_at
            FROM local_tool_events
            WHERE invocation_id = %s AND seq > %s
            ORDER BY seq
            """,
            (invocation_id, since_seq),
        )
        return [dict(r) for r in cursor.fetchall()]


def expire_stale_claims() -> int:
    """租约过期的 claimed/running/cancel_requested → unknown/effect=unknown（claim 路径顺带调用）

    cancel_requested 一并纳入（Phase 4）：deadline 拆除后取消回执等待不再有持久截止
    兜底，租约停续即执行归属丢失，按效果未知收敛（设备是否已停手无法确认）。
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE local_tool_invocations
            SET state = 'unknown', effect = 'unknown', finished_at = NOW()
            WHERE state IN ('claimed', 'running', 'cancel_requested')
              AND lease_expires_at IS NOT NULL
              AND lease_expires_at < NOW()
            """
        )
        count = cursor.rowcount
        conn.commit()
        if count:
            logger.warning(f"后端日志：{count} 条本地工具 invocation 租约过期，置为 unknown")
        return count


def claim_lease_expired(invocation_id: str, tenant_id: str) -> bool:
    """执行侧（claimed/running/cancel_requested）租约是否已停续（活动续租观察）。

    queued 尚无执行归属（未领取、无租约），恒为 False。SQL 内与 NOW() 同会话时区
    比较（lease_expires_at 为无时区列，与 permits 同一注意点）。
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT (state IN ('claimed', 'running', 'cancel_requested')
                    AND lease_expires_at IS NOT NULL AND lease_expires_at < NOW()) AS stale
            FROM local_tool_invocations
            WHERE id = %s AND tenant_id = %s
            """,
            (invocation_id, tenant_id),
        )
        row = cursor.fetchone()
        conn.commit()
        return bool(row and row["stale"])


def claim_next(
    device_id: str,
    tenant_id: str,
    claim_token_hash: str,
    lease_seconds: int,
    provider_keys: Optional[List[str]] = None,
    execution_lane: Optional[str] = "standard",
    expected_invocation_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """领取下一条 queued invocation（事务 + FOR UPDATE SKIP LOCKED 防重复领取）。

    provider 过滤（总工程师契约补充）：行 provider_key 为 NULL → 任何设备可领（旧行为，
    旧数据兼容）；非 NULL → 仅设备 capabilities 含该 provider 的设备可领（provider_keys
    传入设备能力集合，None 表示不过滤——直连 repository 的测试/内部调用）。

    execution_lane（设计 §10）：None = 不过滤（测试/内部）；'standard' = 通用 claim，
    SQL 层排除 session_task 道会话任务 invocation（老客户端不抢新道）；'session_task'
    需配合 expected_invocation_id 定向领取该行（不领取任意 invocation）。COALESCE 兼容
    迁移前 NULL 旧行（视作 standard）。
    """
    lane_filter = ""
    params: List[Any] = [device_id, tenant_id]
    if execution_lane is not None:
        lane_filter = " AND COALESCE(execution_lane, 'standard') = %s"
        params.append(execution_lane)
    if expected_invocation_id is not None:
        lane_filter += " AND id = %s"
        params.append(expected_invocation_id)
    provider_filter = ""
    if provider_keys is not None:
        provider_filter = " AND (provider_key IS NULL OR provider_key = ANY(%s))"
        params.append(list(provider_keys))
    with get_db_connection() as conn:
        cursor = conn.cursor()
        lock_operation_admission(cursor, tenant_id)
        if not lock_active_device(cursor, tenant_id, device_id):
            return None
        cursor.execute(
            f"""
            SELECT * FROM local_tool_invocations
            WHERE state = 'queued' AND device_id = %s AND tenant_id = %s
              {lane_filter}{provider_filter}
              AND (business_kind IS DISTINCT FROM 'agent_runner_recovery' OR business_ref->>'version' IS DISTINCT FROM '3'
                OR (deadline_at>NOW() AND EXISTS (SELECT 1 FROM agent_runners r
                  WHERE r.runner_id=local_tool_invocations.business_ref->>'runner_id' AND r.tenant_id=local_tool_invocations.tenant_id
                    AND r.checkpoint->'local_verification'->>'invocation_id'=local_tool_invocations.id::text)))
            ORDER BY (business_kind='agent_runner_recovery') DESC NULLS LAST,created_at
            LIMIT 1
            FOR UPDATE SKIP LOCKED
            """,
            tuple(params),
        )
        row = cursor.fetchone()
        if not row:
            conn.commit()
            return None
        from .recovery_observation import observation_scope
        assert_device_operations_settled(cursor, tenant_id, device_id, observation=observation_scope(cursor, row, dispatch=True))
        cursor.execute(
            """
            UPDATE local_tool_invocations
            SET state = 'claimed',
                claim_token_hash = %s,
                lease_expires_at = NOW() + (%s * INTERVAL '1 second'),
                claimed_at = NOW()
            WHERE id = %s
            RETURNING id, tenant_id, user_id, device_id, tool_name, arguments_json,
                      state, lease_expires_at, claimed_at, created_at, provider_key,
                      business_kind, business_ref, deadline_at, authorization_epoch,
                      COALESCE(execution_lane, 'standard') AS execution_lane
            """,
            (claim_token_hash, lease_seconds, row["id"]),
        )
        invocation = dict(cursor.fetchone())
        conn.commit()
        return invocation


def mark_started(invocation_id: str, tenant_id: str, claim_token_hash: str) -> Optional[Dict[str, Any]]:
    """claimed → running。hash 匹配时返回当前记录（无论是否迁移），不匹配/不存在返回 None"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        lock_operation_admission(cursor, tenant_id)
        cursor.execute('SELECT * FROM local_tool_invocations WHERE id=%s AND tenant_id=%s AND claim_token_hash=%s',
            (invocation_id, tenant_id, claim_token_hash))
        owner = cursor.fetchone()
        if not owner or not lock_active_device(cursor, tenant_id, str(owner['device_id'])):
            return None
        from .recovery_observation import observation_scope
        observation = observation_scope(cursor, owner, dispatch=True)
        if (owner.get('business_kind') == 'agent_runner_recovery'
                and (owner.get('business_ref') or {}).get('version') == 3 and observation is None):
            return None
        assert_device_operations_settled(cursor, tenant_id, str(owner['device_id']), observation=observation)
        cursor.execute(
            """
            UPDATE local_tool_invocations
            SET state = 'running', started_at = NOW()
            WHERE id = %s AND tenant_id = %s AND claim_token_hash = %s AND state = 'claimed'
            RETURNING *
            """,
            (invocation_id, tenant_id, claim_token_hash),
        )
        row = cursor.fetchone()
        conn.commit()
        if row:
            return dict(row)
        cursor.execute(
            """
            SELECT * FROM local_tool_invocations
            WHERE id = %s AND tenant_id = %s AND claim_token_hash = %s
            """,
            (invocation_id, tenant_id, claim_token_hash),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def append_event(
    invocation_id: str,
    tenant_id: str,
    claim_token_hash: str,
    stage: Optional[str],
    current: Optional[int],
    total: Optional[int],
    message: Optional[str],
    lease_seconds: int,
) -> Optional[Tuple[int, bool]]:
    """追加进度事件并续租，返回 (seq, cancel_flag)。

    校验 claim token + state in (claimed, running, cancel_requested)。
    cancel_requested 也放行，否则设备无法在 progress 响应里拿到 cancel=true。
    hash 不匹配或状态不允许返回 None。
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT device_id FROM local_tool_invocations WHERE id = %s AND tenant_id = %s AND claim_token_hash = %s",
            (invocation_id, tenant_id, claim_token_hash),
        )
        owner = cursor.fetchone()
        if not owner or not lock_active_device(cursor, tenant_id, str(owner["device_id"])):
            return None
        cursor.execute(
            """
            SELECT state FROM local_tool_invocations
            WHERE id = %s AND tenant_id = %s AND claim_token_hash = %s
            """,
            (invocation_id, tenant_id, claim_token_hash),
        )
        row = cursor.fetchone()
        if not row or row["state"] not in ("claimed", "running", "cancel_requested"):
            conn.commit()
            return None

        cancel_flag = row["state"] == "cancel_requested"

        cursor.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM local_tool_events WHERE invocation_id = %s",
            (invocation_id,),
        )
        seq = cursor.fetchone()["next_seq"]
        cursor.execute(
            """
            INSERT INTO local_tool_events (invocation_id, tenant_id, seq, stage, current, total, message)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (invocation_id, tenant_id, seq, stage, current, total, message),
        )
        # 进度回传顺带续租
        cursor.execute(
            """
            UPDATE local_tool_invocations
            SET lease_expires_at = NOW() + (%s * INTERVAL '1 second')
            WHERE id = %s
            """,
            (lease_seconds, invocation_id),
        )
        conn.commit()
        return seq, cancel_flag


def write_runtime_result(invocation_id, tenant_id, claim_token_hash, success, code=None,
        message=None, effect=None, data=None, retryable=None, *, device_id=None):
    invocation = get_invocation(invocation_id, tenant_id)
    if invocation and invocation.get('business_kind') == 'agent_runner_recovery':
        from .recovery_observation import write_result as write_observation_result
        return write_observation_result(invocation_id, tenant_id, claim_token_hash, success,
            code, message, effect, data, retryable, device_id)
    return write_result(invocation_id, tenant_id, claim_token_hash, success, code,
        message, effect, data, retryable, device_id=device_id)


def write_result(
    invocation_id: str,
    tenant_id: str,
    claim_token_hash: str,
    success: bool,
    code: Optional[str] = None,
    message: Optional[str] = None,
    effect: Optional[str] = None,
    data: Optional[Dict[str, Any]] = None,
    retryable: Optional[bool] = None,
    *,
    device_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """写入终态（幂等）+ succeeded 首次落终态时同事务按次计费。

    state 映射：success→succeeded；code='EXECUTION_UNKNOWN'→unknown；其余失败→failed。
    hash 不匹配或不存在返回 None。

    计费（2026-09-01 时机迁移，docs/design/billing/client-billing-integration-design.md §4.1）：
    计费点从 proxy_tool 轮询侧迁到本函数——Runtime 回写结果是 invocation 的权威落库
    时刻，会话断开/轮询协程死亡不再漏计费（agent2 实证 8/25 单日 37 次成功 0 计费）。
    succeeded 首次落终态时同事务完成「台账 INSERT + 租户扣减 + credit_cost 回写」；
    本函数对已终态幂等短路，保证恰好计费一次。免费工具回写 credit_cost=0 占位
    （succeeded 且 credit_cost IS NULL 即计费降级，对账 SQL 可直接揪出）；计费异常
    降级为只落终态（credit_cost 留 NULL），绝不吞掉设备执行结果。
    """
    billed_tenant_id: Optional[str] = None
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # FOR UPDATE 行锁：挡住同一 claim_token 并发重叠写（设备 HTTP 超时重试等）——
        # 后到事务在此阻塞，等先到事务提交后读到终态走幂等短路，杜绝双份台账双倍扣款
        cursor.execute(
            "SELECT * FROM local_tool_invocations WHERE id = %s AND tenant_id = %s FOR UPDATE",
            (invocation_id, tenant_id),
        )
        row = cursor.fetchone()
        if (not row or row["claim_token_hash"] != claim_token_hash
                or device_id is not None and str(row['device_id']) != str(device_id)):
            conn.commit()
            return None
        late_result = row['state'] == 'unknown' and row.get('result_json') is None
        if row["state"] in TERMINAL_STATES and not late_result:
            # 幂等：已终态重复写返回原状态，不报错
            conn.commit()
            return dict(row)

        if success:
            new_state = "succeeded"
        elif code == "EXECUTION_UNKNOWN":
            new_state = "unknown"
        else:
            new_state = "failed"

        # 计费预判：仅 succeeded 首次落终态。price>0 落台账+扣费；price=0（免费）仅回写占位。
        # 先 ceil 到分再判断，与 insert_tool_usage_row 的台账舍入保持同一数值
        bill_price: Optional[float] = None
        if success and row.get("credit_cost") is None:
            bill_price = math.ceil(tool_credit_price(row.get("tool_name")) * 100) / 100
            if bill_price > 0:
                balance_after: Optional[float] = None
                try:
                    balance_after = ClientUsageLogDB.insert_tool_usage_row(
                        cursor,
                        tenant_id=row["tenant_id"],
                        tool_name=row["tool_name"],
                        credit_cost=bill_price,
                        invocation_id=str(row["id"]),
                        device_id=str(row["device_id"]) if row.get("device_id") else None,
                        session_id=row.get("session_id"),
                        user_id=row.get("user_id"),
                        arguments=row.get("arguments_json"),
                    )
                    billed_tenant_id = row["tenant_id"]
                    logger.info(
                        f"后端日志：BOSS工具计费（落库侧） tenant={row['tenant_id']} "
                        f"user={row.get('user_id')} session={row.get('session_id')} "
                        f"tool={row['tool_name']} invocation={row['id']} "
                        f"cost={bill_price} balance_after={balance_after}"
                    )
                    if balance_after is None:
                        # UPDATE tenants 影响 0 行：租户不存在（如测试租户污染），余额未扣——当场告警
                        logger.error(
                            f"后端日志：本地工具计费扣减失败（租户不存在，余额未扣） "
                            f"tenant={row['tenant_id']} tool={row['tool_name']} invocation={row['id']}"
                        )
                except Exception:
                    # 计费异常绝不吞工具结果：回滚计费半程，降级为只落终态
                    # （credit_cost 留 NULL = 待补账，对账 SQL 见设计文档 §6）
                    conn.rollback()
                    billed_tenant_id = None
                    bill_price = None
                    logger.opt(exception=True).error(
                        f"后端日志：本地工具计费落账失败，降级为只落终态（待对账补账） "
                        f"tool={row.get('tool_name')} invocation={row['id']}"
                    )
                    # rollback 释放了行锁：重取行并重新加锁——若并发重复写已落终态则幂等返回，
                    # 否则后续 UPDATE 仍在锁保护下执行
                    cursor.execute(
                        "SELECT * FROM local_tool_invocations WHERE id = %s AND tenant_id = %s FOR UPDATE",
                        (invocation_id, tenant_id),
                    )
                    row = cursor.fetchone()
                    if (not row or row['claim_token_hash'] != claim_token_hash
                            or device_id is not None and str(row['device_id']) != str(device_id)):
                        conn.commit()
                        return None
                    late_result = row['state'] == 'unknown' and row.get('result_json') is None
                    if row["state"] in TERMINAL_STATES and not late_result:
                        conn.commit()
                        return dict(row) if row else None

        result_json = {
            "success": success,
            "code": code,
            "message": message,
            "data": data,
            "retryable": retryable,
        }
        if late_result:
            # The original lease-loss fact stays unknown. A final device receipt
            # proves this invocation stopped, not that a new execution is allowed.
            result_json = {'_late_result': {**result_json, 'effect': effect,
                'stopped': result_proves_completion(success, code, effect)}}
            new_state, effect, code, message = row['state'], row['effect'], row.get('error_code'), row.get('error_message')
        sql = """
            UPDATE local_tool_invocations
            SET state = %s,
                effect = %s,
                result_json = %s,
                error_code = %s,
                error_message = %s,
                finished_at = COALESCE(finished_at, NOW()),
                lease_expires_at = NULL"""
        params: List[Any] = [
            new_state,
            effect,
            Json(result_json),
            code if late_result else None if success else code,
            message if late_result else None if success else message,
        ]
        if bill_price is not None:
            sql += ",\n                credit_cost = %s"
            params.append(bill_price)
        sql += "\n            WHERE id = %s\n            RETURNING *"
        params.append(invocation_id)
        cursor.execute(sql, tuple(params))
        updated = dict(cursor.fetchone())
        conn.commit()

    if billed_tenant_id:
        # 失效租户缓存（确保余额阻断读到最新值，与 record_llm_usage 同一模式）
        try:
            invalidate_tenant_cache(billed_tenant_id)
        except Exception as e:
            logger.warning(f"BOSS工具扣费后失效租户缓存失败 tenant={billed_tenant_id}: {e}")
    return updated


def request_cancel(invocation_id: str, tenant_id: str) -> bool:
    """请求取消。M0.5 路由层使用。

    - queued → cancelled（终态，effect=none）：设备永远不会领取，必须直接落终态，
      否则行会永久卡在 cancel_requested（claim_next 只取 queued，租约清扫只管 claimed/running）
    - claimed/running → cancel_requested：等设备在 progress 响应里看到 cancel=true 后写终态
    """
    with get_db_connection() as conn:
        changed = request_cancel_in_tx(conn.cursor(), invocation_id, tenant_id)
        conn.commit()
        return changed


def request_cancel_in_tx(cursor, invocation_id: str, tenant_id: str) -> bool:
    """Original cancellation SQL using the caller's transaction, no commit."""
    cursor.execute(
            """
            UPDATE local_tool_invocations
            SET state = 'cancelled', effect = 'none', finished_at = NOW()
            WHERE id = %s AND tenant_id = %s AND state = 'queued'
            """,
            (invocation_id, tenant_id),
        )
    count = cursor.rowcount
    cursor.execute(
            """
            UPDATE local_tool_invocations
            SET state = 'cancel_requested'
            WHERE id = %s AND tenant_id = %s AND state IN ('claimed', 'running')
            """,
            (invocation_id, tenant_id),
        )
    count += cursor.rowcount
    return count > 0
