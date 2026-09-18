"""session_task_control_requests：human_required 异步控制迁移（B1.2，设计 §5.5.5）。

职责切分（设计 §5.5.5）：控制请求行只负责**异步迁移**，不是同步发送门禁；
同步阻断由 write-authorize / prepare-send Phase A 在获锁后检查 pending 请求
（has_pending_control_request）+ binding_guard 的 automation_blocked 硬门禁
共同构成——仅查询本表不构成同步屏障。

处理器（scheduler manager 5s tick，受 session_tasks.enabled 门控）：
- 认领：FOR UPDATE SKIP LOCKED 认领 pending 或处理租约已过期的 processing，
  写 processing_owner/processing_lease_expires_at；
- 校验 expected epochs：control_epoch 不符 → stale；binding block epoch 不符
  → stale（不得覆盖较新的阻断）；
- 成功 → applied（执行统一 human_required 迁移函数 = decisions._apply_task_transition，
  Phase A terminal / permits 拒绝 / 本处理器共用）；
- 暂时失败 → 退避重试，MAX_RETRIES（10）次后 → failed + 脱敏审计告警；
- processing/failed 均保持 binding 的 automation_blocked=true——本模块不含任何
  清除阻断的路径（人工解阻 owner-only unblock 属 B4）。

锁序（设计 §5.5.5 矩阵）：认领事务只锁请求行；迁移事务按 subject → task →
（guard 时）binding 获取，不持有 delivery/rate slot 锁。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from loguru import logger

from .constants import STATUS_HUMAN_REQUIRED

PROCESSING_LEASE_SECONDS = 30  # 认领租约：过期后其他实例可重新认领（崩溃恢复）
MAX_RETRIES = 10               # 暂时失败重试上限：达到后 failed + 站内告警
RETRY_BACKOFF_BASE_SECONDS = 5  # 退避基数：5 * 2^retry_count，上限 300s
RETRY_BACKOFF_MAX_SECONDS = 300


def has_pending_control_request(cursor, tenant_id: str, task_id) -> bool:  # noqa: ANN001
    """同步阻断检查（write-authorize / prepare-send Phase A 获锁后调用）。

    仅 pending/processing 请求构成阻断（applied/stale/failed 不阻断）。微信任务
    永远无控制请求行——本查询为走 (tenant_id, task_id) 唯一前缀的廉价空查询。
    task_id 非 UUID（纯底座场景的 task_ref，如 fake-scenario 的 "task-1"）时
    直接返回 False：控制请求行只可能属于 session_tasks（UUID 主键），不得让
    非 UUID task_ref 打到 UUID 列（InvalidTextRepresentation）。
    """
    task_uuid = _as_uuid_str(task_id)
    if task_uuid is None:
        return False
    cursor.execute(
        """
        SELECT 1 FROM session_task_control_requests
        WHERE tenant_id = %s AND task_id = %s AND status IN ('pending', 'processing')
        LIMIT 1
        """,
        (tenant_id, task_uuid),
    )
    return cursor.fetchone() is not None


def _as_uuid_str(value) -> Optional[str]:  # noqa: ANN001
    """task_id 规范化为 UUID 字符串；非 UUID（纯底座场景 task_ref）返回 None。"""
    import uuid as _uuid

    try:
        return str(_uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return None


def insert_control_request(
    cursor,
    tenant_id: str,
    task_id,
    *,
    expected_control_epoch: int,
    expected_block_epoch: int,
    reason: str,
    source_type: str,
    source_ref: str,
) -> bool:  # noqa: ANN001
    """幂等写入控制请求（唯一键 tenant+task+expected_control_epoch+
    expected_block_epoch+reason；CR 三审 P1-1：block epoch 纳入幂等键——同代
    同原因第二次异常以新 block epoch 成行，不被唯一键吞掉）。

    返回是否由本次写入新行（False=已存在，幂等放行）。调用方事务负责提交——
    permits 拒绝副作用按设计"先 commit 再返回拒绝"。
    """
    cursor.execute(
        """
        INSERT INTO session_task_control_requests
            (tenant_id, task_id, expected_control_epoch, expected_block_epoch,
             reason, source_type, source_ref)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (tenant_id, task_id, expected_control_epoch, expected_block_epoch, reason) DO NOTHING
        RETURNING id
        """,
        (tenant_id, str(task_id), int(expected_control_epoch), int(expected_block_epoch),
         reason, source_type, source_ref),
    )
    return cursor.fetchone() is not None


def run_control_request_tick(*, limit: int = 20) -> Dict[str, Any]:
    """处理器 tick：认领 → 逐条迁移事务（stale/applied/retry/failed）→ 返回统计。

    每条请求独立事务：认领（短事务，租约防崩溃悬挂）与迁移（subject→task 锁序）
    分离，避免持请求行锁等待任务锁扩散；租约过期请求可被任意实例重新认领。
    """
    stats: Dict[str, Any] = {
        "claimed": 0, "applied": 0, "stale": 0, "retried": 0, "failed": 0, "lease_lost": 0,
    }
    rows = _claim_requests(limit)
    stats["claimed"] = len(rows)
    for row in rows:
        try:
            outcome = _process_one(row)
        except Exception as exc:  # noqa: BLE001 单条隔离：迁移异常收敛为 retry/failed
            logger.warning(
                f"控制请求处理异常 tenant={row['tenant_id']} request={row['id']}: {exc!r}"
            )
            outcome = _mark_retry(row)
        stats[outcome] = stats.get(outcome, 0) + 1
    return stats


def _claim_requests(limit: int) -> list:
    """认领 pending 或处理租约已过期的 processing（FOR UPDATE SKIP LOCKED + 条件 UPDATE）。"""
    from .service import _conn

    with _conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE session_task_control_requests
            SET status = 'processing',
                processing_owner = %s,
                processing_lease_expires_at = NOW() + (%s * INTERVAL '1 second'),
                updated_at = NOW()
            WHERE id IN (
                SELECT id FROM session_task_control_requests
                WHERE (status = 'pending'
                       OR (status = 'processing' AND processing_lease_expires_at < NOW()))
                  AND (next_retry_at IS NULL OR next_retry_at <= NOW())
                ORDER BY created_at, id
                LIMIT %s
                FOR UPDATE SKIP LOCKED
            )
            RETURNING id, tenant_id, task_id, expected_control_epoch, expected_block_epoch,
                      reason, source_type, source_ref, retry_count,
                      processing_owner
            """,
            (f"control-worker-{datetime.now(timezone.utc).timestamp()}", PROCESSING_LEASE_SECONDS, limit),
        )
        rows = [dict(r) for r in cursor.fetchall()]
        conn.commit()
    return rows


def _process_one(row: Dict[str, Any]) -> str:
    """单条请求迁移事务：epochs 校验 → stale / applied。返回结果类别。"""
    from .decisions import _apply_task_transition
    from .scenario_descriptor import get_descriptor
    from .service import _conn, _lock_task_subject

    tenant_id = row["tenant_id"]
    with _conn() as conn:
        cursor = conn.cursor()
        # 锁序（设计 §5.5.5 矩阵冻结：subject → task →（guard 时）binding）：
        # 先取 subject/task 行锁，guard 场景最后锁 binding——与 write-authorize
        # 拒绝副作用（subject→run→invocation→delivery→binding）和 prepare-send
        # Phase A（subject→task→assignment→decision→binding）同序。矩阵无死锁
        # 论证依赖"不存在 binding→subject 反向边"：若先锁 binding 再经迁移函数
        # 取 subject/task，会与上述路径形成 AB-BA 死锁（CR 修复）。
        _lock_task_subject(conn, tenant_id, row["task_id"])
        cursor.execute(
            "SELECT id, status, control_epoch, conversation_binding_id "
            "FROM session_tasks WHERE tenant_id=%s AND id=%s FOR UPDATE",
            (tenant_id, str(row["task_id"])),
        )
        task = cursor.fetchone()
        if task is None:
            # 任务已物理清理：请求无迁移对象，直接 stale（不覆盖任何状态）
            if not _finish(conn, cursor, row, "stale"):
                return "lease_lost"
            return "stale"
        if int(task["control_epoch"]) != int(row["expected_control_epoch"]):
            # 控制代已前进（用户暂停/恢复/重发布）：迁移已被更新的控制操作取代
            if not _finish(conn, cursor, row, "stale"):
                return "lease_lost"
            return "stale"
        if task["status"] == STATUS_HUMAN_REQUIRED:
            # 已是目标态（同代重复请求）：请求置 stale，不重复迁移、不重复通知
            if not _finish(conn, cursor, row, "stale"):
                return "lease_lost"
            return "stale"
        # binding block epoch 复验（guard 场景，锁序在 subject/task 之后）：
        # 较新的阻断不得被旧请求覆盖——旧 block epoch 的请求标 stale，
        # 新阻断自带新 epoch 的请求
        guard = None
        descriptor = get_descriptor(str(_task_scenario(cursor, tenant_id, row["task_id"]) or ""))
        if descriptor is not None:
            guard = getattr(descriptor, "binding_guard", None)
        if guard is not None:
            binding_row = None
            if task["conversation_binding_id"]:
                binding_row = guard.lock_binding(
                    cursor, tenant_id, str(task["conversation_binding_id"])
                )
            if binding_row is None or int(binding_row.get("automation_block_epoch") or 0) != int(
                row["expected_block_epoch"]
            ):
                if not _finish(conn, cursor, row, "stale"):
                    return "lease_lost"
                return "stale"
        # 统一迁移函数（subject→task→(guard)binding 锁序内）：任务 human_required +
        # epoch/seq 推进 + 授权撤销 + 去重通知；迁移失败抛异常 → 调用方 retry。
        # 返回 False（迁移函数内部已 rollback，未发生任何迁移）→ 不得标记 applied，
        # 按 retry 处理（CR 修复：原实现忽略返回值，可能误标 applied）。
        migrated = _apply_task_transition(
            conn, tenant_id, row["task_id"], STATUS_HUMAN_REQUIRED, row["reason"]
        )
        if not migrated:
            # CR 阻断 6：False（迁移函数内部已 rollback，未发生迁移）进入受租约
            # 保护的 retry 转换——retry_count/next_retry_at 真实推进，达到上限转
            # failed；停留 processing 原地打转的旧实现已废弃
            return _mark_retry(row)
        if not _finish(conn, cursor, row, "applied"):
            # 租约已丢失（过期后被其他实例重新认领）：不得写终态、不得提交本实例
            # 的未提交迁移（认领方会在自己的事务里按同一 epoch 校验重新迁移），
            # 整体回滚交由重领机制（B1.2 CR P2#2）。
            return "lease_lost"
        return "applied"


def _task_scenario(cursor, tenant_id: str, task_id) -> Optional[str]:  # noqa: ANN001
    cursor.execute(
        "SELECT scenario_key FROM session_tasks WHERE tenant_id=%s AND id=%s",
        (tenant_id, str(task_id)),
    )
    row = cursor.fetchone()
    return row["scenario_key"] if row else None


def _finish(conn, cursor, row: Dict[str, Any], status: str) -> bool:  # noqa: ANN001
    """终态落库（applied/stale）：以"仍 processing 且归本租约"为条件，同事务提交。

    租约归属守卫（B1.2 CR P2#2）：UPDATE 追加 processing_owner=本实例认领值 且
    processing_lease_expires_at>now()——处理期间租约已过期并被其他实例重新认领
    时，本实例不写终态（0 行更新 → rollback 丢弃同事务未提交迁移并返回 False），
    请求由认领方按重领机制完整重处理；两实例对同一请求的终态写入被租约互斥。
    """
    cursor.execute(
        """
        UPDATE session_task_control_requests
        SET status = %s, processing_owner = NULL, processing_lease_expires_at = NULL, updated_at = NOW()
        WHERE id = %s AND status = 'processing'
          AND processing_owner = %s AND processing_lease_expires_at > NOW()
        """,
        (status, row["id"], row.get("processing_owner")),
    )
    finished = cursor.rowcount == 1
    if finished:
        conn.commit()
    else:
        conn.rollback()
        logger.warning(
            f"控制请求终态放弃（租约已丢失，交由重领机制）tenant={row['tenant_id']} "
            f"request={row['id']} target_status={status}"
        )
    return finished


def _mark_retry(row: Dict[str, Any]) -> str:
    """暂时失败：退避重试；达到上限 → failed + 脱敏审计 + 站内告警（阻断 7）。

    CR 阻断 5（租约竞态）：retry/failed 转换同时校验 processing_owner=本实例
    认领值 且 processing_lease_expires_at>now()——旧 worker 在租约过期后到达时
    rowcount=0 → 返回 lease_lost：不增加 retry_count、不写 failed/告警，不得
    影响重领 worker 的处理。
    """
    from .service import _conn
    from src.desktop_automation import audit

    retry_count = int(row.get("retry_count") or 0) + 1
    with _conn() as conn:
        cursor = conn.cursor()
        if retry_count >= MAX_RETRIES:
            cursor.execute(
                """
                UPDATE session_task_control_requests
                SET status = 'failed', retry_count = %s, processing_owner = NULL,
                    processing_lease_expires_at = NULL, updated_at = NOW()
                WHERE id = %s AND status = 'processing'
                  AND processing_owner = %s AND processing_lease_expires_at > NOW()
                """,
                (retry_count, row["id"], row.get("processing_owner")),
            )
            if cursor.rowcount != 1:
                conn.rollback()
                return "lease_lost"  # 旧租约 worker：不写终态、不告警（阻断 5）
            audit.insert_audit(
                cursor, row["tenant_id"], "control_request_failed", "task", str(row["task_id"]),
                scenario_key=None,
                detail={
                    "request_id": str(row["id"]), "reason": row["reason"],
                    "source_type": row["source_type"], "retry_count": retry_count,
                },
            )
            # 站内告警（CR 阻断 7）：沿 /notifications 通用通知机制写入
            # session_task_notifications。控制 epoch 取请求的 expected_control_epoch
            # （请求创建时的任务代）——迁移成功后 human_required 正式通知落在
            # _apply_task_transition 的 current+1 ≥ expected+1，唯一键
            # (tenant,task,control_epoch) 天然不冲突；同代重复告警被幂等去重。
            record_failed_notice(
                conn, row["tenant_id"], str(row["task_id"]),
                f"控制迁移失败（{row['source_type']}），任务阻断保持，需人工处理",
                int(row["expected_control_epoch"]),
            )
            logger.error(
                "后端日志：控制请求迁移达重试上限转 failed "
                f"tenant={row['tenant_id']} task={row['task_id']} request={row['id']} "
                f"source_type={row['source_type']}（binding 阻断保持，需人工处理）"
            )
            conn.commit()
            return "failed"
        backoff = min(RETRY_BACKOFF_MAX_SECONDS, RETRY_BACKOFF_BASE_SECONDS * (2 ** retry_count))
        cursor.execute(
            """
            UPDATE session_task_control_requests
            SET status = 'pending', retry_count = %s,
                next_retry_at = NOW() + (%s * INTERVAL '1 second'),
                processing_owner = NULL, processing_lease_expires_at = NULL, updated_at = NOW()
            WHERE id = %s AND status = 'processing'
              AND processing_owner = %s AND processing_lease_expires_at > NOW()
            """,
            (retry_count, backoff, row["id"], row.get("processing_owner")),
        )
        if cursor.rowcount != 1:
            conn.rollback()
            return "lease_lost"  # 旧租约 worker：不增加次数（阻断 5）
        conn.commit()
    return "retried"


def record_failed_notice(conn, tenant_id: str, task_id: str, reason: str, control_epoch: int) -> None:
    """控制迁移失败站内告警（/notifications 数据源；CR 阻断 7）。

    status='failed' 加入通知白名单；与 human_required 正式通知的唯一键冲突
    分析见 _mark_retry 内注释。
    """
    from .notifications import record_notice

    record_notice(conn, tenant_id, task_id, "failed", reason, control_epoch)
