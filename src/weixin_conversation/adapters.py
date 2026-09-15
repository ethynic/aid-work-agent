"""WeixinConversationAdapter：weixin.conversation.v1 场景适配器（C3，设计 §10）。

复用单条执行底座：每次 reply/opening 决策经 accept_manual_trigger → 单条
weixin_message_send_v2 delivery → invocation（execution_lane='session_task'），
无第二套发送账本。

- serve_payload：解密决策冻结正文（严格核对 tenant/task/spec_revision，跨任务
  decision_id 一律拒绝）+ sha256 自检（不一致 fail-closed）。
- authorize_operation：场景热读门控 + 任务 active + 决策 ready 未 superseded +
  input_version 仍当前（准备后新消息 → revoke）+ 绑定 verified + 正文 hash 命中；
  配额 scope 为任务层发送计数（limit=max_replies）。
"""
from __future__ import annotations

import base64
import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.db.database import get_db_connection
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

from .constants import (
    BINDING_VERIFIED,
    OPERATION_MESSAGE_SEND,
    PROVIDER_KEY,
    SCENARIO_KEY,
)
from .render import parse_payload_ref

_EVIDENCE_REF_RE = re.compile(r"^weixin-evidence:[^:\s]+:\d+$")


def _work_window_open(window: Dict[str, Any]) -> bool:
    """工作时段判断（UTC；跨日窗口支持；时段外拒绝签发新许可）。"""
    now = datetime.now(timezone.utc)
    hour = now.hour
    start, end = int(window.get("start_hour_utc", 0)), int(window.get("end_hour_utc", 0)) % 24
    weekdays = window.get("weekdays_utc") or list(range(7))
    if now.weekday() not in weekdays:
        return False
    if start <= end:
        return start <= hour < end or start == end
    return hour >= start or hour < end
_QUOTA_WINDOW_SECONDS = 30 * 86400


class ConversationAdapterError(Exception):
    """受控载荷/目标解析失败（fail-closed）。"""


def _to_uuid_text(value: Any) -> Optional[str]:
    import uuid as _uuid

    try:
        return str(_uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return None


def load_conversation_binding(tenant_id: str, binding_id: str) -> Optional[Dict[str, Any]]:
    key = _to_uuid_text(binding_id)
    if key is None:
        return None
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, tenant_id, user_id, device_id, account_binding_id, conversation_type,
                   identity_version, verification_status, verified_at, verifier_version, expires_at
            FROM bs_weixin_conversation_bindings WHERE tenant_id=%s AND id=%s
            """,
            (tenant_id, key),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def binding_verified(binding: Optional[Dict[str, Any]]) -> bool:
    """真机验证有效性（与 C1 _binding_valid_for_allocation 同口径）。"""
    from .name_contexts import is_name_context, name_context_valid
    if is_name_context(binding):
        return name_context_valid(binding)
    if not binding or binding.get("verification_status") != BINDING_VERIFIED:
        return False
    if int(binding.get("identity_version") or 0) < 1:
        return False
    if not binding.get("verified_at") or not binding.get("verifier_version") or binding.get("expires_at") is None:
        return False
    expires_at = binding["expires_at"]
    expires_at = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=timezone.utc)
    return expires_at > datetime.now(timezone.utc)


class WeixinConversationAdapter:
    """微信自由会话场景适配器（V1：单会话、单条文字发送）。"""

    scenario_key = SCENARIO_KEY

    # ==================== 场景适配器协议 ====================

    def validate_revision(self, ctx: AdapterContext, revision_config: Dict[str, Any]) -> RevisionValidation:
        """会话任务不经时间/事件触发（manual trigger per decision）；配置形状在
        compile_operations 内校验，此处仅保留协议完整性。"""
        return RevisionValidation(ok=True, schedule_specs=[])

    def resolve_target(self, ctx: AdapterContext, target_ref: str) -> TargetResolution:
        """conversation_binding → 短期 handle + 身份版本（verified 才可达）。"""
        binding = load_conversation_binding(ctx.tenant_id, str(target_ref))
        if binding is None or str(binding.get("tenant_id")) != ctx.tenant_id:
            return TargetResolution(ok=False, reason="conversation_binding_not_found")
        if not binding_verified(binding):
            return TargetResolution(ok=False, reason=f"conversation_binding_state:{binding.get('verification_status')}")
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
    ) -> AuthorizeDecision:
        """许可事务内场景授权链（设计 §10：实时开关/epoch/fence/预算/目标都在链中）。

        底座许可事务已锁 task subject 校验 status/epoch；此处复验场景级条件：
        双开关热读（session_tasks 总门控 + 场景门控）、操作名、任务 active、
        **精确执行归属**（invocation.business_ref.decision_id → execution_links 与
        本次 invocation 一致；hash 只校验正文不充当决策标识）、决策 ready 未
        superseded、input_version 仍为当前、当前 assignment/fence/租约/设备、
        绑定 verified 且身份版本与冻结 target_version 一致、工作时段内。
        invocation=None 为 executor 预检上下文（invocation 创建前）：降级为
        hash+linked 反查并跳过 assignment 段——许可签发（携带 invocation）才是
        发送前授权的权威判定。
        """
        from .config import scenario_enabled

        if not scenario_enabled(ctx.tenant_id):
            return AuthorizeDecision(allowed=False, reason="weixin_conversation_disabled")
        from src.session_tasks.config import tenant_allowed as session_tasks_tenant_allowed

        if not session_tasks_tenant_allowed(ctx.tenant_id):
            return AuthorizeDecision(allowed=False, reason="session_tasks_disabled")
        if operation != OPERATION_MESSAGE_SEND:
            return AuthorizeDecision(allowed=False, reason=f"unsupported_operation:{operation}")
        # invocation=None：executor.precheck_quota 在 invocation 创建前的只读预检——
        # 降级路径（hash+linked 反查、跳过 assignment/设备强校验）；许可签发路径
        # （write_authorize 携带 invocation）走严格归属校验，两路径都以许可签发为准
        strict = invocation is not None
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, user_id, status, spec_revision, current_spec_id, control_epoch,
                       conversation_binding_id
                FROM session_tasks WHERE tenant_id=%s AND id=%s
                """,
                (ctx.tenant_id, str(ctx.task_ref)),
            )
            task = cursor.fetchone()
            if task is None:
                return AuthorizeDecision(allowed=False, reason="session_task_missing")
            if task["status"] != "active":
                return AuthorizeDecision(allowed=False, reason=f"session_task_status:{task['status']}")
            if ctx.user_id and task["user_id"] != ctx.user_id:
                return AuthorizeDecision(allowed=False, reason="not_owner")
            # 授权版本：run.revision_ref 必须仍是任务当前 spec
            if authorization_revision and str(task["current_spec_id"]) != str(authorization_revision):
                return AuthorizeDecision(allowed=False, reason="spec_revision_switched")
            # 决策定位（精确执行归属，许可路径）：invocation.business_ref.decision_id 是
            # 服务端冻结的本次执行归属；hash 只负责校验正文一致，不充当决策标识。
            # 映射缺失/不一致一律拒绝（首发路径 links 在 prepare Phase C 已写入且早于
            # claim；fallback 经 delivery.payload_ref 兼容崩溃窗口重试）
            business_ref = (invocation or {}).get("business_ref") or {}
            decision_id = str(business_ref.get("decision_id") or "")
            if strict:
                if not decision_id:
                    delivery_id = str(business_ref.get("delivery_id") or "")
                    cursor.execute(
                        "SELECT payload_ref FROM desktop_automation_deliveries WHERE tenant_id=%s AND id=%s",
                        (ctx.tenant_id, delivery_id),
                    )
                    delivery_row = cursor.fetchone()
                    try:
                        decision_id = parse_payload_ref(delivery_row["payload_ref"]) if delivery_row and delivery_row["payload_ref"] else ""
                    except ValueError:
                        decision_id = ""
                if not decision_id:
                    return AuthorizeDecision(allowed=False, reason="decision_ref_missing")
                cursor.execute(
                    """
                    SELECT id, task_id, status, action, input_version, spec_revision, reply_text_hash,
                           decision_kind
                    FROM session_task_decisions
                    WHERE tenant_id=%s AND id=%s
                    """,
                    (ctx.tenant_id, decision_id),
                )
                decision = cursor.fetchone()
                if decision is None or str(decision["task_id"]) != str(task["id"]):
                    return AuthorizeDecision(allowed=False, reason="decision_not_found")
                if invocation.get("id") is not None:
                    cursor.execute(
                        "SELECT invocation_id FROM session_task_execution_links WHERE tenant_id=%s AND decision_id=%s",
                        (ctx.tenant_id, decision_id),
                    )
                    link_row = cursor.fetchone()
                    if link_row is not None and str(link_row["invocation_id"] or "") != str(invocation["id"]):
                        return AuthorizeDecision(allowed=False, reason="execution_link_mismatch")
            else:
                # 预检路径（invocation 未创建）：同正文多决策时优先取"已链接且 ready"者；
                # 最终归属由许可路径严格判定（错绑只影响预检，不产生许可）
                cursor.execute(
                    """
                    SELECT d.id, d.task_id, d.status, d.action, d.input_version, d.spec_revision, d.reply_text_hash,
                           d.decision_kind
                    FROM session_task_decisions d
                    WHERE d.tenant_id=%s AND d.task_id=%s AND d.reply_text_hash=%s
                    ORDER BY (d.status='ready' AND COALESCE(d.action, 'reply')='reply') DESC,
                        (EXISTS (
                           SELECT 1 FROM session_task_execution_links l
                           WHERE l.tenant_id=d.tenant_id AND l.decision_id=d.id
                        )) DESC, d.created_at DESC
                    """,
                    (ctx.tenant_id, task["id"], payload_hash or ""),
                )
                decision = cursor.fetchone()
                if decision is None:
                    return AuthorizeDecision(allowed=False, reason="decision_not_found")
            if not payload_hash:
                return AuthorizeDecision(allowed=False, reason="payload_hash_required")
            if decision["status"] != "ready" or (decision["action"] or "reply") != "reply":
                return AuthorizeDecision(allowed=False, reason=f"decision_state:{decision['status']}")
            if decision["spec_revision"] != task["spec_revision"]:
                return AuthorizeDecision(allowed=False, reason="decision_spec_stale")
            if decision["reply_text_hash"] and payload_hash != decision["reply_text_hash"]:
                return AuthorizeDecision(allowed=False, reason="payload_hash_not_frozen")
            # input_version 复验（§9）：决策之后有更新批次被接纳 → 旧决策不得发送
            cursor.execute(
                """
                SELECT COALESCE(MAX(input_version), 0) AS cur
                FROM session_task_batches
                WHERE tenant_id=%s AND task_id=%s AND status='accepted' AND synthetic=FALSE
                """,
                (ctx.tenant_id, task["id"]),
            )
            cur_version = int(cursor.fetchone()["cur"])
            dec_version = int(decision["input_version"] or 0)
            if dec_version == 0:
                pass  # opening 合成版本 0：无已接纳普通批次即视为当前（有则 revoke）
            elif cur_version != dec_version:
                return AuthorizeDecision(allowed=False, reason="decision_superseded")
            if dec_version == 0 and cur_version > 0:
                return AuthorizeDecision(allowed=False, reason="decision_superseded")
            # 绑定仍 verified，且当前身份版本与冻结 target_version 一致（身份漂移拒发）
            binding = load_conversation_binding(ctx.tenant_id, str(task["conversation_binding_id"]))
            if not binding_verified(binding):
                return AuthorizeDecision(allowed=False, reason="conversation_binding_invalid")
            if target_version:
                identity_version = int(binding.get("identity_version") or 0)
                if f"iv-{identity_version}" != str(target_version):
                    return AuthorizeDecision(allowed=False, reason="target_version_drift")
            # 当前 assignment/fence/租约/设备复验（仅许可路径：准备阶段通过不能代替
            # 发送前授权；预检路径 invocation 未建，无 assignment 上下文）
            assignment_id = str(business_ref.get("assignment_id") or "")
            frozen_fence = business_ref.get("fence")
            if strict and (not assignment_id or frozen_fence is None):
                return AuthorizeDecision(allowed=False, reason="assignment_ref_missing")
            if not strict:
                assignment_id = ""
            if strict:
                cursor.execute(
                    """
                    SELECT fence, device_id, is_current, lease_expires_at
                    FROM session_task_assignments WHERE tenant_id=%s AND id=%s
                    """,
                    (ctx.tenant_id, assignment_id),
                )
                assignment = cursor.fetchone()
                if assignment is None or not assignment["is_current"]:
                    return AuthorizeDecision(allowed=False, reason="assignment_not_current")
                if int(assignment["fence"]) != int(frozen_fence):
                    return AuthorizeDecision(allowed=False, reason="assignment_fence_stale")
                if str(assignment["device_id"]) != str(invocation.get("device_id") or ""):
                    return AuthorizeDecision(allowed=False, reason="assignment_device_mismatch")
                lease_expires = assignment["lease_expires_at"]
                lease_expires = lease_expires if lease_expires.tzinfo else lease_expires.replace(tzinfo=timezone.utc)
                if lease_expires <= datetime.now(timezone.utc):
                    return AuthorizeDecision(allowed=False, reason="assignment_lease_expired")
            # 任务层发送计数配额（与 prepare-send 的 max_replies 校验双保险）+ 工作时段
            cursor.execute(
                "SELECT limits_json, work_window_json FROM session_task_specs WHERE tenant_id=%s AND id=%s",
                (ctx.tenant_id, str(task["current_spec_id"])),
            )
            spec_row = cursor.fetchone()
            import json as _json

            limits = _json.loads(spec_row["limits_json"]) if spec_row and spec_row["limits_json"] else {}
            # 任务截止期硬门禁（A3）：后台评估未及触达的窗口内也不得签发新许可
            _raw_expires = limits.get("expires_at")
            if _raw_expires:
                try:
                    _exp = datetime.fromisoformat(str(_raw_expires).replace("Z", "+00:00"))
                except ValueError:
                    _exp = None
                if _exp is not None:
                    _exp = _exp if _exp.tzinfo else _exp.replace(tzinfo=timezone.utc)
                    if _exp <= datetime.now(timezone.utc):
                        return AuthorizeDecision(allowed=False, reason="task_deadline_exceeded")
            max_replies = int(limits.get("max_replies") or 0) or 1
            work_window = None
            if spec_row and spec_row["work_window_json"]:
                try:
                    work_window = _json.loads(spec_row["work_window_json"])
                except (ValueError, TypeError):
                    work_window = None
        if work_window and not _work_window_open(work_window):
            return AuthorizeDecision(allowed=False, reason="work_window_closed")
        scopes = [
            QuotaScopeSpec(
                scope_type="task",
                scope_id=f"{self.scenario_key}:{ctx.task_ref}",
                limit_count=max_replies,
                window_seconds=_QUOTA_WINDOW_SECONDS,
            )
        ]
        return AuthorizeDecision(allowed=True, quota_scopes=scopes)

    def compile_operations(self, ctx: AdapterContext, revision_config: Dict[str, Any]) -> List[CompiledOperation]:
        """会话任务 revision_config = 单条发送描述（prepare_send 构造，服务端冻结）。"""
        op = revision_config.get("operation_descriptor") or {}
        if not op.get("payload_ref") or not op.get("payload_hash"):
            raise ConversationAdapterError("会话任务 revision_config 缺少 operation_descriptor")
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

    def invocation_receipt_arguments(self, ctx: AdapterContext, target_ref: str) -> Dict[str, str]:
        """Server-only frozen receipt policy; never derived from provider input."""
        from .name_contexts import is_name_context, name_context_valid
        binding = load_conversation_binding(ctx.tenant_id, target_ref)
        if is_name_context(binding) and name_context_valid(binding):
            return {"receipt_mode": "submission", "receipt_context": "weixin_name"}
        return {}

    def validate_submission_evidence(self, ctx: EvidenceContext) -> bool:
        return ctx.scenario_key == self.scenario_key and ctx.evidence_ref == f"weixin-submission:{ctx.request_id}:1"

    def aggregate_result(self, ctx: AdapterContext, delivery_results: List[Dict[str, Any]]) -> RunBusinessResult:
        """单条发送的业务判定（unknown → 需人工核对；completed 语义由任务层判定）。"""
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

    def validate_evidence(self, ctx: EvidenceContext) -> bool:
        """结构绑定校验（weixin provider 的证据命名空间 + 场景/请求绑定）。"""
        evidence_ref = ctx.evidence_ref or ""
        if not _EVIDENCE_REF_RE.match(evidence_ref):
            return False
        _namespace, request_component, _seq = evidence_ref.split(":", 2)
        if request_component != ctx.request_id:
            return False
        if ctx.scenario_key and ctx.scenario_key != self.scenario_key:
            return False
        return True

    def serve_payload(self, ctx: AdapterContext, payload_ref: str) -> bytes:
        """决策冻结正文字节（租户/任务/revision 严格核对 + hash 自检）。"""
        decision_id = parse_payload_ref(payload_ref)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, task_id, spec_revision, status, reply_text_id, reply_text_hash FROM session_task_decisions WHERE tenant_id=%s AND id=%s",
                (ctx.tenant_id, decision_id),
            )
            decision = cursor.fetchone()
            if decision is None:
                raise ConversationAdapterError(f"决策不存在: {decision_id}")
            if str(decision["task_id"]) != str(ctx.task_ref):
                raise ConversationAdapterError("payload_ref 与当前任务不一致（跨会话引用被拒绝）")
            # run.revision_ref = spec 行 id；核对决策冻结版本与该 spec 行一致
            if ctx.revision_ref:
                cursor.execute(
                    "SELECT revision FROM session_task_specs WHERE tenant_id=%s AND id=%s AND task_id=%s",
                    (ctx.tenant_id, ctx.revision_ref, decision["task_id"]),
                )
                spec_row = cursor.fetchone()
                if spec_row is None or int(spec_row["revision"]) != int(decision["spec_revision"]):
                    raise ConversationAdapterError("决策冻结版本与执行 revision 不一致")
            from src.session_tasks.texts import load_text

            try:
                payload = load_text(conn, ctx.tenant_id, decision["task_id"], decision["reply_text_id"], expected_purpose="decision")
            except Exception as exc:  # noqa: BLE001 解密失败/密钥问题 → fail-closed
                raise ConversationAdapterError("决策正文不可读（解密失败）") from exc
            text = payload.get("text") if isinstance(payload, dict) else None
            if not isinstance(text, str) or not text:
                raise ConversationAdapterError("决策正文为空")
            data = text.encode("utf-8")
            actual = hashlib.sha256(data).hexdigest()
            if decision["reply_text_hash"] and actual != decision["reply_text_hash"]:
                raise ConversationAdapterError("决策正文字节与冻结 hash 不一致")
            return data
