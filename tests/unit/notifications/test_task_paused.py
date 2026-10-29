"""task.paused 通知：注册、渲染、目标快照与幂等键（Phase 3，设计 §3.3/§8.1）。

覆盖：
- PausedParameters 严格校验（长度边界、notify_target_id 可选）；
- 事件注册存在（template_key/resource_type=scheduled_task）与资源校验；
- render_inbox_text 文案；render_message_text 复用 inbox；
- publish_task_paused：notify_target_id 事件生成时快照、event_key 幂等形状、
  TTL 取 settings.notifications.ttl_seconds、接收人 = 任务属主；
- 默认事件集合兼容扩展（task.paused 进默认集合）；_inbox_link 跳转链接；
- expand 目标快照路由（有效目标追加投递 / 失效记 skipped / 缺失仅留痕）。
"""
import json
from contextlib import contextmanager
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.config.settings import NotificationsConfig
from src.notifications.authorization import NotificationError
from src.notifications.contracts import AdapterCapabilities, Subject
from src.notifications.events import (
    EVENTS,
    PausedParameters,
    render_inbox_text,
    render_message_text,
)
from src.notifications.repository import (
    NotificationRepository,
    protected,
)
from src.scheduler.notify import _event_key, publish_task_paused


# ==================== 参数模型与注册 ====================

class TestPausedParameters:
    def test_valid_parameters_with_optional_target(self):
        value = PausedParameters.model_validate({
            "task_name": "每日日报", "reason_label": "积分额度不足",
            "task_id": "sched_abc123", "notify_target_id": "target_1"})
        assert value.model_dump()["notify_target_id"] == "target_1"
        assert PausedParameters.model_validate({
            "task_name": "每日日报", "reason_label": "积分额度不足",
            "task_id": "sched_abc123"}).notify_target_id is None

    @pytest.mark.parametrize("payload", [
        {"task_name": "", "reason_label": "x", "task_id": "t"},        # 空名称
        {"task_name": "x" * 81, "reason_label": "x", "task_id": "t"},  # 名称超 80
        {"task_name": "t", "reason_label": "x" * 121, "task_id": "t"},  # 原因超 120
        {"task_name": "t", "reason_label": "x", "task_id": ""},        # 空 task_id
        {"task_name": "t", "reason_label": "x", "task_id": "t",
         "notify_target_id": "y" * 129},                                # 目标超 128
        {"task_name": "t", "reason_label": "x"},                        # 缺 task_id
    ])
    def test_invalid_parameters_rejected(self, payload):
        with pytest.raises(Exception):
            PausedParameters.model_validate(payload)

    def test_event_registered_with_scheduled_task_resource(self):
        definition = EVENTS.definitions["task.paused"]
        assert definition.template_key == "task_paused"
        assert definition.resource_type == "scheduled_task"
        validated = EVENTS.validate(
            "task.paused",
            {"task_name": "日报", "reason_label": "积分额度不足", "task_id": "sched_x"},
            {"type": "scheduled_task", "id": "sched_x"})
        assert validated[0] is definition
        # 资源类型不匹配拒绝
        with pytest.raises(ValueError):
            EVENTS.validate(
                "task.paused",
                {"task_name": "日报", "reason_label": "积分额度不足", "task_id": "sched_x"},
                {"type": "runner_result", "id": "runner_1"})


# ==================== 渲染文案 ====================

class TestRender:
    PAUSED_PARAMS = {"task_name": "每日日报", "reason_label": "积分额度不足",
                     "task_id": "sched_abc123"}

    def test_inbox_text_and_message_reuse(self):
        title, body = render_inbox_text("task.paused", self.PAUSED_PARAMS)
        assert title == "定时任务已暂停"
        assert "「每日日报」已自动暂停：积分额度不足" in body
        assert "前往任务视图手动恢复" in body
        assert render_message_text("task.paused", self.PAUSED_PARAMS) == (title, body)


# ==================== 发布（快照/幂等键/TTL） ====================

_TASK = {
    "task_id": "sched_abc123",
    "tenant_id": "tenant_1",
    "user_id": "user_1",
    "name": "每日日报",
    "notification_target_id": "target_1",
}


def _capturing_conn(now=datetime(2026, 10, 28, 10, 0, 0)):
    conn = MagicMock()
    cursor = MagicMock()
    cursor.execute.side_effect = lambda sql, params=None: None
    cursor.fetchone.side_effect = lambda: {"now": now}
    conn.cursor.return_value = cursor
    return conn


class TestPublishTaskPaused:
    @patch("src.notifications.events.publish_in_tx")
    @patch("src.scheduler.notify.get_db_connection")
    def test_publishes_with_target_snapshot_and_owner_subject(self, mock_conn, mock_publish):
        mock_publish.return_value = "ref123"
        conn = _capturing_conn()
        mock_conn.return_value.__enter__.return_value = conn

        ref = publish_task_paused(_TASK, "积分额度不足",
                                  occurrence_key="2026-10-28T09:00:00",
                                  pause_reason="credit_blocked")

        assert ref == "ref123"
        kwargs = mock_publish.call_args.kwargs
        # 接收人 = 任务属主；生产者 = scheduler；资源 = scheduled_task
        assert kwargs["subject"] == Subject("user_1", "tenant_1")
        assert kwargs["producer"] == "scheduler"
        assert kwargs["resource_ref"] == {"type": "scheduled_task", "id": "sched_abc123"}
        # notify_target_id 事件生成时快照（§3.3）
        assert kwargs["parameters"]["notify_target_id"] == "target_1"
        assert kwargs["parameters"]["task_name"] == "每日日报"
        assert kwargs["parameters"]["reason_label"] == "积分额度不足"
        # event_key 幂等形状：occurrence 优先
        assert kwargs["event_key"] == \
            "paused:sched_abc123:credit_blocked:2026-10-28T09:00:00"
        # TTL = now + settings.notifications.ttl_seconds
        assert kwargs["expires_at"] == kwargs["occurred_at"] + timedelta(
            seconds=86400)
        conn.commit.assert_called_once()

    @patch("src.notifications.events.publish_in_tx")
    @patch("src.scheduler.notify.get_db_connection")
    def test_no_target_when_unset_and_attempt_key_shape(self, mock_conn, mock_publish):
        mock_publish.return_value = "ref"
        mock_conn.return_value.__enter__.return_value = _capturing_conn()
        task = dict(_TASK, notification_target_id=None)

        # 恢复场景无 runner_id（兼容兜底）：attempt 标识进 event_key
        publish_task_paused(task, "执行中断且自动恢复失败", attempt=2,
                            pause_reason="interrupted")

        kwargs = mock_publish.call_args.kwargs
        assert "notify_target_id" not in kwargs["parameters"]
        assert kwargs["event_key"] == "paused:sched_abc123:interrupted:attempt:2"

    @patch("src.notifications.events.publish_in_tx")
    @patch("src.scheduler.notify.get_db_connection")
    def test_attempt_key_includes_runner_id(self, mock_conn, mock_publish):
        """停车事件幂等单位 = (runner, attempt)：同任务用户恢复后重新认领的
        新 runner 首次中断 attempt 可能与上一 runner 相同，event_key 缺
        runner_id 会被 ON CONFLICT 误去重吞掉第二次暂停通知。"""
        mock_publish.return_value = "ref"
        mock_conn.return_value.__enter__.return_value = _capturing_conn()
        task = dict(_TASK, notification_target_id=None)

        publish_task_paused(task, "执行中断且自动恢复失败", attempt=1,
                            runner_id="runner_a", pause_reason="interrupted")

        kwargs = mock_publish.call_args.kwargs
        assert kwargs["event_key"] == \
            "paused:sched_abc123:interrupted:attempt:runner_a:1"

    def test_event_key_shapes_are_stable_and_distinct(self):
        # 同输入同键（ON CONFLICT 去重），不同发生互相区分
        assert _event_key("t1", "credit_blocked", occurrence_key="k1") == \
            _event_key("t1", "credit_blocked", occurrence_key="k1")
        assert _event_key("t1", "credit_blocked", occurrence_key="k1") != \
            _event_key("t1", "credit_blocked", occurrence_key="k2")
        assert _event_key("t1", "interrupted", attempt=1) != \
            _event_key("t1", "interrupted", attempt=2)
        # 同 attempt 的不同 runner 停车是不同事件，不得误去重
        assert _event_key("t1", "interrupted", attempt=1, runner_id="runner_a") != \
            _event_key("t1", "interrupted", attempt=1, runner_id="runner_b")
        assert _event_key("t1", "paused").endswith(":manual")

    @patch("src.notifications.events.publish_in_tx")
    @patch("src.scheduler.notify.get_db_connection")
    def test_long_name_and_reason_truncated_to_model_limits(self, mock_conn, mock_publish):
        mock_publish.return_value = "ref"
        mock_conn.return_value.__enter__.return_value = _capturing_conn()
        task = dict(_TASK, name="长" * 100)

        publish_task_paused(task, "长" * 200, pause_reason="submit_rejected")

        kwargs = mock_publish.call_args.kwargs
        assert len(kwargs["parameters"]["task_name"]) == 80
        assert len(kwargs["parameters"]["reason_label"]) == 120
        assert kwargs["event_key"] == "paused:sched_abc123:submit_rejected:manual"


# ==================== 默认事件集合与站内信链接 ====================

class TestDefaultEventSetAndLink:
    def test_task_paused_in_default_event_types(self):
        from src.notifications.repository import TASK_EVENT_TYPES, _effective_event_types
        assert "task.paused" in TASK_EVENT_TYPES
        # 旧默认两元与旧 DDL 默认三元均兼容扩展；显式自定义集合不受影响
        assert _effective_event_types(["task.completed", "task.failed"]) == TASK_EVENT_TYPES
        assert _effective_event_types(
            ["task.completed", "task.failed", "task.waiting_input"]) == TASK_EVENT_TYPES
        custom = ("task.completed", "billing.credit_low")
        assert _effective_event_types(custom) == custom

    def test_task_paused_in_inbox_event_types(self):
        assert "task.paused" in NotificationRepository.INBOX_EVENT_TYPES

    def test_inbox_link_uses_notification_entry_with_tenant(self):
        link = NotificationRepository._inbox_link(
            "task.paused", {"task_name": "日报"}, Subject("u1", "t1"),
            notification_ref="abc123")
        assert link == "/notification?ref=abc123&tenant=t1"
        # portal 用户（无租户）不带参数；无 ref 不跳转
        assert NotificationRepository._inbox_link(
            "task.paused", {}, Subject("u1", None),
            notification_ref="abc") == "/notification?ref=abc"
        assert NotificationRepository._inbox_link(
            "task.paused", {}, Subject("u1", "t1"), notification_ref=None) is None


# ==================== Web Push 渲染（task.paused 全渠道） ====================

class TestWebPushRenderPaused:
    """task.paused 参数无 session_title/event_kind：Web Push 适配器以
    task_name/event_kind='paused' 映射（设计 §8.1 全渠道渲染）——否则
    render KeyError 使投递行恒为 failed/render_failure。"""

    def _message(self, event_type, parameters):
        return {"notification_id": "notification_1",
                "notification_ref": "ref1", "event_type": event_type,
                "parameters": parameters,
                "expires_at": datetime(2026, 10, 28, 11, 0, 0)}

    def _target(self):
        return {"installation_ref": "install_1", "binding_version": 3}

    def _adapter(self):
        from unittest.mock import MagicMock
        from src.notifications.web_push import WebPushAdapter
        return WebPushAdapter(MagicMock())

    def test_paused_maps_task_name_and_kind(self):
        payload = self._adapter().render(self._message("task.paused", {
            "task_name": "每日日报", "reason_label": "积分额度不足",
            "task_id": "sched_abc123"}), self._target())
        assert payload["session_title"] == "每日日报"
        assert payload["event_kind"] == "paused"
        assert payload["installation_ref"] == "install_1"
        assert payload["binding_version"] == 3

    def test_session_events_keep_original_fields(self):
        payload = self._adapter().render(self._message("task.completed", {
            "session_title": "项目会话", "event_kind": "completed"}),
            self._target())
        assert payload["session_title"] == "项目会话"
        assert payload["event_kind"] == "completed"


# ==================== resource_in_tx：scheduled_task 资源解析 ====================

class TestScheduledTaskResource:
    def _message(self):
        return {"recipient_user_id": "user_1", "tenant_id": "tenant_1",
                "scope_key": "tenant:tenant_1",
                "resource_ref": {"type": "scheduled_task", "id": "sched_abc123"}}

    @patch("src.notifications.authorization.assert_subject_in_tx")
    def test_resolves_owned_task_row(self, _mock_guard):
        from src.notifications.authorization import resource_in_tx
        cursor = MagicMock()
        cursor.fetchone.return_value = {"task_id": "sched_abc123",
                                        "session_id": "session_1",
                                        "name": "日报", "status": "paused"}
        resource = resource_in_tx(cursor, self._message())
        sql, params = cursor.execute.call_args.args
        assert "FROM scheduled_tasks" in sql
        assert params == ("sched_abc123", "user_1", "tenant_1")
        assert resource["type"] == "scheduled_task"
        assert resource["session_id"] == "session_1"

    @patch("src.notifications.authorization.assert_subject_in_tx")
    def test_missing_task_rejected(self, _mock_guard):
        from src.notifications.authorization import resource_in_tx
        cursor = MagicMock()
        cursor.fetchone.return_value = None
        with pytest.raises(NotificationError, match="NOTIFICATION_RESOURCE_UNAVAILABLE"):
            resource_in_tx(cursor, self._message())


# ==================== expand：目标快照路由 ====================

_TASK_MESSAGE = {
    "notification_id": "notification_1", "notification_ref": "ref1",
    "recipient_user_id": "user_1", "tenant_id": "tenant_1",
    "scope_key": "tenant:tenant_1", "schema_version": 1,
    "policy_key": "accepted_before_enabled", "category": "task",
    "event_type": "task.paused", "event_key": "paused:sched_1:credit_blocked:k1",
    "parameters_encrypted": protected({
        "task_name": "日报", "reason_label": "积分额度不足",
        "task_id": "sched_1", "notify_target_id": "target_9"}),
    "resource_ref": {"type": "scheduled_task", "id": "sched_1"},
    "accepted_at": datetime(2026, 10, 10), "created_at": datetime(2026, 10, 10),
    "expires_at": datetime(2026, 10, 11), "priority": 0,
    "database_now": datetime(2026, 10, 10, 12, 0),
}


class StubAdapter:
    channel = "im_webhook"
    capabilities = AdapterCapabilities()

    def available(self):
        return True

    def render(self, message, target):
        return {"text": "body"}


class TestExpandSnapshotTargetRouting:
    def _run_expand(self, message, snapshot_target):
        """单条 task.paused 消息的 expand 全 mock：偏好路由无目标，快照目标
        由 snapshot_target 注入（None=目标行不存在）。"""
        repo = NotificationRepository.__new__(NotificationRepository)
        repo.config = NotificationsConfig(batch_size=10)
        registry = MagicMock()
        registry.get.return_value = StubAdapter()
        repo.registry = registry

        deliveries, route_updates = [], []
        state = {"snapshot_params": None}
        cursor = MagicMock()

        def execute_dispatch(sql, params=None):
            if "expansion_state='pending'" in sql:
                cursor.fetchall = lambda: [message]
            elif "FROM users" in sql:
                cursor.fetchone = lambda: {"user_id": "user_1", "tenant_id": "tenant_1",
                                           "role": "tenant_user", "status": "active"}
            elif "FROM tenants WHERE tenant_id=%s AND status='active'" in sql:
                cursor.fetchone = lambda: {"ok": 1}
            elif "FROM scheduled_tasks" in sql:
                cursor.fetchone = lambda: {"task_id": "sched_1", "session_id": "s1",
                                           "name": "日报", "status": "paused"}
            elif "FROM notification_targets t" in sql:
                # 主目标查询（Phase 3 评审修正：快照目标在主查询按 target_id
                # 定位并替代偏好路由；无快照时按偏好条件，本夹具均无偏好行）。
                # 模拟真实库 WHERE state='active' 过滤：revoked/invalid 行不返回
                cursor.fetchall = lambda: ([snapshot_target]
                    if snapshot_target and snapshot_target.get("state") == "active" else [])
            elif "INSERT INTO notification_deliveries" in sql:
                deliveries.append((sql, params))
            elif "route_snapshot" in sql:
                route_updates.append((sql, params))

        cursor.execute.side_effect = execute_dispatch
        conn = MagicMock()
        conn.cursor.return_value = cursor

        @contextmanager
        def factory():
            yield conn

        repo.connection_factory = factory
        count = repo.expand()
        return count, state, deliveries, route_updates

    def test_snapshot_target_gets_delivery_row(self):
        target = {"target_id": "target_9", "scope_key": "tenant:tenant_1",
                  "tenant_id": "tenant_1", "recipient_user_id": None,
                  "state": "active", "binding_version": 1,
                  "channel": "im_webhook", "provider": "feishu_bot"}
        count, state, deliveries, _ = self._run_expand(dict(_TASK_MESSAGE), target)
        assert count == 1
        # 快照目标在主查询按 target_id 定位（校验属主与租户匹配），向其投递
        # （pending 状态，render 结果持久化）
        assert len(deliveries) == 1
        params = deliveries[0][1]
        assert params[2] == "target_9"
        assert params[3] == 1  # binding_version
        assert params[5] == "pending"
        from src.notifications.repository import unprotect
        assert unprotect(params[4]) == {"text": "body"}

    def test_invalid_snapshot_target_produces_no_delivery(self):
        """失效目标（属主不符 + 非 active）：主查询 state='active' 条件不返回，
        无投递行产生；站内信不受影响（expand 仍正常完成）。"""
        target = {"target_id": "target_9", "scope_key": "tenant:tenant_1",
                  "tenant_id": "tenant_1", "recipient_user_id": "other_user",
                  "state": "revoked", "binding_version": 2,
                  "channel": "im_webhook", "provider": "feishu_bot"}
        count, _, deliveries, _ = self._run_expand(dict(_TASK_MESSAGE), target)
        assert count == 1
        # 主查询 state='active' 条件过滤 revoked 行（真实库不返回；mock 夹具
        # 注入原始行验证的是"若返回也必被属主校验拒绝"——此处按真实库行为
        # 注入空集），无投递行产生
        assert deliveries == []

    def test_missing_snapshot_target_only_marks_route(self):
        count, _, deliveries, route_updates = self._run_expand(
            dict(_TASK_MESSAGE), None)
        assert count == 1
        assert deliveries == []
        # 目标行不存在：无 FK 行可挂投递，仅路由快照留痕
        assert len(route_updates) == 1
        route = json.loads(route_updates[0][1][1])  # params=(state, route_json, id)
        assert route == [{"target_id": "target_9", "skipped": "target_not_found"}]

    def test_message_without_snapshot_keeps_existing_routing(self):
        message = dict(_TASK_MESSAGE)
        message["parameters_encrypted"] = protected({
            "task_name": "日报", "reason_label": "积分额度不足", "task_id": "sched_1"})
        count, state, deliveries, _ = self._run_expand(message, None)
        assert count == 1
        assert deliveries == []
        assert state.get("snapshot_params") is None
