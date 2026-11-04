"""归档适配器纯逻辑测试（设计 §3.2/§4.6 映射与来源事实派生；不访问数据库）。"""

import base64
import json

from src.wecom_conversation.adapters import (
    batch_generation,
    decode_generation,
    decode_locator,
    extract_archive_plan,
    map_message,
    parse_local_time_text,
)


def _locator(account='acc1', conversation='R:group1'):
    return 'ws1.' + base64.urlsafe_b64encode(
        json.dumps({'account': account, 'conversation_id': conversation}).encode()).decode().rstrip('=')


def _cursor(generation):
    return 'wb1.' + base64.urlsafe_b64encode(
        json.dumps({'account': 'acc1', 'generation': generation, 'state': {}}).encode()).decode().rstrip('=')


def test_decode_locator_and_generation():
    assert decode_locator(_locator()) == ('acc1', 'R:group1')
    assert decode_locator('客户群名') is None
    assert decode_locator('ws1.broken') is None
    assert decode_generation(_cursor('g' * 64)) == 'g' * 64
    assert decode_generation('v2|a|b|c|d|1') is None
    assert batch_generation({'cursor': _cursor('g' * 64)}) == 'g' * 64
    assert batch_generation({'source_generation': 'explicit'}) == 'explicit'
    assert batch_generation({'cursor': None}) is None


def test_map_message_prefers_native_send_time_and_keeps_raw_text():
    message = {'msg_id': 5, 'sender_id': 'peer1', 'sender_name': '张三', 'sender_platform': 'wechat',
               'side': 'peer', 'kind': 'text', 'text': '你好', 'time': '2026-11-01 10:00:00',
               'parse_trusted': True, 'content_type': 2, 'send_time': 1790000000, 'sequence': 9, 'flag': 0}
    row = map_message(message, account='acc1', locator=_locator(), generation='gen',
                      device_id='dev', invocation_id='inv1')
    assert row['source_message_id'] == 5
    assert row['sender_side'] == 'peer'
    assert row['sent_at'] is not None  # 原生 send_time 事实
    assert row['source_time_text'] == '2026-11-01 10:00:00'
    assert row['source_fields'] == {'content_type': 2, 'send_time': 1790000000, 'sequence': 9, 'flag': 0}
    assert row['attachment_ref'] is None


def test_map_message_without_send_time_keeps_text_for_display_parse():
    row = map_message({'msg_id': 6, 'sender_id': 'x', 'side': 'self', 'time': '2026-11-01 10:00:00'},
                      account='a', locator=_locator(), generation='g', device_id='d', invocation_id='i')
    assert row['sent_at'] is None
    assert row['source_time_text'] == '2026-11-01 10:00:00'
    # 无日期文本 → sent_at 空、原文保留（日报列出日期未知缺口）
    row2 = map_message({'msg_id': 7, 'sender_id': 'x', 'side': 'peer', 'time': ''},
                       account='a', locator=_locator(), generation='g', device_id='d', invocation_id='i')
    assert row2['sent_at'] is None and row2['source_time_text'] == ''


def test_display_time_parse_uses_conversation_timezone():
    stamp = parse_local_time_text('2026-11-01 10:00:00', 'Asia/Shanghai')
    assert stamp.hour == 2 and stamp.utcoffset().total_seconds() == 0  # UTC 02:00
    assert parse_local_time_text('not-a-date', 'Asia/Shanghai') is None


def test_attachment_index_stays_logical():
    message = {'msg_id': 8, 'sender_id': 'x', 'side': 'peer', 'kind': 'file',
               'attachment': {'kind': 'file', 'status': 'available', 'name': '报价.pdf',
                              'mime_type': 'application/pdf', 'size_bytes': 12}}
    row = map_message(message, account='acc1', locator=_locator(), generation='g',
                      device_id='dev-uuid', invocation_id='i')
    ref = row['attachment_ref']
    assert ref['session'] == _locator()
    assert ref['source_generation'] == 'g'
    assert ref['device_id'] == 'dev-uuid'
    assert ref['meta']['name'] == '报价.pdf'
    serialized = json.dumps(ref, ensure_ascii=False)
    # 只存逻辑索引：不含本机路径（盘符/反斜杠）或内嵌内容
    assert '\\' not in serialized and 'C:' not in serialized and 'base64' not in serialized


def test_extract_plan_merges_batch_and_recovered_and_requires_generation():
    locator = _locator()
    data = {
        'source_generation': 'gen1',
        'sessions': [{'session': locator, 'conversation_id': 'R:group1', 'name': '售前客服群',
                      'kind': 'group', 'messages': [{'msg_id': 1, 'sender_id': 'a', 'side': 'peer', 'text': 'hi'}]}],
        'recovered_messages': [{'session': locator, 'baseline': True,
                                'message': {'msg_id': 2, 'sender_id': 'b', 'side': 'peer', 'text': 'old'}}],
        # new_messages/pending_sessions 是引用集合，不得重复入库
        'new_messages': [{'msg_id': 1, 'sender_id': 'a', 'side': 'peer', 'text': 'hi'}],
    }
    plan = extract_archive_plan(data, {'sessions': [locator]}, device_id='d', invocation_id='inv')
    assert plan['generation'] == 'gen1'
    session = plan['sessions'][0]
    assert session['name'] == '售前客服群'
    ids = [message['source_message_id'] for message in session['messages']]
    assert ids == [1, 2]
    assert session['messages'][1]['baseline'] is True


def test_extract_plan_requires_generation_or_locator():
    locator = _locator()
    assert extract_archive_plan({'messages': [{'msg_id': 1}]}, {'session': locator},
                                device_id='d', invocation_id='i') is None
    assert extract_archive_plan({'source_generation': 'g', 'messages': []}, {'session': '客户群'},
                                device_id='d', invocation_id='i') is None


def test_name_read_archives_from_result_identity():
    """R06：按名称读取成功时以结果稳定身份归档，不再因参数解码失败跳过。"""
    locator = _locator()
    data = {'source_generation': 'g', 'session': locator, 'title': '售前客服群', 'kind': 'group',
            'messages': [{'msg_id': 1, 'sender_id': 'a', 'side': 'peer', 'text': 'hi',
                          'time': '2026-11-01 10:00:00'}]}
    plan = extract_archive_plan(data, {'session': '售前客服群'}, device_id='d', invocation_id='i')
    assert plan is not None
    assert plan['sessions'][0]['locator'] == locator
    assert plan['sessions'][0]['messages'][0]['source_message_id'] == 1


def test_same_batch_normal_and_recovered_merge_by_source_id():
    """R07：同批普通消息与恢复候选重叠按原ID合并，保留恢复所得身份，不产生重复行。"""
    locator = _locator()
    data = {'source_generation': 'g',
            'messages': [{'msg_id': 5, 'sender_id': 'a', 'side': 'peer', 'text': 'hi',
                          'sender_platform': 'unknown', 'time': '2026-11-01 10:00:00'}],
            'recovered_messages': [{'session': locator, 'baseline': True,
                                    'message': {'msg_id': 5, 'sender_id': 'a', 'side': 'peer',
                                                'text': 'hi', 'sender_platform': 'wechat',
                                                'sender_platform_source': 'user_table'}}]}
    plan = extract_archive_plan(data, {'sessions': [locator]}, device_id='d', invocation_id='i')
    messages = plan['sessions'][0]['messages']
    assert len(messages) == 1  # 合并为一条，ON CONFLICT 不会同命令重复更新同一行
    assert messages[0]['sender_platform'] == 'wechat'  # 恢复所得的新鲜身份保留
    assert messages[0]['baseline'] is True
