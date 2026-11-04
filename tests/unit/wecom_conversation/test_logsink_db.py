"""结果受理回调真实链路测试（隔离库，评审要求：真实结果回调及提交事务）。

不 mock 登记函数：_handle_send/_handle_send_status 走真实连接、真实 SQL，
验证范围登记、片段入库与对账归因；登记不创建计划或补录任务。
"""

import asyncio
import base64
import json
import uuid

import pytest


@pytest.fixture(autouse=True)
def _require_db(db_ready):
    yield


from src.db.database import get_db_connection
from src.wecom_conversation import archive, logsink, plans, service


def _locator(account='acc1', conversation='R:group1'):
    return 'ws1.' + base64.urlsafe_b64encode(
        json.dumps({'account': account, 'conversation_id': conversation}).encode()).decode().rstrip('=')


def _send_row(tenant_id, user_id, locator, device_id=None, observed=True, title='售前群'):
    data = {'session': locator, 'title': title, 'kind': 'group'}
    if observed:
        data['observed_chat'] = {
            'session': locator, 'source_generation': 'gen9',
            'messages': [
                {'msg_id': 31, 'side': 'peer', 'text': '客户提问', 'send_time': 1793635260,
                 'sender_name': '张三', 'sender_platform': 'wechat', 'parse_trusted': True,
                 'content_type': 2},
                {'msg_id': 32, 'side': 'self', 'text': '已回复', 'send_time': 1793635261,
                 'sender_id': 'acc1', 'sender_name': '本人', 'sender_platform': 'wecom',
                 'parse_trusted': True, 'content_type': 2},
            ],
            'confirmed_message_id': 32,
        }
    return {'id': uuid.uuid4(), 'tenant_id': tenant_id, 'user_id': user_id,
            'tool_name': 'wecom_message_send', 'provider_key': 'wecom',
            'state': 'succeeded', 'device_id': device_id, 'result_json': {'data': data},
            'arguments_json': {}, 'business_ref': {}}


@pytest.mark.asyncio
async def test_successful_send_registers_scope_without_backfill_and_stores_fragment(tenant_id):
    user_id = 'user-1'
    device = uuid.uuid4()
    locator = _locator()
    row = _send_row(tenant_id, user_id, locator, device_id=device)
    await logsink._handle_send(row)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            '''SELECT * FROM bs_wecom_conversations WHERE tenant_id=%s AND user_id=%s''',
            (tenant_id, user_id))
        stored = dict(cursor.fetchone())
        cursor.execute(
            '''SELECT task_id, task_prompt FROM scheduled_tasks
               WHERE tenant_id=%s AND user_id=%s AND task_prompt LIKE %s''',
            (tenant_id, user_id, '[wecom-conversation:backfill]%'))
        backfills = [dict(item) for item in cursor.fetchall()]
    assert stored['session_locator'] == locator
    assert stored['enroll_reasons'] == ['agent_replied']
    assert backfills == []  # 自动登记不提交补录
    # 片段入库 + 精确代发标注
    messages = service.list_messages(tenant_id, user_id, stored['id'])
    by_mid = {message['source_message_id']: message for message in messages}
    assert set(by_mid) == {31, 32}
    assert by_mid[32]['send_origin'] == 'agent'
    assert by_mid[32]['origin_evidence']['method'] == 'send_receipt'


@pytest.mark.asyncio
async def test_send_does_not_change_selected_plan_membership(tenant_id):
    """Agent 新会话自动进入记录范围，自选计划成员仍由页面配置决定。"""
    import uuid as _uuid
    user_id = 'user-1'
    device = _uuid.uuid4()
    locator = _locator()
    with get_db_connection() as conn:
        first, _ = archive.register_scope(conn, tenant_id, user_id, locator,
                                          device_id=device, reason='manual_add')
        conn.commit()
    created = plans.save_daily_plan(
        tenant_id, user_id, create=True, account='acc1', device=device,
        target_day='yesterday', scope_mode='selected', conversation_ids=[first['id']])
    plan_task_id = created['task_id']
    locator2 = _locator('acc1', 'R:joinme')
    await logsink._handle_send(_send_row(tenant_id, user_id, locator2, device_id=device,
                                         title='新会话', observed=False))
    from src.wecom_conversation import service
    joined = service.get_conversation(
        tenant_id, user_id,
        _single_id(tenant_id, user_id, 'R:joinme'))
    assert joined['daily_plan_task_id'] is None
    assert service.get_conversation(tenant_id, user_id, first['id'])['daily_plan_task_id'] == plan_task_id
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM scheduled_tasks WHERE tenant_id=%s', (tenant_id,))
        conn.commit()


def _single_id(tenant_id, user_id, conversation):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            '''SELECT id FROM bs_wecom_conversations
               WHERE tenant_id=%s AND user_id=%s AND source_conversation_id=%s''',
            (tenant_id, user_id, conversation))
        return cursor.fetchone()['id']


@pytest.mark.asyncio
async def test_repeat_send_does_not_duplicate_scope_or_create_backfill(tenant_id):
    user_id = 'user-1'
    locator = _locator()
    device = uuid.uuid4()
    await logsink._handle_send(_send_row(tenant_id, user_id, locator, device_id=device))
    await logsink._handle_send(_send_row(tenant_id, user_id, locator, device_id=device))
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT count(*) AS c FROM bs_wecom_conversations WHERE tenant_id=%s AND user_id=%s',
            (tenant_id, user_id))
        assert cursor.fetchone()['c'] == 1
        cursor.execute(
            '''SELECT count(*) AS c FROM scheduled_tasks
               WHERE tenant_id=%s AND user_id=%s AND task_prompt LIKE %s''',
            (tenant_id, user_id, '[wecom-conversation:backfill]%'))
        assert cursor.fetchone()['c'] == 0
        cursor.execute('DELETE FROM scheduled_tasks WHERE tenant_id=%s', (tenant_id,))
        conn.commit()


@pytest.mark.asyncio
async def test_send_status_confirmed_registers_scope_and_attributes_original(tenant_id):
    user_id = 'user-1'
    device = uuid.uuid4()
    locator = _locator()
    original_id = uuid.uuid4()
    original = {'id': original_id, 'tenant_id': tenant_id, 'user_id': user_id,
                'device_id': device, 'result_json': {'data': {
                    'session': locator,
                    'observed_chat': {'session': locator, 'source_generation': 'gen9',
                                      'messages': [
                                          {'msg_id': 41, 'side': 'self', 'text': '可能已发',
                                           'send_time': 1793635262, 'sender_id': 'acc1',
                                           'sender_name': '本人', 'sender_platform': 'wecom',
                                           'parse_trusted': True, 'content_type': 2}]}}}}
    status_row = {'id': uuid.uuid4(), 'tenant_id': tenant_id, 'user_id': user_id,
                  'tool_name': 'wecom_send_status', 'provider_key': 'wecom',
                  'state': 'succeeded', 'device_id': device,
                  'result_json': {'data': {
                      'send_status': 'confirmed',
                      'reconciliation': {'message_id': 41, 'source_generation': 'gen9'}}},
                  'arguments_json': {},
                  'business_ref': {'send_invocation_id': str(original_id)}}
    # 原发送行（曾为 unknown，带 observed_chat）
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            '''INSERT INTO local_tool_invocations
                   (id, tenant_id, user_id, device_id, tool_name, provider_key, state,
                    result_json, arguments_json)
               VALUES (%s,%s,%s,%s,'wecom_message_send','wecom','unknown',%s,%s)''',
            (str(original_id), tenant_id, user_id, str(device), json.dumps(original['result_json']), json.dumps({})))
        conn.commit()
    await logsink._handle_send_status(status_row)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT * FROM bs_wecom_conversations WHERE tenant_id=%s AND user_id=%s',
            (tenant_id, user_id))
        stored = dict(cursor.fetchone())
        cursor.execute(
            '''SELECT count(*) AS c FROM scheduled_tasks
               WHERE tenant_id=%s AND user_id=%s AND task_prompt LIKE %s''',
            (tenant_id, user_id, '[wecom-conversation:backfill]%'))
        backfills = cursor.fetchone()['c']
        cursor.execute('DELETE FROM scheduled_tasks WHERE tenant_id=%s', (tenant_id,))
        cursor.execute('DELETE FROM local_tool_invocations WHERE tenant_id=%s', (tenant_id,))
        conn.commit()
    assert stored['session_locator'] == locator
    assert stored['enroll_reasons'] == ['agent_replied']
    assert backfills == 0
    messages = service.list_messages(tenant_id, user_id, stored['id'])
    assert len(messages) == 1
    assert messages[0]['send_origin'] == 'agent'  # 对账确认补精确标注
    assert messages[0]['origin_evidence']['method'] == 'send_receipt'


@pytest.mark.asyncio
async def test_scope_error_notes_coverage_with_valid_json(tenant_id):
    """评审 P2-21：错误登记 SQL 本身合法（Json 适配器），可写回 coverage。"""
    user_id = 'user-1'
    locator = _locator()
    with get_db_connection() as connection:
        archive.register_scope(connection, tenant_id, user_id, locator, reason='agent_replied')
        connection.commit()
    await asyncio.to_thread(logsink._note_scope_error, tenant_id, user_id, locator, RuntimeError('boom'))
    conversation = service.get_conversation(tenant_id, user_id, _conversation_id(tenant_id, user_id))
    assert conversation['coverage']['last_log_error'].startswith('RuntimeError at ')



def _conversation_id(tenant_id, user_id):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT id FROM bs_wecom_conversations WHERE tenant_id=%s AND user_id=%s LIMIT 1',
            (tenant_id, user_id))
        return cursor.fetchone()['id']


@pytest.mark.asyncio
async def test_late_success_registers_scope_without_backfill(tenant_id):
    """评审二 P1-7：持久 unknown + _late_result 成功 → observed_invocation 视图
    参与旁路登记/首录补录；原 unknown 事实不改写。"""
    user_id = 'user-1'
    locator = _locator('acc1', 'R:late')
    late_payload = {'success': True, 'code': 'OK', 'effect': 'applied',
                    'data': {'session': locator, 'title': '迟到会话', 'kind': 'group'}}
    invocation_id = str(uuid.uuid4())
    with get_db_connection() as connection:
        cursor = connection.cursor()
        cursor.execute(
            '''INSERT INTO local_tool_invocations
                   (id, tenant_id, user_id, device_id, tool_name, provider_key, state,
                    result_json, arguments_json)
               VALUES (%s,%s,%s,%s,'wecom_message_send','wecom','unknown',%s,%s)''',
            (invocation_id, tenant_id, user_id, str(uuid.uuid4()),
             json.dumps({'_late_result': late_payload}), json.dumps({})))
        connection.commit()
    row = {'id': uuid.uuid4(), 'tenant_id': tenant_id, 'user_id': user_id,
           'tool_name': 'wecom_message_send', 'provider_key': 'wecom',
           'state': 'unknown', 'device_id': uuid.uuid4(),
           'result_json': {'_late_result': late_payload},
           'arguments_json': {}, 'business_ref': {}}
    await logsink.on_result_accepted(row)
    with get_db_connection() as connection:
        cursor = connection.cursor()
        cursor.execute('SELECT * FROM bs_wecom_conversations WHERE tenant_id=%s', (tenant_id,))
        stored = dict(cursor.fetchone())
        cursor.execute('SELECT state FROM local_tool_invocations WHERE tenant_id=%s', (tenant_id,))
        persisted_state = cursor.fetchone()['state']
        cursor.execute('DELETE FROM scheduled_tasks WHERE tenant_id=%s', (tenant_id,))
        connection.commit()
    assert stored['session_locator'] == locator  # 迟到成功自动纳入
    assert stored['enroll_reasons'] == ['agent_replied']
    assert persisted_state == 'unknown'  # 原 unknown 事实保持


@pytest.mark.asyncio
async def test_last_daily_generation_projects_partial_failure(tenant_id):
    """评审二 P2-11：最近一次执行的逐会话事实从 Runner checkpoint 投影。"""
    user_id = 'user-1'
    checkpoint = {'execution': {'resources': {'wecom_daily_result': {
        'business_date': '2026-11-03', 'runner_id': 'run-gen-1', 'task_id': 'sched_a',
        'scope_conversation_ids': [1, 2],
        'completed': [{'conversation_id': 1, 'name': '群A'}],
        'failed': [{'conversation_id': 2, 'name': '群B', 'code': 'X'}]}}}}
    with get_db_connection() as connection:
        cursor = connection.cursor()
        cursor.execute(
            '''INSERT INTO agent_runners
                   (runner_id, tenant_id, scope_key, session_kind, session_id,
                    actor_kind, actor_id, user_id, service_id, source,
                    client_request_id, input_digest, input, profile_id,
                    profile_fingerprint, checkpoint, status, record_id)
               VALUES ('run-gen-1', %s, 'tenant:'||%s, 'web', 'sess-g', 'user', %s, %s,
                       'web', 'chat', 'creq-1', 'd', '{}'::jsonb, 'main', 'fp',
                       %s::jsonb, 'completed', 'rec-gen-1')''',
            (tenant_id, tenant_id, user_id, user_id, json.dumps(checkpoint)))
        connection.commit()
    fact = service.last_daily_generation(tenant_id, user_id, '2026-11-03')
    assert fact['runner_id'] == 'run-gen-1'
    assert fact['completed'] == [{'conversation_id': 1, 'name': '群A'}]
    assert fact['failed'][0]['name'] == '群B'
    assert fact['task_id'] == 'sched_a'
    assert fact['scope_conversation_ids'] == [1, 2]
    # 评审三 P1-4：按 task_id 范围取事实，其他计划查不到本次执行
    assert service.last_daily_generation(tenant_id, user_id, '2026-11-03',
                                         task_id='sched_b') is None
    assert service.last_daily_generation(tenant_id, user_id, '2026-11-01') is None
