"""执行链测试 harness：搭起「v2 invocation（running）+ 绑定 delivery/attempt」的最小现场"""

import uuid
from datetime import datetime, timedelta, timezone

from src.desktop_automation import executor, occurrences, runs, subjects
from src.desktop_automation.adapters import TrustedAdapterRegistry
from src.local_tools import repository
from src.local_tools.security import generate_claim_token, sha256_hex
from tests.unit.desktop_automation.fakes import FakeScenarioAdapter


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def setup_scenario(
    tenant_id,
    *,
    operations=None,
    payloads=None,
    quota_limit=10,
    authorize_allowed=True,
    authorize_reason=None,
    task="task-1",
    rev="rev-1",
    adapter=None,
):
    """注册假适配器 + 发布 task/revision。返回 adapter。"""
    adapter = adapter or FakeScenarioAdapter(
        operations=operations or [],
        payloads=payloads or {},
        quota_limit=quota_limit,
        authorize_allowed=authorize_allowed,
        authorize_reason=authorize_reason,
    )
    TrustedAdapterRegistry.register(adapter)
    subjects.publish_revision(
        tenant_id, adapter.scenario_key, task, rev, "owner-1",
        revision_config={}, schedule_specs=[],
    )
    return adapter


def default_operations(payloads=None):
    payloads = payloads if payloads is not None else {
        "payload:1": b"hello-1",
        "payload:2": b"hello-2",
    }
    return [
        {"position": 1, "operation": "fake_op_one", "target_ref": "target-1"},
        {"position": 2, "operation": "fake_op_two", "target_ref": "target-2",
         "payload_ref": "payload:2"},
    ], payloads


def build_running_v2_invocation(
    tenant_id,
    *,
    adapter=None,
    operations=None,
    payloads=None,
    quota_limit=10,
    authorize_allowed=True,
    expires_at=None,
):
    """手动触发 → claim_and_prepare → execute 第 1 条 → 模拟设备 claim+started。

    返回 {run, device_id, delivery, invocation_id, attempt, claim_token, adapter, arguments}。
    """
    if operations is None or payloads is None:
        default_ops, default_payloads = default_operations()
        operations = operations if operations is not None else default_ops
        payloads = payloads if payloads is not None else default_payloads
    adapter = setup_scenario(
        tenant_id, operations=operations, payloads=payloads,
        quota_limit=quota_limit, authorize_allowed=authorize_allowed,
        adapter=adapter,
    )
    trigger = occurrences.accept_manual_trigger(
        tenant_id=tenant_id, scenario_key=adapter.scenario_key, task_ref="task-1",
        request_id=f"req-{uuid.uuid4().hex[:8]}", user_id="owner-1", now=utcnow(),
        expires_at=expires_at or (utcnow() + timedelta(hours=1)),
    )
    assert trigger["created"] is True
    device_id = str(uuid.uuid4())
    prepared = executor.claim_and_prepare_run(
        revision_config={}, device_id=device_id, tenant_id=tenant_id, lease_seconds=300,
    )
    assert prepared and prepared["prepared"] is True, prepared
    run = prepared["run"]
    stepped = executor.execute_next_delivery(run)
    assert stepped is not None, "应派发第 1 条 delivery"
    invocation_id = stepped["invocation_id"]

    claim_token = generate_claim_token()
    claimed = repository.claim_next(device_id, tenant_id, sha256_hex(claim_token), 300)
    assert claimed is not None and str(claimed["id"]) == invocation_id
    started = repository.mark_started(invocation_id, tenant_id, sha256_hex(claim_token))
    assert started and started["state"] == "running"
    run = runs.get_run(str(run["id"]), tenant_id)
    return {
        "run": run,
        "device_id": device_id,
        "delivery": stepped["delivery"],
        "invocation_id": invocation_id,
        "attempt_id": stepped["attempt_id"],
        "claim_token": claim_token,
        "adapter": adapter,
        "arguments": claimed["arguments_json"],
        "request_id": stepped["request_id"],
    }


def latest_bucket(tenant_id, scope_type, scope_id):
    """取该 scope 最近的 quota 桶（T-P2-3：按 bucket_start DESC 取，杜绝整点边界查错桶）"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT tenant_id, scope_type, scope_id, bucket_start, window_seconds,
                   limit_count, reserved_count, used_count
            FROM desktop_automation_quota_buckets
            WHERE tenant_id = %s AND scope_type = %s AND scope_id = %s
            ORDER BY bucket_start DESC
            LIMIT 1
            """,
            (tenant_id, scope_type, scope_id),
        )
        row = cur.fetchone()
        return dict(row) if row else None
