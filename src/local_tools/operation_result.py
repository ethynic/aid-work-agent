"""v2 operation-result 落账（R8 / §5.3 / R10）

Runtime 终态先写本地 result outbox 再经原 invocation/claim 身份回传；云端持久 ACK；
重复回执幂等（2xx，不重复账单）。迟到（attempt 已终态）不改判、不重复结算额度，
按 R28 持久追加证据登记 + 完整回执审计。

计费：v2 operation-result 路径默认 0 价（R2：不新增按次费），与 write_result 的
按次计费语义隔离——本模块直接落终态，不走计费分支。

effect/phase 落账映射（R10：applied 还必须有本次验证证据才成功，unknown 优先）：
- (applied, verified)                 → delivery succeeded（R27：evidence_ref 经适配器
                                         validate_evidence + 绑定交叉核对 + evidence 表
                                         ON CONFLICT 仲裁登记，任一环节失败收敛 unknown）
- (applied, prepared/may_have_started)→ delivery unknown（无写后证据，不可判成功）
- (none, *)                           → delivery failed（safe_to_retry 时可另建 attempt）
- (unknown, *) / phase=unknown        → delivery unknown（待核对，不重发）

R21：绑定校验（claim/request_id/permit_id/permit_token）通过即接纳——不看 permit 状态，
过期许可的迟到 applied/verified/unknown 一律落账 + audit permit_expired_late
（403 仅 PERMIT_BINDING_INVALID 留给真实绑定不匹配）；额度按 R22 结算恰好一次。

R27 证据判定链（applied+verified，同一结果事务内）：
①格式预检（非空、≤512）→ ②适配器 validate_evidence（存在性与归属，受信注册）→
③绑定交叉核对（invocation.device_id/attempt.request_id/delivery.target_ref/
delivery.payload_hash 与 invocation 冻结描述一致）→ ④desktop_automation_evidence
INSERT ON CONFLICT 仲裁（未插入即比对既有行绑定：同操作幂等放行/他操作
bound_to_other/绑定漂移 mismatch）；DB 唯一索引杜绝 check-then-insert 竞态。
任一环节失败 → unknown + 停后续 + audit evidence_invalid
（reason: empty/too_long/adapter_rejected/mismatch/bound_to_other）。

R28 迟到分支（attempt 已终态）：完整绑定校验（claim/request_id；附 permit 则验绑定，
不匹配 403）→ 携带 applied/verified 证据时按 R27 ②③④登记 → audit operation_result_late
持久化完整受控回执字段（evidence_ref/permit_id/request_id/effect/phase/safe_to_retry/
code/message/received_at）→ 提交成功才 ACK；原始判定/effect/额度/计费不动。
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from loguru import logger
from psycopg2.extras import Json

from src.db.database import get_db_connection
from src.desktop_automation import attempts as da_attempts
from src.desktop_automation import audit, deliveries as da_deliveries
from src.desktop_automation.adapters import EvidenceContext, TrustedAdapterRegistry
from src.desktop_automation.constants import (
    BUSINESS_KIND_DESKTOP_AUTOMATION,
    DELIVERY_EFFECTS,
    EFFECT_APPLIED,
    EFFECT_NONE,
    EFFECT_UNKNOWN,
    OPERATION_PHASES,
    PHASE_VERIFIED,
)
from src.local_tools import permits
from src.local_tools.security import sha256_hex

EVIDENCE_REF_MAX_LENGTH = 512


class OperationResultError(Exception):
    def __init__(self, code: str, http_status: int, message: str):
        self.code = code
        self.http_status = http_status
        self.message = message
        super().__init__(f"{code}: {message}")


def _map_delivery_outcome(effect: str, phase: Optional[str]) -> Dict[str, str]:
    """effect/phase → delivery {state, effect, phase}（映射表见模块 docstring）"""
    if effect == EFFECT_UNKNOWN or phase == "unknown":
        return {"state": "unknown", "effect": EFFECT_UNKNOWN, "phase": "unknown"}
    if effect == EFFECT_APPLIED:
        if phase == PHASE_VERIFIED:
            return {"state": "succeeded", "effect": EFFECT_APPLIED, "phase": PHASE_VERIFIED}
        return {"state": "unknown", "effect": EFFECT_UNKNOWN, "phase": "unknown"}
    # effect == none
    return {"state": "failed", "effect": EFFECT_NONE, "phase": phase or "prepared"}


def _evidence_format_reason(evidence_ref: Optional[str]) -> Optional[str]:
    """R27 ①：格式预检——非空、≤512（废除旧「查重」实现，防复用由 evidence 表仲裁）"""
    if not evidence_ref:
        return "empty"
    if len(evidence_ref) > EVIDENCE_REF_MAX_LENGTH:
        return "too_long"
    return None


def _check_evidence_and_register(
    cursor,
    *,
    tenant_id: str,
    user_id: Optional[str],
    scenario_key: str,
    evidence_ref: Optional[str],
    invocation: Dict[str, Any],
    attempt: Dict[str, Any],
    delivery: Optional[Dict[str, Any]],
    args: Dict[str, Any],
    request_id: str,
    effect: str,
    phase: Optional[str],
) -> Optional[str]:
    """R27 判定链（applied+verified 必经，结果事务内执行）：

    ① 格式预检（非空/≤512）→ ② 适配器 validate_evidence（受信注册，校验证据
    存在性与归属——「不存在的证据」「其他操作未登记证据」在此被拒）→
    ③ 绑定交叉核对（attempt.request_id/delivery.target_ref/delivery.payload_hash
    与 invocation 冻结描述一致）→ ④ INSERT ON CONFLICT (tenant_id, evidence_ref)
    仲裁并发：未插入即比对既有行绑定——同操作幂等放行（None）、他操作
    bound_to_other、同操作但绑定漂移 mismatch。DB 唯一索引杜绝 check-then-insert 竞态。

    返回 None 表示证据有效且已登记（或同操作幂等复用）；否则返回失效原因。
    """
    reason = _evidence_format_reason(evidence_ref)
    if reason is not None:
        return reason
    # ② 适配器校验（scenario_key 缺失/未注册 → fail-closed 拒绝）
    adapter = TrustedAdapterRegistry.get(scenario_key) if scenario_key else None
    if adapter is None or not adapter.validate_evidence(
        EvidenceContext(
            tenant_id=tenant_id,
            scenario_key=scenario_key,
            request_id=request_id,
            evidence_ref=evidence_ref,
        )
    ):
        return "adapter_rejected"
    # ③ 绑定交叉核对：证据登记的操作上下文必须与 invocation 冻结描述一致（防篡改）
    if str(attempt["request_id"]) != request_id:
        return "mismatch"
    if delivery is None:
        return "mismatch"
    if (
        delivery.get("target_ref") != args.get("target_ref")
        or delivery.get("payload_hash") != args.get("payload_hash")
    ):
        return "mismatch"
    # ④ 事务级防复用：唯一索引仲裁并发，冲突败者走绑定比对
    device_id = str(invocation["device_id"])
    invocation_id = str(invocation["id"])
    attempt_id = str(attempt["id"])
    target_ref = delivery.get("target_ref")
    payload_hash = delivery.get("payload_hash")
    cursor.execute(
        """
        INSERT INTO desktop_automation_evidence
            (tenant_id, user_id, evidence_ref, invocation_id, attempt_id, device_id,
             request_id, target_ref, payload_hash, effect, phase)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (tenant_id, evidence_ref) DO NOTHING
        RETURNING id
        """,
        (
            tenant_id, user_id, evidence_ref, invocation_id, attempt_id, device_id,
            request_id, target_ref, payload_hash, effect, phase,
        ),
    )
    if cursor.fetchone() is not None:
        return None
    cursor.execute(
        """
        SELECT invocation_id, attempt_id, device_id, request_id, target_ref, payload_hash
        FROM desktop_automation_evidence
        WHERE tenant_id = %s AND evidence_ref = %s
        """,
        (tenant_id, evidence_ref),
    )
    row = cursor.fetchone()
    if row is None:
        return "mismatch"  # 防御：冲突行不可见（理论不可达），fail-closed
    same_operation = (
        str(row["attempt_id"]) == attempt_id
        and str(row["invocation_id"]) == invocation_id
        and str(row["device_id"]) == device_id
        and row["request_id"] == request_id
    )
    if same_operation:
        if row["target_ref"] == target_ref and row["payload_hash"] == payload_hash:
            return None  # 同操作幂等放行（重复回执/迟到补登）
        return "mismatch"
    return "bound_to_other"


def _load_permit_row(cursor, tenant_id: str, permit_id: str) -> Optional[Dict[str, Any]]:
    cursor.execute(
        """
        SELECT state, invocation_id, permit_token_hash
        FROM local_tool_operation_permits
        WHERE id = %s AND tenant_id = %s
        """,
        (permit_id, tenant_id),
    )
    row = cursor.fetchone()
    return dict(row) if row is not None else None


def _permit_binding_invalid(
    permit_row: Optional[Dict[str, Any]], invocation_id: str, permit_token: Optional[str]
) -> bool:
    """R21/R28：permit 绑定 invocation/token 校验（状态不参与——过期也接纳）"""
    return (
        permit_row is None
        or str(permit_row["invocation_id"]) != str(invocation_id)
        or (
            permit_token is not None
            and permit_row["permit_token_hash"] != sha256_hex(permit_token)
        )
    )


def apply_operation_result(
    *,
    tenant_id: str,
    device_id: str,
    invocation_id: str,
    claim_token_hash: str,
    request_id: str,
    effect: str,
    phase: Optional[str],
    evidence_ref: Optional[str] = None,
    safe_to_retry: Optional[bool] = None,
    code: Optional[str] = None,
    message: Optional[str] = None,
    permit_id: Optional[str] = None,
    permit_token: Optional[str] = None,
) -> Dict[str, Any]:
    """v2 结果落账（单事务）：校验 claim/request_id/permit 绑定 → attempt 写结果 →
    delivery 推进 → invocation 终态 → 提交后 runs 聚合（executor.advance_run）。

    返回 {acked, late, state, effect, run_state}；迟到重复回执 acked=True late=True。
    """
    if effect not in DELIVERY_EFFECTS:
        raise OperationResultError("INVALID_EFFECT", 422, f"非法 effect: {effect}")
    if phase is not None and phase not in OPERATION_PHASES:
        raise OperationResultError("INVALID_PHASE", 422, f"非法 phase: {phase}")

    run_id: Optional[str] = None
    late = False
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM local_tool_invocations WHERE id = %s AND tenant_id = %s FOR UPDATE",
            (invocation_id, tenant_id),
        )
        inv = cursor.fetchone()
        if inv is None or inv["claim_token_hash"] != claim_token_hash:
            conn.rollback()
            raise OperationResultError("CLAIM_MISMATCH", 404, "invocation 不存在或 claim token 不匹配")
        if str(inv["device_id"]) != str(device_id):
            conn.rollback()
            raise OperationResultError("DEVICE_MISMATCH", 403, "invocation 不属于当前设备")
        if inv["business_kind"] != BUSINESS_KIND_DESKTOP_AUTOMATION:
            conn.rollback()
            raise OperationResultError(
                "NOT_DESKTOP_AUTOMATION", 409, "仅桌面自动任务 v2 invocation 支持该端点"
            )
        args = inv["arguments_json"] or {}
        if args.get("request_id") != request_id:
            conn.rollback()
            raise OperationResultError("REQUEST_ID_MISMATCH", 409, "request_id 与 invocation 不匹配")

        # 同一事务内读 attempt（P1-6：持 invocation 行锁时不再嵌套取池连接）
        attempt = da_attempts.get_attempt_by_invocation_on(cursor, str(inv["id"]), tenant_id)
        if attempt is None:
            conn.rollback()
            raise OperationResultError("ATTEMPT_NOT_FOUND", 404, "invocation 未绑定 attempt")

        delivery_id = str(attempt["delivery_id"])
        run_id = str(attempt["run_id"]) if attempt.get("run_id") else None

        if attempt.get("finished_at") is not None:
            # R28：迟到（attempt 已终态）——完整绑定校验（claim/request_id 上方已验；
            # 附 permit 则验绑定，不匹配 403）→ 携带 applied/verified 证据时按 R27
            # 判定链登记 → audit operation_result_late 持久化完整受控回执字段 →
            # 提交成功才 ACK；原始判定/effect/额度/计费不动。
            if permit_id is not None:
                permit_row = _load_permit_row(cursor, tenant_id, permit_id)
                if _permit_binding_invalid(permit_row, str(inv["id"]), permit_token):
                    conn.rollback()
                    raise OperationResultError(
                        "PERMIT_BINDING_INVALID", 403, "许可绑定校验失败"
                    )
            evidence_reason = None
            has_evidence_receipt = effect == EFFECT_APPLIED and phase == PHASE_VERIFIED
            if has_evidence_receipt:
                business_ref = inv["business_ref"] or {}
                evidence_reason = _check_evidence_and_register(
                    cursor,
                    tenant_id=tenant_id,
                    user_id=str(inv["user_id"]),
                    scenario_key=str(business_ref.get("scenario_key") or ""),
                    evidence_ref=evidence_ref,
                    invocation=inv,
                    attempt=attempt,
                    delivery=da_deliveries.lock_delivery(cursor, delivery_id, tenant_id),
                    args=args,
                    request_id=request_id,
                    effect=effect,
                    phase=phase,
                )
                if evidence_reason is not None:
                    audit.insert_audit(
                        cursor, tenant_id, "evidence_invalid", "attempt", str(attempt["id"]),
                        user_id=str(inv["user_id"]),
                        detail={
                            "invocation_id": str(inv["id"]), "reason": evidence_reason,
                            "late": True, "evidence_ref": evidence_ref,
                        },
                    )
            audit.insert_audit(
                cursor, tenant_id, "operation_result_late", "attempt", str(attempt["id"]),
                user_id=str(inv["user_id"]),
                detail={
                    # R28：完整受控回执字段（对账/证据追溯）
                    "invocation_id": str(inv["id"]), "request_id": request_id,
                    "effect": effect, "phase": phase, "evidence_ref": evidence_ref,
                    "permit_id": permit_id, "safe_to_retry": safe_to_retry,
                    "code": code, "message": message,
                    "received_at": datetime.now(timezone.utc).isoformat(),
                    "evidence_registered": has_evidence_receipt and evidence_reason is None,
                    "evidence_invalid": evidence_reason,
                    "persisted_effect": attempt.get("effect"),
                    "persisted_phase": attempt.get("phase"),
                },
            )
            conn.commit()
            logger.warning(
                f"后端日志：v2 operation-result 迟到（审计+证据追加，不改判）tenant={tenant_id} "
                f"invocation={invocation_id} attempt={attempt['id']}"
            )
            return {
                "acked": True, "late": True,
                "state": inv["state"], "effect": inv["effect"],
                "run_state": None,
            }

        # permit 绑定校验（§6：绑定 invocation/device/claim/request_id/permit；任一变化拒绝）
        if permit_id is not None:
            permit_row = _load_permit_row(cursor, tenant_id, permit_id)
            if _permit_binding_invalid(permit_row, str(inv["id"]), permit_token):
                conn.rollback()
                raise OperationResultError("PERMIT_BINDING_INVALID", 403, "许可绑定校验失败")
            if permit_row["state"] == "expired":
                # R21：绑定校验通过即接纳，不看 permit 状态——过期许可的迟到回执
                # （结果 outbox 重投/断网恢复）applied/verified/unknown 一律落账，
                # 403 仅留给真实绑定不匹配；额度按 R22 迟到结算恰好一次。
                audit.insert_audit(
                    cursor, tenant_id, "permit_expired_late", "attempt", str(attempt["id"]),
                    user_id=str(inv["user_id"]),
                    detail={
                        "invocation_id": str(inv["id"]), "permit_id": permit_id,
                        "reported_effect": effect, "reported_phase": phase,
                    },
                )
        elif effect == EFFECT_APPLIED and phase == PHASE_VERIFIED:
            # 受控写动作必须以本地已校验 permit 执行（设计 §3）
            conn.rollback()
            raise OperationResultError(
                "PERMIT_REQUIRED", 409, "applied 结果必须携带有效 permit"
            )

        # R27：applied+verified 判定链——适配器校验 → 绑定交叉核对 → evidence 表
        # ON CONFLICT 仲裁登记；失败 → delivery 收敛 unknown（机器证据）、停止后续条目
        # （unknown 传播），audit evidence_invalid；attempt 保留原始上报值。
        evidence_reason: Optional[str] = None
        if effect == EFFECT_APPLIED and phase == PHASE_VERIFIED:
            business_ref = inv["business_ref"] or {}
            evidence_reason = _check_evidence_and_register(
                cursor,
                tenant_id=tenant_id,
                user_id=str(inv["user_id"]),
                scenario_key=str(business_ref.get("scenario_key") or ""),
                evidence_ref=evidence_ref,
                invocation=inv,
                attempt=attempt,
                delivery=da_deliveries.lock_delivery(cursor, delivery_id, tenant_id),
                args=args,
                request_id=request_id,
                effect=effect,
                phase=phase,
            )
            if evidence_reason is not None:
                audit.insert_audit(
                    cursor, tenant_id, "evidence_invalid", "attempt", str(attempt["id"]),
                    user_id=str(inv["user_id"]),
                    detail={
                        "invocation_id": str(inv["id"]), "permit_id": permit_id,
                        "reason": evidence_reason, "evidence_ref": evidence_ref,
                        "reported_effect": effect, "reported_phase": phase,
                    },
                )

        outcome = _map_delivery_outcome(effect, phase)
        if evidence_reason is not None:
            outcome = {"state": "unknown", "effect": EFFECT_UNKNOWN, "phase": "unknown"}

        # attempt 写结果（原始 effect/phase 与人工业务判定分别存储）
        da_attempts.finish_attempt(
            cursor, str(attempt["id"]), tenant_id,
            effect=effect, phase=phase, safe_to_retry=safe_to_retry,
            evidence_ref=evidence_ref,
            detail={"code": code, "message": message, "permit_id": permit_id},
        )
        # delivery 推进
        da_deliveries.apply_operation_outcome(
            cursor, delivery_id, tenant_id,
            state=outcome["state"], effect=outcome["effect"], phase=outcome["phase"],
        )
        # permit 结算（R22）：无论 issued/expired 按原始 effect 结算（none+prepared→release；
        # 其余→settle 保守占用），幂等恰好一次
        if permit_id is not None:
            permits.settle_permit_on_result(
                cursor, permit_id, tenant_id,
                effect=effect, phase=phase, user_id=str(inv["user_id"]),
            )
        # invocation 终态（v2 路径默认 0 价计费，不走 write_result 按次计费分支）
        inv_state = "succeeded" if outcome["state"] == "succeeded" else (
            "unknown" if outcome["state"] == "unknown" else "failed"
        )
        cursor.execute(
            """
            UPDATE local_tool_invocations
            SET state = %s,
                effect = %s,
                result_json = %s,
                error_code = %s,
                error_message = %s,
                finished_at = NOW(),
                lease_expires_at = NULL
            WHERE id = %s AND tenant_id = %s
            """,
            (
                inv_state, outcome["effect"],
                Json({
                    "success": inv_state == "succeeded",
                    "code": code, "message": message,
                    "data": {
                        "request_id": request_id, "effect": effect, "phase": phase,
                        "safe_to_retry": safe_to_retry, "evidence_ref": evidence_ref,
                        "permit_id": permit_id,
                    },
                    "retryable": bool(safe_to_retry),
                }),
                None if inv_state == "succeeded" else (code or None),
                None if inv_state == "succeeded" else (message or None),
                str(inv["id"]),
                tenant_id,
            ),
        )
        audit.insert_audit(
            cursor, tenant_id, "operation_result", "attempt", str(attempt["id"]),
            user_id=str(inv["user_id"]),
            detail={
                "invocation_id": str(inv["id"]), "delivery_id": delivery_id,
                "effect": effect, "phase": phase, "mapped_state": outcome["state"],
                "permit_id": permit_id, "evidence_invalid": evidence_reason,
            },
        )
        conn.commit()

    # 提交后聚合推进（独立事务：unknown 停后续 / 全部终态落 §5.4 终态）
    run_state = None
    if run_id:
        from src.desktop_automation import executor

        run_state = executor.advance_run(run_id, tenant_id)

    logger.info(
        f"后端日志：v2 operation-result 落账 tenant={tenant_id} invocation={invocation_id} "
        f"state={inv_state} effect={outcome['effect']} run_state={run_state}"
    )
    return {
        "acked": True, "late": late,
        "state": inv_state, "effect": outcome["effect"],
        "run_state": run_state,
    }
