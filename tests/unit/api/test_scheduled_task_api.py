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
        "notification_rule_id": None, "notification_target_id": None,
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
            # 任务行字段完整性（session_id/pause_reason/last_status/next_due_at/通知规则）
            for key in ("session_id", "pause_reason", "last_status", "next_due_at",
                        "notification_rule_id"):
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


@pytest.mark.parametrize('method,path,body', [
    ('GET', '/sched_abc123', None),
    ('POST', '/sched_abc123/pause', None),
    ('POST', '/sched_abc123/resume', None),
    ('POST', '/sched_abc123/trigger', None),
    ('DELETE', '/sched_abc123', None),
    ('PUT', '/sched_abc123/schedule',
     {'schedule_type': 'daily', 'time_config': {'hour': 9, 'minute': 0}}),
    ('PUT', '/sched_abc123', {'name': '改名', 'clear_notification_rule': True}),
])
def test_known_daily_task_id_cannot_use_generic_routes(client, monkeypatch,
                                                        method, path, body):
    """已知 task_id 也不得通过通用详情、改期、通知、触发或删除绕过专属页面。"""
    row = _task_row(task_prompt=' \t[wecom-conversation:daily]{"schema_version":2}')
    monkeypatch.setattr(ScheduledTaskDB, 'get_by_id', MagicMock(return_value=row))
    writes = {}
    for name in ('pause', 'resume_recompute', 'submit_manual', 'delete_with_session',
                 'update_schedule', 'update_definition', 'notify_rule_visible'):
        writes[name] = MagicMock()
        monkeypatch.setattr(ScheduledTaskDB, name, writes[name])
    response = client.request(method, '/api/scheduled-tasks' + path,
                              headers=_AUTH, json=body)
    assert response.status_code == 409
    for write in writes.values():
        write.assert_not_called()


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

    def test_definition_edit_clear_notification_rule(self, client, monkeypatch):
        """F4 清空语义：clear_notification_rule=true → clear_fields 清空"""
        monkeypatch.setattr(ScheduledTaskDB, "get_by_id",
                            MagicMock(side_effect=[_task_row(), _task_row()]))
        monkeypatch.setattr(ScheduledTaskDB, "update_definition",
                            MagicMock(return_value=True))
        resp = client.put("/api/scheduled-tasks/sched_abc123",
                          headers=_AUTH, json={"clear_notification_rule": True})
        assert resp.status_code == 200
        kwargs = ScheduledTaskDB.update_definition.call_args.kwargs
        assert kwargs["clear_fields"] == ["notification_rule_id"]
        assert kwargs["notification_rule_id"] is None

    def test_definition_edit_set_and_clear_mutually_exclusive(self, client, monkeypatch):
        monkeypatch.setattr(ScheduledTaskDB, "get_by_id",
                            MagicMock(return_value=_task_row()))
        resp = client.put("/api/scheduled-tasks/sched_abc123",
                          headers=_AUTH,
                          json={"notification_rule_id": "nr_1",
                                "clear_notification_rule": True})
        assert resp.status_code == 422

    def test_definition_edit_no_fields_422(self, client, monkeypatch):
        monkeypatch.setattr(ScheduledTaskDB, "get_by_id",
                            MagicMock(return_value=_task_row()))
        resp = client.put("/api/scheduled-tasks/sched_abc123",
                          headers=_AUTH, json={})
        assert resp.status_code == 422

    def test_definition_edit_invisible_notify_rule_422(self, client, monkeypatch):
        """归属校验：不可见通知规则（他租户/幻觉 ID）→ 422，不更新"""
        monkeypatch.setattr(ScheduledTaskDB, "get_by_id",
                            MagicMock(return_value=_task_row()))
        monkeypatch.setattr(ScheduledTaskDB, "notify_rule_visible",
                            MagicMock(return_value=False))
        monkeypatch.setattr(ScheduledTaskDB, "update_definition", MagicMock())
        resp = client.put("/api/scheduled-tasks/sched_abc123",
                          headers=_AUTH, json={"notification_rule_id": "r_foreign"})
        assert resp.status_code == 422
        assert "通知规则" in resp.json()["detail"]
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
        # §15.2.3：不设起点时首个计划点=当前时刻（此后每隔 N 小时）
        assert before <= kwargs["next_due_at"] <= datetime.now()
        assert kwargs["start_at"] is None and kwargs["ends_at"] is None
        data = resp.json()["data"]
        assert data["interval_seconds"] == 21600 and data["next_due_at"] is not None

    def test_schedule_update_concurrent_completion_409(self, client, monkeypatch):
        """评审 P1（并发）：读取时 active、UPDATE 时已被终态钩子置 completed
        ——UPDATE 状态条件零行命中，重读区分 409（不把竞态当成功返回）。"""
        monkeypatch.setattr(ScheduledTaskDB, "get_by_id", MagicMock(
            side_effect=[_task_row(),                       # 前置归属读取（active）
                         _task_row(status="completed")]))   # 零行更新后的状态重读
        monkeypatch.setattr(ScheduledTaskDB, "update_schedule",
                            MagicMock(return_value=False))  # 状态条件未命中
        resp = client.put("/api/scheduled-tasks/sched_abc123/schedule",
                          headers=_AUTH,
                          json={"schedule_type": "daily",
                                "time_config": {"hour": 9, "minute": 0}})
        assert resp.status_code == 409
        assert "不能改期" in resp.json()["detail"]

    def test_schedule_update_zero_row_without_row_404(self, client, monkeypatch):
        """零行更新且重读不到行（并发删除/越权）：404 不泄露存在性。"""
        monkeypatch.setattr(ScheduledTaskDB, "get_by_id", MagicMock(
            side_effect=[_task_row(), None]))
        monkeypatch.setattr(ScheduledTaskDB, "update_schedule",
                            MagicMock(return_value=False))
        resp = client.put("/api/scheduled-tasks/sched_abc123/schedule",
                          headers=_AUTH,
                          json={"schedule_type": "daily",
                                "time_config": {"hour": 9, "minute": 0}})
        assert resp.status_code == 404

    def test_schedule_update_on_terminal_status_409(self, client, monkeypatch):
        """终态（completed/expired）拒绝改期：completed 行不进到期扫描也无法
        恢复，改期会"成功落库但调度永不生效"（CR P2-5）。"""
        for status in ("completed", "expired"):
            monkeypatch.setattr(ScheduledTaskDB, "get_by_id",
                                MagicMock(return_value=_task_row(status=status)))
            monkeypatch.setattr(ScheduledTaskDB, "update_schedule",
                                MagicMock(return_value=True))
            resp = client.put("/api/scheduled-tasks/sched_abc123/schedule",
                              headers=_AUTH,
                              json={"schedule_type": "daily",
                                    "time_config": {"hour": 9, "minute": 0}})
            assert resp.status_code == 409, status
            ScheduledTaskDB.update_schedule.assert_not_called()

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
                "sched_1", notification_rule_id="nr_1",
                clear_fields=["notification_rule_id"],
                tenant_id="tenant_1", user_id="user_1")
        assert ok is True
        sql = cursor.execute.call_args.args[0]
        params = cursor.execute.call_args.args[1]
        assert "notification_rule_id = NULL" in sql
        # 显式传入的 nr_1 不再作为参数出现（清空优先）
        assert "nr_1" not in params

    def test_clear_fields_unknown_value_rejected(self):
        with pytest.raises(ValueError, match="INVALID_CLEAR_FIELDS"):
            ScheduledTaskDB.update_definition("sched_1", clear_fields=["name"])

    def test_set_without_clear_keeps_value(self):
        """设置路径（无 clear_fields）：notification_rule_id 正常落参数"""
        from unittest.mock import MagicMock, patch
        from contextlib import contextmanager

        cursor = MagicMock()
        cursor.rowcount = 1

        @contextmanager
        def fake_conn():
            yield MagicMock(cursor=MagicMock(return_value=cursor))

        with patch("src.scheduler.db.get_db_connection", fake_conn):
            ok = ScheduledTaskDB.update_definition(
                "sched_1", notification_rule_id="nr_1",
                tenant_id="tenant_1", user_id="user_1")
        assert ok is True
        params = cursor.execute.call_args.args[1]
        assert "nr_1" in params
        assert "NULL" not in cursor.execute.call_args.args[0].replace("updated_at = now()", "")


# ==================== 通知规则归属校验（DAL） ====================

class TestNotifyRuleVisible:
    def _run(self, monkeypatch, fetchone_result, rule_id="rule_x",
             tenant_id="tenant_1"):
        cursor = MagicMock()
        cursor.fetchone.return_value = fetchone_result

        @contextmanager
        def fake_conn():
            yield MagicMock(cursor=MagicMock(return_value=cursor))

        monkeypatch.setattr("src.scheduler.db.get_db_connection", fake_conn)
        return ScheduledTaskDB.notify_rule_visible(
            rule_id, tenant_id=tenant_id), cursor

    def test_visible_rule_scoped_to_own_tenant(self, monkeypatch):
        ok, cursor = self._run(monkeypatch, {"1": 1})
        assert ok is True
        sql, params = cursor.execute.call_args.args
        # 同口径限定：本租户 + scheduled.task + enabled（不含他租户/停用规则）
        assert "tenant_id = %s" in sql
        assert "event_type = 'scheduled.task'" in sql
        assert "enabled" in sql
        assert params == ("rule_x", "tenant_1")

    def test_invisible_rule_false(self, monkeypatch):
        ok, _ = self._run(monkeypatch, None)
        assert ok is False

    def test_fail_closed_on_missing_tenant_or_bad_input(self, monkeypatch):
        # 租户缺失（platform_admin 未带 X-Tenant-Id）与超长/空 ID 直接拒绝
        ok, cursor = self._run(monkeypatch, {"1": 1}, tenant_id=None)
        assert ok is False and not cursor.execute.called
        ok, cursor = self._run(monkeypatch, {"1": 1}, rule_id="x" * 129)
        assert ok is False and not cursor.execute.called
        ok, cursor = self._run(monkeypatch, {"1": 1}, rule_id="")
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


# ==================== 时间窗 / 阈值 / completed 手动执行（设计 §15，2026-11-03） ====================

class TestScheduleConstraints:
    def test_limits_returns_thresholds_with_tenant_effective_value(self, client, monkeypatch):
        """GET /limits：回传最短间隔与租户有效任务上限（前端不再硬编码第二套值）"""
        monkeypatch.setattr(scheduled_task_api, "min_interval_hours", lambda: 2)
        effective = MagicMock(return_value=8)
        monkeypatch.setattr(scheduled_task_api, "max_tasks_per_user", effective)
        resp = client.get("/api/scheduled-tasks/limits", headers=_AUTH)
        assert resp.status_code == 200
        assert resp.json()["data"] == {"min_interval_hours": 2, "max_tasks_per_user": 8}
        # 有效上限按当前租户解析（企业设置覆盖值优先在后端领域函数内完成）
        effective.assert_called_once_with("tenant_1")

    def test_limits_route_registered_before_dynamic_task_id(self):
        """路由顺序回归：/limits 必须先于 /{task_id} 注册"""
        paths = [route.path for route in scheduled_task_api.router.routes]
        assert paths.index("/api/scheduled-tasks/limits") \
            < paths.index("/api/scheduled-tasks/{task_id}")

    def test_schedule_update_with_window_passes_bounds(self, client, monkeypatch):
        """改期带时间窗：start_at/ends_at 与调度定义同事务更新；未来起点时
        首个计划点=窗内首个匹配点（2027-01-01 09:00，确定于当前日期）"""
        monkeypatch.setattr(ScheduledTaskDB, "get_by_id",
                            MagicMock(return_value=_task_row()))
        monkeypatch.setattr(ScheduledTaskDB, "update_schedule",
                            MagicMock(return_value=True))
        resp = client.put("/api/scheduled-tasks/sched_abc123/schedule",
                          headers=_AUTH,
                          json={"schedule_type": "daily",
                                "time_config": {"hour": 9, "minute": 0,
                                                "start_at": "2027-01-01 00:00:00",
                                                "ends_at": "2027-02-01 00:00:00"}})
        assert resp.status_code == 200
        kwargs = ScheduledTaskDB.update_schedule.call_args.kwargs
        assert kwargs["start_at"] == datetime(2027, 1, 1)
        assert kwargs["ends_at"] == datetime(2027, 2, 1)
        assert kwargs["next_due_at"] == datetime(2027, 1, 1, 9, 0)
        data = resp.json()["data"]
        assert data["start_at"] == "2027-01-01T00:00:00"

    def test_schedule_update_window_without_trigger_point_422(self, client, monkeypatch):
        """窗口内无可执行触发点（窗口已整体过去）：422 明确回执"""
        monkeypatch.setattr(ScheduledTaskDB, "get_by_id",
                            MagicMock(return_value=_task_row()))
        resp = client.put("/api/scheduled-tasks/sched_abc123/schedule",
                          headers=_AUTH,
                          json={"schedule_type": "daily",
                                "time_config": {"hour": 9, "minute": 0,
                                                "start_at": "2020-11-01 00:00:00",
                                                "ends_at": "2020-12-01 00:00:00"}})
        assert resp.status_code == 422
        assert "时间窗" in resp.json()["detail"]

    def test_schedule_update_fractional_interval_422(self, client, monkeypatch):
        """0.5 小时（分钟精度）与 1.5 小时改期均被拒绝（§15.2.5）"""
        monkeypatch.setattr(ScheduledTaskDB, "get_by_id",
                            MagicMock(return_value=_task_row()))
        for bad in (0.5, 1.5):
            resp = client.put("/api/scheduled-tasks/sched_abc123/schedule",
                              headers=_AUTH,
                              json={"schedule_type": "interval",
                                    "time_config": {"interval_hours": bad}})
            assert resp.status_code == 422, bad

    def test_once_with_window_rejected_422(self, client, monkeypatch):
        """once 不接受窗口字段（run_at 自带单次语义，宽限规则不变）"""
        monkeypatch.setattr(ScheduledTaskDB, "get_by_id",
                            MagicMock(return_value=_task_row()))
        resp = client.put("/api/scheduled-tasks/sched_abc123/schedule",
                          headers=_AUTH,
                          json={"schedule_type": "once",
                                "time_config": {"run_at": "2026-12-01 09:00:00",
                                                "ends_at": "2026-12-02 00:00:00"}})
        assert resp.status_code == 422
        assert "once" in resp.json()["detail"]

    def test_trigger_allows_window_completed_recurring(self, client, monkeypatch):
        """§15.2.4：时间窗自动完成的循环任务可「立即执行一次」（不改自动计划点）"""
        monkeypatch.setattr(ScheduledTaskDB, "get_by_id",
                            MagicMock(return_value=_task_row(
                                status="completed", schedule_type="interval",
                                interval_seconds=7200,
                                ends_at=datetime(2026, 10, 27))))
        monkeypatch.setattr(ScheduledTaskDB, "submit_manual",
                            MagicMock(return_value=_task_row(pending_trigger="manual")))
        resp = client.post("/api/scheduled-tasks/sched_abc123/trigger", headers=_AUTH)
        assert resp.status_code == 200
        ScheduledTaskDB.submit_manual.assert_called_once()

    def test_trigger_rejects_completed_once(self, client, monkeypatch):
        """once 的 completed 终态不可手动执行（额度已消费）"""
        monkeypatch.setattr(ScheduledTaskDB, "get_by_id",
                            MagicMock(return_value=_task_row(
                                status="completed", schedule_type="once",
                                run_at=datetime(2026, 10, 20))))
        monkeypatch.setattr(ScheduledTaskDB, "submit_manual",
                            MagicMock(return_value=None))
        resp = client.post("/api/scheduled-tasks/sched_abc123/trigger", headers=_AUTH)
        assert resp.status_code == 409

    def test_resume_window_finished_returns_completed_receipt(self, client, monkeypatch):
        """恢复时窗口已结束：任务转 completed，回执说明不再自动执行（§15.2.4）"""
        monkeypatch.setattr(ScheduledTaskDB, "get_by_id",
                            MagicMock(return_value=_task_row(status="paused")))
        monkeypatch.setattr(ScheduledTaskDB, "resume_recompute",
                            MagicMock(return_value=_task_row(status="completed",
                                                             next_due_at=None)))
        resp = client.post("/api/scheduled-tasks/sched_abc123/resume", headers=_AUTH)
        assert resp.status_code == 200
        body = resp.json()
        assert body["completed"] is True and "时间窗" in body["message"]
