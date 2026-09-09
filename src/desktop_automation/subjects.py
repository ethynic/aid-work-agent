"""desktop_automation subjects（中立 subject registry，R5/R12）

多态 task_ref/revision_ref/target_ref 通过 subject registry 建立复合引用：
- kind=task：active_revision_ref + authorization_epoch（底座只持授权快照和撤销 epoch）
- kind=revision：task_ref 归属（发布不可变，切换版本时旧 revision 置 superseded）

锁顺序（R12）：发布/暂停先锁同一 task subject（阻塞 FOR UPDATE），同事务更新
active_revision_ref / status / authorization_epoch 与其 schedules 状态；扫描路径的
subject 锁用 SKIP LOCKED（见 occurrences.py），顺序恒为 subject(task)→schedule→occurrence/run。
"""

from typing import Any, Dict, List, Optional

from loguru import logger

from src.db.database import get_db_connection
from src.desktop_automation import audit
from src.desktop_automation.adapters import AdapterContext, TrustedAdapterRegistry
from src.desktop_automation.constants import (
    SCHEDULE_STATUS_ACTIVE,
    TASK_STATUS_ACTIVE,
    TASK_STATUS_PAUSED,
)

_SUBJECT_COLUMNS = """
    id, tenant_id, scenario_key, kind, ref, version, owner_id, status,
    active_revision_ref, authorization_epoch, task_ref, created_at, updated_at
"""


def lock_task_subject(cursor, tenant_id: str, scenario_key: str, task_ref: str, *, skip_locked: bool):
    """锁定 task subject（R12 锁顺序第一环）。skip_locked=True 为扫描路径（拿不到即跳过本轮），
    False 为发布/暂停路径（阻塞等待）。
    """
    hint = " SKIP LOCKED" if skip_locked else ""
    cursor.execute(
        f"""
        SELECT {_SUBJECT_COLUMNS} FROM desktop_automation_subjects
        WHERE tenant_id = %s AND scenario_key = %s AND kind = 'task' AND ref = %s
        FOR UPDATE{hint}
        """,
        (tenant_id, scenario_key, task_ref),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def get_task_subject(tenant_id: str, scenario_key: str, task_ref: str) -> Optional[Dict[str, Any]]:
    """非锁定读取 task subject（候选扫描线索用；不代表已取得执行权）"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT {_SUBJECT_COLUMNS} FROM desktop_automation_subjects
            WHERE tenant_id = %s AND scenario_key = %s AND kind = 'task' AND ref = %s
            """,
            (tenant_id, scenario_key, task_ref),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def get_revision_subject(
    tenant_id: str, scenario_key: str, revision_ref: str
) -> Optional[Dict[str, Any]]:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT {_SUBJECT_COLUMNS} FROM desktop_automation_subjects
            WHERE tenant_id = %s AND scenario_key = %s AND kind = 'revision' AND ref = %s
            """,
            (tenant_id, scenario_key, revision_ref),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def _upsert_task_subject(
    cursor,
    tenant_id: str,
    scenario_key: str,
    task_ref: str,
    owner_id: str,
    active_revision_ref: str,
    status: str,
) -> Dict[str, Any]:
    """task subject upsert（发布/恢复路径；ON CONFLICT 时 epoch+1 = 授权快照换新）"""
    cursor.execute(
        """
        INSERT INTO desktop_automation_subjects
            (tenant_id, scenario_key, kind, ref, owner_id, status,
             active_revision_ref, authorization_epoch)
        VALUES (%s, %s, 'task', %s, %s, %s, %s, 0)
        ON CONFLICT (tenant_id, scenario_key, kind, ref) DO UPDATE
        SET active_revision_ref = EXCLUDED.active_revision_ref,
            status = EXCLUDED.status,
            authorization_epoch = desktop_automation_subjects.authorization_epoch + 1,
            updated_at = NOW()
        RETURNING authorization_epoch
        """,
        (tenant_id, scenario_key, task_ref, owner_id, status, active_revision_ref),
    )
    return dict(cursor.fetchone())


def _upsert_revision_subject(
    cursor, tenant_id: str, scenario_key: str, task_ref: str, revision_ref: str, owner_id: str
) -> None:
    cursor.execute(
        """
        INSERT INTO desktop_automation_subjects
            (tenant_id, scenario_key, kind, ref, owner_id, status, task_ref)
        VALUES (%s, %s, 'revision', %s, %s, 'published', %s)
        ON CONFLICT (tenant_id, scenario_key, kind, ref) DO UPDATE
        SET status = 'published', task_ref = EXCLUDED.task_ref, updated_at = NOW()
        """,
        (tenant_id, scenario_key, revision_ref, owner_id, task_ref),
    )


def publish_revision(
    tenant_id: str,
    scenario_key: str,
    task_ref: str,
    revision_ref: str,
    owner_id: str,
    revision_config: Dict[str, Any],
    *,
    schedule_specs: Optional[List[Dict[str, Any]]] = None,
    adapter: Optional[Any] = None,
) -> Dict[str, Any]:
    """发布事务（R12）：锁 task subject → 更新 active_revision_ref/status/epoch →
    写 revision subject（旧 active revision 置 superseded）→ 重建该 revision 的 schedules。

    schedule_specs 缺省时取适配器 validate_revision 冻结结果。返回 {authorization_epoch, ...}。
    """
    adapter = adapter or TrustedAdapterRegistry.require(scenario_key)
    ctx = AdapterContext(
        tenant_id=tenant_id, user_id=owner_id, scenario_key=scenario_key,
        task_ref=task_ref, revision_ref=revision_ref,
    )
    validation = adapter.validate_revision(ctx, revision_config)
    if not validation.ok:
        raise ValueError(f"revision 校验失败: {validation.reason}")
    specs = schedule_specs if schedule_specs is not None else validation.schedule_specs

    with get_db_connection() as conn:
        cursor = conn.cursor()
        # R12：先锁同一 task subject（阻塞；扫描路径 SKIP LOCKED 不会与之死锁）
        existing = lock_task_subject(cursor, tenant_id, scenario_key, task_ref, skip_locked=False)
        prev_revision = existing["active_revision_ref"] if existing else None

        row = _upsert_task_subject(
            cursor, tenant_id, scenario_key, task_ref, owner_id,
            active_revision_ref=revision_ref, status=TASK_STATUS_ACTIVE,
        )
        epoch = row["authorization_epoch"]
        _upsert_revision_subject(cursor, tenant_id, scenario_key, task_ref, revision_ref, owner_id)

        # 旧 active revision 置 superseded；旧 revision 的 schedules 一律暂停（版本切换不回放历史事件）
        if prev_revision and prev_revision != revision_ref:
            cursor.execute(
                """
                UPDATE desktop_automation_subjects
                SET status = 'superseded', updated_at = NOW()
                WHERE tenant_id = %s AND scenario_key = %s AND kind = 'revision' AND ref = %s
                """,
                (tenant_id, scenario_key, prev_revision),
            )
            cursor.execute(
                """
                UPDATE desktop_automation_schedules
                SET status = 'paused', updated_at = NOW()
                WHERE tenant_id = %s AND scenario_key = %s AND task_ref = %s AND revision_ref = %s
                """,
                (tenant_id, scenario_key, task_ref, prev_revision),
            )
        # 非 active revision 的 schedules 同样不得保持 active（防御：历史残留；
        # 必须限定 task_ref——同租户同场景其他 task 的 schedules 不受本次发布影响）
        cursor.execute(
            """
            UPDATE desktop_automation_schedules
            SET status = 'paused', updated_at = NOW()
            WHERE tenant_id = %s AND scenario_key = %s AND task_ref = %s AND revision_ref <> %s
              AND status = 'active'
            """,
            (tenant_id, scenario_key, task_ref, revision_ref),
        )

        # 按 spec 重建（同 trigger_key 冲突 → 拒绝发布：配置非法）
        for spec in specs or []:
            _insert_schedule(cursor, tenant_id, scenario_key, task_ref, revision_ref, owner_id, spec)

        audit.insert_audit(
            cursor, tenant_id, "revision_published", "task_subject", task_ref,
            user_id=owner_id, scenario_key=scenario_key,
            detail={
                "revision_ref": revision_ref,
                "prev_revision_ref": prev_revision,
                "authorization_epoch": epoch,
                "schedule_count": len(specs or []),
            },
        )
        conn.commit()
    logger.info(
        f"后端日志：desktop_automation 发布 revision tenant={tenant_id} scenario={scenario_key} "
        f"task={task_ref} revision={revision_ref} epoch={epoch}"
    )
    return {"authorization_epoch": epoch, "revision_ref": revision_ref}


def pause_task(tenant_id: str, scenario_key: str, task_ref: str) -> Optional[int]:
    """暂停事务：锁 task subject → status=paused + epoch+1（授权撤销）→ schedules 暂停。

    暂停后许可事务按 epoch 拒绝新许可（许可绑定发布时快照 epoch）。
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        existing = lock_task_subject(cursor, tenant_id, scenario_key, task_ref, skip_locked=False)
        if existing is None or existing["status"] != TASK_STATUS_ACTIVE:
            conn.commit()
            return None
        cursor.execute(
            """
            UPDATE desktop_automation_subjects
            SET status = 'paused', authorization_epoch = authorization_epoch + 1, updated_at = NOW()
            WHERE tenant_id = %s AND scenario_key = %s AND kind = 'task' AND ref = %s
            RETURNING authorization_epoch, owner_id
            """,
            (tenant_id, scenario_key, task_ref),
        )
        row = cursor.fetchone()
        cursor.execute(
            """
            UPDATE desktop_automation_schedules
            SET status = 'paused', updated_at = NOW()
            WHERE tenant_id = %s AND scenario_key = %s AND task_ref = %s AND status = 'active'
            """,
            (tenant_id, scenario_key, task_ref),
        )
        audit.insert_audit(
            cursor, tenant_id, "task_paused", "task_subject", task_ref,
            user_id=dict(row).get("owner_id") if row else None,
            scenario_key=scenario_key,
            detail={"authorization_epoch": row["authorization_epoch"] if row else None},
        )
        epoch = row["authorization_epoch"] if row else None
        conn.commit()
    logger.info(
        f"后端日志：desktop_automation 暂停 task tenant={tenant_id} scenario={scenario_key} "
        f"task={task_ref} epoch={epoch}"
    )
    return epoch


def resume_task(tenant_id: str, scenario_key: str, task_ref: str) -> Optional[int]:
    """恢复：锁 task subject → status=active + epoch+1 → 该 active revision 的 schedules 恢复"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        existing = lock_task_subject(cursor, tenant_id, scenario_key, task_ref, skip_locked=False)
        if existing is None or existing["status"] != TASK_STATUS_PAUSED:
            conn.commit()
            return None
        revision_ref = existing["active_revision_ref"]
        cursor.execute(
            """
            UPDATE desktop_automation_subjects
            SET status = 'active', authorization_epoch = authorization_epoch + 1, updated_at = NOW()
            WHERE tenant_id = %s AND scenario_key = %s AND kind = 'task' AND ref = %s
            RETURNING authorization_epoch, owner_id
            """,
            (tenant_id, scenario_key, task_ref),
        )
        row = cursor.fetchone()
        cursor.execute(
            """
            UPDATE desktop_automation_schedules
            SET status = 'active', updated_at = NOW()
            WHERE tenant_id = %s AND scenario_key = %s AND task_ref = %s
              AND revision_ref = %s AND status = 'paused'
            """,
            (tenant_id, scenario_key, task_ref, revision_ref),
        )
        audit.insert_audit(
            cursor, tenant_id, "task_resumed", "task_subject", task_ref,
            user_id=dict(row).get("owner_id") if row else None,
            scenario_key=scenario_key,
            detail={"authorization_epoch": row["authorization_epoch"] if row else None},
        )
        epoch = row["authorization_epoch"] if row else None
        conn.commit()
    return epoch


def _insert_schedule(
    cursor,
    tenant_id: str,
    scenario_key: str,
    task_ref: str,
    revision_ref: str,
    owner_id: str,
    spec: Dict[str, Any],
) -> None:
    """按冻结 spec 建 schedule 行（发布事务内；同 trigger_key 已存在即拒绝）"""
    from src.desktop_automation import schedules as schedules_module

    kind = spec.get("kind", "time")
    trigger_key = spec.get("trigger_key") or schedules_module.default_trigger_key(kind, spec)
    next_fire_at = schedules_module.initial_next_fire_at(spec)
    columns = [
        "tenant_id", "scenario_key", "task_ref", "revision_ref", "user_id",
        "kind", "trigger_key", "timezone", "anchor_at", "interval_seconds", "cron_expr",
        "day_of_week", "next_fire_at", "ends_at", "max_count", "grace_seconds",
        "miss_policy", "one_shot", "source_ref", "event_type", "condition_ref",
        "delay_seconds", "status",
    ]
    values = [
        tenant_id, scenario_key, task_ref, revision_ref, owner_id,
        kind, trigger_key, spec.get("timezone"), spec.get("anchor_at"),
        spec.get("interval_seconds"), spec.get("cron_expr"), spec.get("day_of_week"),
        next_fire_at, spec.get("ends_at"), spec.get("max_count"), spec.get("grace_seconds", 0),
        spec.get("miss_policy", "skip_overlap"), bool(spec.get("one_shot", False)),
        spec.get("source_ref"), spec.get("event_type"), spec.get("condition_ref"),
        spec.get("delay_seconds"), SCHEDULE_STATUS_ACTIVE,
    ]
    placeholders = ", ".join(["%s"] * len(columns))
    cursor.execute(
        f"""
        INSERT INTO desktop_automation_schedules ({", ".join(columns)})
        VALUES ({placeholders})
        ON CONFLICT (tenant_id, scenario_key, revision_ref, trigger_key) DO UPDATE
        SET status = 'active', next_fire_at = EXCLUDED.next_fire_at, updated_at = NOW()
        """,
        tuple(values),
    )
