"""端侧会话任务服务层（C1）。

权威契约：设计 §4/§5/§9/§10/§13。云端是发布策略、control_epoch、任务终态与
计费的权威；本层实现任务生命周期 CAS、会话占用唯一、assignment/fence/租约、
events 连续前缀 ACK、决策记录幂等与预算预留接口（C3 接真实账务）。

并发与锁序（设计 §10）：任务控制写统一先锁 session_tasks 行（FOR UPDATE），
再写 assignment/spec/decision 等子行，不反向加锁；claim/renew/events 同一
task/assignment 行锁事务内完成。UUID 全部由服务端生成，客户端自报 ID 不采信。
"""
from __future__ import annotations

import json
import logging
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID, uuid4

from . import confirmations as confirmations_mod
from .config import get_session_tasks_config, tenant_allowed
from .constants import (
    DECISION_KIND_OPENING,
    DECISION_KINDS,
    ERR_BUDGET_EXCEEDED,
    ERR_CAPABILITY_MISSING,
    ERR_CONFLICT,
    ERR_CONVERSATION_IN_USE,
    ERR_EVENT_GAP,
    ERR_EVENT_PAYLOAD_CONFLICT,
    ERR_FEATURE_DISABLED,
    ERR_IDEMPOTENCY_CONFLICT,
    ERR_LEASE_EXPIRED,
    ERR_STALE_ASSIGNMENT,
    ERR_VALIDATION_FAILED,
    OPENING_BATCH_ID,
    REQUIRED_DEVICE_CAPABILITIES,
    STATUS_ACTIVE,
    STATUS_BLOCKED,
    STATUS_DRAFT,
    STATUS_PAUSED,
    TERMINAL_STATUSES,
    SessionTaskError,
)
from .models import (
    SpecValidationError,
    TaskDraftCreatePayload,
    TaskSpecPayload,
    validate_spec_for_scenario,
    validate_task_spec,
)
from .texts import digest_payload, load_text, spec_digest, store_text

logger = logging.getLogger("session_tasks.service")

_CONTROL_TRANSITIONS = {
    # blocked 不允许 pause/resume（须经修复确认，设计 §4），防止 blocked→paused/handoff→active 绕过
    "pause": {"active": "paused"},
    "resume": {"paused": "active", "human_required": "active", "blocked": "active"},
    "stop": {"active": "stopped", "paused": "stopped", "human_required": "stopped", "blocked": "stopped"},
    # blocked 不允许 handoff（须经修复确认；防 blocked→handoff→resume 洗白，评审 P1-2）
    "handoff": {"active": "human_required", "paused": "human_required"},
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _tz(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _spec_to_plain(spec: Any) -> Dict[str, Any]:  # noqa: ANN202
    """校验结果 → 冻结 plain dict（BaseModel 用 model_dump，场景自定义校验器可直接返回 dict）。"""
    if hasattr(spec, "model_dump"):
        return spec.model_dump(mode="json")
    return dict(spec)


def _conn():  # noqa: ANN202
    from src.db.database import get_db_connection

    return get_db_connection()


# ---------------------------------------------------------------------------
# 草稿与发布
# ---------------------------------------------------------------------------


def create_draft(tenant_id: str, user_id: str, payload: TaskDraftCreatePayload,
                 finalizer=None) -> Dict[str, Any]:  # noqa: ANN001
    """创建 draft（不发送、不占用会话；spec 加密入库，task_id 服务端生成）。

    B1.2 envelope（设计 §4.3）：spec 按请求 scenario_key（缺省微信）分派描述器
    spec_validator；微信非法 spec 仍由原 TaskSpecPayload 抛 ValidationError，
    此处加 "spec" loc 前缀包装——HTTP 400 field_errors 路径与 B1.1 前逐字段一致
    （B1.0 特征测试第 8 项为对照基准）。

    finalizer(conn, result)：业务提交前在同一连接写入幂等回执（R51 同事务范式）。
    """
    # CR 三审 P1-7：场景关闭仍可创建/保存草稿（workbench draft_enabled 语义，
    # B1.2 前 create-draft 无执行门控）；publish/claim/decision/resume 才做
    # 场景开关检查。未注册场景保持 validate_spec_for_scenario 的 400 语义。
    try:
        validated = validate_spec_for_scenario(payload.scenario_key, payload.spec)
    except SessionTaskError:
        raise
    except Exception as exc:  # noqa: BLE001 场景 spec_validator 校验失败 → envelope 错误路径
        raise SpecValidationError(exc) from exc
    plain = _spec_to_plain(validated)
    task_id = uuid4()
    with _conn() as conn:
        account_id, binding_id = payload.account_binding_id, payload.conversation_binding_id
        if payload.resolution_invocation_id:
            from .scenario_descriptor import require_descriptor

            account_id, binding_id = require_descriptor(
                payload.scenario_key
            ).binding_resolver.resolve_draft_targets(
                conn, tenant_id, user_id, payload.device_id, payload.resolution_invocation_id
            )
        _verify_bindings(tenant_id, user_id, payload.device_id, account_id,
                         binding_id, require_verified=False, conn=conn,
                         scenario_key=payload.scenario_key)
        text_id = store_text(conn, tenant_id, task_id, "spec", plain)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO session_tasks
                (id, tenant_id, user_id, scenario_key, device_id, account_binding_id, conversation_binding_id,
                 status, version, draft_spec_text_id, draft_digest)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'draft', 1, %s, %s)
            RETURNING version, created_at
            """,
            (task_id, tenant_id, user_id, payload.scenario_key, payload.device_id,
             account_id, binding_id, text_id, spec_digest(plain)),
        )
        row = cursor.fetchone()
        result = {"task_id": str(task_id), "version": row["version"], "status": STATUS_DRAFT}
        if finalizer is not None:
            finalizer(conn, result)
        conn.commit()
    return result


def update_draft(tenant_id: str, user_id: str, task_id: UUID, expected_version: int,
                 spec: Dict[str, Any], request_scenario_key: Optional[str] = None) -> Dict[str, Any]:  # noqa: ANN001
    """PATCH draft：expected_version CAS；仅 draft 可改（active 不可原地改策略）。

    B1.2 envelope（设计 §4.3）：spec 校验按**任务行权威 scenario_key** 分派
    （任务场景归属只来自 create，PATCH 不得切换——请求显式携带 scenario_key 且
    不一致 → 409）。校验先于事务执行（错误优先级与 B1.1 前一致：非法 spec 400
    先于任务不存在 404）；PATCH 的错误路径保持原 validate_task_spec 的未加前缀
    pydantic ValidationError（B1.1 前 API 现状）。
    """
    # 场景归属普通预读（scenario_key 不可变，仅用于分派校验器；权威校验在锁内 CAS）
    pre_scenario = _task_scenario_key(tenant_id, task_id)
    if pre_scenario is not None:
        validated = validate_spec_for_scenario(pre_scenario, spec)
    else:
        validated = validate_task_spec(spec)  # 任务不存在：保持既有 400→404 优先级路径
    plain = _spec_to_plain(validated)
    with _conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT status, version, scenario_key FROM session_tasks WHERE tenant_id=%s AND id=%s AND user_id=%s FOR UPDATE",
            (tenant_id, task_id, user_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise SessionTaskError("任务不存在或无权访问", "NOT_FOUND", 404)
        if request_scenario_key is not None and request_scenario_key != row["scenario_key"]:
            raise SessionTaskError("请求场景与任务场景不一致，禁止切换场景", ERR_CONFLICT, 409)
        if row["status"] not in (STATUS_DRAFT, STATUS_PAUSED):
            raise SessionTaskError("仅草稿/已暂停任务可编辑；active 需先暂停（设计 §4 改版流程）", "CONFLICT", 409)
        if row["version"] != expected_version:
            raise SessionTaskError(f"版本冲突（当前 {row['version']}）", "CONFLICT", 409)
        text_id = store_text(conn, tenant_id, task_id, "spec", plain)
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE session_tasks
            SET draft_spec_text_id=%s, draft_digest=%s, version=version+1, updated_at=CURRENT_TIMESTAMP
            WHERE tenant_id=%s AND id=%s AND version=%s
            """,
            (text_id, spec_digest(plain), tenant_id, task_id, expected_version),
        )
        if cursor.rowcount != 1:
            raise SessionTaskError("版本冲突（并发更新）", "CONFLICT", 409)
        conn.commit()
    return {"task_id": str(task_id), "version": expected_version + 1, "status": STATUS_DRAFT}


def issue_publish_confirmation(tenant_id: str, user_id: str, task_id: UUID, expected_version: int) -> Dict[str, Any]:
    """POST /{task_id}/confirm：为用户实际查看的版本签发一次性发布确认。

    版本 CAS（设计评审 P1-3）：expected_version 必须等于当前任务 version，
    防止「看过 v1、他人改成 v2 后点确认授权了没看过的 v2」；确认绑定该版本的
    草稿摘要，之后任何修改都会使摘要失配。
    """
    if expected_version <= 0:
        raise SessionTaskError("expected_version 必填", ERR_VALIDATION_FAILED, 400)
    with _conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT user_id, status, version, draft_digest FROM session_tasks WHERE tenant_id=%s AND id=%s FOR UPDATE",
            (tenant_id, task_id),
        )
        task = cursor.fetchone()
        if task is None or task["user_id"] != user_id:
            raise SessionTaskError("任务不存在或无权访问", "NOT_FOUND", 404)
        if task["status"] not in (STATUS_DRAFT, STATUS_PAUSED):
            raise SessionTaskError("当前状态不可发布确认", "CONFLICT", 409)
        if task["version"] != expected_version:
            raise SessionTaskError(f"版本冲突（当前 {task['version']}），请刷新后重新确认", "CONFLICT", 409)
        result = confirmations_mod.issue_confirmation(
            conn, tenant_id, user_id, task_id, task["version"], task["draft_digest"]
        )
        conn.commit()
    return result


def publish_task(tenant_id: str, user_id: str, task_id: UUID, expected_version: int, confirmation_id: UUID,
                 finalizer=None) -> Dict[str, Any]:  # noqa: ANN001
    """发布：确认凭据/绑定/能力/占用校验 → 冻结不可变 spec + subject 授权。

    锁序（评审 P1-1，与控制/领取/决策一致）：subject 行 → task 行 → 子行。
    首次发布 subject 行尚不存在：先 INSERT ... ON CONFLICT DO NOTHING 预建占位行
    （不锁既有行），再 FOR UPDATE 锁定，随后锁 task 行——任何路径都先持 subject
    锁再取 task 锁，消除「发布持 task 等 subject、控制持 subject 等 task」死锁。

    激活语义（评审 P1-2）：draft 首次发布 → active；**paused 任务重发布仅冻结
    新版本（spec_revision 前进、subject 指向新 revision、授权 epoch 递增），
    状态保持 paused 不进入 active**——从暂停回到可执行状态的所有入口共用恢复
    门禁，C1 尚无水位/未决发送核验能力，激活待 C2/C3 接入后开放。发布不同步
    发送开场白（C3 的 opening decision 才触发）。
    """
    if not tenant_allowed(tenant_id):
        raise SessionTaskError("会话任务功能未启用", ERR_FEATURE_DISABLED, 403)
    with _conn() as conn:
        # 锁序 1/3：预建并锁定 subject 行（首次发布占位 status='draft'）
        cursor = conn.cursor()
        cursor.execute(
            "SELECT scenario_key, user_id FROM session_tasks WHERE tenant_id=%s AND id=%s",
            (tenant_id, task_id),
        )
        located = cursor.fetchone()
        if located is None:
            raise SessionTaskError("任务不存在或无权访问", "NOT_FOUND", 404)
        # 场景门控按任务行权威 scenario_key 分派（CR 阻断 2：微信开关不再控制
        # 其他场景的生命周期；场景关 → 403 fail-closed）
        _ensure_scenario_enabled(tenant_id, located["scenario_key"])
        _ensure_task_subject(conn, tenant_id, located["scenario_key"], str(task_id), located["user_id"])
        _lock_task_subject(conn, tenant_id, task_id)
        # 锁序 2/3：task 行
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT user_id, status, version, scenario_key, device_id, account_binding_id,
                   conversation_binding_id, spec_revision, draft_spec_text_id, draft_digest, control_epoch
            FROM session_tasks WHERE tenant_id=%s AND id=%s FOR UPDATE
            """,
            (tenant_id, task_id),
        )
        task = cursor.fetchone()
        if task is None or task["user_id"] != user_id:
            raise SessionTaskError("任务不存在或无权访问", "NOT_FOUND", 404)
        if task["status"] not in (STATUS_DRAFT, STATUS_PAUSED):
            raise SessionTaskError(f"当前状态 {task['status']} 不可发布", "CONFLICT", 409)
        if task["version"] != expected_version:
            raise SessionTaskError(f"版本冲突（当前 {task['version']}）", "CONFLICT", 409)

        conf_row = confirmations_mod.load_confirmation(conn, tenant_id, task_id, confirmation_id)
        confirmations_mod.validate_confirmation(
            conf_row, user_id=user_id, expected_version=expected_version, digest=task["draft_digest"]
        )

        _verify_bindings(tenant_id, user_id, task["device_id"], task["account_binding_id"],
                         task["conversation_binding_id"], conn=conn,
                         scenario_key=task["scenario_key"])
        _check_device_capabilities(conn, tenant_id, task["device_id"], scenario_key=task["scenario_key"])

        spec_plain = load_text(conn, tenant_id, task_id, task["draft_spec_text_id"], expected_purpose="spec")
        # 发布时刻复验冻结 spec（草稿保存后 expires_at 可能已过；§5 有限期限发布时仍须有效）
        # B1.2：按任务行权威 scenario_key 分派描述器校验器（微信 = 原 validate_task_spec）
        validate_spec_for_scenario(task["scenario_key"], spec_plain)
        # V1.10（设计 §4.1/§5.3，六审 P1-5）：发布事务内场景 spec 强校验钩子——
        # 已锁 task、写 revision 前调用；BOSS 据此核验 spec 引用话术版本存在、
        # content_hash 与版本表一致、租户归属（失败抛 SessionTaskError → 本事务
        # rollback，零 revision 副作用）。微信描述器本成员为 None，行为零变化。
        from .scenario_descriptor import get_descriptor

        _descriptor = get_descriptor(task["scenario_key"])
        _publish_spec_validator = getattr(_descriptor, "validate_publish_spec", None)
        if callable(_publish_spec_validator):
            _publish_spec_validator(conn, tenant_id, spec_plain)
        new_revision = (task["spec_revision"] or 0) + 1
        target_status = STATUS_ACTIVE if task["status"] == STATUS_DRAFT else STATUS_PAUSED
        _publish_result = {
            "task_id": str(task_id),
            "status": target_status,
            "spec_revision": new_revision,
            "version": expected_version + 1,
            "control_epoch": task["control_epoch"] + 1,
            "reactivated": target_status == STATUS_ACTIVE,
        }
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO session_task_specs (tenant_id, task_id, revision, spec_text_id, limits_json)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
                """,
                (tenant_id, task_id, new_revision, task["draft_spec_text_id"],
                 json.dumps(spec_plain["limits"], ensure_ascii=False)),
            )
            spec_id = cursor.fetchone()["id"]
            # subject 行已预建并持锁：直接 UPDATE（新内容授权 = epoch 递增；
            # 仅 draft 首发进入 active，重发布保持 paused——评审 P1-2）
            cursor.execute(
                """
                UPDATE desktop_automation_subjects
                SET status=%s, active_revision_ref=%s,
                    authorization_epoch=authorization_epoch+1, updated_at=CURRENT_TIMESTAMP
                WHERE tenant_id=%s AND scenario_key=%s AND kind='task' AND ref=%s
                """,
                (target_status, str(spec_id), tenant_id, task["scenario_key"], str(task_id)),
            )
            if cursor.rowcount != 1:
                raise SessionTaskError("任务授权主体缺失（subject 未注册）", "CONFLICT", 409)
            cursor.execute(
                """
                INSERT INTO desktop_automation_subjects (tenant_id, scenario_key, kind, ref, owner_id, status, task_ref)
                VALUES (%s, %s, 'revision', %s, %s, 'published', %s)
                ON CONFLICT (tenant_id, scenario_key, kind, ref) DO UPDATE SET status='published', updated_at=CURRENT_TIMESTAMP
                """,
                (tenant_id, task["scenario_key"], str(spec_id), user_id, str(task_id)),
            )
            if spec_plain.get("opening_text"):
                cursor.execute(
                    """
                    INSERT INTO session_task_batches (tenant_id, task_id, batch_id, input_version, synthetic, status)
                    VALUES (%s, %s, %s, 0, TRUE, 'accepted')
                    ON CONFLICT (tenant_id, task_id, batch_id) DO NOTHING
                    """,
                    (tenant_id, task_id, OPENING_BATCH_ID),
                )
            cursor.execute(
                """
                UPDATE session_tasks
                SET status=%s, spec_revision=%s, current_spec_id=%s, version=version+1,
                    control_epoch=control_epoch+1, server_control_seq=server_control_seq+1,
                    updated_at=CURRENT_TIMESTAMP
                WHERE tenant_id=%s AND id=%s AND version=%s
                """,
                (target_status, new_revision, spec_id, tenant_id, task_id, expected_version),
            )
            if cursor.rowcount != 1:
                raise SessionTaskError("版本冲突（并发发布）", "CONFLICT", 409)
            confirmations_mod.consume_confirmation(conn, conf_row, new_revision)
            if finalizer is not None:
                finalizer(conn, _publish_result)
            conn.commit()
        except SessionTaskError:
            conn.rollback()
            raise
        except Exception as exc:  # noqa: BLE001 占用唯一索引冲突 → 业务 409
            conn.rollback()
            if _is_unique_violation(exc, "idx_session_tasks_occupancy"):
                raise SessionTaskError("该会话已有一个未终结任务（CONVERSATION_IN_USE）", ERR_CONVERSATION_IN_USE, 409) from exc
            raise
    return _publish_result


def _ensure_task_subject(conn, tenant_id: str, scenario_key: str, ref: str, owner_id: str) -> None:  # noqa: ANN001
    """首次发布前预建 task subject 占位行（已存在则无操作；不锁既有行）。

    随后统一走 FOR UPDATE 锁定，保证 subject→task 锁序在「行已存在」与
    「首次创建」两种情况下都成立（评审 P1-1）。
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO desktop_automation_subjects (tenant_id, scenario_key, kind, ref, owner_id, status)
        VALUES (%s, %s, 'task', %s, %s, 'draft')
        ON CONFLICT (tenant_id, scenario_key, kind, ref) DO NOTHING
        """,
        (tenant_id, scenario_key, ref, owner_id),
    )


def publish_task_once(tenant_id: str, user_id: str, task_id: UUID, expected_version: int,
                      confirmation_id: UUID) -> Dict[str, Any]:
    """Tool transport uses the same transactional receipt as HTTP publishing.

    The user-issued confirmation is a stable business key, not an LLM call ID.
    No confirmation is created here. Replays return the original publication.
    """
    from .api import _execute_idempotent
    body = {"expected_version": expected_version, "confirmation_id": str(confirmation_id)}
    response = _execute_idempotent(
        tenant_id, user_id, f"POST /api/session-tasks/{task_id}/publish",
        f"confirmation:{confirmation_id}", body,
        lambda finalizer: publish_task(tenant_id, user_id, task_id, expected_version,
                                      confirmation_id, finalizer=finalizer),
    )
    return json.loads(response.body)["data"]


def control_task(tenant_id: str, user_id: str, task_id: UUID, action: str, expected_version: int,
                 reason_code: Optional[str] = None, *, resume_from=None) -> Dict[str, Any]:
    """pause/stop/handoff：确定性控制动作，CAS + control_epoch 递增。

    - 锁序（设计 §10/评审 P1-1）：先锁 subject 行（kind=task）→ 再锁 session_tasks
      行 → 再写 assignment；与 claim/决策/授权路径共用同一顺序，无反向锁。
    - 离开 active 的动作（pause/stop/handoff）同事务递增 subject
      authorization_epoch：旧授权代立即作废，恢复/重发布产生新授权代。
    - resume 由 C4 workbench 复核显式新基线、绑定、预算及未决效果；
      blocked 原因不因 handoff 清除。改版须先重新确认发布再选择恢复。
    - stop 必须带 reason_code（设计 §4）。
    """
    transitions = _CONTROL_TRANSITIONS.get(action)
    if transitions is None:
        raise SessionTaskError(f"未知控制动作: {action}", ERR_VALIDATION_FAILED)
    if action == "stop" and not reason_code:
        raise SessionTaskError("stop 必须携带 reason_code", ERR_VALIDATION_FAILED)
    if action == "resume":
        from .workbench import resume_task
        return resume_task(tenant_id, user_id, task_id, expected_version, resume_from)
    with _conn() as conn:
        # 锁序 1/2：subject 行（授权权威）→ task 行
        subject = _lock_task_subject(conn, tenant_id, task_id)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT user_id, status, version, control_epoch, scenario_key FROM session_tasks WHERE tenant_id=%s AND id=%s FOR UPDATE",
            (tenant_id, task_id),
        )
        task = cursor.fetchone()
        if task is None or task["user_id"] != user_id:
            raise SessionTaskError("任务不存在或无权访问", "NOT_FOUND", 404)
        if task["status"] in TERMINAL_STATUSES:
            raise SessionTaskError("任务已终结，不可控制", "CONFLICT", 409)
        if task["version"] != expected_version:
            raise SessionTaskError(f"版本冲突（当前 {task['version']}）", "CONFLICT", 409)
        target = transitions.get(task["status"])
        if target is None:
            raise SessionTaskError(f"状态 {task['status']} 不允许 {action}", "CONFLICT", 409)
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE session_tasks
                SET status=%s, version=version+1, control_epoch=control_epoch+1,
                    server_control_seq=server_control_seq+1,
                    completion_reason=COALESCE(%s, completion_reason), updated_at=CURRENT_TIMESTAMP
                WHERE tenant_id=%s AND id=%s AND version=%s
                """,
                (target, reason_code if action == "stop" else None, tenant_id, task_id, expected_version),
            )
            if cursor.rowcount != 1:
                raise SessionTaskError("版本冲突（并发控制）", "CONFLICT", 409)
            cursor.execute(
                """
                UPDATE session_task_assignments SET server_control_seq=server_control_seq+1, updated_at=CURRENT_TIMESTAMP
                WHERE tenant_id=%s AND task_id=%s AND is_current=TRUE
                """,
                (tenant_id, task_id),
            )
            # 同步底座任务授权状态；离开 active（pause/stop/handoff）递增
            # authorization_epoch 撤销旧授权代（评审 P1-1/P1-2）
            subject_status = {"paused": "paused", "human_required": "human_required", "stopped": "stopped", "active": "active"}[target]
            revoke = target != "active"
            cursor.execute(
                """
                UPDATE desktop_automation_subjects
                SET status=%s,
                    authorization_epoch=CASE WHEN %s THEN authorization_epoch+1 ELSE authorization_epoch END,
                    updated_at=CURRENT_TIMESTAMP
                WHERE tenant_id=%s AND scenario_key=%s AND kind='task' AND ref=%s
                """,
                (subject_status, revoke, tenant_id, task["scenario_key"], str(task_id)),
            )
            if subject is not None and revoke and cursor.rowcount != 1:
                raise SessionTaskError("任务授权主体缺失（subject 未注册）", "CONFLICT", 409)
            from .notifications import record_notice
            record_notice(conn, tenant_id, task_id, target, reason_code, task["control_epoch"] + 1)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return {"task_id": str(task_id), "status": target, "version": expected_version + 1,
            "control_epoch": task["control_epoch"] + 1}


def _ensure_scenario_enabled(tenant_id: str, scenario_key) -> None:
    """场景热读门控（CR 阻断 2）：按权威 task.scenario_key 调对应场景 enabled。

    通用生命周期（publish/claim/create_decision/create_draft）不再直读微信开关：
    微信关 BOSS 开时 BOSS 可运行；描述器缺失/未注册 → fail-closed 403。
    """
    from .scenario_descriptor import get_descriptor

    descriptor = get_descriptor(str(scenario_key or ""))
    checker = getattr(descriptor, "scenario_enabled", None) if descriptor is not None else None
    if checker is None or not checker(tenant_id):
        raise SessionTaskError(f"场景未启用: {scenario_key}", ERR_FEATURE_DISABLED, 403)


def _task_scenario_key(tenant_id: str, task_id) -> Optional[str]:  # noqa: ANN001
    """任务场景归属普通预读（scenario_key 创建后不可变，仅用于分派 spec 校验器）。"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT scenario_key FROM session_tasks WHERE tenant_id=%s AND id=%s",
            (tenant_id, task_id),
        )
        row = cursor.fetchone()
        return row["scenario_key"] if row else None


def _assignment_task_id(conn, tenant_id: str, device_id, assignment_id) -> Optional[Any]:  # noqa: ANN001
    """无锁定位 assignment 所属 task_id（随后按 subject→assignment 顺序加锁）。"""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT task_id FROM session_task_assignments WHERE tenant_id=%s AND id=%s AND device_id=%s",
        (tenant_id, assignment_id, device_id),
    )
    row = cursor.fetchone()
    return row["task_id"] if row is not None else None


def _lock_task_subject(conn, tenant_id: str, task_id) -> Optional[Dict[str, Any]]:  # noqa: ANN001
    """锁序第一步：锁任务的 subject 行（kind=task）。返回行（可能不存在）。

    先无锁读 scenario_key 定位行（仅定位，不据此做任何授权判断），再 FOR UPDATE
    锁 subject；随后的 task 行锁在同一事务内构成 subject→task 顺序。
    """
    cursor = conn.cursor()
    cursor.execute(
        "SELECT scenario_key FROM session_tasks WHERE tenant_id=%s AND id=%s",
        (tenant_id, task_id),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    cursor.execute(
        """
        SELECT id, status, authorization_epoch FROM desktop_automation_subjects
        WHERE tenant_id=%s AND scenario_key=%s AND kind='task' AND ref=%s FOR UPDATE
        """,
        (tenant_id, row["scenario_key"], str(task_id)),
    )
    return cursor.fetchone()


# ---------------------------------------------------------------------------
# 设备侧：claim / renew / events / decisions
# ---------------------------------------------------------------------------


def claim_task(device: Dict[str, Any], runtime_instance_id: str) -> Optional[Dict[str, Any]]:
    """设备 claim：遍历本设备候选任务，领取第一个可分配项（设计评审 P1-2）。

    每个候选（锁 task 行 + 当前 assignment 行）按序判定：
    - 本实例已持有有效租约 → 跳过（保持靠 renew 维持，避免重复下发饿死后续任务）；
    - 他实例持有未过期租约 → 跳过找下一个（设备迁移需显式移交，设计 §4）；
    - 无当前 assignment 或租约已过期 → supersede 旧代 + fence+1，创建新 assignment 返回。
    全部不可领取 → rollback 返回 None（API 层 204）。
    """
    if not runtime_instance_id or len(runtime_instance_id) > 128:
        raise SessionTaskError("runtime_instance_id 非法", ERR_VALIDATION_FAILED)
    tenant_id = device["tenant_id"]
    if not tenant_allowed(tenant_id):
        raise SessionTaskError("会话任务功能未启用", ERR_FEATURE_DISABLED, 403)
    cfg = get_session_tasks_config()
    with _conn() as conn:
        # 通用能力前置检查（场景发送能力在候选任务定位后按场景检查，九处 #1）
        _check_device_capabilities(conn, tenant_id, device["id"])
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT t.id, t.conversation_binding_id FROM session_tasks t
            WHERE t.tenant_id=%s AND t.device_id=%s AND t.status='active'
            ORDER BY t.created_at LIMIT 50
            """,
            (tenant_id, device["id"]),
        )
        candidates = cursor.fetchall()
        claimed = None
        capability_error = None
        for candidate in candidates:
            claimed, skipped_error = _claim_one(
                conn, tenant_id, device, runtime_instance_id, candidate, cfg
            )
            if claimed is not None:
                break
            if skipped_error is not None and capability_error is None:
                capability_error = skipped_error  # 记首个能力不匹配（非阻断 b）
        if claimed is None:
            conn.rollback()
            if capability_error is not None:
                # 有候选但全部因能力不兼容被跳过：保留既有逐项拒绝语义
                # （单候选缺失能力 → 409 ERR_CAPABILITY_MISSING，特征测试 6 锁定）
                raise capability_error
            return None  # 无候选/候选场景全关闭：无任务可领（API 204）
        task, assignment_id, fence = claimed
        spec_plain = _load_spec_by_spec_id(conn, tenant_id, task["current_spec_id"], task["id"])
        from .scenario_descriptor import get_descriptor

        descriptor = get_descriptor(task["scenario_key"])
        # 观察契约身份字段（session_observer_v1：绑定/账号身份版本供端侧校验；
        # B1.2 九处 #5：身份查询/名称上下文分支由描述器 binding_resolver 承载）
        binding_row = None
        target_policy = None
        if descriptor is not None:
            binding_row = descriptor.binding_resolver.get_runtime_identity(
                cursor, tenant_id, task["conversation_binding_id"]
            )
            target_policy = descriptor.binding_resolver.runtime_target_policy(binding_row)
        if target_policy:
            spec_plain["_runtime_target"] = target_policy
        from .workbench import input_version
        version_base = input_version(conn, tenant_id, task["id"])
        cursor.execute("UPDATE session_task_batches SET status='resume_claimed' WHERE tenant_id=%s AND task_id=%s AND batch_id=%s AND status='resume_baseline' RETURNING batch_id", (tenant_id, task["id"], f"resume:{task['control_epoch']}"))
        fresh_baseline = cursor.fetchone() is not None
        conn.commit()
    return {
        "assignment_id": str(assignment_id),
        "task_id": str(task["id"]),
        "spec": spec_plain,
        "spec_revision": task["spec_revision"],
        "fence": fence,
        "control_epoch": task["control_epoch"],
        "server_control_seq": task["server_control_seq"],
        "lease_seconds": cfg.lease_seconds,
        "input_version_base": version_base,
        "fresh_baseline": fresh_baseline,
        "scenario_key": str(task["scenario_key"]),
        "conversation_binding_id": str(task["conversation_binding_id"]),
        "binding_version": int(binding_row["identity_version"]) if binding_row else 0,
        "account_identity_version": (
            descriptor.binding_resolver.account_identity_version(binding_row)
            if descriptor is not None else 0
        ),
    }


def _claim_one(conn, tenant_id: str, device: Dict[str, Any], runtime_instance_id: str,
               candidate, cfg) -> tuple:  # noqa: ANN001
    """领取单个候选：可领取返回 (task, assignment_id, fence)；跳过返回 (None, 跳过原因)。

    跳过 = 本实例已持有有效租约 / 他实例持有有效租约 / 场景关闭 / 能力不匹配
    （CR 阻断 2 + 非阻断 b：场景关闭与能力不匹配都只跳过本候选，不 fail 整个
    claim——能力不匹配原因回传调用方，全部候选不兼容时保留既有逐项拒绝语义）；
    不可领取时回滚本候选的行锁影响并继续（rollback 释放锁，下一候选重新开始）。
    """
    # 锁序 subject→task（评审 P1-1）：先无锁定位（拿 scenario_key/conversation_binding_id），
    # 再锁 subject，最后锁 task 行并在锁内复验 active 与绑定有效性（评审 P1-4）
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT t.id, t.scenario_key, t.conversation_binding_id FROM session_tasks t
        WHERE t.tenant_id=%s AND t.id=%s AND t.status='active'
        """,
        (tenant_id, candidate["id"]),
    )
    located = cursor.fetchone()
    if located is None:
        conn.rollback()
        return None, None
    _lock_task_subject(conn, tenant_id, located["id"])
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT t.id, t.scenario_key, t.spec_revision, t.current_spec_id, t.control_epoch, t.server_control_seq,
               t.conversation_binding_id
        FROM session_tasks t
        WHERE t.tenant_id=%s AND t.id=%s AND t.status='active'
        FOR UPDATE OF t SKIP LOCKED LIMIT 1
        """,
        (tenant_id, candidate["id"]),
    )
    task = cursor.fetchone()
    if task is None:
        conn.rollback()  # 锁定间隙状态变化或被其他事务持有：跳过本候选
        return None, None
    # 场景门控按候选任务 scenario_key 分派（CR 阻断 2）：关闭场景的任务跳过
    # （不 fail 整个 claim——微信关 BOSS 开时 BOSS 任务仍可领）
    from .scenario_descriptor import get_descriptor

    descriptor = get_descriptor(str(located["scenario_key"] or ""))
    checker = getattr(descriptor, "scenario_enabled", None) if descriptor is not None else None
    if checker is None or not checker(tenant_id):
        conn.rollback()
        return None, None
    # 场景发送能力检查（九处 #1：能力校验按候选任务 scenario_key 拼接描述器能力；
    # CR 非阻断 b：不匹配跳过本候选继续找兼容场景任务，避免多场景饥饿）
    try:
        _check_device_capabilities(conn, tenant_id, device["id"], scenario_key=located["scenario_key"])
    except SessionTaskError as exc:
        if exc.code != ERR_CAPABILITY_MISSING:
            raise
        conn.rollback()
        return None, exc
    if not _binding_valid_for_allocation(conn, tenant_id, task["conversation_binding_id"],
                                         scenario_key=located["scenario_key"]):
        conn.rollback()
        return None, None
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, fence, runtime_instance_id, lease_expires_at
        FROM session_task_assignments
        WHERE tenant_id=%s AND task_id=%s AND is_current=TRUE FOR UPDATE
        """,
        (tenant_id, task["id"]),
    )
    current = cursor.fetchone()
    lease_valid = current is not None and _tz(current["lease_expires_at"]) > _now()
    if current is not None and current["runtime_instance_id"] == runtime_instance_id and lease_valid:
        conn.rollback()
        return None, None  # 本实例已持有：跳过，找后续任务
    if current is not None and lease_valid:
        conn.rollback()
        return None, None  # 他实例有效租约：跳过（设计 §4 显式移交）
    if current is not None:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE session_task_assignments SET is_current=FALSE, status='superseded', updated_at=CURRENT_TIMESTAMP WHERE id=%s",
            (current["id"],),
        )
    fence = (current["fence"] + 1) if current is not None else 1
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO session_task_assignments
            (tenant_id, task_id, device_id, runtime_instance_id, fence, lease_expires_at,
             control_epoch_at_claim, acked_local_seq, server_control_seq)
        VALUES (%s, %s, %s, %s, %s, %s, %s, 0, %s)
        RETURNING id
        """,
        (tenant_id, task["id"], device["id"], runtime_instance_id, fence,
         _now() + timedelta(seconds=cfg.lease_seconds), task["control_epoch"], task["server_control_seq"]),
    )
    return (task, cursor.fetchone()["id"], fence), None


def renew_assignment(tenant_id: str, device_id: UUID, assignment_id: UUID, fence: int, control_epoch: int) -> Dict[str, Any]:
    """续租：校验当前 assignment/fence/control_epoch/租约未过期；返回最新控制。

    暂停/终态如实返回：Runtime 停止新副作用（暂停不可续成可执行）。
    """
    cfg = get_session_tasks_config()
    with _conn() as conn:
        # 锁序 subject→assignment（评审 P1-1）：先定位 task_id，锁 subject 再锁 assignment
        task_id_loc = _assignment_task_id(conn, tenant_id, device_id, assignment_id)
        if task_id_loc is None:
            raise SessionTaskError("assignment 不存在或不属于本设备", ERR_STALE_ASSIGNMENT, 409)
        _lock_task_subject(conn, tenant_id, task_id_loc)
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT a.fence, a.lease_expires_at,
                   t.status, t.control_epoch, t.server_control_seq, t.completion_reason, t.blocked_reason
            FROM session_task_assignments a JOIN session_tasks t ON t.tenant_id=a.tenant_id AND t.id=a.task_id
            WHERE a.tenant_id=%s AND a.id=%s AND a.device_id=%s AND a.is_current=TRUE FOR UPDATE OF a
            """,
            (tenant_id, assignment_id, device_id),
        )
        row = cursor.fetchone()
        if row is None or row["fence"] != fence or row["control_epoch"] != control_epoch:
            raise SessionTaskError("assignment 已过时（STALE_ASSIGNMENT）", ERR_STALE_ASSIGNMENT, 409)
        if _tz(row["lease_expires_at"]) <= _now():
            raise SessionTaskError("租约已过期，需重新 claim", ERR_LEASE_EXPIRED, 409)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE session_task_assignments SET lease_expires_at=%s, updated_at=CURRENT_TIMESTAMP WHERE id=%s",
            (_now() + timedelta(seconds=cfg.lease_seconds), assignment_id),
        )
        conn.commit()
    return {
        "lease_seconds": cfg.lease_seconds,
        "control": {
            "status": row["status"],
            "control_epoch": row["control_epoch"],
            "server_control_seq": row["server_control_seq"],
            "completion_reason": row["completion_reason"],
            "blocked_reason": row["blocked_reason"],
        },
    }


def ingest_events(tenant_id: str, device_id: UUID, assignment_id: UUID, fence: int, records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """events 连续前缀 ACK + 事实接纳（消息/批次物化）同事务。

    - local_seq 必须从 acked_local_seq+1 连续（乱序/跳号 → EVENT_SEQ_GAP 409）；
    - event_id 全局唯一：同 ID 同 digest 幂等放行，异 digest → PAYLOAD_CONFLICT 409；
    - batch 事件物化 messages + batches（消息 text 加密、evidence 只存 opaque 引用）。
    """
    cfg = get_session_tasks_config()
    if not records:
        raise SessionTaskError("records 不能为空", ERR_VALIDATION_FAILED)
    if len(records) > cfg.events_max_records:
        raise SessionTaskError(f"每批最多 {cfg.events_max_records} 条", ERR_VALIDATION_FAILED)
    # 与客户端 Buffer.byteLength 口径对齐（无分隔空格，UTF-8 字节数——
    # 非 Unicode 字符数：中文 1 字符 = 3 字节，两端按同一 256KiB 判定）
    batch_bytes = sum(len(json.dumps(r, ensure_ascii=False, default=str, separators=(",", ":")).encode("utf-8")) for r in records)
    if batch_bytes > cfg.events_max_bytes:
        raise SessionTaskError("事件批超过 256KiB 上限", ERR_VALIDATION_FAILED)
    with _conn() as conn:
        # 锁序 subject→assignment（评审 P1-1）
        task_id_loc = _assignment_task_id(conn, tenant_id, device_id, assignment_id)
        if task_id_loc is None:
            raise SessionTaskError("assignment 不存在或不属于本设备", ERR_STALE_ASSIGNMENT, 409)
        _lock_task_subject(conn, tenant_id, task_id_loc)
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT a.task_id, a.fence, a.acked_local_seq, a.control_epoch_at_claim, t.status, t.control_epoch, t.server_control_seq,
                   t.conversation_binding_id, t.scenario_key
            FROM session_task_assignments a JOIN session_tasks t ON t.tenant_id=a.tenant_id AND t.id=a.task_id
            WHERE a.tenant_id=%s AND a.id=%s AND a.device_id=%s AND a.is_current=TRUE FOR UPDATE OF a
            """,
            (tenant_id, assignment_id, device_id),
        )
        a = cursor.fetchone()
        if a is None or a["fence"] != fence or a["control_epoch_at_claim"] != a["control_epoch"]:
            # 旧 assignment 的未同步事实补交（设计评审 P2-8）：换代后旧日志残留
            # 事实允许对账补录——仅接纳事实存储，不推进当前代水位、不授予任何
            # 新执行/决策权（决策创建走 create_decision 的门禁）。
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT a.task_id, a.fence, a.acked_local_seq, t.status, t.control_epoch, t.server_control_seq,
                       t.conversation_binding_id, t.scenario_key
                FROM session_task_assignments a JOIN session_tasks t ON t.tenant_id=a.tenant_id AND t.id=a.task_id
                WHERE a.tenant_id=%s AND a.id=%s AND a.device_id=%s
                  AND (a.is_current=FALSE OR a.control_epoch_at_claim<>t.control_epoch)
                FOR UPDATE OF a
                """,
                (tenant_id, assignment_id, device_id),
            )
            hist = cursor.fetchone()
            if hist is None:
                raise SessionTaskError("assignment 不存在或不属于本设备", ERR_STALE_ASSIGNMENT, 409)
            if hist["fence"] != fence:
                raise SessionTaskError("历史补交 fence 不匹配", ERR_STALE_ASSIGNMENT, 409)
            return _ingest_historical_facts(conn, tenant_id, assignment_id, hist, records)
        try:
            expected_seq = a["acked_local_seq"]
            for record in records:
                local_seq = int(record.get("local_seq", -1))
                event_id = str(record.get("event_id", "")).strip()
                event_type = str(record.get("type", "")).strip()
                payload = record.get("payload") or {}
                if not event_id or not event_type:
                    raise SessionTaskError("event_id/type 必填", ERR_VALIDATION_FAILED)
                digest = digest_payload(payload)
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT payload_digest FROM session_task_events WHERE tenant_id=%s AND event_id=%s",
                    (tenant_id, event_id),
                )
                seen = cursor.fetchone()
                if seen is not None:
                    if seen["payload_digest"] != digest:
                        raise SessionTaskError(f"event_id={event_id} 与已接纳 payload 不一致", ERR_EVENT_PAYLOAD_CONFLICT, 409)
                    # 已见同摘要事件：幂等放行，但不推进前缀水位（防跳号；
                    # 跨 assignment 重投的 ack 语义在 C2 与 Runtime 协议联调时冻结）
                    continue
                if local_seq != expected_seq + 1:
                    raise SessionTaskError(
                        f"local_seq 不连续（期望 {expected_seq + 1}，收到 {local_seq}）", ERR_EVENT_GAP, 409
                    )
                text_id = store_text(conn, tenant_id, a["task_id"], "event", payload)
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO session_task_events (tenant_id, task_id, assignment_id, local_seq, event_id, event_type, payload_digest, payload_text_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (tenant_id, a["task_id"], assignment_id, local_seq, event_id, event_type, digest, text_id),
                )
                if event_type == "batch":
                    _materialize_batch(conn, tenant_id, a["task_id"], a["conversation_binding_id"], payload,
                                       scenario_key=a["scenario_key"])
                if event_type == "recovery_blocked" or (event_type in ("phase", "execution_phase", "decision_phase") and payload.get("phase_to", payload.get("to")) == "blocked"):
                    from .notifications import record_notice
                    record_notice(conn, tenant_id, a["task_id"], "blocked", "runtime_blocked", a["control_epoch"])
                expected_seq = local_seq
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE session_task_assignments SET acked_local_seq=%s, updated_at=CURRENT_TIMESTAMP WHERE id=%s",
                (expected_seq, assignment_id),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return {
        "ack_seq": expected_seq,
        "control": {"status": a["status"], "control_epoch": a["control_epoch"], "server_control_seq": a["server_control_seq"]},
    }


def _ingest_historical_facts(conn, tenant_id: str, assignment_id: UUID, hist, records: List[Dict[str, Any]]) -> Dict[str, Any]:  # noqa: ANN001
    """旧 assignment 历史事实补录：event 幂等接纳 + batch 物化，返回当前任务控制。

    语义（设计 §8 恢复顺序）：补交不推进当前代水位、不改变旧 assignment 的
    superseded 状态；批次物化沿用同一事实表（消息幂等复用）。对账结论以
    historical=true 显式标记，Runtime 不得据此触发新决策或发送。
    """
    try:
        expected_seq = hist["acked_local_seq"]
        for record in records:
            local_seq = int(record.get("local_seq", -1))
            event_id = str(record.get("event_id", "")).strip()
            event_type = str(record.get("type", "")).strip()
            payload = record.get("payload") or {}
            if not event_id or not event_type:
                raise SessionTaskError("event_id/type 必填", ERR_VALIDATION_FAILED)
            digest = digest_payload(payload)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT payload_digest FROM session_task_events WHERE tenant_id=%s AND event_id=%s",
                (tenant_id, event_id),
            )
            seen = cursor.fetchone()
            if seen is not None:
                if seen["payload_digest"] != digest:
                    raise SessionTaskError(f"event_id={event_id} 与已接纳 payload 不一致", ERR_EVENT_PAYLOAD_CONFLICT, 409)
                continue
            if local_seq != expected_seq + 1:
                raise SessionTaskError(
                    f"历史事实 local_seq 不连续（期望 {expected_seq + 1}，收到 {local_seq}）", ERR_EVENT_GAP, 409
                )
            text_id = store_text(conn, tenant_id, hist["task_id"], "event", payload)
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO session_task_events (tenant_id, task_id, assignment_id, local_seq, event_id, event_type, payload_digest, payload_text_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (tenant_id, hist["task_id"], assignment_id, local_seq, event_id, event_type, digest, text_id),
            )
            if event_type == "batch":
                # 历史对账批次标记 historical：不可作为决策输入（评审 P1-3）
                _materialize_batch(conn, tenant_id, hist["task_id"], hist["conversation_binding_id"], payload,
                                   batch_status="historical", scenario_key=hist["scenario_key"])
            expected_seq = local_seq
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE session_task_assignments SET acked_local_seq=%s, updated_at=CURRENT_TIMESTAMP WHERE id=%s",
            (expected_seq, assignment_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {
        "ack_seq": expected_seq,
        "historical": True,
        "control": {"status": hist["status"], "control_epoch": hist["control_epoch"], "server_control_seq": hist["server_control_seq"]},
    }


def _materialize_batch(conn, tenant_id: str, task_id: UUID, task_conversation_binding_id, payload: Dict[str, Any],
                       batch_status: str = "accepted", scenario_key: Optional[str] = None) -> None:  # noqa: ANN001
    """batch 事件物化：messages + batch 行（幂等：批次已存在直接返回）。"""
    batch_id = str(payload.get("batch_id", "")).strip()
    if not batch_id:
        raise SessionTaskError("batch 事件缺少 batch_id", ERR_VALIDATION_FAILED)
    if batch_id == OPENING_BATCH_ID:
        raise SessionTaskError("普通批次不得使用保留值 opening", ERR_VALIDATION_FAILED)
    try:
        UUID(batch_id)
    except ValueError as exc:
        raise SessionTaskError("普通批次 batch_id 必须是 UUID 字符串", ERR_VALIDATION_FAILED) from exc
    conversation_binding_id = str(payload.get("conversation_binding_id", "")).strip()
    if not conversation_binding_id:
        raise SessionTaskError("batch 事件缺少 conversation_binding_id", ERR_VALIDATION_FAILED)
    # 消息归属校验（设计 §9/§13.2）：客户端自报会话必须等于任务行冻结的会话绑定
    if conversation_binding_id != str(task_conversation_binding_id):
        raise SessionTaskError("batch 事件的 conversation_binding_id 与任务绑定不符", ERR_VALIDATION_FAILED, 409)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id FROM session_task_batches WHERE tenant_id=%s AND task_id=%s AND batch_id=%s",
        (tenant_id, task_id, batch_id),
    )
    if cursor.fetchone() is not None:
        return
    messages = payload.get("messages") or []
    input_version = int(payload.get("input_version", 0))
    message_ids: List[str] = []
    for m in messages:
        message_id = str(m.get("local_message_id", "")).strip()
        sender = str(m.get("sender", "")).strip()
        text = m.get("text")
        if not message_id or sender not in ("peer", "self", "system") or not isinstance(text, str) or not text.strip():
            raise SessionTaskError("消息字段非法（sender 必须可判定、text 非空）", ERR_VALIDATION_FAILED)
        # 消息事实独立于批次（设计评审 P1-7）：重新合批时新批次 [m1,m2] 可引用
        # 已有消息 m1——sender 相同且正文近似匹配则复用首次密文；真实异文仍拒绝
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT m.sender, t.encrypted_payload FROM session_task_messages m
            JOIN session_task_texts t ON t.tenant_id=m.tenant_id AND t.id=m.text_id
            WHERE m.tenant_id=%s AND m.task_id=%s AND m.message_id=%s
            """,
            (tenant_id, task_id, message_id),
        )
        existing = cursor.fetchone()
        if existing is not None:
            if existing["sender"] != sender:
                raise SessionTaskError(f"消息 {message_id} 已存在且 sender 不一致", "CONFLICT", 409)
            from .texts import _crypto

            _, decrypt_secret = _crypto()
            try:
                prior = json.loads(decrypt_secret(existing["encrypted_payload"]).decode("utf-8"))["text"]
            except Exception as exc:  # noqa: BLE001
                raise SessionTaskError(f"消息 {message_id} 已有密文无法核对", "CRYPTO_UNAVAILABLE", 503) from exc
            from .ocr_matching import ocr_text_matches

            if not ocr_text_matches(prior, text):
                raise SessionTaskError(f"消息 {message_id} 已存在且正文不一致", "CONFLICT", 409)
            message_ids.append(message_id)
            continue
        text_row_id = store_text(conn, tenant_id, task_id, "message", {"text": text})
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO session_task_messages
                (tenant_id, task_id, conversation_binding_id, binding_version, input_version, message_id, sender, text_id, evidence_ref, batch_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (tenant_id, task_id, conversation_binding_id, int(payload.get("binding_version", 0)),
             input_version, message_id, sender, text_row_id, m.get("source_evidence_ref"), batch_id),
        )
        message_ids.append(message_id)
    if not message_ids:
        raise SessionTaskError("非合成批次不得为空", ERR_VALIDATION_FAILED)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO session_task_batches (tenant_id, task_id, batch_id, input_version, message_ids_json, observation_id, synthetic, status)
        VALUES (%s, %s, %s, %s, %s, %s, FALSE, %s)
        """,
        (tenant_id, task_id, batch_id, input_version, json.dumps(message_ids), payload.get("observation_id"), batch_status),
    )
    if batch_status == "accepted":
        # C3：新批次接纳 → 旧决策 superseded + 未开始发送取消（设计 §6/§9）。
        # 调用方（ingest_events）已持 subject→assignment/task 锁，锁序不变。
        from . import decisions as decisions_mod

        decisions_mod.supersede_decisions_on_batch(conn, tenant_id, task_id, input_version)
        # 人工介入判定（设计 §5）：self 消息无法归属到冻结决策正文 → human_required
        self_messages = [m for m in messages if m.get("sender") == "self"]
        if self_messages and decisions_mod.check_manual_intervention(
            conn, tenant_id, task_id, self_messages, scenario_key=scenario_key
        ):
            decisions_mod.handle_manual_intervention(conn, tenant_id, task_id)


def create_decision(tenant_id: str, device_id: UUID, assignment_id: UUID, fence: int, batch_id: str,
                    decision_kind: str, input_version: int, control_epoch: int = -1,
                    spec_revision: int = -1) -> Dict[str, Any]:
    """创建决策记录（五元唯一键幂等）；模型调用/状态推进属 C3，此处落 pending。

    新决策是新授权点而非迟到事实（设计评审 P1-6）：实时开关 + 冻结版本校验
    （control_epoch/spec_revision 必须与当前任务一致，防暂停/改版后旧请求
    被建成新版本决策）。
    """
    if decision_kind not in DECISION_KINDS:
        raise SessionTaskError(f"非法 decision_kind: {decision_kind}", ERR_VALIDATION_FAILED)
    if not tenant_allowed(tenant_id):
        raise SessionTaskError("会话任务功能未启用", ERR_FEATURE_DISABLED, 403)
    cfg = get_session_tasks_config()
    with _conn() as conn:
        # 锁序 subject→assignment（评审 P1-1）：与控制/授权路径串行化
        task_id_loc = _assignment_task_id(conn, tenant_id, device_id, assignment_id)
        if task_id_loc is None:
            raise SessionTaskError("assignment 不存在或不属于本设备", ERR_STALE_ASSIGNMENT, 409)
        # 场景开关热读（评审 P2-5）：按任务行权威 scenario_key 分派（CR 阻断 2）
        _ensure_scenario_enabled(tenant_id, _task_scenario_key(tenant_id, task_id_loc))
        _lock_task_subject(conn, tenant_id, task_id_loc)
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT a.task_id, a.fence, a.lease_expires_at, t.status, t.spec_revision, t.control_epoch
            FROM session_task_assignments a JOIN session_tasks t ON t.tenant_id=a.tenant_id AND t.id=a.task_id
            WHERE a.tenant_id=%s AND a.id=%s AND a.device_id=%s AND a.is_current=TRUE FOR UPDATE OF a
            """,
            (tenant_id, assignment_id, device_id),
        )
        a = cursor.fetchone()
        if a is None or a["fence"] != fence:
            raise SessionTaskError("assignment 已过时或非本设备 assignment（STALE_ASSIGNMENT）", ERR_STALE_ASSIGNMENT, 409)
        if control_epoch != a["control_epoch"] or spec_revision != a["spec_revision"]:
            raise SessionTaskError(
                "任务控制代或版本已变化（暂停/改版后旧请求失效），拒绝创建决策", ERR_STALE_ASSIGNMENT, 409
            )
        if _tz(a["lease_expires_at"]) <= _now():
            raise SessionTaskError("租约已过期，禁止创建决策", ERR_LEASE_EXPIRED, 409)
        if a["status"] != STATUS_ACTIVE:
            raise SessionTaskError(f"任务状态 {a['status']} 禁止新决策", "CONFLICT", 409)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT input_version, synthetic, status FROM session_task_batches WHERE tenant_id=%s AND task_id=%s AND batch_id=%s",
            (tenant_id, a["task_id"], batch_id),
        )
        batch = cursor.fetchone()
        if batch is None:
            raise SessionTaskError("批次不存在或尚未被接纳", "NOT_FOUND", 404)
        if batch["status"] != "accepted":
            raise SessionTaskError(
                f"批次状态 {batch['status']} 不可作为决策输入（历史对账批次不得触发新决策）", "CONFLICT", 409
            )
        if decision_kind == DECISION_KIND_OPENING:
            if not cfg.opening_enabled:
                raise SessionTaskError("开场白决策未启用", ERR_FEATURE_DISABLED, 403)
            if not batch["synthetic"]:
                raise SessionTaskError("opening 决策只能引用 opening 合成批次", ERR_VALIDATION_FAILED)
            # §13.2：已有发布后新入站 → 取消 opening（不再产生；需主动开场须新任务）
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) AS n FROM session_task_batches WHERE tenant_id=%s AND task_id=%s AND synthetic=FALSE AND status='accepted'",
                (tenant_id, a["task_id"]),
            )
            if int(cursor.fetchone()["n"]) > 0:
                raise SessionTaskError("已有新入站消息，开场白取消（不自动重新生成）", "CONFLICT", 409)
        elif batch["synthetic"]:
            raise SessionTaskError("reply 决策不得引用 opening 合成批次", ERR_VALIDATION_FAILED)
        elif int(input_version) != batch["input_version"]:
            raise SessionTaskError("input_version 与批次不匹配", ERR_VALIDATION_FAILED)
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO session_task_decisions (tenant_id, task_id, spec_revision, batch_id, decision_kind, status, input_version)
                VALUES (%s, %s, %s, %s, %s, 'pending', %s)
                ON CONFLICT (tenant_id, task_id, spec_revision, batch_id, decision_kind) DO NOTHING
                RETURNING id, status
                """,
                (tenant_id, a["task_id"], a["spec_revision"], batch_id, decision_kind,
                 0 if decision_kind == DECISION_KIND_OPENING else int(input_version)),
            )
            row = cursor.fetchone()
            if row is None:
                cursor.execute(
                    """
                    SELECT id, status FROM session_task_decisions
                    WHERE tenant_id=%s AND task_id=%s AND spec_revision=%s AND batch_id=%s AND decision_kind=%s
                    """,
                    (tenant_id, a["task_id"], a["spec_revision"], batch_id, decision_kind),
                )
                row = cursor.fetchone()
            conn.commit()
        except SessionTaskError:
            conn.rollback()
            raise
        except Exception as exc:  # noqa: BLE001 opening 部分唯一索引冲突
            conn.rollback()
            if _is_unique_violation(exc, "idx_session_task_decisions_opening"):
                raise SessionTaskError("开场白决策已存在（每任务最多一次）", ERR_IDEMPOTENCY_CONFLICT, 409) from exc
            raise
    return {"decision_id": str(row["id"]), "status": row["status"]}


def get_decision(tenant_id: str, device_id: UUID, assignment_id: UUID, decision_id: UUID) -> Dict[str, Any]:
    """决策状态查询（设备只读自己 assignment 的决策；含冻结 action 供端侧分流）。"""
    with _conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT d.id, d.status, d.decision_kind, d.batch_id, d.input_version, d.reply_text_hash,
                   d.action, d.failure_code, d.updated_at
            FROM session_task_decisions d
            JOIN session_task_assignments a ON a.tenant_id=d.tenant_id AND a.task_id=d.task_id
            WHERE d.tenant_id=%s AND d.id=%s AND a.id=%s AND a.device_id=%s AND a.is_current=TRUE
            """,
            (tenant_id, decision_id, assignment_id, device_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise SessionTaskError("决策不存在或不属于当前 assignment", "NOT_FOUND", 404)
        return dict(row)


# ---------------------------------------------------------------------------
# 预算预留（C3 接真实账务；已结算 + 未决预留共同占任务额度）
# ---------------------------------------------------------------------------


def task_spend(conn, tenant_id: str, task_id: UUID) -> Tuple[float, float]:  # noqa: ANN001
    """返回 (已结算, 未决预留)。"""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT
            COALESCE(SUM(settled_amount), 0) AS settled,
            COALESCE(SUM(CASE WHEN state='reserved' THEN amount ELSE 0 END), 0) AS reserved
        FROM session_task_cost_reservations WHERE tenant_id=%s AND task_id=%s
        """,
        (tenant_id, task_id),
    )
    row = cursor.fetchone()
    return Decimal(row["settled"] or 0), Decimal(row["reserved"] or 0)  # Decimal 贯通比较（评审 P2-9）


def _decimal_amount(value, field: str = "amount") -> Decimal:  # noqa: ANN001
    """金额校验（评审 P2-6）：Decimal 化，拒绝非有限/非正/非法值；精度与库列
    NUMERIC(14,4) 对齐——超过 4 位小数直接拒绝（不静默截断，防「0.00001 校验
    通过、落库为零」的账实不一致）。"""
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise SessionTaskError(f"{field} 非法数值", ERR_VALIDATION_FAILED) from exc
    if not d.is_finite() or d <= 0:
        raise SessionTaskError(f"{field} 必须为正的有限数值", ERR_VALIDATION_FAILED)
    quantized = d.quantize(Decimal("0.0001"))
    if quantized != d:
        raise SessionTaskError(f"{field} 精度超过 4 位小数（与账本 NUMERIC(14,4) 不符）", ERR_VALIDATION_FAILED)
    if quantized.as_tuple().exponent < -4 or abs(quantized) >= Decimal("10000000000000"):
        raise SessionTaskError(f"{field} 超出账本范围", ERR_VALIDATION_FAILED)
    return quantized


def reserve_cost(tenant_id: str, task_id: UUID, purpose: str, ref_key: str, amount) -> Dict[str, Any]:  # noqa: ANN001
    """原子预留（幂等：同 purpose+ref_key 返回既有行）；超任务额度 → TASK_BUDGET_EXHAUSTED。

    金额全程 Decimal 比较（0.1+0.2 类浮点误差不再误拒足额预算）。
    """
    amount = _decimal_amount(amount)
    with _conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT current_spec_id FROM session_tasks WHERE tenant_id=%s AND id=%s FOR UPDATE",
            (tenant_id, task_id),
        )
        task = cursor.fetchone()
        if task is None:
            raise SessionTaskError("任务不存在", "NOT_FOUND", 404)
        spec = _load_spec_by_spec_id(conn, tenant_id, task["current_spec_id"], task_id)
        max_cost = Decimal(str(spec["limits"]["max_cost_units"]))
        settled, reserved = task_spend(conn, tenant_id, task_id)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, amount, state FROM session_task_cost_reservations WHERE tenant_id=%s AND task_id=%s AND purpose=%s AND ref_key=%s",
            (tenant_id, task_id, purpose, ref_key),
        )
        existing = cursor.fetchone()
        if existing is not None:
            conn.rollback()
            return {"reservation_id": str(existing["id"]), "amount": float(existing["amount"]), "state": existing["state"], "idempotent": True}  # 展示层 float，账内保持 NUMERIC
        if settled + reserved + amount > max_cost:
            raise SessionTaskError(
                f"任务预算不足（已结算 {settled} + 未决 {reserved} + 本次 {amount} > {max_cost}）", ERR_BUDGET_EXCEEDED, 409
            )
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO session_task_cost_reservations (tenant_id, task_id, purpose, ref_key, amount)
                VALUES (%s, %s, %s, %s, %s) RETURNING id
                """,
                (tenant_id, task_id, purpose, ref_key, amount),
            )
            rid = cursor.fetchone()["id"]
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return {"reservation_id": str(rid), "amount": amount, "state": "reserved", "idempotent": False}


def settle_cost(tenant_id: str, task_id: UUID, purpose: str, ref_key: str, settled_amount) -> Dict[str, Any]:  # noqa: ANN001
    """结算预留（实际扣账在 C3 接 client_usage_logs；此处更新预留账状态）。"""
    settled_amount = _decimal_amount(settled_amount, field="settled_amount")
    with _conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE session_task_cost_reservations
            SET state='settled', settled_amount=%s, updated_at=CURRENT_TIMESTAMP
            WHERE tenant_id=%s AND task_id=%s AND purpose=%s AND ref_key=%s AND state='reserved'
            RETURNING id
            """,
            (settled_amount, tenant_id, task_id, purpose, ref_key),
        )
        row = cursor.fetchone()
        if row is None:
            # 结算幂等（评审 P2-6）：同键同额重放返回原结果；异额重放拒绝
            cursor.execute(
                "SELECT id, state, settled_amount FROM session_task_cost_reservations WHERE tenant_id=%s AND task_id=%s AND purpose=%s AND ref_key=%s",
                (tenant_id, task_id, purpose, ref_key),
            )
            existing = cursor.fetchone()
            conn.rollback()
            if existing is not None and existing["state"] == "settled":
                if Decimal(existing["settled_amount"]) == settled_amount:
                    return {"reservation_id": str(existing["id"]), "state": "settled", "idempotent": True}
                raise SessionTaskError("同键结算金额与已结算不一致，拒绝重放", "CONFLICT", 409)
            raise SessionTaskError("预留不存在或已结算", "NOT_FOUND", 404)
        conn.commit()
    return {"reservation_id": str(row["id"]), "state": "settled", "idempotent": False}


def release_cost(tenant_id: str, task_id: UUID, purpose: str, ref_key: str) -> Dict[str, Any]:
    """释放未开始且安全取消的预留（unknown 保留占用，不释放）。"""
    with _conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE session_task_cost_reservations SET state='released', updated_at=CURRENT_TIMESTAMP
            WHERE tenant_id=%s AND task_id=%s AND purpose=%s AND ref_key=%s AND state='reserved'
            RETURNING id
            """,
            (tenant_id, task_id, purpose, ref_key),
        )
        row = cursor.fetchone()
        if row is None:
            raise SessionTaskError("预留不存在或不可释放", "NOT_FOUND", 404)
        conn.commit()
    return {"reservation_id": str(row["id"]), "state": "released"}


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------


def list_tasks(tenant_id: str, user_id: str, *, limit: int = 20, offset: int = 0,
               status: Optional[str] = None) -> Dict[str, Any]:
    """属主过滤列表（列表不带正文）。"""
    where = "tenant_id=%s AND user_id=%s"
    params: List[Any] = [tenant_id, user_id]
    if status:
        where += " AND status=%s"
        params.append(status)
    with _conn() as conn:
        cursor = conn.cursor()
        cursor.execute(f"SELECT COUNT(*) AS cnt FROM session_tasks WHERE {where}", params)
        total = cursor.fetchone()["cnt"]
        cursor.execute(
            f"""
            SELECT id, scenario_key, device_id, status, version, spec_revision, control_epoch,
                   completion_reason, created_at, updated_at
            FROM session_tasks WHERE {where} ORDER BY created_at DESC LIMIT %s OFFSET %s
            """,
            params + [limit, offset],
        )
        items = [dict(r) for r in cursor.fetchall()]
    # Reuse the ACL-protected projection and never expose encrypted references.
    for item in items:
        detail = get_task(tenant_id, user_id, item["id"])
        spec = detail.get("draft_spec") or detail.get("spec") or {}
        for key in ("phase", "input_version", "binding_label", "device_online", "last_observed_at", "replies_count", "rounds_count", "decisions_count", "cost", "blocked_reason"):
            item[key] = detail.get(key)
        item["goal_summary"] = spec.get("goal", "")[:120]
        item.update({k: spec.get("limits", {}).get(k) for k in ("max_replies", "max_cost_units", "expires_at")})
    return {"total": total, "items": items}


def get_task(tenant_id: str, user_id: str, task_id: UUID) -> Dict[str, Any]:
    """详情：属主校验 + 解密 spec + 花费/预算投影。"""
    with _conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, user_id, scenario_key, device_id, account_binding_id, conversation_binding_id,
                   status, version, spec_revision, current_spec_id, control_epoch, server_control_seq,
                   completion_reason, blocked_reason, created_at, updated_at, draft_spec_text_id
            FROM session_tasks WHERE tenant_id=%s AND id=%s
            """,
            (tenant_id, task_id),
        )
        task = cursor.fetchone()
        if task is None or task["user_id"] != user_id:
            raise SessionTaskError("任务不存在或无权访问", "NOT_FOUND", 404)
        published_spec = (
            _load_spec_by_spec_id(conn, tenant_id, task["current_spec_id"], task_id)
            if task["current_spec_id"] else None
        )
        # draft_spec：待确认草稿（draft 任务或暂停改版后与已发布并存的下一版本），
        # 保证「展示 / 确认 / 发布」三者绑定同一份内容（设计评审 P1-3）
        draft_spec = (
            load_text(conn, tenant_id, task_id, task["draft_spec_text_id"], expected_purpose="spec")
            if task["draft_spec_text_id"] and task["status"] in (STATUS_DRAFT, STATUS_PAUSED) else None
        )
        settled, reserved = task_spend(conn, tenant_id, task_id)
        from .workbench import projection
        projected = projection(conn, tenant_id, task)
    result = dict(task)
    result.update(projected)
    result.pop("draft_spec_text_id", None)
    result["spec"] = published_spec
    result["draft_spec"] = draft_spec
    result["cost"] = {
        "settled": float(settled),  # 展示层 float；账内/比较全程 Decimal（评审 P2-9）
        "reserved": float(reserved),
        "max_cost_units": float(published_spec["limits"]["max_cost_units"]) if published_spec else None,
    }
    return result


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------


def _load_spec_by_spec_id(conn, tenant_id: str, spec_id: Optional[UUID], task_id: UUID) -> Dict[str, Any]:  # noqa: ANN001
    if spec_id is None:
        raise SessionTaskError("任务尚未发布，无 spec", "CONFLICT", 409)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT spec_text_id FROM session_task_specs WHERE tenant_id=%s AND task_id=%s AND id=%s",
        (tenant_id, task_id, spec_id),
    )
    row = cursor.fetchone()
    if row is None:
        raise SessionTaskError("spec 不存在", "NOT_FOUND", 404)
    return load_text(conn, tenant_id, task_id, row["spec_text_id"], expected_purpose="spec")


def _binding_valid_for_allocation(conn, tenant_id: str, conversation_binding_id,
                                  scenario_key: Optional[str] = None) -> bool:  # noqa: ANN001
    """领取/分配时的绑定复核（九处 #5）：语义由描述器 binding_resolver 承载。

    微信 resolver 与原内联 SQL 逐字一致（B1.1 行为锁定测试）；场景未注册时
    fail-closed 返回 False（不可分配）。
    """
    from .scenario_descriptor import get_descriptor

    descriptor = get_descriptor(scenario_key) if scenario_key else None
    if descriptor is None:
        return False
    return bool(descriptor.binding_resolver.is_valid_for_allocation(conn, tenant_id, conversation_binding_id))


def _verify_bindings(tenant_id: str, user_id: str, device_id: str, account_binding_id: str,
                     conversation_binding_id: str, *, require_verified: bool = True, conn=None,
                     scenario_key: Optional[str] = None) -> None:  # noqa: ANN001
    """绑定属主/租户校验：设备 + 会话绑定必须存在且属主匹配（设计 §11/§13.3）。

    B1.2（九处 #5）：绑定行查询与发布有效性语义由描述器 binding_resolver 承载
    （微信 resolver 与原 SQL/错误文案逐字一致）。

    生产发布（require_verified=True）要求 conversation binding 通过场景发布
    有效性校验；建草稿允许 pending（§13.3）。conn 可由持锁事务的调用方传入
    （publish 场景避免嵌套第二个池化连接）；缺省自开连接。
    """
    from .scenario_descriptor import get_descriptor

    descriptor = get_descriptor(scenario_key) if scenario_key else None
    if descriptor is None:
        raise SessionTaskError(f"场景未注册: {scenario_key}", ERR_VALIDATION_FAILED, 400)

    def _device_row(db_conn):  # noqa: ANN202
        cursor = db_conn.cursor()
        cursor.execute(
            "SELECT tenant_id, user_id, status FROM local_tool_devices WHERE id=%s",
            (device_id,),
        )
        return cursor.fetchone()

    if conn is not None:
        device = _device_row(conn)
    else:
        with _conn() as own_conn:
            device = _device_row(own_conn)
    if device is None or str(device["tenant_id"]) != tenant_id or str(device["user_id"]) != user_id:
        raise SessionTaskError("设备不存在或不属于当前用户", "NOT_FOUND", 404)
    if device["status"] not in ("paired", "active"):
        raise SessionTaskError(f"设备状态 {device['status']} 不可用", "CONFLICT", 409)
    # 绑定行查询（租户过滤；跨租户/不存在统一"不存在或不属于当前用户"）
    cursor = conn.cursor() if conn is not None else None
    if cursor is not None:
        binding = descriptor.binding_resolver.get_binding_by_id(cursor, tenant_id, conversation_binding_id)
    else:
        with _conn() as own_conn:
            binding = descriptor.binding_resolver.get_binding_by_id(
                own_conn.cursor(), tenant_id, conversation_binding_id
            )
    if binding is None or str(binding["user_id"]) != user_id:
        raise SessionTaskError("会话绑定不存在或不属于当前用户", "NOT_FOUND", 404)
    if str(binding["device_id"]) != str(device_id) or str(binding["account_binding_id"]) != str(account_binding_id):
        raise SessionTaskError("会话绑定与设备/账号绑定不匹配", ERR_VALIDATION_FAILED)
    if require_verified:
        # 发布有效性语义（含名称定位上下文分支与逐条错误文案）由场景解析器持有
        descriptor.binding_resolver.ensure_valid_for_publish(binding)


def _required_capabilities(scenario_key: Optional[str] = None) -> tuple:  # noqa: ANN202
    """设备必需能力 = 通用部分 + 场景描述器 required_send_capability（九处之 #1）。

    scenario_key 提供且描述器已注册时拼接场景发送能力；未注册/未提供只要求
    通用能力（session_task_v1 + session_observer_v1）。
    """
    caps = list(REQUIRED_DEVICE_CAPABILITIES)
    if scenario_key:
        from .scenario_descriptor import get_descriptor

        descriptor = get_descriptor(scenario_key)
        if descriptor is not None:
            capability = descriptor.required_send_capability
            if capability and capability not in caps:
                caps.append(capability)
    return tuple(caps)


def _check_device_capabilities(conn, tenant_id: str, device_id, scenario_key: Optional[str] = None) -> None:  # noqa: ANN001
    """claim/publish 前的能力检查：通用能力 + 场景发送能力（scenario_key 提供时拼接）。

    调用点必须携带场景上下文（设计 §4.2 #1）：publish/resume 传任务行
    scenario_key；claim 在候选任务定位后按候选场景检查。
    """
    cursor = conn.cursor()
    cursor.execute(
        "SELECT capabilities_json FROM local_tool_devices WHERE tenant_id=%s AND id=%s AND status='active'",
        (tenant_id, device_id),
    )
    row = cursor.fetchone()
    if row is None:
        raise SessionTaskError("设备不存在或未激活", ERR_CAPABILITY_MISSING, 409)
    cap_names = _parse_capability_names(row["capabilities_json"])
    missing = [c for c in _required_capabilities(scenario_key) if c not in cap_names]
    if missing:
        raise SessionTaskError(f"设备缺少必需能力: {','.join(missing)}", ERR_CAPABILITY_MISSING, 409)


def _parse_capability_names(caps: Any) -> set:
    """capabilities_json 兼容解析：JSONB 对象（capabilities 数组，含 providers/
    provider_manifests 键的 Runtime 上报形态）或直接数组/字符串。"""
    if caps is None:
        return set()
    if isinstance(caps, str):
        try:
            caps = json.loads(caps)
        except (TypeError, ValueError):
            return set()
    items: List[Any] = []
    if isinstance(caps, dict):
        raw = caps.get("capabilities")
        if isinstance(raw, list):
            items = raw
    elif isinstance(caps, list):
        items = caps
    names = set()
    for item in items:
        if isinstance(item, str):
            names.add(item)
        elif isinstance(item, dict):
            names.add(str(item.get("name") or item.get("capability") or ""))
    return names


def _is_unique_violation(exc: Exception, constraint_name: str) -> bool:
    return getattr(exc, "diag", None) is not None and getattr(exc.diag, "constraint_name", None) == constraint_name
