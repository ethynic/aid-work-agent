"""runner 终态通知触发判定（wecom-app-task-notification-design §4.2）：

web 渠道用户自有会话才发布 task.completed/failed；时长阈值
notifications.wecom_task_min_seconds 仅作用于 source=chat 的手动任务
（299s 不推、≥300s 推），scheduled 定时任务不受时长门禁。
平台告警 runner.long_running 不经过该判定。
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from src.config.settings import NotificationsConfig
from src.notifications.producer import (_runner_duration_seconds,
                                        _should_notify_terminal,
                                        publish_platform_runner_alert_in_tx,
                                        publish_runner_terminal_in_tx)

START = datetime(2026, 11, 13, 10, 0, tzinfo=timezone.utc)


def runner(duration_seconds=600, **kw):
    """chat/web 用户自有终态 runner（时长可调，finished = start + duration）。"""
    row = dict(source='chat', session_kind='web', actor_kind='user', actor_id='user',
               user_id='user', tenant_id='tenant', scope_key='tenant:tenant',
               status='completed', session_id='session', runner_id='runner',
               execution_started_at=START,
               finished_at=START + timedelta(seconds=duration_seconds),
               accepted_at=START)
    row.update(kw)
    return row


@pytest.fixture
def published(monkeypatch):
    """拦截 producer 函数内延迟导入的 events.publish_in_tx，捕获发布参数。"""
    from src.notifications import events as events_mod
    calls = []

    def spy(cursor, **kwargs):
        calls.append(kwargs)
        return 'notification_ref'

    monkeypatch.setattr(events_mod, 'publish_in_tx', spy)
    return calls


class TestDurationGate:
    def test_web_session_long_task_publishes(self, published):
        """web 会话 + 时长 ≥300 → 发布 task.completed（既有事件与幂等键不变）。"""
        cursor = MagicMock()
        cursor.fetchone.return_value = {'title': '会话A'}
        publish_runner_terminal_in_tx(cursor, runner(), NotificationsConfig())
        assert len(published) == 1
        event = published[0]
        assert event['event_type'] == 'task.completed'
        assert event['event_key'] == 'terminal:runner'
        assert event['producer'] == 'runner'
        # 时长随终态事件带入（设计 §4.3，textcard 渲染耗时用）
        assert event['parameters'] == {'session_title': '会话A', 'event_kind': 'completed',
                                       'duration_seconds': 600}

    def test_short_task_not_published_and_no_row_written(self, published):
        """web 会话 + 时长 299 → 不发布；阈值判定在发布前，不产生任何 DB 访问
        （短任务连 notification_messages 行都不写）。"""
        cursor = MagicMock()
        publish_runner_terminal_in_tx(cursor, runner(duration_seconds=299),
                                      NotificationsConfig())
        assert published == []
        cursor.execute.assert_not_called()

    def test_threshold_boundary_300_publishes(self, published):
        """边界：时长恰好等于阈值（300s）→ 发布（≥ 判定）。"""
        cursor = MagicMock()
        cursor.fetchone.return_value = {'title': '会话A'}
        publish_runner_terminal_in_tx(cursor, runner(duration_seconds=300),
                                      NotificationsConfig())
        assert len(published) == 1

    def test_unknown_duration_not_published(self, published):
        """execution_started_at 为空（未开始执行即终态）视为时长未知 → 不推送。"""
        row = runner()
        row['execution_started_at'] = None
        cursor = MagicMock()
        publish_runner_terminal_in_tx(cursor, row, NotificationsConfig())
        assert published == []
        cursor.execute.assert_not_called()

    def test_config_injection_overrides_threshold(self, published):
        """阈值从 config 注入可调：阈值 10s 时 299s 的任务照常发布。"""
        cursor = MagicMock()
        cursor.fetchone.return_value = {'title': '会话A'}
        config = NotificationsConfig(wecom_task_min_seconds=10)
        publish_runner_terminal_in_tx(cursor, runner(duration_seconds=299), config)
        assert len(published) == 1

    def test_default_settings_path_reads_notifications_config(self, monkeypatch, published):
        """config=None 时按项目惯例读 settings.notifications（可注入替换）。"""
        from src.config.settings import settings
        monkeypatch.setattr(settings, 'notifications',
                            NotificationsConfig(wecom_task_min_seconds=0))
        row = runner()
        row['execution_started_at'] = None  # 时长未知，但阈值 0 表示不设门禁
        cursor = MagicMock()
        cursor.fetchone.return_value = {'title': '会话A'}
        publish_runner_terminal_in_tx(cursor, row)
        assert len(published) == 1
        # 时长未知按 0 带入（渲染侧兜底不显示耗时）
        assert published[0]['parameters']['duration_seconds'] == 0

    def test_terminal_parameters_carry_chat_duration(self, published):
        """设计 §4.3：chat 终态触发判定已算得的时长随事件带入（TEXTCARD 渲染耗时）。"""
        cursor = MagicMock()
        cursor.fetchone.return_value = {'title': '会话A'}
        publish_runner_terminal_in_tx(cursor, runner(duration_seconds=332),
                                      NotificationsConfig())
        assert published[0]['parameters']['duration_seconds'] == 332

    def test_scheduled_terminal_parameters_carry_duration(self, published):
        """scheduled 终态（不受时长门禁）同样把时长带入事件参数。"""
        from unittest.mock import patch
        scheduled = runner(duration_seconds=125, source='scheduled',
                           actor_kind='system', actor_id='scheduler',
                           input={'request_data': {'scheduled': {'task_id': 'sched_1'}}})
        cursor = MagicMock()
        cursor.fetchone.side_effect = [
            {'title': '每日日报'},
            {'task_id': 'sched_1', 'name': '每日日报', 'notification_rule_id': None,
             'notification_target_id': None, 'task_prompt': ''},
        ]
        with patch('src.services.task_result_sharing.service'
                   '.ensure_task_result_share_in_tx', lambda *args: 'share_key'):
            publish_runner_terminal_in_tx(cursor, scheduled, NotificationsConfig())
        assert len(published) == 1
        assert published[0]['parameters']['duration_seconds'] == 125


class TestWebSessionOnly:
    @pytest.mark.parametrize('source', ['wecom_kf', 'channel_bot'])
    def test_non_web_source_never_publishes(self, published, source):
        """非 web 渠道会话（source=wecom_kf 等）不发布——时长再长也不推。"""
        row = runner(duration_seconds=3600, source=source)
        cursor = MagicMock()
        publish_runner_terminal_in_tx(cursor, row, NotificationsConfig())
        assert published == []
        cursor.execute.assert_not_called()

    def test_channel_session_kind_never_publishes(self, published):
        row = runner(duration_seconds=3600, session_kind='channel')
        cursor = MagicMock()
        publish_runner_terminal_in_tx(cursor, row, NotificationsConfig())
        assert published == []
        cursor.execute.assert_not_called()

    def test_scheduled_ignores_duration_gate_chat_does_not(self, published):
        """设计 §4.2：仅 source=chat 受时长门禁——299s 的 scheduled 照常发布，
        同样 299s 的 chat 不发布（避免快问快答被推送只针对手动任务）。"""
        from unittest.mock import patch
        scheduled = runner(duration_seconds=299, source='scheduled',
                           actor_kind='system', actor_id='scheduler',
                           input={'request_data': {'scheduled': {'task_id': 'sched_1'}}})
        cursor = MagicMock()
        cursor.fetchone.side_effect = [
            {'title': '每日日报'},
            {'task_id': 'sched_1', 'name': '每日日报', 'notification_rule_id': None,
             'notification_target_id': None, 'task_prompt': ''},
        ]
        with patch('src.services.task_result_sharing.service'
                   '.ensure_task_result_share_in_tx', lambda *args: 'share_key'):
            publish_runner_terminal_in_tx(cursor, scheduled, NotificationsConfig())
        assert [event['event_type'] for event in published] == ['task.completed']

        chat = runner(duration_seconds=299)
        cursor = MagicMock()
        publish_runner_terminal_in_tx(cursor, chat, NotificationsConfig())
        assert len(published) == 1  # chat 299s 未追加发布
        cursor.execute.assert_not_called()


class TestTerminalKinds:
    @pytest.mark.parametrize('status', ['completed', 'failed'])
    def test_completed_and_failed_share_the_same_gate(self, published, status):
        """task.failed 与 task.completed 同口径：同阈值、同事件键格式。"""
        long_cursor = MagicMock()
        long_cursor.fetchone.return_value = {'title': '会话A'}
        publish_runner_terminal_in_tx(long_cursor, runner(600, status=status),
                                      NotificationsConfig())
        assert published[-1]['event_type'] == 'task.' + status
        assert published[-1]['event_key'] == 'terminal:runner'

        short_cursor = MagicMock()
        publish_runner_terminal_in_tx(short_cursor, runner(299, status=status),
                                      NotificationsConfig())
        assert short_cursor.execute.call_count == 0
        assert len(published) == 1  # 短任务未追加发布


class TestPlatformAlertUnaffected:
    def test_alert_publishes_for_non_web_long_runner(self):
        """平台告警 runner.long_running 不经过终态触发判定：非 web 渠道的
        长 runner 照常发布全局告警（告警阈值与通知阈值互不相干）。"""
        row = runner(duration_seconds=1800, source='wecom_kf', session_kind='channel',
                     attempt=2, profile_id='profile_1')
        assert _should_notify_terminal(row, NotificationsConfig()) is False
        cursor = MagicMock()
        cursor.fetchone.side_effect = [
            {'status': 'running', 'attempt': 2, 'tenant_id': 'tenant',
             'profile_id': 'profile_1', 'execution_started_at': START},
            {'now': START}, {'notification_ref': 'ref_alert'},
        ]
        ref = publish_platform_runner_alert_in_tx(cursor, runner=row, duration_seconds=1800)
        assert ref == 'ref_alert'
        insert = cursor.execute.call_args_list[2].args
        assert 'INSERT INTO notification_messages' in insert[0]
        assert "'global',NULL,NULL" in insert[0]
        assert "'runner.long_running'" in insert[0]
        assert insert[1][3] == 'runner:2'

    def test_duration_helper_reads_runner_row_without_extra_queries(self):
        """时长取自 runner 行自带 execution_started_at/finished_at（事务尾
        RETURNING * 已含），调用点无需改动。"""
        row = runner(duration_seconds=305)
        assert _runner_duration_seconds(row) == 305
        assert _runner_duration_seconds({}) == 0
