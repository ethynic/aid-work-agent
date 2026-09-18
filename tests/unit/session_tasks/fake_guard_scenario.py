"""B1.2 fake 场景：binding_guard / SAVEPOINT 结算 / 结构化拒绝的测试承载。

仅测试基础设施（不进生产 DDL/注册表）：在 fixture 内自建 `session_task_fake_guard_*`
两张表（binding + rate slot），经 scenario_descriptor.register_scenario 注册
"fake.guard.v1" 描述器，用于验证：
- prepare-send Phase A 门禁（eligible/deferred/terminal + 阻断穿透）；
- write-authorize 结构化拒绝（control_action → 副作用先提交再返回拒绝）；
- operation-result SAVEPOINT 结算与异常升级（settle 失败注入）；
- binding 定位（business_ref.task_ref → task.conversation_binding_id → 锁后重验）。

频控语义按设计 §5.5.2/§5.5.3 的 BOSS 形态简化：日上限（daily_cap）→ terminal；
间隔未满足（last_reserved_at + interval_seconds > now）→ deferred；否则 eligible
计数 +1（去重 last_rate_decision_id）。时间一律 DB 侧 clock_timestamp()，不依赖
宿主机墙钟（DB 时钟比宿主机快约 5.8s）。
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from src.desktop_automation.adapters import (
    AdapterContext,
    AuthorizeDecision,
    CompiledOperation,
    EvidenceContext,
    QuotaScopeSpec,
    RevisionValidation,
    RunBusinessResult,
)

SCENARIO_KEY = "fake.guard.v1"
OPERATION_MESSAGE_SEND = "fake_send_v1"
PROVIDER_KEY = "fake"
QUOTA_WINDOW_SECONDS = 30 * 86400

FAKE_BINDING_DDL = """
CREATE TABLE IF NOT EXISTS session_task_fake_guard_bindings (
    id UUID PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    device_id UUID NOT NULL,
    account_binding_id UUID NOT NULL,
    identity_version INTEGER NOT NULL DEFAULT 1,
    conversation_label TEXT,
    automation_blocked BOOLEAN NOT NULL DEFAULT FALSE,
    automation_block_reason TEXT,
    automation_block_epoch INTEGER NOT NULL DEFAULT 0,
    automation_blocked_at TIMESTAMPTZ,
    rate_trigger_date DATE,
    rate_trigger_count INTEGER NOT NULL DEFAULT 0,
    last_rate_decision_id UUID,
    last_reserved_at TIMESTAMPTZ,
    daily_cap INTEGER NOT NULL DEFAULT 2,
    interval_seconds INTEGER NOT NULL DEFAULT 0
)
"""

FAKE_RATE_SLOT_DDL = """
CREATE TABLE IF NOT EXISTS session_task_fake_guard_rate_slots (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    delivery_id UUID NOT NULL,
    decision_id UUID,
    status TEXT NOT NULL,
    reserved_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    settled_at TIMESTAMPTZ,
    UNIQUE (tenant_id, delivery_id)
)
"""


def create_fake_tables(conn) -> None:  # noqa: ANN001
    cursor = conn.cursor()
    cursor.execute(FAKE_BINDING_DDL)
    # 幂等补列（既有库的旧 fake 表升级）
    for ddl in (
        "ALTER TABLE session_task_fake_guard_bindings ADD COLUMN IF NOT EXISTS identity_version INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE session_task_fake_guard_bindings ADD COLUMN IF NOT EXISTS conversation_label TEXT",
    ):
        cursor.execute(ddl)
    cursor.execute(FAKE_RATE_SLOT_DDL)
    conn.commit()


def drop_fake_tables(conn) -> None:  # noqa: ANN001
    cursor = conn.cursor()
    cursor.execute("DROP TABLE IF EXISTS session_task_fake_guard_rate_slots")
    cursor.execute("DROP TABLE IF EXISTS session_task_fake_guard_bindings")
    conn.commit()


def insert_fake_binding(conn, tenant_id: str, device_id: str, *, daily_cap: int = 2,
                        interval_seconds: int = 0) -> Dict[str, Any]:  # noqa: ANN001
    account_binding_id = str(uuid.uuid4())
    binding_id = str(uuid.uuid4())
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO session_task_fake_guard_bindings
            (id, tenant_id, user_id, device_id, account_binding_id, daily_cap, interval_seconds)
        VALUES (%s, %s, 'user-1', %s, %s, %s, %s)
        """,
        (binding_id, tenant_id, device_id, account_binding_id, daily_cap, interval_seconds),
    )
    conn.commit()
    return {
        "conversation_binding_id": binding_id,
        "account_binding_id": account_binding_id,
        "device_id": device_id,
    }


class FakeGuardResolver:
    """fake 绑定查询面（BindingResolver 契约；仅测试库表）。"""

    def get_binding_by_id(self, cursor, tenant_id: str, binding_id: str) -> Optional[Dict[str, Any]]:  # noqa: ANN001
        cursor.execute(
            """
            SELECT id, tenant_id, user_id, device_id, account_binding_id, automation_blocked,
                   automation_block_epoch
            FROM session_task_fake_guard_bindings WHERE tenant_id=%s AND id=%s
            """,
            (tenant_id, binding_id),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_runtime_identity(self, cursor, tenant_id: str, binding_id: str) -> Optional[Dict[str, Any]]:  # noqa: ANN001
        cursor.execute(
            "SELECT id, identity_version, conversation_label FROM session_task_fake_guard_bindings "
            "WHERE tenant_id=%s AND id=%s",
            (tenant_id, binding_id),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def is_valid_for_allocation(self, conn, tenant_id: str, binding_id: str) -> bool:  # noqa: ANN001
        row = self.get_binding_by_id(conn.cursor(), tenant_id, binding_id)
        return row is not None and not row["automation_blocked"]

    def resolve_draft_targets(self, conn, tenant_id, user_id, device_id, resolution_invocation_id):  # noqa: ANN001
        from src.session_tasks.scenario_descriptor import ScenarioDescriptorError

        raise ScenarioDescriptorError("fake 场景不支持名称定位路径")

    def list_bindings(self, tenant_id, user_id, device_id, limit):  # noqa: ANN001
        from src.session_tasks.scenario_descriptor import ScenarioDescriptorError

        raise ScenarioDescriptorError("fake 场景不支持绑定管理 API")

    def create_binding(self, tenant_id, user_id, device_id, account_binding_id, binding_type, label):  # noqa: ANN001
        from src.session_tasks.scenario_descriptor import ScenarioDescriptorError

        raise ScenarioDescriptorError("fake 场景不支持绑定管理 API")

    def ensure_valid_for_publish(self, binding: Dict[str, Any]) -> None:  # noqa: ANN001
        return None

    def runtime_target_policy(self, binding_row):  # noqa: ANN001
        return None

    def account_identity_version(self, binding_row):  # noqa: ANN001
        return 0


class FakeGuard:
    """fake binding 同步门禁（BindingGuard 契约；BOSS §5.5.2 形态的测试等价物）。

    职责切分（CR 冻结）：guard 只做锁/检查/落库；纯计算判断在模块级
    fake_send_eligibility_gate（send_eligibility_gate 契约），由 gate_transaction
    在锁内调用。
    """

    def lock_binding(self, cursor, tenant_id: str, binding_id: str) -> Optional[Dict[str, Any]]:  # noqa: ANN001
        cursor.execute(
            "SELECT * FROM session_task_fake_guard_bindings WHERE tenant_id=%s AND id=%s FOR UPDATE",
            (tenant_id, binding_id),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def check_blocked(self, binding_row) -> Optional[str]:  # noqa: ANN001
        """已锁定行上的同步阻断检查（幂等重 prepare 只锁不计数路径）。
        返回受控码（V1.9 terminal reason 契约）。"""
        if binding_row is not None and binding_row.get("automation_blocked"):
            return "automation_blocked"
        return None

    def gate_transaction(self, cursor, task: Dict[str, Any], decision: Dict[str, Any],
                         gate) -> Dict[str, Any]:  # noqa: ANN001
        # 1) 锁场景 binding 行（调用方已持 subject→task→assignment→decision 锁）
        row = self.lock_binding(cursor, task["tenant_id"], str(task["conversation_binding_id"]))
        if row is None:
            return {"terminal": "human_required", "reason": "binding_missing"}
        # 2) 同步阻断硬门禁（blocked → terminal，不调 gate）
        blocked = self.check_blocked(row)
        if blocked is not None:
            return {"terminal": "human_required", "reason": blocked}
        # 3) 跨日归一化（guard 持有的持久化职责；Asia/Shanghai 简化为 DB 当日）
        cursor.execute("SELECT (clock_timestamp() AT TIME ZONE 'Asia/Shanghai')::date AS d")
        today = cursor.fetchone()["d"]
        if row["rate_trigger_date"] != today:
            cursor.execute(
                "UPDATE session_task_fake_guard_bindings SET rate_trigger_date=%s, rate_trigger_count=0 "
                "WHERE tenant_id=%s AND id=%s",
                (today, task["tenant_id"], str(task["conversation_binding_id"])),
            )
        # 4) 纯计算 send_eligibility_gate（只读，零写入；V1.9 冻结职责切分）
        outcome = gate(cursor, task, decision)
        name = (
            "eligible" if outcome.get("eligible") is True else
            "deferred" if outcome.get("deferred") is True else
            "terminal" if "terminal" in outcome else None
        ) if isinstance(outcome, dict) else None
        if name not in ("eligible", "deferred"):
            # terminal 或畸形词汇原样上交（持久化职责只覆盖 eligible/deferred；
            # 畸形词汇由通用层 V1.9 严格判别校验 fail-closed）
            return outcome
        # 5) 落库（去重 last_rate_decision_id：已计数的同 decision 不重复 +1；
        # reserved_at 仅 eligible 首次计数推进——deferred/重复计数不占发送时刻）。
        # effective_count 非法（畸形 eligible/deferred）→ 原样上交，通用层 fail-closed
        effective_count = outcome.get("effective_count")
        if type(effective_count) is not int or effective_count < 1:
            return outcome
        already_counted = str(row["last_rate_decision_id"] or "") == str(decision["id"])
        cursor.execute(
            """
            UPDATE session_task_fake_guard_bindings
            SET rate_trigger_count=%s, last_rate_decision_id=%s,
                last_reserved_at=CASE WHEN %s THEN last_reserved_at ELSE clock_timestamp() END
            WHERE tenant_id=%s AND id=%s
            """,
            (effective_count, str(decision["id"]), name == "deferred" or already_counted,
             task["tenant_id"], str(task["conversation_binding_id"])),
        )
        return outcome

    def block_binding(self, cursor, task: Dict[str, Any], reason: str) -> int:  # noqa: ANN001
        cursor.execute(
            """
            UPDATE session_task_fake_guard_bindings
            SET automation_blocked=TRUE, automation_block_reason=%s,
                automation_block_epoch=automation_block_epoch+1, automation_blocked_at=clock_timestamp()
            WHERE tenant_id=%s AND id=%s
            RETURNING automation_block_epoch
            """,
            (reason, task["tenant_id"], str(task["conversation_binding_id"])),
        )
        return int(cursor.fetchone()["automation_block_epoch"])


def fake_send_eligibility_gate(cursor, task: Dict[str, Any], decision: Dict[str, Any]) -> Dict[str, Any]:
    """纯计算发送门禁（send_eligibility_gate 契约，设计 §4.1/§5.5.2）。

    只读（复用 guard 已锁定的行，同一 cursor 同一事务一致读），零写入；
    返回冻结词汇之一，畸形由通用层 fail-closed。时间口径 DB 侧 clock_timestamp。
    """
    cursor.execute(
        "SELECT daily_cap, interval_seconds, rate_trigger_date, rate_trigger_count, "
        "last_rate_decision_id, last_reserved_at FROM session_task_fake_guard_bindings "
        "WHERE tenant_id=%s AND id=%s",
        (task["tenant_id"], str(task["conversation_binding_id"])),
    )
    row = cursor.fetchone()
    if row is None:
        return {"terminal": "human_required", "reason": "binding_missing"}
    cursor.execute(
        "SELECT (clock_timestamp() AT TIME ZONE 'Asia/Shanghai')::date AS d, clock_timestamp() AS ts"
    )
    now_row = cursor.fetchone()
    today, now_ts = now_row["d"], now_row["ts"]
    stored = int(row["rate_trigger_count"] or 0)
    if row["rate_trigger_date"] != today:
        stored = 0
    already_counted = str(row["last_rate_decision_id"] or "") == str(decision["id"])
    effective_count = stored if already_counted else stored + 1
    if int(row["daily_cap"]) > 0 and stored >= int(row["daily_cap"]):
        return {"terminal": "human_required", "reason": "rate_limit"}
    interval = int(row["interval_seconds"] or 0)
    if not already_counted and interval > 0 and row["last_reserved_at"] is not None:
        from datetime import timedelta, timezone as _tz

        eligible_at = row["last_reserved_at"] + timedelta(seconds=interval)
        if eligible_at.tzinfo is None:
            eligible_at = eligible_at.replace(tzinfo=_tz.utc)
        if eligible_at > now_ts:
            retry_ms = max(int((eligible_at - now_ts).total_seconds() * 1000), 1)
            return {
                "deferred": True,
                "effective_count": effective_count,
                "server_now": now_ts.astimezone(_tz.utc),
                "deferred_until": eligible_at.astimezone(_tz.utc),
                "retry_after_ms": retry_ms,
                "deferred_reason": "rate_interval",
                "response_revision": int(task["control_epoch"]) * 1000 + effective_count,
            }
    return {"eligible": True, "effective_count": effective_count}


class FakeGuardAdapter:
    """fake 场景适配器：authorize 复判 + SAVEPOINT 结算（可注入失败）+ 结构化拒绝。"""

    scenario_key = SCENARIO_KEY
    settle_should_raise = False
    settle_anomaly_reason = None  # 注入 anomaly_committed（补建保留+升级，CR 阻断 9）
    settle_calls: list = []

    def __init__(self) -> None:
        self.guard = FakeGuard()

    # ---- ScenarioAdapter 协议 ----

    def validate_revision(self, ctx, revision_config):  # noqa: ANN001
        return RevisionValidation(ok=True, schedule_specs=[])

    def resolve_target(self, ctx, target_ref):  # noqa: ANN001
        from src.desktop_automation.adapters import TargetResolution

        return TargetResolution(ok=True, target_handle=f"handle:{target_ref}", target_version="iv-1")

    def authorize_operation(
        self, ctx: AdapterContext, *, operation, target_ref, target_version, payload_hash,
        authorization_revision, authorization_epoch, invocation=None, cursor=None,
    ):  # noqa: ANN001
        # write-authorize 权威复判（许可事务同一 cursor）：锁 binding → 阻断/频控复判。
        # cursor=None 为 executor.precheck_quota 只读预检（invocation 创建前）：
        # 降级放行默认额度（许可签发路径才是权威判定，与微信适配器两路径同构）。
        if cursor is None:
            return AuthorizeDecision(allowed=True, quota_scopes=[QuotaScopeSpec(
                scope_type="task", scope_id=f"{self.scenario_key}:{ctx.task_ref}",
                limit_count=10, window_seconds=QUOTA_WINDOW_SECONDS,
            )])
        business_ref = (invocation or {}).get("business_ref") or {}
        task_ref = str(business_ref.get("task_ref") or ctx.task_ref or "")
        cursor.execute(
            "SELECT id, tenant_id, control_epoch, conversation_binding_id FROM session_tasks "
            "WHERE tenant_id=%s AND id=%s",
            (ctx.tenant_id, task_ref),
        )
        task_row = cursor.fetchone()
        if task_row is None or not task_row["conversation_binding_id"]:
            return AuthorizeDecision(allowed=False, reason="binding_location:task_missing")
        binding_row = self.guard.lock_binding(cursor, ctx.tenant_id, str(task_row["conversation_binding_id"]))
        if binding_row is None:
            return AuthorizeDecision(allowed=False, reason="binding_location:binding_missing")
        if binding_row["automation_blocked"]:
            return AuthorizeDecision(
                allowed=False,
                reason=f"automation_blocked:{binding_row['automation_block_reason'] or 'blocked'}",
                control_action="block",
                audit_code="fake_binding_blocked",
            )
        stored = int(binding_row["rate_trigger_count"] or 0)
        cursor.execute("SELECT (clock_timestamp() AT TIME ZONE 'Asia/Shanghai')::date AS d")
        if binding_row["rate_trigger_date"] != cursor.fetchone()["d"]:
            stored = 0
        if int(binding_row["daily_cap"]) > 0 and stored >= int(binding_row["daily_cap"]):
            # 三分类：日上限 → 保守转人工（阻断 + 控制请求，先提交再拒绝）
            return AuthorizeDecision(
                allowed=False,
                reason="rate_daily_cap",
                control_action="human_required",
                audit_code="fake_rate_daily_cap",
            )
        return AuthorizeDecision(allowed=True, quota_scopes=[QuotaScopeSpec(
            scope_type="task", scope_id=f"{self.scenario_key}:{task_ref}",
            limit_count=10, window_seconds=QUOTA_WINDOW_SECONDS,
        )])

    def compile_operations(self, ctx, revision_config):  # noqa: ANN001
        op = revision_config.get("operation_descriptor") or {}
        return [CompiledOperation(
            position=1,
            operation=op.get("operation") or OPERATION_MESSAGE_SEND,
            provider_key=op.get("provider_key") or PROVIDER_KEY,
            target_ref=str(op.get("target_ref") or ""),
            payload_ref=str(op["payload_ref"]),
            payload_hash=str(op["payload_hash"]),
        )]

    def invocation_receipt_arguments(self, ctx, target_ref):  # noqa: ANN001
        """服务端冻结回执策略（executor 经 getattr 可选调用；供 submitted 接纳链）。"""
        return {"receipt_mode": "submission", "receipt_context": "fake_context"}

    def aggregate_result(self, ctx, delivery_results):  # noqa: ANN001
        return RunBusinessResult(verdict="reply_delivered", summary=None)

    def validate_submission_evidence(self, ctx: EvidenceContext) -> bool:
        return ctx.scenario_key == self.scenario_key and ctx.evidence_ref == f"fake-submission:{ctx.request_id}:1"

    def validate_evidence(self, ctx: EvidenceContext) -> bool:
        ref = ctx.evidence_ref or ""
        return (
            ctx.scenario_key == self.scenario_key
            and ref.startswith("fake-evidence:")
            and ref.split(":")[1] == ctx.request_id
        )

    def settle_operation_result(self, cursor, result):  # noqa: ANN001
        """fake 结算（结构化结果，CR 阻断 9）：reserved 落账 → settled。

        返回 None（normal）/ {"status": "anomaly_committed", "reason": ...}
        （补建行保留且通用层升级阻断+控制请求）；抛异常 → SAVEPOINT 回滚撤销补建。
        settle_anomaly_reason 注入 anomaly_committed 路径。
        """
        type(self).settle_calls.append(dict(result))
        if type(self).settle_should_raise:
            raise RuntimeError("injected settle failure")
        if result.get("effect") != "none":
            cursor.execute(
                """
                INSERT INTO session_task_fake_guard_rate_slots (tenant_id, delivery_id, status)
                VALUES (%s, %s, 'reserved')
                ON CONFLICT (tenant_id, delivery_id) DO UPDATE SET status='settled', settled_at=clock_timestamp()
                """,
                (result["tenant_id"], result["delivery_id"]),
            )
            cursor.execute(
                "UPDATE session_task_fake_guard_rate_slots SET status='settled', settled_at=clock_timestamp() "
                "WHERE tenant_id=%s AND delivery_id=%s",
                (result["tenant_id"], result["delivery_id"]),
            )
        if type(self).settle_anomaly_reason:
            # anomaly_committed：上面的补建写入保留（不回滚），由通用层升级异常
            return {"status": "anomaly_committed", "reason": type(self).settle_anomaly_reason}
        return None

    def serve_payload(self, ctx, payload_ref):  # noqa: ANN001
        # 复用微信实现（只读通用表，场景无关）
        from src.weixin_conversation.adapters import WeixinConversationAdapter

        return WeixinConversationAdapter().serve_payload(ctx, payload_ref)


class FakeGuardHooks:
    """决策钩子：委托微信实现（prompt/校验为场景无关的通用结构），换 scenario_key。"""

    scenario_key = SCENARIO_KEY
    execution_lane = "session_task"

    def __init__(self) -> None:
        from src.weixin_conversation.registration import _ConversationHooks

        self._inner = _ConversationHooks()

    def __getattr__(self, name):  # noqa: ANN204
        return getattr(self._inner, name)


class FakeGuardDescriptor:
    """fake.guard.v1 描述器（BindingGuard + settle + 结构化拒绝的测试承载）。"""

    scenario_key = SCENARIO_KEY

    def __init__(self) -> None:
        from src.session_tasks.models import validate_task_spec

        self.spec_validator = validate_task_spec
        self.required_send_capability = "fake_send_v1"
        self.operation_descriptor = {
            "operation": OPERATION_MESSAGE_SEND,
            "provider_key": PROVIDER_KEY,
            "target_ref_source": "conversation_binding_id",
        }
        self.receipt_policy = {
            "mode": "submission",
            "context": "fake_context",
            "submission_evidence_namespace": "fake-submission",
            "verified_evidence_namespace": None,
        }
        self.binding_resolver = FakeGuardResolver()
        self.decision_hooks = FakeGuardHooks()
        self.adapter = FakeGuardAdapter()
        self.workbench_label_resolver = _fake_label
        # 纯计算 gate 与 guard 成对注册（CR 阻断 4：gate/guard 职责切分与一致性）
        self.send_eligibility_gate = fake_send_eligibility_gate
        self.binding_guard = FakeGuard()
        self.scenario_enabled = staticmethod(lambda tenant_id: True)


def _fake_label(cursor, tenant_id: str, binding_id: str):  # noqa: ANN001
    cursor.execute(
        "SELECT 'fake-binding' AS conversation_label FROM session_task_fake_guard_bindings "
        "WHERE tenant_id=%s AND id=%s",
        (tenant_id, binding_id),
    )
    row = cursor.fetchone()
    return row["conversation_label"] if row else None


def build_fake_guard_descriptor() -> FakeGuardDescriptor:
    return FakeGuardDescriptor()
