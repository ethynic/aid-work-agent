"""管理员门控角色口径与租户级 target 展开（多管理员 event_key 去重）验证。"""
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from src.config.settings import NotificationsConfig
from src.notifications.authorization import NotificationError
from src.notifications.contracts import AdapterCapabilities, Subject
from src.notifications.repository import (
    NotificationRepository,
    protected,
    tenant_installation_ref,
)


def make_repo():
    repo = NotificationRepository.__new__(NotificationRepository)
    repo.config = NotificationsConfig()
    return repo


def make_cursor(user_row):
    cursor = MagicMock()

    def execute_dispatch(sql, params=None):
        if 'FROM users' in sql:
            cursor.fetchone = lambda: user_row
        elif "FROM tenants WHERE tenant_id=%s AND status='active'" in sql:
            cursor.fetchone = lambda: {'ok': 1}

    cursor.execute.side_effect = execute_dispatch
    return cursor


class TestGuardAdminRoles:
    @pytest.mark.parametrize('role', ['tenant_admin', 'platform_admin'])
    def test_admin_roles_accepted(self, role):
        cursor = make_cursor({'user_id': 'u1', 'tenant_id': 't1', 'role': role, 'status': 'active'})
        NotificationRepository.guard_admin(make_repo(), cursor, Subject('u1', 't1'))

    @pytest.mark.parametrize('role', ['tenant_user', 'admin', None])
    def test_non_admin_roles_rejected(self, role):
        cursor = make_cursor({'user_id': 'u1', 'tenant_id': 't1', 'role': role, 'status': 'active'})
        with pytest.raises(NotificationError, match='NOTIFICATION_FORBIDDEN'):
            NotificationRepository.guard_admin(make_repo(), cursor, Subject('u1', 't1'))


class StubAdapter:
    channel = 'im_webhook'
    capabilities = AdapterCapabilities()

    def available(self):
        return True

    def render(self, message, target):
        return {'text': 'body'}


def tenant_target():
    return {'target_id': 'target_1', 'scope_key': 'tenant:t1', 'tenant_id': 't1',
            'recipient_user_id': None, 'state': 'active', 'binding_version': 1,
            'channel': 'im_webhook', 'provider': 'feishu_bot',
            'installation_ref': tenant_installation_ref('t1'),
            'enabled_since': datetime(2026, 1, 1), 'preference_enabled': None,
            'preference_since': None, 'event_types': None}


def billing_message(admin_id, event_key):
    return {'notification_id': 'notification_' + admin_id, 'notification_ref': admin_id,
            'recipient_user_id': admin_id, 'tenant_id': 't1', 'scope_key': 'tenant:t1',
            'schema_version': 1, 'policy_key': 'tenant_admin_event', 'category': 'billing',
            'event_type': 'billing.credit_low', 'event_key': event_key,
            'parameters_encrypted': protected({'tenant_name': '宏陶', 'daily_avg_credits': 5.0,
                                               'estimated_days': 2, 'remaining_credits': 10.0}),
            'resource_ref': {'type': 'tenant_credit', 'id': 't1'},
            'accepted_at': datetime(2026, 10, 10), 'created_at': datetime(2026, 10, 10),
            'expires_at': datetime(2026, 10, 11), 'priority': 0,
            'database_now': datetime(2026, 10, 10, 12, 0)}


class TestExpandTenantDedupe:
    def _run_expand(self, messages, dedupe_hits):
        """dedupe_hits: 去重查询依次返回的假值列表（None=无已有投递）。"""
        repo = NotificationRepository.__new__(NotificationRepository)
        repo.config = NotificationsConfig(batch_size=10)
        registry = MagicMock()
        registry.get.return_value = StubAdapter()
        repo.registry = registry

        cursor = MagicMock()
        state = {'messages': iter([messages]), 'dedupe': iter(dedupe_hits), 'inserted': 0}

        def execute_dispatch(sql, params=None):
            if "expansion_state='pending'" in sql:
                cursor.fetchall = lambda: next(state['messages'])
            elif 'FROM users' in sql:
                cursor.fetchone = lambda: {'user_id': 'x', 'tenant_id': 't1',
                                           'role': 'tenant_admin', 'status': 'active'}
            elif "FROM tenants WHERE tenant_id=%s AND status='active'" in sql:
                cursor.fetchone = lambda: {'ok': 1}
            elif 'FROM notification_targets' in sql:
                cursor.fetchall = lambda: [tenant_target()]
            elif 'JOIN notification_messages m' in sql:
                hit = next(state['dedupe'])
                cursor.fetchone = lambda: hit
            elif 'INSERT INTO notification_deliveries' in sql:
                state['inserted'] += 1

        cursor.execute.side_effect = execute_dispatch
        conn = MagicMock()
        conn.cursor.return_value = cursor

        @contextmanager
        def factory():
            yield conn

        repo.connection_factory = factory
        count = repo.expand()
        return count, state

    def test_second_admin_message_skips_duplicate_tenant_delivery(self):
        key = 'credit_low:t1:2026-10'
        messages = [billing_message('admin1', key), billing_message('admin2', key)]
        count, state = self._run_expand(messages, dedupe_hits=[None, {'x': 1}])
        assert count == 2
        # 同一租户级 target 只插入一条投递，第二个管理员的消息命中去重跳过
        assert state['inserted'] == 1

    def test_single_admin_unaffected_by_dedupe(self):
        key = 'credit_low:t1:2026-10'
        count, state = self._run_expand([billing_message('admin1', key)], dedupe_hits=[None])
        assert count == 1
        assert state['inserted'] == 1


def inbox_repo(user_row):
    """构造 guard 走真 DB mock、SQL 可捕获的 repository"""
    repo = NotificationRepository.__new__(NotificationRepository)
    repo.config = NotificationsConfig()

    cursor = MagicMock()
    captured = {}
    state = {'unread': [{'unread': 0}], 'items': []}

    def execute_dispatch(sql, params=None):
        if 'FROM users' in sql:
            cursor.fetchone = lambda: user_row
        elif "FROM tenants WHERE tenant_id=%s AND status='active'" in sql:
            cursor.fetchone = lambda: {'ok': 1}
        elif 'count(*) AS unread' in sql:
            captured['count_sql'] = sql
            cursor.fetchone = lambda: next(iter(state['unread']))
        elif 'ORDER BY created_at DESC' in sql:
            captured['list_sql'] = sql
            cursor.fetchall = lambda: state['items']

    cursor.execute.side_effect = execute_dispatch
    conn = MagicMock()
    conn.cursor.return_value = cursor

    @contextmanager
    def factory():
        yield conn

    repo.connection_factory = factory
    return repo, captured


class TestInboxPlatformScope:
    def test_platform_admin_inbox_includes_global_scope(self):
        repo, captured = inbox_repo({'user_id': 'p1', 'tenant_id': None,
                                     'role': 'platform_admin', 'status': 'active'})
        result = repo.inbox(Subject('p1', None))
        assert "scope_key='global'" in captured['count_sql']
        assert "scope_key='global'" in captured['list_sql']
        assert result['unread_count'] == 0

    def test_platform_admin_in_tenant_context_also_sees_global(self):
        repo, captured = inbox_repo({'user_id': 'p1', 'tenant_id': None,
                                     'role': 'platform_admin', 'status': 'active'})
        repo.inbox(Subject('p1', 't1'))
        assert "(scope_key=%s OR scope_key='global')" in captured['list_sql']

    def test_tenant_user_inbox_strict_scope(self):
        repo, captured = inbox_repo({'user_id': 'u1', 'tenant_id': 't1',
                                     'role': 'tenant_admin', 'status': 'active'})
        repo.inbox(Subject('u1', 't1'))
        assert "OR scope_key='global'" not in captured['count_sql']
        assert "OR scope_key='global'" not in captured['list_sql']
