"""desktop_automation 执行器骨架（【计划 §4】，P1-A）

- claim_run 只占短事务（lease/fence）；锁外做重验链与编译；每个状态步骤保存进度后即可退出，
  由下一次 tick / 回调继续——不在 API request 或长 DB 事务里等待整个 RPA 过程。
- 重验链：task subject 状态 / active revision / authorization_epoch / 截止 / quota（只读预检，
  真正预留发生在许可事务 R9）。可信上下文来自持久任务，不来自模型参数。
- 单步驱动经 LocalInvocationService.enqueue（v2 统一操作描述 R15）创建 attempt；
  结果推进由 operation-result 回调侧调用 advance_run。
- 不 import 任何场景业务模块：场景校验/编译/授权经 TrustedAdapterRegistry。
"""

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from loguru import logger

from src.db.database import get_db_connection
from src.desktop_automation import audit
from src.desktop_automation import attempts as attempts_module
from src.desktop_automation import deliveries as deliveries_module
from src.desktop_automation import outbox
from src.desktop_automation import quota as quota_module
from src.desktop_automation import runs as runs_module
from src.desktop_automation import subjects as subjects_module
from src.desktop_automation.adapters import (
    AdapterContext,
    AdapterNotFoundError,
    TrustedAdapterRegistry,
)
from src.desktop_automation.constants import (
    BUSINESS_KIND_DESKTOP_AUTOMATION,
    OPERATION_PROTOCOL_V2,
    TASK_STATUS_ACTIVE,
)

# v2 invocation 默认截止预算（enqueue 时写入 deadline_at；许可事务复验同一截止）
DEFAULT_OPERATION_DEADLINE_SECONDS = 600


def derive_resource_key(device_id: str, windows_user: str = "", session_id: str = "") -> str:
    """桌面资源标识（R13：sha256(device_id|windows_user|session_id)）。

    服务端在 enqueue 时写入 invocation 操作描述；Runtime 侧本地 OS 锁与许可绑定同一键
    （windows_user/session_id 由 Runtime 上报后在 P1-B/C 补全，当前允许空分量）。
    """
    return hashlib.sha256(f"{device_id}|{windows_user}|{session_id}".encode("utf-8")).hexdigest()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _op_dict(op: Any) -> Dict[str, Any]:
    """CompiledOperation/dataclass → dict（insert_deliveries 输入规整）"""
    if isinstance(op, dict):
        return op
    from dataclasses import asdict

    return asdict(op)


def build_v2_operation_arguments(
    *,
    operation: str,
    provider_key: str,
    target_ref: str,
    target_handle: Optional[str],
    target_version: Optional[str],
    payload_ref: Optional[str],
    payload_hash: Optional[str],
    request_id: str,
    delivery_id: str,
    authorization_revision: Optional[str],
    authorization_epoch: Optional[int],
    resource_key: Optional[str],
    deadline_at: Optional[datetime],
) -> Dict[str, Any]:
    """v2 统一操作描述（R15）：正文不进 arguments_json（payload_ref/hash 引用，resolver 取字节）"""
    return {
        "protocol_version": OPERATION_PROTOCOL_V2,
        "operation": operation,
        "provider_key": provider_key,
        "target_ref": target_ref,
        "target_handle": target_handle,
        "target_version": target_version,
        "payload_ref": payload_ref,
        "payload_hash": payload_hash,
        "request_id": request_id,
        "delivery_id": delivery_id,
        "authorization_revision": authorization_revision,
        "authorization_epoch": authorization_epoch,
        "resource_key": resource_key,
        "deadline_at": deadline_at.astimezone(timezone.utc).isoformat() if deadline_at else None,
    }


def _adapter_ctx(run: Dict[str, Any]) -> AdapterContext:
    return AdapterContext(
        tenant_id=run["tenant_id"],
        user_id=run["user_id"],
        scenario_key=run["scenario_key"],
        task_ref=run["task_ref"],
        revision_ref=run["revision_ref"],
    )


# ==================== 重验链 ====================


def revalidate_run(
    run: Dict[str, Any], now: Optional[datetime] = None
) -> Tuple[bool, str]:
    """执行前重验：task subject 状态 / active revision / epoch 快照 / 截止（§4 顺序 1）"""
    now = now or _utcnow()
    task = subjects_module.get_task_subject(
        run["tenant_id"], run["scenario_key"], run["task_ref"]
    )
    if task is None:
        return False, "task_subject_missing"
    if task["status"] != TASK_STATUS_ACTIVE:
        return False, "task_not_active"
    if task["active_revision_ref"] != run["revision_ref"]:
        return False, "revision_switched"
    run_epoch = run.get("authorization_epoch")
    if run_epoch is not None and task["authorization_epoch"] != run_epoch:
        # 发布/暂停已推进 epoch：本 run 的授权快照失效（发送前授权撤销统一拦截）
        return False, "authorization_epoch_stale"
    expires_at = run.get("expires_at")
    if expires_at is not None:
        expires_at = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=timezone.utc)
        if now > expires_at:
            return False, "deadline_exceeded"
    return True, ""


def precheck_quota(run: Dict[str, Any], delivery: Dict[str, Any]) -> Tuple[bool, str]:
    """quota 只读预检（重验链最后一环）：按适配器授权决定给出的层级读桶核对余量；
    真正的预留/回滚在许可事务（R9），此处失败提前止损不建 attempt。"""
    adapter = TrustedAdapterRegistry.require(run["scenario_key"])
    decision = adapter.authorize_operation(
        _adapter_ctx(run),
        operation=delivery["operation"],
        target_ref=delivery.get("target_ref"),
        target_version=delivery.get("target_version"),
        payload_hash=delivery.get("payload_hash"),
        authorization_revision=run.get("revision_ref"),
        authorization_epoch=run.get("authorization_epoch"),
    )
    if not decision.allowed:
        return False, f"authorization_denied:{decision.reason}"
    now = _utcnow()
    for spec in decision.quota_scopes:
        scope = quota_module.QuotaScope(
            scope_type=spec.scope_type, scope_id=spec.scope_id,
            limit_count=spec.limit_count, window_seconds=spec.window_seconds,
        )
        bucket_start = quota_module.bucket_start_for(scope.window_seconds, now)
        bucket = quota_module.get_bucket(
            run["tenant_id"], scope.scope_type, scope.scope_id, bucket_start
        )
        if bucket is not None and bucket["reserved_count"] >= bucket["limit_count"]:
            return False, f"quota_exhausted:{scope.scope_type}:{scope.scope_id}"
    return True, ""


# ==================== claim + 初始化 deliveries ====================


def claim_pending_run(
    *,
    tenant_id: Optional[str] = None,
    expected_run_id: Optional[str] = None,
    lease_seconds: int = 120,
) -> Optional[Dict[str, Any]]:
    """领取一条到期 pending run（R50）：expected_run_id 提供时定向领取（SKIP LOCKED，
    非目标/已被领返回 None）；缺省领取范围内最早到期者。只领取本进程已注册适配器的
    场景（避免领取后因适配器缺失而卡死）。"""
    claimed = runs_module.claim_run(
        lease_seconds=lease_seconds,
        limit=1,
        tenant_id=tenant_id,
        scenario_keys=TrustedAdapterRegistry.registered_keys() or None,
        expected_run_id=expected_run_id,
    )
    return claimed[0] if claimed else None


def fix_run_device(run: Dict[str, Any], device_id: str) -> Dict[str, Any]:
    """固定任务 device_id（不跟随用户临时“选中设备”漂移，§4 顺序 2）"""
    if device_id:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE desktop_automation_runs
                SET device_id = %s, started_at = NOW()
                WHERE id = %s AND tenant_id = %s AND state = 'running'
                """,
                (device_id, str(run["id"]), run["tenant_id"]),
            )
            conn.commit()
        return {**run, "device_id": device_id}
    return run


def prepare_claimed_run(
    run: Dict[str, Any],
    *,
    revision_config: Dict[str, Any],
    device_id: str,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """已领取 run 的准备段：固定设备 → 重验 → 编译 deliveries（payload hash 预载校验）
    → 短事务落行。revision_config 必须是**该 run 自身** revision 的冻结配置（R50：
    场景侧先定向领到 run、再加载其配置，杜绝错配编译）。

    P2-3 双保险：revision_config 自带 revision_ref（场景侧 load 时写入）且与
    run.revision_ref 不符即抛——定向领取下不可达的错配在此 fail-fast。

    不通过重验的 run 直接按原因落终态（cancelled/unknown/expired）。
    """
    config_revision = str(revision_config.get("revision_ref") or "")
    if config_revision and config_revision != str(run.get("revision_ref") or ""):
        raise ValueError(
            f"revision 配置与 run 不一致（config={config_revision} "
            f"run={run.get('revision_ref')}）——R50 定向领取路径不得出现，拒绝错配编译"
        )
    run = fix_run_device(run, device_id)
    now = now or _utcnow()
    try:
        TrustedAdapterRegistry.require(run["scenario_key"])
    except AdapterNotFoundError:
        # 场景适配器未注册（进程分工/部署差异）：run 落终态，不留永久 running 行
        _abandon_run(run, "scenario_not_registered", now)
        return {"run": run, "prepared": False, "reason": "scenario_not_registered"}
    ok, reason = revalidate_run(run, now)
    if not ok:
        _abandon_run(run, reason, now)
        return {"run": run, "prepared": False, "reason": reason}

    adapter = TrustedAdapterRegistry.require(run["scenario_key"])
    ctx = _adapter_ctx(run)
    compiled = adapter.compile_operations(ctx, revision_config)
    compiled = sorted(compiled, key=lambda op: op.position)

    # payload 预加载并验 hash（§4 顺序 4）：所需资源准备成功后才开始第一项
    for op in compiled:
        if op.payload_ref is None:
            continue
        payload = adapter.serve_payload(ctx, op.payload_ref)
        actual = hashlib.sha256(payload).hexdigest()
        if op.payload_hash and actual != op.payload_hash:
            _abandon_run(run, "payload_hash_mismatch", now)
            return {"run": run, "prepared": False, "reason": "payload_hash_mismatch"}

    with get_db_connection() as conn:
        cursor = conn.cursor()
        delivery_ids = deliveries_module.insert_deliveries(
            cursor, run, [_op_dict(op) for op in compiled]
        )
        conn.commit()
    logger.info(
        f"后端日志：desktop_automation run 准备完成 tenant={run['tenant_id']} "
        f"run={run['id']} deliveries={len(delivery_ids)}"
    )
    return {"run": run, "prepared": True, "reason": "", "delivery_ids": delivery_ids}


def claim_and_prepare_run(
    *,
    revision_config: Dict[str, Any],
    device_id: str,
    tenant_id: Optional[str] = None,
    lease_seconds: int = 120,
    now: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    """领取 pending run → 固定任务 device_id → 重验 → 编译 deliveries（payload hash
    预载校验）→ 短事务落行（claim_pending_run + prepare_claimed_run 的组合封装）。

    **仅测试兼容用**（P2-3 标注）：非定向领取（范围内最早 pending），生产路径应走
    claim_pending_run(expected_run_id=...) + prepare_claimed_run 的定向领取（R50）。

    revision_config 由场景侧加载的冻结配置（场景表归属，底座不解析），须与被领取
    run 的 revision 一致（定向领取路径由 claim_pending_run(expected_run_id=...) 保证）；
    tenant_id 可选限定领取范围（不传为全局）；只领取本进程已注册适配器的场景，
    领取后发现适配器缺失的 run 落终态（不卡死在 running）。
    不通过重验的 run 直接按原因落终态（cancelled/unknown/expired）。
    """
    run = claim_pending_run(tenant_id=tenant_id, lease_seconds=lease_seconds)
    if run is None:
        return None
    return prepare_claimed_run(
        run, revision_config=revision_config, device_id=device_id, now=now
    )


def _abandon_run(run: Dict[str, Any], reason: str, now: datetime) -> None:
    """重验失败的 run 落终态（§5.4 语义映射；未提交条目按取消/截止口径收敛）"""

    with get_db_connection() as conn:
        cursor = conn.cursor()
        if reason == "deadline_exceeded":
            deliveries_module.expire_unstarted_deliveries(cursor, run["tenant_id"], str(run["id"]))
            rows = deliveries_module.list_run_deliveries(str(run["id"]), run["tenant_id"])
            state = runs_module.compute_run_terminal_state(rows, deadline_exceeded=True)
        elif reason in ("task_not_active", "authorization_epoch_stale", "revision_switched"):
            deliveries_module.skip_remaining_deliveries(
                cursor, str(run["id"]), run["tenant_id"], reason
            )
            rows = deliveries_module.list_run_deliveries(str(run["id"]), run["tenant_id"])
            # 重验发生在编译 deliveries 之前：rows 可能为空，全部未提交且取消 → cancelled
            state = runs_module.compute_run_terminal_state(rows, cancelled=True) or "cancelled"
        else:
            state = "unknown" if reason == "payload_hash_mismatch" else "failed"
            deliveries_module.skip_remaining_deliveries(
                cursor, str(run["id"]), run["tenant_id"], reason
            )
        if state is None:
            state = "failed"
        runs_module.finish_run(
            cursor, str(run["id"]), run["tenant_id"], state, {"reason": reason},
            fence_token=run.get("fence_token"),
        )
        audit.insert_audit(
            cursor, run["tenant_id"], "run_abandoned", "run", str(run["id"]),
            user_id=run.get("user_id"), scenario_key=run.get("scenario_key"),
            detail={"reason": reason, "state": state},
        )
        conn.commit()
    logger.warning(
        f"后端日志：desktop_automation run 放弃 tenant={run['tenant_id']} run={run['id']} "
        f"reason={reason} state={state}"
    )


# ==================== 单步驱动 ====================


def execute_next_delivery(
    run: Dict[str, Any], *, now: Optional[datetime] = None,
    execution_lane: str = "standard",
    extra_business_ref: Optional[Dict[str, Any]] = None,
    deadline_cap: Optional[datetime] = None,
    dedupe_key_override: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """顺序规则（§4 顺序 5）：下一条只在上一条 applied 且 verified 后开始。

    返回 {delivery, invocation_id, request_id, attempt_id}；无可执行条目返回 None。
    execution_lane/extra_business_ref（设计 §10 会话任务扩展）：lane 仅服务端设置
    （'session_task' 道仅定向 claim 领取）；extra_business_ref 由场景侧传入并入
    invocation business_ref（decision_id/batch_id/input_version 等，均服务端生成）。
    """
    now = now or _utcnow()
    delivery = deliveries_module.next_executable_delivery(str(run["id"]), run["tenant_id"])
    if delivery is None:
        return None
    ok, reason = revalidate_run(run, now)
    if not ok:
        _abandon_run(run, reason, now)
        return None
    ok, reason = precheck_quota(run, delivery)
    if not ok:
        _abandon_run(run, reason, now)
        return None

    adapter = TrustedAdapterRegistry.require(run["scenario_key"])
    ctx = _adapter_ctx(run)
    # target resolve：target handle 只在同一设备/账号及有效版本内使用（身份规则由适配器验证）
    resolution = adapter.resolve_target(ctx, delivery["target_ref"])
    if not resolution.ok:
        _abandon_run(run, f"target_unresolved:{resolution.reason}", now)
        return None

    request_id = str(uuid.uuid4())
    deadline_at = now + timedelta(seconds=DEFAULT_OPERATION_DEADLINE_SECONDS)
    if deadline_cap is not None:
        # 会话任务（A3）：invocation 截止不晚于任务 expires_at——许可 deadline 取
        # LEAST(ttl, invocation.deadline_at, lease)，传导后 permit 亦不晚于任务截止
        cap = deadline_cap if deadline_cap.tzinfo else deadline_cap.replace(tzinfo=timezone.utc)
        deadline_at = min(deadline_at, cap)
    resource_key = derive_resource_key(str(run.get("device_id") or ""))
    arguments = build_v2_operation_arguments(
        operation=delivery["operation"],
        provider_key=delivery["provider_key"],
        target_ref=delivery["target_ref"],
        target_handle=resolution.target_handle,
        target_version=resolution.target_version or delivery.get("target_version"),
        payload_ref=delivery.get("payload_ref"),
        payload_hash=delivery.get("payload_hash"),
        request_id=request_id,
        delivery_id=str(delivery["id"]),
        authorization_revision=run.get("revision_ref"),
        authorization_epoch=run.get("authorization_epoch"),
        resource_key=resource_key,
        deadline_at=deadline_at,
    )

    from src.local_tools.service import LocalInvocationService

    # R45：首派 dedupe_key 用 per-attempt 形态（delivery:{id}:a:{n}），与场景侧
    # 人工重试键同一空间；n 取该 delivery 现有 attempt 最大号 +1（与 create_attempt
    # 的 MAX+1 计算一致——首派路径无既有 attempt，恒为 1；并发双派发由
    # UNIQUE(tenant,business_kind,dedupe_key) 幂等收敛为同一 invocation）
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COALESCE(MAX(attempt_no), 0) + 1 AS next_no "
            "FROM desktop_automation_attempts WHERE delivery_id = %s AND tenant_id = %s",
            (str(delivery["id"]), run["tenant_id"]),
        )
        next_attempt_no = int(cursor.fetchone()["next_no"])

    service = LocalInvocationService()
    # #5 稳定执行身份：会话任务按决策（业务键）去重——并发 prepare / 崩溃重试
    # 在 attempt 编号计算的交错窗口内收敛到同一 invocation，不解释为新发送尝试；
    # 缺省沿用 per-attempt 键（营销/重试链语义不变）
    effective_dedupe_key = dedupe_key_override or f"delivery:{delivery['id']}:a:{next_attempt_no}"
    business_ref = {
        "delivery_id": str(delivery["id"]),
        "run_id": str(run["id"]),
        "occurrence_id": str(run.get("occurrence_id") or ""),
        "scenario_key": run["scenario_key"],
        "task_ref": run["task_ref"],
        "revision_ref": run["revision_ref"],
    }
    if extra_business_ref:
        business_ref.update(extra_business_ref)
    invocation = service.enqueue(
        tenant_id=run["tenant_id"],
        user_id=run["user_id"],
        device_id=str(run.get("device_id") or ""),
        tool_name=delivery["operation"],
        arguments=arguments,
        provider_key=delivery["provider_key"],
        business_kind=BUSINESS_KIND_DESKTOP_AUTOMATION,
        business_ref=business_ref,
        dedupe_key=effective_dedupe_key,
        deadline_at=deadline_at,
        authorization_epoch=run.get("authorization_epoch"),
        execution_lane=execution_lane,
    )
    # dedupe 幂等复用旧 invocation 时（崩溃后重派等），request_id 以旧 arguments_json 为准
    # （permit/attempt 与 invocation 三方绑定一致；P2-4）
    effective_request_id = (invocation.get("arguments_json") or {}).get("request_id") or request_id

    with get_db_connection() as conn:
        cursor = conn.cursor()
        existing_attempt = attempts_module.get_attempt_by_invocation_on(
            cursor, str(invocation["id"]), run["tenant_id"]
        )
        if existing_attempt is not None:
            attempt_id = str(existing_attempt["id"])  # 幂等：不重复建 attempt
        else:
            attempt_id = attempts_module.create_attempt(
                cursor, run["tenant_id"], delivery, str(invocation["id"]), effective_request_id
            )
        deliveries_module.mark_dispatched(cursor, str(delivery["id"]), run["tenant_id"])
        conn.commit()
    logger.info(
        f"后端日志：desktop_automation 单步下发 tenant={run['tenant_id']} run={run['id']} "
        f"delivery={delivery['id']} position={delivery['position']} invocation={invocation['id']}"
    )
    return {
        "delivery": delivery,
        "invocation_id": str(invocation["id"]),
        "request_id": effective_request_id,
        "attempt_id": attempt_id,
    }


# ==================== 结果推进（operation-result 回调侧） ====================


def advance_run(run_id: str, tenant_id: str, *, now: Optional[datetime] = None) -> Optional[str]:
    """结果推进：任一 unknown 停止后续；全部终态时按 §5.4 聚合落终态 + 场景业务判定。

    返回最终 run state（未到终态返回 None）。
    """

    now = now or _utcnow()
    rows = deliveries_module.list_run_deliveries(run_id, tenant_id)
    has_unknown = any(runs_module.delivery_is_unknown(d) for d in rows)
    if has_unknown:
        # unknown/提交后断网/崩溃：待核对，不重发，停止本包后续（§5.4 表）
        with get_db_connection() as conn:
            cursor = conn.cursor()
            deliveries_module.skip_remaining_deliveries(
                cursor, run_id, tenant_id, "predecessor_unknown"
            )
            conn.commit()
        rows = deliveries_module.list_run_deliveries(run_id, tenant_id)

    state = runs_module.compute_run_terminal_state(rows)
    if state is None:
        return None

    run = runs_module.get_run(run_id, tenant_id)
    if run is None:
        return None
    adapter = TrustedAdapterRegistry.get(run["scenario_key"])
    business: Dict[str, Any] = {}
    if adapter is not None:
        try:
            verdict = adapter.aggregate_result(_adapter_ctx(run), rows)
            business = {"verdict": verdict.verdict, "summary": verdict.summary}
        except Exception as e:  # noqa: BLE001 业务判定异常不影响机器终态
            logger.opt(exception=True).warning(
                f"后端日志：desktop_automation 场景聚合判定失败 run={run_id}: {e}"
            )
    with get_db_connection() as conn:
        cursor = conn.cursor()
        runs_module.finish_run(
            cursor, run_id, tenant_id, state, {"business": business},
            fence_token=run.get("fence_token"),
        )
        outbox.enqueue_outbox(
            cursor, tenant_id, "run_finished", run_id, f"run-finished:{run_id}",
            user_id=run.get("user_id"),
            payload_ref=run.get("occurrence_id"),
        )
        audit.insert_audit(
            cursor, tenant_id, "run_finished", "run", run_id,
            user_id=run.get("user_id"), scenario_key=run.get("scenario_key"),
            detail={"state": state, "business": business},
        )
        conn.commit()
    logger.info(
        f"后端日志：desktop_automation run 终态 tenant={tenant_id} run={run_id} state={state}"
    )
    return state
