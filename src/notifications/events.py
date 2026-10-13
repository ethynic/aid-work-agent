"""Trusted event registration and cursor-only channel-neutral message writer."""
from dataclasses import dataclass
import json
import uuid
from typing import Literal

from pydantic import Field
from .contracts import SCHEMA_VERSION, StrictDTO
from .repository import protected


class TerminalParameters(StrictDTO):
    session_title: str = Field(min_length=1,max_length=80)
    event_kind: Literal['completed','failed']


class TestParameters(StrictDTO):
    session_title: Literal['浏览器通知测试']
    event_kind: Literal['test']


class CreditLowParameters(StrictDTO):
    tenant_name: str = Field(min_length=1, max_length=128)
    daily_avg_credits: float = Field(ge=0, le=1e12)
    estimated_days: int = Field(ge=0, le=3650)
    remaining_credits: float = Field(ge=0, le=1e12)


class TenantExpiryParameters(StrictDTO):
    tenant_name: str = Field(min_length=1, max_length=128)
    expire_date: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    days_left: int = Field(ge=0, le=3660)


class AbuseParameters(StrictDTO):
    tenant_id: str = Field(min_length=1, max_length=64)
    tenant_name: str = Field(min_length=1, max_length=128)
    rule_name: str = Field(min_length=1, max_length=128)
    subject_label: str = Field(min_length=1, max_length=128)
    user_id: str = Field(min_length=1, max_length=128)
    period: Literal['hour', 'day']
    window_start: str = Field(pattern=r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$')
    window_end: str = Field(pattern=r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$')
    window_credits: float = Field(ge=0, le=1e12)
    threshold_credits: float = Field(ge=0, le=1e12)
    observe_mode: bool
    show_tenant: bool = False  # 仅平台管理员投递时置 True（租户管理员即当前租户，无需显示）


@dataclass(frozen=True)
class EventDefinition:
    template_key: str
    parameter_model: type[StrictDTO]
    resource_type: str


class EventRegistry:
    def __init__(self):
        self.definitions={}

    def register(self, event_type, definition):
        if event_type in self.definitions:
            raise ValueError('NOTIFICATION_EVENT_ALREADY_REGISTERED')
        self.definitions[event_type]=definition

    def validate(self, event_type, parameters, resource_ref):
        definition=self.definitions.get(event_type)
        if definition is None or resource_ref.get('type') != definition.resource_type:
            raise ValueError('NOTIFICATION_EVENT_UNSUPPORTED')
        if not isinstance(resource_ref.get('id'), str) or not 1<=len(resource_ref['id'])<=256:
            raise ValueError('NOTIFICATION_RESOURCE_INVALID')
        return definition,definition.parameter_model.model_validate(parameters).model_dump()


EVENTS=EventRegistry()
EVENTS.register('task.completed',EventDefinition('runner_terminal',TerminalParameters,'runner_result'))
EVENTS.register('task.failed',EventDefinition('runner_terminal',TerminalParameters,'runner_result'))
EVENTS.register('notification.test',EventDefinition('notification_test',TestParameters,'notification_test'))
EVENTS.register('billing.credit_low',EventDefinition('billing_credit_low',CreditLowParameters,'tenant_credit'))
EVENTS.register('billing.tenant_expiry',EventDefinition('billing_tenant_expiry',TenantExpiryParameters,'tenant_profile'))
EVENTS.register('abuse.rule_triggered',EventDefinition('abuse_rule_triggered',AbuseParameters,'abuse_event'))


def render_inbox_text(event_type, parameters):
    """站内信标题与正文按事件模板渲染；仅做纯文本插值，参数已过严格模型。"""
    if event_type == 'billing.credit_low':
        return '积分即将耗尽提醒', (
            f'「{parameters["tenant_name"]}」近 7 天日均消耗 {parameters["daily_avg_credits"]:.1f} 积分，'
            f'当前剩余 {parameters["remaining_credits"]:.1f} 积分，'
            f'预计 {parameters["estimated_days"]} 天内耗尽，请及时充值或续费。')
    if event_type == 'billing.tenant_expiry':
        return '租户有效期即将到期', (
            f'「{parameters["tenant_name"]}」将于 {parameters["expire_date"]} 到期'
            f'（剩余 {parameters["days_left"]} 天），请及时续费。')
    if event_type == 'abuse.rule_triggered':
        mode = '（观察模式，未拦截）' if parameters.get('observe_mode') else ''
        # .get 兜底：存量通知的 parameters 缺少新增字段时不让渲染整体失败
        tenant_prefix = f'租户「{parameters["tenant_name"]}」的' if parameters.get('show_tenant') else ''
        window_start, window_end = parameters.get('window_start') or '', parameters.get('window_end') or ''
        window_txt = f'（{window_start} ~ {window_end}）' if window_start and window_end else ''
        return '异常消耗告警', (
            f'{tenant_prefix}反滥用规则「{parameters["rule_name"]}」触发{mode}：'
            f'{parameters["subject_label"]} 在统计窗口{window_txt}内消耗 {parameters["window_credits"]:.1f} 积分，'
            f'阈值 {parameters["threshold_credits"]:.1f} 积分。')
    raise ValueError('NOTIFICATION_EVENT_UNSUPPORTED')


def render_message_text(event_type, parameters):
    """外发渠道统一文本（标题, 正文）；测试事件也走此渲染。"""
    if event_type == 'notification.test':
        return '通知渠道测试', '这是一条测试通知，收到即表示外发渠道配置有效。'
    return render_inbox_text(event_type, parameters)


def publish_in_tx(cursor, *, subject, producer, event_type, event_key, resource_ref, parameters,
                  occurred_at, accepted_at, expires_at, category='task',priority=0,
                  policy_key='accepted_before_enabled',registry=EVENTS):
    """Only trusted producers invoke this; the caller owns the authoritative transaction."""
    definition,parameters=registry.validate(event_type,parameters,resource_ref)
    if (not producer or len(producer)>128 or not event_key or len(event_key)>512
            or not subject.user_id or expires_at<=occurred_at
            or (policy_key=='accepted_before_enabled' and accepted_at is None)):
        raise ValueError('NOTIFICATION_INTENT_INVALID')
    identifier,ref='notification_'+uuid.uuid4().hex,uuid.uuid4().hex
    cursor.execute('''INSERT INTO notification_messages
        (notification_id,notification_ref,schema_version,producer,event_type,event_key,
         scope_key,tenant_id,recipient_user_id,resource_ref,template_key,template_version,
         parameters_encrypted,occurred_at,accepted_at,expires_at,category,priority,policy_key)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,1,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT(scope_key,recipient_user_id,producer,event_key) DO NOTHING
        RETURNING notification_ref''',
        (identifier,ref,SCHEMA_VERSION,producer,event_type,event_key,subject.scope_key,
         subject.tenant_id,subject.user_id,json.dumps(resource_ref),definition.template_key,
         protected(parameters),occurred_at,accepted_at,expires_at,category,priority,policy_key))
    created=cursor.fetchone()
    if created:
        return created['notification_ref']
    cursor.execute('''SELECT notification_ref FROM notification_messages
        WHERE scope_key=%s AND recipient_user_id=%s AND producer=%s AND event_key=%s''',
        (subject.scope_key,subject.user_id,producer,event_key))
    return cursor.fetchone()['notification_ref']
