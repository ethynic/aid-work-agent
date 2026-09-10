"""weixin_marketing 调度闭环 tick 测试（R44 接线 / R45 per-attempt dedupe / R48 验收）

假时钟注入 tick（dispatch 函数与 APScheduler 解耦）+ 真实 DB + 假设备行为驱动
invocation 全链路：time_scan → occurrence/run 落行 → run_dispatch → 设备
claim/start/write-authorize/operation-result → 逐条 delivery → run 终态。
"""

import uuid
from dataclasses import replace
from datetime import timedelta

import pytest

from src.desktop_automation import runs as da_runs
from src.db.database import get_db_connection
from src.local_tools import permits, repository
from src.local_tools.operation_result import apply_operation_result
from src.local_tools.security import generate_claim_token, sha256_hex
from src.weixin_marketing import dispatch
from tests.unit.weixin_marketing.conftest import (
    create_and_publish,
    manual_run_pending,
    utcnow,
)

pytestmark = pytest.mark.unit

TERMINAL_RUN_STATES = da_runs.RUN_TERMINAL_STATES


# ==================== 假设备驱动 helpers ====================


def _run_invocations(tenant_id, run_id):
    """run 名下全部 v2 invocation（按 business_ref.run_id）"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, device_id, arguments_json, state, dedupe_key
            FROM local_tool_invocations
            WHERE tenant_id = %s AND business_kind = 'desktop_automation'
              AND business_ref->>'run_id' = %s
            ORDER BY created_at
            """,
            (tenant_id, str(run_id)),
        )
        return [dict(r) for r in cur.fetchall()]


def _finish_invocation(
    tenant_id, inv, *, effect="applied", phase="verified", safe_to_retry=None
):
    """假设备完整执行一条 invocation：claim → start → write-authorize → operation-result"""
    args = inv["arguments_json"]
    device_id = inv["device_id"]
    token = generate_claim_token()
    claimed = repository.claim_next(device_id, tenant_id, sha256_hex(token), 300)
    assert claimed is not None, "设备应能领取派发给绑定设备的 invocation"
    assert str(claimed["id"]) == str(inv["id"])
    started = repository.mark_started(str(inv["id"]), tenant_id, sha256_hex(token))
    assert started is not None and started["state"] == "running"
    permit = permits.write_authorize(
        tenant_id=tenant_id, device_id=device_id, invocation_id=str(inv["id"]),
        claim_token_hash=sha256_hex(token), request_id=args["request_id"],
        target_version=args.get("target_version"), payload_hash=args.get("payload_hash"),
    )
    evidence_ref = (
        f"weixin-evidence:{args['request_id']}:1"
        if effect == "applied" and phase == "verified"
        else None
    )
    return apply_operation_result(
        tenant_id=tenant_id, device_id=device_id, invocation_id=str(inv["id"]),
        claim_token_hash=sha256_hex(token), request_id=args["request_id"],
        effect=effect, phase=phase, evidence_ref=evidence_ref,
        safe_to_retry=safe_to_retry,
        permit_id=permit["permit_id"], permit_token=permit["permit_token"],
    )


def _drive_to_terminal(tenant_id, run_id, cfg, now, *, outcomes=None):
    """run_dispatch_tick ↔ 假设备交替推进至 run 终态。

    outcomes: delivery_id -> (effect, phase)；默认全部 applied+verified。
    """
    for _ in range(12):
        run = da_runs.get_run(run_id, tenant_id)
        if run["state"] in TERMINAL_RUN_STATES:
            return run["state"]
        dispatch.run_dispatch_tick(now=now, config=cfg)
        for inv in _run_invocations(tenant_id, run_id):
            if inv["state"] != "queued":
                continue
            effect, phase = (outcomes or {}).get(
                inv["arguments_json"]["delivery_id"], ("applied", "verified")
            )
            _finish_invocation(tenant_id, inv, effect=effect, phase=phase)
    run = da_runs.get_run(run_id, tenant_id)
    pytest.fail(f"run 未在预期步数内到终态: state={run['state']}")
    return run["state"]


def _count(tenant_id, table, extra=""):
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) AS c FROM {table} WHERE tenant_id = %s{extra}", (tenant_id,))
        return int(cur.fetchone()["c"])


def _expire_run_lease(tenant_id, run_id):
    """模拟 worker 死亡：租约置为已过期（真实 DB 时钟）"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE desktop_automation_runs SET lease_expires_at = NOW() - INTERVAL '1 second' "
            "WHERE tenant_id = %s AND id = %s",
            (tenant_id, str(run_id)),
        )
        conn.commit()


# ==================== R48 验收闭环（假时钟）====================


class TestTimeClosedLoop:
    def test_once_full_closed_loop_no_replay(self, service, tenant_id, bindings, wx_config, adapter):
        """once 只一次 + 全链路 tick 驱动：publish → time_scan → run_dispatch →
        假设备逐条 → succeeded；第二次 time_scan 无新 occurrence；outbox 全 done。"""
        _, group_id = bindings
        # run_at 取当前真实时刻附近：occurrence/run 的 due_at 用真实 DB 时钟判定
        # 领取（claim_run WHERE due_at <= NOW()），远期假时钟只用于扫描侧断言
        run_at = utcnow()
        blocks = [
            {"type": "text", "text_content": "第 1 条"},
            {"type": "link", "url": "https://example.com/a"},
        ]
        automation_id, _, _ = create_and_publish(
            service, tenant_id, group_id, blocks=blocks,
            trigger={"type": "once", "run_at": run_at.isoformat(), "timezone": "UTC"},
        )
        scan_now = run_at + timedelta(seconds=5)
        result = dispatch.time_scan_tick(now=scan_now, config=wx_config)
        assert result["accepted"] == 1
        assert _count(tenant_id, "desktop_automation_occurrences") == 1
        assert _count(tenant_id, "desktop_automation_runs") == 1

        runs_list = service.list_runs(tenant_id, "owner-1", automation_id=automation_id)
        run_id = str(runs_list["items"][0]["id"])
        state = _drive_to_terminal(tenant_id, run_id, wx_config, scan_now)
        assert state == "succeeded"
        detail = service.get_run_detail(tenant_id, run_id, "owner-1")
        assert [d["state"] for d in detail["deliveries"]] == ["succeeded", "succeeded"]

        # once 只一次：远期再扫描无新 occurrence/run
        again = dispatch.time_scan_tick(now=run_at + timedelta(hours=1), config=wx_config)
        assert again["accepted"] == 0
        assert _count(tenant_id, "desktop_automation_occurrences") == 1
        assert _count(tenant_id, "desktop_automation_runs") == 1

        # outbox 台账收敛：run_due / run_finished 全 done（run_finished 由下一 tick 落账）
        dispatch.run_dispatch_tick(now=scan_now, config=wx_config)
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT kind, state FROM desktop_automation_outbox WHERE tenant_id = %s",
                (tenant_id,),
            )
            outbox_rows = {r["kind"]: r["state"] for r in cur.fetchall()}
        assert outbox_rows == {"run_due": "done", "run_finished": "done"}

    def test_interval_restart_no_backlog(self, service, tenant_id, bindings, wx_config, adapter):
        """interval 重启不积压：两个远隔 now 点各恰按最近槽接纳一次，不展开错过槽。"""
        _, group_id = bindings
        start = utcnow().replace(microsecond=0)
        interval = 300  # wx_config.min_interval_seconds=300
        automation_id, _, _ = create_and_publish(
            service, tenant_id, group_id,
            blocks=[{"type": "text", "text_content": "间隔内容"}],
            trigger={
                "type": "interval", "start_at": start.isoformat(),
                "interval_seconds": interval, "timezone": "UTC",
            },
        )
        # 第一个远隔点（错过 2 个槽）：只接纳最近槽 start+900
        t1 = start + timedelta(seconds=3 * interval + 5)
        first = dispatch.time_scan_tick(now=t1, config=wx_config)
        assert first["accepted"] == 1
        from src.desktop_automation import occurrences as da_occurrences

        runs_list = service.list_runs(tenant_id, "owner-1", automation_id=automation_id)
        assert runs_list["total"] == 1
        time_run = da_runs.get_run(str(runs_list["items"][0]["id"]), tenant_id)
        occurrence = da_occurrences.get_occurrence(str(time_run["occurrence_id"]), tenant_id)
        assert occurrence["scheduled_for"] == start + timedelta(seconds=3 * interval)
        # 错过槽留 missed 审计（不展开积压）
        assert _count(
            tenant_id, "desktop_automation_audit_events", extra=" AND kind = 'schedule_missed'"
        ) == 1

        # 第二个远隔点（再 +600s）：恰接纳下一槽 start+1500；首个 run 未收敛，
        # skip_overlap（默认策略）下该槽记 skipped 不建第二个 run——不积压、无双发
        t2 = start + timedelta(seconds=5 * interval + 5)
        second = dispatch.time_scan_tick(now=t2, config=wx_config)
        assert second["accepted"] == 1
        assert _count(tenant_id, "desktop_automation_occurrences") == 2
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT status FROM desktop_automation_occurrences "
                "WHERE tenant_id = %s ORDER BY created_at",
                (tenant_id,),
            )
            statuses = [r["status"] for r in cur.fetchall()]
        assert statuses == ["open", "skipped"]
        assert service.list_runs(tenant_id, "owner-1", automation_id=automation_id)["total"] == 1

    def test_time_triggers_subflag_gates_scan(self, service, tenant_id, bindings, wx_config, adapter):
        """time_triggers_enabled=false：enabled 仍真，时间扫描零接纳"""
        _, group_id = bindings
        run_at = utcnow() + timedelta(seconds=30)
        create_and_publish(
            service, tenant_id, group_id,
            blocks=[{"type": "text", "text_content": "内容"}],
            trigger={"type": "once", "run_at": run_at.isoformat(), "timezone": "UTC"},
        )
        cfg = replace(wx_config, time_triggers_enabled=False)
        result = dispatch.time_scan_tick(now=run_at + timedelta(seconds=60), config=cfg)
        assert result["accepted"] == 0
        assert _count(tenant_id, "desktop_automation_occurrences") == 0


# ==================== 崩溃恢复：租约过期收敛（§5.4）====================


class TestScanFilterPushdown:
    def test_starvation_allowed_task_accepted_with_100_excluded_due(
        self, service, tenant_id, bindings, wx_config, adapter
    ):
        """R53 饿死测试：100 条白名单外 due schedule + 1 条允许任务 → 允许任务当轮
        被接纳（scenario/tenant 过滤在底座候选 SQL 的 LIMIT 之前，噪声不占配额；
        batch=50 < 噪声数，若过滤在 Python 侧则允许任务本轮必被饿死）。"""
        _, group_id = bindings
        noise_tenants = [f"wxm_test_{uuid.uuid4().hex[:12]}" for _ in range(100)]
        run_at = utcnow()
        with get_db_connection() as conn:
            cur = conn.cursor()
            for tid in noise_tenants:
                cur.execute(
                    """
                    INSERT INTO desktop_automation_schedules
                        (tenant_id, scenario_key, task_ref, revision_ref, user_id, kind,
                         trigger_key, next_fire_at, one_shot, status)
                    VALUES (%s, 'weixin.fixed_content.v1', 'noise-task', 'rev-noise',
                            'owner-noise', 'time', 'time', %s, TRUE, 'active')
                    """,
                    (tid, run_at - timedelta(seconds=60)),
                )
            conn.commit()
        try:
            automation_id, _, _ = create_and_publish(
                service, tenant_id, group_id,
                blocks=[{"type": "text", "text_content": "内容"}],
                trigger={"type": "once", "run_at": run_at.isoformat(), "timezone": "UTC"},
            )
            excluded = replace(wx_config, tenant_allowlist=[tenant_id])
            result = dispatch.time_scan_tick(
                now=run_at + timedelta(seconds=5), config=excluded, batch=50
            )
            assert result["scanned"] == 1  # SQL 内过滤后仅剩允许任务
            assert result["accepted"] == 1
            assert _count(tenant_id, "desktop_automation_occurrences") == 1
            # 同 allowlist 复扫：允许任务已接纳（one-shot consumed），候选归零（噪声被
            # SQL 持续过滤，不回流 Python 侧）
            assert dispatch.time_scan_tick(
                now=run_at + timedelta(seconds=5), config=excluded, batch=50
            )["scanned"] == 0
        finally:
            from tests.unit.weixin_marketing.conftest import cleanup_da_tables

            for tid in noise_tenants:
                cleanup_da_tables(tid)


# ==================== 崩溃恢复：租约过期收敛（§5.4）====================


class TestRunsReclaim:
    def _dispatch_first(self, service, tenant_id, group_id, wx_config, blocks, *, authorize=True):
        automation_id, _, _ = create_and_publish(
            service, tenant_id, group_id, blocks=blocks,
            trigger=None,  # 默认 once 未来时刻（不会先触发）
        )
        _, run_id = manual_run_pending(service, tenant_id, automation_id)
        dispatch.run_dispatch_tick(now=utcnow(), config=wx_config)
        invocations = _run_invocations(tenant_id, run_id)
        assert len(invocations) == 1, "首 tick 只派发第一条"
        if authorize:
            # 假设备签发许可 → delivery may_have_started（已越过提交边界）
            inv = invocations[0]
            token = generate_claim_token()
            repository.claim_next(inv["device_id"], tenant_id, sha256_hex(token), 300)
            repository.mark_started(str(inv["id"]), tenant_id, sha256_hex(token))
            permits.write_authorize(
                tenant_id=tenant_id, device_id=inv["device_id"],
                invocation_id=str(inv["id"]), claim_token_hash=sha256_hex(token),
                request_id=inv["arguments_json"]["request_id"],
                target_version=inv["arguments_json"].get("target_version"),
                payload_hash=inv["arguments_json"].get("payload_hash"),
            )
        return automation_id, run_id, invocations[0]

    def test_may_have_started_converges_unknown_not_redispatched(
        self, service, tenant_id, bindings, wx_config, adapter
    ):
        """第 1 条已签许可（may_have_started）后 worker 失联 → reclaim 收敛 unknown；
        may_have_started 条目不重派（无新 invocation）；剩余 pending → expired。"""
        _, group_id = bindings
        blocks = [
            {"type": "text", "text_content": "第 1 条"},
            {"type": "text", "text_content": "第 2 条"},
        ]
        automation_id, run_id, first_inv = self._dispatch_first(
            service, tenant_id, group_id, wx_config, blocks, authorize=True
        )
        _expire_run_lease(tenant_id, run_id)
        result = dispatch.runs_reclaim_tick(now=utcnow(), config=wx_config)
        assert result["reclaimed"] == 1

        detail = service.get_run_detail(tenant_id, run_id, "owner-1")
        assert [d["state"] for d in detail["deliveries"]] == ["unknown", "expired"]
        run = da_runs.get_run(run_id, tenant_id)
        assert run["state"] == "unknown"  # 有 unknown 无成功（§5.4：unknown 优先）
        # 不重派：delivery 1 仍只有最初 1 条 invocation
        assert len(_run_invocations(tenant_id, run_id)) == 1
        assert str(_run_invocations(tenant_id, run_id)[0]["id"]) == str(first_inv["id"])

    def test_prepared_dispatched_expires_pure_wait(self, service, tenant_id, bindings, wx_config, adapter):
        """已派发但未签许可（prepared，从未提交）→ reclaim 置 expired，run=expired。"""
        _, group_id = bindings
        automation_id, run_id, _ = self._dispatch_first(
            service, tenant_id, group_id, wx_config,
            blocks=[{"type": "text", "text_content": "唯一条"}], authorize=False,
        )
        _expire_run_lease(tenant_id, run_id)
        result = dispatch.runs_reclaim_tick(now=utcnow(), config=wx_config)
        assert result["reclaimed"] == 1
        detail = service.get_run_detail(tenant_id, run_id, "owner-1")
        assert [d["state"] for d in detail["deliveries"]] == ["expired"]
        assert da_runs.get_run(run_id, tenant_id)["state"] == "expired"


# ==================== permits_sweep（R22）====================


class TestPermitsSweep:
    def test_expired_issued_keeps_reservation(self, service, tenant_id, bindings, wx_config, adapter):
        """过期 issued → expired 且额度预留保留（R22：释放仅经 release/settle 两条路）"""
        _, group_id = bindings
        run_at = utcnow()  # 真实时钟锚点（claim_run 按 DB NOW() 判定到期）
        automation_id, _, _ = create_and_publish(
            service, tenant_id, group_id,
            blocks=[{"type": "text", "text_content": "内容"}],
            trigger={"type": "once", "run_at": run_at.isoformat(), "timezone": "UTC"},
        )
        scan_now = run_at + timedelta(seconds=5)
        dispatch.time_scan_tick(now=scan_now, config=wx_config)
        run_id = str(
            service.list_runs(tenant_id, "owner-1", automation_id=automation_id)["items"][0]["id"]
        )
        dispatch.run_dispatch_tick(now=scan_now, config=wx_config)
        inv = _run_invocations(tenant_id, run_id)[0]
        token = generate_claim_token()
        repository.claim_next(inv["device_id"], tenant_id, sha256_hex(token), 300)
        repository.mark_started(str(inv["id"]), tenant_id, sha256_hex(token))
        permit = permits.write_authorize(
            tenant_id=tenant_id, device_id=inv["device_id"], invocation_id=str(inv["id"]),
            claim_token_hash=sha256_hex(token), request_id=inv["arguments_json"]["request_id"],
            target_version=inv["arguments_json"].get("target_version"),
            payload_hash=inv["arguments_json"].get("payload_hash"),
        )

        from src.db.database import get_db_connection

        def _bucket_snapshot():
            with get_db_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT scope_type, scope_id, reserved_count, used_count "
                    "FROM desktop_automation_quota_buckets WHERE tenant_id = %s "
                    "ORDER BY scope_type, scope_id",
                    (tenant_id,),
                )
                return [dict(r) for r in cur.fetchall()]

        before = _bucket_snapshot()
        assert any(b["reserved_count"] >= 1 for b in before), "许可签发后应有预留"

        # 许可 deadline 置过期（真实 DB 时钟）→ 清扫
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE local_tool_operation_permits SET deadline = NOW() - INTERVAL '1 second' "
                "WHERE tenant_id = %s AND id = %s",
                (tenant_id, str(permit["permit_id"])),
            )
            conn.commit()
        result = dispatch.permits_sweep_tick(now=utcnow(), config=wx_config)
        assert result["expired"] >= 1
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT state FROM local_tool_operation_permits WHERE tenant_id = %s AND id = %s",
                (tenant_id, str(permit["permit_id"])),
            )
            assert cur.fetchone()["state"] == "expired"
        assert _bucket_snapshot() == before  # 预留保留（R22）


# ==================== enabled=false 零动作（R42）====================


class TestGating:
    def test_disabled_ticks_zero_db_action(self, service, tenant_id, bindings, wx_config, adapter):
        """enabled=false：四类 tick 全零动作（无 occurrence/run/outbox/invocation 行，
        schedule 不前移）"""
        _, group_id = bindings
        run_at = utcnow() + timedelta(seconds=30)
        automation_id, _, _ = create_and_publish(
            service, tenant_id, group_id,
            blocks=[{"type": "text", "text_content": "内容"}],
            trigger={"type": "once", "run_at": run_at.isoformat(), "timezone": "UTC"},
        )
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, next_fire_at FROM desktop_automation_schedules WHERE tenant_id = %s",
                (tenant_id,),
            )
            schedule_row = dict(cur.fetchone())

        disabled = replace(wx_config, enabled=False)
        now = run_at + timedelta(seconds=600)
        assert dispatch.time_scan_tick(now=now, config=disabled)["accepted"] == 0
        assert dispatch.run_dispatch_tick(now=now, config=disabled)["dispatched"] == 0
        assert dispatch.permits_sweep_tick(now=now, config=disabled)["expired"] == 0
        assert dispatch.runs_reclaim_tick(now=now, config=disabled)["reclaimed"] == 0

        assert _count(tenant_id, "desktop_automation_occurrences") == 0
        assert _count(tenant_id, "desktop_automation_runs") == 0
        assert _count(tenant_id, "desktop_automation_outbox") == 0
        assert _count(tenant_id, "local_tool_invocations") == 0
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT next_fire_at FROM desktop_automation_schedules WHERE id = %s",
                (str(schedule_row["id"]),),
            )
            assert cur.fetchone()["next_fire_at"] == schedule_row["next_fire_at"]  # 未前移

    def test_scheduler_registration_gated_by_enabled(self, monkeypatch):
        """R42：enabled=false 零注册；enabled=true 注册全部 tick job（interval 从配置读；
        P4-A 增 assets_cleanup；P4-B 增 event_match；P5 R59③ 增 retention_cleanup 与
        assets_orphan_scan——后者 tick 内受 assets_orphan_scan_enabled 默认关的门控）"""
        from apscheduler.triggers.interval import IntervalTrigger

        from src.scheduler.manager import ScheduledTaskManager
        from src.weixin_marketing import config as wxm_config_module

        class FakeScheduler:
            def __init__(self):
                self.jobs = {}

            def add_job(self, func, trigger=None, id=None, **kwargs):  # noqa: A002
                self.jobs[id] = {"func": func, "trigger": trigger}
                return type("J", (), {"id": id})()

        manager = ScheduledTaskManager()
        manager._scheduler = FakeScheduler()
        wxm_jobs_prefix = "job_system_weixin_marketing_"

        disabled_cfg = wxm_config_module.WeixinMarketingConfig(enabled=False)
        monkeypatch.setattr(wxm_config_module, "get_weixin_marketing_config", lambda: disabled_cfg)
        manager._register_system_jobs()
        assert not [jid for jid in manager._scheduler.jobs if jid.startswith(wxm_jobs_prefix)]

        enabled_cfg = wxm_config_module.WeixinMarketingConfig(
            enabled=True,
            time_scan_interval_seconds=7,
            dispatch_interval_seconds=11,
            permits_sweep_interval_seconds=31,
            runs_reclaim_interval_seconds=61,
            assets_cleanup_interval_seconds=3601,
            event_match_interval_seconds=13,
            retention_cleanup_interval_seconds=86401,
            assets_orphan_scan_interval_seconds=86403,
        )
        manager._scheduler.jobs.clear()
        monkeypatch.setattr(wxm_config_module, "get_weixin_marketing_config", lambda: enabled_cfg)
        manager._register_system_jobs()
        wxm_jobs = {jid: j for jid, j in manager._scheduler.jobs.items() if jid.startswith(wxm_jobs_prefix)}
        assert set(wxm_jobs) == {
            "job_system_weixin_marketing_time_scan",
            "job_system_weixin_marketing_dispatch",
            "job_system_weixin_marketing_sweep",
            "job_system_weixin_marketing_reclaim",
            "job_system_weixin_marketing_assets_cleanup",
            "job_system_weixin_marketing_event_match",
            "job_system_weixin_marketing_retention_cleanup",
            "job_system_weixin_marketing_assets_orphan_scan",
        }
        assert isinstance(wxm_jobs["job_system_weixin_marketing_time_scan"]["trigger"], IntervalTrigger)
        assert wxm_jobs["job_system_weixin_marketing_time_scan"]["trigger"].interval.total_seconds() == 7
        assert wxm_jobs["job_system_weixin_marketing_dispatch"]["trigger"].interval.total_seconds() == 11
        assert wxm_jobs["job_system_weixin_marketing_sweep"]["trigger"].interval.total_seconds() == 31
        assert wxm_jobs["job_system_weixin_marketing_reclaim"]["trigger"].interval.total_seconds() == 61
        assert (
            wxm_jobs["job_system_weixin_marketing_assets_cleanup"]["trigger"]
            .interval.total_seconds()
            == 3601
        )
        # P4-B：事件匹配 worker（R57；受 event_triggers_enabled 细分门控）
        assert (
            wxm_jobs["job_system_weixin_marketing_event_match"]["trigger"]
            .interval.total_seconds()
            == 13
        )
        # P5 R59③：保留期清理与孤儿素材扫描
        assert (
            wxm_jobs["job_system_weixin_marketing_retention_cleanup"]["trigger"]
            .interval.total_seconds()
            == 86401
        )
        assert (
            wxm_jobs["job_system_weixin_marketing_assets_orphan_scan"]["trigger"]
            .interval.total_seconds()
            == 86403
        )


# ==================== tenant_allowlist 预检（CR-P1-2）====================


class TestTenantAllowlist:
    def _pending_manual_run(self, service, tenant_id, group_id, blocks=None):
        automation_id, _, _ = create_and_publish(
            service, tenant_id, group_id,
            blocks=blocks or [{"type": "text", "text_content": "内容"}],
        )
        _, run_id = manual_run_pending(service, tenant_id, automation_id)
        return automation_id, run_id

    def test_excluded_tenant_run_due_not_driven_zero_change(
        self, service, tenant_id, bindings, wx_config, adapter
    ):
        """allowlist 排除租户：due run_dispatch 条目不被驱动——run/deliveries/
        invocations/permits 表行零变化（run 保持 pending），条目留 processing。"""
        _, group_id = bindings
        automation_id, run_id = self._pending_manual_run(service, tenant_id, group_id)
        excluded = replace(wx_config, tenant_allowlist=["tenant-other"])

        counts_before = (
            _count(tenant_id, "desktop_automation_occurrences"),
            _count(tenant_id, "desktop_automation_deliveries"),
            _count(tenant_id, "local_tool_invocations"),
            _count(tenant_id, "local_tool_operation_permits"),
        )
        stats = dispatch.run_dispatch_tick(now=utcnow(), config=excluded)
        assert stats["claimed"] == 0 and stats["dispatched"] == 0
        assert stats["skipped_tenants"] == 1
        assert da_runs.get_run(run_id, tenant_id)["state"] == "pending"
        assert counts_before == (
            _count(tenant_id, "desktop_automation_occurrences"),
            _count(tenant_id, "desktop_automation_deliveries"),
            _count(tenant_id, "local_tool_invocations"),
            _count(tenant_id, "local_tool_operation_permits"),
        )
        # 条目留 processing（claim 已置位，lease 过期归还 → 短期排除=暂停语义）
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT state FROM desktop_automation_outbox "
                "WHERE tenant_id = %s AND kind = 'run_due'",
                (tenant_id,),
            )
            assert cur.fetchone()["state"] == "processing"

    def test_excluded_tenant_long_exclusion_converges_via_cap(
        self, service, tenant_id, bindings, wx_config, adapter
    ):
        """长期排除（attempt_count 超毒丸上限）：条目 done + 目标 pending run 收敛
        failed（reason=tenant_not_allowed）+ audit——不永久 pending。"""
        _, group_id = bindings
        automation_id, run_id = self._pending_manual_run(service, tenant_id, group_id)
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE desktop_automation_outbox SET attempt_count = %s "
                "WHERE tenant_id = %s AND kind = 'run_due'",
                (dispatch.OUTBOX_MAX_ATTEMPTS, tenant_id),
            )
            conn.commit()
        excluded = replace(wx_config, tenant_allowlist=["tenant-other"])
        stats = dispatch.run_dispatch_tick(now=utcnow(), config=excluded)
        assert stats["skipped_tenants"] == 0  # 超上限走收敛而非跳过
        run = da_runs.get_run(run_id, tenant_id)
        assert run["state"] == "failed"
        assert run["result_json"]["reason"] == "tenant_not_allowed"
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT kind, detail FROM desktop_automation_audit_events "
                "WHERE tenant_id = %s AND kind = 'run_dispatch_poisoned'",
                (tenant_id,),
            )
            audit = cur.fetchone()
            cur.execute(
                "SELECT state FROM desktop_automation_outbox "
                "WHERE tenant_id = %s AND kind = 'run_due'",
                (tenant_id,),
            )
            outbox_state = cur.fetchone()["state"]
        assert audit is not None
        assert audit["detail"]["reason"] == "tenant_not_allowed"
        assert outbox_state == "done"
        assert _count(tenant_id, "local_tool_invocations") == 0  # 零驱动

    def test_excluded_tenant_running_run_not_driven_or_renewed(
        self, service, tenant_id, bindings, wx_config, adapter
    ):
        """排除生效于在途 run：不派发下一条、不续租（lease_expires_at 不变）；
        租约到期后由 reclaim 收敛（已提交条目照实回收）。"""
        _, group_id = bindings
        automation_id, run_id = self._pending_manual_run(
            service, tenant_id, group_id,
            blocks=[
                {"type": "text", "text_content": "第 1 条"},
                {"type": "text", "text_content": "第 2 条"},
            ],
        )
        now = utcnow()
        dispatch.run_dispatch_tick(now=now, config=wx_config)  # delivery 1 已派发
        first = _run_invocations(tenant_id, run_id)
        assert len(first) == 1
        _finish_invocation(tenant_id, first[0])  # verified → delivery 2 可派发

        excluded = replace(wx_config, tenant_allowlist=["tenant-other"])
        lease_before = da_runs.get_run(run_id, tenant_id)["lease_expires_at"]
        stats = dispatch.run_dispatch_tick(now=now + timedelta(seconds=5), config=excluded)
        assert stats["dispatched"] == 0 and stats["renewed"] == 0
        assert stats["skipped_tenants"] == 1
        assert len(_run_invocations(tenant_id, run_id)) == 1  # 第 2 条未派发
        detail = service.get_run_detail(tenant_id, run_id, "owner-1")
        assert [d["state"] for d in detail["deliveries"]] == ["succeeded", "pending"]
        assert da_runs.get_run(run_id, tenant_id)["lease_expires_at"] == lease_before


# ==================== P2-3：毒丸条目收敛 pending run ====================


class TestPoisonConvergence:
    def test_poison_entry_fails_pending_run_with_audit(
        self, service, tenant_id, bindings, wx_config, adapter
    ):
        """允许租户的毒丸条目：done + 目标 pending run failed
        （reason=dispatch_attempts_exceeded）+ audit；零 deliveries/invocations。"""
        _, group_id = bindings
        automation_id, _, _ = create_and_publish(
            service, tenant_id, group_id, blocks=[{"type": "text", "text_content": "内容"}],
        )
        _, run_id = manual_run_pending(service, tenant_id, automation_id)
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE desktop_automation_outbox SET attempt_count = %s "
                "WHERE tenant_id = %s AND kind = 'run_due'",
                (dispatch.OUTBOX_MAX_ATTEMPTS, tenant_id),
            )
            conn.commit()
        stats = dispatch.run_dispatch_tick(now=utcnow(), config=wx_config)
        # 共享 DB 加固：claim_pending_outbox 为全局批量（并行测试包的条目也会进入
        # 本轮 claimed/dispatched 计数），只对本租户条目下界断言；毒丸语义由下方
        # 本租户 run/audit/outbox 断言承载
        assert stats["claimed"] >= 1
        run = da_runs.get_run(run_id, tenant_id)
        assert run["state"] == "failed"
        assert run["result_json"]["reason"] == "dispatch_attempts_exceeded"
        assert run["result_json"]["attempts"] == dispatch.OUTBOX_MAX_ATTEMPTS + 1
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT kind, detail FROM desktop_automation_audit_events "
                "WHERE tenant_id = %s AND kind = 'run_dispatch_poisoned'",
                (tenant_id,),
            )
            audit = cur.fetchone()
            cur.execute(
                "SELECT state FROM desktop_automation_outbox "
                "WHERE tenant_id = %s AND kind = 'run_due'",
                (tenant_id,),
            )
            outbox_state = cur.fetchone()["state"]
        assert audit is not None
        assert audit["detail"]["reason"] == "dispatch_attempts_exceeded"
        assert outbox_state == "done"
        assert _count(tenant_id, "desktop_automation_deliveries") == 0
        assert _count(tenant_id, "local_tool_invocations") == 0

    def test_poison_run_idempotent_when_already_terminal(
        self, service, tenant_id, bindings, wx_config, adapter
    ):
        """毒丸触发时目标 run 已被并发收敛（终态）：不再改判、audit 不重复写 run，
        条目照常 done（finish_run state NOT IN terminal 幂等）。"""
        _, group_id = bindings
        automation_id, _, _ = create_and_publish(
            service, tenant_id, group_id, blocks=[{"type": "text", "text_content": "内容"}],
        )
        _, run_id = manual_run_pending(service, tenant_id, automation_id)
        from src.desktop_automation import runs as runs_mod

        with get_db_connection() as conn:
            cur = conn.cursor()
            runs_mod.finish_run(  # 模拟并发已收敛为 cancelled
                cur, run_id, tenant_id, "cancelled", {"reason": "preempted"}
            )
            cur.execute(
                "UPDATE desktop_automation_outbox SET attempt_count = %s "
                "WHERE tenant_id = %s AND kind = 'run_due'",
                (dispatch.OUTBOX_MAX_ATTEMPTS, tenant_id),
            )
            conn.commit()
        dispatch.run_dispatch_tick(now=utcnow(), config=wx_config)
        run = da_runs.get_run(run_id, tenant_id)
        assert run["state"] == "cancelled"  # 不改判
        assert run["result_json"]["reason"] == "preempted"


# ==================== P2 复审 R50：定向领取 ====================


class TestDirectedClaimDispatch:
    def test_prepare_failure_only_affects_target_run(
        self, service, tenant_id, bindings, wx_config, adapter, monkeypatch
    ):
        """R50 定向领取语义：条目目标 run 的准备段失败（模拟适配器编译异常）只影响
        目标自身——保持 running-零提交，交租约回收按 §5.4 收敛；另一条 pending run
        不被领取、零 deliveries/invocations（无误领，快照归还 hack 已删除）。"""
        _, group_id = bindings
        automation_a, _, _ = create_and_publish(
            service, tenant_id, group_id, blocks=[{"type": "text", "text_content": "A 内容"}],
        )
        automation_b, _, _ = create_and_publish(
            service, tenant_id, group_id, blocks=[{"type": "text", "text_content": "B 内容"}],
        )
        _, run_a = manual_run_pending(service, tenant_id, automation_a)
        _, run_b = manual_run_pending(service, tenant_id, automation_b)
        # 只保留 B 的 run_due 条目：本轮 tick 的领取目标恒为 B（A 无条目驱动）
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM desktop_automation_outbox "
                "WHERE tenant_id = %s AND kind = 'run_due' "
                "AND aggregate_ref = (SELECT occurrence_id::text FROM desktop_automation_runs WHERE id = %s)",
                (tenant_id, str(run_a)),
            )
            conn.commit()

        def failing_prepare(run, **kwargs):
            raise RuntimeError("simulated prepare failure")

        monkeypatch.setattr(dispatch.da_executor, "prepare_claimed_run", failing_prepare)
        stats = dispatch.run_dispatch_tick(now=utcnow(), config=wx_config)  # 不得抛出
        assert stats["dispatched"] == 0
        # 目标 B：定向领取后准备失败——running 零提交（交租约回收收敛，不归还 pending）
        run_b_row = da_runs.get_run(run_b, tenant_id)
        assert run_b_row["state"] == "running"
        assert service.get_run_detail(tenant_id, run_b, "owner-1")["deliveries"] == []
        # 无辜 A：未被领取、零提交（定向领取不触碰非目标 run）
        run_a_row = da_runs.get_run(run_a, tenant_id)
        assert run_a_row["state"] == "pending"
        assert run_a_row["lease_expires_at"] is None
        assert service.get_run_detail(tenant_id, run_a, "owner-1")["deliveries"] == []
        assert _run_invocations(tenant_id, run_a) == []
        # B 的条目留 processing（lease 到期重投；run 已 running，重投时按在途/终态收敛）
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT state FROM desktop_automation_outbox "
                "WHERE tenant_id = %s AND kind = 'run_due'",
                (tenant_id,),
            )
            assert [r["state"] for r in cur.fetchall()] == ["processing"]
        # 租约回收把 running-零提交的 B 收敛为 failed（§5.4：无任何提交按未完成失败）
        _expire_run_lease(tenant_id, run_b)
        assert dispatch.runs_reclaim_tick(now=utcnow(), config=wx_config)["reclaimed"] == 1
        assert da_runs.get_run(run_b, tenant_id)["state"] == "failed"
        assert da_runs.get_run(run_a, tenant_id)["state"] == "pending"

    def test_entry_drives_its_own_target_not_earliest(
        self, service, tenant_id, bindings, wx_config, adapter
    ):
        """R50：outbox 条目按 aggregate_ref（run_id）定向驱动——B 条目存在、A 更早
        pending 但无条目时，本轮只领取/编译 B；A 不被触碰。"""
        _, group_id = bindings
        automation_a, revision_a, _ = create_and_publish(
            service, tenant_id, group_id, blocks=[{"type": "text", "text_content": "A 唯一"}],
        )
        automation_b, revision_b, _ = create_and_publish(
            service, tenant_id, group_id, blocks=[{"type": "text", "text_content": "B 唯一"}],
        )
        _, run_a = manual_run_pending(service, tenant_id, automation_a)
        _, run_b = manual_run_pending(service, tenant_id, automation_b)
        # A 更早到期（领取序在前的旧语义会先领 A），只保留 B 的条目
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE desktop_automation_runs SET due_at = NOW() - INTERVAL '10 seconds' "
                "WHERE tenant_id = %s AND id = %s",
                (tenant_id, str(run_a)),
            )
            cur.execute(
                "DELETE FROM desktop_automation_outbox "
                "WHERE tenant_id = %s AND kind = 'run_due' "
                "AND aggregate_ref = (SELECT occurrence_id::text FROM desktop_automation_runs WHERE id = %s)",
                (tenant_id, str(run_a)),
            )
            conn.commit()

        stats = dispatch.run_dispatch_tick(now=utcnow(), config=wx_config)
        assert stats["claimed"] == 1 and stats["dispatched"] == 1
        # B 被领取并编译（deliveries 属 B 自身 revision 的内容块）
        detail_b = service.get_run_detail(tenant_id, run_b, "owner-1")["deliveries"]
        assert detail_b and detail_b[0]["state"] == "dispatched"
        own_hash = _own_block_hashes(tenant_id, revision_b)
        assert detail_b[0]["payload_hash"] in own_hash
        # A 未被领取（pending、无条目）
        assert da_runs.get_run(run_a, tenant_id)["state"] == "pending"
        assert service.get_run_detail(tenant_id, run_a, "owner-1")["deliveries"] == []

    def test_due_at_tie_no_innocent_run_failed(
        self, service, tenant_id, bindings, wx_config, adapter
    ):
        """真实 tie 分歧不变量：两 run（不同 revision）强制同 due_at，多轮 tick 后
        无 run 落 failed/unknown；任何已编译 deliveries 的 payload_hash 必属其自身
        revision 的内容块（无误配编译产物）。"""
        _, group_id = bindings
        automation_a, revision_a, _ = create_and_publish(
            service, tenant_id, group_id, blocks=[{"type": "text", "text_content": "A 唯一"}],
        )
        automation_b, revision_b, _ = create_and_publish(
            service, tenant_id, group_id, blocks=[{"type": "text", "text_content": "B 唯一"}],
        )
        _, run_a = manual_run_pending(service, tenant_id, automation_a)
        _, run_b = manual_run_pending(service, tenant_id, automation_b)
        same_due = utcnow().replace(microsecond=0)
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE desktop_automation_runs SET due_at = %s "
                "WHERE tenant_id = %s AND id IN (%s, %s)",
                (same_due, tenant_id, str(run_a), str(run_b)),
            )
            conn.commit()

        # P2-4：内置 10 轮（连跑证据固化进用例自身——tie 分歧是排序不确定性，
        # 单轮 3 次不足以覆盖领取序抖动）
        for _ in range(10):
            dispatch.run_dispatch_tick(now=utcnow(), config=wx_config)

        own_hashes = {
            str(run_id): hashes
            for run_id, revision_id in ((run_a, revision_a), (run_b, revision_b))
            for hashes in [_own_block_hashes(tenant_id, revision_id)]
        }
        for run_id in (run_a, run_b):
            run = da_runs.get_run(run_id, tenant_id)
            assert run["state"] not in ("failed", "unknown"), run["state"]
            if run["state"] == "running":
                cur_deliveries = service.get_run_detail(tenant_id, run_id, "owner-1")["deliveries"]
                assert cur_deliveries, "running run 应有已编译条目（非空转）"
                for d in cur_deliveries:
                    assert d["payload_hash"] in own_hashes[str(run_id)]


def _own_block_hashes(tenant_id: str, revision_id: str) -> set:
    """revision 自身内容块的 payload_hash 集合（误配编译判据）"""
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT payload_hash FROM bs_weixin_marketing_content_blocks "
            "WHERE tenant_id = %s AND revision_id = %s",
            (tenant_id, str(revision_id)),
        )
        return {r["payload_hash"] for r in cur.fetchall()}


# ==================== 批次有界 + 单条异常隔离 ====================


class TestBatchIsolation:
    def test_one_run_error_does_not_block_batch(self, service, tenant_id, bindings, wx_config, adapter, monkeypatch):
        """两条 run_due 条目：第一条驱动抛错 → 留 processing 待重投；第二条照常派发。"""
        _, group_id = bindings
        automation_a, _, _ = create_and_publish(
            service, tenant_id, group_id, blocks=[{"type": "text", "text_content": "A 内容"}],
        )
        automation_b, _, _ = create_and_publish(
            service, tenant_id, group_id, blocks=[{"type": "text", "text_content": "B 内容"}],
        )
        _, run_a = manual_run_pending(service, tenant_id, automation_a)
        _, run_b = manual_run_pending(service, tenant_id, automation_b)

        original = dispatch.da_executor.execute_next_delivery

        def flaky(run, **kwargs):
            if str(run["id"]) == str(run_a):
                raise RuntimeError("boom: 模拟单 run 驱动异常")
            return original(run, **kwargs)

        monkeypatch.setattr(dispatch.da_executor, "execute_next_delivery", flaky)
        stats = dispatch.run_dispatch_tick(now=utcnow(), config=wx_config)  # 不得抛出
        assert stats["dispatched"] == 1
        assert len(_run_invocations(tenant_id, run_b)) == 1  # B 正常派发
        assert _run_invocations(tenant_id, run_a) == []  # A 未派发（异常隔离）
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT kind, state FROM desktop_automation_outbox "
                "WHERE tenant_id = %s AND kind = 'run_due' ORDER BY created_at",
                (tenant_id,),
            )
            states = [r["state"] for r in cur.fetchall()]
        assert states == ["processing", "done"]  # A 留 processing（lease 到期重投），B 完成


# ==================== R45：dedupe per-attempt ====================


class TestPerAttemptDedupe:
    def test_first_dispatch_key_form_and_retry_keyspace(self, service, tenant_id, bindings, wx_config, adapter):
        """首派 dedupe_key = delivery:{id}:a:1（与场景重试键 delivery:{id}:a:2 同一空间）；
        重投不放大（同键幂等复用旧 invocation）。"""
        _, group_id = bindings
        automation_id, _, _ = create_and_publish(
            service, tenant_id, group_id, blocks=[{"type": "text", "text_content": "唯一内容"}],
        )
        _, run_id = manual_run_pending(service, tenant_id, automation_id)
        dispatch.run_dispatch_tick(now=utcnow(), config=wx_config)
        invocations = _run_invocations(tenant_id, run_id)
        assert len(invocations) == 1
        delivery_id = invocations[0]["arguments_json"]["delivery_id"]
        assert invocations[0]["dedupe_key"] == f"delivery:{delivery_id}:a:1"

        # 人工重试（先到 failed；机器安全回执须显式 safe_to_retry=true）：新 attempt
        # 键 = delivery:{id}:a:2（同键空间、不冲突）
        failed = _finish_invocation(
            tenant_id, invocations[0], effect="none", phase="prepared",
            safe_to_retry=True,
        )
        assert failed["state"] == "failed"
        retry = service.retry_delivery(tenant_id, delivery_id, "owner-1", confirm=True)
        assert retry["attempt_no"] == 2
        retry_invocations = [
            i for i in _run_invocations(tenant_id, run_id)
            if str(i["id"]) == retry["invocation_id"]
        ]
        assert retry_invocations[0]["dedupe_key"] == f"delivery:{delivery_id}:a:2"

        # 首派键幂等：同键 enqueue 复用同一 invocation（崩溃重派不放大）
        from src.local_tools.service import LocalInvocationService

        reused = LocalInvocationService().enqueue(
            tenant_id=tenant_id, user_id="owner-1",
            device_id=invocations[0]["device_id"],
            tool_name=invocations[0]["arguments_json"]["operation"],
            arguments=invocations[0]["arguments_json"],
            provider_key="weixin", business_kind="desktop_automation",
            business_ref={"delivery_id": delivery_id},
            dedupe_key=f"delivery:{delivery_id}:a:1",
        )
        assert str(reused["id"]) == str(invocations[0]["id"])

    def test_dispatch_idempotent_on_redrive(self, service, tenant_id, bindings, wx_config, adapter):
        """run 在途时重复 tick 不重复派发（dedupe + next_executable 双重收敛）。"""
        _, group_id = bindings
        automation_id, _, _ = create_and_publish(
            service, tenant_id, group_id,
            blocks=[
                {"type": "text", "text_content": "第 1 条"},
                {"type": "text", "text_content": "第 2 条"},
            ],
        )
        _, run_id = manual_run_pending(service, tenant_id, automation_id)
        now = utcnow()
        dispatch.run_dispatch_tick(now=now, config=wx_config)
        dispatch.run_dispatch_tick(now=now, config=wx_config)  # 在途重复 tick
        dispatch.run_dispatch_tick(now=now, config=wx_config)
        assert len(_run_invocations(tenant_id, run_id)) == 1
