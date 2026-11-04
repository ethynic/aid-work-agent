"""通知直达本次执行结果（设计 §15.5，2026-11-03）。

覆盖：
- scheduled_result_link：仅 scheduled 终态（parameters 带 task_id）生成
  绝对链接；普通聊天终态不生成（不误导到任务页）；未配置
  app.public_base_url 时不拼假地址（记录配置缺口、通知照常投递）；
- im_webhook / smtp_email 渲染：完成/失败文本附「查看本次执行结果」链接；
- 站内信：task.completed/failed 进 INBOX_EVENT_TYPES，_inbox_link 复用
  /notification?ref=...&tenant=... 登录后归属核验入口。
"""

from unittest.mock import MagicMock, patch

from src.notifications.events import scheduled_result_link
from src.notifications.im_webhook import FeishuBotAdapter
from src.notifications.repository import NotificationRepository, _effective_event_types
from src.notifications.smtp_email import SmtpEmailAdapter
from src.notifications.contracts import Subject


_TERMINAL_PARAMS = {
    'session_title': '每日日报', 'event_kind': 'completed',
    'task_id': 'sched_abc', 'task_name': '每日日报',
}


def _message(event_type='task.completed', params=None, tenant_id='t1', ref='ref99'):
    return {'notification_ref': ref, 'event_type': event_type,
            'tenant_id': tenant_id,
            'parameters': params if params is not None else dict(_TERMINAL_PARAMS)}


def _settings_with_base(base):
    mock = MagicMock()
    mock.app.public_base_url = base
    return mock


class TestScheduledResultLink:
    def test_scheduled_terminal_with_base_url_builds_absolute_link(self):
        with patch('src.config.settings.settings', _settings_with_base('https://app.example.com/')):
            link = scheduled_result_link(_message())
        assert link == ('https://app.example.com/notification'
                        '?ref=ref99&tenant=t1')

    def test_link_is_locator_only_no_share_key(self):
        """链接只是定位符：走既有 /notification?ref 归属核验入口，不含结果
        正文、分享密钥或免登录参数。"""
        with patch('src.config.settings.settings', _settings_with_base('https://app.example.com')):
            link = scheduled_result_link(_message())
        assert 'share' not in link and 'token' not in link
        assert link.startswith('https://app.example.com/notification?ref=')

    def test_chat_terminal_without_task_id_gets_no_link(self):
        """普通聊天终态（无 task_id 标记）不加任务页链接。"""
        params = {'session_title': '会话', 'event_kind': 'completed'}
        with patch('src.config.settings.settings', _settings_with_base('https://app.example.com')):
            assert scheduled_result_link(_message(params=params)) is None

    def test_missing_public_base_url_returns_none(self):
        """未配置公开站点地址：不拼假地址（通知文本照常投递，缺口已记录）。"""
        with patch('src.config.settings.settings', _settings_with_base('')):
            assert scheduled_result_link(_message()) is None

    def test_non_terminal_event_returns_none(self):
        with patch('src.config.settings.settings', _settings_with_base('https://app.example.com')):
            assert scheduled_result_link(
                _message(event_type='task.waiting_input')) is None

    def test_portal_message_without_tenant_omits_tenant_param(self):
        with patch('src.config.settings.settings', _settings_with_base('https://app.example.com')):
            link = scheduled_result_link(_message(tenant_id=None))
        assert link == 'https://app.example.com/notification?ref=ref99'


class TestImWebhookRender:
    def test_scheduled_terminal_text_appends_result_link(self):
        with patch('src.config.settings.settings', _settings_with_base('https://app.example.com')):
            payload = FeishuBotAdapter().render(_message(), {})
        assert '查看本次执行结果：https://app.example.com/notification?ref=ref99&tenant=t1' \
            in payload['text']

    def test_chat_terminal_text_has_no_result_link(self):
        params = {'session_title': '会话', 'event_kind': 'failed'}
        with patch('src.config.settings.settings', _settings_with_base('https://app.example.com')):
            payload = FeishuBotAdapter().render(
                _message(event_type='task.failed', params=params), {})
        assert '查看本次执行结果' not in payload['text']

    def test_no_base_url_keeps_plain_text(self):
        with patch('src.config.settings.settings', _settings_with_base('')):
            payload = FeishuBotAdapter().render(_message(), {})
        assert '查看本次执行结果' not in payload['text']
        assert '定时任务' in payload['text']  # 通知本身仍完整


class TestSmtpRender:
    def test_scheduled_terminal_body_appends_result_link(self):
        with patch('src.config.settings.settings', _settings_with_base('https://app.example.com')):
            payload = SmtpEmailAdapter().render(_message(), {})
        assert '查看本次执行结果：https://app.example.com/notification?ref=ref99&tenant=t1' \
            in payload['body']
        assert payload['subject'] == '定时任务执行结果'


class TestTerminalTextBySource:
    """评审 P2：task.completed/failed 为聊天与定时任务共用事件名——渲染按
    task_id 区分来源，普通聊天不显示"定时任务"文案。"""

    def test_scheduled_terminal_keeps_scheduled_wording(self):
        from src.notifications.events import render_inbox_text
        title, body = render_inbox_text('task.completed', dict(_TERMINAL_PARAMS))
        assert title == '定时任务执行结果'
        assert '定时任务「每日日报」已完成' in body

    def test_chat_terminal_uses_chat_wording(self):
        from src.notifications.events import render_inbox_text
        title, body = render_inbox_text(
            'task.completed', {'session_title': '需求分析', 'event_kind': 'completed'})
        assert title == '任务执行结果'
        assert body == '「需求分析」中的任务已完成。'
        assert '定时任务' not in body and '定时任务' not in title

    def test_chat_failed_terminal_uses_chat_wording(self):
        from src.notifications.events import render_inbox_text
        title, body = render_inbox_text(
            'task.failed', {'session_title': '需求分析', 'event_kind': 'failed'})
        assert '执行失败' in body and '定时任务' not in body


class TestManualRejectedEvent:
    """评审 P1：task.manual_rejected（窗口收尾任务手动执行被拒）站内信文案
    与事件注册（publish 校验依赖注册表与参数模型）。"""

    def test_event_registered_with_scheduled_task_resource(self):
        from src.notifications.events import EVENTS
        definition = EVENTS.definitions.get('task.manual_rejected')
        assert definition is not None
        assert definition.resource_type == 'scheduled_task'

    def test_inbox_text_states_completed_kept_and_retryable(self):
        from src.notifications.events import render_inbox_text
        title, body = render_inbox_text('task.manual_rejected', {
            'session_title': '每日日报', 'event_kind': 'manual_rejected',
            'task_name': '每日日报', 'task_id': 'sched_abc',
            'reason_label': '积分额度不足'})
        assert title == '手动执行未成功'
        assert '「立即执行一次」未提交成功：积分额度不足' in body
        assert '保持已完成状态' in body

    def test_manual_rejected_in_inbox_types_and_link(self):
        assert 'task.manual_rejected' in NotificationRepository.INBOX_EVENT_TYPES
        link = NotificationRepository._inbox_link(
            'task.manual_rejected', {'task_id': 'sched_abc'}, Subject('u1', 't1'),
            notification_ref='ref5')
        assert link == '/notification?ref=ref5&tenant=t1'

    def test_publish_in_tx_validates_parameters(self):
        """参数模型经 publish_in_tx 严格校验（白名单/长度），非法参数拒绝。"""
        import pytest as _pytest
        from unittest.mock import MagicMock
        from src.notifications.events import publish_in_tx
        from src.notifications.contracts import Subject
        from datetime import datetime, timedelta
        now = datetime(2026, 11, 4, 10, 0, 0)
        publish_in_tx(
            MagicMock(), subject=Subject('u1', 't1'), producer='scheduler',
            event_type='task.manual_rejected', event_key='manual-rejected:s1:m1',
            resource_ref={'type': 'scheduled_task', 'id': 'sched_abc'},
            parameters={'session_title': '每日日报', 'event_kind': 'manual_rejected',
                        'task_name': '每日日报', 'task_id': 'sched_abc',
                        'reason_label': '积分额度不足'},
            occurred_at=now, accepted_at=now, expires_at=now + timedelta(hours=1))
        with _pytest.raises(Exception):
            publish_in_tx(
                MagicMock(), subject=Subject('u1', 't1'), producer='scheduler',
                event_type='task.manual_rejected', event_key='k2',
                resource_ref={'type': 'scheduled_task', 'id': 'sched_abc'},
                parameters={'session_title': '每日日报', 'event_kind': 'paused'},
                occurred_at=now, accepted_at=now, expires_at=now + timedelta(hours=1))


class TestInboxIntegration:
    def test_terminal_events_in_inbox_types(self):
        assert 'task.completed' in NotificationRepository.INBOX_EVENT_TYPES
        assert 'task.failed' in NotificationRepository.INBOX_EVENT_TYPES

    def test_inbox_link_routes_via_notification_entry(self):
        link = NotificationRepository._inbox_link(
            'task.completed', _TERMINAL_PARAMS, Subject('u1', 't1'),
            notification_ref='ref7')
        assert link == '/notification?ref=ref7&tenant=t1'
        assert NotificationRepository._inbox_link(
            'task.failed', _TERMINAL_PARAMS, Subject('u1', None),
            notification_ref='ref8') == '/notification?ref=ref8'


# ==================== 默认偏好投递（§15 评审 P2，2026-11-04） ====================

class TestResolveCarriesInboxText:
    """§15 四轮评审 P2：resolve 响应附站内信同款 title/body——推送点击落地页
    可展示通知文案（如手动执行被拒的原因），仅模板渲染文本不带原始参数。"""

    def _run_resolve(self, message):
        from contextlib import contextmanager
        from unittest.mock import MagicMock

        from src.notifications.repository import NotificationRepository

        repo = NotificationRepository.__new__(NotificationRepository)
        repo.config = MagicMock()
        repo.config.allowed_tenants = None
        user_row = {"user_id": "user_1", "tenant_id": "tenant_1", "role": "user",
                    "status": "active"}
        task_row = {"task_id": "sched_1", "session_id": "s1", "name": "每日日报",
                    "status": "completed"}
        # 调用序列：guard(users,tenants) → message → resource_in_tx 内再次
        # (users,tenants) → scheduled_task 资源行
        cursor = MagicMock()
        cursor.fetchone.side_effect = [
            user_row, {"ok": 1}, message, user_row, {"ok": 1}, task_row]
        conn = MagicMock()
        conn.cursor.return_value = cursor

        @contextmanager
        def factory():
            yield conn

        repo.connection_factory = factory
        return repo.resolve(Subject("user_1", "tenant_1"), "refmr")

    @staticmethod
    def _message():
        from datetime import datetime

        from src.notifications.repository import protected
        return {
            "notification_ref": "refmr", "recipient_user_id": "user_1",
            "scope_key": "tenant:tenant_1", "tenant_id": "tenant_1",
            "event_type": "task.manual_rejected",
            "parameters_encrypted": protected({
                "session_title": "每日日报", "event_kind": "manual_rejected",
                "task_name": "每日日报", "task_id": "sched_1",
                "reason_label": "积分额度不足"}),
            "resource_ref": {"type": "scheduled_task", "id": "sched_1"},
        }

    def test_resolve_returns_title_and_body(self):
        result = self._run_resolve(self._message())
        assert result["title"] == "手动执行未成功"
        assert "积分额度不足" in result["body"]
        assert "原始参数" not in result and "parameters" not in result


class TestManualRejectedPreferenceDelivery:
    """task.manual_rejected 进默认事件集合：旧默认五元偏好行（DDL 历史默认）
    在展开偏好门控下等效扩展，实际产生 web_push 投递行。"""

    def _run_expand_with_preference_target(self, message, target):
        from contextlib import contextmanager
        from unittest.mock import MagicMock

        from src.config.settings import NotificationsConfig
        from src.notifications.contracts import AdapterCapabilities
        from src.notifications.repository import NotificationRepository

        class _StubAdapter:
            channel = "web_push"
            provider = "stub"
            capabilities = AdapterCapabilities()

            def available(self):
                return True

            def render(self, message, target):
                return {"text": "body"}

        repo = NotificationRepository.__new__(NotificationRepository)
        repo.config = NotificationsConfig(batch_size=10)
        registry = MagicMock()
        registry.get.return_value = _StubAdapter()
        repo.registry = registry

        deliveries = []
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
                                           "name": "每日日报", "status": "completed"}
            elif "FROM notification_targets t" in sql:
                cursor.fetchall = lambda: [target]
            elif "INSERT INTO notification_deliveries" in sql:
                deliveries.append(params)

        cursor.execute.side_effect = execute_dispatch
        conn = MagicMock()
        conn.cursor.return_value = cursor

        @contextmanager
        def factory():
            yield conn

        repo.connection_factory = factory
        repo.expand()
        return deliveries

    @staticmethod
    def _message():
        from datetime import datetime

        from src.notifications.repository import protected
        return {
            "notification_id": "notification_mr", "notification_ref": "refmr",
            "recipient_user_id": "user_1", "tenant_id": "tenant_1",
            "scope_key": "tenant:tenant_1", "schema_version": 1,
            "policy_key": "accepted_before_enabled", "category": "task",
            "event_type": "task.manual_rejected",
            "event_key": "manual-rejected:sched_1:m-uuid-1",
            "parameters_encrypted": protected({
                "session_title": "每日日报", "event_kind": "manual_rejected",
                "task_name": "每日日报", "task_id": "sched_1",
                "reason_label": "积分额度不足"}),
            "resource_ref": {"type": "scheduled_task", "id": "sched_1"},
            "accepted_at": datetime(2026, 10, 10), "created_at": datetime(2026, 10, 10),
            "expires_at": datetime(2026, 10, 11), "priority": 0,
            "database_now": datetime(2026, 10, 10, 12, 0),
        }

    def test_legacy_default_preference_delivers_manual_rejected(self):
        from datetime import datetime

        from src.notifications.repository import (
            LEGACY_DEFAULT_TASK_EVENT_TYPES_V2, TASK_EVENT_TYPES)

        # 集合兼容：旧默认五元 → 当前默认集合（含 manual_rejected）
        assert "task.manual_rejected" in TASK_EVENT_TYPES
        assert "task.manual_rejected" in _effective_event_types(
            LEGACY_DEFAULT_TASK_EVENT_TYPES_V2)

        # 偏好门控（expand）：旧默认五元偏好的个人 web_push 目标实际拿到投递行
        target = {
            "target_id": "target_wp", "scope_key": "tenant:tenant_1",
            "tenant_id": "tenant_1", "recipient_user_id": "user_1",
            "state": "active", "binding_version": 3,
            "channel": "web_push", "provider": "stub",
            "enabled_since": datetime(2026, 10, 1),
            "preference_enabled": True,
            "preference_since": datetime(2026, 10, 1),
            "event_types": list(LEGACY_DEFAULT_TASK_EVENT_TYPES_V2),
        }
        deliveries = self._run_expand_with_preference_target(self._message(), target)
        assert len(deliveries) == 1
        assert deliveries[0][2] == "target_wp"  # delivery → notification → target_id

    def test_custom_event_set_not_extended(self):
        from src.notifications.repository import _effective_event_types
        custom = ["task.completed", "billing.credit_low"]
        assert _effective_event_types(custom) == tuple(custom)
        assert "task.manual_rejected" not in _effective_event_types(custom)
