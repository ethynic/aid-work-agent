"""通知规则引擎（设计 v0.3）：事件级消息展开、两层规则匹配、接收人展开、
外发目标去重、billing 规则门控、预置规则惰性补插、规则 CRUD 边界。"""
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from src.config.settings import NotificationsConfig
from src.notifications.authorization import NotificationError
from src.notifications.contracts import AdapterCapabilities, Subject
from src.notifications.events import LeadCapturedParameters, publish_event_in_tx, render_inbox_text
from src.notifications.repository import (
    PRESET_RULES,
    NotificationRepository,
    protected,
)

T1 = 't1'
NOW = datetime(2026, 10, 29, 12, 0)


def make_repo(registry=None):
    repo = NotificationRepository.__new__(NotificationRepository)
    repo.config = NotificationsConfig(batch_size=10)
    repo.registry = registry if registry is not None else MagicMock()
    return repo


class StubAdapter:
    channel = 'im_webhook'
    capabilities = AdapterCapabilities()

    def available(self):
        return True

    def render(self, message, target):
        return {'text': 'body'}


def lead_params(**over):
    params = {'lead_id': 'lead1', 'contact_method': 'phone',
              'contact_summary': '手机号 13800000000、邮箱 a@b.com',
              'contact_name': '张三', 'demand_summary': '咨询报价',
              'kf_config_id': 'chan_a', 'kf_account_name': '售前客服',
              'assignee_user_id': 'emp1', 'assignee_name': '李四',
              'previous_captured_at': ''}
    params.update(over)
    return params


def rule_event_message(**over):
    message = {'notification_id': 'notification_ev1', 'notification_ref': 'ev1',
               'recipient_user_id': None, 'tenant_id': T1, 'scope_key': 'tenant:' + T1,
               'schema_version': 1, 'policy_key': 'rule_event', 'category': 'lead',
               'event_type': 'lead.captured', 'event_key': 'lead:t1:lead1',
               'parameters_encrypted': protected(lead_params()),
               'resource_ref': {'type': 'lead_capture_lead', 'id': 'lead1'},
               'template_key': 'lead_captured', 'producer': 'lead_capture',
               'accepted_at': NOW, 'created_at': NOW, 'expires_at': datetime(2026, 10, 30),
               'occurred_at': NOW, 'priority': 1, 'database_now': NOW}
    message.update(over)
    return message


def rule_row(rule_id='rule_1', name='留资通知', conditions=None, mode='kf_assignee'):
    return {'rule_id': rule_id, 'name': name,
            'conditions': conditions if conditions is not None else {'scope': 'channel'},
            'recipient_mode': mode}


def target_row(target_id='target_1', rule_id='rule_1'):
    return {'target_id': target_id, 'scope_key': 'tenant:' + T1, 'tenant_id': T1,
            'recipient_user_id': None, 'state': 'active', 'binding_version': 1,
            'channel': 'im_webhook', 'provider': 'feishu_bot', 'rule_id': rule_id,
            'installation_ref': 'x', 'enabled_since': datetime(2026, 1, 1),
            'preference_enabled': None, 'preference_since': None, 'event_types': None}


def run_rule_expand(messages, rules=None, assignee_row=None, admins=(), target_pages=()):
    """驱动 expand() 处理事件级消息。target_pages: 每次目标查询依次返回的列表。"""
    registry = MagicMock()
    registry.get.return_value = StubAdapter()
    repo = make_repo(registry)
    cursor = MagicMock()
    state = {'messages': iter([messages]), 'pages': iter(target_pages),
             'recipient_messages': 0, 'deliveries': 0, 'target_queries': 0,
             'expansion_states': []}

    def execute_dispatch(sql, params=None):
        if "expansion_state='pending'" in sql:
            cursor.fetchall = lambda: next(state['messages'])
        elif 'UPDATE notification_messages SET expansion_state=%s' in sql:
            state['expansion_states'].append(params[0])
        elif 'SELECT 1 FROM notification_rules' in sql:
            cursor.fetchone = lambda: {'ok': 1}
        elif 'FROM notification_rules WHERE tenant_id' in sql:
            cursor.fetchall = lambda: rules or []
        elif "WHERE user_id=%s AND tenant_id=%s AND status='active'" in sql:
            cursor.fetchone = lambda: assignee_row
        elif "role='tenant_admin'" in sql:
            cursor.fetchall = lambda: [{'user_id': a} for a in admins]
        elif 'FROM notification_targets' in sql:
            state['target_queries'] += 1
            try:
                page = next(state['pages'])
            except StopIteration:
                page = []
            cursor.fetchall = lambda: page
        elif 'INSERT INTO notification_messages' in sql:
            state['recipient_messages'] += 1
            cursor.fetchone = lambda: {'notification_ref': 'r1'}
        elif 'INSERT INTO notification_deliveries' in sql:
            state['deliveries'] += 1

    cursor.execute.side_effect = execute_dispatch
    conn = MagicMock()
    conn.cursor.return_value = cursor

    @contextmanager
    def factory():
        yield conn

    repo.connection_factory = factory
    repo.expand()
    return state


class TestExpandRuleEvent:
    def test_channel_rule_matches_and_expands(self):
        state = run_rule_expand(
            [rule_event_message()], rules=[rule_row()],
            assignee_row={'user_id': 'emp1'}, target_pages=[[target_row()]])
        assert state['recipient_messages'] == 1
        assert state['deliveries'] == 1

    def test_kf_config_rule_skips_unmatched_account(self):
        rule = rule_row(rule_id='rule_kf', conditions={'scope': 'kf_config',
                                                       'kf_config_ids': ['chan_x']})
        state = run_rule_expand([rule_event_message()], rules=[rule],
                                assignee_row={'user_id': 'emp1'})
        assert state['recipient_messages'] == 0
        assert state['deliveries'] == 0

    def test_kf_config_rule_matches_configured_account(self):
        rule = rule_row(rule_id='rule_kf', conditions={'scope': 'kf_config',
                                                       'kf_config_ids': ['chan_a']})
        state = run_rule_expand(
            [rule_event_message()], rules=[rule],
            assignee_row={'user_id': 'emp1'}, target_pages=[[target_row()]])
        assert state['recipient_messages'] == 1
        assert state['deliveries'] == 1

    def test_two_layer_rules_each_deliver(self):
        kf_rule = rule_row(rule_id='rule_kf', conditions={'scope': 'kf_config',
                                                          'kf_config_ids': ['chan_a']})
        state = run_rule_expand(
            [rule_event_message()], rules=[rule_row(), kf_rule],
            assignee_row={'user_id': 'emp1'},
            target_pages=[[target_row('target_1', 'rule_1')], []])
        # 两层规则都命中：同一接收人各留一条站内信（"都通知"口径）
        assert state['recipient_messages'] == 2
        assert state['deliveries'] == 1

    def test_no_matching_rules_zero_delivery_not_pending(self):
        state = run_rule_expand([rule_event_message()], rules=[])
        assert state['recipient_messages'] == 0
        assert state['deliveries'] == 0
        assert state['target_queries'] == 0
        # 无命中也不滞留：事件消息直接置 expanded（而非保持 pending）
        assert state['expansion_states'] == ['expanded']

    def test_assignee_cross_tenant_rejected(self):
        state = run_rule_expand([rule_event_message()], rules=[rule_row()],
                                assignee_row=None)
        assert state['recipient_messages'] == 0

    def test_each_rule_delivers_own_targets(self):
        rules = [rule_row(rule_id='rule_1'), rule_row(rule_id='rule_2')]
        state = run_rule_expand(
            [rule_event_message()], rules=rules,
            assignee_row={'user_id': 'emp1'},
            target_pages=[[target_row('target_rule1', 'rule_1')],
                          [target_row('target_rule2', 'rule_2')]])
        # 每条规则只投递自己的规则级目标（默认渠道概念已移除）
        assert state['deliveries'] == 2

    def test_expired_message_not_expanded(self):
        message = rule_event_message(expires_at=NOW)
        state = run_rule_expand([message], rules=[rule_row()],
                                assignee_row={'user_id': 'emp1'})
        assert state['recipient_messages'] == 0
        assert state['deliveries'] == 0


class TestBillingRuleGate:
    def _run_tenant_expand(self, enabled_rule_ids):
        """复用旧邮件通知路径（tenant_admin_event）验证规则门控：
        外发目标限定该事件启用规则的规则级 target（默认渠道已移除）。"""
        registry = MagicMock()
        registry.get.return_value = StubAdapter()
        repo = make_repo(registry)
        cursor = MagicMock()
        state = {'targets_params': None, 'delivered': 0}

        def execute_dispatch(sql, params=None):
            if "expansion_state='pending'" in sql:
                cursor.fetchall = lambda: [dict(
                    notification_id='notification_a1', notification_ref='a1',
                    recipient_user_id='admin1', tenant_id=T1, scope_key='tenant:' + T1,
                    schema_version=1, policy_key='tenant_admin_event', category='billing',
                    event_type='billing.credit_low', event_key='credit_low:t1:2026-10',
                    parameters_encrypted=protected({'tenant_name': '宏陶',
                                                    'daily_avg_credits': 5.0,
                                                    'estimated_days': 2,
                                                    'remaining_credits': 10.0}),
                    resource_ref={'type': 'tenant_credit', 'id': T1},
                    accepted_at=datetime(2026, 10, 10), created_at=datetime(2026, 10, 10),
                    expires_at=datetime(2026, 10, 11), priority=0,
                    database_now=datetime(2026, 10, 10, 12, 0))]
            elif 'SELECT 1 FROM notification_rules' in sql:
                cursor.fetchone = lambda: {'ok': 1}
            elif 'SELECT rule_id FROM notification_rules' in sql:
                cursor.fetchall = lambda: [{'rule_id': r} for r in enabled_rule_ids]
            elif 'FROM users' in sql:
                cursor.fetchone = lambda: {'user_id': 'admin1', 'tenant_id': T1,
                                           'role': 'tenant_admin', 'status': 'active'}
            elif "FROM tenants WHERE tenant_id=%s AND status='active'" in sql:
                cursor.fetchone = lambda: {'ok': 1}
            elif 'FROM notification_targets' in sql:
                state['targets_params'] = params
                cursor.fetchall = lambda: [target_row('target_tenant', 'rule_preset_1')]

        cursor.execute.side_effect = execute_dispatch
        conn = MagicMock()
        conn.cursor.return_value = cursor

        @contextmanager
        def factory():
            yield conn

        repo.connection_factory = factory
        repo.expand()
        return state

    def test_no_enabled_rule_blocks_tenant_target(self):
        state = self._run_tenant_expand([])
        # 无启用规则时外发目标过滤参数为空列表（参数序：category, scope_key,
        # user_id, tenant_rule_ids, params_tenant, tenant_rule_ids）
        assert state['targets_params'][3] == []

    def test_enabled_rule_routes_to_rule_targets(self):
        state = self._run_tenant_expand(['rule_preset_1'])
        assert state['targets_params'][3] == ['rule_preset_1']


class TestPresetRulesEnsure:
    def _run_ensure(self, existing):
        cursor = MagicMock()
        inserts = []

        def execute_dispatch(sql, params=None):
            if 'SELECT 1 FROM notification_rules' in sql:
                cursor.fetchone = lambda: existing
            elif 'INSERT INTO notification_rules' in sql:
                inserts.append(params)

        cursor.execute.side_effect = execute_dispatch
        NotificationRepository._ensure_preset_rules_in_tx(cursor, T1, 'lead.captured')
        return inserts

    def test_missing_tenant_gets_preset_rule(self):
        inserts = self._run_ensure(None)
        assert len(inserts) == 1
        rule_id, tenant_id, event_type, name = inserts[0][:4]
        assert rule_id.startswith('rule_preset_')
        assert tenant_id == T1 and event_type == 'lead.captured' and name == '留资通知'

    def test_existing_rules_not_overwritten(self):
        # 已存在任意规则行（含停用）不补插——停用事实不被覆盖
        assert self._run_ensure({'ok': 1}) == []

    def test_preset_definitions_shape(self):
        by_event = {item[0]: item for item in PRESET_RULES}
        assert by_event['lead.captured'][3] == 'kf_assignee'
        assert by_event['billing.credit_low'][3] == 'tenant_admins'
        assert by_event['lead.captured'][2] == {'scope': 'channel'}


class TestLeadCapturedContract:
    def test_strict_dto_rejects_extra_fields(self):
        with pytest.raises(Exception):
            LeadCapturedParameters.model_validate(dict(lead_params(), extra_field='x'))

    def test_contact_method_literal(self):
        with pytest.raises(Exception):
            LeadCapturedParameters.model_validate(dict(lead_params(), contact_method='qr'))

    def test_optional_fields_default_empty(self):
        params = LeadCapturedParameters.model_validate({
            'lead_id': 'lead1', 'contact_method': 'phone',
            'contact_summary': '手机号 13800000000', 'kf_config_id': 'chan_a',
            'kf_account_name': '售前客服'})
        assert params.contact_name == '' and params.assignee_user_id == ''

    def test_publish_event_in_tx_rejects_unknown_event(self):
        cursor = MagicMock()
        with pytest.raises(ValueError, match='NOTIFICATION_EVENT_UNSUPPORTED'):
            publish_event_in_tx(cursor, producer='lead_capture', event_type='unknown.event',
                                event_key='k', tenant_id=T1,
                                resource_ref={'type': 'lead_capture_lead', 'id': 'l1'},
                                parameters={}, occurred_at=NOW, accepted_at=NOW,
                                expires_at=datetime(2026, 10, 30))

    def test_publish_event_in_tx_rejects_invalid_ttl(self):
        cursor = MagicMock()
        with pytest.raises(ValueError, match='NOTIFICATION_INTENT_INVALID'):
            publish_event_in_tx(cursor, producer='lead_capture', event_type='lead.captured',
                                event_key='k', tenant_id=T1,
                                resource_ref={'type': 'lead_capture_lead', 'id': 'l1'},
                                parameters=lead_params(), occurred_at=datetime(2026, 10, 30),
                                accepted_at=NOW, expires_at=NOW)

    def test_publish_event_in_tx_null_recipient_sql(self):
        """事件级消息落库：recipient NULL + 部分唯一索引 ON CONFLICT 子句。"""
        cursor = MagicMock()
        cursor.fetchone.return_value = {'notification_ref': 'ref1'}
        sql_holder = {}

        def execute_dispatch(sql, params=None):
            sql_holder['sql'] = sql
            sql_holder['params'] = params

        cursor.execute.side_effect = execute_dispatch
        ref = publish_event_in_tx(
            cursor, producer='lead_capture', event_type='lead.captured',
            event_key='lead:t1:lead1', tenant_id=T1,
            resource_ref={'type': 'lead_capture_lead', 'id': 'lead1'},
            parameters=lead_params(), occurred_at=NOW, accepted_at=NOW,
            expires_at=datetime(2026, 10, 30), category='lead', priority=1)
        assert ref == 'ref1'
        assert 'WHERE recipient_user_id IS NULL DO NOTHING' in sql_holder['sql']
        assert 'NULL' in sql_holder['sql']
        params = sql_holder['params']
        assert params[6] == 'tenant:t1'  # scope_key
        assert params[7] == T1  # tenant_id

    def test_render_template_with_previous_capture(self):
        title, body = render_inbox_text('lead.captured', lead_params(
            previous_captured_at='2026-10-01 10:00:00'))
        assert title == '客户留资提醒'
        assert '通过手机号 13800000000、邮箱 a@b.com留资' in body
        assert '2026-10-01 10:00:00 留资' in body

    def test_render_template_defaults_for_blank_name_demand(self):
        _, body = render_inbox_text('lead.captured', lead_params(contact_name='', demand_summary=''))
        assert '未署名' in body and '未提供' in body


class TestRuleConditionsValidation:
    def test_non_lead_event_rejects_conditions(self):
        with pytest.raises(NotificationError, match='NOTIFICATION_RULE_CONDITIONS_INVALID'):
            NotificationRepository._validate_rule_conditions('billing.credit_low', {'scope': 'channel'})

    def test_lead_channel_scope_ok(self):
        assert NotificationRepository._validate_rule_conditions(
            'lead.captured', {'scope': 'channel'}) == {'scope': 'channel'}

    def test_lead_kf_config_requires_ids(self):
        with pytest.raises(NotificationError, match='NOTIFICATION_RULE_CONDITIONS_INVALID'):
            NotificationRepository._validate_rule_conditions(
                'lead.captured', {'scope': 'kf_config', 'kf_config_ids': []})

    def test_lead_unknown_keys_rejected(self):
        with pytest.raises(NotificationError, match='NOTIFICATION_RULE_CONDITIONS_INVALID'):
            NotificationRepository._validate_rule_conditions(
                'lead.captured', {'scope': 'channel', 'other': 1})

    def test_lead_default_scope_is_channel(self):
        assert NotificationRepository._validate_rule_conditions(
            'lead.captured', {}) == {'scope': 'channel'}


class TestDeleteRule:
    def _run_delete(self, rule_row_value):
        repo = make_repo()
        cursor = MagicMock()
        state = {'revoked_targets': 0, 'deleted': 0}

        def execute_dispatch(sql, params=None):
            if 'FROM users' in sql:
                cursor.fetchone = lambda: {'user_id': 'admin1', 'tenant_id': T1,
                                           'role': 'tenant_admin', 'status': 'active'}
            elif "FROM tenants WHERE tenant_id=%s AND status='active'" in sql:
                cursor.fetchone = lambda: {'ok': 1}
            elif 'DELETE FROM notification_rules' in sql:
                state['deleted'] += 1
            elif 'FROM notification_rules WHERE rule_id' in sql:
                cursor.fetchone = lambda: rule_row_value
            elif "FROM notification_targets" in sql:
                cursor.fetchall = lambda: [target_row('target_1', rule_row_value['rule_id'])]
            elif 'UPDATE notification_targets' in sql:
                state['revoked_targets'] += 1

        cursor.execute.side_effect = execute_dispatch
        conn = MagicMock()
        conn.cursor.return_value = cursor

        @contextmanager
        def factory():
            yield conn

        repo.connection_factory = factory
        NotificationRepository.delete_rule(repo, Subject('admin1', T1), 'rule_1')
        return state

    def test_system_rule_delete_rejected(self):
        rule = dict(rule_row(), is_system=True, tenant_id=T1)
        with pytest.raises(NotificationError, match='NOTIFICATION_RULE_SYSTEM'):
            repo = make_repo()
            cursor = MagicMock()

            def execute_dispatch(sql, params=None):
                if 'FROM users' in sql:
                    cursor.fetchone = lambda: {'user_id': 'admin1', 'tenant_id': T1,
                                               'role': 'tenant_admin', 'status': 'active'}
                elif "FROM tenants WHERE tenant_id=%s AND status='active'" in sql:
                    cursor.fetchone = lambda: {'ok': 1}
                elif 'FROM notification_rules WHERE rule_id' in sql:
                    cursor.fetchone = lambda: rule

            cursor.execute.side_effect = execute_dispatch
            conn = MagicMock()
            conn.cursor.return_value = cursor

            @contextmanager
            def factory():
                yield conn

            repo.connection_factory = factory
            repo.delete_rule(Subject('admin1', T1), 'rule_1')

    def test_custom_rule_delete_revokes_targets(self):
        rule = dict(rule_row(), is_system=False, tenant_id=T1)
        state = self._run_delete(rule)
        assert state['deleted'] == 1
        assert state['revoked_targets'] == 1
