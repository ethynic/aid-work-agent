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
- (applied, submitted) → succeeded only for a frozen name-chat submission policy
  and bound authenticated command journal; phase remains submitted, not delivered.
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
    PHASE_SUBMITTED,
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
        if phase in (PHASE_VERIFIED, PHASE_SUBMITTED):
            return {"state": "succeeded", "effect": EFFECT_APPLIED, "phase": phase}
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
    if phase == PHASE_SUBMITTED:
        # B1.2（九处 #9）：submitted 接纳白名单由场景描述器 receipt_policy +
        # operation_descriptor 提供（微信逐值一致：submission/weixin_name/
        # weixin_message_send_v2/session_task）；描述器未注册 → fail-closed
        # 拒绝（与原"非微信场景必拒"同语义）。
        from src.session_tasks.scenario_descriptor import get_descriptor

        policy_descriptor = get_descriptor(scenario_key) if scenario_key else None
        policy = getattr(policy_descriptor, "receipt_policy", None) if policy_descriptor is not None else None
        op_descriptor = getattr(policy_descriptor, "operation_descriptor", None) if policy_descriptor is not None else None
        if (policy is None
                or op_descriptor is None
                or policy.get("mode") != "submission"
                or args.get("receipt_mode") != policy.get("mode")
                or args.get("receipt_context") != policy.get("context")
                or invocation.get("tool_name") != op_descriptor.get("operation")
                or invocation.get("execution_lane") != "session_task"):
            return "submission_not_authorized"
        validator = getattr(adapter, "validate_submission_evidence", None)
    else:
        validator = getattr(adapter, "validate_evidence", None)
    if validator is None or not validator(
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


def _locate_and_lock_binding(cursor, guard, tenant_id: str, inv: Dict[str, Any]) -> Dict[str, Any]:  # noqa: ANN001
    """B1.2 binding 定位（设计 §5.5.5 冻结流程，仅 binding_guard 场景）：

    无锁定位（受信 business_ref.task_ref 普通读 task 行，不锁）→ guard 锁场景
    binding 行（FOR UPDATE）→ **锁后重新读取并核对**（CR 阻断 8）：重读 task 行
    核对 conversation_binding_id 仍指向已锁 binding、tenant 归属不变、invocation
    冻结 scenario_key 与任务行一致。定位失败分类处置（CR 三审 P1-2）：
    - task 缺失 / task 无绑定 / binding 缺失 / guard 返回归属不符行 →
      **抛 OperationResultError 拒绝且不 ACK**（无受信 task_row 可升级，只审计
      后 ACK 会让任务无阻断无请求地保持 active）；
    - 重验不一致（改绑窗口 / 场景漂移）→ 返回 anomaly 走异常升级（阻断+控制
      请求+审计+ACK）。
    调用点在冻结锁序 invocation→attempt→delivery 之后（CR 阻断 1），binding→
    rate slot（结算 SAVEPOINT）最后。不从客户端回执参数接受 binding_id；定位
    阶段不锁 task（operation-result 不获取 subject/task 锁）。"""
    business_ref = inv["business_ref"] or {}
    task_ref = str(business_ref.get("task_ref") or business_ref.get("task_id") or "")
    if not task_ref:
        # 无受信 task_ref：拒绝且不 ACK（无定位链即无升级对象）
        raise OperationResultError(
            "BINDING_LOCATION_FAILED", 409, "结算定位失败：invocation 冻结链缺 task_ref"
        )
    cursor.execute(
        "SELECT id, tenant_id, conversation_binding_id, control_epoch "
        "FROM session_tasks WHERE tenant_id=%s AND id=%s",
        (tenant_id, task_ref),
    )
    task_row = cursor.fetchone()
    if task_row is None or not task_row["conversation_binding_id"]:
        # CR 三审 P1-2：拒绝且不 ACK——无受信 task_row 时升级无对象，只审计后
        # ACK 会让任务无阻断无请求地保持 active（Runtime outbox 重投保留事实）
        raise OperationResultError(
            "BINDING_LOCATION_FAILED", 409, "结算定位失败：task 缺失或未绑定场景绑定"
        )
    # P0-1（四审）：首锁前建 SAVEPOINT——重验不一致时 ROLLBACK 释放旧绑定锁，
    # 升级段按 binding id 排序重新加锁，消除交叉改绑 A→B/B→A 的 AB-BA 死锁
    cursor.execute("SAVEPOINT binding_locate")
    binding_row = guard.lock_binding(cursor, tenant_id, str(task_row["conversation_binding_id"]))
    if binding_row is None or (
        str(binding_row.get("tenant_id") or "") != tenant_id
        or str(binding_row.get("id") or "") != str(task_row["conversation_binding_id"])
    ):
        cursor.execute("ROLLBACK TO SAVEPOINT binding_locate")
        # CR 三审 P1-2：binding 被删除或 guard 返回归属不符行 → 拒绝且不 ACK
        raise OperationResultError(
            "BINDING_LOCATION_FAILED", 409, "结算定位失败：场景绑定缺失或归属不符"
        )
    # 锁后重验（CR 阻断 8 + 三审 P1-2 scenario 核对）：无锁读→加锁窗口内定位
    # 被并发改变/场景漂移 → anomaly（有受信 task_row 可升级）
    cursor.execute(
        "SELECT id, tenant_id, conversation_binding_id, control_epoch, scenario_key "
        "FROM session_tasks WHERE tenant_id=%s AND id=%s",
        (tenant_id, task_ref),
    )
    task_row_locked = cursor.fetchone()
    frozen_scenario = str(business_ref.get("scenario_key") or "")
    revalidated = (
        task_row_locked is not None
        and str(task_row_locked["conversation_binding_id"] or "") == str(binding_row["id"])
        and str(task_row_locked["tenant_id"]) == tenant_id
        and str(task_row_locked["id"]) == task_ref
        and (not frozen_scenario or str(task_row_locked["scenario_key"] or "") == frozen_scenario)
    )
    ctx: Dict[str, Any] = {"task_row": None, "binding_row": None, "anomaly": None}
    if not revalidated:
        # P0-1（四审）：定位链在无锁读→加锁窗口内被并发改变——回滚到 SAVEPOINT
        # 释放旧绑定锁（PG 对 SAVEPOINT 后获取的行锁随回滚释放），升级段按
        # 旧/新 binding id 排序重新加锁（与并发镜像事务同全序，无反向边）；
        # 再次变化由升级段的二次确认拒绝（无界重试禁止）。
        cursor.execute("ROLLBACK TO SAVEPOINT binding_locate")
        ctx["anomaly"] = "binding_location:revalidation_mismatch"
        ctx["task_row"] = dict(task_row_locked) if task_row_locked is not None else dict(task_row)
        ctx["stale_binding_id"] = str(binding_row["id"])
        return ctx
    cursor.execute("RELEASE SAVEPOINT binding_locate")
    ctx["task_row"] = dict(task_row_locked)
    ctx["binding_row"] = dict(binding_row)
    return ctx


def _is_controlled_code_local(value) -> bool:
    """受控码校验（与 decisions.V1.9 terminal reason 同口径）。"""
    import re as _re

    return isinstance(value, str) and bool(_re.fullmatch(r"^[a-z][a-z0-9_]{0,63}$", value))


def _settle_scenario_result(
    cursor, descriptor, guard, tenant_id: str, inv: Dict[str, Any], attempt: Dict[str, Any],  # noqa: ANN001
    delivery_id: str, request_id: str, effect: str, phase: Optional[str],
    evidence_reason: Optional[str], permit_id: Optional[str],
) -> Dict[str, Any]:
    """B1.2 场景结算（设计 §5.5.4 顺序 3–6；CR 阻断 9 结构化结果）：
    SAVEPOINT rate_settlement 包裹 adapter.settle_operation_result。

    返回 {"kind": "normal"}（结算成功，无升级）
      | {"kind": "anomaly_committed", "detail": str}（settle 返回
        {"status": "anomaly_committed", "reason": <非空受控码>}：补建/落账写入
        **保留**（不回滚），由调用方升级阻断+控制请求+审计）
      | {"kind": "error", "detail": str}（settle 抛异常或**返回未知值**：ROLLBACK
        TO SAVEPOINT 撤销本结算写入，原始回执照常持久化，由调用方升级异常）。

    CR 三审 P1-3：只有 None 与严格形态的 anomaly_committed 被识别；拼写错误/
    未知状态/非 dict 一律按未知返回值处理——回滚 SAVEPOINT 并升级，不得当
    normal 静默放行。

    微信 adapter settle 为 no-op 返回 None（normal）——SAVEPOINT 包裹 no-op 不改变
    任何行为；描述器未注册（纯底座场景）不进入本函数（现状零变化）。
    """
    business_ref = inv["business_ref"] or {}
    result_facts = {
        "tenant_id": tenant_id,
        "task_id": str(business_ref.get("task_ref") or business_ref.get("task_id") or ""),
        "invocation_id": str(inv["id"]),
        "delivery_id": str(delivery_id),
        "attempt_id": str(attempt["id"]),
        "request_id": request_id,
        "effect": effect,
        "phase": phase,
        "evidence_invalid": evidence_reason,
        "permit_id": permit_id,
    }
    cursor.execute("SAVEPOINT rate_settlement")
    try:
        settle_result = descriptor.adapter.settle_operation_result(cursor, result_facts)
    except Exception as exc:  # noqa: BLE001 结算失败：回滚到保存点，异常升级由调用方处理
        logger.warning(
            "后端日志：场景结算失败（ROLLBACK TO SAVEPOINT，照常接纳回执并升级异常）"
            f" tenant={tenant_id} invocation={inv['id']}: {exc!r}"
        )
        cursor.execute("ROLLBACK TO SAVEPOINT rate_settlement")
        return {"kind": "error", "detail": f"settlement_failed:{type(exc).__name__}"}
    if (
        isinstance(settle_result, dict)
        and settle_result.get("status") == "anomaly_committed"
        and isinstance(settle_result.get("reason"), str)
        and _is_controlled_code_local(settle_result["reason"])
    ):
        cursor.execute("RELEASE SAVEPOINT rate_settlement")
        # 补建写入保留（SAVEPOINT 已 RELEASE 不回滚），仅声明异常升级
        return {"kind": "anomaly_committed", "detail": settle_result["reason"]}
    if settle_result is not None:
        # CR 三审 P1-3：未知返回值（拼写错误/未知状态/非 dict/受控码不合法）
        # fail-closed——回滚本结算写入并按结算异常升级，不当 normal 静默放行。
        # 补齐5：审计只落受控码与返回类型名，不落原始 repr（防大对象/敏感内容入审计）。
        cursor.execute("ROLLBACK TO SAVEPOINT rate_settlement")
        return {
            "kind": "error",
            "detail": f"settlement_unknown_result:{type(settle_result).__name__}",
        }
    cursor.execute("RELEASE SAVEPOINT rate_settlement")
    return {"kind": "normal"}


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
        if effect == EFFECT_APPLIED and phase == PHASE_SUBMITTED and (not permit_id or not permit_token):
            conn.rollback()
            raise OperationResultError("PERMIT_REQUIRED", 409, "submitted 结果必须携带许可及令牌")

        # ---- CR 阻断 1（P0）：冻结锁序 invocation→attempt→delivery→(guard)binding→
        # rate slot。invocation 锁后无条件 FOR UPDATE 锁 attempt；随后无条件锁
        # delivery 并校验租户/归属——不存在必须拒绝且不 ACK（unknown/none 路径
        # 同样不例外）；evidence 分支复用已锁 delivery（不再各自加锁）；场景
        # binding 锁定在结算段（delivery 之后），rate slot 在结算 SAVEPOINT 内。
        attempt = da_attempts.lock_attempt_by_invocation_on(cursor, str(inv["id"]), tenant_id)
        if attempt is None:
            conn.rollback()
            raise OperationResultError("ATTEMPT_NOT_FOUND", 404, "invocation 未绑定 attempt")

        delivery_id = str(attempt["delivery_id"])
        run_id = str(attempt["run_id"]) if attempt.get("run_id") else None
        delivery_row = da_deliveries.lock_delivery(cursor, delivery_id, tenant_id)
        if delivery_row is None or str(delivery_row.get("tenant_id") or "") != tenant_id:
            conn.rollback()
            # 拒绝且不 ACK：attempt/delivery 归属链不完整时不得确认任何回执
            # （Runtime outbox 会重投；机器副作用事实以 DB 为准）
            raise OperationResultError(
                "DELIVERY_NOT_FOUND", 404, "attempt 绑定的 delivery 不存在或归属不符"
            )
        if attempt.get("run_id") and delivery_row.get("run_id") and str(
            delivery_row["run_id"]
        ) != str(attempt["run_id"]):
            conn.rollback()
            raise OperationResultError(
                "DELIVERY_ATTEMPT_MISMATCH", 409, "delivery 与 attempt 归属不一致"
            )

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
            has_evidence_receipt = effect == EFFECT_APPLIED and phase in (PHASE_VERIFIED, PHASE_SUBMITTED)
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
                    delivery=delivery_row,  # CR 阻断 1：复用上方已无条件锁定的 delivery
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
        elif effect == EFFECT_APPLIED and phase in (PHASE_VERIFIED, PHASE_SUBMITTED):
            # 受控写动作必须以本地已校验 permit 执行（设计 §3）
            conn.rollback()
            raise OperationResultError(
                "PERMIT_REQUIRED", 409, "applied 结果必须携带有效 permit"
            )

        # R27：applied+verified 判定链——适配器校验 → 绑定交叉核对 → evidence 表
        # ON CONFLICT 仲裁登记；失败 → delivery 收敛 unknown（机器证据）、停止后续条目
        # （unknown 传播），audit evidence_invalid；attempt 保留原始上报值。
        evidence_reason: Optional[str] = None
        if effect == EFFECT_APPLIED and phase in (PHASE_VERIFIED, PHASE_SUBMITTED):
            business_ref = inv["business_ref"] or {}
            evidence_reason = _check_evidence_and_register(
                cursor,
                tenant_id=tenant_id,
                user_id=str(inv["user_id"]),
                scenario_key=str(business_ref.get("scenario_key") or ""),
                evidence_ref=evidence_ref,
                invocation=inv,
                attempt=attempt,
                delivery=delivery_row,  # CR 阻断 1：复用上方已无条件锁定的 delivery
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

        # ---- B1.2 场景结算（设计 §5.5.4 顺序 3–6；锁序矩阵：invocation→attempt→
        # delivery→(guard 时)binding→rate slot SAVEPOINT，不获取 subject/task）----
        # 描述器注册场景：先（guard 时）定位并锁 binding，再 SAVEPOINT 结算；
        # 结算失败 → ROLLBACK TO SAVEPOINT，照常持久化原始回执后升级异常
        # （审计 + 阻断 binding + 幂等控制请求）。微信 settle=no-op 且 guard=None：
        # 仅多一次 SAVEPOINT 包裹 no-op，行为与锁面零变化；无描述器的纯底座
        # 场景完全不进入（现状零变化）。
        from src.session_tasks import control_requests as control_requests_mod
        from src.session_tasks.scenario_descriptor import get_descriptor

        _scenario_key = str((inv["business_ref"] or {}).get("scenario_key") or "")
        _descriptor = get_descriptor(_scenario_key) if _scenario_key else None
        _guard = getattr(_descriptor, "binding_guard", None) if _descriptor is not None else None
        settlement_error: Optional[str] = None
        settlement_kind: Optional[str] = None
        binding_ctx: Optional[Dict[str, Any]] = None
        if _descriptor is not None:
            if _guard is not None:
                binding_ctx = _locate_and_lock_binding(cursor, _guard, tenant_id, inv)
                if binding_ctx["anomaly"]:
                    settlement_error = binding_ctx["anomaly"]
                    settlement_kind = "anomaly"
            if settlement_error is None:
                settle_outcome = _settle_scenario_result(
                    cursor, _descriptor, _guard, tenant_id, inv, attempt, delivery_id,
                    request_id, effect, phase, evidence_reason, permit_id,
                )
                settlement_kind = settle_outcome["kind"]
                if settle_outcome["kind"] != "normal":
                    settlement_error = settle_outcome["detail"]

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
                "settlement_error": settlement_error, "settlement_kind": settlement_kind,
            },
        )
        if settlement_error is not None:
            # 结算异常升级（设计 §5.5.4 顺序 5/6；CR 阻断 9 两种来源同一升级路径）：
            # - kind=error：settle 抛异常/未知返回值，SAVEPOINT 已回滚撤销本结算写入；
            # - kind=anomaly_committed：settle 结构化返回，补建/落账写入保留。
            # 两者都：原始回执已照常持久化（attempt 保留上报值）→ 脱敏审计 +
            # 锁定 binding 置同步阻断 + 幂等 human_required 控制请求 → 提交主事务
            # 并正常 ACK（回执接纳性不受结算失败影响）。阻断/控制请求任一写入
            # 失败 → 整体异常（不 ACK、不声称已转人工，机器副作用事实由 Runtime
            # outbox 重投）。
            # P1-3（四审）：guard 缺失 = 无升级对象（无 binding 可阻断、控制请求
            # 无法迁移）——禁止"只审计后 ACK"，抛受控错误、回滚主事务且不 ACK，
            # 由 Runtime outbox 重投；微信 no-op 恒返回 None 不进入本分支。
            if _guard is None:
                conn.rollback()
                raise OperationResultError(
                    "SETTLEMENT_ESCALATION_UNAVAILABLE", 409,
                    "结算异常但场景无升级通道（binding_guard 缺失），回执未接纳",
                )
            audit.insert_audit(
                cursor, tenant_id, "rate_settlement_failed", "attempt", str(attempt["id"]),
                user_id=str(inv["user_id"]), scenario_key=_scenario_key or None,
                detail={
                    "invocation_id": str(inv["id"]), "delivery_id": delivery_id,
                    "settlement_error": settlement_error,
                },
            )
            if _guard is not None and binding_ctx is not None and binding_ctx.get("task_row"):
                task_row = binding_ctx["task_row"]
                new_block_epoch = None
                # P0-1（四审）：改绑异常时定位段已 ROLLBACK SAVEPOINT 释放旧绑定锁；
                # 此处按 旧/当前 binding id 排序重新加锁（与并发镜像事务同全序，
                # 无反向边），随后二次读取 task 确认 binding/scenario 未继续变化——
                # 再变则拒绝且不 ACK（不做无界重试）。
                _lock_ids = sorted({
                    str(task_row.get("conversation_binding_id") or ""),
                    str(binding_ctx.get("stale_binding_id") or ""),
                } - {""})
                _locked_all = bool(_lock_ids)
                for _bid in _lock_ids:
                    if _guard.lock_binding(cursor, tenant_id, _bid) is None:
                        conn.rollback()
                        raise OperationResultError(
                            "BINDING_LOCATION_FAILED", 409,
                            "结算升级失败：场景绑定在升级窗口内被删除",
                        )
                if _lock_ids:
                    # 二次确认：以 anomaly 时刻快照为基准（含 scenario 漂移场景——
                    # 漂移本身就是 anomaly 的一部分，不与冻结值比较），确认未再变化
                    _snapshot = task_row
                    cursor.execute(
                        "SELECT conversation_binding_id, scenario_key FROM session_tasks "
                        "WHERE tenant_id=%s AND id=%s",
                        (tenant_id, task_row["id"]),
                    )
                    _recheck = cursor.fetchone()
                    if (
                        _recheck is None
                        or str(_recheck["conversation_binding_id"] or "") != str(
                            _snapshot.get("conversation_binding_id") or ""
                        )
                        or str(_recheck["scenario_key"] or "") != str(
                            _snapshot.get("scenario_key") or ""
                        )
                    ):
                        conn.rollback()
                        raise OperationResultError(
                            "BINDING_LOCATION_FAILED", 409,
                            "结算升级失败：任务绑定/场景在升级窗口内再次变化",
                        )
                    new_block_epoch = _guard.block_binding(cursor, task_row, "rate_ledger_anomaly")
                control_requests_mod.insert_control_request(
                    cursor, tenant_id, task_row["id"],
                    expected_control_epoch=int(task_row["control_epoch"]),
                    expected_block_epoch=int(new_block_epoch) if new_block_epoch is not None else 0,
                    reason="rate_ledger_anomaly",
                    source_type="rate_settlement",
                    source_ref=f"invocation:{inv['id']}/delivery:{delivery_id}",
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
