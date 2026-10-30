"""留资通知事件生产者（规则引擎，设计 §5.1/§7）。

在留资写入的同一事务内发布 lead.captured 事件级消息（recipient NULL），
接收人与外发由展开阶段按租户通知规则决定。contact_summary 拼接全部联系方式
（客户可同时留多种），可变长字段发布前截断到 DTO 上限——超长直接发布会被
DTO 校验拒绝导致零通知；明文豁免口径见设计 §7（仅投递到租户配置的内部目标，
参数加密落库、日志不落明文）。
"""
from datetime import datetime, timedelta

from .events import publish_event_in_tx

PRODUCER = 'lead_capture'

_CONTACT_LABELS = {'phone': '手机号', 'landline': '固定电话', 'email': '邮箱'}

# 与 events.LeadCapturedParameters 的字段上限保持一致：发布前截断，
# 避免超长输入（如 LLM 转写的 demand_summary）触发 DTO 校验失败丢通知
_CLIP_LIMITS = {
    'contact_summary': 500,
    'contact_name': 128,
    'demand_summary': 1000,
    'kf_account_name': 128,
    'assignee_name': 128,
}


def _clip(value: str, limit: int) -> str:
    return (value or '')[:limit]


def build_contact_summary(record):
    """按留资记录拼完整联系方式串（phone + other_contact 已由 DB 层解密）。"""
    parts = []
    if record.get('phone'):
        parts.append(f'手机号 {record["phone"]}')
    for item in record.get('other_contact') or []:
        label = _CONTACT_LABELS.get(item.get('type'), item.get('type') or '')
        value = item.get('value')
        if value:
            parts.append(f'{label} {value}' if label else str(value))
    return '、'.join(parts)


def publish_lead_captured_in_tx(cursor, record, *, kf_config_id, previous_captured_at=None,
                                config=None):
    """LeadCaptureDB.create 的 event_publisher 回调入口；调用方负责兜住异常
    （通知失败不影响留资结果，与旧邮件通知同口径）。"""
    if config is None:
        from src.config.settings import settings
        config = settings.notifications
    tenant_id = record.get('tenant_id')
    lead_id = record.get('lead_id')
    parameters = {
        'lead_id': lead_id,
        'contact_method': record.get('contact_method'),
        'contact_summary': build_contact_summary(record),
        'contact_name': record.get('contact_name') or '',
        'demand_summary': record.get('demand_summary') or '',
        # kf_config_id 缺失时退化为 open_kfid（channel 层规则不受影响，
        # kf_config 层规则按 ID 不匹配自然跳过）
        'kf_config_id': kf_config_id or record.get('channel_chat_id') or '',
        'kf_account_name': record.get('kf_account_name') or '',
        'assignee_user_id': record.get('assigned_to') or '',
        'assignee_name': record.get('assignee_name') or '',
        'previous_captured_at': previous_captured_at or '',
    }
    for field, limit in _CLIP_LIMITS.items():
        parameters[field] = _clip(parameters[field], limit)
    now = datetime.now()
    return publish_event_in_tx(
        cursor, producer=PRODUCER, event_type='lead.captured',
        event_key=f'lead:{tenant_id}:{lead_id}', tenant_id=tenant_id,
        resource_ref={'type': 'lead_capture_lead', 'id': lead_id},
        parameters=parameters, occurred_at=now, accepted_at=now,
        expires_at=now + timedelta(seconds=config.ttl_seconds),
        category='lead', priority=1)
