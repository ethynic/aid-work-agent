"""防滥用站内信：渲染模板与跳转链接构造。"""
import pytest

from src.notifications.contracts import Subject
from src.notifications.events import AbuseParameters, render_inbox_text

pytestmark = pytest.mark.unit


def _params(**overrides):
    base = dict(
        tenant_id='tenant_e9b2fab93a2',
        tenant_name='小腾老师2',
        rule_name='时间窗积分预算',
        subject_label='微信用户 孙晨',
        user_id='user_abc',
        period='day',
        window_start='2026-10-13 14:52',
        window_end='2026-10-13 15:52',
        window_credits=9.3,
        threshold_credits=5.0,
        observe_mode=True,
    )
    base.update(overrides)
    return base


class TestRenderAbuseInbox:
    def test_tenant_admin_view_no_tenant_name(self):
        title, body = render_inbox_text('abuse.rule_triggered', AbuseParameters(**_params()).model_dump())
        assert title == '异常消耗告警'
        assert '小腾老师2' not in body
        assert '微信用户 孙晨' in body
        assert '2026-10-13 14:52 ~ 2026-10-13 15:52' in body
        assert '（观察模式，未拦截）' in body

    def test_platform_admin_view_keeps_tenant_name(self):
        params = AbuseParameters(**_params(show_tenant=True)).model_dump()
        _, body = render_inbox_text('abuse.rule_triggered', params)
        assert '租户「小腾老师2」的' in body

    def test_legacy_parameters_without_new_fields(self):
        # 存量通知 parameters 缺新增字段，渲染兜底不抛异常
        legacy = {
            'tenant_name': 'X', 'rule_name': 'r', 'subject_label': '孙晨',
            'window_credits': 9.3, 'threshold_credits': 5.0, 'observe_mode': False,
        }
        _, body = render_inbox_text('abuse.rule_triggered', legacy)
        assert '孙晨' in body


class TestAbuseInboxLink:
    def test_link_built_from_parameters(self):
        from src.notifications.repository import NotificationRepository
        subject = Subject(user_id='admin_1', tenant_id='tenant_e9b2fab93a2')
        link = NotificationRepository._inbox_link(
            'abuse.rule_triggered', _params(), subject)
        assert link == ('/t/tenant_e9b2fab93a2/abuse-center'
                        '?focus_user=user_abc&window=day')

    def test_link_hour_window(self):
        from src.notifications.repository import NotificationRepository
        subject = Subject(user_id='admin_1', tenant_id=None)
        link = NotificationRepository._inbox_link(
            'abuse.rule_triggered', _params(period='hour'), subject)
        assert link.endswith('&window=hour')

    def test_link_none_for_other_events(self):
        from src.notifications.repository import NotificationRepository
        subject = Subject(user_id='admin_1', tenant_id='t1')
        assert NotificationRepository._inbox_link(
            'billing.credit_low', {}, subject) is None

    def test_link_none_without_user(self):
        from src.notifications.repository import NotificationRepository
        subject = Subject(user_id='admin_1', tenant_id='t1')
        params = _params()
        params.pop('user_id')
        assert NotificationRepository._inbox_link(
            'abuse.rule_triggered', params, subject) is None


class TestSubjectLabel:
    @pytest.mark.parametrize("source,expected_prefix", [
        ('wecom_kf', '微信用户'),
        ('wecom', '企微用户'),
        ('wecom_personal_rpa', '企微用户'),
        ('dingtalk', '钉钉用户'),
        ('feishu', '飞书用户'),
        (None, '内部用户'),
        ('unknown_channel', '内部用户'),
    ])
    def test_source_prefix(self, source, expected_prefix):
        from src.services.anti_abuse.scanner import _subject_label
        label = _subject_label({'source': source, 'nickname': '孙晨'}, 'user_x')
        assert label == f'{expected_prefix} 孙晨'

    def test_fallback_to_user_id(self):
        from src.services.anti_abuse.scanner import _subject_label
        assert _subject_label({'source': 'dingtalk'}, 'user_x') == '钉钉用户 user_x'
