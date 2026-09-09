"""occurrences 触发接纳测试（R11 触发键编码 / ON CONFLICT 幂等 / 并发同槽唯一 /
宽限-最近一次 / 一次性 consumed / interval 重启不积压 / 手动不改 schedule）"""

import threading
from datetime import datetime, timedelta, timezone

import pytest

from src.desktop_automation import occurrences, schedules, subjects
from src.desktop_automation.constants import (
    event_trigger_key,
    manual_trigger_key,
    time_trigger_key,
)
from tests.unit.desktop_automation.fakes import FakeScenarioAdapter

pytestmark = pytest.mark.unit

NOW = datetime(2026, 9, 8, 12, 0, 0, tzinfo=timezone.utc)


def _register(tenant_id, **kwargs):
    adapter = FakeScenarioAdapter(**kwargs)
    from src.desktop_automation.adapters import TrustedAdapterRegistry

    TrustedAdapterRegistry.register(adapter)
    return adapter


def _publish(tenant_id, task="task-1", rev="rev-1", specs=None):
    subjects.publish_revision(
        tenant_id, "fake-scenario", task, rev, "owner-1", revision_config={},
        schedule_specs=specs if specs is not None else [],
    )


class TestTriggerKeyEncoding:
    """R11：规范编码 + 外部 ID/request_id 先哈希，杜绝分隔符碰撞"""

    def test_time_key_format(self):
        key = time_trigger_key("rev-1", datetime(2026, 9, 8, 10, 30, 0, tzinfo=timezone.utc))
        assert key == "time:rev-1:2026-09-08T10:30:00Z"

    def test_time_key_naive_treated_as_utc(self):
        assert time_trigger_key("r", datetime(2026, 9, 8, 10, 30, 0)) == "time:r:2026-09-08T10:30:00Z"

    def test_event_key_hash_no_separator_collision(self):
        # external_event_id 含 ':' 时不能与 source_ref 边界产生碰撞
        a = event_trigger_key("s", "x:y")
        b = event_trigger_key("s:x", "y")
        assert a != b
        assert a.startswith("event:s:") and len(a) == len("event:s:") + 43  # b64url(sha256)

    def test_manual_key_hash(self):
        a = manual_trigger_key("req/1")
        b = manual_trigger_key("req/2")
        assert a.startswith("manual:") and a != b
        assert manual_trigger_key("r:1") != manual_trigger_key("r:1:x")

    def test_parse_time_key_roundtrip(self):
        slot = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        parsed = occurrences.time_trigger_key("rev", slot)
        from src.desktop_automation.constants import parse_time_trigger_key

        assert parse_time_trigger_key(parsed) == slot
        assert parse_time_trigger_key("event:s:x") is None


class TestAdmitIdempotent:
    def test_same_slot_second_admission_reuses_occurrence(self, tenant_id):
        _register(tenant_id)
        anchor = NOW - timedelta(minutes=5)
        _publish(tenant_id, specs=[{"kind": "time", "anchor_at": anchor, "interval_seconds": 3600}])
        candidates = schedules.find_due_candidates(NOW)
        assert len(candidates) == 1
        r1 = occurrences.accept_time_slot(candidates[0], NOW)
        assert r1["created"] is True
        # 同槽重复扫描：ON CONFLICT 复用，不再建 run/outbox
        r2 = occurrences.accept_time_slot(candidates[0], NOW)
        assert r2["created"] is False
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) AS c FROM desktop_automation_runs WHERE tenant_id=%s",
                (tenant_id,),
            )
            assert cur.fetchone()["c"] == 1
            cur.execute(
                "SELECT COUNT(*) AS c FROM desktop_automation_outbox WHERE tenant_id=%s",
                (tenant_id,),
            )
            assert cur.fetchone()["c"] == 1

    def test_concurrent_threads_same_slot_single_run(self, tenant_id):
        """并发线程接纳同槽：仅一个 run（SKIP LOCKED + ON CONFLICT 双保险）"""
        _register(tenant_id)
        anchor = NOW - timedelta(minutes=5)
        _publish(tenant_id, specs=[{"kind": "time", "anchor_at": anchor, "interval_seconds": 3600}])
        candidates = schedules.find_due_candidates(NOW)
        results = []
        lock = threading.Lock()

        def worker():
            r = occurrences.accept_time_slot(dict(candidates[0]), NOW)
            with lock:
                results.append(r)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        created = [r for r in results if r.get("created")]
        assert len(created) == 1
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) AS c FROM desktop_automation_runs WHERE tenant_id=%s", (tenant_id,))
            assert cur.fetchone()["c"] == 1


class TestGraceAndMiss:
    def test_past_grace_missed_not_accepted(self, tenant_id):
        """停机多槽且最近槽超宽限：missed 审计 + 不接纳 + next_fire 前移到未来"""
        _register(tenant_id)
        anchor = NOW - timedelta(hours=2, minutes=30)
        _publish(tenant_id, specs=[{
            "kind": "time", "anchor_at": anchor, "interval_seconds": 3600, "grace_seconds": 0,
        }])
        candidates = schedules.find_due_candidates(NOW)
        r = occurrences.accept_time_slot(candidates[0], NOW)
        assert r["accepted"] is False
        assert r["reason"] == "missed"
        assert r["missed_count"] == 2
        row = schedules.list_schedules(tenant_id)[0]
        # interval 3600，最近槽 NOW-30min 已超宽限(0)：next 前移到 >= NOW 的最近槽，不展开积压
        assert row["next_fire_at"] >= NOW
        audits = [a for a in __import__("src.desktop_automation.audit", fromlist=["x"]).list_audits(
            tenant_id, aggregate_type="schedule") if a["kind"] == "schedule_missed"]
        assert len(audits) == 1

    def test_within_grace_accepts_latest_once(self, tenant_id):
        """宽限内最多接纳最近一次：跳过旧槽（missed 计数），接纳最近槽"""
        _register(tenant_id)
        anchor = NOW - timedelta(hours=2, minutes=30)
        _publish(tenant_id, specs=[{
            "kind": "time", "anchor_at": anchor, "interval_seconds": 3600, "grace_seconds": 3600,
        }])
        candidates = schedules.find_due_candidates(NOW)
        r = occurrences.accept_time_slot(candidates[0], NOW)
        assert r["accepted"] is True and r["created"] is True
        assert r["missed_count"] == 2  # NOW-2.5h 与 NOW-1.5h 两个旧槽跳过，接纳 NOW-30m
        # 接纳的槽应是 <= NOW 的最近槽（scheduled_for = NOW-30m）
        occ = occurrences.get_occurrence(r["occurrence_id"], tenant_id)
        assert occ["scheduled_for"] == NOW - timedelta(minutes=30)
        # next 前移（锚点对齐下最近未来槽 >= NOW）
        assert schedules.list_schedules(tenant_id)[0]["next_fire_at"] >= NOW

    def test_one_shot_past_grace_missed_and_consumed(self, tenant_id):
        """P2-9（总工裁决）：one-shot 超宽限 → missed 审计 + 置 consumed（行保留对账）"""
        _register(tenant_id)
        anchor = NOW - timedelta(hours=2)
        _publish(tenant_id, specs=[{
            "kind": "time", "anchor_at": anchor, "one_shot": True, "grace_seconds": 0,
        }])
        candidates = schedules.find_due_candidates(NOW)
        r = occurrences.accept_time_slot(candidates[0], NOW)
        assert r["accepted"] is False and r["reason"] == "missed"
        row = schedules.list_schedules(tenant_id)[0]
        assert row["consumed"] is True  # 超宽限也置 consumed，与 recurring 语义对齐
        assert row["status"] == "active"  # 行保留对账
        audits = [a for a in __import__("src.desktop_automation.audit", fromlist=["x"]).list_audits(
            tenant_id, aggregate_type="schedule") if a["kind"] == "schedule_missed"]
        assert len(audits) == 1
        assert audits[0]["detail"]["reason"] == "one_shot_past_grace"

    def test_one_shot_within_grace_accepted(self, tenant_id):
        """P2-9：one-shot 宽限内迟到照常接纳"""
        _register(tenant_id)
        anchor = NOW - timedelta(minutes=10)
        _publish(tenant_id, specs=[{
            "kind": "time", "anchor_at": anchor, "one_shot": True, "grace_seconds": 3600,
        }])
        candidates = schedules.find_due_candidates(NOW)
        r = occurrences.accept_time_slot(candidates[0], NOW)
        assert r["accepted"] is True and r["created"] is True
        assert schedules.list_schedules(tenant_id)[0]["consumed"] is True

    def test_one_shot_consumed_row_kept(self, tenant_id):
        """一次性 schedule：接纳后 consumed=TRUE 且行保留对账，二次扫描不再接纳"""
        _register(tenant_id)
        anchor = NOW - timedelta(minutes=1)
        _publish(tenant_id, specs=[{
            "kind": "time", "anchor_at": anchor, "one_shot": True, "grace_seconds": 300,
        }])
        candidates = schedules.find_due_candidates(NOW)
        r = occurrences.accept_time_slot(candidates[0], NOW)
        assert r["created"] is True
        row = schedules.list_schedules(tenant_id)[0]
        assert row["consumed"] is True and row["status"] == "active"  # 保留行
        assert schedules.find_due_candidates(NOW) == []  # consumed 不再入选


class TestSkipOverlap:
    def test_skip_overlap_records_skipped_no_run(self, tenant_id):
        _register(tenant_id)
        anchor = NOW - timedelta(minutes=5)
        _publish(tenant_id, specs=[{"kind": "time", "anchor_at": anchor, "interval_seconds": 3600}])
        candidates = schedules.find_due_candidates(NOW)
        r1 = occurrences.accept_time_slot(candidates[0], NOW)
        assert r1["created"] is True
        # 前移后，把 next_fire_at 拔回过去模拟新槽到期 + 旧 run 未结束
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE desktop_automation_schedules SET next_fire_at = %s WHERE tenant_id = %s",
                (NOW + timedelta(hours=1), tenant_id),  # 对齐 r1 推进后的下一槽
            )
            conn.commit()
        nxt = schedules.find_due_candidates(NOW + timedelta(hours=1, seconds=1))[0]
        r2 = occurrences.accept_time_slot(nxt, NOW + timedelta(hours=1, seconds=1))
        assert r2["reason"] == "skipped_overlap"
        assert r2["created"] is False
        from src.db.database import get_db_connection as _gdb

        with _gdb() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) AS c FROM desktop_automation_runs WHERE tenant_id=%s", (tenant_id,))
            assert cur.fetchone()["c"] == 1  # 不生成第二个可执行 run


class TestEventIngestion:
    """events.py 库函数最小覆盖：源注册/接纳幂等/eligible 快照（匹配 worker 接线 P2）"""

    def test_register_source_and_accept_event_idempotent(self, tenant_id):
        from src.desktop_automation import events

        _register(tenant_id)
        events.register_event_source(
            tenant_id=tenant_id, scenario_key="fake-scenario", source_ref="src-1",
            source_type="internal", allowed_event_types=["lead.created"],
        )
        r1 = events.accept_event(
            tenant_id=tenant_id, source_ref="src-1", external_event_id="evt-a",
            event_type="lead.created", payload_ref="payload:evt-a",
            payload_hash="h" * 64,
        )
        assert r1["accepted"] is True and r1["duplicate"] is False
        assert r1["event_id"]
        # 事件订阅行存在时 eligible 快照含该订阅
        _publish(tenant_id, specs=[{
            "kind": "event", "source_ref": "src-1", "event_type": "lead.created",
        }])
        r2 = events.accept_event(
            tenant_id=tenant_id, source_ref="src-1", external_event_id="evt-b",
            event_type="lead.created", payload_ref="payload:evt-b", payload_hash="h" * 64,
        )
        assert any(e["task_ref"] == "task-1" for e in r2["eligible"])
        # 重复外部事件 ID：幂等返回原接纳结果
        r3 = events.accept_event(
            tenant_id=tenant_id, source_ref="src-1", external_event_id="evt-a",
            event_type="lead.created", payload_ref="payload:evt-a", payload_hash="h" * 64,
        )
        assert r3["duplicate"] is True and r3["event_id"] == r1["event_id"]

    def test_event_type_not_allowed_rejected(self, tenant_id):
        from src.desktop_automation import events

        _register(tenant_id)
        events.register_event_source(
            tenant_id=tenant_id, scenario_key="fake-scenario", source_ref="src-2",
            source_type="internal", allowed_event_types=["lead.created"],
        )
        r = events.accept_event(
            tenant_id=tenant_id, source_ref="src-2", external_event_id="evt-x",
            event_type="other.type", payload_ref="p", payload_hash="h" * 64,
        )
        assert r["accepted"] is False and r["reason"] == "event_type_not_allowed"


class TestManualTrigger:
    def test_manual_occurrence_no_schedule_change(self, tenant_id):
        _register(tenant_id)
        anchor = NOW + timedelta(hours=2)
        _publish(tenant_id, specs=[{"kind": "time", "anchor_at": anchor, "interval_seconds": 3600}])
        before = schedules.list_schedules(tenant_id)[0]
        r = occurrences.accept_manual_trigger(
            tenant_id=tenant_id, scenario_key="fake-scenario", task_ref="task-1",
            request_id="req-manual-1", user_id="owner-1", now=NOW,
        )
        assert r["created"] is True
        after = schedules.list_schedules(tenant_id)[0]
        assert after["next_fire_at"] == before["next_fire_at"]  # 手动不改时间 schedule
        assert occurrences.get_occurrence_by_trigger_key(
            tenant_id, "fake-scenario", "task-1", manual_trigger_key("req-manual-1")
        ) is not None

    def test_manual_duplicate_request_id_once(self, tenant_id):
        _register(tenant_id)
        _publish(tenant_id)
        r1 = occurrences.accept_manual_trigger(
            tenant_id=tenant_id, scenario_key="fake-scenario", task_ref="task-1",
            request_id="req-dup", user_id="owner-1", now=NOW,
        )
        r2 = occurrences.accept_manual_trigger(
            tenant_id=tenant_id, scenario_key="fake-scenario", task_ref="task-1",
            request_id="req-dup", user_id="owner-1", now=NOW + timedelta(seconds=1),
        )
        assert r1["created"] is True and r2["created"] is False
        assert r2["occurrence_id"] == r1["occurrence_id"]


class TestEventTrigger:
    def test_event_admission_and_dedup(self, tenant_id):
        _register(tenant_id)
        _publish(tenant_id, specs=[{
            "kind": "event", "source_ref": "src-1", "event_type": "lead.created",
        }])
        kwargs = dict(
            tenant_id=tenant_id, scenario_key="fake-scenario", task_ref="task-1",
            revision_ref="rev-1", source_ref="src-1", external_event_id="evt-1",
            user_id="owner-1", due_at=NOW, now=NOW,
        )
        r1 = occurrences.accept_event_trigger(**kwargs)
        r2 = occurrences.accept_event_trigger(**kwargs)
        assert r1["created"] is True and r2["created"] is False
        # 同 task 默认跨 revision 只接纳一次同一源事件（同一 trigger_key）
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) AS c FROM desktop_automation_runs WHERE tenant_id=%s", (tenant_id,))
            assert cur.fetchone()["c"] == 1

    def test_paused_task_rejects_event(self, tenant_id):
        _register(tenant_id)
        _publish(tenant_id, specs=[{"kind": "event", "source_ref": "src-1"}])
        subjects.pause_task(tenant_id, "fake-scenario", "task-1")
        r = occurrences.accept_event_trigger(
            tenant_id=tenant_id, scenario_key="fake-scenario", task_ref="task-1",
            revision_ref="rev-1", source_ref="src-1", external_event_id="evt-2",
            user_id="owner-1", due_at=NOW, now=NOW,
        )
        # task 暂停：active_revision_ref 复验不通过（订阅行属 paused revision）
        assert r["accepted"] is False
