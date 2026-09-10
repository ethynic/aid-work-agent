"""desktop_automation occurrences（触发接纳，【计划 §3.1】）

- 触发键（R11）：time:{revision_ref}:{ISO8601 UTC 秒} / event:{source_ref}:{b64url(sha256)} /
  manual:{b64url(sha256(request_id))}；UNIQUE(tenant_id,scenario_key,task_ref,trigger_key)，
  INSERT ... ON CONFLICT DO NOTHING 幂等，不依赖内存集合。
- 接纳事务锁顺序恒为 subject(task)→schedule→occurrence/run（R12）：扫描路径 task subject
  用 FOR UPDATE SKIP LOCKED（拿不到即跳过本轮），schedule/occurrence/run 阻塞 FOR UPDATE。
- 冲突复用已有 occurrence，不再生成 run/outbox 或重复计数。
- 时间扫描：迟于宽限的槽记 missed 审计；宽限内最多接纳最近一次；一次性 schedule 保存
  consumed 状态并保留行；skip_overlap 下新槽记 skipped 不生成可执行 run；interval 停机后
  直接算最近可用槽不展开积压。
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from src.db.database import get_db_connection
from src.desktop_automation import audit, outbox, runs, schedules, subjects
from src.desktop_automation.constants import (
    REVISION_STATUS_PUBLISHED,
    SCHEDULE_STATUS_ACTIVE,
    TASK_STATUS_ACTIVE,
    TRIGGER_KIND_EVENT,
    TRIGGER_KIND_MANUAL,
    TRIGGER_KIND_TIME,
    event_trigger_key,
    manual_trigger_key,
    time_trigger_key,
)

_OCCURRENCE_COLUMNS = """
    id, tenant_id, scenario_key, task_ref, revision_ref, user_id, trigger_kind, trigger_key,
    scheduled_for, due_at, expires_at, status, created_at
"""


def admit_occurrence(
    cursor,
    *,
    tenant_id: str,
    scenario_key: str,
    task_ref: str,
    revision_ref: str,
    user_id: str,
    trigger_kind: str,
    trigger_key: str,
    due_at: datetime,
    expires_at: Optional[datetime],
    scheduled_for: Optional[datetime] = None,
    authorization_epoch: Optional[int] = None,
) -> Tuple[Optional[str], bool]:
    """接纳 occurrence（ON CONFLICT DO NOTHING）：仅新接纳时创建 run + outbox。

    返回 (occurrence_id, created)；已存在时 created=False 复用已有行（不重复建 run/outbox）。
    """
    cursor.execute(
        """
        INSERT INTO desktop_automation_occurrences
            (tenant_id, scenario_key, task_ref, revision_ref, user_id, trigger_kind,
             trigger_key, scheduled_for, due_at, expires_at, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'open')
        ON CONFLICT (tenant_id, scenario_key, task_ref, trigger_key) DO NOTHING
        RETURNING id
        """,
        (
            tenant_id, scenario_key, task_ref, revision_ref, user_id, trigger_kind,
            trigger_key, scheduled_for, due_at, expires_at,
        ),
    )
    row = cursor.fetchone()
    if row is None:
        return None, False
    occurrence_id = str(row["id"])
    run_id = runs.create_run(
        cursor, tenant_id, occurrence_id, scenario_key, task_ref, revision_ref, user_id,
        due_at=due_at, expires_at=expires_at, authorization_epoch=authorization_epoch,
    )
    outbox.enqueue_outbox(
        cursor, tenant_id, "run_due", occurrence_id, outbox.run_due_dedupe_key(occurrence_id),
        user_id=user_id,
    )
    return occurrence_id, True


def _check_subjects_active(
    cursor, tenant_id: str, scenario_key: str, task_ref: str, revision_ref: str
) -> Tuple[bool, str]:
    """锁内复验（§3.1）：task 启用、active_revision_ref == schedule.revision_ref、
    revision subject 属于该 task 且已发布有效。"""
    cursor.execute(
        """
        SELECT status, active_revision_ref, authorization_epoch, owner_id
        FROM desktop_automation_subjects
        WHERE tenant_id = %s AND scenario_key = %s AND kind = 'task' AND ref = %s
        FOR UPDATE
        """,
        (tenant_id, scenario_key, task_ref),
    )
    task_row = cursor.fetchone()
    if task_row is None:
        return False, "task_subject_missing"
    if task_row["status"] != TASK_STATUS_ACTIVE:
        return False, "task_not_active"
    if task_row["active_revision_ref"] != revision_ref:
        return False, "revision_switched"
    cursor.execute(
        """
        SELECT status, task_ref FROM desktop_automation_subjects
        WHERE tenant_id = %s AND scenario_key = %s AND kind = 'revision' AND ref = %s
        """,
        (tenant_id, scenario_key, revision_ref),
    )
    revision_row = cursor.fetchone()
    if revision_row is None or revision_row["task_ref"] != task_ref:
        return False, "revision_subject_missing"
    if revision_row["status"] != REVISION_STATUS_PUBLISHED:
        return False, "revision_not_published"
    return True, ""


def _has_open_run(cursor, tenant_id: str, scenario_key: str, task_ref: str) -> bool:
    """skip_overlap 判定：同 task 存在未结束 run（通过 occurrences 关联）"""
    cursor.execute(
        """
        SELECT 1
        FROM desktop_automation_runs r
        JOIN desktop_automation_occurrences o ON o.id = r.occurrence_id AND o.tenant_id = r.tenant_id
        WHERE r.tenant_id = %s AND r.scenario_key = %s AND r.task_ref = %s
          AND r.state NOT IN %s
        LIMIT 1
        """,
        (tenant_id, scenario_key, task_ref, runs.RUN_TERMINAL_STATES),
    )
    return cursor.fetchone() is not None


def accept_time_slot(candidate: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    """单个到期候选的接纳短事务（§3.1 顺序：task subject SKIP LOCKED → schedule → occurrence/run）。

    返回 {accepted, created, occurrence_id, reason, missed_count}：
    - accepted=False 时 reason 说明（task_locked/task_not_active/missed/skipped_overlap/...）。
    """
    now = schedules.ensure_utc(now)
    tenant_id = candidate["tenant_id"]
    scenario_key = candidate["scenario_key"]
    task_ref = candidate["task_ref"]

    with get_db_connection() as conn:
        cursor = conn.cursor()
        # R12 第一环：task subject FOR UPDATE SKIP LOCKED——拿不到即跳过本轮
        task_row = subjects.lock_task_subject(
            cursor, tenant_id, scenario_key, task_ref, skip_locked=True
        )
        if task_row is None:
            conn.commit()
            return {"accepted": False, "created": False, "occurrence_id": None,
                    "reason": "task_locked", "missed_count": 0}

        # 第二环：schedule（阻塞 FOR UPDATE），复验到期与引用未变
        schedule_row = schedules.lock_schedule(cursor, str(candidate["id"]), tenant_id)
        if schedule_row is None or schedule_row["status"] != SCHEDULE_STATUS_ACTIVE \
                or schedule_row["consumed"] or schedule_row["next_fire_at"] is None \
                or schedule_row["next_fire_at"] > now:
            conn.commit()
            return {"accepted": False, "created": False, "occurrence_id": None,
                    "reason": "schedule_not_due", "missed_count": 0}
        revision_ref = schedule_row["revision_ref"]

        # task/revision 复验（task 行已持有锁，这里复验状态与 active revision）
        ok, reason = _check_subjects_active(cursor, tenant_id, scenario_key, task_ref, revision_ref)
        if not ok:
            conn.commit()
            return {"accepted": False, "created": False, "occurrence_id": None,
                    "reason": reason, "missed_count": 0}

        grace = timedelta(seconds=schedule_row["grace_seconds"] or 0)
        slot = schedules.ensure_utc(schedule_row["next_fire_at"])
        original_slot = slot
        user_id = schedule_row["user_id"]
        epoch = task_row["authorization_epoch"]

        # one-shot 迟到同样受 grace 判定（总工裁决：宽限内接纳；超宽限记 missed 并置
        # consumed 与 recurring 语义对齐，行保留对账）
        if schedule_row["one_shot"] and (now - slot) > grace:
            audit.insert_audit(
                cursor, tenant_id, "schedule_missed", "schedule", str(schedule_row["id"]),
                user_id=user_id, scenario_key=scenario_key,
                detail={
                    "first_missed_slot": original_slot.isoformat(), "now": now.isoformat(),
                    "missed_count": 1, "reason": "one_shot_past_grace",
                },
            )
            _advance_schedule(cursor, schedule_row, now, accepted_slot=slot)  # 置 consumed
            conn.commit()
            return {"accepted": False, "created": False, "occurrence_id": None,
                    "reason": "missed", "missed_count": 1}

        # 迟到处理：停机后计算「<= now 的最近槽」，旧槽 missed；
        # 宽限内最多接纳最近一次（interval 直接算最近槽不展开积压，cron 有界逐槽前移）
        missed_count = _advance_due_slot(schedule_row, now)
        if missed_count:
            slot = schedules.ensure_utc(schedule_row["next_fire_at"])
        # 接纳条件：正常扫描（无错过）直接接纳；有错过时仅当最近槽仍在宽限内
        if missed_count and (now - slot) > grace:
            audit.insert_audit(
                cursor, tenant_id, "schedule_missed", "schedule", str(schedule_row["id"]),
                user_id=user_id, scenario_key=scenario_key,
                detail={
                    "first_missed_slot": original_slot.isoformat(),
                    "latest_slot": slot.isoformat(), "now": now.isoformat(),
                    "missed_count": missed_count, "reason": "past_grace",
                },
            )
            _advance_schedule(cursor, schedule_row, now, accepted_slot=slot)
            conn.commit()
            return {"accepted": False, "created": False, "occurrence_id": None,
                    "reason": "missed", "missed_count": missed_count}
        if missed_count:
            # 宽限内接纳最近一次：旧槽 missed 审计留痕
            audit.insert_audit(
                cursor, tenant_id, "schedule_missed", "schedule", str(schedule_row["id"]),
                user_id=user_id, scenario_key=scenario_key,
                detail={
                    "missed_count": missed_count, "accepted_slot": slot.isoformat(),
                    "first_due_slot": original_slot.isoformat(),
                },
            )

        # skip_overlap：同 task 有未结束 run → 新槽记 skipped，不生成可执行 run
        overlap_skipped = False
        if schedule_row["miss_policy"] != "catch_up_latest" and _has_open_run(
            cursor, tenant_id, scenario_key, task_ref
        ):
            overlap_skipped = True

        trigger_key = time_trigger_key(revision_ref, slot)
        if overlap_skipped:
            cursor.execute(
                """
                INSERT INTO desktop_automation_occurrences
                    (tenant_id, scenario_key, task_ref, revision_ref, user_id, trigger_kind,
                     trigger_key, scheduled_for, due_at, expires_at, status)
                VALUES (%s, %s, %s, %s, %s, 'time', %s, %s, %s, %s, 'skipped')
                ON CONFLICT (tenant_id, scenario_key, task_ref, trigger_key) DO NOTHING
                """,
                (tenant_id, scenario_key, task_ref, revision_ref, user_id,
                 trigger_key, slot, slot, schedule_row.get("expires_at")),
            )
            audit.insert_audit(
                cursor, tenant_id, "occurrence_skipped", "schedule", str(schedule_row["id"]),
                user_id=user_id, scenario_key=scenario_key,
                detail={"trigger_key": trigger_key, "reason": "skip_overlap"},
            )
            _advance_schedule(cursor, schedule_row, now, accepted_slot=slot)
            conn.commit()
            return {"accepted": True, "created": False, "occurrence_id": None,
                    "reason": "skipped_overlap", "missed_count": missed_count}

        occurrence_id, created = admit_occurrence(
            cursor,
            tenant_id=tenant_id, scenario_key=scenario_key, task_ref=task_ref,
            revision_ref=revision_ref, user_id=user_id,
            trigger_kind=TRIGGER_KIND_TIME, trigger_key=trigger_key,
            due_at=slot, expires_at=schedule_row.get("expires_at"),
            scheduled_for=slot, authorization_epoch=epoch,
        )
        _advance_schedule(cursor, schedule_row, now, accepted_slot=slot)
        conn.commit()
    logger.info(
        f"后端日志：desktop_automation 时间槽接纳 tenant={tenant_id} task={task_ref} "
        f"slot={slot.isoformat()} created={created} missed={missed_count}"
    )
    return {"accepted": True, "created": created, "occurrence_id": occurrence_id,
            "reason": "" if created else "duplicate", "missed_count": missed_count}


def _anchor_of(schedule_row: Dict[str, Any]) -> datetime:
    return schedules.ensure_utc(schedule_row["anchor_at"] or schedule_row["next_fire_at"])


def _advance_due_slot(schedule_row: Dict[str, Any], now: datetime) -> int:
    """停机后把内存中的到期槽前移到「<= now 的最近槽」，返回越过的槽数（missed 计数）。

    - interval：直接算最近网格槽，不循环展开积压（【计划 §3.1】）；
    - cron：有界逐槽前移（上限 1000 槽防御性封顶）；
    - one-shot / 无重复：只有唯一槽，不前移（正常扫描即接纳）。
    只改内存 dict（DB 前移由 _advance_schedule 在接纳/错过路径统一落库）。
    """
    slot = schedules.ensure_utc(schedule_row["next_fire_at"])
    if schedule_row["interval_seconds"]:
        anchor = _anchor_of(schedule_row)
        latest = schedules.latest_slot_at_or_before(
            anchor, schedule_row["interval_seconds"], now
        )
        if latest is None or latest <= slot:
            return 0
        missed = schedules.slots_between(
            anchor, schedule_row["interval_seconds"], slot, latest
        )
        schedule_row["next_fire_at"] = latest
        return missed
    if schedule_row["one_shot"] or not (
        schedule_row.get("cron_expr") or schedule_row.get("day_of_week") is not None
    ):
        return 0
    spec = dict(schedule_row)
    missed = 0
    cursor_slot = slot
    for _ in range(1000):
        nxt = schedules.next_cron_fire(spec, cursor_slot, strictly_after=True)
        if nxt is None or nxt > now:
            break
        missed += 1
        cursor_slot = nxt
    if missed:
        schedule_row["next_fire_at"] = cursor_slot
    return missed


def _advance_schedule(
    cursor, schedule_row: Dict[str, Any], now: datetime, *, accepted_slot: Optional[datetime] = None
) -> None:
    """前移 next_fire_at、更新计数；一次性 schedule 保存 consumed 状态并保留行（§3.1）"""
    schedule_id = str(schedule_row["id"])
    if schedule_row["one_shot"]:
        cursor.execute(
            """
            UPDATE desktop_automation_schedules
            SET consumed = TRUE, run_count = run_count + 1, updated_at = NOW()
            WHERE id = %s AND tenant_id = %s
            """,
            (schedule_id, schedule_row["tenant_id"]),
        )
        return
    spec = dict(schedule_row)
    if schedule_row["interval_seconds"]:
        anchor = _anchor_of(schedule_row)
        nxt = schedules.next_slot_strictly_after(
            anchor, schedule_row["interval_seconds"],
            accepted_slot or schedules.ensure_utc(schedule_row["next_fire_at"]),
        )
        # 停机后不积压：若仍早于 now，直接跳最近可用槽
        if nxt < now:
            nxt = schedules.next_slot_on_or_after(anchor, schedule_row["interval_seconds"], now)
    else:
        # cron：以被接纳槽为起点严格向后取下一槽（与 interval 对称）；
        # 若仍早于 now（防御），补位到 >= now 的最近 cron 槽不积压
        nxt = schedules.next_cron_fire(
            spec, accepted_slot or schedules.ensure_utc(schedule_row["next_fire_at"]),
            strictly_after=True,
        )
        if nxt is None:
            schedules.mark_schedule_finished(
                cursor, schedule_id, schedule_row["tenant_id"], "no_next_fire"
            )
            return
        if nxt < now:
            nxt = schedules.next_cron_fire(spec, now, strictly_after=False)
    ends_at = schedule_row.get("ends_at")
    if ends_at is not None and nxt > schedules.ensure_utc(ends_at):
        schedules.mark_schedule_finished(cursor, schedule_id, schedule_row["tenant_id"], "ends_at")
        return
    max_count = schedule_row.get("max_count")
    run_count = (schedule_row.get("run_count") or 0) + 1
    if max_count is not None and run_count >= max_count:
        cursor.execute(
            """
            UPDATE desktop_automation_schedules
            SET next_fire_at = %s, run_count = %s, status = 'finished', updated_at = NOW()
            WHERE id = %s AND tenant_id = %s
            """,
            (nxt, run_count, schedule_id, schedule_row["tenant_id"]),
        )
        return
    cursor.execute(
        """
        UPDATE desktop_automation_schedules
        SET next_fire_at = %s, run_count = %s, updated_at = NOW()
        WHERE id = %s AND tenant_id = %s
        """,
        (nxt, run_count, schedule_id, schedule_row["tenant_id"]),
    )


def scan_and_accept_time_slots(now: datetime, limit: int = 100) -> List[Dict[str, Any]]:
    """时间扫描 tick（库函数，假时钟可测）：非锁定读候选 → 逐候选短事务接纳"""
    candidates = schedules.find_due_candidates(now, limit=limit)
    results = []
    for candidate in candidates:
        results.append(accept_time_slot(candidate, now))
    return results


def accept_manual_trigger(
    *,
    tenant_id: str,
    scenario_key: str,
    task_ref: str,
    request_id: str,
    user_id: str,
    now: datetime,
    expires_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """手动触发：manual:{b64url(sha256(request_id))}，不改时间 schedule.next_fire_at；
    使用 task 当前 active revision（锁 task subject 阻塞 FOR UPDATE，短事务）。"""
    now = schedules.ensure_utc(now)
    trigger_key = manual_trigger_key(request_id)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        task_row = subjects.lock_task_subject(
            cursor, tenant_id, scenario_key, task_ref, skip_locked=False
        )
        if task_row is None or task_row["status"] != TASK_STATUS_ACTIVE:
            conn.commit()
            return {"accepted": False, "created": False, "occurrence_id": None,
                    "reason": "task_not_active"}
        revision_ref = task_row["active_revision_ref"]
        occurrence_id, created = admit_occurrence(
            cursor,
            tenant_id=tenant_id, scenario_key=scenario_key, task_ref=task_ref,
            revision_ref=revision_ref, user_id=user_id,
            trigger_kind=TRIGGER_KIND_MANUAL, trigger_key=trigger_key,
            due_at=now, expires_at=expires_at, scheduled_for=now,
            authorization_epoch=task_row["authorization_epoch"],
        )
        if not created:
            existing = get_occurrence_by_trigger_key_on(
                cursor, tenant_id, scenario_key, task_ref, trigger_key
            )
            occurrence_id = str(existing["id"]) if existing else None
        conn.commit()
    return {"accepted": True, "created": created, "occurrence_id": occurrence_id,
            "reason": "" if created else "duplicate"}


def accept_event_trigger(
    *,
    tenant_id: str,
    scenario_key: str,
    task_ref: str,
    revision_ref: str,
    source_ref: str,
    external_event_id: str,
    user_id: str,
    due_at: datetime,
    now: datetime,
    expires_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """事件触发接纳（匹配 worker 命中后调用；锁顺序同 R12：task→schedule→occurrence/run）。

    事件订阅行锁：kind=event schedule（source_ref/event_type 匹配）阻塞 FOR UPDATE 后
    复验 active_revision_ref 未变化。"""
    now = schedules.ensure_utc(now)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        result = accept_event_trigger_on(
            cursor,
            tenant_id=tenant_id, scenario_key=scenario_key, task_ref=task_ref,
            revision_ref=revision_ref, source_ref=source_ref,
            external_event_id=external_event_id, user_id=user_id,
            due_at=due_at, now=now, expires_at=expires_at,
        )
        conn.commit()
        return result


def accept_event_trigger_on(
    cursor,
    *,
    tenant_id: str,
    scenario_key: str,
    task_ref: str,
    revision_ref: str,
    source_ref: str,
    external_event_id: str,
    user_id: str,
    due_at: datetime,
    now: datetime,
    expires_at: Optional[datetime] = None,
    schedule_id: Optional[str] = None,
) -> Dict[str, Any]:
    """accept_event_trigger 的同事务版本（P4-B 匹配 worker：候选锁与游标推进同事务，
    锁顺序 task subject→schedule→occurrence/run；调用方负责 commit/rollback）。

    schedule_id 提供时定向锁定该订阅行（eligible 快照内的 schedule_id），复验
    kind=event 且 active；缺省沿用既有语义（该 task+revision 任一 active 订阅行）。
    """
    now = schedules.ensure_utc(now)
    trigger_key = event_trigger_key(source_ref, external_event_id)
    task_row = subjects.lock_task_subject(
        cursor, tenant_id, scenario_key, task_ref, skip_locked=True
    )
    if task_row is None:
        # P4-B 复审 P0-1：区分「行不存在」（业务性 skip，匹配 worker 记持久原因）
        # 与「SKIP LOCKED 竞争」（瞬时，可重试）——二者此前共用 task_locked 会让
        # 已删除 subject 的事件永远重试（活锁）或让竞争被误记永久 skip（丢事件）
        cursor.execute(
            """
            SELECT 1 FROM desktop_automation_subjects
            WHERE tenant_id = %s AND scenario_key = %s AND kind = 'task' AND ref = %s
            """,
            (tenant_id, scenario_key, task_ref),
        )
        if cursor.fetchone() is None:
            return {"accepted": False, "created": False, "occurrence_id": None,
                    "reason": "task_subject_missing"}
        return {"accepted": False, "created": False, "occurrence_id": None,
                "reason": "task_locked"}
    if schedule_id is not None:
        cursor.execute(
            """
            SELECT id, user_id FROM desktop_automation_schedules
            WHERE id = %s AND tenant_id = %s AND scenario_key = %s AND task_ref = %s
              AND revision_ref = %s AND kind = 'event' AND status = 'active'
            FOR UPDATE
            """,
            (schedule_id, tenant_id, scenario_key, task_ref, revision_ref),
        )
        schedule_row = cursor.fetchone()
    else:
        cursor.execute(
            """
            SELECT id, user_id FROM desktop_automation_schedules
            WHERE tenant_id = %s AND scenario_key = %s AND task_ref = %s
              AND revision_ref = %s AND kind = 'event' AND status = 'active'
            ORDER BY created_at
            LIMIT 1
            FOR UPDATE
            """,
            (tenant_id, scenario_key, task_ref, revision_ref),
        )
        schedule_row = cursor.fetchone()
    if schedule_row is None:
        return {"accepted": False, "created": False, "occurrence_id": None,
                "reason": "subscription_inactive"}
    if not user_id:
        user_id = schedule_row["user_id"]
    occurrence_id, created = admit_occurrence(
        cursor,
        tenant_id=tenant_id, scenario_key=scenario_key, task_ref=task_ref,
        revision_ref=revision_ref, user_id=user_id,
        trigger_kind=TRIGGER_KIND_EVENT, trigger_key=trigger_key,
        due_at=due_at, expires_at=expires_at, scheduled_for=now,
        authorization_epoch=task_row["authorization_epoch"],
    )
    if not created:
        existing = get_occurrence_by_trigger_key_on(
            cursor, tenant_id, scenario_key, task_ref, trigger_key
        )
        occurrence_id = str(existing["id"]) if existing else None
    return {"accepted": True, "created": created, "occurrence_id": occurrence_id,
            "reason": "" if created else "duplicate"}


def get_occurrence(occurrence_id: str, tenant_id: str) -> Optional[Dict[str, Any]]:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT {_OCCURRENCE_COLUMNS} FROM desktop_automation_occurrences "
            "WHERE id = %s AND tenant_id = %s",
            (occurrence_id, tenant_id),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def get_occurrence_by_trigger_key(
    tenant_id: str, scenario_key: str, task_ref: str, trigger_key: str
) -> Optional[Dict[str, Any]]:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        return get_occurrence_by_trigger_key_on(
            cursor, tenant_id, scenario_key, task_ref, trigger_key
        )


def get_occurrence_by_trigger_key_on(
    cursor, tenant_id: str, scenario_key: str, task_ref: str, trigger_key: str
) -> Optional[Dict[str, Any]]:
    """按触发键查 occurrence（P1-6：接受既有事务游标，供持锁事务内读取）"""
    cursor.execute(
        f"SELECT {_OCCURRENCE_COLUMNS} FROM desktop_automation_occurrences "
        "WHERE tenant_id = %s AND scenario_key = %s AND task_ref = %s AND trigger_key = %s",
        (tenant_id, scenario_key, task_ref, trigger_key),
    )
    row = cursor.fetchone()
    return dict(row) if row else None
