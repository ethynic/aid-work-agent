"""本地工具写动作许可（write-authorize，§5.2 / R8 / R9）

不可消除的不确定窗口 → 短期一次性许可：在最后一次目标复验后、输入/回车前申请。
服务器在**同一事务**中验证 claim/device、active revision epoch、当前 delivery 未获
许可、取消状态、deadline 和配额，保守预留配额并返回 permit_id/短 deadline。

锁顺序：task subject（FOR UPDATE，与发布/暂停互斥——许可事务同时锁定 subject/epoch，
避免检查后授权漂移）→ invocation → delivery → quota buckets（R9 固定 scope 顺序）。

许可发出后将该 delivery 标为 may_have_started（旧租约过期也不重新分配该条）。
permit token 明文只在签发响应中返回一次，库里只存 hash。
"""

import secrets
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from loguru import logger
from psycopg2.extras import Json

from src.db.database import get_db_connection
from src.desktop_automation import audit, deliveries as da_deliveries, quota, subjects
from src.desktop_automation.adapters import AdapterContext, TrustedAdapterRegistry
from src.desktop_automation.constants import (
    BUSINESS_KIND_DESKTOP_AUTOMATION,
    EFFECT_NONE,
    OPERATION_PROTOCOL_V2,
    PHASE_PREPARED,
    TASK_STATUS_ACTIVE,
)
from src.local_tools.security import sha256_hex

PERMIT_TTL_SECONDS = 120  # 许可短有效期（默认；实际取 min(TTL, invocation deadline, claim lease))


class PermitError(Exception):
    """许可拒绝（code 对应 HTTP status；message 中文说明）"""

    def __init__(self, code: str, http_status: int, message: str):
        self.code = code
        self.http_status = http_status
        self.message = message
        super().__init__(f"{code}: {message}")


def generate_permit_token() -> str:
    """一次性许可 token（hex 64 字符，明文仅签发响应返回）"""
    return secrets.token_hex(32)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def write_authorize(
    *,
    tenant_id: str,
    device_id: str,
    invocation_id: str,
    claim_token_hash: str,
    request_id: str,
    target_version: Optional[str],
    payload_hash: Optional[str],
    now: Optional[datetime] = None,
    ttl_seconds: int = PERMIT_TTL_SECONDS,
) -> Dict[str, Any]:
    """签发写动作许可（单事务校验链）。成功返回 {permit_id, permit_token, deadline_at}。

    校验链（任一失败整体回滚，不产生半预留）：
    设备/claim → 租约/state=running/未取消 → business_kind → deadline → v2 协议与
    request_id/目标绑定 → subject 状态/epoch（适配器授权前后皆锁定）→ 适配器
    authorize_operation → R9 quota 预留 → 幂等（同 delivery 已有 issued/consumed → 409）
    → INSERT permit → delivery 置 may_have_started → audit。
    """
    now = _aware(now or datetime.now(timezone.utc))
    with get_db_connection() as conn:
        cursor = conn.cursor()

        # 1) invocation 行锁 + 设备/claim/状态链
        #    租约/截止/许可 deadline 上限全部在 SQL 侧计算：lease_expires_at 是无时区
        #    TIMESTAMP（既有列，R7 不动），客户端按 UTC 解释会因会话时区错判——SQL 内
        #    与 NOW() 同一会话时区比较才正确；LEAST 对 naive/timestamptz 混合按会话时区提升。
        cursor.execute(
            """
            SELECT *,
                   (lease_expires_at IS NOT NULL AND lease_expires_at < NOW()) AS lease_expired,
                   (deadline_at IS NOT NULL AND deadline_at < NOW()) AS deadline_expired,
                   LEAST(
                       NOW() + (%s * INTERVAL '1 second'),
                       COALESCE(deadline_at, NOW() + INTERVAL '1000 years'),
                       COALESCE(lease_expires_at, NOW() + INTERVAL '1000 years')
                   ) AS permit_deadline
            FROM local_tool_invocations
            WHERE id = %s AND tenant_id = %s
            FOR UPDATE
            """,
            (ttl_seconds, invocation_id, tenant_id),
        )
        inv = cursor.fetchone()
        if inv is None or inv["claim_token_hash"] != claim_token_hash:
            conn.rollback()
            raise PermitError("CLAIM_MISMATCH", 404, "invocation 不存在或 claim token 不匹配")
        if str(inv["device_id"]) != str(device_id):
            conn.rollback()
            raise PermitError("DEVICE_MISMATCH", 403, "invocation 不属于当前设备")
        if inv["business_kind"] != BUSINESS_KIND_DESKTOP_AUTOMATION:
            # R8：只对 business_kind='desktop_automation' 的 v2 invocation 生效；旧 invocation 409
            conn.rollback()
            raise PermitError(
                "NOT_DESKTOP_AUTOMATION", 409, "仅桌面自动任务 v2 invocation 支持写动作许可"
            )
        if inv["state"] != "running":
            state = inv["state"]
            conn.rollback()
            if state == "cancel_requested":
                raise PermitError("INVOCATION_CANCELLED", 409, "invocation 已请求取消")
            raise PermitError("INVOCATION_NOT_RUNNING", 409, f"invocation 状态不允许: {state}")
        if inv["lease_expired"]:
            conn.rollback()
            raise PermitError("LEASE_EXPIRED", 409, "设备租约已过期，请重新领取")

        args = inv["arguments_json"] or {}
        if args.get("protocol_version") != OPERATION_PROTOCOL_V2:
            conn.rollback()
            raise PermitError("NOT_V2_OPERATION", 409, "非 v2 操作描述，不支持写动作许可")
        if args.get("request_id") != request_id:
            conn.rollback()
            raise PermitError("REQUEST_ID_MISMATCH", 409, "request_id 与 invocation 不匹配")
        # 许可绑定 target_version/payload_hash（防替换目标；任一变化拒绝，§6）
        if target_version is not None and target_version != args.get("target_version"):
            conn.rollback()
            raise PermitError("TARGET_VERSION_MISMATCH", 409, "target_version 与 invocation 不匹配")
        if payload_hash is not None and payload_hash != args.get("payload_hash"):
            conn.rollback()
            raise PermitError("PAYLOAD_HASH_MISMATCH", 409, "payload_hash 与 invocation 不匹配")
        if inv["deadline_expired"]:
            conn.rollback()
            raise PermitError("DEADLINE_EXCEEDED", 409, "操作截止时间已过")

        # 许可 deadline = min(TTL, invocation 截止, claim 租约)；SQL 侧算（lease 为会话时区 naive）
        deadline = _aware(inv["permit_deadline"])
        if deadline <= now:
            conn.rollback()
            raise PermitError("DEADLINE_EXCEEDED", 409, "操作截止时间已过")

        # 2) task subject 锁定（许可事务同时锁定 subject/epoch，避免检查后授权漂移）。
        #    business_ref 缺 scenario_key/task_ref 一律 409（P1-5：fail-closed，不无授权放行）
        business_ref = inv["business_ref"] or {}
        scenario_key = business_ref.get("scenario_key") or ""
        task_ref = business_ref.get("task_ref") or ""
        delivery_id = business_ref.get("delivery_id") or ""
        if not delivery_id:
            conn.rollback()
            raise PermitError("DELIVERY_BINDING_MISSING", 409, "invocation 未绑定 delivery")
        if not scenario_key or not task_ref:
            conn.rollback()
            raise PermitError(
                "SCENARIO_BINDING_MISSING", 409, "invocation 未绑定 scenario/task，拒绝签发许可"
            )
        task = subjects.lock_task_subject(cursor, tenant_id, scenario_key, task_ref, skip_locked=False)
        if task is None or task["status"] != TASK_STATUS_ACTIVE:
            conn.rollback()
            raise PermitError("TASK_NOT_ACTIVE", 409, "任务已暂停或不存在，拒绝授权")
        if task["active_revision_ref"] != args.get("authorization_revision"):
            conn.rollback()
            raise PermitError("REVISION_STALE", 409, "任务版本已切换，拒绝授权")
        inv_epoch = args.get("authorization_epoch")
        if inv_epoch is not None and task["authorization_epoch"] != inv_epoch:
            conn.rollback()
            raise PermitError("AUTHORIZATION_EPOCH_STALE", 409, "授权 epoch 已失效（任务已暂停/重发布），拒绝授权")

        # 3) delivery 行锁 + 幂等拒绝重复许可
        delivery = da_deliveries.lock_delivery(cursor, delivery_id, tenant_id)
        if delivery is None:
            conn.rollback()
            raise PermitError("DELIVERY_NOT_FOUND", 404, "delivery 不存在")
        cursor.execute(
            """
            SELECT id, state FROM local_tool_operation_permits
            WHERE tenant_id = %s AND delivery_id = %s AND state IN ('issued', 'consumed')
            LIMIT 1
            """,
            (tenant_id, delivery_id),
        )
        dup = cursor.fetchone()
        if dup is not None:
            conn.rollback()
            raise PermitError(
                "PERMIT_ALREADY_ISSUED", 409, "该 delivery 已持有许可（一次性），拒绝重复签发"
            )

        # 4) 适配器场景授权（epoch/target_version/payload_hash）+ R9 quota 预留
        #    （scenario_key 上方已强制非空，fail-closed）
        adapter = TrustedAdapterRegistry.get(scenario_key)
        if adapter is None:
            conn.rollback()
            raise PermitError("SCENARIO_NOT_REGISTERED", 409, f"场景 {scenario_key} 未注册受信适配器")
        decision = adapter.authorize_operation(
            AdapterContext(
                    tenant_id=tenant_id,
                    user_id=str(inv["user_id"]),
                    scenario_key=scenario_key,
                    task_ref=task_ref,
                    revision_ref=str(args.get("authorization_revision") or ""),
                ),
                operation=str(args.get("operation") or ""),
                target_ref=args.get("target_ref"),
                target_version=args.get("target_version"),
                payload_hash=args.get("payload_hash"),
                authorization_revision=args.get("authorization_revision"),
                authorization_epoch=args.get("authorization_epoch"),
            )
        if not decision.allowed:
            conn.rollback()
            raise PermitError("ADAPTER_DENIED", 403, f"场景授权拒绝: {decision.reason}")
        scopes = [
            quota.QuotaScope(
                scope_type=s.scope_type, scope_id=s.scope_id,
                limit_count=s.limit_count, window_seconds=s.window_seconds,
            )
            for s in decision.quota_scopes
        ]
        try:
            reservations = quota.reserve_quota(cursor, tenant_id, scopes, now)
        except quota.QuotaExhausted as e:
            conn.rollback()
            raise PermitError(
                "QUOTA_EXCEEDED", 409,
                f"额度不足（{e.scope.scope_type}/{e.scope.scope_id}），许可事务已回滚",
            )

        # 5) 签发许可（token 明文仅此一次返回）
        permit_token = generate_permit_token()
        cursor.execute(
            """
            INSERT INTO local_tool_operation_permits
                (tenant_id, user_id, invocation_id, device_id, claim_token_hash, request_id,
                 delivery_id, operation, target_ref, target_version, payload_hash,
                 authorization_revision, authorization_epoch, resource_key,
                 quota_reservation, state, permit_token_hash, deadline)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    'issued', %s, %s)
            RETURNING id
            """,
            (
                tenant_id, str(inv["user_id"]), str(inv["id"]), str(device_id),
                claim_token_hash, request_id, delivery_id,
                args.get("operation"), args.get("target_ref"), args.get("target_version"),
                args.get("payload_hash"), args.get("authorization_revision"),
                args.get("authorization_epoch"), args.get("resource_key"),
                Json([r.as_dict() for r in reservations]) if reservations else None,
                sha256_hex(permit_token), deadline,
            ),
        )
        permit_id = str(cursor.fetchone()["id"])

        # 6) delivery 置 may_have_started（旧租约过期也不重新分配该条）+ invocation 回写
        da_deliveries.mark_may_have_started(cursor, delivery_id, tenant_id)
        cursor.execute(
            "UPDATE local_tool_invocations SET write_phase = 'may_have_started' "
            "WHERE id = %s AND tenant_id = %s",
            (str(inv["id"]), tenant_id),
        )
        audit.insert_audit(
            cursor, tenant_id, "permit_issued", "permit", permit_id,
            user_id=str(inv["user_id"]), scenario_key=scenario_key,
            detail={
                "invocation_id": str(inv["id"]), "delivery_id": delivery_id,
                "operation": args.get("operation"), "deadline": deadline.isoformat(),
                "quota_scopes": [r.as_dict() for r in reservations],
            },
        )
        conn.commit()

    logger.info(
        f"后端日志：写动作许可签发 tenant={tenant_id} invocation={invocation_id} "
        f"delivery={delivery_id} permit={permit_id} deadline={deadline.isoformat()}"
    )
    return {
        "permit_id": permit_id,
        "permit_token": permit_token,
        "deadline_at": deadline,
    }


def settle_permit_on_result(
    cursor,
    permit_id: str,
    tenant_id: str,
    *,
    effect: str,
    phase: Optional[str] = None,
    user_id: Optional[str] = None,
) -> Optional[str]:
    """operation-result 到达时的许可结算（R22）：permit 无论 issued/expired 均按 effect
    结算，幂等恰好一次。

    - effect=none 且 phase ∈ {None, prepared}（可信未执行）→ release（reserved-1）；
    - 其余结果（applied/unknown/verified 等）→ settle（reserved→used，保守占用）。

    原子判定（P1-3）：UPDATE ... WHERE state IN ('issued','expired') RETURNING 拿行锁
    与预留凭据，再按 effect 落账——与 expire_permits 清扫互斥（清扫只置 expired 不动
    quota），消除 reserved 二次扣减/used 多计的竞态。幂等：已 consumed 返回 None，
    不重复结算。
    """
    release = effect == EFFECT_NONE and phase in (None, PHASE_PREPARED)
    cursor.execute(
        """
        UPDATE local_tool_operation_permits
        SET state = 'consumed', consumed_at = NOW()
        WHERE id = %s AND tenant_id = %s AND state IN ('issued', 'expired')
        RETURNING quota_reservation, user_id
        """,
        (permit_id, tenant_id),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    reservations = row["quota_reservation"] or []
    if release:
        quota.release_quota(cursor, tenant_id, reservations)
    else:
        quota.settle_quota(cursor, tenant_id, reservations)
    audit.insert_audit(
        cursor, tenant_id, "permit_consumed", "permit", permit_id,
        user_id=user_id or row["user_id"],
        detail={
            "quota_released" if release else "quota_settled": len(reservations),
        },
    )
    return "released" if release else "settled"


def expire_permits(now: Optional[datetime] = None) -> int:
    """过期清扫（R22）：issued 且 deadline < now → expired，**不释放预留**。

    额度释放仅两条路：①operation-result effect=none 且 phase=prepared（可信未执行）
    → release；②其余结果 → settle reserved→used（含 expired 状态的迟到结算，幂等恰好
    一次）。彻底遗弃（永无回执）的预留随 quota 窗口翻页自然失效；Runtime 单调时钟
    限制本地有效期，进程重启后旧 permit 一律失效（P1-C 客户端配合）。
    """
    now = _aware(now or datetime.now(timezone.utc))
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, tenant_id, user_id
            FROM local_tool_operation_permits
            WHERE state = 'issued' AND deadline < %s
            ORDER BY deadline
            LIMIT 500
            FOR UPDATE SKIP LOCKED
            """,
            (now,),
        )
        rows = [dict(r) for r in cursor.fetchall()]
        expired = 0
        for row in rows:
            cursor.execute(
                """
                UPDATE local_tool_operation_permits
                SET state = 'expired', expired_at = NOW()
                WHERE id = %s AND tenant_id = %s AND state = 'issued'
                """,
                (str(row["id"]), row["tenant_id"]),
            )
            audit.insert_audit(
                cursor, row["tenant_id"], "permit_expired", "permit", str(row["id"]),
                user_id=row.get("user_id"),
                detail={"quota_released": 0, "reservation_held": True},
            )
            expired += 1
        conn.commit()
        if expired:
            logger.warning(
                f"后端日志：写动作许可过期清扫 {expired} 条（预留保留，待迟到结算/窗口翻页失效）"
            )
        return expired
