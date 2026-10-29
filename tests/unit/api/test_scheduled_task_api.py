"""定时任务 REST API（Runner 化 Phase 4）单元测试：全 mock，不触真实 DB

覆盖（plan-scheduled-task-runner-refactor.md Phase 4）：
- 列表归属过滤 + 内部字段剥离；统计聚合
- pause/resume/trigger/delete 状态机（409 口径：pause 非 active / resume 非
  paused / resume once 过宽限 / trigger pending 占用或非 active / delete BusyError）
- 编辑不动调度（update_definition 不触碰调度字段）；schedule 变更重算 due
- F4：clear_notification_target → clear_fields 清空通知目标
- 会话删除守卫：metadata.scheduled_task 标记 → 409；普通会话不受影响
"""

from contextlib import contextmanager
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import scheduled_task as scheduled_task_api
from src.api import session as session_api
from src.scheduler.db import ScheduledTaskBusyError, ScheduledTaskDB

_USER = {"user_id": "user_1", "tenant_id": "tenant_1", "role": "user"}
_AUTH = {"Authorization": "Bearer test_token"}


def _task_row(**overrides):
    row = {
        "task_id": "sched_abc123", "tenant_id": "tenant_1", "user_id": "user_1",
        "session_id": "sess_task_1", "name": "每日日报", "task_prompt": "生成日报",
        "attachments": [], "profile_id": "main", "instance_id": None,
        "schedule_type": "daily", "cron_expression": "0 9 * * *",
        "interval_seconds": None, "run_at": None,
        "status": "active", "pause_reason": None,
        "next_due_at": datetime(2026, 10, 29, 9, 0),
        "pending_occurrence_key": None, "pending_trigger": None,
        "pending_retry_at": None, "pending_snapshot": {"text": "内部快照"},
        "notification_target_id": None,
        "total_runs": 3, "success_count": 2, "fail_count": 1,
        "last_run_at": datetime(2026, 10, 28, 9, 0), "last_status": "completed",
        "created_at": datetime(2026, 10, 20, 9, 0), "updated_at": datetime(2026, 10, 28, 9, 0),
    }
    row.update(overrides)
    return row


@pytest.fixture
def client(monkeypatch):
    """最小 app 挂定时任务路由；鉴权与 DAL 边界全部 mock"""
    app = FastAPI()
    app.include_router(scheduled_task_api.router)
    monkeypatch.setattr(
        scheduled_task_api, "fresh_web_user", lambda authorization: dict(_USER))
    yield TestClient(app, raise_server_exceptions=False)


# ==================== 列表 / 统计 / 详情 ====================

class TestListAndStats:
    def test_list_filters_by_tenant_and_user_and_strips_internal(self, client, monkeypatch):
        rows = [_task_row(), _task_row(task_id="sched_2", name="周报")]
        monkeypatch.setattr(ScheduledTaskDB, "list_by_user",
                            MagicMock(return_value=rows))
        resp = client.get("/api/scheduled-tasks", headers=_AUTH)
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True and body["data"]["total"] == 2
        # 归属过滤进 SQL
        args, kwargs = ScheduledTaskDB.list_by_user.call_args
        assert args[0] == "user_1" and kwargs["tenant_id"] == "tenant_1"
        # 内部交接字段不外泄
        for task in body["data"]["tasks"]:
            assert "pending_snapshot" not in task
            # 任务行字段完整性（session_id/pause_reason/last_status/next_due_at/通知目标）
            for key in ("session_id", "pause_reason", "last_status", "next_due_at",
                        "notification_target_id"):
                assert key in task

    def test_stats_aggregates(self, client, monkeypatch):
        monkeypatch.setattr(ScheduledTaskDB, "list_by_user", MagicMock(return_value=[
            _task_row(), _task_row(task_id="sched_2", status="paused", total_runs=7),
        ]))
        resp = client.get("/api/scheduled-tasks/stats", headers=_AUTH)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total_tasks"] == 2
        assert data["active_tasks"] == 1 and data["paused_tasks"] == 1
        assert data["total_runs"] == 10 and data["total_success"] == 4
        assert data["success_rate"] == 40.0

    def test_detail_404_for_missing_or_foreign(self, client, monkeypatch):
        monkeypatch.setattr(ScheduledTaskDB, "get_by_id", MagicMock(return_value=None))
        resp = client.get("/api/scheduled-tasks/sched_x", headers=_AUTH)
        assert resp.status_code == 404

    def test_unauthorized_rejected(self, client, monkeypatch):
        from fastapi import HTTPException
        def deny(authorization):
            raise HTTPException(401, "USER_UNAUTHORIZED")
        monkeypatch.setattr(scheduled_task_api, "fresh_web_user", deny)
        resp = client.get("/api/scheduled-tasks")
        assert resp.status_code == 401

    def test_tenant_mismatch_forbidden(self, client, monkeypatch):
        monkeypatch.setattr(
            scheduled_task_api, "fresh_web_user",
            lambda authorization: dict(_USER, tenant_id="tenant_1"))
        # 普通用户带 X-Tenant-Id 与自身租户不一致 → 403
        resp = client.get("/api/scheduled-tasks",
                          headers={**_AUTH, "X-Tenant-Id": "tenant_2"})
        assert resp.status_code == 403


# ==================== 状态机：pause / resume / trigger / delete ====================

class TestLifecycleEndpoints:
    def _patch_owned(self, monkeypatch, row):
        monkeypatch.setattr(ScheduledTaskDB, "get_by_id", MagicMock(return_value=row))

    def test_pause_success(self, client, monkeypatch):
        self._patch_owned(monkeypatch, _task_row())
        monkeypatch.setattr(ScheduledTaskDB, "pause", MagicMock(return_value=True))
        resp = client.post("/api/scheduled-tasks/sched_abc123/pause", headers=_AUTH)
        assert resp.status_code == 200
        kwargs = ScheduledTaskDB.pause.call_args.kwargs
        assert kwargs["tenant_id"] == "tenant_1" and kwargs["user_id"] == "user_1"

    def test_pause_conflict_when_not_active(self, client, monkeypatch):
        self._patch_owned(monkeypatch, _task_row(status="completed"))
        monkeypatch.setattr(ScheduledTaskDB, "pause", MagicMock(return_value=False))
        resp = client.post("/api/scheduled-tasks/sched_abc123/pause", headers=_AUTH)
        assert resp.status_code == 409

    def test_resume_uses_transactional_recompute(self, client, monkeypatch):
        """恢复统一走 resume_recompute（事务内锁行重算，评审 P1）：
        API 不传预计算 due，由事务内按锁定行计算。"""
        task = _task_row(status="paused", schedule_type="interval",
                         interval_seconds=7200, cron_expression=None)
        self._patch_owned(monkeypatch, task)
        monkeypatch.setattr(ScheduledTaskDB, "resume_recompute",
                            MagicMock(return_value=dict(task, status="active",
                                                        pause_reason=None,
                                                        next_due_at=datetime.now())))
        resp = client.post("/api/scheduled-tasks/sched_abc123/resume", headers=_AUTH)
        assert resp.status_code == 200
        call = ScheduledTaskDB.resume_recompute.call_args
        assert "next_due_at" not in call.kwargs  # 入口不传预计算值
        assert call.kwargs.get("max_active") is not None  # 配额事务内检查
        assert resp.json()["data"]["next_due_at"] is not None

    def test_resume_conflict_when_not_paused(self, client, monkeypatch):
        self._patch_owned(monkeypatch, _task_row(status="active"))
        monkeypatch.setattr(ScheduledTaskDB, "resume_recompute",
                            MagicMock(return_value=None))
        resp = client.post("/api/scheduled-tasks/sched_abc123/resume", headers=_AUTH)
        assert resp.status_code == 409

    def test_resume_once_past_grace_expires_unified(self, client, monkeypatch):
        """once 过宽限：统一转 expired（与工具同一路径，不再 409 强制改期）"""
        task = _task_row(status="paused", schedule_type="once", cron_expression=None,
                         run_at=datetime.now() - timedelta(hours=3))
        self._patch_owned(monkeypatch, task)
        monkeypatch.setattr(ScheduledTaskDB, "resume_recompute",
                            MagicMock(return_value=dict(task, status="expired",
                                                        next_due_at=None,
                                                        pause_reason=None)))
        resp = client.post("/api/scheduled-tasks/sched_abc123/resume", headers=_AUTH)
        assert resp.status_code == 200
        assert resp.json().get("expired") is True
        assert "作废" in resp.json()["message"]

    def test_trigger_writes_manual_slot_without_touching_due(self, client, monkeypatch):
        """trigger：submit_manual manual 槽（uuid hex 幂等键），不改 next_due_at"""
        self._patch_owned(monkeypatch, _task_row())
        monkeypatch.setattr(ScheduledTaskDB, "submit_manual",
                            MagicMock(return_value=_task_row()))
        resp = client.post("/api/scheduled-tasks/sched_abc123/trigger", headers=_AUTH)
        assert resp.status_code == 200
        args, kwargs = ScheduledTaskDB.submit_manual.call_args
        assert args[0] == "sched_abc123"
        # occurrence_key = uuid hex；manual 槽不触碰 next_due_at（DAL 契约），
        # 且非交互指令随快照固化
        assert len(kwargs["occurrence_key"]) == 32
        int(kwargs["occurrence_key"], 16)  # hex
        assert kwargs["prompt_augmentations"]
        assert "next_due" not in kwargs

    def test_trigger_conflict_when_occupied_or_not_active(self, client, monkeypatch):
        self._patch_owned(monkeypatch, _task_row())
        monkeypatch.setattr(ScheduledTaskDB, "submit_manual", MagicMock(return_value=None))
        resp = client.post("/api/scheduled-tasks/sched_abc123/trigger", headers=_AUTH)
        assert resp.status_code == 409

    def test_delete_success(self, client, monkeypatch):
        self._patch_owned(monkeypatch, _task_row())
        monkeypatch.setattr(ScheduledTaskDB, "delete_with_session",
                            MagicMock(return_value=_task_row()))
        resp = client.delete("/api/scheduled-tasks/sched_abc123", headers=_AUTH)
        assert resp.status_code == 200
        kwargs = ScheduledTaskDB.delete_with_session.call_args.kwargs
        assert kwargs["tenant_id"] == "tenant_1" and kwargs["user_id"] == "user_1"

    def test_delete_row_not_matched_returns_404_not_false_success(self, client, monkeypatch):
        """守卫锁行未命中（并发删除/platform_admin 未带租户）：404，不谎报已删除"""
        self._patch_owned(monkeypatch, _task_row())
        monkeypatch.setattr(ScheduledTaskDB, "delete_with_session",
                            MagicMock(return_value=None))
        resp = client.delete("/api/scheduled-tasks/sched_abc123", headers=_AUTH)
        assert resp.status_code == 404

    def test_delete_busy_409_friendly_message(self, client, monkeypatch):
        self._patch_owned(monkeypatch, _task_row())
        monkeypatch.setattr(ScheduledTaskDB, "delete_with_session",
                            MagicMock(side_effect=ScheduledTaskBusyError("busy")))
        resp = client.delete("/api/scheduled-tasks/sched_abc123", headers=_AUTH)
        assert resp.status_code == 409
        assert "任务正在执行" in resp.json()["detail"]

    def test_delete_missing_404(self, client, monkeypatch):
        monkeypatch.setattr(ScheduledTaskDB, "get_by_id", MagicMock(return_value=None))
        resp = client.delete("/api/scheduled-tasks/sched_x", headers=_AUTH)
        assert resp.status_code == 404


# ==================== 编辑与改期（内容/调度解耦） ====================

class TestEditEndpoints:
    def test_definition_edit_does_not_touch_schedule(self, client, monkeypatch):
        """编辑（PUT /{task_id}）：只走 update_definition，不动调度字段"""
        monkeypatch.setattr(ScheduledTaskDB, "get_by_id",
                            MagicMock(side_effect=[_task_row(), _task_row(name="新名")]))
        monkeypatch.setattr(ScheduledTaskDB, "update_definition",
                            MagicMock(return_value=True))
        monkeypatch.setattr(ScheduledTaskDB, "update_schedule", MagicMock())
        resp = client.put("/api/scheduled-tasks/sched_abc123",
                          headers=_AUTH, json={"name": "新名", "task_prompt": "新定义"})
        assert resp.status_code == 200
        kwargs = ScheduledTaskDB.update_definition.call_args.kwargs
        assert kwargs["name"] == "新名" and kwargs["task_prompt"] == "新定义"
        assert kwargs.get("clear_fields") is None
        ScheduledTaskDB.update_schedule.assert_not_called()
        assert resp.json()["data"]["name"] == "新名"

    def test_definition_edit_clear_notification_target(self, client, monkeypatch):
        """F4 清空语义：clear_notification_target=true → clear_fields 清空"""
        monkeypatch.setattr(ScheduledTaskDB, "get_by_id",
                            MagicMock(side_effect=[_task_row(), _task_row()]))
        monkeypatch.setattr(ScheduledTaskDB, "update_definition",
                            MagicMock(return_value=True))
        resp = client.put("/api/scheduled-tasks/sched_abc123",
                          headers=_AUTH, json={"clear_notification_target": True})
        assert resp.status_code == 200
        kwargs = ScheduledTaskDB.update_definition.call_args.kwargs
        assert kwargs["clear_fields"] == ["notification_target_id"]
        assert kwargs["notification_target_id"] is None

    def test_definition_edit_set_and_clear_mutually_exclusive(self, client, monkeypatch):
        monkeypatch.setattr(ScheduledTaskDB, "get_by_id",
                            MagicMock(return_value=_task_row()))
        resp = client.put("/api/scheduled-tasks/sched_abc123",
                          headers=_AUTH,
                          json={"notification_target_id": "nt_1",
                                "clear_notification_target": True})
        assert resp.status_code == 422

    def test_definition_edit_no_fields_422(self, client, monkeypatch):
        monkeypatch.setattr(ScheduledTaskDB, "get_by_id",
                            MagicMock(return_value=_task_row()))
        resp = client.put("/api/scheduled-tasks/sched_abc123",
                          headers=_AUTH, json={})
        assert resp.status_code == 422

    def test_definition_edit_invisible_notify_target_422(self, client, monkeypatch):
        """归属校验：不可见通知目标（他租户/幻觉 ID）→ 422，不更新"""
        monkeypatch.setattr(ScheduledTaskDB, "get_by_id",
                            MagicMock(return_value=_task_row()))
        monkeypatch.setattr(ScheduledTaskDB, "notify_target_visible",
                            MagicMock(return_value=False))
        monkeypatch.setattr(ScheduledTaskDB, "update_definition", MagicMock())
        resp = client.put("/api/scheduled-tasks/sched_abc123",
                          headers=_AUTH, json={"notification_target_id": "t_foreign"})
        assert resp.status_code == 422
        assert "通知目标" in resp.json()["detail"]
        ScheduledTaskDB.update_definition.assert_not_called()

    def test_definition_edit_deleted_after_update_404(self, client, monkeypatch):
        """更新与删除并发：更新后行已不存在 → 404（不 500、不泄露存在性）"""
        monkeypatch.setattr(ScheduledTaskDB, "get_by_id",
                            MagicMock(side_effect=[_task_row(), None]))
        monkeypatch.setattr(ScheduledTaskDB, "update_definition",
                            MagicMock(return_value=True))
        resp = client.put("/api/scheduled-tasks/sched_abc123",
                          headers=_AUTH, json={"name": "新名"})
        assert resp.status_code == 404

    def test_schedule_update_recomputes_due(self, client, monkeypatch):
        """改期（PUT /{task_id}/schedule）：调度字段 + 重算 next_due_at"""
        monkeypatch.setattr(ScheduledTaskDB, "get_by_id",
                            MagicMock(return_value=_task_row()))
        monkeypatch.setattr(ScheduledTaskDB, "update_schedule",
                            MagicMock(return_value=True))
        before = datetime.now()
        resp = client.put("/api/scheduled-tasks/sched_abc123/schedule",
                          headers=_AUTH,
                          json={"schedule_type": "interval",
                                "time_config": {"interval_hours": 6}})
        assert resp.status_code == 200
        kwargs = ScheduledTaskDB.update_schedule.call_args.kwargs
        assert kwargs["schedule_type"] == "interval"
        assert kwargs["interval_seconds"] == 21600
        assert kwargs["cron_expression"] is None
        assert before + timedelta(hours=6) <= kwargs["next_due_at"] <= \
            datetime.now() + timedelta(hours=6)
        data = resp.json()["data"]
        assert data["interval_seconds"] == 21600 and data["next_due_at"] is not None

    def test_schedule_update_invalid_config_422(self, client, monkeypatch):
        monkeypatch.setattr(ScheduledTaskDB, "get_by_id",
                            MagicMock(return_value=_task_row()))
        resp = client.put("/api/scheduled-tasks/sched_abc123/schedule",
                          headers=_AUTH,
                          json={"schedule_type": "once", "time_config": {}})
        assert resp.status_code == 422
        assert "先向用户确认" in resp.json()["detail"]


# ==================== F4：DAL clear_fields 直接单测 ====================

class TestUpdateDefinitionClearFields:
    def test_clear_fields_sets_null_and_set_value_skipped(self):
        """clear_fields 命中字段置 NULL；同名字段显式传值被清空覆盖"""
        from unittest.mock import MagicMock, patch
        from contextlib import contextmanager

        cursor = MagicMock()
        cursor.rowcount = 1

        @contextmanager
        def fake_conn():
            yield MagicMock(cursor=MagicMock(return_value=cursor))

        with patch("src.scheduler.db.get_db_connection", fake_conn):
            ok = ScheduledTaskDB.update_definition(
                "sched_1", notification_target_id="nt_1",
                clear_fields=["notification_target_id"],
                tenant_id="tenant_1", user_id="user_1")
        assert ok is True
        sql = cursor.execute.call_args.args[0]
        params = cursor.execute.call_args.args[1]
        assert "notification_target_id = NULL" in sql
        # 显式传入的 nt_1 不再作为参数出现（清空优先）
        assert "nt_1" not in params

    def test_clear_fields_unknown_value_rejected(self):
        with pytest.raises(ValueError, match="INVALID_CLEAR_FIELDS"):
            ScheduledTaskDB.update_definition("sched_1", clear_fields=["name"])

    def test_set_without_clear_keeps_value(self):
        """设置路径（无 clear_fields）：notification_target_id 正常落参数"""
        from unittest.mock import MagicMock, patch
        from contextlib import contextmanager

        cursor = MagicMock()
        cursor.rowcount = 1

        @contextmanager
        def fake_conn():
            yield MagicMock(cursor=MagicMock(return_value=cursor))

        with patch("src.scheduler.db.get_db_connection", fake_conn):
            ok = ScheduledTaskDB.update_definition(
                "sched_1", notification_target_id="nt_1",
                tenant_id="tenant_1", user_id="user_1")
        assert ok is True
        params = cursor.execute.call_args.args[1]
        assert "nt_1" in params
        assert "NULL" not in cursor.execute.call_args.args[0].replace("updated_at = now()", "")


# ==================== 通知目标归属校验（DAL） ====================

class TestNotifyTargetVisible:
    def _run(self, monkeypatch, fetchone_result, target_id="target_x",
             tenant_id="tenant_1", user_id="user_1"):
        cursor = MagicMock()
        cursor.fetchone.return_value = fetchone_result

        @contextmanager
        def fake_conn():
            yield MagicMock(cursor=MagicMock(return_value=cursor))

        monkeypatch.setattr("src.scheduler.db.get_db_connection", fake_conn)
        return ScheduledTaskDB.notify_target_visible(
            target_id, tenant_id=tenant_id, user_id=user_id), cursor

    def test_visible_target_scoped_to_own_tenant(self, monkeypatch):
        ok, cursor = self._run(monkeypatch, {"1": 1})
        assert ok is True
        sql, params = cursor.execute.call_args.args
        # 同口径限定：active + 本人/本租户 scope（不含他租户目标）
        assert "state = 'active'" in sql and "scope_key = %s" in sql
        assert "recipient_user_id = %s OR recipient_user_id IS NULL" in sql
        assert params[1] == "tenant:tenant_1"

    def test_invisible_target_false(self, monkeypatch):
        ok, _ = self._run(monkeypatch, None)
        assert ok is False

    def test_fail_closed_on_missing_tenant_or_bad_input(self, monkeypatch):
        # 租户缺失（platform_admin 未带 X-Tenant-Id）与超长/空 ID 直接拒绝
        ok, cursor = self._run(monkeypatch, {"1": 1}, tenant_id=None)
        assert ok is False and not cursor.execute.called
        ok, cursor = self._run(monkeypatch, {"1": 1}, target_id="x" * 129)
        assert ok is False and not cursor.execute.called
        ok, cursor = self._run(monkeypatch, {"1": 1}, target_id="")
        assert ok is False and not cursor.execute.called


# ==================== 会话删除守卫 ====================

@pytest.fixture
def session_client(monkeypatch):
    """最小 app 挂会话路由；鉴权与 DB 边界 mock（含行为审计落库边界）"""
    app = FastAPI()
    app.include_router(session_api.router)
    monkeypatch.setattr(session_api, "get_current_user",
                        lambda request: dict(_USER))
    from src.services import behavior_log
    monkeypatch.setattr(behavior_log, "record_behavior", AsyncMock())
    yield TestClient(app, raise_server_exceptions=False)


class TestSessionDeleteGuard:
    def test_scheduled_task_session_delete_rejected(self, session_client, monkeypatch):
        """任务专属会话（metadata.scheduled_task）→ 409 引导走任务管理"""
        monkeypatch.setattr(session_api.SessionDB, "get_by_id",
                            MagicMock(return_value={
                                "session_id": "sess_task_1", "user_id": "user_1",
                                "metadata": {"scheduled_task": "sched_abc123"},
                            }))
        monkeypatch.setattr(session_api.SessionDB, "delete", MagicMock())
        resp = session_client.delete("/api/sessions/sess_task_1", headers=_AUTH)
        assert resp.status_code == 409
        assert "定时任务" in resp.json()["detail"]
        session_api.SessionDB.delete.assert_not_called()

    def test_metadata_json_string_also_guarded(self, session_client, monkeypatch):
        """缓存路径可能回放 JSON 字符串：同口径归一化后拒绝"""
        monkeypatch.setattr(session_api.SessionDB, "get_by_id",
                            MagicMock(return_value={
                                "session_id": "sess_task_1", "user_id": "user_1",
                                "metadata": '{"scheduled_task": "sched_abc123"}',
                            }))
        resp = session_client.delete("/api/sessions/sess_task_1", headers=_AUTH)
        assert resp.status_code == 409

    def test_normal_session_delete_unaffected(self, session_client, monkeypatch):
        """普通会话（无标记）不受守卫影响"""
        monkeypatch.setattr(session_api.SessionDB, "get_by_id",
                            MagicMock(return_value={
                                "session_id": "sess_normal", "user_id": "user_1",
                                "metadata": None,
                            }))
        monkeypatch.setattr(session_api.SessionDB, "delete",
                            MagicMock(return_value=True))
        resp = session_client.delete("/api/sessions/sess_normal", headers=_AUTH)
        assert resp.status_code == 200
        session_api.SessionDB.delete.assert_called_once_with("sess_normal")
