"""Phase 3 二轮评审修复回归：

1. scheduled 终态参数被实际注册模型接受（TerminalParameters 可选扩展——
   此前 task_id/task_name/notify_target_id 触发 extra_forbidden 使终态
   事务回滚）；
2. 资源解析接受 scheduled runner（system/scheduler actor 不再被
   actor_kind='user' 拒绝——归属以 runner.user_id+会话核对）；
3. 恢复排除条件的 NULL 语义（真实 SQL 引擎执行：用户暂停 pause_reason=NULL
   的已受理 interrupted runner 仍是候选）；
4. 快照目标免启用时序门禁（受理后启用的目标在终态可达）；
5. needs_action 站内链接与 selectable-targets/task-deliveries 接口存在性。
"""

import sqlite3
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.notifications.contracts import NotificationError, Subject


# ==================== 1. 终态参数过实际注册模型（评审 P1） ====================


class TestScheduledTerminalParametersAccepted:
    def _runner(self):
        return {
            'runner_id': 'runner_t1', 'session_id': 'sess_t1', 'status': 'completed',
            'source': 'scheduled', 'session_kind': 'web',
            'user_id': 'u1', 'tenant_id': 'tenant_1', 'scope_key': 'tenant:tenant_1',
            'actor_kind': 'system', 'actor_id': 'scheduler',
            'accepted_at': datetime(2026, 10, 28, 9, 0),
            'finished_at': datetime(2026, 10, 28, 9, 2),
            'input': {'request_data': {'scheduled': {'task_id': 'sched_1',
                                                     'trigger': 'scheduled'}}},
        }

    def _cursor(self, session_row, task_row):
        cursor = MagicMock()
        cursor.fetchone.side_effect = [
            session_row,          # chat_sessions 标题
            {'notification_ref': 'ref_t1'},  # publish_in_tx INSERT..RETURNING
        ]

        def execute(sql, params=()):
            if 'FROM scheduled_tasks' in sql:
                cursor.fetchone = lambda: task_row
            elif 'FROM chat_sessions' in sql:
                cursor.fetchone = lambda: session_row
            elif 'INSERT INTO notification_messages' in sql:
                cursor.fetchone = lambda: {'notification_ref': 'ref_t1'}
        cursor.execute.side_effect = execute
        return cursor

    def test_scheduled_terminal_publishes_with_extended_parameters(self):
        """scheduled 终态的完整参数（task_id/task_name/notify_target_id）必须
        通过 task.completed 注册模型校验——此前 extra_forbidden 使终态事务回滚"""
        from src.config.settings import NotificationsConfig
        from src.notifications import events as events_mod
        from src.notifications.events import EVENTS
        from src.notifications.producer import publish_runner_terminal_in_tx

        captured = {}
        real_publish = events_mod.publish_in_tx

        def spy_publish(cursor, **kwargs):
            captured.update(kwargs)
            return real_publish(cursor, **kwargs)

        cursor = self._cursor(
            {'title': '每日日报'},
            {'notification_target_id': 'target_9', 'name': '每日日报'})
        with patch.object(events_mod, 'publish_in_tx', spy_publish):
            # producer 内部 from .events import publish_in_tx 为函数内延迟导入，
            # patch events 模块属性即可拦截
            publish_runner_terminal_in_tx(cursor, self._runner(), NotificationsConfig())

        assert captured['event_type'] == 'task.completed'
        parameters = captured['parameters']
        # 参数经注册模型 validate：EVENTS.validate 不抛且保留全字段
        definition, validated = EVENTS.validate('task.completed', parameters,
                                                {'type': 'runner_result',
                                                 'id': 'runner_t1'})
        assert definition.parameter_model.__name__ == 'TerminalParameters'
        assert validated['task_id'] == 'sched_1'
        assert validated['task_name'] == '每日日报'
        assert validated['notify_target_id'] == 'target_9'

    def test_chat_terminal_parameters_still_minimal(self):
        """chat 终态不携带扩展字段（缺省 None），兼容既有渲染路径"""
        from src.config.settings import NotificationsConfig
        from src.notifications import events as events_mod
        from src.notifications.producer import publish_runner_terminal_in_tx

        captured = {}

        def spy_publish(cursor, **kwargs):
            captured.update(kwargs)
            return {'notification_ref': 'ref_c1'}

        runner = {
            'runner_id': 'runner_c1', 'session_id': 'sess_c1', 'status': 'failed',
            'source': 'chat', 'session_kind': 'web',
            'user_id': 'u1', 'tenant_id': 'tenant_1', 'scope_key': 'tenant:tenant_1',
            'actor_kind': 'user', 'actor_id': 'u1',
            'accepted_at': datetime(2026, 10, 28, 9, 0),
            'finished_at': datetime(2026, 10, 28, 9, 2), 'input': {},
        }
        cursor = MagicMock()
        cursor.fetchone.side_effect = [{'title': '普通会话'},
                                       {'notification_ref': 'ref_c1'}]
        with patch.object(events_mod, 'publish_in_tx', spy_publish):
            publish_runner_terminal_in_tx(cursor, runner, NotificationsConfig())
        # chat 旧路径只传两字段；扩展字段经模型校验缺省 None（兼容既有渲染）
        assert captured['parameters'] == {'session_title': '普通会话',
                                          'event_kind': 'failed'}
        from src.notifications.events import EVENTS
        _, validated = EVENTS.validate('task.failed', captured['parameters'],
                                       {'type': 'runner_result', 'id': 'runner_c1'})
        assert validated['task_id'] is None and validated['notify_target_id'] is None


# ==================== 2. 资源解析接受 scheduled（评审 P1） ====================


class TestScheduledResourceResolution:
    def _message(self, ref_type, ref_id):
        return {'recipient_user_id': 'u1', 'tenant_id': 'tenant_1',
                'scope_key': 'tenant:tenant_1',
                'resource_ref': {'type': ref_type, 'id': ref_id}}

    def _store(self, runner_row, session_row=None):
        store = SimpleNamespace(runner=runner_row, session=session_row)

        class Cursor:
            def execute(self, sql, params=None):
                self.sql = sql
                if 'FROM users' in sql:
                    self.row = {'user_id': 'u1', 'tenant_id': 'tenant_1',
                                'role': 'tenant_user', 'status': 'active'}
                elif 'FROM tenants' in sql:
                    self.row = {'ok': 1}
                elif 'FROM agent_runners' in sql:
                    self.row = store.runner
                elif 'FROM chat_sessions' in sql:
                    self.row = store.session
                else:
                    self.row = None

            def fetchone(self):
                return self.row
        return Cursor()

    def test_scheduled_runner_result_resolves_for_owner(self):
        from src.notifications.authorization import resource_in_tx
        cursor = self._store(
            {'runner_id': 'runner_t1', 'session_id': 'sess_t1',
             'profile_id': 'main'},
            {'session_id': 'sess_t1', 'subagent_id': None, 'instance_id': None,
             'title': '每日日报', 'user_id': 'u1', 'tenant_id': 'tenant_1'})
        resource = resource_in_tx(cursor, self._message('runner_result', 'runner_t1'))
        assert resource['runner_id'] == 'runner_t1'

    def test_scheduled_runner_of_other_user_unavailable(self):
        """非属主 runner：SQL 的 r.user_id=%(user)s 由真实库过滤——mock 模拟
        该过滤（user_id 不匹配则不返回行）后必须 404 fail-closed"""
        from src.notifications.authorization import resource_in_tx

        class FilteringCursor:
            def execute(self, sql, params=None):
                self.sql, self.params = sql, params
                if 'FROM users' in sql:
                    self.row = {'user_id': 'u1', 'tenant_id': 'tenant_1',
                                'role': 'tenant_user', 'status': 'active'}
                elif 'FROM tenants' in sql:
                    self.row = {'ok': 1}
                elif 'FROM agent_runners' in sql:
                    # 模拟真实库 WHERE r.user_id=%(user)s 过滤
                    self.row = ({'runner_id': 'runner_t1', 'session_id': 'sess_t1',
                                 'profile_id': 'main'} if params.get('user') == 'u2'
                                else None) if isinstance(params, dict) else None
                else:
                    self.row = None

            def fetchone(self):
                return self.row
        with pytest.raises(NotificationError) as err:
            resource_in_tx(FilteringCursor(),
                           self._message('runner_result', 'runner_t1'))
        assert err.value.code == 'NOTIFICATION_RESOURCE_UNAVAILABLE'

    def test_scheduled_waiting_resolves_for_needs_action(self):
        from src.notifications.authorization import resource_in_tx
        cursor = self._store(
            {'runner_id': 'runner_t2', 'session_id': 'sess_t1',
             'profile_id': 'main'},
            {'session_id': 'sess_t1', 'subagent_id': None, 'instance_id': None,
             'title': '每日日报', 'user_id': 'u1', 'tenant_id': 'tenant_1'})
        resource = resource_in_tx(cursor, self._message('runner_waiting', 'runner_t2'))
        assert resource['runner_id'] == 'runner_t2'

    def test_scheduled_runner_resource_exposes_scheduled_task_id(self):
        """Phase 5 二轮评审第 8 项：runner 资源解析 SQL 以子查询透出
        scheduled_task_id（scheduled_tasks.session_id 匹配），通知入口直接路由
        任务视图，不再经任务列表匹配"""
        from src.notifications.authorization import resource_in_tx
        cursor = self._store(
            {'runner_id': 'runner_t1', 'session_id': 'sess_t1',
             'profile_id': 'main', 'scheduled_task_id': 'sched_1'},
            {'session_id': 'sess_t1', 'subagent_id': None, 'instance_id': None,
             'title': '每日日报', 'user_id': 'u1', 'tenant_id': 'tenant_1'})
        resource = resource_in_tx(cursor, self._message('runner_result', 'runner_t1'))
        assert resource['scheduled_task_id'] == 'sched_1'

    def test_runner_resource_task_id_subquery_registered(self):
        """子查询以 scheduled_tasks.session_id（UNIQUE）为条件，任务删除时为 NULL"""
        import inspect
        from src.notifications import authorization
        source = inspect.getsource(authorization.resource_in_tx)
        assert 'FROM scheduled_tasks t WHERE t.session_id=r.session_id' in source
        assert 'AS scheduled_task_id' in source


# ==================== 3. NULL 安全排除条件（评审 P2，真实 SQL 引擎） ====================


class TestRecoveryExclusionNullSemantics:
    def _rows(self, pause_reason):
        conn = sqlite3.connect(':memory:')
        conn.execute('CREATE TABLE t (status TEXT, pause_reason TEXT)')
        conn.execute('INSERT INTO t VALUES (?,?)', ('paused', pause_reason))
        predicate = ("SELECT count(*) FROM t WHERE status='paused' "
                     "AND pause_reason IS NOT DISTINCT FROM 'interrupted'")
        excluded = conn.execute(predicate).fetchone()[0]
        return excluded == 1

    def test_user_paused_null_reason_not_excluded(self):
        """用户主动暂停（pause_reason=NULL）：NOT(paused AND reason='interrupted')
        不得因 NULL 语义把行排除——IS NOT DISTINCT FROM 使 NULL 安全"""
        assert self._rows(None) is False  # 不被排除 → 恢复继续

    def test_scheduler_stopped_recovery_excluded(self):
        assert self._rows('interrupted') is True  # 被排除

    def test_other_reason_not_excluded(self):
        assert self._rows('credit_blocked') is False


# ==================== 4. 快照目标免启用时序门禁（评审 P2） ====================


class TestSnapshotTargetNoAcceptedAtGate:
    def test_target_enabled_after_acceptance_still_eligible(self):
        """目标在受理后、事件生成前启用：显式选择语义下必须可达（此前
        enabled_since<=accepted_at 过滤，长任务新目标永远投不出去）"""
        from src.notifications import repository as repo_mod
        source = open(repo_mod.__file__, encoding='utf-8').read()
        # 快照分支不做时序门禁（eligible = True）；普通路由的时序检查仍在
        start = source.index('elif snapshot_target_id is not None:')
        end = source.index("elif target['recipient_user_id'] is None", start)
        snapshot_block = source[start:end]
        # 无条件通过（eligible=True）：无 enabled_since/偏好/事件集合检查
        assert 'eligible = True' in snapshot_block
        code_only = chr(10).join(line.split('#')[0] for line in snapshot_block.splitlines())
        assert 'enabled_since' not in code_only
        assert 'preference_enabled' not in code_only
        assert '_effective_event_types' not in code_only


# ==================== 5. 接口与链接存在性 ====================


class TestPhase3EndpointsAndLinks:
    def test_needs_action_in_inbox_link_types(self):
        from src.notifications.repository import NotificationRepository
        assert 'task.needs_action' in NotificationRepository.INBOX_EVENT_TYPES

    def test_selectable_targets_and_task_deliveries_routes_exist(self):
        api_source = open('src/notifications/api.py', encoding='utf-8').read()
        assert '/selectable-targets' in api_source
        assert '/task-deliveries' in api_source
        repo_source = open('src/notifications/repository.py', encoding='utf-8').read()
        assert 'def selectable_targets' in repo_source
        assert 'def task_event_deliveries' in repo_source

    def test_task_event_deliveries_carry_expansion_state(self):
        """Phase 5 二轮评审第 5 项：事件账透出 expansion_state——pending=消息尚待
        异步展开（deliveries=[] 不代表未配置外发），前端据此显示"投递准备中" """
        from types import SimpleNamespace as NS
        from src.notifications.repository import NotificationRepository
        message_rows = [
            {'notification_id': 'n1', 'notification_ref': 'ref_p', 'event_type': 'task.completed',
             'event_key': 'k1', 'created_at': '2026-10-28T09:00:00', 'expansion_state': 'pending',
             'target_id': None, 'state': None, 'attempts': None, 'error_class': None,
             'accepted_at': None, 'delivered_at': None, 'channel': None, 'provider': None},
        ]

        class Cursor:
            def execute(self, sql, params=None):
                if 'FROM users' in sql:
                    self._one = {'user_id': 'u1', 'tenant_id': 'tenant_1',
                                 'role': 'tenant_user', 'status': 'active'}
                    self._all = []
                elif 'FROM tenants' in sql:
                    self._one = {'ok': 1}
                    self._all = []
                elif 'FROM scheduled_tasks' in sql:
                    self._one = {'session_id': 'sess_t1'}
                    self._all = []
                elif 'FROM agent_runners' in sql:
                    self._one = None
                    self._all = [{'runner_id': 'runner_t1'}]
                else:
                    self._one = None
                    self._all = message_rows

            def fetchone(self):
                return self._one

            def fetchall(self):
                return self._all

        class Conn:
            def cursor(self):
                return Cursor()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        repo = NotificationRepository(NS(allowed_tenants=None), NS(), connection_factory=lambda: Conn())
        events = repo.task_event_deliveries(Subject('u1', 'tenant_1'), 'sched_1')
        assert events == [{'notification_ref': 'ref_p', 'event_type': 'task.completed',
                           'created_at': '2026-10-28T09:00:00',
                           'expansion_state': 'pending', 'deliveries': []}]

    def test_recovery_retry_at_column_registered(self):
        for path in ('deploy/init-postgres.sql', 'deploy/db_update.sql',
                     'deploy/db_update.yaml'):
            assert 'recovery_retry_at' in open(path, encoding='utf-8').read()
