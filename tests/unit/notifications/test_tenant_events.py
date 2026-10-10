"""租户通知公共件：站内信渲染模板、租户级 installation_ref 确定性、tenant_owned 判定。"""
import hashlib

import pytest

from src.notifications.contracts import Subject
from src.notifications.events import render_inbox_text, render_message_text
from src.notifications.repository import tenant_installation_ref, tenant_owned

CREDIT_LOW = {'tenant_name': '宏陶', 'daily_avg_credits': 5.0,
              'estimated_days': 2, 'remaining_credits': 10.0}
TENANT_EXPIRY = {'tenant_name': '宏陶', 'expire_date': '2026-11-09', 'days_left': 30}
ABUSE = {'rule_name': '单用户高频调用', 'subject_label': 'user_001',
         'window_credits': 900.0, 'threshold_credits': 500.0, 'observe_mode': False}


class TestRenderInboxText:
    def test_credit_low(self):
        title, body = render_inbox_text('billing.credit_low', CREDIT_LOW)
        assert title == '积分即将耗尽提醒'
        assert '「宏陶」' in body and '日均消耗 5.0' in body and '2 天内耗尽' in body

    def test_tenant_expiry(self):
        title, body = render_inbox_text('billing.tenant_expiry', TENANT_EXPIRY)
        assert title == '租户有效期即将到期'
        assert '2026-11-09' in body and '剩余 30 天' in body

    def test_abuse_triggered(self):
        title, body = render_inbox_text('abuse.rule_triggered', ABUSE)
        assert title == '异常消耗告警'
        assert '「单用户高频调用」' in body
        assert '（观察模式，未拦截）' not in body

    def test_abuse_observe_mode_noted(self):
        params = dict(ABUSE, observe_mode=True)
        _, body = render_inbox_text('abuse.rule_triggered', params)
        assert '（观察模式，未拦截）' in body

    def test_unsupported_event_rejected(self):
        with pytest.raises(ValueError, match='NOTIFICATION_EVENT_UNSUPPORTED'):
            render_inbox_text('unknown.event', {})


class TestRenderMessageText:
    def test_test_event_renders_fixed_copy(self):
        title, body = render_message_text('notification.test', {
            'session_title': '浏览器通知测试', 'event_kind': 'test'})
        assert title == '通知渠道测试'
        assert '外发渠道配置有效' in body

    def test_billing_events_fall_through_to_inbox_render(self):
        title, _ = render_message_text('billing.credit_low', CREDIT_LOW)
        assert title == '积分即将耗尽提醒'


class TestTenantInstallationRef:
    def test_deterministic_and_namespaced(self):
        ref = tenant_installation_ref('t1')
        again = tenant_installation_ref('t1')
        other = tenant_installation_ref('t2')
        assert ref == again
        assert ref != other
        assert ref.startswith('tenant_')
        digest = ref[len('tenant_'):]
        assert len(digest) == 40
        int(digest, 16)  # 十六进制

    def test_matches_construction_formula(self):
        expected = 'tenant_' + hashlib.sha256(b'tenant_target:t1').hexdigest()[:40]
        assert tenant_installation_ref('t1') == expected


class TestTenantOwned:
    def test_tenant_level_target_owned_by_subject(self):
        subject = Subject('admin1', 't1')
        row = {'scope_key': 'tenant:t1', 'tenant_id': 't1', 'recipient_user_id': None}
        assert tenant_owned(row, subject)

    def test_user_level_target_not_tenant_owned(self):
        subject = Subject('admin1', 't1')
        row = {'scope_key': 'tenant:t1', 'tenant_id': 't1', 'recipient_user_id': 'admin1'}
        assert not tenant_owned(row, subject)

    def test_cross_tenant_rejected(self):
        subject = Subject('admin1', 't1')
        row = {'scope_key': 'tenant:t2', 'tenant_id': 't2', 'recipient_user_id': None}
        assert not tenant_owned(row, subject)

    def test_none_row_rejected(self):
        assert not tenant_owned(None, Subject('admin1', 't1'))
