"""P4-B 事件匹配 worker + 内部事件示例全链测试（R57/R58）

覆盖：eligible 快照匹配 → occurrence/run（含 condition 命中/不命中 skipped 原因
持久）、暂停/版本切换 skipped、due_at=received_at+delay、cursor 断点续跑（分页
重启不漏不重）、多事件分页、乱序、重复事件不重执行、门控零动作、示例业务
源侧 outbox 同事务 → 投递器 → events → 匹配 → occurrence/run 假设备驱动至终态。
"""

import json
import uuid
from datetime import timedelta

import pytest

from src.db.database import get_db_connection
from src.desktop_automation import events as da_events
from src.desktop_automation import runs as da_runs
from src.weixin_marketing import dispatch
from src.weixin_marketing import event_sources as wxm_sources
from src.weixin_marketing import internal_event_example as wxm_example
from tests.unit.weixin_marketing.test_dispatch import _drive_to_terminal
from tests.unit.weixin_marketing.conftest import create_and_publish, utcnow

pytestmark = pytest.mark.unit


def _register_source(tenant_id, source_ref, *, allowed=None, source_type="webhook"):
    return wxm_sources.create_event_source(
        tenant_id=tenant_id, user_id="owner-1", source_ref=source_ref,
        source_type=source_type, allowed_event_types=allowed,
    )


def _accept_webhook(tenant_id, source, event_id, *, event_type="order.completed",
                    extra=None, nonce=None):
    from tests.unit.weixin_marketing.webhook_support import post_webhook

    return post_webhook(tenant_id, source, {
        "event_id": event_id, "event_type": event_type, **(extra or {}),
    }, nonce=nonce)


def _event_row(tenant_id, event_id):
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT e.* FROM desktop_automation_events e
            JOIN desktop_automation_event_sources s ON s.id = e.source_id
            WHERE e.tenant_id = %s AND e.external_event_id = %s
            """,
            (tenant_id, event_id),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def _occurrence_count(tenant_id, task_ref):
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) AS c FROM desktop_automation_occurrences "
            "WHERE tenant_id = %s AND task_ref = %s",
            (tenant_id, str(task_ref)),
        )
        return int(cur.fetchone()["c"])


def _event_match_skips(tenant_id):
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT detail FROM desktop_automation_audit_events "
            "WHERE tenant_id = %s AND kind = 'event_match_skipped'",
            (tenant_id,),
        )
        return [dict(r)["detail"] for r in cur.fetchall()]


@pytest.fixture()
def event_automation(service, tenant_id, bindings, wx_config, adapter):
    """已发布 event 触发自动化（event_type 订阅 order.completed）"""
    _, group_id = bindings

    def _make(trigger=None):
        return create_and_publish(
            service, tenant_id, group_id,
            trigger=trigger or {
                "type": "event", "source_ref": None, "event_type": "order.completed",
                "delay_seconds": 0,
            },
            blocks=[{"type": "text", "text_content": "事件触发内容"}],
        )

    return _make


class TestEventMatchWorker:
    def test_full_match_creates_occurrence_and_run(
        self, tenant_id, wx_config, event_automation
    ):
        source_ref = f"hook-{uuid.uuid4().hex[:8]}"
        source = _register_source(tenant_id, source_ref, allowed=["order.completed"])
        automation_id, revision_id, _ = event_automation({
            "type": "event", "source_ref": source_ref,
            "event_type": "order.completed", "delay_seconds": 0,
        })
        result = _accept_webhook(tenant_id, source, "ev-1")
        assert result["accepted"] and not result["duplicate"]
        # 快照含该订阅
        row = _event_row(tenant_id, "ev-1")
        assert len(row["eligible_revision_refs"]) == 1
        assert row["state"] == "received"

        stats = dispatch.event_match_tick(now=utcnow(), config=wx_config)
        assert stats["matched"] == 1 and stats["processed_events"] == 1
        row = _event_row(tenant_id, "ev-1")
        assert row["state"] == "processed"
        assert _occurrence_count(tenant_id, automation_id) == 1
        # run 已建并进入 outbox 驱动链（run_dispatch_tick 责任）
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id FROM desktop_automation_runs WHERE tenant_id = %s "
                "AND occurrence_id = %s",
                (tenant_id, str(_occurrence_id(tenant_id, automation_id, "ev-1"))),
            )
            assert cur.fetchone() is not None

    def test_duplicate_event_not_reexecuted(self, tenant_id, wx_config, event_automation):
        source_ref = f"hook-{uuid.uuid4().hex[:8]}"
        source = _register_source(tenant_id, source_ref, allowed=["order.completed"])
        automation_id, _, _ = event_automation({
            "type": "event", "source_ref": source_ref,
            "event_type": "order.completed",
        })
        _accept_webhook(tenant_id, source, "ev-dup")
        dispatch.event_match_tick(now=utcnow(), config=wx_config)
        # 同事件重发（不同 nonce）：返回原结果 duplicate=true，不重建 occurrence
        again = _accept_webhook(tenant_id, source, "ev-dup", nonce="n-second")
        assert again["accepted"] and again["duplicate"]
        stats = dispatch.event_match_tick(now=utcnow(), config=wx_config)
        assert stats["matched"] == 0
        assert _occurrence_count(tenant_id, automation_id) == 1

    def test_condition_not_matched_skipped_persisted(
        self, tenant_id, wx_config, event_automation
    ):
        source_ref = f"hook-{uuid.uuid4().hex[:8]}"
        source = _register_source(tenant_id, source_ref, allowed=["order.completed"])
        automation_id, _, _ = event_automation({
            "type": "event", "source_ref": source_ref,
            "event_type": "order.completed",
            "condition": [{"field": "status", "op": "eq", "value": "completed"}],
        })
        # payload status=pending → condition 不命中
        _accept_webhook(tenant_id, source, "ev-cond", extra={"status": "pending"})
        stats = dispatch.event_match_tick(now=utcnow(), config=wx_config)
        assert stats["matched"] == 0 and stats["skipped"] == 1
        skips = _event_match_skips(tenant_id)
        assert any(s.get("reason") == "condition_not_matched" for s in skips)
        assert _occurrence_count(tenant_id, automation_id) == 0
        assert _event_row(tenant_id, "ev-cond")["state"] == "processed"

    def test_condition_matched_triggers(self, tenant_id, wx_config, event_automation):
        source_ref = f"hook-{uuid.uuid4().hex[:8]}"
        source = _register_source(tenant_id, source_ref, allowed=["order.completed"])
        automation_id, _, _ = event_automation({
            "type": "event", "source_ref": source_ref,
            "event_type": "order.completed",
            "condition": [{"field": "status", "op": "eq", "value": "completed"}],
        })
        _accept_webhook(tenant_id, source, "ev-ok", extra={"status": "completed"})
        stats = dispatch.event_match_tick(now=utcnow(), config=wx_config)
        assert stats["matched"] == 1
        assert _occurrence_count(tenant_id, automation_id) == 1

    def test_paused_task_skipped_reason_persisted(
        self, tenant_id, wx_config, event_automation, service
    ):
        from src.weixin_marketing.models import VersionedActionInput

        source_ref = f"hook-{uuid.uuid4().hex[:8]}"
        source = _register_source(tenant_id, source_ref, allowed=["order.completed"])
        automation_id, _, _ = event_automation({
            "type": "event", "source_ref": source_ref,
            "event_type": "order.completed",
        })
        _accept_webhook(tenant_id, source, "ev-paused")
        detail = service.get_automation_detail(tenant_id, automation_id, "owner-1")
        service.pause(tenant_id, automation_id, "owner-1", VersionedActionInput(
            expected_version=detail["automation"]["version"]
        ))
        stats = dispatch.event_match_tick(now=utcnow(), config=wx_config)
        assert stats["matched"] == 0 and stats["skipped"] == 1
        skips = _event_match_skips(tenant_id)
        assert any(
            s.get("reason") in ("task_not_active", "subscription_inactive")
            for s in skips
        )
        assert _occurrence_count(tenant_id, automation_id) == 0
        # skipped 后事件照常收敛 processed（不阻塞后续事件）
        assert _event_row(tenant_id, "ev-paused")["state"] == "processed"

    def test_revision_switch_skipped_no_fallback(
        self, tenant_id, wx_config, event_automation, service, bindings
    ):
        """版本切换：快照里的旧 revision 订阅不再 active → skipped，不转投新版本"""
        source_ref = f"hook-{uuid.uuid4().hex[:8]}"
        source = _register_source(tenant_id, source_ref, allowed=["order.completed"])
        automation_id, rev1, _ = event_automation({
            "type": "event", "source_ref": source_ref,
            "event_type": "order.completed",
        })
        _accept_webhook(tenant_id, source, "ev-switch")
        # 快照后发布新 revision（active 切换；发布后迭代需完整 trigger/blocks/绑定）
        _, group_id = bindings
        detail = service.get_automation_detail(tenant_id, automation_id, "owner-1")
        from src.weixin_marketing.models import DraftUpdateInput, PublishInput

        service.update_draft(
            tenant_id, automation_id, "owner-1", DraftUpdateInput(
                expected_version=detail["automation"]["version"],
                trigger={
                    "type": "event", "source_ref": source_ref,
                    "event_type": "order.completed",
                },
                blocks=[{"type": "text", "text_content": "v2 内容"}],
                group_binding_id=group_id,
            ),
        )
        detail2 = service.get_automation_detail(tenant_id, automation_id, "owner-1")
        service.publish(tenant_id, automation_id, "owner-1", PublishInput(
            expected_version=detail2["automation"]["version"]
        ))
        stats = dispatch.event_match_tick(now=utcnow(), config=wx_config)
        assert stats["matched"] == 0
        skips = _event_match_skips(tenant_id)
        assert any(
            s.get("reason") in ("revision_switched", "subscription_inactive")
            for s in skips
        )
        assert _occurrence_count(tenant_id, automation_id) == 0

    def test_due_at_is_received_plus_delay(self, tenant_id, wx_config, event_automation):
        source_ref = f"hook-{uuid.uuid4().hex[:8]}"
        source = _register_source(tenant_id, source_ref, allowed=["order.completed"])
        automation_id, _, _ = event_automation({
            "type": "event", "source_ref": source_ref,
            "event_type": "order.completed", "delay_seconds": 120,
        })
        _accept_webhook(tenant_id, source, "ev-delay")
        dispatch.event_match_tick(now=utcnow(), config=wx_config)
        row = _event_row(tenant_id, "ev-delay")
        occ = _occurrence_row(tenant_id, automation_id, "ev-delay")
        received = row["received_at"].replace(tzinfo=None) if row["received_at"].tzinfo is None else row["received_at"]
        due = occ["due_at"].replace(tzinfo=None) if occ["due_at"].tzinfo is None else occ["due_at"]
        delta = (due - received).total_seconds()
        assert 119 <= delta <= 121, f"due_at 应为 received_at+120s，实际 {delta}s"

    def test_cursor_resume_across_restart_no_loss_no_dup(
        self, tenant_id, wx_config, event_automation
    ):
        """分页重启恢复：多候选单事件 cursor 断点 + 批量事件分页（batch 截断）"""
        source_ref = f"hook-{uuid.uuid4().hex[:8]}"
        source = _register_source(tenant_id, source_ref, allowed=["order.completed"])
        automation_id, _, _ = event_automation({
            "type": "event", "source_ref": source_ref,
            "event_type": "order.completed",
        })
        # 3 个事件乱序接纳（arrival 顺序与 event_id 无关）
        for eid in ("ev-c", "ev-a", "ev-b"):
            assert _accept_webhook(tenant_id, source, eid)["accepted"]
        # 第一轮 batch=2（模拟分页/中断）：处理 2 个，剩 1 个
        stats1 = dispatch.event_match_tick(now=utcnow(), batch=2, config=wx_config)
        assert stats1["scanned"] == 2 and stats1["matched"] == 2
        # 第二轮全量：处理剩余 + 无重复
        stats2 = dispatch.event_match_tick(now=utcnow(), config=wx_config)
        assert stats2["matched"] == 1
        assert _occurrence_count(tenant_id, automation_id) == 3
        for eid in ("ev-a", "ev-b", "ev-c"):
            assert _event_row(tenant_id, eid)["state"] == "processed"
        # 第三轮空转：无新事件
        stats3 = dispatch.event_match_tick(now=utcnow(), config=wx_config)
        assert stats3["scanned"] == 0 and stats3["matched"] == 0

    def test_cursor_recovery_mid_event(self, tenant_id, wx_config, event_automation):
        """单事件内 cursor 断点：模拟「第 1 候选已处理、进程崩溃」，重跑只处理
        第 2 候选——候选总数恰处理一次（不重不漏）。"""
        source_ref = f"hook-{uuid.uuid4().hex[:8]}"
        source = _register_source(tenant_id, source_ref, allowed=["order.completed"])
        automation1, _, _ = event_automation({
            "type": "event", "source_ref": source_ref,
            "event_type": "order.completed",
        })
        # ev-mid 先接纳：快照仅 automation1 订阅（1 候选）
        _accept_webhook(tenant_id, source, "ev-mid")
        # 第二个任务以 * 订阅同源——此后接纳的 ev-mid2 快照有 2 候选
        automation2, _, _ = event_automation({
            "type": "event", "source_ref": source_ref, "event_type": "*",
        })
        _accept_webhook(tenant_id, source, "ev-mid2", event_type="order.completed")
        # 模拟崩溃：ev-mid2 处理完第 1 候选后进程死亡——cursor=1
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE desktop_automation_events SET match_cursor = 1, state = 'processing' "
                "WHERE tenant_id = %s AND external_event_id = 'ev-mid2'",
                (tenant_id,),
            )
            conn.commit()
        stats = dispatch.event_match_tick(now=utcnow(), config=wx_config)
        # ev-mid 1 候选 + ev-mid2 断点续跑 1 候选（第 1 候选不重复处理）
        assert stats["matched"] == 2
        assert _event_row(tenant_id, "ev-mid")["state"] == "processed"
        assert _event_row(tenant_id, "ev-mid2")["state"] == "processed"
        # 不重不漏：两事件合计恰 2 个 occurrence，automation1 必得 ev-mid 触发
        total = _occurrence_count(tenant_id, automation1) + _occurrence_count(
            tenant_id, automation2
        )
        assert total == 2
        assert _occurrence_count(tenant_id, automation1) >= 1

    def test_gating_disabled_zero_action(self, tenant_id, wx_config, event_automation):
        from dataclasses import replace

        source_ref = f"hook-{uuid.uuid4().hex[:8]}"
        source = _register_source(tenant_id, source_ref, allowed=["order.completed"])
        automation_id, _, _ = event_automation({
            "type": "event", "source_ref": source_ref,
            "event_type": "order.completed",
        })
        _accept_webhook(tenant_id, source, "ev-gate")
        cfg_off = replace(wx_config, event_triggers_enabled=False)
        stats = dispatch.event_match_tick(now=utcnow(), config=cfg_off)
        assert stats == {"enabled": False, "matched": 0, "skipped": 0, "processed_events": 0}
        assert _event_row(tenant_id, "ev-gate")["state"] == "received"
        cfg_disabled = replace(wx_config, enabled=False)
        assert dispatch.event_match_tick(now=utcnow(), config=cfg_disabled)["enabled"] is False
        # 恢复后正常处理
        stats = dispatch.event_match_tick(now=utcnow(), config=wx_config)
        assert stats["matched"] == 1

    def test_empty_eligible_converges_processed(self, tenant_id, wx_config):
        """无订阅事件（eligible 空）：直接收敛 processed，不阻塞 worker"""
        source_ref = f"hook-{uuid.uuid4().hex[:8]}"
        source = _register_source(tenant_id, source_ref, allowed=["order.completed"])
        _accept_webhook(tenant_id, source, "ev-lonely")
        stats = dispatch.event_match_tick(now=utcnow(), config=wx_config)
        assert stats["processed_events"] == 1
        assert _event_row(tenant_id, "ev-lonely")["state"] == "processed"


class TestInternalExampleFullChain:
    def test_example_order_completed_to_terminal_run(
        self, tenant_id, wx_config, bindings, service, adapter, event_automation
    ):
        """内部示例全链：complete（源侧 outbox 同事务）→ 投递器 → events →
        匹配 worker → occurrence/run → 假设备驱动至终态 succeeded。"""
        wxm_example.register_example_source(tenant_id)
        automation_id, _, _ = event_automation({
            "type": "event", "source_ref": wxm_example.EXAMPLE_SOURCE_REF,
            "event_type": wxm_example.EXAMPLE_EVENT_TYPE,
        })
        order = wxm_example.create_example_order(
            tenant_id=tenant_id, user_id="owner-1", label="订单A",
        )
        # 业务状态变更：业务行 + 源侧 outbox 同事务
        completed = wxm_example.complete_example_order(
            tenant_id=tenant_id, user_id="owner-1", order_id=order["order_id"],
        )
        assert completed["enqueued"] is True
        # 重复完成不重复入队
        again = wxm_example.complete_example_order(
            tenant_id=tenant_id, user_id="owner-1", order_id=order["order_id"],
        )
        assert again["enqueued"] is False

        # 事件尚在 outbox：匹配 worker 先投递再匹配（同 tick 内完成）
        stats = dispatch.event_match_tick(now=utcnow(), config=wx_config)
        assert stats["example_delivered"] == 1
        assert stats["matched"] == 1
        row = _event_row(tenant_id, f"example:{order['order_id']}")
        assert row["state"] == "processed"
        assert row["event_type"] == wxm_example.EXAMPLE_EVENT_TYPE

        # occurrence/run 建立，假设备驱动至终态
        occ_id = _occurrence_id(tenant_id, automation_id, f"example:{order['order_id']}")
        run = _run_by_occurrence(tenant_id, occ_id)
        assert run is not None and run["state"] == "pending"
        final = _drive_to_terminal(tenant_id, str(run["id"]), wx_config, utcnow())
        assert final == "succeeded"

        # 重投幂等：再次触发投递器/匹配零新建
        stats2 = dispatch.event_match_tick(now=utcnow(), config=wx_config)
        assert stats2["example_delivered"] == 0 and stats2["matched"] == 0
        assert _occurrence_count(tenant_id, automation_id) == 1

    def test_example_outbox_redelivery_idempotent(self, tenant_id, wx_config):
        """投递器崩溃恢复：条目手工复位 pending 重投 → 事件不重复（幂等）"""
        wxm_example.register_example_source(tenant_id)
        order = wxm_example.create_example_order(
            tenant_id=tenant_id, user_id="owner-1",
        )
        wxm_example.complete_example_order(
            tenant_id=tenant_id, user_id="owner-1", order_id=order["order_id"],
        )
        r1 = wxm_example.deliver_example_event_outbox()
        assert r1["delivered"] == 1
        # 模拟崩溃残留：done 行复位 pending（真实语义：lease 过期回 pending）
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE desktop_automation_outbox SET state = 'pending' "
                "WHERE tenant_id = %s AND kind = %s",
                (tenant_id, wxm_example.OUTBOX_KIND_EXAMPLE_EVENT),
            )
            conn.commit()
        r2 = wxm_example.deliver_example_event_outbox()
        assert r2["duplicates"] == 1 and r2["failed"] == 0
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) AS c FROM desktop_automation_events WHERE tenant_id = %s",
                (tenant_id,),
            )
            assert int(cur.fetchone()["c"]) == 1


# ==================== helpers ====================


def _occurrence_id(tenant_id, task_ref, external_event_id):
    """按规范触发键查 occurrence（event:{source_ref}:{b64url(sha256(external_event_id))}）"""
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT s.source_ref FROM desktop_automation_event_sources s
            JOIN desktop_automation_events e
              ON e.source_id = s.id AND e.tenant_id = s.tenant_id
            WHERE e.tenant_id = %s AND e.external_event_id = %s
            """,
            (tenant_id, external_event_id),
        )
        row = cur.fetchone()
        assert row is not None, f"事件行未找到: {external_event_id}"
        from src.desktop_automation.constants import event_trigger_key

        key = event_trigger_key(row["source_ref"], external_event_id)
        cur.execute(
            "SELECT id FROM desktop_automation_occurrences "
            "WHERE tenant_id = %s AND task_ref = %s AND trigger_key = %s",
            (tenant_id, str(task_ref), key),
        )
        occ = cur.fetchone()
        assert occ is not None, f"occurrence 未建立 task={task_ref} key={key}"
        return str(occ["id"])


def _occurrence_row(tenant_id, task_ref, external_event_id):
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT o.* FROM desktop_automation_occurrences o
            WHERE o.tenant_id = %s AND o.task_ref = %s
            ORDER BY o.created_at DESC LIMIT 1
            """,
            (tenant_id, str(task_ref)),
        )
        row = cur.fetchone()
        assert row is not None
        return dict(row)


def _run_by_occurrence(tenant_id, occurrence_id):
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, state FROM desktop_automation_runs "
            "WHERE tenant_id = %s AND occurrence_id = %s",
            (tenant_id, str(occurrence_id)),
        )
        row = cur.fetchone()
        return dict(row) if row else None
