"""Trusted event registration and cursor-only channel-neutral message writer."""
from dataclasses import dataclass
import json
import uuid
from typing import Literal

from pydantic import Field
from .contracts import RULE_EVENT_POLICY_KEY, SCHEMA_VERSION, StrictDTO
from .repository import protected


class TerminalParameters(StrictDTO):
    session_title: str = Field(min_length=1,max_length=80)
    event_kind: Literal['completed','failed']
    # scheduled 终态扩展（Phase 3 复核，设计 §8.2）：任务标识/名称与目标
    # 快照；chat 来源缺省（可选字段，白名单不放宽 extra 校验）
    task_id: str | None = Field(default=None, min_length=1, max_length=64)
    task_name: str | None = Field(default=None, min_length=1, max_length=80)
    notify_target_id: str | None = Field(default=None, min_length=1, max_length=128)


class WaitingInputParameters(StrictDTO):
    """Runner 进入 waiting(form) 的提问通知参数（Phase 2，设计 §5.6/§7.1）。

    session_title/event_kind 与 TerminalParameters 同形（Web Push 渲染复用
    同一模板字段），question_title 供站内信正文与徽标提示；session_id 供
    站内信跳转链接构造（resource_ref 仍以 runner 为准）。
    """
    session_title: str = Field(min_length=1,max_length=80)
    event_kind: Literal['waiting_input']
    question_title: str = Field(min_length=1,max_length=120)
    session_id: str = Field(min_length=1,max_length=64)


class PausedParameters(StrictDTO):
    """定时任务自动暂停通知参数（Phase 3，设计 §3.3/§8.1）。

    notify_target_id 为事件生成时的目标快照（任务行当前
    notification_target_id），后续任务改目标不影响已生成事件；
    无目标时缺省，走用户个人偏好路由。
    """
    task_name: str = Field(min_length=1,max_length=80)
    reason_label: str = Field(min_length=1,max_length=120)
    task_id: str = Field(min_length=1,max_length=64)
    notify_target_id: str | None = Field(default=None,min_length=1,max_length=128)


class NeedsActionParameters(StrictDTO):
    """scheduled 非提问类等待停车通知参数（Phase 3，设计 §8.1）。

    与 PausedParameters 同构（session_title/event_kind 供 Web Push 模板），
    action_label 说明要做什么（设备确认/人工处理），目标快照同规则。"""
    session_title: str = Field(min_length=1,max_length=80)
    event_kind: Literal['needs_action']
    task_name: str = Field(min_length=1,max_length=80)
    task_id: str = Field(min_length=1,max_length=64)
    action_label: str = Field(min_length=1,max_length=120)
    notify_target_id: str | None = Field(default=None,min_length=1,max_length=128)


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


class LeadGenReplyIntentParameters(StrictDTO):
    """AI获客：客户回复意向通知（producer=ai_lead_gen，recipient=campaign 属主用户）"""
    campaign_name: str = Field(min_length=1, max_length=128)
    company_name: str = Field(min_length=1, max_length=256)
    contact_name: str = Field(min_length=1, max_length=128)
    contact_email: str = Field(min_length=3, max_length=254)
    intent: Literal['interested', 'info_request', 'refused', 'irrelevant', 'unclear']
    intent_summary: str = Field(min_length=1, max_length=1000)


class LeadGenDailySummaryParameters(StrictDTO):
    """AI获客：每日循环执行汇总通知（design §8）"""
    campaign_name: str = Field(min_length=1, max_length=128)
    summary_date: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    new_leads: int = Field(ge=0, le=1e6)
    new_contacts: int = Field(ge=0, le=1e6)
    emails_sent: int = Field(ge=0, le=1e6)
    emails_failed: int = Field(ge=0, le=1e6)
    emails_bounced: int = Field(ge=0, le=1e6)
    new_replies: int = Field(ge=0, le=1e6)
    interested_replies: int = Field(ge=0, le=1e6)
    awaiting_review: int = Field(ge=0, le=1e6)


class LeadCapturedParameters(StrictDTO):
    """留资通知事件参数（规则引擎，设计 §5.1）。

    contact_summary 为发布方拼接的完整联系方式串（客户可同时留多种联系方式，
    全量拼接不截断，明文豁免口径见设计 §7）；assignee_user_id 为线索归属员工
    （kf_assignee 接收人展开依据，可空——无归属时仅外发不站内信）。
    """
    lead_id: str = Field(min_length=1, max_length=64)
    contact_method: Literal['phone', 'landline', 'email']
    contact_summary: str = Field(min_length=1, max_length=500)
    contact_name: str = Field(default='', max_length=128)
    # 微信昵称（渠道侧固定标识，便于员工在微信内定位客户）；contact_name 为
    # 对话中提取的姓名，客户口述（如「孙先生」）在外部系统不可检索
    customer_nickname: str = Field(default='', max_length=128)
    demand_summary: str = Field(default='', max_length=1000)
    kf_config_id: str = Field(min_length=1, max_length=64)
    open_kfid: str = Field(default='', max_length=64)
    kf_account_name: str = Field(min_length=1, max_length=128)
    assignee_user_id: str = Field(default='', max_length=64)
    assignee_name: str = Field(default='', max_length=128)
    previous_captured_at: str = Field(default='', max_length=32)


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

LEADGEN_INTENT_LABELS = {
    'interested': '有意向',
    'info_request': '索取资料',
    'refused': '婉拒',
    'irrelevant': '无关',
    'unclear': '待确认',
}

EVENTS.register('task.completed',EventDefinition('runner_terminal',TerminalParameters,'runner_result'))
EVENTS.register('task.failed',EventDefinition('runner_terminal',TerminalParameters,'runner_result'))
EVENTS.register('task.waiting_input',EventDefinition('runner_waiting_input',WaitingInputParameters,'runner_waiting'))
EVENTS.register('task.paused',EventDefinition('task_paused',PausedParameters,'scheduled_task'))
EVENTS.register('task.needs_action',EventDefinition('task_needs_action',NeedsActionParameters,'runner_waiting'))
EVENTS.register('notification.test',EventDefinition('notification_test',TestParameters,'notification_test'))
EVENTS.register('billing.credit_low',EventDefinition('billing_credit_low',CreditLowParameters,'tenant_credit'))
EVENTS.register('billing.tenant_expiry',EventDefinition('billing_tenant_expiry',TenantExpiryParameters,'tenant_profile'))
EVENTS.register('abuse.rule_triggered',EventDefinition('abuse_rule_triggered',AbuseParameters,'abuse_event'))
EVENTS.register('leadgen.reply_intent',EventDefinition('leadgen_reply_intent',LeadGenReplyIntentParameters,'leadgen_campaign'))
EVENTS.register('leadgen.daily_summary',EventDefinition('leadgen_daily_summary',LeadGenDailySummaryParameters,'leadgen_campaign'))
EVENTS.register('lead.captured',EventDefinition('lead_captured',LeadCapturedParameters,'lead_capture_lead'))


def _non_work_time_note(now=None):
    """非工作时间提示（设计 §5.1：沿用旧通知侧按服务器时间判断的口径，
    迁入模板渲染；渲染时刻即站内信读取/投递执行时刻）。"""
    from datetime import datetime
    now = now or datetime.now()
    if now.weekday() < 5 and 9 <= now.hour < 18:
        return ''
    return '当前为非工作时间，请于下一个工作日跟进。'


def render_inbox_text(event_type, parameters):
    """站内信标题与正文按事件模板渲染；仅做纯文本插值，参数已过严格模型。"""
    if event_type == 'task.waiting_input':
        return '等待你的回答', (
            f'「{parameters["session_title"]}」中的提问「{parameters["question_title"]}」'
            f'正在等待你的回答，请前往该会话处理。')
    if event_type == 'task.paused':
        return '定时任务已暂停', (
            f'「{parameters["task_name"]}」已自动暂停：{parameters["reason_label"]}。'
            f'请处理后前往任务视图手动恢复。')
    if event_type == 'task.needs_action':
        return '定时任务需要处理', (
            f'定时任务「{parameters["task_name"]}」{parameters["action_label"]}，'
            f'处理后执行将自动继续。')
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
    if event_type == 'leadgen.reply_intent':
        intent_label = LEADGEN_INTENT_LABELS.get(parameters['intent'], parameters['intent'])
        return '客户回复意向提醒', (
            f'获客活动「{parameters["campaign_name"]}」收到 '
            f'{parameters["company_name"]} {parameters["contact_name"]}'
            f'（{parameters["contact_email"]}）的回复，判定为「{intent_label}」：'
            f'{parameters["intent_summary"]}')
    if event_type == 'leadgen.daily_summary':
        p = parameters
        lines = [
            f'获客活动「{p["campaign_name"]}」{p["summary_date"]} 汇总：',
            f'新增客户 {p["new_leads"]}、有效联系人 {p["new_contacts"]}；',
            f'发送 {p["emails_sent"]} 封（失败 {p["emails_failed"]}、退信 {p["emails_bounced"]}）；',
            f'新回复 {p["new_replies"]} 条（有意向 {p["interested_replies"]} 条）。',
        ]
        if p['awaiting_review'] > 0:
            lines.append(f'另有 {p["awaiting_review"]} 封草稿待审阅确认。')
        return 'AI获客每日汇总', ''.join(lines)
    if event_type == 'lead.captured':
        # 优先显示微信昵称（外部可检索的固定标识），对话中提取的姓名作兜底
        name = (parameters.get('customer_nickname')
                or parameters.get('contact_name') or '未署名')
        demand = parameters.get('demand_summary') or '未提供'
        body = (f'微信客户「{name}」通过{parameters["contact_summary"]}留资'
                f'（客服账号 {parameters["kf_account_name"]}），'
                f'需求摘要：{demand}，请及时跟进。')
        if parameters.get('previous_captured_at'):
            body += f'该客户此前已于 {parameters["previous_captured_at"]} 留资，请结合历史需求跟进。'
        body += _non_work_time_note()
        return '客户留资提醒', body
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


def publish_event_in_tx(cursor, *, producer, event_type, event_key, tenant_id, resource_ref,
                        parameters, occurred_at, accepted_at, expires_at, category='task',
                        priority=0, policy_key=RULE_EVENT_POLICY_KEY, registry=EVENTS):
    """事件级消息发布（规则引擎，设计 §2/§5）：只提交"发生了什么"，接收人与
    外发由展开阶段按租户通知规则决定（recipient_user_id 为 NULL）。

    幂等：完整唯一约束含 recipient 列，NULL 互不相等，改命中部分唯一索引
    notification_event_identity(scope_key,producer,event_key) WHERE recipient_user_id IS NULL。
    仅租户 scope（事件必然归属具体租户）。"""
    definition,parameters=registry.validate(event_type,parameters,resource_ref)
    if (not producer or len(producer)>128 or not event_key or len(event_key)>512
            or not tenant_id or expires_at<=occurred_at):
        raise ValueError('NOTIFICATION_INTENT_INVALID')
    identifier,ref='notification_'+uuid.uuid4().hex,uuid.uuid4().hex
    scope_key='tenant:'+tenant_id
    cursor.execute('''INSERT INTO notification_messages
        (notification_id,notification_ref,schema_version,producer,event_type,event_key,
         scope_key,tenant_id,recipient_user_id,resource_ref,template_key,template_version,
         parameters_encrypted,occurred_at,accepted_at,expires_at,category,priority,policy_key)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,NULL,%s::jsonb,%s,1,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT(scope_key,producer,event_key) WHERE recipient_user_id IS NULL DO NOTHING
        RETURNING notification_ref''',
        (identifier,ref,SCHEMA_VERSION,producer,event_type,event_key,scope_key,tenant_id,
         json.dumps(resource_ref),definition.template_key,
         protected(parameters),occurred_at,accepted_at,expires_at,category,priority,policy_key))
    created=cursor.fetchone()
    if created:
        return created['notification_ref']
    cursor.execute('''SELECT notification_ref FROM notification_messages
        WHERE scope_key=%s AND recipient_user_id IS NULL AND producer=%s AND event_key=%s''',
        (scope_key,producer,event_key))
    return cursor.fetchone()['notification_ref']
