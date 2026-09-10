"""受限租户端到端发布检查（P5 R59①：tenant_allowlist 单租户 enabled=true 下完整
调度周期观察）

发布检查（微信计划 §10「发布检查」段 + R59①③）：
- 建绑定 → 发布（time 触发近未来）→ time_scan/dispatch/sweep/reclaim/event_match/
  assets_cleanup/retention_cleanup 各 tick 至少一轮（now 注入，不触真实调度器）；
- 假设备驱动（claim/start/write-authorize/operation-result verified）→ runs 终态；
- 密钥轮换 retiring→retired 收敛随 event_match tick 观察一轮；
- allowlist 外租户零动作（时间扫描 SQL 下推过滤：due schedule 不接纳、无 occurrence/
  run/invocation）。
"""

import uuid
from dataclasses import replace
from datetime import timedelta

import pytest

from src.desktop_automation import runs as da_runs
from src.local_tools import permits, repository
from src.local_tools.operation_result import apply_operation_result
from src.local_tools.security import generate_claim_token, sha256_hex
from src.weixin_marketing import dispatch
from src.weixin_marketing import event_sources as wxm_sources
from tests.unit.weixin_marketing.conftest import (
    cleanup_weixin_tenant,
    create_and_publish,
    manual_run_pending,
    utcnow,
)

pytestmark = pytest.mark.integration

TERMINAL_RUN_STATES = da_runs.RUN_TERMINAL_STATES


def _tables_ready() -> bool:
    from src.db.database import get_db_connection

    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name='bs_weixin_marketing_automations'"
            )
            return cur.fetchone() is not None
    except Exception:
        return False


@pytest.fixture(scope="module")
def _module_tables(init_db_pool):
    if not _tables_ready():
        pytest.skip("bs_weixin_marketing 表未初始化（先运行 init_database）")
    from src.weixin_marketing import event_sources as wxm_sources
    from src.weixin_marketing import internal_event_example as wxm_example

    wxm_sources.ensure_event_source_tables()
    wxm_example.ensure_example_tables()


def _cleanup_tenant(tenant_id: str) -> None:
    from tests.unit.desktop_automation.conftest import cleanup_tenant as cleanup_da_tables
    from tests.unit.weixin_marketing.conftest import _cleanup_webhook_nonces

    try:
        _cleanup_webhook_nonces(tenant_id)
    except Exception:
        pass
    cleanup_da_tables(tenant_id)
    cleanup_weixin_tenant(tenant_id)


@pytest.fixture()
def tenants(_module_tables):
    """allowlisted 租户 A + allowlist 外租户 B（测后双清）"""
    tenant_a = f"wxm_rollout_a_{uuid.uuid4().hex[:10]}"
    tenant_b = f"wxm_rollout_b_{uuid.uuid4().hex[:10]}"
    yield tenant_a, tenant_b
    _cleanup_tenant(tenant_a)
    _cleanup_tenant(tenant_b)


@pytest.fixture()
def rollout_config(_module_tables):
    """受限租户配置：enabled=true，allowlist 仅含租户 A"""
    from src.weixin_marketing.config import get_weixin_marketing_config

    return replace(
        get_weixin_marketing_config(),
        enabled=True,
        time_triggers_enabled=True,
        event_triggers_enabled=True,
    )


@pytest.fixture()
def adapter(rollout_config):
    from src.desktop_automation.adapters import TrustedAdapterRegistry
    from src.weixin_marketing.adapters import WeixinFixedContentAdapter

    instance = WeixinFixedContentAdapter(config=rollout_config)
    TrustedAdapterRegistry.register(instance)
    yield instance
    TrustedAdapterRegistry.unregister(instance.scenario_key)


def _make_bindings(tenant_id):
    from src.db.database import get_db_connection

    account_id = str(uuid.uuid4())
    group_id = str(uuid.uuid4())
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO bs_weixin_marketing_account_bindings
                (id, tenant_id, user_id, device_id, account_anchor_ref, session_epoch, status)
            VALUES (%s, %s, 'owner-1', %s, 'anchor-1', 3, 'active')
            """,
            (account_id, tenant_id, str(uuid.uuid4())),
        )
        cur.execute(
            """
            INSERT INTO bs_weixin_marketing_group_bindings
                (id, tenant_id, user_id, device_id, account_binding_id, label,
                 identity_evidence_ref, identity_version, state, verified_at)
            VALUES (%s, %s, 'owner-1', %s, %s, '发布检查群', 'ev-1', '7', 'complete', NOW())
            """,
            (group_id, tenant_id, str(uuid.uuid4()), account_id),
        )
        conn.commit()
    return group_id


def _count(tenant_id, table, extra=""):
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            f"SELECT COUNT(*) AS c FROM {table} WHERE tenant_id = %s{extra}", (tenant_id,)
        )
        return int(cur.fetchone()["c"])


def _run_invocations(tenant_id, run_id):
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, device_id, arguments_json, state
            FROM local_tool_invocations
            WHERE tenant_id = %s AND business_kind = 'desktop_automation'
              AND business_ref->>'run_id' = %s
            ORDER BY created_at
            """,
            (tenant_id, str(run_id)),
        )
        return [dict(r) for r in cur.fetchall()]


def _finish_invocation(tenant_id, inv):
    """假设备完整执行一条 v2 invocation：claim → start → 许可 → verified 结果"""
    args = inv["arguments_json"]
    device_id = inv["device_id"]
    token = generate_claim_token()
    claimed = repository.claim_next(device_id, tenant_id, sha256_hex(token), 300)
    assert claimed is not None and str(claimed["id"]) == str(inv["id"])
    started = repository.mark_started(str(inv["id"]), tenant_id, sha256_hex(token))
    assert started is not None and started["state"] == "running"
    permit = permits.write_authorize(
        tenant_id=tenant_id, device_id=device_id, invocation_id=str(inv["id"]),
        claim_token_hash=sha256_hex(token), request_id=args["request_id"],
        target_version=args.get("target_version"), payload_hash=args.get("payload_hash"),
    )
    return apply_operation_result(
        tenant_id=tenant_id, device_id=device_id, invocation_id=str(inv["id"]),
        claim_token_hash=sha256_hex(token), request_id=args["request_id"],
        effect="applied", phase="verified",
        evidence_ref=f"weixin-evidence:{args['request_id']}:1",
        permit_id=permit["permit_id"], permit_token=permit["permit_token"],
    )


def _drive_to_terminal(tenant_id, run_id, cfg, now):
    for _ in range(12):
        run = da_runs.get_run(run_id, tenant_id)
        if run["state"] in TERMINAL_RUN_STATES:
            return run["state"]
        dispatch.run_dispatch_tick(now=now, config=cfg)
        for inv in _run_invocations(tenant_id, run_id):
            if inv["state"] != "queued":
                continue
            _finish_invocation(tenant_id, inv)
    run = da_runs.get_run(run_id, tenant_id)
    pytest.fail(f"run 未在预期步数内到终态: state={run['state']}")
    return run["state"]


class TestRestrictedTenantRolloutE2E:
    def test_full_cycle_single_allowlisted_tenant(self, tenants, rollout_config, adapter):
        """受限租户完整周期：publish → time_scan → dispatch+假设备（verified×2）→
        sweep/reclaim/event_match（含轮换收敛）/assets_cleanup/retention 各一轮；
        allowlist 外租户全流程零动作。"""
        from src.db.database import get_db_connection
        from src.weixin_marketing.service import WeixinMarketingService

        tenant_a, tenant_b = tenants
        cfg = replace(rollout_config, tenant_allowlist=[tenant_a])  # 受限租户：仅 A
        service = WeixinMarketingService()

        # --- 建绑定（两租户）---
        group_a = _make_bindings(tenant_a)
        group_b = _make_bindings(tenant_b)

        # --- 发布（time 触发近未来；A=2 块内容，B=1 块）---
        # run_at 取当前真实时刻：occurrence/run 的 due_at 用真实 DB 时钟判定领取
        # （claim_run WHERE due_at <= NOW()），假时钟只用于扫描侧（宽限内）断言
        t0 = utcnow()
        run_at = t0 + timedelta(seconds=1)
        trigger = {"type": "once", "run_at": run_at.isoformat(), "timezone": "UTC"}
        blocks_a = [
            {"type": "text", "text_content": "发布检查第一条"},
            {"type": "link", "url": "https://example.com/rollout"},
        ]
        automation_a, _, _ = create_and_publish(
            service, tenant_a, group_a, trigger=trigger, blocks=blocks_a
        )
        blocks_b = [{"type": "text", "text_content": "排除租户内容"}]
        create_and_publish(
            service, tenant_b, group_b, trigger=trigger, blocks=blocks_b
        )

        # --- 密钥轮换（event_match tick 观察项：retiring → retired 收敛）---
        source = wxm_sources.create_event_source(
            tenant_id=tenant_a, user_id="owner-1",
            source_ref=f"rollout-hook-{uuid.uuid4().hex[:8]}", source_type="webhook",
        )
        wxm_sources.rotate_key(
            tenant_id=tenant_a, source_id=source["source"]["id"],
            user_id="owner-1", rotate_window_seconds=900,
        )

        # --- time_scan tick（now=t0+10s：两租户 schedule 均 due，仅 A 接纳）---
        t1 = t0 + timedelta(seconds=10)
        scan = dispatch.time_scan_tick(now=t1, config=cfg)
        assert scan["enabled"] is True and scan["accepted"] >= 1
        assert _count(tenant_a, "desktop_automation_occurrences") == 1
        assert _count(tenant_b, "desktop_automation_occurrences") == 0, "allowlist 外租户被接纳"
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, state FROM desktop_automation_runs WHERE tenant_id = %s",
                (tenant_a,),
            )
            rows = [dict(r) for r in cur.fetchall()]
        assert len(rows) == 1 and rows[0]["state"] == "pending"
        run_id_a = str(rows[0]["id"])
        assert _count(tenant_b, "desktop_automation_runs") == 0

        # --- dispatch tick × 假设备：A 到终态 succeeded（2 条 verified）---
        final_state = _drive_to_terminal(tenant_a, run_id_a, cfg, t1)
        assert final_state == "succeeded"
        detail = service.get_run_detail(tenant_a, run_id_a, "owner-1")
        assert [d["state"] for d in detail["deliveries"]] == ["succeeded", "succeeded"]
        # allowlist 外租户：invocation/许可零动作
        assert _count(tenant_b, "local_tool_invocations", extra=" AND business_kind='desktop_automation'") == 0
        assert _count(tenant_b, "local_tool_operation_permits") == 0

        # --- reclaim tick 观察：第二个 run（A）领取后失联 → 租约过期收敛 ---
        automation2, _, _ = create_and_publish(
            service, tenant_a, group_a,
            trigger={"type": "once", "run_at": (t0 + timedelta(hours=2)).isoformat(),
                     "timezone": "UTC"},
            blocks=[{"type": "text", "text_content": "回收观察"}],
        )
        _, run2_id = manual_run_pending(service, tenant_a, automation2)
        dispatch.run_dispatch_tick(now=t1, config=cfg)  # 领取 + 首条派发（queued invocation）
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE desktop_automation_runs SET lease_expires_at = %s - INTERVAL '1 hour' "
                "WHERE tenant_id = %s AND id = %s",
                (t1, tenant_a, run2_id),
            )
            conn.commit()
        reclaim = dispatch.runs_reclaim_tick(now=t1, batch=100, config=cfg)
        assert reclaim["enabled"] is True and reclaim["reclaimed"] >= 1
        run2 = da_runs.get_run(run2_id, tenant_a)
        assert run2["state"] in TERMINAL_RUN_STATES
        assert run2["state"] == "expired"  # 设备从未领取：未提交条目按截止口径 expired

        # --- sweep tick（许可清扫一轮；主链许可已 consumed，expired 仅统计）---
        sweep = dispatch.permits_sweep_tick(now=t1, config=cfg)
        assert sweep["enabled"] is True

        # --- event_match tick 一轮（含轮换收敛观察：过窗 retiring → retired）---
        t3 = utcnow() + timedelta(seconds=1000)  # > rotate 时刻 + 900s 窗
        match = dispatch.event_match_tick(now=t3, config=cfg)
        assert match["enabled"] is True
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT status FROM weixin_marketing_event_source_keys "
                "WHERE tenant_id = %s ORDER BY key_version",
                (tenant_a,),
            )
            key_statuses = [r["status"] for r in cur.fetchall()]
        assert key_statuses == ["retired", "active"], key_statuses

        # --- assets_cleanup / retention_cleanup / orphan_scan tick 各一轮（零动作观察）---
        assets = dispatch.assets_cleanup_tick(now=t3, config=cfg)
        assert assets["enabled"] is True
        retention = dispatch.retention_cleanup_tick(now=t3, config=cfg)
        assert retention["enabled"] is True
        orphan = dispatch.assets_orphan_scan_tick(
            config=replace(cfg, assets_orphan_scan_enabled=True)
        )
        assert orphan["scan_enabled"] is True and orphan["orphan_count"] == 0

        # --- 终局断言：A 两个 run 均终态；B 全程零 occurrence/run/invocation ---
        assert da_runs.get_run(run_id_a, tenant_a)["state"] == "succeeded"
        assert _count(tenant_b, "desktop_automation_occurrences") == 0
        assert _count(tenant_b, "desktop_automation_runs") == 0
        assert _count(tenant_b, "local_tool_invocations", extra=" AND business_kind='desktop_automation'") == 0
