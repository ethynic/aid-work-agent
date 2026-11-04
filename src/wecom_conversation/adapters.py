"""读取结果 → 业务归档行的事实解析（设计 §3.2/§4.6）。

本模块只做纯解析：不访问数据库、不派发任何操作。来源事实包括：
- 原会话定位 ws1.{account, conversation_id}（CLI 稳定标识，base64url JSON）；
- 来源代次：新插件 `data.source_generation`，旧结果按现行 wb1 游标派生，
  两者都不存在时不归档该批（不臆造代次）；
- 消息：`data.messages`（单会话）/ `data.sessions[*].messages`（批量）/
  `data.recovered_messages`（恢复候选），`new_messages`/`pending_sessions`
  是引用集合，不重复入库。
"""
from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = 'Asia/Shanghai'


class ArchiveSourceError(ValueError):
    """结果缺少可归档的来源事实（非存储故障；该批按原工具结果继续）。"""


def decode_locator(session_value):
    """ws1. 定位 → (account, conversation_id)；名称输入返回 None。"""
    if not isinstance(session_value, str) or not session_value.startswith('ws1.'):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(session_value[4:] + '===').decode('utf-8'))
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    account, conversation_id = payload.get('account'), payload.get('conversation_id')
    if not isinstance(account, str) or not account or not isinstance(conversation_id, str) or not conversation_id:
        return None
    return account, conversation_id


def decode_generation(cursor_value):
    """wb1. 游标 → generation（hash([sourceId, schema])）；非现行格式返回 None。"""
    if not isinstance(cursor_value, str) or not cursor_value.startswith('wb1.'):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor_value[4:] + '===').decode('utf-8'))
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return None
    if isinstance(payload, dict):
        generation = payload.get('generation')
        if isinstance(generation, str) and len(generation) <= 128:
            return generation
    return None


def batch_generation(data):
    """本批来源代次：优先新插件显式事实，回退现行 wb1 游标。"""
    explicit = data.get('source_generation')
    if isinstance(explicit, str) and explicit:
        return explicit
    return decode_generation(data.get('cursor'))


def _safe_int(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and -(2 ** 63) <= value <= 2 ** 63 - 1 else None


def parse_local_time_text(text, tz_name):
    """本地显示时间文本 'YYYY-MM-DD HH:MM:SS' → UTC（按会话存储时区解析）。

    这是显示时间解析（设计 §4.6），不声称取得原时间戳；无可判定日期返回 None。
    """
    if not isinstance(text, str) or len(text) < 10:
        return None
    try:
        day = datetime.strptime(text[:10], '%Y-%m-%d').date()
    except ValueError:
        return None
    clock = text[10:].strip() or '00:00:00'
    try:
        stamp = datetime.strptime(f'{day} {clock}'[:19], '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return None
    try:
        tz = ZoneInfo(tz_name or DEFAULT_TIMEZONE)
    except Exception:
        tz = ZoneInfo(DEFAULT_TIMEZONE)
    return stamp.replace(tzinfo=tz).astimezone(timezone.utc)


def attachment_index(message, locator, account, generation, device_id):
    """附件只存逻辑索引（设计 §4.3）：不存绝对路径/短期链接/Base64。"""
    attachment = message.get('attachment')
    if not isinstance(attachment, dict):
        return None
    keep = {}
    for key in ('kind', 'status', 'name', 'mime_type', 'size_bytes', 'width', 'height'):
        if attachment.get(key) is not None:
            keep[key] = attachment.get(key)
    return {
        'session': locator, 'account': account, 'source_generation': generation,
        'device_id': str(device_id) if device_id else None,
        'message_id': _safe_int(message.get('msg_id')), **({'meta': keep} if keep else {}),
    }


def source_fields(message):
    """小型原生字段快照（content_type/send_time/sequence/flag），不含正文副本。"""
    fields = {}
    for key in ('content_type', 'send_time', 'sequence', 'flag'):
        value = message.get(key)
        if _safe_int(value) is not None:
            fields[key] = value
    return fields or None


def map_message(message, *, account, locator, generation, device_id, invocation_id):
    """单条原消息 → 业务行字典；缺字段留空，不生成看似原生的 ID。

    sent_at 优先取原 send_time（UTC 事实，与时区无关）；旧结果仅本地 time 文本时
    保留原文，由归档层按会话存储时区补换算（设计 §4.6 显示时间解析）。
    """
    if not isinstance(message, dict) or _safe_int(message.get('msg_id')) is None:
        return None
    side = message.get('side')
    if side not in {'self', 'peer', 'timeline'}:
        side = 'timeline' if message.get('system') is True else 'peer'
    send_time = _safe_int(message.get('send_time'))
    sent_at = datetime.fromtimestamp(send_time, tz=timezone.utc) if send_time is not None and send_time >= 0 else None
    raw_time = message.get('time')
    platform = message.get('sender_platform')
    return {
        'source_message_id': message['msg_id'],
        'server_id': message.get('server_id') if isinstance(message.get('server_id'), str) else None,
        'source_invocation_id': invocation_id,
        'sender_id': message.get('sender_id') if isinstance(message.get('sender_id'), str) else None,
        'sender_name': message.get('sender_name') if isinstance(message.get('sender_name'), str) else None,
        'name_source': message.get('sender_name_source') if isinstance(message.get('sender_name_source'), str) else None,
        'sender_platform': platform if platform in {'wechat', 'wecom'} else 'unknown',
        'sender_side': side,
        'kind': message.get('kind') if isinstance(message.get('kind'), str) else None,
        'text': message.get('text') if isinstance(message.get('text'), str) else None,
        'text_format': message.get('text_format') if isinstance(message.get('text_format'), str) else None,
        'system': message.get('system') is True,
        'parse_trusted': message.get('parse_trusted') is True,
        'baseline': message.get('baseline') is True,
        'sent_at': sent_at,
        'source_time_text': raw_time if isinstance(raw_time, str) else None,
        'source_fields': source_fields(message),
        'attachment_ref': attachment_index(message, locator, account, generation, device_id),
    }


def _session_rows(data, arguments):
    """读取结果的会话视图：单会话与批量统一为行列表。

    单会话身份以成功结果的稳定 `data.session` 为准（R06）：按名称读取同样
    归档；仅当结果缺稳定标识时回退定位参数（必须是合法 ws1 定位）。
    """
    if isinstance(arguments, dict) and isinstance(arguments.get('session'), str):
        locator = data.get('session')
        if not isinstance(locator, str) or decode_locator(locator) is None:
            locator = arguments['session']
        decoded = decode_locator(locator)
        if decoded is None:
            return []
        account, conversation_id = decoded
        return [{'locator': locator, 'account': account, 'conversation_id': conversation_id,
                 'name': data.get('title') if isinstance(data.get('title'), str) else None,
                 'kind': data.get('kind') if isinstance(data.get('kind'), str) else None,
                 'peer_platform': data.get('peer_platform') if isinstance(data.get('peer_platform'), str) else None,
                 'messages': data.get('messages')}]
    rows = []
    for entry in data.get('sessions') or []:
        if not isinstance(entry, dict):
            continue
        decoded = decode_locator(entry.get('session'))
        if decoded is None:
            continue
        account, conversation_id = decoded
        if entry.get('conversation_id') not in (None, conversation_id):
            continue
        rows.append({'locator': entry['session'], 'account': account, 'conversation_id': conversation_id,
                     'name': entry.get('name') if isinstance(entry.get('name'), str) else None,
                     'kind': entry.get('kind') if isinstance(entry.get('kind'), str) else None,
                     'peer_platform': None,
                     'messages': entry.get('messages')})
    return rows


def _recovered_rows(data):
    """恢复候选沿用原来源身份与 baseline（设计 §3.2），不按恢复时间冒充发送时间。"""
    rows = []
    for entry in data.get('recovered_messages') or []:
        if not isinstance(entry, dict):
            continue
        decoded = decode_locator(entry.get('session'))
        if decoded is None or not isinstance(entry.get('message'), dict):
            continue
        rows.append({'locator': entry['session'], 'account': decoded[0], 'conversation_id': decoded[1],
                     'name': None, 'kind': None, 'peer_platform': None,
                     'messages': [entry['message']],
                     'baseline': entry.get('baseline') is True})
    return rows


def extract_archive_plan(result_data, arguments, *, device_id, invocation_id):
    """一次成功读取结果 → 按会话分组的归档计划。

    返回 {generation, sessions: [...]}；缺少来源代次或没有任何可定位会话时
    返回 None（调用方按原工具结果继续，不创建空业务数据）。

    同批内按原消息ID先合并（R07）：普通消息与恢复候选重叠时保留恢复所得
    身份（新鲜读取确认的 platform 等），只合并同一条原消息，不按正文去重。
    """
    if not isinstance(result_data, dict):
        return None
    generation = batch_generation(result_data)
    if not generation:
        return None
    grouped = {}
    order = []

    def _add(row, baseline_default, *, recovered=False):
        key = (row['account'], row['conversation_id'])
        if key not in grouped:
            grouped[key] = {'account': row['account'], 'conversation_id': row['conversation_id'],
                            'locator': row['locator'], 'name': row.get('name'), 'kind': row.get('kind'),
                            'peer_platform': row.get('peer_platform'), 'messages': {}}
            order.append(key)
        entry = grouped[key]
        baseline = row.get('baseline', baseline_default)
        for message in row.get('messages') or []:
            mapped = map_message(message, account=row['account'], locator=row['locator'],
                                 generation=generation, device_id=device_id,
                                 invocation_id=invocation_id)
            if mapped is None:
                continue
            mapped['baseline'] = mapped['baseline'] or baseline is True
            existing = entry['messages'].get(mapped['source_message_id'])
            if existing is None:
                entry['messages'][mapped['source_message_id']] = mapped
            elif recovered:
                # 恢复候选来自新鲜读取确认的原身份：保留其身份，baseline 取并集。
                mapped['baseline'] = mapped['baseline'] or existing['baseline']
                entry['messages'][mapped['source_message_id']] = mapped
        if row.get('name'):
            entry['name'] = row['name']
        if row.get('kind'):
            entry['kind'] = row['kind']
        if row.get('peer_platform'):
            entry['peer_platform'] = row['peer_platform']

    baseline_default = result_data.get('baseline') is True
    for row in _session_rows(result_data, arguments):
        _add(row, baseline_default)
    for row in _recovered_rows(result_data):
        _add(row, row.get('baseline', False), recovered=True)
    if not order:
        return None
    sessions = []
    for key in order:
        entry = grouped[key]
        entry['messages'] = list(entry['messages'].values())
        sessions.append(entry)
    return {'generation': generation, 'sessions': sessions}
