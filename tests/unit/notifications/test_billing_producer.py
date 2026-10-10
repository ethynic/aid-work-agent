"""billing_daily 生产者：开关、事件键幂等格式、阈值档升级选择、异常隔离。"""
from datetime import date, datetime
from unittest.mock import MagicMock

import pytest

from src.config.settings import NotificationsConfig
from src.notifications import billing_producer
from src.notifications.billing_producer import (
    PRODUCER,
    run_billing_notifications_once,
)

TODAY = date(2026, 10, 10)


def make_repo(**config_kw):
    repo = MagicMock()
    repo.config = NotificationsConfig(enabled=True, tenant_events_enabled=True, **config_kw)
    conn = MagicMock()
    repo.connection_factory.return_value.__enter__.return_value = conn
    return repo, conn


def tenant_row(tenant_id='t1', balance=10.0, expire_at=None, company='宏陶'):
    return {'tenant_id': tenant_id, 'company_name': company,
            'credit_balance': balance, 'expire_at': expire_at}


def set_cursor_sequence(cursor, tenants, totals, admins, now=None):
    """按 SQL 文本分发结果：租户列表 / 消耗合计 / 管理员列表 / 时钟 各取各的迭代器。"""
    now_list = [now] if now is not None else [datetime(2026, 10, 10, 3, 10)] * sum(
        len(batch) for batch in tenants)
    state = {'tenants': iter(tenants), 'totals': iter(totals),
             'admins': iter(admins), 'clock': iter(now_list)}

    def execute_dispatch(sql, params=None):
        if 'FROM tenants' in sql:
            cursor.fetchall = lambda: next(state['tenants'])
        elif 'FROM chat_records' in sql:
            cursor.fetchone = lambda: {'total': next(state['totals'])}
        elif 'FROM users' in sql:
            cursor.fetchall = lambda: next(state['admins'])
        elif 'clock_timestamp' in sql:
            cursor.fetchone = lambda: {'now': next(state['clock'])}

    cursor.execute.side_effect = execute_dispatch


class TestSwitches:
    def test_disabled_returns_zero_without_connection(self):
        repo, _ = make_repo()
        repo.config = NotificationsConfig(enabled=True)  # tenant_events_enabled 默认 False
        assert run_billing_notifications_once(repo, today=TODAY) == 0
        repo.connection_factory.assert_not_called()

    def test_notification_core_disabled_returns_zero(self):
        repo, _ = make_repo()
        repo.config = NotificationsConfig(tenant_events_enabled=True)  # enabled 默认 False
        assert run_billing_notifications_once(repo, today=TODAY) == 0
        repo.connection_factory.assert_not_called()


class TestEventPublishing:
    @pytest.fixture
    def captured(self, monkeypatch):
        calls = []

        def fake_publish(cursor, **kw):
            calls.append(kw)
            return 'ref-' + str(len(calls))

        monkeypatch.setattr(billing_producer, 'publish_in_tx', fake_publish)
        return calls

    def test_credit_low_monthly_event_key_and_params(self, captured):
        repo, conn = make_repo()
        # 日均 5 积分，余额 10 -> 2 天耗尽 < 7 天阈值
        set_cursor_sequence(conn.cursor.return_value,
                            tenants=[[tenant_row(balance=10.0)]],
                            totals=[35.0],
                            admins=[[{'user_id': 'admin1'}]])
        published = run_billing_notifications_once(repo, today=TODAY)
        assert published == 1
        event = captured[0]
        assert event['event_type'] == 'billing.credit_low'
        assert event['event_key'] == f'credit_low:t1:{TODAY.strftime("%Y-%m")}'
        assert event['policy_key'] == 'tenant_admin_event'
        assert event['category'] == 'billing'
        assert event['producer'] == PRODUCER
        assert event['resource_ref'] == {'type': 'tenant_credit', 'id': 't1'}
        params = event['parameters']
        assert params['daily_avg_credits'] == 5.0
        assert params['estimated_days'] == 2
        assert params['remaining_credits'] == 10.0
        assert params['tenant_name'] == '宏陶'

    def test_healthy_balance_no_credit_low(self, captured):
        repo, conn = make_repo()
        # 日均 5 积分，余额 100 -> 20 天 > 7 天阈值，不提醒
        set_cursor_sequence(conn.cursor.return_value,
                            tenants=[[tenant_row(balance=100.0)]],
                            totals=[35.0], admins=[[]])
        assert run_billing_notifications_once(repo, today=TODAY) == 0
        assert captured == []

    def test_expiry_tier_escalation_picks_smallest_matching(self, captured):
        repo, conn = make_repo()
        # 5 天后到期：候选 30/7 档，应取最小档 7（逐级升级，event_key 含档位）
        expire = datetime(2026, 10, 15, 12, 0)
        set_cursor_sequence(conn.cursor.return_value,
                            tenants=[[tenant_row(balance=100.0, expire_at=expire)]],
                            totals=[0.0],
                            admins=[[{'user_id': 'admin1'}]])
        run_billing_notifications_once(repo, today=TODAY)
        event = captured[0]
        assert event['event_type'] == 'billing.tenant_expiry'
        assert event['event_key'] == 'tenant_expiry:t1:2026-10-15:7'
        assert event['resource_ref'] == {'type': 'tenant_profile', 'id': 't1'}
        assert event['parameters'] == {
            'tenant_name': '宏陶', 'expire_date': '2026-10-15', 'days_left': 5}

    def test_last_day_picks_tier_one(self, captured):
        repo, conn = make_repo()
        expire = datetime(2026, 10, 11, 0, 0)
        set_cursor_sequence(conn.cursor.return_value,
                            tenants=[[tenant_row(balance=100.0, expire_at=expire)]],
                            totals=[0.0],
                            admins=[[{'user_id': 'admin1'}]])
        run_billing_notifications_once(repo, today=TODAY)
        assert captured[0]['event_key'] == 'tenant_expiry:t1:2026-10-11:1'

    def test_far_expiry_no_event(self, captured):
        repo, conn = make_repo()
        expire = datetime(2027, 6, 1, 0, 0)
        set_cursor_sequence(conn.cursor.return_value,
                            tenants=[[tenant_row(balance=100.0, expire_at=expire)]],
                            totals=[0.0], admins=[[]])
        assert run_billing_notifications_once(repo, today=TODAY) == 0
        assert captured == []

    def test_expired_tenant_no_event(self, captured):
        repo, conn = make_repo()
        expire = datetime(2026, 10, 5, 0, 0)  # 已过期 5 天
        set_cursor_sequence(conn.cursor.return_value,
                            tenants=[[tenant_row(balance=100.0, expire_at=expire)]],
                            totals=[0.0], admins=[[]])
        assert run_billing_notifications_once(repo, today=TODAY) == 0
        assert captured == []

    def test_each_admin_gets_own_message(self, captured):
        repo, conn = make_repo()
        set_cursor_sequence(conn.cursor.return_value,
                            tenants=[[tenant_row(balance=10.0, expire_at=datetime(2026, 10, 15))]],
                            totals=[35.0],
                            admins=[[{'user_id': 'admin1'}, {'user_id': 'admin2'}]])
        run_billing_notifications_once(repo, today=TODAY)
        recipients = {(kw['subject'].user_id, kw['event_type']) for kw in captured}
        assert recipients == {('admin1', 'billing.credit_low'), ('admin1', 'billing.tenant_expiry'),
                              ('admin2', 'billing.credit_low'), ('admin2', 'billing.tenant_expiry')}

    def test_no_admins_skips_without_error(self, captured):
        repo, conn = make_repo()
        set_cursor_sequence(conn.cursor.return_value,
                            tenants=[[tenant_row(balance=10.0)]], totals=[35.0], admins=[[]])
        assert run_billing_notifications_once(repo, today=TODAY) == 0
        assert captured == []

    def test_tenant_failure_isolated_and_rolled_back(self, monkeypatch):
        repo, conn = make_repo()
        set_cursor_sequence(conn.cursor.return_value,
                            tenants=[[tenant_row(tenant_id='t1', balance=10.0),
                                      tenant_row(tenant_id='t2', balance=10.0)]],
                            totals=[35.0, 35.0],
                            admins=[[{'user_id': 'admin1'}], [{'user_id': 'admin1'}]])

        def fake(cursor, **kw):
            if kw['subject'].tenant_id == 't1':
                raise RuntimeError('db burst')
            return 'ref'

        monkeypatch.setattr(billing_producer, 'publish_in_tx', fake)
        published = run_billing_notifications_once(repo, today=TODAY)
        conn.rollback.assert_called_once()
        conn.commit.assert_called_once()
        assert published == 1
