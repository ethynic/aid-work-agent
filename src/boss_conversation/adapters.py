"""BossConversationAdapter：boss.chat_reply.v1 场景适配器（B2，设计 §5.4/§5.5）。

复用单条执行底座（与微信同形）：reply 决策经 accept_manual_trigger → 单条
boss_send_to_v2 delivery → invocation（execution_lane='session_task'）。

- authorize_operation（第二闸门，设计 §5.5.4）：write-authorize 许可事务同一
  cursor 内权威复判——**binding FOR UPDATE 行锁内**（P1-1）核对冻结 target_ref/
  target_version（P1-2）+ 频控三窗口重算（reserved_at 口径同 gate）；通过 →
  rate slot 预留状态机（P1-3：首插 reserved；released→原子恢复；reserved/settled/
  归属不符→rate_ledger_anomaly 拒绝）；失败三分类 rate_window_race /
  rate_daily_cap / rate_ledger_anomaly → 结构化拒绝（control_action/audit_code
  受控码），通用 permits 先提交阻断+控制请求副作用再返回拒绝。
- settle_operation_result（SAVEPOINT 结算，§5.5.4 顺序 3–6）：reserved→settled
  （submitted/verified/unknown）/released（明确未开始）；应占额度结果 slot 缺失 →
  INSERT ON CONFLICT 幂等补建 settled + 写异常队列 + 返回
  {"status":"anomaly_committed","reason":"rate_slot_missing"}；结算/状态冲突 →
  嵌套 SAVEPOINT 回滚本结算写入后写异常队列，返回 anomaly_committed（受控码）
  ——异常行与升级副作用保留、通用层升级，语义等价 §5.5.4 顺序 6。
"""
from __future__ import annotations

import base64
import hashlib
import re
from datetime import timedelta
from typing import Any, Dict, List, Optional

from src.desktop_automation.adapters import (
    AdapterContext,
    AuthorizeDecision,
    CompiledOperation,
    EvidenceContext,
    QuotaScopeSpec,
    RevisionValidation,
    RunBusinessResult,
    TargetResolution,
)
from src.desktop_automation.constants import PHASE_PREPARED

from .constants import (
    BUSINESS_TIMEZONE,
    OPERATION_MESSAGE_SEND,
    PROVIDER_KEY,
    RATE_DAILY_CAP,
    RATE_INTERVAL_SECONDS,
    RATE_WINDOW_MAX,
    RATE_WINDOW_SECONDS,
    RECEIPT_POLICY,
    SCENARIO_KEY,
)
from .gate import _count_windows, _to_aware_utc
from .models import template_content_hash
from .render import parse_payload_ref

_SUBMISSION_EVIDENCE_PREFIX = RECEIPT_POLICY["submission_evidence_namespace"]
_VERIFIED_EVIDENCE_RE = re.compile(
    r"^" + re.escape(RECEIPT_POLICY["verified_evidence_namespace"]) + r":([^:\s]+):\d+$"
)
_QUOTA_WINDOW_SECONDS = 30 * 86400


class BossConversationAdapterError(Exception):
    """受控载荷/目标解析失败（fail-closed）。"""


class BossConversationSettlementError(BossConversationAdapterError):
    """结算失败（P1-4）：携带受控异常码（anomaly_error_code），结算 SAVEPOINT
    回滚后据此写 bs_boss_rate_settlement_anomalies 并返回 anomaly_committed。"""

    def __init__(self, error_code: str, detail: str = "") -> None:
        super().__init__(f"{error_code}: {detail}" if detail else error_code)
        self.anomaly_error_code = error_code


class _RateSlotReserveConflict(Exception):
    """write-authorize rate slot 预留冲突（P1-3）：reserved/settled 重复授权、
    归属不符或同 decision 复用——内部信号，转 rate_ledger_anomaly 拒绝。"""


class BossConversationAdapter:
    """BOSS 候选人回复场景适配器（V1：单会话、单条文字发送、频控双闸门）。"""

    scenario_key = SCENARIO_KEY

    # ==================== 场景适配器协议 ====================

    def validate_revision(self, ctx: AdapterContext, revision_config: Dict[str, Any]) -> RevisionValidation:  # noqa: ANN001
        """会话任务不经时间/事件触发（manual trigger per decision）；配置形状在
        compile_operations 内校验，此处仅保留协议完整性。"""
        return RevisionValidation(ok=True, schedule_specs=[])

    def resolve_target(self, ctx: AdapterContext, target_ref: str) -> TargetResolution:  # noqa: ANN001
        """conversation_binding（候选人绑定）→ 短期 handle + 身份版本（verified 才可达）。"""
        from .bindings import is_binding_verified_valid

        with _own_connection() as conn:
            from .bindings import _load_binding_row

            binding = _load_binding_row(conn.cursor(), ctx.tenant_id, str(target_ref))
        if binding is None or str(binding.get("tenant_id")) != ctx.tenant_id:
            return TargetResolution(ok=False, reason="boss_binding_not_found")
        if not is_binding_verified_valid(binding):
            return TargetResolution(ok=False, reason=f"boss_binding_state:{binding.get('verification_status')}")
        identity_version = int(binding.get("identity_version") or 0)
        handle = base64.urlsafe_b64encode(
            hashlib.sha256(f"{ctx.tenant_id}|{target_ref}|{identity_version}".encode("utf-8")).digest()
        ).rstrip(b"=").decode("ascii")[:32]
        return TargetResolution(ok=True, target_handle=handle, target_version=f"iv-{identity_version}")

    def authorize_operation(
        self,
        ctx: AdapterContext,
        *,
        operation: str,
        target_ref: Optional[str],
        target_version: Optional[str],
        payload_hash: Optional[str],
        authorization_revision: Optional[str],
        authorization_epoch: Optional[int],
        invocation: Optional[Dict[str, Any]] = None,
        cursor: Optional[Any] = None,
    ) -> AuthorizeDecision:
        """第二闸门（设计 §5.5.4）：许可事务同一 cursor 内权威复判。

        cursor=None 为 executor.precheck_quota 只读预检（invocation 创建前）：
        降级放行默认额度（许可签发路径才是权威判定，与微信/ fake 适配器同构）。
        """
        from .config import tenant_allowed as boss_tenant_allowed

        if not boss_tenant_allowed(ctx.tenant_id):
            return AuthorizeDecision(allowed=False, reason="boss_conversation_disabled")
        from src.session_tasks.config import tenant_allowed as session_tasks_tenant_allowed

        if not session_tasks_tenant_allowed(ctx.tenant_id):
            return AuthorizeDecision(allowed=False, reason="session_tasks_disabled")
        if operation != OPERATION_MESSAGE_SEND:
            return AuthorizeDecision(allowed=False, reason=f"unsupported_operation:{operation}")
        if cursor is None:
            return AuthorizeDecision(allowed=True, quota_scopes=[QuotaScopeSpec(
                scope_type="task", scope_id=f"{self.scenario_key}:{ctx.task_ref}",
                limit_count=10, window_seconds=_QUOTA_WINDOW_SECONDS,
            )])
        return self._authorize_on_cursor(
            ctx, cursor, operation=operation, target_ref=target_ref,
            target_version=target_version, payload_hash=payload_hash, invocation=invocation,
        )

    def _authorize_on_cursor(
        self, ctx: AdapterContext, cursor, *, operation, target_ref, target_version,
        payload_hash, invocation,
    ) -> AuthorizeDecision:  # noqa: ANN001
        """复判主体（锁序矩阵 §5.5.5：subject→run→invocation→delivery 已由许可链
        持有，此处 binding→rate slot 为链尾延伸；P1-1/P1-2/P1-3 六审修复）。

        顺序（V1.10 冻结语义）：
        ① 普通读 task 行定位 conversation_binding_id（许可路径不取 task 行锁）；
        ② 冻结目标核验（加锁前）：delivery 冻结 target_ref == task 当前绑定；
        ③ binding FOR UPDATE（P1-1：与 operation-result 结算/阻断在同一行锁上串行，
           杜绝按旧窗口签发 permit 穿透 60s/10min/日频控或越过同步阻断）；
        ④ 加锁后复验任务绑定未改绑（P1-2）；
        ⑤ blocked → 结构化拒绝（control_action）；verified/过期 → 普通拒绝；
        ⑥ target_version == iv-{identity_version}（P1-2：identity 版本变化的旧
           invocation 不得签发 permit/slot）；
        ⑦ 频控三窗口在锁内重算 → rate_daily_cap / rate_window_race；
        ⑧ rate slot 预留状态机（P1-3/V1.10 §5.5.4：首插 reserved；released→原子
           恢复 reserved；reserved/settled/归属不符 → rate_ledger_anomaly 拒绝；
           每条允许路径恰好影响一行）。
        """
        from .bindings import is_binding_verified_valid
        from .gate import BossBindingGuard

        cursor.execute(
            "SELECT id, tenant_id, control_epoch, conversation_binding_id "
            "FROM session_tasks WHERE tenant_id=%s AND id=%s",
            (ctx.tenant_id, str(ctx.task_ref)),
        )
        task_row = cursor.fetchone()
        if task_row is None or not task_row["conversation_binding_id"]:
            # 无受信定位链：拒绝且无副作用对象（普通拒绝，许可链整体回滚）
            return AuthorizeDecision(allowed=False, reason="binding_location:task_missing")
        if not _is_uuid(str(target_ref or "")) or str(target_ref) != str(task_row["conversation_binding_id"]):
            # P1-2（加锁前核验）：delivery 冻结目标 ≠ 任务当前绑定（已改绑/伪造）
            # → 不得向 A 发送却按 B 记账，拒绝且不产生 permit/slot
            return AuthorizeDecision(allowed=False, reason="boss_target_frozen_mismatch")
        guard = BossBindingGuard()
        binding_row = guard.lock_binding(cursor, ctx.tenant_id, str(task_row["conversation_binding_id"]))
        if binding_row is None:
            return AuthorizeDecision(allowed=False, reason="binding_location:binding_missing")
        # P1-2（加锁后复验）：task 普通读与 binding 加锁窗口内的改绑一律拒绝
        # （READ COMMITTED 新语句快照可见已提交改绑；在途改绑持 task 锁尚未提交，
        # 其 binding 锁请求与本事务互斥，提交后由下一次复判/回执路径拦截）
        cursor.execute(
            "SELECT conversation_binding_id FROM session_tasks WHERE tenant_id=%s AND id=%s",
            (ctx.tenant_id, str(ctx.task_ref)),
        )
        recheck = cursor.fetchone()
        if recheck is None or str(recheck["conversation_binding_id"] or "") != str(target_ref):
            return AuthorizeDecision(allowed=False, reason="boss_target_rebound")
        if binding_row.get("automation_blocked"):
            # CR：control_action 即通用层控制请求 reason→任务 human_required reason
            # （permits 把 control_action 原样写 reason，control_requests 消费时作为
            # 迁移 reason）——与 prepare-send gate terminal 同词汇（automation_blocked）
            return AuthorizeDecision(
                allowed=False,
                reason=f"automation_blocked:{binding_row.get('automation_block_reason') or 'blocked'}",
                control_action="automation_blocked",
                audit_code="boss_binding_blocked",
            )
        if not is_binding_verified_valid(binding_row):
            # 绑定失效（pending/invalid/expired）：非频控异常，普通拒绝（B3 指纹/
            # 失效链路负责迁移与通知）
            return AuthorizeDecision(allowed=False, reason="boss_binding_not_verified")
        # P1-2：冻结目标版本核验——target_version 必须等于 iv-{identity_version}
        #（resolve_target 冻结时的身份版本）；identity 版本已前进（账号切换/重验）
        # 的旧 invocation 拒绝签发 permit/slot
        expected_version = f"iv-{int(binding_row.get('identity_version') or 0)}"
        if str(target_version or "") != expected_version:
            return AuthorizeDecision(allowed=False, reason="boss_target_version_mismatch")
        # 频控三窗口重算（reserved_at 口径与 gate 完全同源；P1-1：在 binding 锁内）
        windows = _count_windows(cursor, ctx.tenant_id, str(binding_row["id"]))
        if int(windows["today_count"]) >= RATE_DAILY_CAP:
            # CR：设计 §5.5.4 表冻结 human_required reason=rate_limit（与 gate
            # terminal 同词汇）；分类名保留在 audit_code
            return AuthorizeDecision(
                allowed=False, reason="rate_daily_cap",
                control_action="rate_limit", audit_code="rate_daily_cap",
            )
        now_utc = _to_aware_utc(windows["now_ts"])
        constraints = []
        if windows["last_reserved_at"] is not None:
            constraints.append(("rate_interval", _to_aware_utc(windows["last_reserved_at"]) + timedelta(seconds=RATE_INTERVAL_SECONDS)))
        if int(windows["window_count"]) >= RATE_WINDOW_MAX and windows["window_third_reserved_at"] is not None:
            constraints.append(("rate_window", _to_aware_utc(windows["window_third_reserved_at"]) + timedelta(seconds=RATE_WINDOW_SECONDS)))
        still_blocked = [name for name, until in constraints if until > now_utc]
        if still_blocked:
            # 门禁通过后账本被迟到回执补账/并发/恢复结算改变 → 保守转人工，不自动重试
            # （CR：control_action=设计 §5.5.4 冻结 human_required reason）
            return AuthorizeDecision(
                allowed=False, reason="rate_window_race",
                control_action="rate_window_race", audit_code="rate_window_race",
            )
        decision_ref = _decision_ref(invocation)
        delivery_ref = _delivery_ref(invocation)
        if not _is_uuid(decision_ref) or not _is_uuid(delivery_ref):
            # invocation 冻结 business_ref 缺决策/delivery 归属：账本无法精确落行
            # → 数据异常保守转人工（§5.5.4 第三分类）
            return AuthorizeDecision(
                allowed=False, reason="rate_ledger_anomaly",
                control_action="rate_ledger_anomaly", audit_code="rate_ledger_anomaly",
            )
        # P1-3（V1.10 §5.5.4 冻结状态机）：rate slot 预留——禁止 ON CONFLICT DO
        # NOTHING 静默。首插 reserved；released（同 delivery 人工重试）→ 原子恢复
        # reserved（刷新 reserved_at、清 settled_at/released_at/settlement_effect）；
        # reserved/settled（同 delivery 重复授权）或 binding/decision 归属不符 →
        # rate_ledger_anomaly 保守拒绝。每条允许路径恰好影响一行（RETURNING 校验）。
        cursor.execute("SAVEPOINT boss_rate_reserve")
        try:
            cursor.execute(
                """
                SELECT id, status, binding_id, decision_id
                FROM bs_boss_conversation_rate_slots
                WHERE tenant_id=%s AND delivery_id=%s FOR UPDATE
                """,
                (ctx.tenant_id, delivery_ref),
            )
            slot = cursor.fetchone()
            reserved = False
            if slot is None:
                # 同 decision 复用（协议破坏：一个 decision 仅一组执行单元）→ 异常
                cursor.execute(
                    "SELECT 1 FROM bs_boss_conversation_rate_slots "
                    "WHERE tenant_id=%s AND decision_id=%s",
                    (ctx.tenant_id, decision_ref),
                )
                if cursor.fetchone() is not None:
                    raise _RateSlotReserveConflict("decision_reused")
                cursor.execute(
                    """
                    INSERT INTO bs_boss_conversation_rate_slots
                        (tenant_id, user_id, binding_id, decision_id, delivery_id,
                         status, reserved_at)
                    VALUES (%s, %s, %s, %s, %s, 'reserved', clock_timestamp())
                    RETURNING id
                    """,
                    (
                        ctx.tenant_id, binding_row.get("user_id"),
                        str(binding_row["id"]), decision_ref, delivery_ref,
                    ),
                )
                reserved = cursor.fetchone() is not None
            elif (
                str(slot["binding_id"]) == str(binding_row["id"])
                and str(slot["decision_id"]) == decision_ref
                and slot["status"] == "released"
            ):
                # released → 原子恢复 reserved（刷新时间 + 清旧结算字段）
                cursor.execute(
                    """
                    UPDATE bs_boss_conversation_rate_slots
                    SET status='reserved', reserved_at=clock_timestamp(),
                        settled_at=NULL, released_at=NULL, settlement_effect=NULL,
                        updated_at=clock_timestamp()
                    WHERE id=%s AND status='released'
                    RETURNING id
                    """,
                    (slot["id"],),
                )
                reserved = cursor.fetchone() is not None
            # slot 存在但 reserved/settled 或归属不符 → 冲突（双放行/漏账防护）
            if not reserved:
                raise _RateSlotReserveConflict(
                    "slot_state" if slot is not None else "insert_conflict"
                )
            cursor.execute("RELEASE SAVEPOINT boss_rate_reserve")
        except _RateSlotReserveConflict:
            cursor.execute("ROLLBACK TO SAVEPOINT boss_rate_reserve")
            return AuthorizeDecision(
                allowed=False, reason="rate_ledger_anomaly",
                control_action="rate_ledger_anomaly", audit_code="rate_ledger_anomaly",
            )
        except Exception:
            # 非预期 DB 错误（如 decision_id 唯一索引并发冲突）：回滚预留段，
            # 异常上抛由许可链整体回滚（不签发 permit，fail-closed）
            cursor.execute("ROLLBACK TO SAVEPOINT boss_rate_reserve")
            raise
        return AuthorizeDecision(allowed=True, quota_scopes=[QuotaScopeSpec(
            scope_type="task", scope_id=f"{self.scenario_key}:{ctx.task_ref}",
            limit_count=10, window_seconds=_QUOTA_WINDOW_SECONDS,
        )])

    def compile_operations(self, ctx: AdapterContext, revision_config: Dict[str, Any]) -> List[CompiledOperation]:  # noqa: ANN001
        """会话任务 revision_config = 单条发送描述（prepare_send 构造，服务端冻结）。"""
        op = revision_config.get("operation_descriptor") or {}
        if not op.get("payload_ref") or not op.get("payload_hash"):
            raise BossConversationAdapterError("会话任务 revision_config 缺少 operation_descriptor")
        return [
            CompiledOperation(
                position=1,
                operation=op.get("operation") or OPERATION_MESSAGE_SEND,
                provider_key=op.get("provider_key") or PROVIDER_KEY,
                target_ref=str(op.get("target_ref") or ""),
                target_handle=None,
                target_version=None,
                payload_ref=str(op["payload_ref"]),
                payload_hash=str(op["payload_hash"]),
            )
        ]

    def invocation_receipt_arguments(self, ctx: AdapterContext, target_ref: str) -> Dict[str, str]:  # noqa: ANN001
        """服务端冻结回执策略（submitted 接纳链要求与描述器 receipt_policy 一致）。"""
        from .bindings import _load_binding_row, is_binding_verified_valid

        with _own_connection() as conn:
            binding = _load_binding_row(conn.cursor(), ctx.tenant_id, str(target_ref))
        if binding is not None and is_binding_verified_valid(binding):
            return {"receipt_mode": RECEIPT_POLICY["mode"], "receipt_context": RECEIPT_POLICY["context"]}
        return {}

    def validate_submission_evidence(self, ctx: EvidenceContext) -> bool:  # noqa: ANN001
        """提交证据（applied/submitted）：boss-submission:<request_id>:1 精确匹配。"""
        return (
            ctx.scenario_key == self.scenario_key
            and ctx.evidence_ref == f"{_SUBMISSION_EVIDENCE_PREFIX}:{ctx.request_id}:1"
        )

    def validate_evidence(self, ctx: EvidenceContext) -> bool:  # noqa: ANN001
        """写后验证证据（applied/verified）：boss-send-verifier:<request_id>:<n> 结构
        匹配（生产 verifier 由 B3 接入；本层只做命名空间/归属校验）。"""
        evidence_ref = ctx.evidence_ref or ""
        match = _VERIFIED_EVIDENCE_RE.match(evidence_ref)
        if not match:
            return False
        if match.group(1) != ctx.request_id:
            return False
        if ctx.scenario_key and ctx.scenario_key != self.scenario_key:
            return False
        return True

    def aggregate_result(self, ctx: AdapterContext, delivery_results: List[Dict[str, Any]]) -> RunBusinessResult:  # noqa: ANN001
        """单条发送业务判定（unknown → 需人工核对；completed 语义由任务层判定）。"""
        succeeded = sum(
            1 for d in delivery_results if d.get("effect") == "applied" and d.get("phase") == "verified"
        )
        unknown = sum(
            1
            for d in delivery_results
            if d.get("effect") == "unknown" or d.get("phase") == "unknown" or d.get("state") == "unknown"
        )
        submitted = sum(1 for d in delivery_results if d.get("effect") == "applied"
                        and d.get("phase") == "submitted" and d.get("state") == "succeeded")
        if unknown:
            verdict = "needs_manual_review"
        elif succeeded == len(delivery_results) and succeeded > 0:
            verdict = "reply_delivered"
        elif succeeded + submitted == len(delivery_results) and submitted:
            verdict = "reply_submitted"
        else:
            verdict = "reply_not_delivered"
        return RunBusinessResult(
            verdict=verdict,
            summary=f"total={len(delivery_results)} succeeded={succeeded} submitted={submitted} unknown={unknown}",
        )

    # ==================== 结算（设计 §5.5.4 顺序 3–6） ====================

    def settle_operation_result(self, cursor, result) -> Optional[Dict[str, Any]]:  # noqa: ANN001
        """SAVEPOINT 结算（结构化返回契约，V1.9 变更记录 3）。

        返回 None（normal）| {"status": "anomaly_committed", "reason": 受控码}。
        内部以嵌套 SAVEPOINT boss_settle_inner 包裹结算变更：失败/状态冲突 →
        ROLLBACK TO 撤销本结算写入 → 写异常队列（保留）→ 返回 anomaly_committed
        由通用层升级（阻断+控制请求+审计+ACK）——异常行与升级副作用不回滚，
        语义等价 §5.5.4 顺序 6。

        P1-4（V1.10）：**真正结算异常（DB 错误/补建归属或时间缺失等）同样先
        ROLLBACK TO SAVEPOINT，再写受控 settlement_failed 异常行并返回严格
        anomaly_committed**；只有异常队列自身也无法写入时才继续上抛（由通用层
        ROLLBACK rate_settlement 升级）。

        P1-A（七审）：完整归属解析（_resolve_settle_owner）移入 SAVEPOINT 内执行，
        并以最小 owner facts（受信回执事实）打底——定位阶段异常不再绕过异常队列，
        统一走 settlement_failed 落行 + anomaly_committed 升级。"""
        settlement_effect = _classify_settlement(result)
        delivery_id = str(result["delivery_id"])
        tenant_id = str(result["tenant_id"])
        # P1-A（七审）：先构造**最小 owner facts**（仅受信回执事实，零 DB 读），
        # 创建 SAVEPOINT 后在其内解析完整归属——定位阶段（_resolve_settle_owner）
        # 的 SQL/代码异常同样走 ROLLBACK TO → settlement_failed 异常队列 → 升级，
        # 不再绕过异常队列直接上抛。
        owner = _minimal_settle_owner(result)
        cursor.execute("SAVEPOINT boss_settle_inner")
        try:
            # result_facts 只携带受信回执事实（无 binding/decision 归属列）——从任务行
            # 与 invocation 冻结 business_ref 普通读补齐（operation-result 不取 task 锁，
            # 普通读与 _locate_and_lock_binding 的定位读同语义）；解析失败时 owner 保持
            # 已填充的最小/部分归属，供异常队列落行
            owner = _resolve_settle_owner(cursor, result, base=owner)
            cursor.execute(
                """
                SELECT id, status, settlement_effect FROM bs_boss_conversation_rate_slots
                WHERE tenant_id=%s AND delivery_id=%s FOR UPDATE
                """,
                (tenant_id, delivery_id),
            )
            slot = cursor.fetchone()
            if settlement_effect is None:
                # 明确未开始（effect=none 且 phase ∈ {None, prepared}）
                if slot is None or slot["status"] == "released":
                    cursor.execute("RELEASE SAVEPOINT boss_settle_inner")
                    return None  # 未开始且无 slot 属正常情况，不补建（§5.5.4 顺序 5）
                if slot["status"] == "reserved":
                    cursor.execute(
                        """
                        UPDATE bs_boss_conversation_rate_slots
                        SET status='released', released_at=clock_timestamp(),
                            settlement_effect='not_started', updated_at=clock_timestamp()
                        WHERE id=%s
                        """,
                        (slot["id"],),
                    )
                    cursor.execute("RELEASE SAVEPOINT boss_settle_inner")
                    return None
                # settled 后又报未开始：状态冲突（保守不改判已占额度）
                cursor.execute("ROLLBACK TO SAVEPOINT boss_settle_inner")
                return self._commit_anomaly(cursor, result, owner, "rate_slot_state_conflict")
            # ---- 应占额度（submitted/verified/unknown）----
            if slot is not None and slot["status"] == "reserved":
                cursor.execute(
                    """
                    UPDATE bs_boss_conversation_rate_slots
                    SET status='settled', settled_at=clock_timestamp(),
                        settlement_effect=%s, updated_at=clock_timestamp()
                    WHERE id=%s AND status='reserved'
                    """,
                    (settlement_effect, slot["id"]),
                )
                if settlement_effect == "verified":
                    # 发送 verified → 沟通日志投影入队（同事务，设计 §5.6；
                    # unknown 的人工判定入队属 B4 工作台动作）
                    from .projection import enqueue_projection

                    enqueue_projection(
                        cursor, tenant_id,
                        delivery_id=delivery_id, binding_id=str(owner.get("binding_id") or ""),
                        resume_id=owner.get("resume_id"), user_id=owner.get("user_id"),
                    )
                cursor.execute("RELEASE SAVEPOINT boss_settle_inner")
                return None
            if slot is not None and slot["status"] == "settled":
                # 幂等：重复回执（先到先得，额度已占）
                cursor.execute("RELEASE SAVEPOINT boss_settle_inner")
                return None
            if slot is not None and slot["status"] == "released":
                # 已按"明确未开始"释放却来了应占额度回执：状态冲突（额度漏占）
                cursor.execute("ROLLBACK TO SAVEPOINT boss_settle_inner")
                return self._commit_anomaly(cursor, result, owner, "rate_slot_state_conflict")
            # slot 缺失 → 幂等补建 settled（保守占额度；reserved_at 按 V1.10 冻结
            # 三级依次取用 permit→attempt→invocation 创建时刻；三级全缺升级结算
            # 异常，禁止用回执到达时间拉长窗口，§5.5.4 顺序 5）
            if not owner.get("decision_id") or not owner.get("binding_id"):
                # 归属链不完整：无法精确补建账本行 → 受控结算异常（P1-4 路径：
                # 回滚后写异常队列，返回 anomaly_committed）
                raise BossConversationSettlementError(
                    "rate_slot_backfill_owner_missing",
                    f"task_id={owner.get('task_id')}",
                )
            reserved_at = self._resolve_backfilled_reserved_at(cursor, result)
            cursor.execute(
                """
                INSERT INTO bs_boss_conversation_rate_slots
                    (tenant_id, user_id, binding_id, decision_id, delivery_id, status,
                     reserved_at, settled_at, settlement_effect)
                VALUES (%s, %s, %s, %s, %s, 'settled', %s, clock_timestamp(), %s)
                ON CONFLICT (tenant_id, delivery_id) DO NOTHING
                """,
                (
                    tenant_id, owner.get("user_id"), owner.get("binding_id"),
                    owner.get("decision_id"), delivery_id, reserved_at, settlement_effect,
                ),
            )
        except Exception as exc:  # noqa: BLE001 P1-4：结算异常 → 异常队列 + anomaly_committed
            cursor.execute("ROLLBACK TO SAVEPOINT boss_settle_inner")
            error_code = getattr(exc, "anomaly_error_code", None)
            if not isinstance(error_code, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", error_code):
                error_code = "settlement_failed"
            # P1-A：owner 仅最小/部分归属时，以独立于 _resolve_settle_owner 的最小
            # 普通读补齐受信键（task→binding、invocation→decision）——保证异常队列
            # NOT NULL 键（task/binding/invocation/delivery）可写，注入解析异常时
            # 仍能落行升级；兜底读再失败则保留最小归属（_commit_anomaly 因 NOT NULL
            # 失败会上抛 → 通用层 kind=error 升级，阻断+控制请求+ACK 语义不变）
            owner = _complete_minimal_owner(cursor, result, owner)
            # 异常队列写入失败时让异常继续上抛（通用层 ROLLBACK rate_settlement
            # 升级，不 ACK）——只有这一种情况不返回 anomaly_committed
            return self._commit_anomaly(cursor, result, owner, error_code)
        # 补建成功仍登记异常（slot 缺失本身是不变量破坏，§5.5.4 顺序 5 冻结）
        return self._commit_anomaly(cursor, result, owner, "rate_slot_missing")

    def _resolve_backfilled_reserved_at(self, cursor, result) -> Any:  # noqa: ANN001
        """补建行的 reserved_at（V1.10 冻结三级顺序，DB 侧时间）：permit 创建时刻 →
        attempt 创建（启动）时刻 → invocation 创建时刻；三级全缺 → 受控结算异常
        （P1-4 路径写 settlement/异常队列），**禁止退回报文到达时间**。"""
        permit_id = str(result.get("permit_id") or "")
        if permit_id:
            cursor.execute(
                "SELECT created_at FROM local_tool_operation_permits WHERE id=%s AND tenant_id=%s",
                (permit_id, str(result["tenant_id"])),
            )
            row = cursor.fetchone()
            if row is not None and row["created_at"] is not None:
                return row["created_at"]
        attempt_id = str(result.get("attempt_id") or "")
        if attempt_id:
            cursor.execute(
                "SELECT created_at FROM desktop_automation_attempts WHERE id=%s AND tenant_id=%s",
                (attempt_id, str(result["tenant_id"])),
            )
            row = cursor.fetchone()
            if row is not None and row["created_at"] is not None:
                return row["created_at"]
        invocation_id = str(result.get("invocation_id") or "")
        if invocation_id:
            cursor.execute(
                "SELECT created_at FROM local_tool_invocations WHERE id=%s AND tenant_id=%s",
                (invocation_id, str(result["tenant_id"])),
            )
            row = cursor.fetchone()
            if row is not None and row["created_at"] is not None:
                return row["created_at"]
        raise BossConversationSettlementError(
            "rate_slot_backfill_time_missing",
            f"permit={permit_id or 'none'} attempt={attempt_id or 'none'} invocation={invocation_id or 'none'}",
        )

    def _commit_anomaly(self, cursor, result, owner: Dict[str, Any], error_code: str) -> Dict[str, Any]:  # noqa: ANN001
        """写异常队列（UNIQUE(tenant,delivery) 幂等；只存受控错误码不落敏感详情）
        并返回结构化 anomaly_committed（补建/落账写入保留，由通用层升级）。"""
        cursor.execute(
            """
            INSERT INTO bs_boss_rate_settlement_anomalies
                (tenant_id, user_id, task_id, binding_id, delivery_id, invocation_id, error_code, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending')
            ON CONFLICT (tenant_id, delivery_id) DO NOTHING
            """,
            (
                str(result["tenant_id"]), owner.get("user_id"), owner.get("task_id"),
                owner.get("binding_id"), str(result["delivery_id"]),
                owner.get("invocation_id"), error_code,
            ),
        )
        return {"status": "anomaly_committed", "reason": error_code}

    def serve_payload(self, ctx: AdapterContext, payload_ref: str) -> bytes:  # noqa: ANN001
        """决策冻结正文字节（租户/任务/revision 严格核对 + hash 自检）。"""
        decision_id = parse_payload_ref(payload_ref)
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, task_id, spec_revision, status, reply_text_id, reply_text_hash FROM session_task_decisions WHERE tenant_id=%s AND id=%s",
                (ctx.tenant_id, decision_id),
            )
            decision = cursor.fetchone()
            if decision is None:
                raise BossConversationAdapterError(f"决策不存在: {decision_id}")
            if str(decision["task_id"]) != str(ctx.task_ref):
                raise BossConversationAdapterError("payload_ref 与当前任务不一致（跨会话引用被拒绝）")
            if ctx.revision_ref:
                cursor.execute(
                    "SELECT revision FROM session_task_specs WHERE tenant_id=%s AND id=%s AND task_id=%s",
                    (ctx.tenant_id, ctx.revision_ref, decision["task_id"]),
                )
                spec_row = cursor.fetchone()
                if spec_row is None or int(spec_row["revision"]) != int(decision["spec_revision"]):
                    raise BossConversationAdapterError("决策冻结版本与执行 revision 不一致")
            from src.session_tasks.texts import load_text

            try:
                payload = load_text(conn, ctx.tenant_id, decision["task_id"], decision["reply_text_id"], expected_purpose="decision")
            except Exception as exc:  # noqa: BLE001 解密失败/密钥问题 → fail-closed
                raise BossConversationAdapterError("决策正文不可读（解密失败）") from exc
            text = payload.get("text") if isinstance(payload, dict) else None
            if not isinstance(text, str) or not text:
                raise BossConversationAdapterError("决策正文为空")
            data = text.encode("utf-8")
            actual = hashlib.sha256(data).hexdigest()
            if decision["reply_text_hash"] and actual != decision["reply_text_hash"]:
                raise BossConversationAdapterError("决策正文字节与冻结 hash 不一致")
            return data


def _own_connection():  # noqa: ANN202
    from src.db.database import get_db_connection

    return get_db_connection()


def _decision_ref(invocation: Optional[Dict[str, Any]]) -> str:  # noqa: ANN001
    business_ref = (invocation or {}).get("business_ref") or {}
    return str(business_ref.get("decision_id") or "")


def _delivery_ref(invocation: Optional[Dict[str, Any]]) -> str:  # noqa: ANN001
    business_ref = (invocation or {}).get("business_ref") or {}
    return str(business_ref.get("delivery_id") or "")


def _is_uuid(value: str) -> bool:  # noqa: ANN001
    import uuid as _uuid

    try:
        _uuid.UUID(str(value))
        return True
    except (ValueError, TypeError, AttributeError):
        return False


def _minimal_settle_owner(result: Dict[str, Any]) -> Dict[str, Any]:  # noqa: ANN001
    """最小 owner facts（P1-A 七审）：仅受信回执事实，零 DB 读——SAVEPOINT 创建前
    构造；定位阶段异常时异常队列仍可落行（task/binding NOT NULL 键由兜底读补齐）。"""
    return {
        "task_id": str(result.get("task_id") or "") or None,
        "binding_id": None,
        "user_id": None,
        "decision_id": None,
        "invocation_id": str(result.get("invocation_id") or "") or None,
        "resume_id": None,
    }


def _complete_minimal_owner(cursor, result: Dict[str, Any], owner: Dict[str, Any]) -> Dict[str, Any]:  # noqa: ANN001
    """异常路径兜底（P1-A 七审）：与 _resolve_settle_owner 分离的最小普通读，补齐
    异常队列 NOT NULL 键——task→binding/user、invocation→decision。任何兜底读失败
    静默保留现状（此时 _commit_anomaly 因 NOT NULL 失败会上抛 → 通用层 kind=error
    升级，阻断+控制请求+ACK 语义不受影响）。"""
    tenant_id = str(result["tenant_id"])
    task_id = str(result.get("task_id") or "")
    invocation_id = str(result.get("invocation_id") or "")
    if task_id and not owner.get("binding_id"):
        try:
            cursor.execute(
                "SELECT conversation_binding_id, user_id FROM session_tasks "
                "WHERE tenant_id=%s AND id=%s",
                (tenant_id, task_id),
            )
            task_row = cursor.fetchone()
            if task_row is not None:
                if not owner.get("binding_id") and task_row["conversation_binding_id"]:
                    owner["binding_id"] = str(task_row["conversation_binding_id"])
                if not owner.get("user_id") and task_row["user_id"]:
                    owner["user_id"] = str(task_row["user_id"])
        except Exception:  # noqa: BLE001 兜底读失败不覆盖原始结算异常
            pass
    if invocation_id and not owner.get("decision_id"):
        try:
            cursor.execute(
                "SELECT business_ref FROM local_tool_invocations WHERE tenant_id=%s AND id=%s",
                (tenant_id, invocation_id),
            )
            inv_row = cursor.fetchone()
            if inv_row is not None:
                decision = ((inv_row["business_ref"] or {}).get("decision_id") or None)
                if decision:
                    owner["decision_id"] = str(decision)
        except Exception:  # noqa: BLE001 同上
            pass
    return owner


def _resolve_settle_owner(cursor, result: Dict[str, Any], base: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:  # noqa: ANN001
    """结算归属补齐（普通读，不加任务锁）：task_id/binding_id/user_id/decision_id/
    invocation_id——task 行 conversation_binding_id 与 invocation 冻结 business_ref
    是受信定位链（设计 §5.5.5 binding 定位）。

    P1-A（七审）：base 提供最小 owner facts 时**原地增量填充**（已解析键不重读）；
    定位阶段中途异常时调用方仍持有已填充的部分归属（dict 原地变更保留）。"""
    tenant_id = str(result["tenant_id"])
    task_id = str(result.get("task_id") or "")
    invocation_id = str(result.get("invocation_id") or "")
    if isinstance(base, dict):
        owner = base
        owner.setdefault("task_id", task_id or None)
        owner.setdefault("binding_id", None)
        owner.setdefault("user_id", None)
        owner.setdefault("decision_id", None)
        owner.setdefault("invocation_id", invocation_id or None)
        owner.setdefault("resume_id", None)
    else:
        owner = {
            "task_id": task_id or None, "binding_id": None, "user_id": None,
            "decision_id": None, "invocation_id": invocation_id or None, "resume_id": None,
        }
    if task_id and not owner.get("binding_id"):
        cursor.execute(
            "SELECT conversation_binding_id, user_id FROM session_tasks WHERE tenant_id=%s AND id=%s",
            (tenant_id, task_id),
        )
        task_row = cursor.fetchone()
        if task_row is not None:
            owner["binding_id"] = str(task_row["conversation_binding_id"]) if task_row["conversation_binding_id"] else None
            owner["user_id"] = str(task_row["user_id"]) if task_row["user_id"] else None
    if owner.get("binding_id") and not owner.get("resume_id"):
        cursor.execute(
            "SELECT resume_id FROM bs_boss_conversation_bindings WHERE tenant_id=%s AND id=%s",
            (tenant_id, owner["binding_id"]),
        )
        binding_row = cursor.fetchone()
        if binding_row is not None and binding_row["resume_id"] is not None:
            owner["resume_id"] = int(binding_row["resume_id"])
    if invocation_id and not owner.get("decision_id"):
        cursor.execute(
            "SELECT business_ref, user_id FROM local_tool_invocations WHERE tenant_id=%s AND id=%s",
            (tenant_id, invocation_id),
        )
        inv_row = cursor.fetchone()
        if inv_row is not None:
            owner["decision_id"] = ((inv_row["business_ref"] or {}).get("decision_id") or None)
            if not owner.get("user_id") and inv_row["user_id"]:
                owner["user_id"] = str(inv_row["user_id"])
    return owner


def _classify_settlement(result: Dict[str, Any]) -> Optional[str]:  # noqa: ANN001
    """原始机器事实 → settlement_effect：submitted/verified → 同名；unknown 优先；
    effect=none 且 phase ∈ {None, prepared}（明确未开始）→ None（released）；
    其余保守 settled(unknown)。"""
    effect = str(result.get("effect") or "")
    phase = result.get("phase")
    if phase == "unknown" or effect == "unknown":
        return "unknown"
    if effect == "applied" and phase == "submitted":
        return "submitted"
    if effect == "applied" and phase == "verified":
        return "verified"
    if effect == "none" and phase in (None, PHASE_PREPARED):
        return None
    return "unknown"
