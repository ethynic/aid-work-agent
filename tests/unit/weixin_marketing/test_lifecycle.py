"""automations 生命周期测试（R40 发布不可变 / CAS 409 / epoch / pause 撤销 / 并发恰一成功）"""

import threading
from datetime import timedelta

import pytest

from src.desktop_automation import schedules as da_schedules
from src.desktop_automation import subjects as da_subjects
from src.weixin_marketing.constants import SCENARIO_KEY
from src.weixin_marketing.service import ConflictError, NotFoundError, WeixinValidationError
from tests.unit.weixin_marketing.conftest import (
    create_and_publish,
    make_create_payload,
    manual_run_pending,
    utcnow,
)

pytestmark = pytest.mark.unit


class TestCreateAndDraft:
    def test_create_draft_freezes_blocks(self, service, tenant_id, bindings):
        _, group_id = bindings
        detail = service.create_automation(tenant_id, "owner-1", make_create_payload(group_id))
        automation = detail["automation"]
        assert automation["status"] == "draft"
        assert automation["version"] == 1
        assert detail["revisions"][0]["status"] == "draft"
        assert detail["revisions"][0]["revision_no"] == 1
        # 冻结块：payload_hash 已算出且正文不进通用表
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT position, kind, payload_hash FROM bs_weixin_marketing_content_blocks "
                "WHERE tenant_id = %s AND revision_id = %s ORDER BY position",
                (tenant_id, automation["draft_revision_id"]),
            )
            rows = [dict(r) for r in cur.fetchall()]
        assert [r["position"] for r in rows] == [1, 2]
        assert all(r["payload_hash"] for r in rows)

    def test_update_draft_cas_conflict(self, service, tenant_id, bindings):
        _, group_id = bindings
        detail = service.create_automation(tenant_id, "owner-1", make_create_payload(group_id))
        automation = detail["automation"]
        from src.weixin_marketing.models import DraftUpdateInput

        with pytest.raises(ConflictError):
            service.update_draft(
                tenant_id, str(automation["id"]), "owner-1",
                DraftUpdateInput(expected_version=99, name="新名字"),
            )
        # 数据未变
        assert service.get_automation_detail(tenant_id, str(automation["id"]), "owner-1")[
            "automation"
        ]["version"] == 1

    def test_update_draft_rewrites_blocks(self, service, tenant_id, bindings):
        _, group_id = bindings
        detail = service.create_automation(tenant_id, "owner-1", make_create_payload(group_id))
        automation = detail["automation"]
        from src.weixin_marketing.models import DraftUpdateInput

        updated = service.update_draft(
            tenant_id, str(automation["id"]), "owner-1",
            DraftUpdateInput(
                expected_version=1, name="改名",
                blocks=[{"type": "text", "text_content": "新内容"}],
            ),
        )
        assert updated["automation"]["version"] == 2
        assert updated["automation"]["name"] == "改名"

    def test_cross_tenant_and_cross_owner_404(self, service, tenant_id, bindings):
        _, group_id = bindings
        detail = service.create_automation(tenant_id, "owner-1", make_create_payload(group_id))
        automation_id = str(detail["automation"]["id"])
        with pytest.raises(NotFoundError):
            service.get_automation_detail(tenant_id, automation_id, "other-user")
        with pytest.raises(NotFoundError):
            service.get_automation_detail("wxm_other_tenant", automation_id, "owner-1")


class TestPublish:
    def test_publish_registers_subject_and_schedules(self, service, tenant_id, bindings, adapter):
        _, group_id = bindings
        automation_id, revision_id, result = create_and_publish(service, tenant_id, group_id)
        assert result["authorization_epoch"] == 0
        task = da_subjects.get_task_subject(tenant_id, SCENARIO_KEY, automation_id)
        assert task["status"] == "active"
        assert task["active_revision_ref"] == revision_id
        rows = da_schedules.list_schedules(tenant_id, SCENARIO_KEY, automation_id)
        assert len(rows) == 1 and rows[0]["status"] == "active" and rows[0]["one_shot"] is True
        # 发布后：revision 不可变（无草稿可编辑）
        from src.weixin_marketing.models import DraftUpdateInput

        with pytest.raises(WeixinValidationError):
            service.update_draft(
                tenant_id, automation_id, "owner-1",
                DraftUpdateInput(expected_version=2, name="x"),
            )
        detail = service.get_automation_detail(tenant_id, automation_id, "owner-1")
        assert detail["automation"]["status"] == "active"
        assert detail["automation"]["active_revision_id"] == revision_id

    def test_publish_cas_conflict(self, service, tenant_id, bindings, adapter):
        _, group_id = bindings
        detail = service.create_automation(tenant_id, "owner-1", make_create_payload(group_id))
        automation = detail["automation"]
        from src.weixin_marketing.models import PublishInput

        with pytest.raises(ConflictError):
            service.publish(
                tenant_id, str(automation["id"]), "owner-1",
                PublishInput(expected_version=automation["version"] + 5),
            )

    def test_publish_rejected_when_paused(self, service, tenant_id, bindings, adapter):
        """P2-6 裁决：paused 下 publish 拒绝 409（提示先 resume；草稿编辑不受影响）"""
        _, group_id = bindings
        automation_id, revision_id, publish_result = create_and_publish(service, tenant_id, group_id)
        from src.weixin_marketing.models import DraftUpdateInput, PublishInput, VersionedActionInput

        paused = service.pause(
            tenant_id, automation_id, "owner-1",
            VersionedActionInput(expected_version=publish_result["version"]),
        )
        # paused 下仍可新建草稿（编辑不受影响）
        detail = service.update_draft(
            tenant_id, automation_id, "owner-1",
            DraftUpdateInput(
                expected_version=paused["version"],
                trigger={"type": "once", "run_at": (utcnow() + timedelta(hours=3)).isoformat(),
                         "timezone": "UTC"},
                blocks=[{"type": "text", "text_content": "暂停期间的新草稿"}],
                group_binding_id=group_id,
            ),
        )
        with pytest.raises(ConflictError, match="resume"):
            service.publish(
                tenant_id, automation_id, "owner-1",
                PublishInput(expected_version=detail["automation"]["version"]),
            )

    def test_publish_rollback_on_schedule_failure(self, service, tenant_id, bindings, adapter, monkeypatch):
        """必修 D②：发布事务中途失败（subject 写入后 _insert_schedule 抛错）→
        subjects/schedules/业务行零残留（automation 保持 draft、version 不变）"""
        from src.desktop_automation import subjects as da_subjects_module

        def boom(*args, **kwargs):
            raise RuntimeError("injected schedule failure")

        monkeypatch.setattr(da_subjects_module, "_insert_schedule", boom)
        _, group_id = bindings
        detail = service.create_automation(tenant_id, "owner-1", make_create_payload(group_id))
        automation = detail["automation"]
        automation_id = str(automation["id"])
        from src.weixin_marketing.models import PublishInput

        with pytest.raises(RuntimeError, match="injected schedule failure"):
            service.publish(tenant_id, automation_id, "owner-1", PublishInput(expected_version=1))
        # 底座零残留：无 task/revision subject、无 schedule 行
        assert da_subjects.get_task_subject(tenant_id, SCENARIO_KEY, automation_id) is None
        assert da_schedules.list_schedules(tenant_id, SCENARIO_KEY, automation_id) == []
        # 业务行零残留：automation 保持 draft、version/active/draft 引用不变
        after = service.get_automation_detail(tenant_id, automation_id, "owner-1")["automation"]
        assert after["status"] == "draft"
        assert after["version"] == 1
        assert after["active_revision_id"] is None
        assert str(after["draft_revision_id"]) == str(automation["draft_revision_id"])
        # revision 仍 draft；审计无 automation_published
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT status FROM bs_weixin_marketing_revisions "
                "WHERE tenant_id = %s AND id = %s",
                (tenant_id, automation["draft_revision_id"]),
            )
            assert cur.fetchone()["status"] == "draft"
            cur.execute(
                "SELECT action FROM bs_weixin_marketing_audit_events "
                "WHERE tenant_id = %s AND automation_id = %s",
                (tenant_id, automation_id),
            )
            actions = [r["action"] for r in cur.fetchall()]
        assert actions == ["automation_created"]

    def test_publish_fails_loud_without_registered_adapter(self, service, tenant_id, bindings):
        """P2-2：适配器未注册（本用例不注入 adapter fixture）→ ConfigurationError，
        不静默自建兜底适配器"""
        from src.weixin_marketing.models import PublishInput
        from src.weixin_marketing.service import ConfigurationError

        _, group_id = bindings
        detail = service.create_automation(tenant_id, "owner-1", make_create_payload(group_id))
        automation = detail["automation"]
        with pytest.raises(ConfigurationError):
            service.publish(
                tenant_id, str(automation["id"]), "owner-1", PublishInput(expected_version=1),
            )

    def test_publish_rejects_invalid_config(self, service, tenant_id, bindings, adapter):
        _, group_id = bindings
        # once 时刻已过 + 群绑定未完成（直接置 pending 验证绑定门禁）
        detail = service.create_automation(
            tenant_id, "owner-1",
            make_create_payload(group_id, trigger={
                "type": "once", "run_at": (utcnow() - timedelta(hours=1)).isoformat(),
                "timezone": "UTC",
            }),
        )
        automation = detail["automation"]
        from src.weixin_marketing.models import PublishInput

        # 已过期 once 仍有 initial fire（anchor 在过去）→ 结构上可发布；
        # 改为验证绑定门禁：置 pending 后发布必须 422
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE bs_weixin_marketing_group_bindings SET state = 'pending' "
                "WHERE tenant_id = %s AND id = %s",
                (tenant_id, group_id),
            )
            conn.commit()
        with pytest.raises(WeixinValidationError):
            service.publish(
                tenant_id, str(automation["id"]), "owner-1",
                PublishInput(expected_version=1),
            )

    def test_republish_bumps_epoch_and_supersedes(self, service, tenant_id, bindings, adapter):
        _, group_id = bindings
        automation_id, revision_1, result_1 = create_and_publish(service, tenant_id, group_id)
        # 发布后迭代：新建草稿（revision_no=2）→ 发布 → epoch+1，旧 revision superseded
        from src.weixin_marketing.models import DraftUpdateInput, PublishInput

        detail = service.update_draft(
            tenant_id, automation_id, "owner-1",
            DraftUpdateInput(
                expected_version=result_1["version"],
                trigger={"type": "once", "run_at": (utcnow() + timedelta(hours=2)).isoformat(),
                         "timezone": "UTC"},
                blocks=[{"type": "text", "text_content": "第二轮内容"}],
                group_binding_id=group_id,
            ),
        )
        assert detail["automation"]["draft_revision_id"] is not None
        result_2 = service.publish(
            tenant_id, automation_id, "owner-1",
            PublishInput(expected_version=detail["automation"]["version"]),
        )
        assert result_2["authorization_epoch"] == result_1["authorization_epoch"] + 1
        task = da_subjects.get_task_subject(tenant_id, SCENARIO_KEY, automation_id)
        assert task["active_revision_ref"] == result_2["revision_id"]
        old = da_subjects.get_revision_subject(tenant_id, SCENARIO_KEY, revision_1)
        assert old["status"] == "superseded"
        # 旧 revision 的 schedules 暂停，新 revision 的 active
        rows = da_schedules.list_schedules(tenant_id, SCENARIO_KEY, automation_id)
        by_rev = {r["revision_ref"]: r["status"] for r in rows}
        assert by_rev[revision_1] == "paused"
        assert by_rev[result_2["revision_id"]] == "active"


class TestPauseResumeArchive:
    def test_pause_bumps_epoch_and_cancels_open_runs(self, service, tenant_id, bindings, adapter):
        _, group_id = bindings
        automation_id, _, publish_result = create_and_publish(service, tenant_id, group_id)
        _, run_id = manual_run_pending(service, tenant_id, automation_id)
        from src.desktop_automation import runs as da_runs

        run = da_runs.get_run(run_id, tenant_id)
        assert run["state"] == "pending"
        from src.weixin_marketing.models import VersionedActionInput

        result = service.pause(
            tenant_id, automation_id, "owner-1",
            VersionedActionInput(expected_version=publish_result["version"], reason="维护"),
        )
        assert result["status"] == "paused"
        assert result["authorization_epoch"] == publish_result["authorization_epoch"] + 1
        assert result["cancelled_runs"] == 1
        # run 被撤销（未开始 → cancelled）；schedules 暂停
        assert da_runs.get_run(run_id, tenant_id)["state"] == "cancelled"
        rows = da_schedules.list_schedules(tenant_id, SCENARIO_KEY, automation_id)
        assert all(r["status"] == "paused" for r in rows)
        # 暂停后手动 run 拒绝
        with pytest.raises(ConflictError):
            manual_run_pending(service, tenant_id, automation_id)

    def test_resume_reactivates(self, service, tenant_id, bindings, adapter):
        _, group_id = bindings
        automation_id, _, publish_result = create_and_publish(service, tenant_id, group_id)
        from src.weixin_marketing.models import VersionedActionInput

        paused = service.pause(
            tenant_id, automation_id, "owner-1", VersionedActionInput(expected_version=publish_result["version"]),
        )
        resumed = service.resume(
            tenant_id, automation_id, "owner-1", VersionedActionInput(expected_version=paused["version"]),
        )
        assert resumed["status"] == "active"
        assert resumed["authorization_epoch"] == paused["authorization_epoch"] + 1
        rows = da_schedules.list_schedules(tenant_id, SCENARIO_KEY, automation_id)
        assert all(r["status"] == "active" for r in rows)

    def test_archive_is_terminal(self, service, tenant_id, bindings, adapter):
        _, group_id = bindings
        automation_id, _, publish_result = create_and_publish(service, tenant_id, group_id)
        from src.weixin_marketing.models import VersionedActionInput

        archived = service.archive(
            tenant_id, automation_id, "owner-1", VersionedActionInput(expected_version=publish_result["version"]),
        )
        assert archived["status"] == "archived"
        with pytest.raises(ConflictError):  # 归档不可 resume / 不可再 publish
            service.resume(tenant_id, automation_id, "owner-1", VersionedActionInput(expected_version=archived["version"]))
        from src.weixin_marketing.models import PublishInput

        with pytest.raises(ConflictError):
            service.publish(tenant_id, automation_id, "owner-1", PublishInput(expected_version=archived["version"]))


class TestConcurrency:
    def test_concurrent_publish_and_pause_exactly_one_wins(self, service, tenant_id, bindings, adapter):
        """同 automation 并发 publish / pause：version CAS 串行化，恰一成功（409 恰一次）"""
        _, group_id = bindings
        detail = service.create_automation(tenant_id, "owner-1", make_create_payload(group_id))
        automation = detail["automation"]
        automation_id = str(automation["id"])
        from src.weixin_marketing.models import PublishInput, VersionedActionInput

        barrier = threading.Barrier(2)
        results = {}

        def do_publish():
            barrier.wait(timeout=10)
            try:
                results["publish"] = service.publish(
                    tenant_id, automation_id, "owner-1", PublishInput(expected_version=1),
                )
            except Exception as e:  # noqa: BLE001
                results["publish_error"] = e

        def do_pause():
            barrier.wait(timeout=10)
            try:
                results["pause"] = service.pause(
                    tenant_id, automation_id, "owner-1", VersionedActionInput(expected_version=1),
                )
            except Exception as e:  # noqa: BLE001
                results["pause_error"] = e

        threads = [threading.Thread(target=do_publish), threading.Thread(target=do_pause)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        outcomes = [k for k in ("publish", "pause") if k in results]
        errors = [k for k in ("publish_error", "pause_error") if k in results]
        assert len(outcomes) == 1 and len(errors) == 1, results
        assert isinstance(results[errors[0]], ConflictError)
        final = service.get_automation_detail(tenant_id, automation_id, "owner-1")["automation"]
        # 恰一成功推进 version → 2；状态与胜者一致
        assert final["version"] == 2
        if "publish" in results:
            assert final["status"] == "active"
        else:
            assert final["status"] == "paused"
