"""会话任务决策 worker、预算结算与执行映射（C3，设计 §9/§10/§13.2–13.5）。

职责：
- 异步决策 job：pending 队列 DB claim/租约（每租户 ≤max_decisions_per_tenant 在飞、
  每任务 1 个）、受限输出校验（一次修复重试）、费用归属（预留→实际结算）；
- opening 决策：不调模型，正文取自冻结 spec；同任务跨 revision 至多一条；
- 三模式完成判定与预算/期限终止（严格区分"达成目标"与"预算/期限终止"）；
- supersede：新批次/人工回复使旧决策失效并取消未开始发送；
- prepare_send：ready 决策 → 幂等物化单条底座执行（occurrence/run/delivery/
  invocation + execution_links，execution_lane='session_task'），无第二套发送账本；
- 定向 claim：session 道只允许绑定的 assignment/fence 领取。

锁序（与 C1 一致）：task subject → session_tasks 行 → assignment/decision 等子行。
模型调用在无数据库事务持有的窗口执行（长事务不持锁等模型）。
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID, uuid4

from .config import get_session_tasks_config, tenant_allowed
from .constants import (
    DECISION_KIND_COMPLETION_REVIEW,
    DECISION_KIND_OPENING,
    DECISION_KIND_REPLY,
    ERR_BUDGET_EXCEEDED,
    ERR_FEATURE_DISABLED,
    ERR_LEASE_EXPIRED,
    ERR_STALE_ASSIGNMENT,
    STATUS_ACTIVE,
    STATUS_BLOCKED,
    STATUS_COMPLETED,
    STATUS_HUMAN_REQUIRED,
    STATUS_STOPPED,
    SessionTaskError,
)
from .scenario_hooks import require_hooks
from .service import _conn, _lock_task_subject, _now, _tz, task_spend
from .texts import load_text, store_text

logger = logging.getLogger("session_tasks.decisions")

DECISION_STATUS_TERMINAL = ("ready", "superseded", "failed")
_LANE_SESSION_TASK = "session_task"


class _BudgetStop(Exception):
    """任务预算耗尽：任务 stopped（与租户余额 blocked 区分，§13.4）。"""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _task_expires_at(spec: Dict[str, Any]):
    """从冻结 spec 提取截止时间（ISO 字符串 → aware datetime；无/非法返回 None）。"""
    raw = (spec.get("limits") or {}).get("expires_at")
    if not raw:
        return None
    from datetime import datetime as _dt

    try:
        exp = _dt.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return exp if exp.tzinfo else exp.replace(tzinfo=timezone.utc)


def _assert_task_not_expired(spec: Dict[str, Any]) -> None:
    """任务截止期门禁：到期 → _BudgetStop(deadline_expired)（任务 stopped，
    与"达成目标 completed"严格区分，§5）。"""
    exp = _task_expires_at(spec)
    if exp is not None and exp <= _now():
        raise _BudgetStop("deadline_expired")


class _BalanceBlocked(Exception):
    """租户余额不足：任务 blocked/INSUFFICIENT_TENANT_CREDIT，充值后需显式恢复。"""


# ---------------------------------------------------------------------------
# 任务状态迁移（worker 侧；与 control_task 同锁序：subject → task → 子行）
# ---------------------------------------------------------------------------


def _apply_task_transition(conn, tenant_id: str, task_id: UUID, target: str, reason: str,
                           expected_status: Optional[str] = None) -> bool:  # noqa: ANN001
    """worker 触发的任务状态迁移（完成/终止/转人工/阻断）。

    expected_status 提供时按其 CAS（并发控制/评估竞争时败者无操作）；离开 active
    同步递增 subject authorization_epoch（撤销旧授权代）。返回是否迁移。
    """
    subject = _lock_task_subject(conn, tenant_id, task_id)
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT status, version, control_epoch, scenario_key FROM session_tasks
        WHERE tenant_id=%s AND id=%s FOR UPDATE
        """,
        (tenant_id, task_id),
    )
    task = cursor.fetchone()
    if task is None:
        conn.rollback()
        return False
    if expected_status is not None and task["status"] != expected_status:
        conn.rollback()
        return False
    if task["status"] == target:
        conn.rollback()
        return False
    cursor.execute(
        """
        UPDATE session_tasks
        SET status=%s, version=version+1, control_epoch=control_epoch+1,
            server_control_seq=server_control_seq+1,
            completion_reason=%s, blocked_reason=%s, updated_at=CURRENT_TIMESTAMP
        WHERE tenant_id=%s AND id=%s AND status=%s
        """,
        (
            target,
            reason if target in (STATUS_COMPLETED, STATUS_STOPPED) else None,
            reason if target == STATUS_BLOCKED else None,
            tenant_id, task_id, task["status"],
        ),
    )
    if cursor.rowcount != 1:
        conn.rollback()
        return False
    cursor.execute(
        "UPDATE session_task_assignments SET server_control_seq=server_control_seq+1, updated_at=CURRENT_TIMESTAMP WHERE tenant_id=%s AND task_id=%s AND is_current=TRUE",
        (tenant_id, task_id),
    )
    subject_status = {
        STATUS_HUMAN_REQUIRED: "human_required",
        STATUS_BLOCKED: "blocked",
        STATUS_COMPLETED: "stopped",  # 底座授权语义：完成即不再发送；session_tasks.status 保留 completed
        STATUS_ACTIVE: "active",
    }.get(target, "stopped")
    cursor.execute(
        """
        UPDATE desktop_automation_subjects
        SET status=%s,
            authorization_epoch=CASE WHEN %s THEN authorization_epoch+1 ELSE authorization_epoch END,
            updated_at=CURRENT_TIMESTAMP
        WHERE tenant_id=%s AND scenario_key=%s AND kind='task' AND ref=%s
        """,
        (subject_status, target != STATUS_ACTIVE, tenant_id, task["scenario_key"], str(task_id)),
    )
    if subject is not None and cursor.rowcount != 1:
        conn.rollback()
        raise SessionTaskError("任务授权主体缺失", "CONFLICT", 409)
    from .notifications import record_notice
    record_notice(conn, tenant_id, task_id, target, reason, task["control_epoch"] + 1)
    return True


def _transition_task_standalone(tenant_id: str, task_id: UUID, target: str, reason: str) -> bool:
    with _conn() as conn:
        if not _apply_task_transition(conn, tenant_id, task_id, target, reason, expected_status=STATUS_ACTIVE):
            conn.rollback()
            return False
        conn.commit()
    return True


# ---------------------------------------------------------------------------
# 决策 worker 主 tick
# ---------------------------------------------------------------------------


def run_decision_tick(*, now: Optional[datetime] = None, model_call=None,
                      batch_limit: Optional[int] = None) -> Dict[str, Any]:
    """决策 worker tick：回收滞留 → 领取 pending → 逐条处理 → 任务完成评估。

    model_call(messages, max_tokens) -> {"content": str, "usage": dict}：测试注入；
    生产缺省走 llm_gateway.chat（temperature=0，输出有界）。
    """
    cfg = get_session_tasks_config()
    stats: Dict[str, Any] = {"enabled": bool(cfg.enabled), "claimed": 0, "processed": 0,
                             "reaped": 0, "evaluated": 0, "errors": 0}
    if not cfg.enabled:
        return stats
    now = now or _now()
    stats["reaped"] = _reap_stale_decisions(cfg, now)
    _ensure_completion_reviews()
    try:
        _recover_pending_billing()
    except Exception as exc:  # noqa: BLE001 补偿属收敛性动作：本轮失败不中断 tick，下轮/手动重试
        logger.warning("计费补偿本轮异常（下轮重试）: %s", exc)
    from .scenario_hooks import registered_hook_keys

    for decision in claim_pending_decisions(
        cfg, now, limit=batch_limit or cfg.decision_batch_limit,
        scenario_keys=registered_hook_keys(),
    ):
        stats["claimed"] += 1
        try:
            _process_decision(decision, cfg, now, model_call)
            stats["processed"] += 1
        except (_BudgetStop, _BalanceBlocked):
            stats["processed"] += 1  # 状态迁移已内联完成
        except Exception as exc:  # noqa: BLE001 单条隔离
            stats["errors"] += 1
            logger.warning("决策处理异常 tenant=%s decision=%s: %s", decision["tenant_id"], decision["id"], exc)
    stats["evaluated"] = evaluate_tasks(now=now)
    return stats


def _reap_stale_decisions(cfg, now: datetime) -> int:  # noqa: ANN001
    """滞留回收（§13.5：过期调用未确认结束前不释放槽位启动替代调用——仅在超过
    stale 线后才收敛为 failed + 任务转人工，不重叠重试）。"""
    cutoff = now - timedelta(seconds=cfg.decision_stale_seconds)
    reaped = 0
    # 逐条短事务，锁序 subject→task→decision（与 finalize/supersede 一致，避免交叉死锁）
    with _conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT d.id, d.tenant_id, d.task_id FROM session_task_decisions d
            WHERE d.status='running' AND d.lease_expires_at < %s
            ORDER BY d.updated_at LIMIT 20
            """,
            (cutoff,),
        )
        rows = [dict(r) for r in cursor.fetchall()]
        conn.rollback()
    for row in rows:
        with _conn() as conn:
            _lock_task_subject(conn, row["tenant_id"], row["task_id"])
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id FROM session_task_decisions WHERE tenant_id=%s AND id=%s FOR UPDATE",
                (row["tenant_id"], row["id"]),
            )
            cursor.execute(
                """UPDATE session_task_decisions d SET status='pending', lease_owner=NULL, lease_expires_at=NULL,
                   updated_at=CURRENT_TIMESTAMP
                   WHERE d.tenant_id=%s AND d.id=%s AND d.status='running'
                     AND d.lease_expires_at < %s AND NOT d.model_call_pending
                     AND EXISTS (SELECT 1 FROM session_task_decision_attempts a
                         WHERE a.tenant_id=d.tenant_id AND a.decision_id=d.id AND a.result_text_id IS NOT NULL)
                     AND NOT EXISTS (SELECT 1 FROM session_task_decision_attempts a WHERE a.tenant_id=d.tenant_id AND a.decision_id=d.id AND a.state='started')
                   RETURNING id""", (row["tenant_id"], row["id"], cutoff))
            if cursor.fetchone() is not None:
                conn.commit()
                reaped += 1
                continue
            # 回收只确认"停止调度"（任务转人工、决策失效），**不**确认底层模型调用
            # 已结束——model_call_pending 保留至调用真实返回/异常（§13.5：未确认
            # 结束前不释放槽位启动替代调用；worker 崩溃未返回则长期保守占用）
            cursor.execute(
                """
                UPDATE session_task_decisions
                SET status='failed', failure_code='model_timeout_unconfirmed',
                    lease_owner=NULL, lease_expires_at=NULL,
                    updated_at=CURRENT_TIMESTAMP
                WHERE tenant_id=%s AND id=%s AND status='running' AND lease_expires_at < %s
                """,
                (row["tenant_id"], row["id"], cutoff),
            )
            if cursor.rowcount == 1:
                cursor.execute(
                    "SELECT model_call_ref, model_call_pending FROM session_task_decisions WHERE tenant_id=%s AND id=%s",
                    (row["tenant_id"], row["id"]),
                )
                _row = cursor.fetchone()
                if _row is not None:
                    _release_unstarted_reservations_keep_last(
                        conn, row["tenant_id"], row["task_id"], _row["model_call_ref"], bool(_row["model_call_pending"])
                    )
                cursor.execute("SELECT status FROM session_tasks WHERE tenant_id=%s AND id=%s FOR UPDATE",
                               (row["tenant_id"], row["task_id"]))
                task_state = cursor.fetchone()
                if task_state is not None and task_state["status"] == STATUS_ACTIVE:
                    _apply_task_transition(
                        conn, row["tenant_id"], row["task_id"], STATUS_HUMAN_REQUIRED,
                        "decision_model_timeout_unconfirmed", expected_status=STATUS_ACTIVE,
                    )
                reaped += 1
            conn.commit()
    return reaped


def claim_pending_decisions(cfg, now: datetime, limit: int = 5,
                            scenario_keys: Optional[List[str]] = None) -> List[Dict[str, Any]]:  # noqa: ANN001
    """DB 槽位领取：每租户在飞（running 且租约有效）≤ max_decisions_per_tenant，
    每任务 ≤ 1；FOR UPDATE SKIP LOCKED。被 supersede 的 pending 由 supersede 路径清理。
    scenario_keys：仅领取已注册决策钩子的场景（None = 不过滤，测试用）。"""
    claimed: List[Dict[str, Any]] = []
    with _conn() as conn:
        cursor = conn.cursor()
        scenario_filter = ""
        params: List[Any] = []
        if scenario_keys is not None:
            scenario_filter = " AND t.scenario_key::text = ANY(%s)"
            params.append(list(scenario_keys))
        cursor.execute(
            f"""
            SELECT d.id, d.tenant_id, d.task_id, d.decision_kind, d.spec_revision, d.batch_id,
                   d.input_version, d.model_attempts
            FROM session_task_decisions d
            JOIN session_tasks t ON t.tenant_id=d.tenant_id AND t.id=d.task_id
            WHERE d.status='pending' AND t.status='active'{scenario_filter}
            ORDER BY d.created_at
            LIMIT %s FOR UPDATE OF d SKIP LOCKED
            """,
            (*params, limit * 3),
        )
        candidates = cursor.fetchall()
        tenant_inflight: Dict[str, int] = {}
        tasks_touched: set = set()
        for cand in candidates:
            if len(claimed) >= limit:
                break
            tenant_id = cand["tenant_id"]
            if tenant_id not in tenant_inflight:
                # §13.5 跨进程槽位：租户级 advisory 事务锁串行化"计数+领取"窗口
                # （否则 READ COMMITTED 下并发 worker 互见不到未提交 running，超上限）
                cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"st_decisions:{tenant_id}",))
                cursor.execute(
                    """
                    SELECT COUNT(*) AS n FROM session_task_decisions
                    WHERE tenant_id=%s AND ((status='running' AND lease_expires_at > %s) OR model_call_pending)
                    """,
                    (tenant_id, now),
                )
                tenant_inflight[tenant_id] = int(cursor.fetchone()["n"])
            if tenant_inflight[tenant_id] >= cfg.max_decisions_per_tenant:
                continue
            cursor.execute(
                "SELECT COUNT(*) AS n FROM session_task_decisions WHERE tenant_id=%s AND task_id=%s AND (status='running' OR model_call_pending)",
                (tenant_id, cand["task_id"]),
            )
            if int(cursor.fetchone()["n"]) > 0:
                continue
            lease_owner = f"worker-{uuid4()}"
            cursor.execute(
                """
                UPDATE session_task_decisions
                SET status='running', lease_owner=%s, lease_expires_at=%s, updated_at=CURRENT_TIMESTAMP
                WHERE tenant_id=%s AND id=%s AND status='pending'
                RETURNING id, tenant_id, task_id, decision_kind, spec_revision, batch_id,
                          input_version, model_attempts, lease_owner
                """,
                (lease_owner, now + timedelta(seconds=cfg.decision_lease_seconds),
                 tenant_id, cand["id"]),
            )
            row = cursor.fetchone()
            if row is None:
                continue
            claimed.append(dict(row))
            tenant_inflight[tenant_id] = tenant_inflight.get(tenant_id, 0) + 1
            tasks_touched.add((tenant_id, str(cand["task_id"])))
        conn.commit()
    return claimed


# ---------------------------------------------------------------------------
# 单决策处理
# ---------------------------------------------------------------------------


def _load_task_spec(conn, tenant_id: str, task_id, spec_revision: Optional[int] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:  # noqa: ANN001
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, tenant_id, user_id, scenario_key, status, spec_revision, current_spec_id,
               device_id, conversation_binding_id, control_epoch
        FROM session_tasks WHERE tenant_id=%s AND id=%s
        """,
        (tenant_id, task_id),
    )
    task = cursor.fetchone()
    if task is None:
        raise SessionTaskError("任务不存在", "NOT_FOUND", 404)
    spec_id = task["current_spec_id"]
    cursor.execute(
        "SELECT spec_text_id, revision FROM session_task_specs WHERE tenant_id=%s AND task_id=%s AND id=%s",
        (tenant_id, task_id, spec_id),
    )
    spec_row = cursor.fetchone()
    if spec_row is None:
        raise SessionTaskError("任务 spec 不存在", "CONFLICT", 409)
    if spec_revision is not None and int(spec_row["revision"]) != int(spec_revision):
        raise SessionTaskError("决策冻结版本与任务当前版本不一致", "CONFLICT", 409)
    spec = load_text(conn, tenant_id, task_id, spec_row["spec_text_id"], expected_purpose="spec")
    return dict(task), spec


def _load_transcript(conn, tenant_id: str, task_id, up_to_input_version: int) -> List[Dict[str, Any]]:  # noqa: ANN001
    """已接纳消息（≤ 指定输入版本）解密为模型上下文（时间升序，尾部有界）。"""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT m.message_id, m.sender, m.input_version, m.text_id
        FROM session_task_messages m
        JOIN session_task_batches b ON b.tenant_id=m.tenant_id AND b.task_id=m.task_id AND b.batch_id=m.batch_id
        WHERE m.tenant_id=%s AND m.task_id=%s AND m.input_version <= %s AND b.status='accepted'
        ORDER BY m.input_version, m.created_at
        """,
        (tenant_id, task_id, up_to_input_version),
    )
    rows = cursor.fetchall()
    transcript: List[Dict[str, Any]] = []
    for r in rows[-80:]:
        # #4：所需消息不可读（密文损坏/密钥不匹配/引用缺失）不得静默跳过——
        # 缺失关键约束的上下文喂给模型等于伪造输入。中止本次决策并转入 blocked
        try:
            payload = load_text(conn, tenant_id, task_id, r["text_id"], expected_purpose="message")
        except SessionTaskError as exc:
            raise SessionTaskError(
                f"决策输入消息不可读（message_id={r['message_id']}，{exc.code}），中止决策",
                exc.code if exc.code == "CRYPTO_UNAVAILABLE" else "TRANSCRIPT_UNREADABLE", 503,
            ) from exc
        transcript.append({"message_id": r["message_id"], "sender": r["sender"], "text": payload.get("text", "")})
    return transcript


def _peer_messages_current(conn, tenant_id: str, task_id, input_version: int, *, up_to: bool = False) -> Dict[str, Dict[str, Any]]:  # noqa: ANN001
    """peer 消息（引用归属校验用）。缺省取指定版本；up_to=True 取 **<= 指定版本的
    全部已接纳 peer 消息**（D4：与该次决策冻结 transcript 同范围——模型看到的是
    累积上下文，跨批次的早期证据同样合法；发布基线之前无消息行，天然排除）。"""
    cursor = conn.cursor()
    op = "<=" if up_to else "="
    cursor.execute(
        f"""
        SELECT m.message_id, m.text_id FROM session_task_messages m
        JOIN session_task_batches b ON b.tenant_id=m.tenant_id AND b.task_id=m.task_id AND b.batch_id=m.batch_id
        WHERE m.tenant_id=%s AND m.task_id=%s AND m.input_version {op} %s AND m.sender='peer' AND b.status='accepted'
        """,
        (tenant_id, task_id, input_version),
    )
    result: Dict[str, Dict[str, Any]] = {}
    for r in cursor.fetchall():
        try:
            payload = load_text(conn, tenant_id, task_id, r["text_id"], expected_purpose="message")
        except SessionTaskError:
            continue
        result[r["message_id"]] = {"text": payload.get("text", "")}
    return result


def _validate_judged_proposal_quotes(hooks, spec: Dict[str, Any], validated: Dict[str, Any],
                                     peer_messages: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:  # noqa: ANN001
    """D3：judged done 提议逐条核对引用原文——每个 criterion_results 项的每个
    message_id 必须归属当前冻结范围内的 peer 消息，且每个 quote 必须命中**其对应
    message_ids 之一**的原文连续片段（ID↔quote 错配/虚构/真假混合全部拒绝）。
    返回剔除不合法项后的 evidence；任一项不合法 → 整体按 confirmation 不完整
    处理（继续等待/回复，不进入完成审核）。"""
    from src.weixin_conversation.prompts import OutputInvalid

    results = validated.get("criterion_results") or []
    for item in results:
        mids = item.get("message_ids") or []
        quotes = item.get("quotes") or []
        for mid in mids:
            if mid not in peer_messages:
                raise OutputInvalid(f"标准「{item.get('criterion')}」引用了冻结范围外的消息: {mid}")
        for quote in quotes:
            if not any(_fragment_in_message(quote, peer_messages[m]["text"]) for m in mids):
                raise OutputInvalid(f"标准「{item.get('criterion')}」的引用片段与所引消息原文不符（虚构或错配）")
    return {"criterion_results": results}


def _fragment_in_message(fragment: str, text: str) -> bool:
    if not fragment or not text:
        return False
    if fragment in text:
        return True
    return "".join(fragment.split()) in "".join(text.split())


def _work_window_open(spec: Dict[str, Any], now: datetime) -> bool:
    window = spec.get("work_window")
    if not window:
        return True
    hour = now.astimezone(timezone.utc).hour
    start, end = int(window.get("start_hour_utc", 0)), int(window.get("end_hour_utc", 24) % 24)
    weekdays = window.get("weekdays_utc") or list(range(7))
    if now.astimezone(timezone.utc).weekday() not in weekdays:
        return False
    if start <= end:
        return start <= hour < end or (start == end)
    return hour >= start or hour < end  # 跨日窗口


def _finalize_decision(
    tenant_id: str, task_id, decision_id, lease_owner: str, *, status: str,  # noqa: ANN001
    action: Optional[str] = None, reply_text: Optional[str] = None,
    evidence: Optional[Dict[str, Any]] = None, failure_code: Optional[str] = None,
    extra_updates: Optional[Dict[str, Any]] = None,
) -> bool:
    """终结被本 worker 领取的决策（subject→task→decision 锁序；已被 supersede/
    回收者收割时条件更新败者无操作）。reply_text 提供时冻结正文与 hash。"""
    with _conn() as conn:
        _lock_task_subject(conn, tenant_id, task_id)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT status, lease_owner FROM session_task_decisions WHERE tenant_id=%s AND id=%s FOR UPDATE",
            (tenant_id, decision_id),
        )
        row = cursor.fetchone()
        if row is None or row["status"] != "running" or row["lease_owner"] != lease_owner:
            conn.rollback()
            return False
        sets = ["status=%s", "lease_owner=NULL", "lease_expires_at=NULL", "updated_at=CURRENT_TIMESTAMP"]
        params: List[Any] = [status]
        if action is not None:
            sets.append("action=%s")
            params.append(action)
        if failure_code:
            sets.append("failure_code=%s")
            params.append(failure_code)
        if evidence is not None:
            sets.append("completion_evidence=%s")
            params.append(json.dumps(evidence, ensure_ascii=False))
        if reply_text is not None:
            import hashlib

            text_id = store_text(conn, tenant_id, task_id, "decision", {"text": reply_text})
            sets.append("reply_text_id=%s")
            params.append(text_id)
            sets.append("reply_text_hash=%s")
            params.append(hashlib.sha256(reply_text.encode("utf-8")).hexdigest())
        cursor.execute(
            f"UPDATE session_task_decisions SET {', '.join(sets)} WHERE tenant_id=%s AND id=%s",
            (*params, tenant_id, decision_id),
        )
        # B2 崩溃补偿：终结（failed/superseded/ready）且无未确认结束调用时，释放
        # "预留后未启动"的残留 attempt 预留（保留最后已启动 ref 供结算/核对）
        if status in DECISION_STATUS_TERMINAL:
            cursor.execute(
                "SELECT model_call_ref, model_call_pending FROM session_task_decisions WHERE tenant_id=%s AND id=%s",
                (tenant_id, decision_id),
            )
            _row = cursor.fetchone()
            if _row is not None:
                released = _release_unstarted_reservations_keep_last(
                    conn, tenant_id, task_id, _row["model_call_ref"], bool(_row["model_call_pending"])
                )
                if released:
                    logger.info("终结补偿释放未启动预留 tenant=%s task=%s rows=%s", tenant_id, task_id, released)
        conn.commit()
    return True


def _finalize_and_transition(
    tenant_id: str, task_id, decision_id, lease_owner: str,  # noqa: ANN001
    *,
    decision_status: str, action: Optional[str] = None,
    reply_text: Optional[str] = None, evidence: Optional[Dict[str, Any]] = None,
    failure_code: Optional[str] = None,
    transition: Optional[Tuple[str, str]] = None,
    create_review_for_batch: Optional[str] = None,
    review_input_version: Optional[int] = None,
) -> bool:
    """D2 统一终态入口：决策落库 + 任务状态迁移 + completion_review 创建在**同一
    事务**内完成（subject→task→decision 锁序），以"决策仍 running 且归本 worker
    持有"为原子条件——finalize 与迁移之间不存在可被新输入/暂停穿插的窗口。
    决策已失效（superseded/回收/换代）→ 整体无操作返回 False（迁移/审核创建
    均不发生）。transition=(target_status, reason)；create_review_for_batch
    非空时同事务创建 completion_review 决策（同 batch 唯一键幂等）。"""
    with _conn() as conn:
        _lock_task_subject(conn, tenant_id, task_id)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT status, lease_owner, input_version, spec_revision FROM session_task_decisions WHERE tenant_id=%s AND id=%s FOR UPDATE",
            (tenant_id, decision_id),
        )
        row = cursor.fetchone()
        if row is None or row["status"] != "running" or row["lease_owner"] != lease_owner:
            conn.rollback()
            return False
        # E2 任务侧条件（同事务统一锁序下）：任务仍 active、冻结版本未换代、
        # 输入版本仍当前——模型在飞期间用户的 pause/stop/改版/新输入使任一条件
        # 失效时，决策结果照常落库（记录事实），但任务迁移与审核创建整体跳过
        cursor.execute(
            """
            SELECT t.status, t.spec_revision,
                   (SELECT COALESCE(MAX(b.input_version), 0) FROM session_task_batches b
                    WHERE b.tenant_id=t.tenant_id AND b.task_id=t.id
                      AND b.synthetic=FALSE AND b.status='accepted') AS cur_input_version
            FROM session_tasks t WHERE t.tenant_id=%s AND t.id=%s FOR UPDATE
            """,
            (tenant_id, task_id),
        )
        task_row = cursor.fetchone()
        task_side_ok = (
            task_row is not None
            and task_row["status"] == STATUS_ACTIVE
            and int(task_row["spec_revision"]) == int(row["spec_revision"])
            and int(task_row["cur_input_version"]) == int(row["input_version"] or 0)
        )
        if not task_side_ok and (transition is not None or create_review_for_batch is not None):
            # 控制操作/改版/新输入已先行：仅落库决策结果，不覆盖任务状态、不建审核
            transition = None
            create_review_for_batch = None
        sets = ["status=%s", "lease_owner=NULL", "lease_expires_at=NULL", "updated_at=CURRENT_TIMESTAMP"]
        params: List[Any] = [decision_status]
        if action is not None:
            sets.append("action=%s")
            params.append(action)
        if failure_code:
            sets.append("failure_code=%s")
            params.append(failure_code)
        if evidence is not None:
            sets.append("completion_evidence=%s")
            params.append(json.dumps(evidence, ensure_ascii=False))
        if reply_text is not None:
            import hashlib

            text_id = store_text(conn, tenant_id, task_id, "decision", {"text": reply_text})
            sets.append("reply_text_id=%s")
            params.append(text_id)
            sets.append("reply_text_hash=%s")
            params.append(hashlib.sha256(reply_text.encode("utf-8")).hexdigest())
        cursor.execute(
            f"UPDATE session_task_decisions SET {', '.join(sets)} WHERE tenant_id=%s AND id=%s",
            (*params, tenant_id, decision_id),
        )
        # 同事务任务迁移（决策原子条件成立时才执行）
        if transition is not None:
            target, reason = transition
            _apply_task_transition(conn, tenant_id, task_id, target, reason, expected_status=None)
        # 同事务创建 completion_review（judged done；同 batch 唯一键幂等）
        if create_review_for_batch is not None:
            cursor.execute(
                """
                INSERT INTO session_task_decisions (tenant_id, task_id, spec_revision, batch_id, decision_kind, status, input_version)
                VALUES (%s, %s, (SELECT spec_revision FROM session_task_decisions WHERE tenant_id=%s AND id=%s),
                        %s, 'completion_review', 'pending', %s)
                ON CONFLICT (tenant_id, task_id, spec_revision, batch_id, decision_kind) DO NOTHING
                """,
                (tenant_id, task_id, tenant_id, decision_id, create_review_for_batch,
                 review_input_version if review_input_version is not None else row["input_version"]),
            )
        # D1 崩溃补偿（决策终结且无在飞调用时释放确实未启动的预留）
        if decision_status in DECISION_STATUS_TERMINAL:
            cursor.execute(
                "SELECT model_call_ref, model_call_pending FROM session_task_decisions WHERE tenant_id=%s AND id=%s",
                (tenant_id, decision_id),
            )
            _row = cursor.fetchone()
            if _row is not None:
                _release_unstarted_reservations_keep_last(
                    conn, tenant_id, task_id, _row["model_call_ref"], bool(_row["model_call_pending"])
                )
        conn.commit()
    return True


def _release_decision_lease(tenant_id: str, decision_id, lease_owner: str) -> None:  # noqa: ANN001
    """处理中途失败但决策可重试（如余额阻断）：释放回 pending（模型未调用时）。"""
    with _conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE session_task_decisions SET status='pending', lease_owner=NULL, lease_expires_at=NULL,
                   updated_at=CURRENT_TIMESTAMP
            WHERE tenant_id=%s AND id=%s AND status='running' AND lease_owner=%s
            """,
            (tenant_id, decision_id, lease_owner),
        )
        conn.commit()


def _default_model_call(messages: List[Dict[str, str]], max_tokens: int) -> Dict[str, Any]:
    """生产模型调用：现有 LLM 网关（temperature=0、输出有界；非流式——流式无计费）。

    决策/审核是结构化判定任务（输出 JSON），关思考提速且防思考烧穿 max_tokens
    致 content 空；max_tokens 预算全部留给正文输出。
    """
    from src.llm.gateway import llm_gateway

    loop = asyncio.new_event_loop()
    try:
        response = loop.run_until_complete(
            llm_gateway.chat_no_thinking(
                messages=messages, temperature=0.0, max_tokens=max_tokens
            )
        )
    finally:
        loop.close()
    response = dict(response)
    response.setdefault("model", llm_gateway.get_model_name())
    return response


def _tenant_balance_locked(conn, tenant_id: str) -> Optional[float]:  # noqa: ANN001
    """租户余额（§13.4 预检；必须在调用方事务内执行——tenants 行 FOR UPDATE 与
    租户 advisory 锁共同覆盖"预检→任务预留写入"窗口，跨任务预留原子化）。租户
    不存在/查询异常返回 None（不误阻断，proxy_tool 同语义）。"""
    try:
        cursor = conn.cursor()
        # SAVEPOINT：SELECT 因连接级错误失败时事务进入 aborted 态，回滚到保存点
        # 恢复事务可用性（后续 task 行锁/预留写入继续执行，预检按异常放行）
        cursor.execute("SAVEPOINT st_balance_check")
        cursor.execute("SELECT credit_balance FROM tenants WHERE tenant_id=%s FOR UPDATE", (tenant_id,))
        row = cursor.fetchone()
        cursor.execute("RELEASE SAVEPOINT st_balance_check")
        if row is None:
            return None
        return float(row["credit_balance"] or 0)
    except SessionTaskError:
        raise
    except Exception:  # noqa: BLE001 预检异常放行（proxy_tool 同语义；SAVEPOINT 已恢复事务）
        try:
            cursor.execute("ROLLBACK TO SAVEPOINT st_balance_check")
        except Exception:  # noqa: BLE001 保存点不可用（如连接已断）：交由外层事务失败收敛
            pass
        return None


def _reserve_decision_budget(
    task: Dict[str, Any], spec: Dict[str, Any], decision: Dict[str, Any],
    attempt_ref: str, cfg,  # noqa: ANN001
) -> None:
    """§13.4 跨任务原子预留：单事务内 取租户 advisory 锁 → tenants 行锁余额预检
    → task 行锁 → 预算额度判断 → 预留行写入，提交后才允许发起模型调用。
    锁序 advisory(租户)→task 行：全库仅本路径按此序取锁，与既有 subject→task→
    子行全序无反向等待（其他 task 行锁持有者不取该 advisory），无死锁环。

    - 余额低于保守预留 → _BalanceBlocked（任务 blocked，充值后显式恢复）；
    - 已结算+未决+本次 > max_cost_units → _BudgetStop（任务 stopped）；
    - 同 purpose+ref_key 幂等（重试/恢复重放不重复占额度）。
    """
    from .service import _load_spec_by_spec_id

    tenant_id = task["tenant_id"]
    task_id = task["id"]
    amount = Decimal(str(cfg.decision_reserve_units))
    with _conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"st_budget:{tenant_id}",))
        balance = _tenant_balance_locked(conn, tenant_id)
        # 幂等先行：同 purpose+ref_key 既有行（重试/恢复重放）不重复占用、不重复
        # 参与余额判定——否则同 attempt 重放会被自身预留挡住
        cursor.execute(
            "SELECT id FROM session_task_cost_reservations WHERE tenant_id=%s AND task_id=%s AND purpose='decision' AND ref_key=%s",
            (tenant_id, task_id, attempt_ref),
        )
        if cursor.fetchone() is not None:
            conn.commit()
            return
        # 跨任务扣减：可用余额 = 当前余额 − 租户全部任务的未决预留（本事务持租户
        # 锁，读-判-写原子；已结算部分已由既有计费实扣余额，不重复扣）
        cursor.execute(
            "SELECT COALESCE(SUM(amount), 0) AS pending FROM session_task_cost_reservations WHERE tenant_id=%s AND state='reserved'",
            (tenant_id,),
        )
        tenant_pending = Decimal(cursor.fetchone()["pending"] or 0)
        if balance is not None and (Decimal(str(balance)) - tenant_pending) < amount:
            conn.rollback()
            raise _BalanceBlocked()
        cursor.execute(
            "SELECT current_spec_id FROM session_tasks WHERE tenant_id=%s AND id=%s FOR UPDATE",
            (tenant_id, task_id),
        )
        task_row = cursor.fetchone()
        if task_row is None:
            conn.rollback()
            raise SessionTaskError("任务不存在", "NOT_FOUND", 404)
        frozen_spec = _load_spec_by_spec_id(conn, tenant_id, task_row["current_spec_id"], task_id)
        # attempt 生命周期（#1）：预留与 attempt 行同事务落库——后续补偿按 attempt
        # 状态精确释放，不再按 task 级"最后 ref"推断
        cursor.execute(
            """
            INSERT INTO session_task_decision_attempts (tenant_id, task_id, decision_id, attempt_ref, state)
            VALUES (%s, %s, %s, %s, 'reserved')
            ON CONFLICT (tenant_id, attempt_ref) DO NOTHING
            """,
            (tenant_id, task_id, decision["id"], attempt_ref),
        )
        # 任务截止期硬门禁（A3）：到期后不得产生任何模型费用/调用——不依赖后台
        # 评估。截止统一读 limits_json（许可链同源，冻结时与加密 spec 一致）
        cursor.execute(
            "SELECT limits_json FROM session_task_specs WHERE tenant_id=%s AND task_id=%s AND id=%s",
            (tenant_id, task_id, task_row["current_spec_id"]),
        )
        _limits_row = cursor.fetchone()
        if _limits_row is not None and _limits_row["limits_json"]:
            _assert_task_not_expired({"limits": json.loads(_limits_row["limits_json"])})
        max_cost = Decimal(str((frozen_spec.get("limits") or {}).get("max_cost_units") or 0))
        settled, reserved = task_spend(conn, tenant_id, task_id)
        if settled + reserved + amount > max_cost:
            conn.rollback()
            raise _BudgetStop("max_cost_units_exhausted")
        cursor.execute(
            "INSERT INTO session_task_cost_reservations (tenant_id, task_id, purpose, ref_key, amount) VALUES (%s, %s, 'decision', %s, %s)",
            (tenant_id, task_id, attempt_ref, amount),
        )
        conn.commit()


def _release_unstarted_reservation(tenant_id: str, task_id, attempt_ref: str) -> None:  # noqa: ANN001
    """释放"明确未启动"的 attempt 费用预留（#1）：仅当 attempt 行仍处
    state='reserved'（从未启动）时释放预留与 attempt 行（→released），幂等（重复
    调用无操作）。已启动/返回/计费中/未知的 attempt 不受影响——保持占用或结算。"""
    with _conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE session_task_decision_attempts SET state='released', updated_at=CURRENT_TIMESTAMP
            WHERE tenant_id=%s AND attempt_ref=%s AND state='reserved'
            RETURNING id
            """,
            (tenant_id, attempt_ref),
        )
        if cursor.fetchone() is not None:
            cursor.execute(
                """
                UPDATE session_task_cost_reservations SET state='released', updated_at=CURRENT_TIMESTAMP
                WHERE tenant_id=%s AND task_id=%s AND purpose='decision' AND ref_key=%s AND state='reserved'
                """,
                (tenant_id, task_id, attempt_ref),
            )
        conn.commit()


def _release_unstarted_reservations_keep_last(conn, tenant_id: str, task_id, model_call_ref, model_call_pending: bool) -> int:  # noqa: ANN001
    """终态崩溃补偿（#1 精确化）：释放该任务下**attempt 行仍处 state='reserved'**
    （确实从未启动）的预留——逐 attempt 依据持久状态判定，不再按 task 级"最后
    ref"推断（那会误释放其他决策/未知费用的预留）。已启动/返回/计费中的 attempt
    一律保留；无 attempt 行的裸预留（历史数据/外部写入）视为未知，保留；
    model_call_pending=TRUE（存在未确认结束调用）时一并保守保留（attempt 行
    状态可能滞后于真实启动）。"""
    if model_call_pending:
        return 0
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE session_task_cost_reservations r SET state='released', updated_at=CURRENT_TIMESTAMP
        WHERE r.tenant_id=%s AND r.task_id=%s AND r.purpose='decision' AND r.state='reserved'
          AND EXISTS (
              SELECT 1 FROM session_task_decision_attempts a
              WHERE a.tenant_id=r.tenant_id AND a.attempt_ref=r.ref_key AND a.state='reserved'
          )
        """,
        (tenant_id, task_id),
    )
    return cursor.rowcount


def _settle_attempt(tenant_id: str, task_id, attempt_ref: str, credit_cost) -> bool:  # noqa: ANN001
    """Only a confirmed reservation settlement completes an attempt."""
    from .service import settle_cost

    amount = Decimal(str(credit_cost))
    if amount == 0:
        # The public reservation API accepts positive amounts only. A confirmed zero
        # model charge must settle as zero as well, rather than inventing a minimum fee.
        with _conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""UPDATE session_task_cost_reservations SET state='settled', settled_amount=0,
                           updated_at=CURRENT_TIMESTAMP WHERE tenant_id=%s AND task_id=%s
                           AND purpose='decision' AND ref_key=%s AND
                           (state='reserved' OR (state='settled' AND settled_amount=0)) RETURNING id""",
                           (tenant_id, task_id, attempt_ref))
            if cursor.fetchone() is None:
                raise SessionTaskError("Zero charge reservation unavailable", "CONFLICT", 409)
            conn.commit()
    else:
        settle_cost(tenant_id, task_id, "decision", attempt_ref, amount)
    with _conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE session_task_decision_attempts SET state='settled', billing_retry_at=NULL, updated_at=CURRENT_TIMESTAMP WHERE tenant_id=%s AND attempt_ref=%s",
            (tenant_id, attempt_ref),
        )
        conn.commit()
    return True


def _defer_attempt_billing(tenant_id: str, attempt_ref: str) -> None:
    # Bounded backoff plus oldest-due ordering prevents poisoned rows occupying every page.
    with _conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """UPDATE session_task_decision_attempts
               SET billing_retry_count=COALESCE(billing_retry_count,0)+1,
                   billing_retry_at=CURRENT_TIMESTAMP + LEAST(300, 5 * (COALESCE(billing_retry_count,0)+1)) * INTERVAL '1 second',
                   updated_at=CURRENT_TIMESTAMP
               WHERE tenant_id=%s AND attempt_ref=%s AND state <> 'settled'""",
            (tenant_id, attempt_ref),
        )
        conn.commit()


def _recover_pending_billing(limit: int = 200) -> int:
    """Recover each returned attempt independently; unknown calls remain untouched."""
    with _conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """SELECT tenant_id, task_id, attempt_ref FROM session_task_decision_attempts
               WHERE state IN ('returned','billing_pending','billed','reservation_missing')
                 AND (billing_retry_at IS NULL OR billing_retry_at <= CURRENT_TIMESTAMP)
               ORDER BY COALESCE(billing_retry_at, updated_at), id LIMIT %s""",
            (limit,),
        )
        rows = [dict(r) for r in cursor.fetchall()]
    recovered = 0
    for row in rows:
        try:
            if _price_and_bill_attempt(row["tenant_id"], row["task_id"], row["attempt_ref"]):
                recovered += 1
            else:
                _defer_attempt_billing(row["tenant_id"], row["attempt_ref"])
        except Exception:  # each attempt owns its failure; never log returned content
            logger.warning(f"Decision billing recovery failed ref={row['attempt_ref']}")
            try:
                _defer_attempt_billing(row["tenant_id"], row["attempt_ref"])
            except Exception:
                logger.warning(f"Decision billing retry scheduling failed ref={row['attempt_ref']}")
    return recovered


def _call_model_with_budget(
    task: Dict[str, Any], spec: Dict[str, Any], decision: Dict[str, Any],
    messages: List[Dict[str, str]], cfg, attempt_ref: str, model_call,  # noqa: ANN001
) -> str:
    """余额预检 → 跨任务原子预留 → 模型调用 → 实际计费 → 按实际结算。

    - 租户余额 ≤0 或低于本次保守预留 → _BalanceBlocked（任务 blocked）；
    - 任务已结算+未决预留 + 本次 > max_cost_units → _BudgetStop（任务 stopped）；
    - 实际费用经 record_background_llm_usage 计入既有账务（ChatRecordDB 同事务扣
      tenants.credit_balance）；任务预留账 settle 为实际值——同一费用源只扣一次。
    """
    from .service import settle_cost

    tenant_id = task["tenant_id"]
    task_id = task["id"]
    # A reclaimed decision replays its encrypted response, including repair attempts.
    # Finalization still checks ownership, input version and task control state.
    with _conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT result_text_id, state FROM session_task_decision_attempts WHERE tenant_id=%s AND task_id=%s AND decision_id=%s AND attempt_ref=%s",
                       (tenant_id, task_id, decision["id"], attempt_ref))
        previous = cursor.fetchone()
        if previous and previous["result_text_id"]:
            try:
                saved = load_text(conn, tenant_id, task_id, previous["result_text_id"], expected_purpose="decision")
                if not isinstance(saved, dict) or not isinstance(saved.get("content"), str):
                    raise ValueError("Invalid saved model result shape")
                return saved["content"]
            except (SessionTaskError, ValueError, TypeError, KeyError) as exc:
                # A durable result must never be silently replaced with a new model call.
                raise SessionTaskError(
                    "已保存的模型结果不可读，中止决策", "MODEL_RESULT_UNREADABLE", 503,
                ) from exc
        if previous and previous["state"] == "started":
            raise SessionTaskError("模型调用结果未知，取消本次模型调用", "CONFLICT", 409)
    # §13.4 跨任务原子预留（租户 advisory 锁内：余额预检 + 任务预算预留同事务）
    _reserve_decision_budget(task, spec, decision, attempt_ref, cfg)
    # max_decisions 硬上限（模型调用决策计数；opening 不占；修复重试属同一决策
    # 不重复计数——排除自身后 used+1 > max 即拒，§5"额度临界双决策"不得放行）
    max_decisions = int((spec.get("limits") or {}).get("max_decisions") or 0)
    if max_decisions:
        with _conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) AS n FROM session_task_decisions WHERE tenant_id=%s AND task_id=%s AND model_attempts > 0 AND id != %s",
                (tenant_id, task_id, decision["id"]),
            )
            used = int(cursor.fetchone()["n"])
        if used + 1 > max_decisions:
            # 未启动即拒绝：释放本 attempt 预留（B2——不留未决占用）
            _release_unstarted_reservation(tenant_id, task_id, attempt_ref)
            raise _BudgetStop("max_decisions_exhausted")
    # §13.5 槽位占位 + 原子启动资格复核（B1）：同一 UPDATE 的 WHERE 保证
    # "决策仍 running、归本 worker 持有、**租约未过期**"与"占位"原子成立——
    # 过期领取不得启动模型（恢复执行需重新领取，重新领取必经租户容量控制）；
    # supersede/回收/换代后不再发起（初次与修复重试同规则）；占位仅由
    # "调用返回（含异常）"清除，未确认结束不释放
    with _conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE session_task_decisions SET model_call_pending=TRUE, updated_at=CURRENT_TIMESTAMP
            WHERE tenant_id=%s AND id=%s AND status='running' AND lease_owner=%s
              AND lease_expires_at > statement_timestamp()
            RETURNING id
            """,
            (decision["tenant_id"], decision["id"], decision["lease_owner"]),
        )
        if cursor.fetchone() is None:
            conn.rollback()
            # 启动被拒（未发生调用）：幂等释放本 attempt 预留（B2——不留未决占用；
            # 已开始/未知的调用预留不经此路径）
            _release_unstarted_reservation(tenant_id, task_id, attempt_ref)
            raise SessionTaskError(
                "决策已被 superseded/回收/租约过期或持有者变化，取消本次模型调用", "CONFLICT", 409
            )
        cursor.execute(
            "UPDATE session_task_decision_attempts SET state='started', updated_at=CURRENT_TIMESTAMP WHERE tenant_id=%s AND attempt_ref=%s",
            (tenant_id, attempt_ref),
        )
        conn.commit()
    caller = model_call or _default_model_call
    try:
        response = caller(messages, cfg.decision_model_max_tokens)
    except Exception:
        with _conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE session_task_decisions SET model_call_pending=FALSE, updated_at=CURRENT_TIMESTAMP WHERE tenant_id=%s AND id=%s",
                (decision["tenant_id"], decision["id"]),
            )
            conn.commit()
        raise
    content = str(response.get("content") or "")
    usage = response.get("usage") or {}
    model_name = response.get("model") or None
    # ---- 第 1 步（先行，无计价依赖）：持久化原始返回事实——usage/model/user、
    # 释放槽位（pending=FALSE）、准确计数（attempts+1）、attempt→returned。
    # 此后任何计价/账务/结算失败都不再影响槽位与事实留存（五轮 #1 顺序修复）
    with _conn() as conn:
        result_text_id = store_text(conn, tenant_id, task_id, "decision", {"content": content})
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE session_task_decisions
            SET model_call_pending=FALSE, model_attempts = model_attempts + 1, model_call_ref=%s,
                updated_at=CURRENT_TIMESTAMP
            WHERE tenant_id=%s AND id=%s
            """,
            (attempt_ref, decision["tenant_id"], decision["id"]),
        )
        cursor.execute(
            """
            UPDATE session_task_decision_attempts
            SET state='returned', usage_json=%s, model=%s, user_id=%s, billing_key=%s, result_text_id=%s, updated_at=CURRENT_TIMESTAMP
            WHERE tenant_id=%s AND attempt_ref=%s
            """,
            (json.dumps({k: int(v) for k, v in usage.items() if isinstance(v, (int, float))}), model_name,
             task.get("user_id"), f"st-decision:{attempt_ref}", result_text_id, tenant_id, attempt_ref),
        )
        conn.commit()
    # ---- 第 2 步：计价（仅一次；失败滞留 returned/credit_cost=NULL，补偿用持久
    # 化 usage 原价重试——不重新调用模型）
    try:
        _price_and_bill_attempt(tenant_id, task_id, attempt_ref)
    except Exception:
        # Durable response and billing facts already exist. Billing recovery is independent
        # of output validation; do not discard the completed model call.
        logger.warning(f"Decision billing deferred ref={attempt_ref}")
    return content


def _attempt_billing_row(tenant_id: str, attempt_ref: str):  # noqa: ANN001
    with _conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT tenant_id, task_id, decision_id, attempt_ref, state, usage_json, credit_cost, billing_key, model, user_id
            FROM session_task_decision_attempts WHERE tenant_id=%s AND attempt_ref=%s
            """,
            (tenant_id, attempt_ref),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def _price_attempt(attempt: Dict[str, Any]) -> Optional[Decimal]:
    """按持久化事实计价（五轮 #1：复用已存金额，避免按变化后配置重新计价）。
    credit_cost 已存 → 直接返回；否则以 usage_json+model 计算并落库。失败返回
    None（attempt 保持 returned，补偿重试）。"""
    if attempt.get("credit_cost") is not None:
        return Decimal(str(attempt["credit_cost"]))
    try:
        usage = json.loads(attempt.get("usage_json") or "{}")
    except (ValueError, TypeError):
        usage = {}
    from src.services import billing as billing_module

    try:
        cost, _breakdown = billing_module.calculate_credit_cost_with_breakdown(
            int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0),
            attempt.get("model"), cached_input_tokens=int(usage.get("cached_tokens") or 0),
        )
    except Exception as exc:  # noqa: BLE001 计价失败：可恢复状态（usage 已持久化）
        logger.warning("决策计价失败（补偿将以持久化用量重试）ref=%s: %s", attempt["attempt_ref"], exc)
        return None
    with _conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE session_task_decision_attempts SET credit_cost=%s, updated_at=CURRENT_TIMESTAMP WHERE tenant_id=%s AND attempt_ref=%s AND credit_cost IS NULL",
            (Decimal(str(cost)), attempt["tenant_id"], attempt["attempt_ref"]),
        )
        cursor.execute("SELECT credit_cost FROM session_task_decision_attempts WHERE tenant_id=%s AND attempt_ref=%s",
                       (attempt["tenant_id"], attempt["attempt_ref"]))
        frozen = cursor.fetchone()
        conn.commit()
    return Decimal(str(frozen["credit_cost"])) if frozen and frozen["credit_cost"] is not None else None


def _bill_attempt_once(tenant_id: str, attempt: Dict[str, Any], credit_cost: float) -> bool:
    """幂等账务写入（五轮 #1 核心）：单事务内
    ① INSERT chat_records(..., billing_ref) ON CONFLICT (tenant_id, billing_ref)
       WHERE billing_ref IS NOT NULL DO NOTHING RETURNING record_id——部分唯一索引
       是判重锚点，并发首次计费/补偿双跑只有一个事务拿到行；
    ② 拿到行者同事务 UPDATE tenants 扣减 credit_balance（判重与扣费同事务）；
    ③ attempt→billed。返回是否由本次执行扣费（False=已存在，幂等跳过）。"""
    billing_ref = attempt.get("billing_key") or f"st-decision:{attempt['attempt_ref']}"
    usage = json.loads(attempt.get("usage_json") or "{}") if attempt.get("usage_json") else {}
    from src.db.models import generate_record_id

    record_id = generate_record_id()
    with _conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO chat_records
                (record_id, session_id, tenant_id, user_id, user_message, total_token_count,
                 prompt_tokens, completion_tokens, cached_input_tokens, model, provider,
                 source_type, credit_cost, status, billing_ref)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'background_llm', %s, 'completed', %s)
            ON CONFLICT (tenant_id, billing_ref) WHERE billing_ref IS NOT NULL DO NOTHING
            RETURNING record_id
            """,
            (record_id, f"background_llm_session_task_decision_{attempt.get('user_id') or 'unknown'}",
             tenant_id, attempt.get("user_id"), "端侧会话任务决策",
             int(usage.get("total_tokens") or 0), int(usage.get("prompt_tokens") or 0),
             int(usage.get("completion_tokens") or 0), int(usage.get("cached_tokens") or 0),
             attempt.get("model"), "session_task_decision", credit_cost, billing_ref),
        )
        owned = cursor.fetchone() is not None
        if not owned:
            cursor.execute("SELECT credit_cost FROM chat_records WHERE tenant_id=%s AND billing_ref=%s", (tenant_id, billing_ref))
            existing = cursor.fetchone()
            if existing is None or Decimal(str(existing["credit_cost"])) != Decimal(str(credit_cost)):
                raise SessionTaskError("Attempt ledger amount mismatch", "BILLING_AMOUNT_MISMATCH", 409)
        if owned and tenant_id and credit_cost and credit_cost > 0:
            cursor.execute(
                "UPDATE tenants SET credit_balance = credit_balance - %s WHERE tenant_id = %s",
                (credit_cost, tenant_id),
            )
        cursor.execute(
            "UPDATE session_task_decision_attempts SET state='billed', updated_at=CURRENT_TIMESTAMP WHERE tenant_id=%s AND attempt_ref=%s AND state <> 'settled'",
            (tenant_id, attempt["attempt_ref"]),
        )
        conn.commit()
    if owned:
        try:
            from src.core.cache_utils import invalidate_tenant_cache

            invalidate_tenant_cache(tenant_id)
        except Exception:  # noqa: BLE001 缓存失效失败不影响账务
            pass
    return owned


def _price_and_bill_attempt(tenant_id: str, task_id, attempt_ref: str) -> bool:  # noqa: ANN001
    """True means billing and reservation settlement both completed."""
    attempt = _attempt_billing_row(tenant_id, attempt_ref)
    if attempt is None or str(attempt["task_id"]) != str(task_id):
        return False
    if attempt["state"] == "settled":
        return True
    if attempt["state"] not in ("returned", "billing_pending", "billed", "reservation_missing"):
        return False
    cost = _price_attempt(attempt)
    if cost is None:
        return False
    _bill_attempt_once(tenant_id, attempt, cost)
    with _conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT state FROM session_task_cost_reservations WHERE tenant_id=%s AND task_id=%s AND purpose='decision' AND ref_key=%s",
                       (tenant_id, task_id, attempt_ref))
        if cursor.fetchone() is None:
            cursor.execute("UPDATE session_task_decision_attempts SET state='reservation_missing', updated_at=CURRENT_TIMESTAMP WHERE tenant_id=%s AND attempt_ref=%s AND state <> 'settled'", (tenant_id, attempt_ref))
            conn.commit()
            return False
    return _settle_attempt(tenant_id, task_id, attempt_ref, cost)



def _process_decision(decision: Dict[str, Any], cfg, now: datetime, model_call) -> None:  # noqa: ANN001
    tenant_id: str = decision["tenant_id"]
    task_id = decision["task_id"]
    decision_id = decision["id"]
    lease_owner = decision["lease_owner"]
    with _conn() as conn:
        task, spec = _load_task_spec(conn, tenant_id, task_id, spec_revision=decision["spec_revision"])
    hooks = require_hooks(task["scenario_key"])

    if decision["decision_kind"] == DECISION_KIND_OPENING:
        _process_opening(tenant_id, task, spec, decision, cfg)
        return
    if decision["decision_kind"] not in (DECISION_KIND_REPLY, DECISION_KIND_COMPLETION_REVIEW):
        _finalize_decision(tenant_id, task_id, decision_id, lease_owner, status="failed", failure_code="unknown_kind")
        return
    if task["status"] != STATUS_ACTIVE:
        # 暂停/阻断：决策留在 pending（恢复后可继续）；终态：superseded
        status = "superseded" if task["status"] in ("completed", "stopped") else "pending"
        if status == "superseded":
            _finalize_decision(tenant_id, task_id, decision_id, lease_owner, status="superseded", failure_code="task_terminal")
        else:
            _release_decision_lease(tenant_id, decision_id, lease_owner)
        return
    if not _work_window_open(spec, now):
        _release_decision_lease(tenant_id, decision_id, lease_owner)
        return
    try:
        if decision["decision_kind"] == DECISION_KIND_COMPLETION_REVIEW:
            _process_completion_review(tenant_id, task, spec, decision, hooks, cfg, now, model_call)
        else:
            _process_reply(tenant_id, task, spec, decision, hooks, cfg, now, model_call)
    except SessionTaskError as exc:
        if exc.status_code == 409 and "取消本次模型调用" in str(exc):
            # 决策在处理途中被 supersede/回收：调用未发起，无需状态迁移
            return
        if exc.code == "MODEL_RESULT_UNREADABLE":
            _finalize_and_transition(
                tenant_id, task_id, decision_id, lease_owner,
                decision_status="failed", failure_code="model_result_unreadable",
                transition=(STATUS_BLOCKED, "model_result_unreadable"),
            )
            return
        if exc.code in ("TRANSCRIPT_UNREADABLE", "CRYPTO_UNAVAILABLE") and "中止决策" in str(exc):
            # 输入不可读：决策 failed + 任务 blocked 同事务（#4/D2——模型零调用）
            _finalize_and_transition(
                tenant_id, task_id, decision_id, decision["lease_owner"],
                decision_status="failed", failure_code="transcript_unreadable",
                transition=(STATUS_BLOCKED, "transcript_unreadable"),
            )
            return
        raise
    except _BalanceBlocked:
        _release_decision_lease(tenant_id, decision_id, lease_owner)
        _transition_task_standalone(tenant_id, task_id, STATUS_BLOCKED, "INSUFFICIENT_TENANT_CREDIT")
    except _BudgetStop as exc:
        _finalize_decision(tenant_id, task_id, decision_id, lease_owner, status="failed", failure_code=exc.reason)
        _transition_task_standalone(tenant_id, task_id, STATUS_STOPPED, exc.reason)


def _process_opening(tenant_id: str, task: Dict[str, Any], spec: Dict[str, Any], decision: Dict[str, Any], cfg) -> None:  # noqa: ANN001
    """opening：不调模型、不占决策数；正文取首次生成该决策的冻结 spec（§13.2）。"""
    decision_id = decision["id"]
    lease_owner = decision["lease_owner"]
    opening_text = spec.get("opening_text")
    if not opening_text:
        _finalize_decision(tenant_id, task["id"], decision_id, lease_owner, status="failed", failure_code="opening_text_missing")
        return
    # 开场前提：尚无已接纳普通批次（已有新入站/人工回复 → 取消 opening，不绕过复验）
    with _conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) AS n FROM session_task_batches WHERE tenant_id=%s AND task_id=%s AND synthetic=FALSE AND status='accepted'",
            (tenant_id, task["id"]),
        )
        if int(cursor.fetchone()["n"]) > 0:
            cursor.execute(
                """
                UPDATE session_task_decisions SET status='superseded', failure_code='inbound_before_opening',
                       lease_owner=NULL, lease_expires_at=NULL, updated_at=CURRENT_TIMESTAMP
                WHERE tenant_id=%s AND id=%s AND status='running' AND lease_owner=%s
                """,
                (tenant_id, decision_id, lease_owner),
            )
            conn.commit()
            return
    _finalize_decision(
        tenant_id, task["id"], decision_id, lease_owner, status="ready",
        action="reply", reply_text=opening_text,
        evidence={"kind": "opening"},
    )


def _process_reply(tenant_id: str, task: Dict[str, Any], spec: Dict[str, Any], decision: Dict[str, Any],
                   hooks, cfg, now: datetime, model_call) -> None:  # noqa: ANN001
    decision_id = decision["id"]
    lease_owner = decision["lease_owner"]
    task_id = task["id"]
    input_version = int(decision["input_version"] or 0)
    with _conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT 1 FROM session_task_messages m
            JOIN session_task_batches b ON b.tenant_id=m.tenant_id AND b.task_id=m.task_id AND b.batch_id=m.batch_id
            WHERE m.tenant_id=%s AND m.task_id=%s AND m.input_version=%s
              AND m.sender='peer' AND b.status='accepted'
            LIMIT 1
            """,
            (tenant_id, task_id, input_version),
        )
        current_peers = cursor.fetchone() is not None
        transcript = _load_transcript(conn, tenant_id, task_id, input_version) if current_peers else []
    if not current_peers:
        # Self echoes still pass ingestion/manual-intervention checks, but do not
        # prompt another reply to a historical peer message.
        _finalize_decision(
            tenant_id, task_id, decision_id, lease_owner, status="ready", action="wait",
            evidence={"kind": DECISION_KIND_REPLY, "input_version": input_version, "reason_code": "no_new_peer"},
        )
        return
    peer_ids = [m["message_id"] for m in transcript if m.get("sender") == "peer"]
    feedback: Optional[str] = None
    validated: Optional[Dict[str, Any]] = None
    for attempt in (1, 2):  # 初次 + 一次修复（§9：修复调用也计决策与费用）
        ref = f"{decision_id}" if attempt == 1 else f"{decision_id}:r{attempt}"
        messages = hooks.build_decision_messages(spec, transcript, DECISION_KIND_REPLY, repair_feedback=feedback)
        content = _call_model_with_budget(task, spec, decision, messages, cfg, ref, model_call)
        try:
            validated = hooks.validate_decision_output(spec, content, peer_ids, DECISION_KIND_REPLY)
            break
        except Exception as exc:  # noqa: BLE001 OutputInvalid
            feedback = str(exc)
            validated = None
    if validated is None:
        if not _finalize_and_transition(
            tenant_id, task_id, decision_id, lease_owner,
            decision_status="failed", failure_code="invalid_model_output",
            transition=(STATUS_HUMAN_REQUIRED, "decision_output_invalid"),
        ):
            logger.info("非法输出决策已失效，跳过任务转人工 tenant=%s decision=%s", tenant_id, decision_id)
        return

    action = validated["action"]
    evidence: Dict[str, Any] = {"kind": DECISION_KIND_REPLY, "input_version": input_version}
    for key in ("reason_code", "evidence_message_ids", "wait_for"):
        if validated.get(key) is not None:
            evidence[key] = validated[key]

    if action == "reply":
        if not _finalize_decision(tenant_id, task_id, decision_id, lease_owner, status="ready",
                                  action="reply", reply_text=validated["reply_text"], evidence=evidence):
            logger.info("reply 决策已失效，丢弃结果 tenant=%s decision=%s", tenant_id, decision_id)
        return
    if action == "wait":
        if not _finalize_decision(tenant_id, task_id, decision_id, lease_owner, status="ready", action="wait", evidence=evidence):
            logger.info("wait 决策已失效，丢弃结果 tenant=%s decision=%s", tenant_id, decision_id)
        return
    if action == "handoff":
        # D2：决策落库与任务迁移同事务原子完成——落库成功即迁移，落库失败即整体
        # 无操作（迟到 handoff 不洗掉新输入版本下的任务状态，无中间窗口）
        if not _finalize_and_transition(
            tenant_id, task_id, decision_id, lease_owner,
            decision_status="ready", action="handoff", evidence=evidence,
            transition=(STATUS_HUMAN_REQUIRED, f"model_handoff:{validated.get('reason_code', '')}"),
        ):
            logger.info("handoff 决策已失效，跳过任务转人工 tenant=%s decision=%s", tenant_id, decision_id)
        return
    # action == done
    mode = (spec.get("completion_rule") or {}).get("mode")
    if mode == "peer_confirmed":
        with _conn() as conn:
            peer_messages = _peer_messages_current(conn, tenant_id, task_id, input_version)
        # §13.2：矛盾（contradicted=true）→ handoff 转人工；缺字段/引用不实 → 继续等待
        contradicted = bool((validated["peer_confirmation"] or {}).get("contradicted"))
        if contradicted:
            evidence["peer_confirmation"] = validated["peer_confirmation"]
            if not _finalize_and_transition(
                tenant_id, task_id, decision_id, lease_owner,
                decision_status="ready", action="handoff", evidence=evidence,
                transition=(STATUS_HUMAN_REQUIRED, "peer_confirmation_contradicted"),
            ):
                logger.info("矛盾 handoff 决策已失效，跳过迁移 tenant=%s decision=%s", tenant_id, decision_id)
            return
        try:
            hooks.validate_peer_confirmation(
                (spec["completion_rule"]).get("fields") or [],
                validated["peer_confirmation"], peer_messages,
            )
        except Exception as exc:  # noqa: BLE001 缺字段/引用不实：继续等待
            evidence["peer_confirmation_invalid"] = str(exc)
            evidence["done_rejected"] = "confirmation_incomplete"
            _finalize_decision(tenant_id, task_id, decision_id, lease_owner, status="ready", action="wait", evidence=evidence)
            return
        evidence["peer_confirmation"] = validated["peer_confirmation"]
        evidence["peer_confirmed"] = True
        _finalize_decision(tenant_id, task_id, decision_id, lease_owner, status="ready", action="done", evidence=evidence)
        return
    # judged：D3 先逐条核对提议引用原文（范围=冻结 transcript 同范围 peer 消息，
    # 含跨批次早期证据 D4）——虚构/错配/混合引用不得进入完成审核，按继续等待收敛
    with _conn() as conn:
        scoped_peers = _peer_messages_current(conn, tenant_id, task_id, input_version, up_to=True)
    try:
        _validate_judged_proposal_quotes(hooks, spec, validated, scoped_peers)
    except Exception as exc:  # noqa: BLE001 OutputInvalid
        evidence["proposal_quote_invalid"] = str(exc)
        evidence["done_rejected"] = "proposal_evidence_invalid"
        _finalize_and_transition(
            tenant_id, task_id, decision_id, lease_owner,
            decision_status="ready", action="wait", evidence=evidence,
        )
        return
    # 建 completion_review（同事务原子，D2）：finalize 败者不建审核
    evidence["criterion_results"] = validated["criterion_results"]
    if not _finalize_and_transition(
        tenant_id, task_id, decision_id, lease_owner,
        decision_status="ready", action="done", evidence=evidence,
        create_review_for_batch=decision["batch_id"], review_input_version=input_version,
    ):
        logger.info("done 提议决策已失效，跳过审核创建 tenant=%s decision=%s", tenant_id, decision_id)


def _process_completion_review(tenant_id: str, task: Dict[str, Any], spec: Dict[str, Any], decision: Dict[str, Any],
                               hooks, cfg, now: datetime, model_call) -> None:  # noqa: ANN001
    """judged 独立完成审核（§5）：一次调用；同意 → goal_judged 候选，不同意 → 转人工。"""
    decision_id = decision["id"]
    lease_owner = decision["lease_owner"]
    task_id = task["id"]
    input_version = int(decision["input_version"] or 0)
    with _conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COALESCE(MAX(input_version), 0) AS cur FROM session_task_batches WHERE tenant_id=%s AND task_id=%s AND synthetic=FALSE AND status='accepted'",
            (tenant_id, task_id),
        )
        if int(cursor.fetchone()["cur"]) != input_version:
            # 审核期间已有更新批次接纳（对方反悔/新消息）：旧版本审核作废（§13.2
            # 三模式均受最新输入版本门禁约束；review 由 supersede 路径清理，此处
            # 兜底防止"新消息未决策完成审核"窗口）
            cursor.execute(
                "UPDATE session_task_decisions SET status='superseded', failure_code='input_version_stale', lease_owner=NULL, lease_expires_at=NULL, updated_at=CURRENT_TIMESTAMP WHERE tenant_id=%s AND id=%s AND status='running' AND lease_owner=%s",
                (tenant_id, decision_id, lease_owner),
            )
            conn.commit()
            return
        transcript = _load_transcript(conn, tenant_id, task_id, input_version)
        # 找到触发审核的 reply done 提议（同 batch 的 ready reply 决策）
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT completion_evidence FROM session_task_decisions
            WHERE tenant_id=%s AND task_id=%s AND batch_id=%s AND decision_kind='reply' AND status='ready'
            """,
            (tenant_id, task_id, decision["batch_id"]),
        )
        row = cursor.fetchone()
    proposal: Dict[str, Any] = {}
    if row is not None and row["completion_evidence"]:
        try:
            proposal = json.loads(row["completion_evidence"])
        except (ValueError, TypeError):
            proposal = {}
    review: Optional[Dict[str, Any]] = None
    feedback: Optional[str] = None
    # 审核结论硬校验输入：与该次冻结 transcript 同范围（<= 版本的已接纳 peer
    # 消息，D4——审核引用早期批次证据合法）
    with _conn() as conn:
        peer_texts = {k: v.get("text", "") for k, v in _peer_messages_current(conn, tenant_id, task_id, input_version, up_to=True).items()}
    for attempt in (1, 2):
        ref = f"{decision_id}" if attempt == 1 else f"{decision_id}:r{attempt}"
        messages = hooks.build_review_messages(spec, transcript, proposal)
        content = _call_model_with_budget(task, spec, decision, messages, cfg, ref, model_call)
        try:
            review = hooks.validate_review_output(content)
            # #3：结论与冻结标准/各项检查一致性（唯一覆盖 + agree↔satisfied 一致
            # + satisfied 须附原文引用）——失败按审核输出非法处理（一次修复后转人工）
            hooks.validate_review_conclusion(spec, review, peer_texts)
            break
        except Exception as exc:  # noqa: BLE001
            feedback = str(exc)
            review = None
    if review is None:
        if not _finalize_and_transition(
            tenant_id, task_id, decision_id, lease_owner,
            decision_status="failed", failure_code="invalid_review_output",
            transition=(STATUS_HUMAN_REQUIRED, "review_output_invalid"),
        ):
            logger.info("审核输出决策已失效，跳过迁移 tenant=%s decision=%s", tenant_id, decision_id)
        return
    evidence = {"kind": DECISION_KIND_COMPLETION_REVIEW, "review": review}
    if review.get("agree"):
        if not _finalize_and_transition(
            tenant_id, task_id, decision_id, lease_owner,
            decision_status="ready", action="done", evidence=evidence,
        ):
            logger.info("审核同意决策已失效，丢弃 tenant=%s decision=%s", tenant_id, decision_id)
    else:
        if not _finalize_and_transition(
            tenant_id, task_id, decision_id, lease_owner,
            decision_status="ready", action="handoff", evidence=evidence,
            transition=(STATUS_HUMAN_REQUIRED, "completion_review_rejected"),
        ):
            logger.info("审核拒绝决策已失效，跳过迁移 tenant=%s decision=%s", tenant_id, decision_id)


# ---------------------------------------------------------------------------
# supersede（ingest 事务内钩子：新批次/人工回复）
# ---------------------------------------------------------------------------


def supersede_decisions_on_batch(conn, tenant_id: str, task_id, new_input_version: int) -> Dict[str, int]:  # noqa: ANN001
    """新批次接纳后：旧 reply（input_version 更低）与 opening 决策 superseded；
    未开始（queued）invocation 取消。调用方持 subject→assignment/task 锁。"""
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE session_task_decisions
        SET status='superseded', lease_owner=NULL, lease_expires_at=NULL, updated_at=CURRENT_TIMESTAMP
        WHERE tenant_id=%s AND task_id=%s AND status IN ('pending','running','ready')
          AND (
            (decision_kind IN ('reply', 'completion_review') AND input_version < %s)
            OR decision_kind='opening'
          )
        RETURNING id
        """,
        (tenant_id, task_id, new_input_version),
    )
    decision_ids = [str(r["id"]) for r in cursor.fetchall()]
    # 取消走独立连接先行提交（request_cancel 自带事务）：与 ingest 事务非原子，
    # ingest 回滚时可能出现"invocation 已取消但决策未 superseded"——方向保守
    # （少发不错发），下次 supersede/终态评估收敛，不反向产生多发
    cancelled = 0
    if decision_ids:
        cursor.execute(
            """
            SELECT l.invocation_id FROM session_task_execution_links l
            WHERE l.tenant_id=%s AND l.decision_id = ANY(%s::uuid[]) AND l.invocation_id IS NOT NULL
            """,
            (tenant_id, decision_ids),
        )
        for link in cursor.fetchall():
            from src.local_tools import repository

            try:
                if repository.request_cancel(str(link["invocation_id"]), tenant_id):
                    cancelled += 1
            except Exception:  # noqa: BLE001 取消失败不阻断事实接纳
                logger.warning("supersede 取消 invocation 失败 inv=%s", link["invocation_id"])
    return {"superseded": len(decision_ids), "cancelled_invocations": cancelled}


def _submitted_echo_messages(conn, tenant_id, task_id):
    """One self in the first timely post-command batch; no OCR text comparison.

    Persistent message IDs consume the one-command capacity across re-batching.
    Multiple self messages or commands competing for one echo remain ambiguous.
    """
    cursor = conn.cursor()
    cursor.execute("""
        WITH candidates AS (
            SELECT d.id AS decision_id, m.message_id, m.text_id
            FROM session_task_decisions d
            JOIN session_task_execution_links l ON l.tenant_id=d.tenant_id AND l.decision_id=d.id
            JOIN desktop_automation_deliveries dl ON dl.tenant_id=l.tenant_id AND dl.id=l.delivery_id
            JOIN local_tool_invocations i ON i.tenant_id=l.tenant_id AND i.id=l.invocation_id
            CROSS JOIN LATERAL (
                SELECT b.batch_id, b.created_at FROM session_task_batches b
                WHERE b.tenant_id=d.tenant_id AND b.task_id=d.task_id
                  AND b.input_version>d.input_version AND NOT b.synthetic
                  AND b.created_at>=i.created_at
                ORDER BY b.input_version, b.created_at, b.batch_id LIMIT 1
            ) first_batch
            JOIN session_task_messages m ON m.tenant_id=d.tenant_id AND m.task_id=d.task_id
                AND m.batch_id=first_batch.batch_id AND m.sender='self'
            WHERE d.tenant_id=%s AND d.task_id=%s
              AND dl.state='succeeded' AND dl.phase='submitted'
              AND i.arguments_json->>'receipt_mode'='submission'
              AND i.arguments_json->>'receipt_context'='weixin_name'
              AND i.business_ref->>'scenario_key'='weixin.conversation.v1'
              AND first_batch.created_at<=dl.finished_at + INTERVAL '60 seconds'
              AND (SELECT COUNT(*) FROM session_task_messages s WHERE s.tenant_id=d.tenant_id
                   AND s.task_id=d.task_id AND s.batch_id=first_batch.batch_id AND s.sender='self')=1
        ) SELECT message_id, text_id FROM candidates
          GROUP BY message_id, text_id HAVING COUNT(DISTINCT decision_id)=1
        """, (tenant_id, task_id))
    return {str(row["message_id"]): str(row["text_id"]) for row in cursor.fetchall()}


def check_manual_intervention(conn, tenant_id: str, task_id, self_messages: List[Dict[str, Any]]) -> bool:  # noqa: ANN001
    """批次内 self 消息归属核对：无法归属到**实际发送成功**的决策 → 人工介入（§5）。

    归属依据是发送尝试的结果证据（execution_links → delivery succeeded），不是
    "曾生成过相同正文"：未发送/已取消/发送失败的决策正文不能作为回显归属证明。
    计数封顶：同一正文的历史 self 消息总数不得超过该正文成功发送次数——同正文
    再次由人工发送不会被历史正文永久豁免。unknown/不可靠归属按人工介入处理
    （fail-closed，不误发）。消息级证据精确归属属 C0/C5。
    """
    if not self_messages:
        return False
    submitted = _submitted_echo_messages(conn, tenant_id, task_id)
    self_messages = [m for m in self_messages if str(m.get("local_message_id", "")) not in submitted]
    if not self_messages:
        return False
    cursor = conn.cursor()
    # 该正文的成功发送次数（decision → link → delivery succeeded）
    cursor.execute(
        """
        SELECT d.reply_text_id, COUNT(*) AS sent_count
        FROM session_task_decisions d
        JOIN session_task_execution_links l ON l.tenant_id=d.tenant_id AND l.decision_id=d.id
        JOIN desktop_automation_deliveries dl ON dl.tenant_id=l.tenant_id AND dl.id=l.delivery_id
        WHERE d.tenant_id=%s AND d.task_id=%s AND d.reply_text_id IS NOT NULL
          AND dl.state='succeeded' AND dl.phase='verified'
        GROUP BY d.reply_text_id
        """,
        (tenant_id, task_id),
    )
    sent_capacity: Dict[str, int] = {}
    for r in cursor.fetchall():
        try:
            payload = load_text(conn, tenant_id, task_id, r["reply_text_id"], expected_purpose="decision")
        except SessionTaskError:
            continue
        if isinstance(payload, dict) and isinstance(payload.get("text"), str):
            text = payload["text"]
            # 不同决策的 reply_text_id 各自独立：相同正文必须**累加**成功发送容量，
            # 否则第二次自动回显会被误判人工接管（验收 A2）
            sent_capacity[text] = sent_capacity.get(text, 0) + int(r["sent_count"])
    if not sent_capacity:
        return True  # 无任何可靠发送证据：全部 self 消息按人工介入处理
    def echo_key(text: str) -> str:
        from .ocr_matching import ocr_text_matches

        candidates = [sent for sent in sent_capacity if ocr_text_matches(sent, text)]
        # Ambiguous similar commands cannot claim each other's send capacity.
        return candidates[0] if len(candidates) == 1 else ""

    # 历史 self 消息（含本批）按正文计数，不得超过成功发送次数
    cursor.execute(
        """
        SELECT m.text_id FROM session_task_messages m
        WHERE m.tenant_id=%s AND m.task_id=%s AND m.sender='self'
        """,
        (tenant_id, task_id),
    )
    used: Dict[str, int] = {}
    for r in cursor.fetchall():
        if str(r["text_id"]) in submitted.values():
            continue
        try:
            payload = load_text(conn, tenant_id, task_id, r["text_id"], expected_purpose="message")
        except SessionTaskError:
            continue
        text = str(payload.get("text", "")) if isinstance(payload, dict) else ""
        if text:
            key = echo_key(text)
            used[key] = used.get(key, 0) + 1
    for m in self_messages:
        text = echo_key(str(m.get("text", "")))
        if not text or used.get(text, 0) > sent_capacity.get(text, 0):
            return True
    return False


def handle_manual_intervention(conn, tenant_id: str, task_id) -> None:  # noqa: ANN001
    """人工介入：任务 human_required + 旧决策 superseded + 取消未开始发送（同事务）。"""
    supersede_decisions_on_batch(conn, tenant_id, task_id, new_input_version=10**9)
    _apply_task_transition(conn, tenant_id, task_id, STATUS_HUMAN_REQUIRED, "manual_intervention")


# ---------------------------------------------------------------------------
# prepare-send 与定向 claim
# ---------------------------------------------------------------------------


def prepare_send(tenant_id: str, device_id: UUID, assignment_id: UUID, fence: int, decision_id: UUID) -> Dict[str, Any]:
    """ready reply/opening → 幂等物化单条底座执行单元（§9/§10）。

    Phase A 校验（subject→task→assignment→decision，与控制/领取/评估同序）：assignment 当前/fence/租约、
    任务 active、决策 ready+action=reply、input_version 仍当前、工作时段、
    max_replies 预算（已结算发送+未决预留共同占用）。
    Phase B 底座链（幂等）：accept_manual_trigger(request_id=decision_id) → run
    驱动（claim/prepare/execute_next_delivery，execution_lane='session_task'）。
    Phase C execution_links 落账（UNIQUE(tenant,decision) 幂等）→ invocation_id。
    """
    cfg = get_session_tasks_config()
    if not tenant_allowed(tenant_id):
        raise SessionTaskError("会话任务功能未启用", ERR_FEATURE_DISABLED, 403)
    now = _now()
    with _conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT task_id FROM session_task_assignments WHERE tenant_id=%s AND id=%s AND device_id=%s",
            (tenant_id, assignment_id, device_id),
        )
        located = cursor.fetchone()
        if located is None:
            raise SessionTaskError("assignment 不存在或不属于本设备", ERR_STALE_ASSIGNMENT, 409)
        task_id = located["task_id"]
        # 锁序与控制/领取/评估一致（§10）：subject → task 行 → assignment 行
        _lock_task_subject(conn, tenant_id, task_id)
        cursor.execute(
            "SELECT id, status, version, spec_revision, current_spec_id FROM session_tasks WHERE tenant_id=%s AND id=%s FOR UPDATE",
            (tenant_id, task_id),
        )
        task_row = cursor.fetchone()
        cursor.execute(
            """
            SELECT a.fence, a.lease_expires_at, t.status, t.spec_revision, t.current_spec_id,
                   t.user_id, t.device_id, t.scenario_key
            FROM session_task_assignments a JOIN session_tasks t ON t.tenant_id=a.tenant_id AND t.id=a.task_id
            WHERE a.tenant_id=%s AND a.id=%s AND a.device_id=%s AND a.is_current=TRUE FOR UPDATE OF a
            """,
            (tenant_id, assignment_id, device_id),
        )
        a = cursor.fetchone()
        if a is None or a["fence"] != fence:
            raise SessionTaskError("assignment 已过时（STALE_ASSIGNMENT）", ERR_STALE_ASSIGNMENT, 409)
        if _tz(a["lease_expires_at"]) <= now:
            raise SessionTaskError("租约已过期", ERR_LEASE_EXPIRED, 409)
        if task_row is None or task_row["status"] != STATUS_ACTIVE:
            conn.rollback()
            raise SessionTaskError(f"任务状态不可发送（{_safe_status(task_row)})", "CONFLICT", 409)
        cursor.execute(
            """
            SELECT id, task_id, status, action, input_version, spec_revision, reply_text_hash,
                   decision_kind, batch_id
            FROM session_task_decisions WHERE tenant_id=%s AND id=%s FOR UPDATE
            """,
            (tenant_id, decision_id),
        )
        decision = cursor.fetchone()
        if decision is None or str(decision["task_id"]) != str(task_id):
            raise SessionTaskError("决策不存在或不属于该任务", "NOT_FOUND", 404)
        if decision["status"] == "superseded":
            conn.rollback()
            return {"invocation_id": None, "decision_status": "superseded"}
        if decision["status"] != "ready" or (decision["action"] or "reply") != "reply":
            conn.rollback()
            raise SessionTaskError(f"决策状态 {decision['status']} 不可准备发送", "CONFLICT", 409)
        task, spec = _load_task_spec(conn, tenant_id, task_id)
        if int(decision["spec_revision"]) != int(task["spec_revision"]):
            conn.rollback()
            return {"invocation_id": None, "decision_status": "superseded"}
        prepare_hooks = require_hooks(task["scenario_key"])
        lane = prepare_hooks.execution_lane
        # 任务截止期硬门禁（A3）：到期即刻拒绝物化并收敛任务终态（不依赖评估扫描）。
        # 截止统一读 limits_json（与许可链/worker 同源，冻结时与加密 spec 一致）；
        # task_expires 同时下传底座（occurrence/run/invocation/permit 有效期上限）
        cursor.execute(
            "SELECT limits_json FROM session_task_specs WHERE tenant_id=%s AND task_id=%s AND id=%s",
            (tenant_id, task_id, task["current_spec_id"]),
        )
        _spec_row = cursor.fetchone()
        _limits_plain = {}
        if _spec_row is not None and _spec_row["limits_json"]:
            _limits_plain = json.loads(_spec_row["limits_json"])
        task_expires = _task_expires_at({"limits": _limits_plain})
        if task_expires is not None and task_expires <= now:
            conn.rollback()
            _transition_task_standalone(tenant_id, task_id, STATUS_STOPPED, "deadline_expired")
            raise SessionTaskError("任务已到期（expires_at），禁止准备发送", "TASK_EXPIRED", 409)
        cursor.execute(
            """
            SELECT COALESCE(MAX(input_version), 0) AS cur FROM session_task_batches
            WHERE tenant_id=%s AND task_id=%s AND synthetic=FALSE AND status='accepted'
            """,
            (tenant_id, task_id),
        )
        cur_version = int(cursor.fetchone()["cur"])
        dec_version = int(decision["input_version"] or 0)
        version_current = (dec_version == 0 and cur_version == 0) or (dec_version > 0 and cur_version == dec_version)
        if not version_current:
            supersede_decisions_on_batch(conn, tenant_id, task_id, cur_version)
            conn.commit()
            return {"invocation_id": None, "decision_status": "superseded"}
        if not _work_window_open(spec, now):
            conn.rollback()
            raise SessionTaskError("当前不在任务工作时段（WORK_WINDOW_CLOSED）", "WORK_WINDOW_CLOSED", 409)
        # max_replies 预算：已占链接（非 failed/skipped delivery）+ 1 ≤ max_replies
        cursor.execute(
            """
            SELECT l.decision_id, d.state AS delivery_state
            FROM session_task_execution_links l
            LEFT JOIN desktop_automation_deliveries d ON d.tenant_id=l.tenant_id AND d.id=l.delivery_id
            WHERE l.tenant_id=%s AND l.task_id=%s
            """,
            (tenant_id, task_id),
        )
        links = [dict(r) for r in cursor.fetchall()]
        # 已结算发送 + 未决预留共同占额度：非 failed/skipped 的链接都占用（含无
        # delivery 的未决链接）；重试本决策自身的既有链接不重复计数
        occupied = [
            l for l in links
            if l.get("delivery_state") not in ("failed", "skipped")
            and str(l["decision_id"]) != str(decision_id)
        ]
        max_replies = int((spec.get("limits") or {}).get("max_replies") or 0)
        if max_replies and len(occupied) + 1 > max_replies:
            conn.rollback()
            _transition_task_standalone(tenant_id, task_id, STATUS_STOPPED, "max_replies_exhausted")
            raise SessionTaskError("回复上限已用尽（max_replies）", ERR_BUDGET_EXCEEDED, 409)
        existing_link = next((l for l in links if str(l["decision_id"]) == str(decision_id)), None)
        spec_row_id = str(task["current_spec_id"])
        conn.commit()

    # 执行费用预留（>0 才预留——不虚构按轮收费，§13.4/C3 计划）。结算挂接属
    # 结果上报侧（execution 结果 settle），在 weixin 发送有按次积分价之前保持 0
    if cfg.execution_reserve_units > 0:
        from .service import reserve_cost

        try:
            reserve_cost(tenant_id, task_id, "execution", str(decision_id), Decimal(str(cfg.execution_reserve_units)))
        except SessionTaskError as exc:
            if exc.code == ERR_BUDGET_EXCEEDED:
                _transition_task_standalone(tenant_id, task_id, STATUS_STOPPED, "max_cost_units_exhausted")
            raise

    # #5 幂等快路径：既有链接直接复用稳定执行身份（并发重复 prepare / 崩溃重试
    # 不解释为新发送尝试；物化内部的竞态窗口由底座稳定业务 dedupe key 收敛）
    with _conn() as _quick:
        _qc = _quick.cursor()
        _qc.execute(
            "SELECT invocation_id FROM session_task_execution_links WHERE tenant_id=%s AND decision_id=%s",
            (tenant_id, decision_id),
        )
        _prior = _qc.fetchone()
    if _prior is not None and _prior["invocation_id"]:
        return {
            "invocation_id": str(_prior["invocation_id"]),
            "decision_id": str(decision_id),
            "run_id": None,
            "idempotent": True,
        }
    return _prepare_send_locked(
        tenant_id, task_id, decision_id, task, spec, spec_row_id, assignment_id, fence,
        task_expires, lane, decision, existing_link, now, prepare_hooks,
    )


def _prepare_send_locked(
    tenant_id, task_id, decision_id, task, spec, spec_row_id, assignment_id, fence,
    task_expires, lane, decision, existing_link, now, prepare_hooks,
):
    # Phase B/Phase C（调用方已持同决策 advisory 锁）
    from src.desktop_automation import executor as da_executor
    from src.desktop_automation import runs as runs_module
    from src.desktop_automation import deliveries as deliveries_module
    from src.desktop_automation.adapters import TrustedAdapterRegistry
    from src.desktop_automation.occurrences import accept_manual_trigger

    trigger = accept_manual_trigger(
        tenant_id=tenant_id, scenario_key=task["scenario_key"], task_ref=str(task_id),
        request_id=str(decision_id), user_id=task["user_id"], now=now,
        expires_at=task_expires,  # A3：底座 occurrence/run 截止 ≤ 任务截止期
    )
    if not trigger.get("accepted"):
        raise SessionTaskError("任务触发被拒绝（任务可能已不在 active）", "CONFLICT", 409)
    occurrence_id = trigger.get("occurrence_id")

    def _load_run() -> Optional[Dict[str, Any]]:
        with _conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, tenant_id, occurrence_id, scenario_key, task_ref, revision_ref, user_id,
                       state, device_id, lease_expires_at, fence_token, authorization_epoch
                FROM desktop_automation_runs WHERE tenant_id=%s AND occurrence_id=%s
                """,
                (tenant_id, occurrence_id),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    run = _load_run()
    if run is None:
        raise SessionTaskError("执行 run 缺失", "CONFLICT", 409)
    revision_config = {
        "revision_ref": spec_row_id,
        "operation_descriptor": {
            "operation": "weixin_message_send_v2",
            "provider_key": "weixin",
            "target_ref": str(task["conversation_binding_id"]),
            "payload_ref": prepare_hooks.build_payload_ref(str(decision_id)),
            "payload_hash": decision["reply_text_hash"],
        },
    }
    if run["state"] == "pending":
        claimed = da_executor.claim_pending_run(tenant_id=tenant_id, expected_run_id=str(run["id"]), lease_seconds=300)
        if claimed is not None:
            prepared = da_executor.prepare_claimed_run(
                claimed, revision_config=revision_config, device_id=str(task["device_id"]), now=now
            )
            if not prepared.get("prepared"):
                raise SessionTaskError(f"执行准备失败：{prepared.get('reason')}", "CONFLICT", 409)
            run = _load_run() or run
    if run["state"] in ("pending", "running"):
        deliveries = deliveries_module.list_run_deliveries(str(run["id"]), tenant_id)
        if not deliveries:
            adapter = TrustedAdapterRegistry.require(task["scenario_key"])
            from src.desktop_automation.adapters import AdapterContext

            compiled = adapter.compile_operations(
                AdapterContext(tenant_id=tenant_id, user_id=task["user_id"], scenario_key=task["scenario_key"],
                               task_ref=str(task_id), revision_ref=spec_row_id),
                revision_config,
            )
            from dataclasses import asdict

            with _conn() as conn:
                cursor = conn.cursor()
                deliveries_module.insert_deliveries(cursor, run, [asdict(op) for op in compiled])
                conn.commit()
        if run["state"] == "pending":
            raise SessionTaskError("执行 run 等待领取中，请重试", "CONFLICT", 409)
        da_executor.execute_next_delivery(
            run, now=now, execution_lane=lane, deadline_cap=task_expires,
            dedupe_key_override=f"session:{task_id}:{decision_id}",  # 设计 §10 业务 dedupe key
            extra_business_ref={
                "task_id": str(task_id), "spec_revision": int(decision["spec_revision"]),
                "assignment_id": str(assignment_id), "fence": int(fence),
                "decision_id": str(decision_id), "batch_id": str(decision["batch_id"]),
                "input_version": int(decision["input_version"] or 0),
                "execution_lane": lane,
            },
        )

    # Phase C：execution_links 幂等落账
    invocation_id = existing_link_invocation(tenant_id, task_id, decision_id, run)
    return {
        "invocation_id": invocation_id,
        "decision_id": str(decision_id),
        "run_id": str(run["id"]),
    }


def _safe_status(task_row) -> str:  # noqa: ANN001
    return str(task_row["status"]) if task_row is not None else "unknown"


def existing_link_invocation(tenant_id: str, task_id, decision_id, run) -> Optional[str]:  # noqa: ANN001
    """execution_links 幂等写/读：返回该决策绑定的 invocation_id。"""
    from src.desktop_automation import attempts as attempts_module
    from src.desktop_automation import deliveries as deliveries_module
    from src.local_tools import repository

    with _conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, invocation_id FROM session_task_execution_links WHERE tenant_id=%s AND decision_id=%s",
            (tenant_id, decision_id),
        )
        row = cursor.fetchone()
        if row is not None and row["invocation_id"]:
            conn.commit()
            return str(row["invocation_id"])
        # 解析 delivery / 最新 attempt 的 invocation
        deliveries = deliveries_module.list_run_deliveries(str(run["id"]), tenant_id)
        invocation_id = None
        occurrence_id = run.get("occurrence_id")
        for d in deliveries:
            cursor.execute(
                "SELECT invocation_id FROM desktop_automation_attempts WHERE tenant_id=%s AND delivery_id=%s ORDER BY attempt_no DESC LIMIT 1",
                (tenant_id, str(d["id"])),
            )
            attempt = cursor.fetchone()
            if attempt is not None and attempt["invocation_id"]:
                invocation_id = str(attempt["invocation_id"])
                delivery_id = str(d["id"])
                break
        if invocation_id is None:
            conn.commit()
            raise SessionTaskError("执行单元尚未物化（invocation 缺失），请重试", "CONFLICT", 409)
        cursor.execute(
            """
            INSERT INTO session_task_execution_links (tenant_id, task_id, decision_id, occurrence_id, run_id, delivery_id, invocation_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, decision_id) DO NOTHING
            """,
            (tenant_id, task_id, str(decision_id), str(occurrence_id) if occurrence_id else None,
             str(run["id"]), delivery_id, invocation_id),
        )
        cursor.execute(
            "SELECT invocation_id FROM session_task_execution_links WHERE tenant_id=%s AND decision_id=%s",
            (tenant_id, decision_id),
        )
        linked = cursor.fetchone()
        conn.commit()
    return str(linked["invocation_id"]) if linked else None


def claim_session_invocation(tenant_id: str, device_id: UUID, assignment_id: UUID, fence: int,
                             invocation_id: UUID, claim_token_hash: str, lease_seconds: int,
                             provider_keys: Optional[List[str]]) -> Dict[str, Any]:
    """定向领取 session 道 invocation（§9）：仅绑定 assignment 的 queued 行可领。

    返回 {"invocation": row} 或 {"invocation": None, "state": 当前状态}。
    """
    from src.local_tools import repository

    now = _now()
    with _conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT task_id FROM session_task_assignments WHERE tenant_id=%s AND id=%s AND device_id=%s",
            (tenant_id, assignment_id, device_id),
        )
        located = cursor.fetchone()
        if located is None:
            raise SessionTaskError("assignment 不存在或不属于本设备", ERR_STALE_ASSIGNMENT, 409)
        # 锁序对齐：先 task 行再 assignment 行（短锁窗，仅读取任务状态）
        cursor.execute(
            "SELECT status FROM session_tasks WHERE tenant_id=%s AND id=%s FOR UPDATE",
            (tenant_id, located["task_id"]),
        )
        _task_status_row = cursor.fetchone()
        cursor.execute(
            """
            SELECT a.fence, a.lease_expires_at, t.status
            FROM session_task_assignments a JOIN session_tasks t ON t.tenant_id=a.tenant_id AND t.id=a.task_id
            WHERE a.tenant_id=%s AND a.id=%s AND a.is_current=TRUE FOR UPDATE OF a
            """,
            (tenant_id, assignment_id),
        )
        a = cursor.fetchone()
        if a is None or a["fence"] != fence:
            raise SessionTaskError("assignment 已过时（STALE_ASSIGNMENT）", ERR_STALE_ASSIGNMENT, 409)
        if _tz(a["lease_expires_at"]) <= now:
            raise SessionTaskError("租约已过期", ERR_LEASE_EXPIRED, 409)
        task_status = a["status"]
        conn.commit()
    invocation = repository.get_invocation(str(invocation_id), tenant_id)
    if invocation is None or str(invocation["device_id"]) != str(device_id):
        raise SessionTaskError("invocation 不存在或不属于本设备", "NOT_FOUND", 404)
    lane = invocation.get("execution_lane") or "standard"
    if lane != _LANE_SESSION_TASK:
        raise SessionTaskError("非会话任务执行道", "CONFLICT", 409)
    business_ref = invocation.get("business_ref") or {}
    if str(business_ref.get("task_ref") or business_ref.get("task_id") or "") != str(located["task_id"]):
        raise SessionTaskError("invocation 不属于当前 assignment 的任务", "CONFLICT", 409)
    if task_status != STATUS_ACTIVE:
        return {"invocation": None, "state": f"task_{task_status}"}
    if invocation["state"] != "queued":
        return {"invocation": None, "state": invocation["state"]}
    claimed = repository.claim_next(
        str(device_id), tenant_id, claim_token_hash, lease_seconds,
        provider_keys=provider_keys, execution_lane=_LANE_SESSION_TASK,
        expected_invocation_id=str(invocation_id),
    )
    if claimed is None:
        fresh = repository.get_invocation(str(invocation_id), tenant_id)
        return {"invocation": None, "state": fresh["state"] if fresh else "missing"}
    return {"invocation": claimed}


# ---------------------------------------------------------------------------
# 完成评估
# ---------------------------------------------------------------------------


def _ensure_completion_reviews() -> int:
    """崩溃恢复：judged 模式 ready done 提议（criterion_results 在案）但同 batch
    completion_review 缺失（finalize 与 INSERT 两事务间崩溃）→ 补建 pending review。
    幂等：五元唯一键 ON CONFLICT DO NOTHING。"""
    created = 0
    with _conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT d.tenant_id, d.task_id, d.spec_revision, d.batch_id, d.input_version
            FROM session_task_decisions d
            JOIN session_tasks t ON t.tenant_id=d.tenant_id AND t.id=d.task_id
            WHERE d.decision_kind='reply' AND d.status='ready' AND d.action='done'
              AND t.status='active'
              AND d.completion_evidence LIKE '%criterion_results%'
              AND NOT EXISTS (
                SELECT 1 FROM session_task_decisions r
                WHERE r.tenant_id=d.tenant_id AND r.task_id=d.task_id
                  AND r.spec_revision=d.spec_revision AND r.batch_id=d.batch_id
                  AND r.decision_kind='completion_review'
              )
            LIMIT 10
            """,
        )
        for row in cursor.fetchall():
            cursor.execute(
                """
                INSERT INTO session_task_decisions (tenant_id, task_id, spec_revision, batch_id, decision_kind, status, input_version)
                VALUES (%s, %s, %s, %s, 'completion_review', 'pending', %s)
                ON CONFLICT (tenant_id, task_id, spec_revision, batch_id, decision_kind) DO NOTHING
                """,
                (row["tenant_id"], row["task_id"], row["spec_revision"], row["batch_id"], row["input_version"]),
            )
            if cursor.rowcount == 1:
                created += 1
        conn.commit()
    return created


def evaluate_tasks(*, now: Optional[datetime] = None, limit: int = 50) -> int:
    """扫描 active 会话任务做完成/终止判定（返回评估条数）。"""
    from .scenario_hooks import get_hooks

    now = now or _now()
    evaluated = 0
    with _conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT t.id, t.tenant_id, t.scenario_key FROM session_tasks t
            WHERE t.status='active'
            ORDER BY t.updated_at DESC LIMIT %s
            """,
            (limit,),
        )
        candidates = cursor.fetchall()
    for cand in candidates:
        if get_hooks(cand["scenario_key"]) is None:
            continue
        try:
            if evaluate_task(cand["tenant_id"], cand["id"], now=now):
                evaluated += 1
        except Exception as exc:  # noqa: BLE001 单任务隔离
            logger.warning("完成评估异常 tenant=%s task=%s: %s", cand["tenant_id"], cand["id"], exc)
    return evaluated


def evaluate_task(tenant_id: str, task_id, *, now: datetime) -> bool:  # noqa: ANN001
    """单任务完成评估（subject→task 锁内；幂等，CAS active）。"""
    from .scenario_hooks import require_hooks

    with _conn() as conn:
        _lock_task_subject(conn, tenant_id, task_id)
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, user_id, scenario_key, status, spec_revision, current_spec_id
            FROM session_tasks WHERE tenant_id=%s AND id=%s FOR UPDATE
            """,
            (tenant_id, task_id),
        )
        task = cursor.fetchone()
        if task is None or task["status"] != STATUS_ACTIVE:
            conn.rollback()
            return False
        task_dict, spec = _load_task_spec(conn, tenant_id, task_id)
        spec_id = task_dict["current_spec_id"]
        hooks = require_hooks(task["scenario_key"])
        cursor.execute(
            """
            SELECT l.decision_id, l.invocation_id, d.state AS delivery_state,
                   dec.decision_kind AS decision_kind
            FROM session_task_execution_links l
            LEFT JOIN desktop_automation_deliveries d ON d.tenant_id=l.tenant_id AND d.id=l.delivery_id
            LEFT JOIN session_task_decisions dec ON dec.tenant_id=l.tenant_id AND dec.id=l.decision_id
            WHERE l.tenant_id=%s AND l.task_id=%s
            """,
            (tenant_id, task_id),
        )
        links = [dict(r) for r in cursor.fetchall()]
        # 未决发送：delivery 在途，或绑定 invocation 非终态
        pending = False
        unknown = False
        for l in links:
            if l.get("delivery_state") in ("pending", "dispatched"):
                pending = True
            if l.get("delivery_state") == "unknown":
                unknown = True
            if l.get("invocation_id"):
                cursor.execute(
                    "SELECT state FROM local_tool_invocations WHERE tenant_id=%s AND id=%s",
                    (tenant_id, l["invocation_id"]),
                )
                inv = cursor.fetchone()
                if inv is not None and inv["state"] in ("queued", "claimed", "running"):
                    pending = True
        if unknown:
            conn.rollback()
            if _transition_task_standalone(tenant_id, task_id, STATUS_BLOCKED, "unknown_send_result"):
                logger.warning("任务因未知发送结果阻断 tenant=%s task=%s", tenant_id, task_id)
            return True
        # 最新 done-提议证据 / 审核结论 / 最后 peer 活动 / 未决决策数
        cursor.execute(
            """
            SELECT completion_evidence, updated_at FROM session_task_decisions
            WHERE tenant_id=%s AND task_id=%s AND decision_kind='reply' AND status='ready'
            ORDER BY updated_at DESC LIMIT 1
            """,
            (tenant_id, task_id),
        )
        latest = cursor.fetchone()
        latest_evidence = None
        if latest is not None and latest["completion_evidence"]:
            try:
                parsed = json.loads(latest["completion_evidence"])
                latest_evidence = parsed if parsed.get("peer_confirmed") else None
            except (ValueError, TypeError):
                latest_evidence = None
        cursor.execute(
            """
            SELECT COALESCE(MAX(input_version), 0) AS cur FROM session_task_batches
            WHERE tenant_id=%s AND task_id=%s AND synthetic=FALSE AND status='accepted'
            """,
            (tenant_id, task_id),
        )
        cur_input_version = int(cursor.fetchone()["cur"])
        cursor.execute(
            """
            SELECT action, status, input_version FROM session_task_decisions
            WHERE tenant_id=%s AND task_id=%s AND decision_kind='completion_review'
            ORDER BY updated_at DESC LIMIT 1
            """,
            (tenant_id, task_id),
        )
        review_row = cursor.fetchone()
        review_decision = (
            {
                "action": review_row["action"],
                "status": review_row["status"],
                # 审核版本已落后（对方反悔后有更新批次）→ 不得据以完成（supersede 兜底）
                "current": int(review_row["input_version"] or 0) == cur_input_version,
            }
            if review_row else None
        )
        cursor.execute(
            """
            SELECT COALESCE(
                (SELECT MAX(created_at) FROM session_task_batches
                 WHERE tenant_id=%s AND task_id=%s AND synthetic=FALSE AND status='accepted'),
                (SELECT MAX(created_at) FROM session_task_batches
                 WHERE tenant_id=%s AND task_id=%s AND status IN ('resume_baseline','resume_claimed')),
                (SELECT published_at FROM session_task_specs WHERE tenant_id=%s AND task_id=%s AND id=%s)
            ) AS last_at
            """,
            (tenant_id, task_id, tenant_id, task_id, tenant_id, task_id, spec_id),
        )
        last_peer_activity = cursor.fetchone()["last_at"]
        cursor.execute(
            "SELECT COUNT(*) AS n FROM session_task_decisions WHERE tenant_id=%s AND task_id=%s AND status IN ('pending','running')",
            (tenant_id, task_id),
        )
        pending_decisions = int(cursor.fetchone()["n"])
        verdict = hooks.evaluate_completion(
            spec=spec, links=links, has_pending_sends=pending,
            latest_reply_evidence=latest_evidence, review_decision=review_decision,
            last_peer_activity_at=last_peer_activity, now=now,
            pending_decision_count=pending_decisions,
        )
        if verdict is None:
            conn.rollback()
            return False
        target = verdict["status"]
        reason = verdict["reason"]
        migrated = _apply_task_transition(conn, tenant_id, task_id, target, reason, expected_status=STATUS_ACTIVE)
        conn.commit()
        if migrated:
            logger.info("任务终态迁移 tenant=%s task=%s → %s(%s)", tenant_id, task_id, target, reason)
        return migrated
