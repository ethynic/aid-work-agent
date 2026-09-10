"""weixin_marketing 调度闭环 tick（R44：与 APScheduler 解耦的库函数）

四类 tick（APScheduler 注册接线在 src/scheduler/manager.py 增量段，本模块不感知
调度器，可直接注入 now/batch/config 调用测试）：
- time_scan_tick：到期时间槽扫描 → occurrences.accept_time_slot 短事务接纳
  （scenario + tenant_allowlist 过滤已下推进底座候选 SQL，R53；once 只一次、
  interval 重启不积压由底座保证）。
- run_dispatch_tick：outbox worker（run_due 条目 lease/attempt_count）→ 领取 pending
  run（claim_and_prepare_run）→ 循环 execute_next_delivery 至需要设备/终态；
  另含在途 run 驱动段（上一条 verified 后派发下一条）与租约续租（invocation
  存活期内），单 tick 有界批量、单条异常隔离。
- permits_sweep_tick：expire_permits（R22：issued→expired 只置状态不释放预留）。
- runs_reclaim_tick：租约过期 running run 按 §5.4 收敛（may_have_started/unknown
  条目置 unknown 不重派；未提交条目 expired；空 deliveries 按未完成失败收敛）。
- assets_cleanup_tick：过期无引用素材硬删（P4-A R57：retention_until 已过且无
  draft/published 引用；行+文件，引用复核与删除同事务）。

入口统一门控（R42）：enabled=false 直接 return（零 DB 动作）；time_scan 额外受
time_triggers_enabled 细分开关。registration.ensure_registered 每 tick 幂等调用
（进程内单次注册 + registry 存活性复核，配置翻热后自愈）。
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from loguru import logger

from src.db.database import get_db_connection
from src.desktop_automation import audit as da_audit
from src.desktop_automation import events as da_events
from src.desktop_automation import deliveries as da_deliveries
from src.desktop_automation import executor as da_executor
from src.desktop_automation import occurrences as da_occurrences
from src.desktop_automation import outbox as da_outbox
from src.desktop_automation import runs as da_runs
from src.desktop_automation import schedules as da_schedules
from src.local_tools import permits as lt_permits
from src.local_tools import repository as lt_repository
from src.weixin_marketing.config import (
    WeixinMarketingConfig,
    get_weixin_marketing_config,
    tenant_allowed,
)
from src.weixin_marketing.constants import SCENARIO_KEY

# run 租约（领取/续租同值）：调度器存活期间由 run_dispatch_tick 周期续租；
# 调度器死亡后租约到期，由 runs_reclaim_tick 收敛（不自动重派）
RUN_LEASE_SECONDS = 300
# outbox 条目领取租约与重投宽限：条目驱动为秒级有界操作，短租约让「因领取顺序
# 延后处理」的条目更快回到 pending 重试
OUTBOX_LEASE_SECONDS = 120
OUTBOX_REQUEUE_GRACE_SECONDS = 60
# outbox 条目毒丸上限：超过后标记 done 停止无限重试（错误日志留痕，行保留对账）
OUTBOX_MAX_ATTEMPTS = 10
# 时间扫描单页候选上限（scenario/tenant_allowlist 过滤在调用侧）
DEFAULT_SCAN_BATCH = 100
# 租约回收单批 run 数
DEFAULT_RECLAIM_BATCH = 100
# 单 run 单 tick 内最大连续派发步数（正常至多 1 步：派发后在途即停；防御性封顶）
MAX_STEPS_PER_RUN = 64

_RUN_COLUMNS = (
    "id, tenant_id, occurrence_id, scenario_key, task_ref, revision_ref, user_id, "
    "state, device_id, lease_expires_at, fence_token, authorization_epoch, "
    "due_at, expires_at, claimed_at, created_at"
)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _resolve_config(config: Optional[WeixinMarketingConfig]) -> WeixinMarketingConfig:
    return config if config is not None else get_weixin_marketing_config()


# ==================== ① 时间扫描 tick ====================


def time_scan_tick(
    now: Optional[datetime] = None,
    batch: Optional[int] = None,
    config: Optional[WeixinMarketingConfig] = None,
) -> Dict[str, Any]:
    """时间触发扫描：候选非锁定读 → 逐条短事务接纳。

    R53 过滤下推：scenario_key 与 tenant_allowlist 在 find_due_candidates 的 SQL
    WHERE 内（LIMIT 之前）过滤——白名单外的 due schedule 不占用 LIMIT 配额，允许
    任务当轮必被扫到（无饿死）。排除租户的 schedule 不前移，下轮仍不入选，恢复后
    照常接纳。空 allowlist（不限制）归一为 None（无过滤）。
    """
    cfg = _resolve_config(config)
    if not cfg.enabled or not cfg.time_triggers_enabled:
        return {"enabled": False, "scanned": 0, "accepted": 0}
    now = _aware(now or _utcnow())
    batch = int(batch or DEFAULT_SCAN_BATCH)
    candidates = da_schedules.find_due_candidates(
        now,
        limit=batch,
        scenario_key=SCENARIO_KEY,
        tenant_allowlist=cfg.tenant_allowlist or None,
    )
    accepted: List[Dict[str, Any]] = []
    for candidate in candidates:
        try:
            accepted.append(da_occurrences.accept_time_slot(candidate, now))
        except Exception as e:  # noqa: BLE001 单条隔离：一个槽失败不阻断本轮扫描
            logger.opt(exception=True).warning(
                f"后端日志：weixin_marketing 时间槽接纳异常 tenant={candidate['tenant_id']} "
                f"schedule={candidate.get('id')}: {e}"
            )
    if accepted:
        logger.info(
            f"后端日志：weixin_marketing 时间扫描 now={now.isoformat()} "
            f"候选={len(candidates)} 接纳={len(accepted)}"
        )
    return {"enabled": True, "scanned": len(candidates), "accepted": len(accepted),
            "results": accepted}


# ==================== ② 执行驱动 tick（outbox worker + 在途驱动）====================


def run_dispatch_tick(
    now: Optional[datetime] = None,
    batch: Optional[int] = None,
    config: Optional[WeixinMarketingConfig] = None,
) -> Dict[str, Any]:
    """执行驱动：claim run_due outbox 条目 → 驱动 run 至需要设备/终态；
    再扫在途 running run（上一条 verified 后派发下一条、invocation 存活期内续租）。"""
    cfg = _resolve_config(config)
    if not cfg.enabled:
        return {"enabled": False, "requeued": 0, "claimed": 0, "dispatched": 0,
                "renewed": 0}
    from src.weixin_marketing.registration import ensure_registered

    ensure_registered()  # 幂等（enabled 门控内；registry 存活性复核）
    now = _aware(now or _utcnow())
    batch = int(batch or cfg.dispatch_batch_size)
    stats: Dict[str, Any] = {
        "enabled": True, "requeued": 0, "claimed": 0, "dispatched": 0, "renewed": 0,
        "skipped_tenants": 0,
    }
    # worker 崩溃恢复：lease 过期 processing → pending 重投（幂等）
    try:
        stats["requeued"] = da_outbox.requeue_stale_processing(
            lease_grace_seconds=OUTBOX_REQUEUE_GRACE_SECONDS
        )
    except Exception as e:  # noqa: BLE001
        logger.opt(exception=True).warning(f"后端日志：weixin_marketing outbox 重投失败: {e}")
    entries = da_outbox.claim_pending_outbox(
        limit=batch, lease_seconds=OUTBOX_LEASE_SECONDS
    )
    for entry in entries:
        try:
            _process_outbox_entry(entry, now, cfg, stats)
        except Exception as e:  # noqa: BLE001 单条隔离：异常条目留 processing，lease 到期重投
            logger.opt(exception=True).error(
                f"后端日志：weixin_marketing outbox 条目处理异常 tenant={entry['tenant_id']} "
                f"kind={entry['kind']} aggregate={entry['aggregate_ref']}: {e}"
            )
    _drive_running_runs(now, batch, cfg, stats)
    logger.info(
        f"后端日志：weixin_marketing 执行驱动 now={now.isoformat()} "
        f"requeued={stats['requeued']} claimed={stats['claimed']} "
        f"dispatched={stats['dispatched']} renewed={stats['renewed']} "
        f"skipped_tenants={stats['skipped_tenants']}"
    )
    return stats


def _process_outbox_entry(
    entry: Dict[str, Any], now: datetime, cfg: WeixinMarketingConfig, stats: Dict[str, Any]
) -> None:
    """单条 outbox 条目：run_due → 驱动 run；run_finished → 落账（P2 无下游消费者）。

    非本场景条目不动（留 processing，lease 到期回 pending，由对应场景 worker 接管）。
    tenant_allowlist 预检（CR-P1-2）：排除租户的 pending run 不驱动、不改判（暂停
    语义）——条目留 processing 由 lease 过期归还，短期排除恢复后自动续跑；长期排除
    经毒丸上限收敛 failed（reason=tenant_not_allowed）。
    """
    tenant_id = entry["tenant_id"]
    kind = entry["kind"]
    if kind == "run_due":
        occurrence = da_occurrences.get_occurrence(str(entry["aggregate_ref"]), tenant_id)
        if occurrence is None or occurrence.get("scenario_key") != SCENARIO_KEY:
            return
        run = _get_run_by_occurrence(tenant_id, str(occurrence["id"]))
        attempts = int(entry.get("attempt_count") or 0)
        over_cap = attempts > OUTBOX_MAX_ATTEMPTS
        if run is not None and run["state"] == "pending":
            allowed = tenant_allowed(cfg, tenant_id)
            if not allowed and not over_cap:
                stats["skipped_tenants"] += 1
                logger.debug(
                    f"后端日志：weixin_marketing 租户不在 allowlist，跳过驱动（暂停语义）"
                    f"tenant={tenant_id} run={run['id']} attempts={attempts}"
                )
                return
            stats["claimed"] += 1
            if over_cap:
                # P2-3：毒丸条目收敛目标 pending run——manual run 无 expires_at，
                # 不被底座 expire_overdue_runs 覆盖，不收敛将永久 pending
                reason = "tenant_not_allowed" if not tenant_allowed(cfg, tenant_id) else "dispatch_attempts_exceeded"
                _fail_poisoned_run(run, attempts, reason)
                da_outbox.mark_outbox_done(str(entry["id"]), tenant_id)
                return
            if _claim_prepare_and_drive(run, now, stats):
                da_outbox.mark_outbox_done(str(entry["id"]), tenant_id)
                return
            # 目标 run 已被并发领取/已非 pending：条目留 processing，lease 到期重投后
            # 按届时状态收敛（R50 定向领取，不存在误领其他 run）
            return
        # 已 running（重投恢复，交在途驱动段按 allowlist 门控推进/续租）/终态
        # （回调已聚合）/无 run（防御）：条目的「驱动启动」职责已完成
        stats["claimed"] += 1
        da_outbox.mark_outbox_done(str(entry["id"]), tenant_id)
        return
    if kind == "run_finished":
        run = da_runs.get_run(str(entry["aggregate_ref"]), tenant_id)
        if run is None or run.get("scenario_key") != SCENARIO_KEY:
            return
        stats["claimed"] += 1
        # P2 无下游通知消费者：账本行保留（done 即投递完成），后续阶段接线真实消费者；
        # 纯落账无执行副作用，不受 tenant_allowlist 门控
        da_outbox.mark_outbox_done(str(entry["id"]), tenant_id)
        return
    # 未知 kind：不动（未来扩展 kind 由其消费者处理）


def _fail_poisoned_run(
    run: Dict[str, Any], attempts: int, reason: str
) -> None:
    """毒丸/长期排除条目的目标 pending run 落终态 failed + audit（P2-3）。

    run 从未派发（pending、零 deliveries），failed 语义=调度侧放弃而非执行失败；
    finish_run 的 state NOT IN terminal 条件保证幂等（并发下恰一次）。
    """
    tenant_id = run["tenant_id"]
    run_id = str(run["id"])
    with get_db_connection() as conn:
        cursor = conn.cursor()
        ok = da_runs.finish_run(
            cursor, run_id, tenant_id, "failed",
            {"reason": reason, "attempts": attempts},
            fence_token=run.get("fence_token"),
        )
        if ok:
            da_audit.insert_audit(
                cursor, tenant_id, "run_dispatch_poisoned", "run", run_id,
                user_id=run.get("user_id"), scenario_key=SCENARIO_KEY,
                detail={"reason": reason, "attempts": attempts},
            )
        conn.commit()
    if ok:
        logger.error(
            f"后端日志：weixin_marketing 毒丸条目收敛 pending run tenant={tenant_id} "
            f"run={run_id} reason={reason} attempts={attempts}"
        )


def _claim_prepare_and_drive(run: Dict[str, Any], now: datetime, stats: Dict[str, Any]) -> bool:
    """outbox 条目目标的 pending run 定向领取驱动（R50）。返回条目职责是否完成。

    claim_pending_run(expected_run_id)（FOR UPDATE SKIP LOCKED 定向领取）只可能领到
    本条目目标 run——非目标/已被领/已非 pending 返回 None（条目留 processing，lease
    到期重投）。领到目标后才加载**该 run 自身**的 revision 配置与固定设备（配置与
    run 恒一致，tie 分歧下无误配编译）；准备段重验失败已按原因落终态
    （claim_and_prepare 内部审计留痕）。
    """
    tenant_id = run["tenant_id"]
    prepared_run = da_executor.claim_pending_run(
        tenant_id=tenant_id,
        expected_run_id=str(run["id"]),
        lease_seconds=RUN_LEASE_SECONDS,
    )
    if prepared_run is None:
        return False  # 并发：目标 run 已被其他 worker 领取/已非 pending（重投路径会再见）
    revision_config = _load_revision_config(tenant_id, str(prepared_run["revision_ref"]))
    device_id = _resolve_device_id(tenant_id, revision_config)
    prepared = da_executor.prepare_claimed_run(
        prepared_run, revision_config=revision_config, device_id=device_id, now=now,
    )
    if prepared["prepared"]:
        stats["dispatched"] += _drive_dispatch_loop(prepared["run"], now)
    # prepared=False：重验失败已按原因落终态（准备段内部审计留痕）
    return True


def _drive_dispatch_loop(run: Dict[str, Any], now: datetime) -> int:
    """顺序派发直至需要设备（在途/终态/重验失败）——execute_next_delivery 返回 None。"""
    dispatched = 0
    for _ in range(MAX_STEPS_PER_RUN):
        step = da_executor.execute_next_delivery(run, now=now)
        if step is None:
            break
        dispatched += 1
    return dispatched


def _drive_running_runs(
    now: datetime, batch: int, cfg: WeixinMarketingConfig, stats: Dict[str, Any]
) -> None:
    """在途 running run 驱动：无在途条目时派发下一条；在途且 invocation 存活时续租。

    tenant_allowlist 预检（CR-P1-2）：排除租户的 running run 不派发不续租——
    租约到期后由 runs_reclaim_tick 收敛（已提交条目照实回收，§5.4），恢复
    allowlist 前 pause 语义。
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT id, tenant_id FROM desktop_automation_runs
            WHERE scenario_key = %s AND state = 'running'
            ORDER BY claimed_at NULLS LAST, created_at
            LIMIT %s
            """,
            (SCENARIO_KEY, batch),
        )
        rows = [dict(r) for r in cursor.fetchall()]
    for row in rows:
        if not tenant_allowed(cfg, row["tenant_id"]):
            stats["skipped_tenants"] += 1
            continue
        run = da_runs.get_run(str(row["id"]), row["tenant_id"])
        if run is None or run["state"] != "running":
            continue
        try:
            stats["dispatched"] += _advance_running_run(run, now, stats)
        except Exception as e:  # noqa: BLE001 单 run 隔离：不阻断批次其余
            logger.opt(exception=True).error(
                f"后端日志：weixin_marketing 在途 run 驱动异常 tenant={row['tenant_id']} "
                f"run={row['id']}: {e}"
            )


def _advance_running_run(run: Dict[str, Any], now: datetime, stats: Dict[str, Any]) -> int:
    """单个在途 run：有在途 delivery → invocation 存活则续租；否则派发下一条。"""
    tenant_id = run["tenant_id"]
    run_id = str(run["id"])
    deliveries = da_deliveries.list_run_deliveries(run_id, tenant_id)
    in_flight = next((d for d in deliveries if d.get("state") == "dispatched"), None)
    if in_flight is not None:
        if _invocation_alive(tenant_id, str(in_flight["id"]), now):
            # 设备仍在执行预算内：续租防误回收（调度器死亡后无人续租 → 到期收敛）
            da_runs.renew_lease(
                run_id, tenant_id, run["fence_token"], RUN_LEASE_SECONDS
            )
            stats["renewed"] += 1
        # invocation 已终态/超截止：停止续租，租约到期后交 runs_reclaim_tick 收敛
        return 0
    return _drive_dispatch_loop(run, now)


def _invocation_alive(tenant_id: str, delivery_id: str, now: datetime) -> bool:
    """在途 delivery 的最新 attempt 绑定 invocation 是否仍可能产出结果。"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT i.state, i.deadline_at
            FROM desktop_automation_attempts a
            JOIN local_tool_invocations i
              ON i.id = a.invocation_id AND i.tenant_id = a.tenant_id
            WHERE a.tenant_id = %s AND a.delivery_id = %s
            ORDER BY a.attempt_no DESC
            LIMIT 1
            """,
            (tenant_id, delivery_id),
        )
        row = cursor.fetchone()
    if row is None:
        return False
    if row["state"] in lt_repository.TERMINAL_STATES:
        return False
    deadline = row["deadline_at"]
    if deadline is not None and _aware(deadline) <= now:
        return False
    return True


def _get_run_by_occurrence(tenant_id: str, occurrence_id: str) -> Optional[Dict[str, Any]]:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT {_RUN_COLUMNS} FROM desktop_automation_runs "
            "WHERE tenant_id = %s AND occurrence_id = %s",
            (tenant_id, occurrence_id),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def _load_revision_config(tenant_id: str, revision_ref: str) -> Dict[str, Any]:
    from src.weixin_marketing.service import WeixinMarketingService

    return WeixinMarketingService().load_revision_config(tenant_id, revision_ref)


def _resolve_device_id(tenant_id: str, revision_config: Dict[str, Any]) -> str:
    """目标设备取自 revision 绑定的 group_binding（run 固定任务设备，不跟随临时选中）。"""
    binding_id = str((revision_config or {}).get("group_binding_id") or "")
    try:
        import uuid as _uuid

        binding_id = str(_uuid.UUID(binding_id))
    except (ValueError, TypeError, AttributeError):
        return ""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT device_id FROM bs_weixin_marketing_group_bindings "
            "WHERE tenant_id = %s AND id = %s",
            (tenant_id, binding_id),
        )
        row = cursor.fetchone()
    device_id = str(row["device_id"]) if row and row["device_id"] else ""
    return device_id


# ==================== ③ 许可过期清扫 tick ====================


def permits_sweep_tick(
    now: Optional[datetime] = None,
    config: Optional[WeixinMarketingConfig] = None,
) -> Dict[str, Any]:
    """许可过期清扫（R22）：issued 且 deadline 过期 → expired，不释放预留。"""
    cfg = _resolve_config(config)
    if not cfg.enabled:
        return {"enabled": False, "expired": 0}
    expired = lt_permits.expire_permits(now=_aware(now or _utcnow()))
    if expired:
        logger.warning(
            f"后端日志：weixin_marketing 许可清扫 expired={expired}（预留保留，R22）"
        )
    return {"enabled": True, "expired": expired}


# ==================== ④ run 租约回收 tick ====================


def runs_reclaim_tick(
    now: Optional[datetime] = None,
    batch: Optional[int] = None,
    config: Optional[WeixinMarketingConfig] = None,
) -> Dict[str, Any]:
    """租约过期 running run 收敛（§5.4）：按 deliveries 状态聚合，不自动重派。

    - dispatched 且 may_have_started/unknown → unknown（可能已执行，绝不重派）；
    - pending / dispatched 仍 prepared → expired（未提交，许可从未签发）；
    - 聚合：有 unknown → unknown/partial；全成功 → succeeded；其余按截止口径
      （部分已发送→partial、纯等待→expired、全失败→failed）；
    - 空 deliveries（领取后失联）→ failed（无任何提交，保守按未完成失败）。
    """
    cfg = _resolve_config(config)
    if not cfg.enabled:
        return {"enabled": False, "reclaimed": 0}
    now = _aware(now or _utcnow())
    batch = int(batch or DEFAULT_RECLAIM_BATCH)
    reclaimed = 0
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT {_RUN_COLUMNS} FROM desktop_automation_runs
            WHERE scenario_key = %s AND state = 'running'
              AND lease_expires_at IS NOT NULL AND lease_expires_at < %s
            ORDER BY lease_expires_at
            LIMIT %s
            FOR UPDATE SKIP LOCKED
            """,
            (SCENARIO_KEY, now, batch),
        )
        stale = [dict(r) for r in cursor.fetchall()]
        for run in stale:
            tenant_id = run["tenant_id"]
            run_id = str(run["id"])
            da_deliveries.expire_unstarted_deliveries(cursor, tenant_id, run_id)
            # 聚合读取走同一游标（本事务内已提交未落的 expired/unknown 更新对自有
            # 连接可见；list_run_deliveries 会另开连接读不到未提交行）
            cursor.execute(
                """
                SELECT state, effect, phase FROM desktop_automation_deliveries
                WHERE tenant_id = %s AND run_id = %s ORDER BY position
                """,
                (tenant_id, run_id),
            )
            rows = [dict(r) for r in cursor.fetchall()]
            if rows:
                state = da_runs.compute_run_terminal_state(
                    rows, deadline_exceeded=True
                ) or "unknown"
            else:
                state = "failed"
            da_runs.finish_run(
                cursor, run_id, tenant_id, state,
                {"reason": "lease_expired_reclaimed"},
                fence_token=run.get("fence_token"),
            )
            da_audit.insert_audit(
                cursor, tenant_id, "run_reclaimed", "run", run_id,
                user_id=run.get("user_id"), scenario_key=SCENARIO_KEY,
                detail={
                    "state": state,
                    "lease_expires_at": run.get("lease_expires_at").isoformat()
                    if run.get("lease_expires_at") else None,
                },
            )
            reclaimed += 1
        conn.commit()
    if reclaimed:
        logger.warning(
            f"后端日志：weixin_marketing 租约回收 reclaimed={reclaimed}（§5.4 收敛，不重派）"
        )
    return {"enabled": True, "reclaimed": reclaimed}


# ==================== ⑤ 过期素材清理 tick（P4-A R57）====================


def assets_cleanup_tick(
    now: Optional[datetime] = None,
    batch: Optional[int] = None,
    config: Optional[WeixinMarketingConfig] = None,
) -> Dict[str, Any]:
    """过期无引用素材批量硬删（enabled 门控）：retention_until 已过且无 draft/
    published 引用 → 删行+回收文件（引用复核与删除同事务，绝不误删在用素材）。"""
    from src.weixin_marketing import assets as wxm_assets

    cfg = _resolve_config(config)
    if not cfg.enabled:
        return {"enabled": False, "deleted": 0, "skipped_referenced": 0}
    return wxm_assets.cleanup_expired_assets(now=now, batch=batch, config=cfg)


# ==================== ⑥ 事件匹配 worker tick（P4-B R57）====================

# 单事件单 tick 内最大候选处理步数（eligible 快照集合上限防御）
MAX_EVENT_CANDIDATES_PER_TICK = 200
# 事件匹配单页扫描事件数
DEFAULT_EVENT_MATCH_BATCH = 100


def event_match_tick(
    now: Optional[datetime] = None,
    batch: Optional[int] = None,
    config: Optional[WeixinMarketingConfig] = None,
) -> Dict[str, Any]:
    """事件匹配 worker（底座计划 §3.2）：源侧 outbox 投递（示例源）→ 按
    events.eligible_revision_refs 持久集合逐事件处理。

    - 只遍历接纳时冻结的 eligible 快照，不按 event_type 动态查询新版本（配置发布
      不回放历史事件）；
    - 每候选短事务锁序 task subject→schedule→occurrence/run→event 游标（R12：游标
      CAS 更新置于上述锁之后），CAS 失败即并发方在处理，本轮放弃该事件；
    - 已暂停/版本切换/订阅失效/condition 不命中 → 候选 skipped 原因持久（audit），
      不转投新版本；
    - due_at = received_at + 冻结 delay；occurred_at 只作业务条件与审计依据；
    - 快照集合全部处理完才置 events.state=processed（重启经 match_cursor 断点续跑）。
    """
    cfg = _resolve_config(config)
    if not cfg.enabled or not cfg.event_triggers_enabled:
        return {"enabled": False, "matched": 0, "skipped": 0, "processed_events": 0}
    now = _aware(now or _utcnow())
    batch = int(batch or DEFAULT_EVENT_MATCH_BATCH)

    # 示例内部源投递（生产真实源接入前的事件来源；失败不阻断匹配段）
    from src.weixin_marketing import internal_event_example as wxm_example
    from src.weixin_marketing import event_sources as wxm_sources

    delivery_stats: Dict[str, Any] = {"delivered": 0}
    try:
        delivery_stats = wxm_example.deliver_example_event_outbox(now=now)
    except Exception as e:  # noqa: BLE001
        logger.opt(exception=True).warning(
            f"后端日志：weixin_marketing 示例事件源投递异常: {e}"
        )
    try:
        wxm_sources.cleanup_expired_nonces(now)
    except Exception as e:  # noqa: BLE001
        logger.opt(exception=True).warning(f"后端日志：weixin_marketing nonce 清理异常: {e}")

    pending_events = da_events.list_unprocessed_events(
        SCENARIO_KEY,
        limit=batch,
        tenant_allowlist=cfg.tenant_allowlist or None,
    )
    matched = skipped = deferred = 0
    processed_events = 0
    for event in pending_events:
        try:
            stats = _match_single_event(event, now)
            matched += stats["matched"]
            skipped += stats["skipped"]
            deferred += stats.get("deferred", 0)
            processed_events += stats["completed"]
        except Exception as e:  # noqa: BLE001 单事件隔离
            logger.opt(exception=True).error(
                f"后端日志：weixin_marketing 事件匹配异常 tenant={event['tenant_id']} "
                f"event={event['id']}: {e}"
            )
    if pending_events:
        logger.info(
            f"后端日志：weixin_marketing 事件匹配 now={now.isoformat()} "
            f"events={len(pending_events)} matched={matched} skipped={skipped} "
            f"deferred={deferred} processed_events={processed_events} "
            f"example_delivered={delivery_stats.get('delivered', 0)}"
        )
    return {
        "enabled": True, "scanned": len(pending_events), "matched": matched,
        "skipped": skipped, "deferred": deferred, "processed_events": processed_events,
        "example_delivered": delivery_stats.get("delivered", 0),
    }


def _match_single_event(event: Dict[str, Any], now: datetime) -> Dict[str, int]:
    """单事件匹配：按冻结 eligible 快照稳定顺序处理，游标断点续跑。

    返回 {matched, skipped, deferred, completed}；completed=1 表示本 tick 将事件置
    processed。计数在候选事务成功提交后才累加（P2-4：回滚/CAS 失败路径不计）。

    瞬时放弃（不推进游标、不 processed，下轮重试，P0-1）：
    - task_locked：task subject 被 SKIP LOCKED 竞争（瞬时）——与 CAS 失败同路；
    - 游标 CAS 失败：另一 worker 已推进（本候选事务整体回滚）。
    放弃路径经 log_audit 记 kind=event_match_deferred（独立短事务，失败仅告警），
    与业务性 event_match_skipped（持久 skip 原因）明确区分。
    """
    from src.weixin_marketing import event_sources as wxm_sources

    tenant_id = event["tenant_id"]
    event_id = str(event["id"])
    source_ref = event["source_ref"]
    external_event_id = event["external_event_id"]
    eligible = list(event.get("eligible_revision_refs") or [])
    # 稳定排序（快照行已按 scenario/task/revision/schedule_id 排序，防御性重排）
    eligible.sort(key=lambda c: (c.get("scenario_key") or "", c.get("task_ref") or "",
                                 c.get("revision_ref") or "", str(c.get("id"))))
    cursor = int(event["match_cursor"] or 0)
    matched = skipped = deferred = 0

    if cursor >= len(eligible):
        # 空快照/已全部处理：直接收敛 processed（幂等）
        da_events.mark_event_processed(event_id, tenant_id)
        return {"matched": matched, "skipped": skipped, "deferred": deferred,
                "completed": 1}

    payload = wxm_sources.load_event_payload(tenant_id, event.get("payload_ref"))
    received_at = _aware(event["received_at"])

    # P2-1：切片窗口相对 cursor（eligible[cursor:MAX] 在 cursor>0 时卡死游标）
    for candidate in eligible[cursor:cursor + MAX_EVENT_CANDIDATES_PER_TICK]:
        with get_db_connection() as conn:
            cur = conn.cursor()
            try:
                hit, cond_reason = wxm_sources.evaluate_condition(
                    candidate.get("condition_ref"), payload
                )
                if not hit:
                    da_audit.insert_audit(
                        cur, tenant_id, "event_match_skipped", "schedule",
                        str(candidate.get("id")), user_id=None,
                        scenario_key=candidate.get("scenario_key"),
                        detail={
                            "event_id": event_id, "reason": cond_reason,
                            "external_event_id": external_event_id,
                        },
                    )
                    ok = _advance_event_cursor(
                        cur, tenant_id, event_id, cursor, final=False
                    )
                    if not ok:
                        conn.rollback()
                        return _match_give_up(matched, skipped)
                    conn.commit()
                    skipped += 1
                    cursor += 1
                    continue
                delay_seconds = int(candidate.get("delay_seconds") or 0)
                due_at = received_at + timedelta(seconds=delay_seconds)
                result = da_occurrences.accept_event_trigger_on(
                    cur,
                    tenant_id=tenant_id,
                    scenario_key=candidate.get("scenario_key"),
                    task_ref=candidate.get("task_ref"),
                    revision_ref=candidate.get("revision_ref"),
                    source_ref=source_ref,
                    external_event_id=external_event_id,
                    user_id="",
                    due_at=due_at,
                    now=now,
                    schedule_id=str(candidate.get("id")),
                )
                reason = result.get("reason") or ""
                if not result.get("accepted") and reason == "task_locked":
                    # P0-1：瞬时 SKIP LOCKED 竞争——回滚候选事务、本轮放弃该事件
                    # （不推进游标、不 processed），下轮重试；绝不与业务 skip 同路
                    conn.rollback()
                    _log_deferred(tenant_id, event_id, external_event_id, candidate,
                                  "task_locked")
                    return _match_give_up(matched, skipped, deferred=1)
                if not (result.get("accepted") and result.get("created")):
                    # 业务性 skipped 原因持久（task_not_active/revision_switched/
                    # subscription_inactive/duplicate 等；duplicate 不重复建 run）
                    da_audit.insert_audit(
                        cur, tenant_id, "event_match_skipped", "schedule",
                        str(candidate.get("id")), user_id=None,
                        scenario_key=candidate.get("scenario_key"),
                        detail={
                            "event_id": event_id, "reason": reason or "duplicate",
                            "external_event_id": external_event_id,
                            "occurrence_id": result.get("occurrence_id"),
                        },
                    )
                ok = _advance_event_cursor(
                    cur, tenant_id, event_id, cursor, final=False
                )
                if not ok:
                    # 并发 worker 已推进：整个候选事务回滚，本轮放弃该事件
                    conn.rollback()
                    return _match_give_up(matched, skipped)
                conn.commit()
                # 计数在成功提交后累加（P2-4）
                if result.get("accepted") and result.get("created"):
                    matched += 1
                else:
                    skipped += 1
                cursor += 1
            except Exception:
                conn.rollback()
                raise

    if cursor >= len(eligible):
        da_events.mark_event_processed(event_id, tenant_id)
        return {"matched": matched, "skipped": skipped, "deferred": deferred,
                "completed": 1}
    return {"matched": matched, "skipped": skipped, "deferred": deferred,
            "completed": 0}


def _match_give_up(matched: int, skipped: int, *, deferred: int = 0) -> Dict[str, int]:
    """瞬时放弃该事件（本轮不 processed，计数只含已提交成功的候选）"""
    return {"matched": matched, "skipped": skipped, "deferred": deferred,
            "completed": 0}


def _log_deferred(
    tenant_id: str, event_id: str, external_event_id: str,
    candidate: Dict[str, Any], reason: str,
) -> None:
    """瞬时放弃的观测留痕（独立短事务，与候选事务解耦；失败仅告警）。

    kind=event_match_deferred 与业务性 event_match_skipped 区分：deferred=下轮重试，
    skipped=持久结论。"""
    try:
        da_audit.log_audit(
            tenant_id, "event_match_deferred", "schedule",
            str(candidate.get("id")), scenario_key=candidate.get("scenario_key"),
            detail={
                "event_id": event_id, "reason": reason,
                "external_event_id": external_event_id,
                "task_ref": candidate.get("task_ref"),
            },
        )
    except Exception as e:  # noqa: BLE001 观测留痕失败不影响主流程（日志兜底）
        logger.warning(
            f"后端日志：weixin_marketing event_match_deferred 审计写入失败 "
            f"event={event_id} reason={reason}: {type(e).__name__}"
        )


def _advance_event_cursor(
    cursor, tenant_id: str, event_id: str, expected_cursor: int, *, final: bool
) -> bool:
    """CAS 推进事件匹配游标（expected_cursor 未变才 +1；失败=并发方在处理）"""
    return da_events.advance_match_cursor(
        cursor, event_id, tenant_id, processed=final, expected_cursor=expected_cursor
    )
