"""subjects 发布/暂停/恢复事务测试（R5/R12：epoch/原子性/旧 schedule 暂停）"""

import threading
from datetime import datetime, timedelta, timezone

import pytest

from src.desktop_automation import schedules, subjects
from tests.unit.desktop_automation.fakes import FakeScenarioAdapter

pytestmark = pytest.mark.unit

NOW = datetime(2026, 9, 8, 12, 0, 0, tzinfo=timezone.utc)


def _register(tenant_id):
    adapter = FakeScenarioAdapter()
    from src.desktop_automation.adapters import TrustedAdapterRegistry

    TrustedAdapterRegistry.register(adapter)
    return adapter


def _interval_spec(anchor=NOW, interval=3600, **extra):
    spec = {"kind": "time", "anchor_at": anchor, "interval_seconds": interval}
    spec.update(extra)
    return spec


class TestPublish:
    def test_publish_creates_task_and_revision_subjects(self, tenant_id):
        _register(tenant_id)
        result = subjects.publish_revision(
            tenant_id, "fake-scenario", "task-1", "rev-1", "owner-1",
            revision_config={},
            schedule_specs=[_interval_spec()],
        )
        # 首建 epoch=0（INSERT 路径），active revision 锁定
        assert result["authorization_epoch"] == 0
        task = subjects.get_task_subject(tenant_id, "fake-scenario", "task-1")
        assert task["status"] == "active"
        assert task["active_revision_ref"] == "rev-1"
        assert task["owner_id"] == "owner-1"
        revision = subjects.get_revision_subject(tenant_id, "fake-scenario", "rev-1")
        assert revision["status"] == "published"
        assert revision["task_ref"] == "task-1"
        # schedule 已建且激活
        rows = schedules.list_schedules(tenant_id, "fake-scenario", "task-1")
        assert len(rows) == 1
        assert rows[0]["status"] == "active"
        assert rows[0]["trigger_key"] == "time"

    def test_republish_bumps_epoch_and_supersedes_old(self, tenant_id):
        _register(tenant_id)
        subjects.publish_revision(
            tenant_id, "fake-scenario", "task-1", "rev-1", "owner-1",
            revision_config={}, schedule_specs=[_interval_spec()],
        )
        result = subjects.publish_revision(
            tenant_id, "fake-scenario", "task-1", "rev-2", "owner-1",
            revision_config={}, schedule_specs=[_interval_spec()],
        )
        assert result["authorization_epoch"] == 1
        task = subjects.get_task_subject(tenant_id, "fake-scenario", "task-1")
        assert task["active_revision_ref"] == "rev-2"
        # 旧 revision 置 superseded，旧 revision 的 schedule 暂停
        assert subjects.get_revision_subject(tenant_id, "fake-scenario", "rev-1")["status"] == "superseded"
        rows = schedules.list_schedules(tenant_id, "fake-scenario", "task-1")
        by_rev = {r["revision_ref"]: r for r in rows}
        assert by_rev["rev-1"]["status"] == "paused"
        assert by_rev["rev-2"]["status"] == "active"


class TestPauseResume:
    def test_pause_updates_status_epoch_and_schedules_atomically(self, tenant_id):
        _register(tenant_id)
        subjects.publish_revision(
            tenant_id, "fake-scenario", "task-1", "rev-1", "owner-1",
            revision_config={}, schedule_specs=[_interval_spec()],
        )
        epoch = subjects.pause_task(tenant_id, "fake-scenario", "task-1")
        assert epoch == 1
        task = subjects.get_task_subject(tenant_id, "fake-scenario", "task-1")
        assert task["status"] == "paused"
        assert task["authorization_epoch"] == 1
        rows = schedules.list_schedules(tenant_id, "fake-scenario", "task-1")
        assert all(r["status"] == "paused" for r in rows)

    def test_pause_idempotent_returns_none_when_not_active(self, tenant_id):
        _register(tenant_id)
        subjects.publish_revision(
            tenant_id, "fake-scenario", "task-1", "rev-1", "owner-1",
            revision_config={}, schedule_specs=[],
        )
        assert subjects.pause_task(tenant_id, "fake-scenario", "task-1") == 1
        # 已暂停：不再推进 epoch
        assert subjects.pause_task(tenant_id, "fake-scenario", "task-1") is None

    def test_resume_reactivates_schedules_of_active_revision(self, tenant_id):
        _register(tenant_id)
        subjects.publish_revision(
            tenant_id, "fake-scenario", "task-1", "rev-1", "owner-1",
            revision_config={}, schedule_specs=[_interval_spec()],
        )
        subjects.pause_task(tenant_id, "fake-scenario", "task-1")
        epoch = subjects.resume_task(tenant_id, "fake-scenario", "task-1")
        assert epoch == 2
        task = subjects.get_task_subject(tenant_id, "fake-scenario", "task-1")
        assert task["status"] == "active"
        rows = schedules.list_schedules(tenant_id, "fake-scenario", "task-1")
        assert all(r["status"] == "active" for r in rows)


class TestMultiTaskIsolation:
    def test_publish_pause_only_touches_own_task_schedules(self, tenant_id):
        """P0-1 回归：同租户同场景两个 task，发布/暂停 task-2 不得影响 task-1 的 schedules"""
        _register(tenant_id)
        subjects.publish_revision(
            tenant_id, "fake-scenario", "task-1", "rev-1", "owner-1",
            revision_config={}, schedule_specs=[_interval_spec(anchor=NOW)],
        )
        subjects.publish_revision(
            tenant_id, "fake-scenario", "task-2", "rev-2", "owner-2",
            revision_config={}, schedule_specs=[_interval_spec(anchor=NOW + timedelta(hours=1))],
        )
        # task-2 重发布（旧 rev-2 → rev-2b）：task-1 的 schedule 必须保持 active
        subjects.publish_revision(
            tenant_id, "fake-scenario", "task-2", "rev-2b", "owner-2",
            revision_config={}, schedule_specs=[_interval_spec(anchor=NOW + timedelta(hours=2))],
        )
        t1 = [r for r in schedules.list_schedules(tenant_id, "fake-scenario", "task-1")]
        assert len(t1) == 1 and t1[0]["status"] == "active" and t1[0]["revision_ref"] == "rev-1"
        t2 = [r for r in schedules.list_schedules(tenant_id, "fake-scenario", "task-2")]
        assert {r["status"] for r in t2} == {"paused", "active"}  # rev-2 暂停、rev-2b 激活

        # 暂停 task-2：task-1 的 schedule 不受影响
        subjects.pause_task(tenant_id, "fake-scenario", "task-2")
        t1_after = schedules.list_schedules(tenant_id, "fake-scenario", "task-1")
        assert all(r["status"] == "active" for r in t1_after)
        # resume task-2 只恢复 task-2 的 active revision schedules
        subjects.resume_task(tenant_id, "fake-scenario", "task-2")
        t2_resumed = schedules.list_schedules(tenant_id, "fake-scenario", "task-2")
        by_rev = {r["revision_ref"]: r["status"] for r in t2_resumed}
        assert by_rev == {"rev-2": "paused", "rev-2b": "active"}


class TestConcurrency:
    def test_concurrent_publish_epoch_consistent(self, tenant_id):
        """预建 task 后两个线程并发发布不同 revision：epoch 恰好推进 2 次，最终状态一致"""
        _register(tenant_id)
        subjects.publish_revision(
            tenant_id, "fake-scenario", "task-1", "rev-init", "owner-1",
            revision_config={}, schedule_specs=[],
        )
        barrier = threading.Barrier(2)
        errors = []

        def worker(rev, index):
            try:
                barrier.wait(timeout=10)
                subjects.publish_revision(
                    tenant_id, "fake-scenario", "task-1", rev, "owner-1",
                    revision_config={}, schedule_specs=[_interval_spec(anchor=NOW + timedelta(hours=index + 1))],
                )
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [
            threading.Thread(target=worker, args=(f"rev-{i}", i)) for i in range(2)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert not errors, errors
        task = subjects.get_task_subject(tenant_id, "fake-scenario", "task-1")
        # 阻塞锁串行化：0（预建）→ 1 → 2，无半提交
        assert task["authorization_epoch"] == 2
        assert task["active_revision_ref"] in ("rev-0", "rev-1")
        # 两份并发 revision subject 恰好一份 published（败者被置 superseded）
        statuses = {
            subjects.get_revision_subject(tenant_id, "fake-scenario", r)["status"]
            for r in ("rev-0", "rev-1")
        }
        assert statuses == {"published", "superseded"}
