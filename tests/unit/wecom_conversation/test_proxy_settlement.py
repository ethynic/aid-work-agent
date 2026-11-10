"""代理批次结束核定测试（代理任务契约 §5，P6）。

- 纯函数：五条按序规则逐条（含 @我服务端核定、mentions_complete 不参与、
  非 JSON 不猜成功、确认发送事实优先）、发送事实三分类的 fail-closed 方向；
- 落库编排：Runner 终态 → 批次 succeeded＋processed_position 推进到冻结上界、
  end_json 原样保存、零发送重试一次再失败 halted、unknown → reconcile_hold；
- 详情投影：批次 end_json 出现在任务详情（P4 评审缺口）；
- 100/101-109/110 位置语义：推进只到 batch_end_msg_id（101 留给下批）。
"""

import json
import uuid

import pytest


@pytest.fixture(autouse=True)
def _require_db(db_ready):
    yield


from src.db.database import get_db_connection
from src.wecom_conversation import proxy_settlement, proxy_tasks
from src.wecom_conversation.proxy_settlement import (
    batch_has_explicit_mention, classify_send_facts, evaluate_end, parse_end_json)

DEVICE = '11111111-1111-1111-1111-111111111111'
ACCOUNT = 'acc1'
USER = 'user-1'


def _locator(number, account=ACCOUNT):
    import base64
    value = {'account': account, 'conversation_id': f'R:group-{number}'}
    return 'ws1.' + base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')


def _peer(msg_id, text='在吗', **overrides):
    message = {'msg_id': msg_id, 'side': 'peer', 'system': False, 'text': text,
               'sender_id': 'customer-1', 'sender_name': '客户',
               'sender_platform': 'wecom', 'send_time': 1760000000,
               'mentions': [], 'mentions_complete': True}
    message.update(overrides)
    return message


def _register(tenant_id, locator, device=DEVICE):
    from src.wecom_conversation import archive

    with get_db_connection() as conn:
        archive.register_scope(conn, tenant_id, USER, locator, name='售前群', kind='group',
                               device_id=device, reason='manual_add')
        conn.commit()


def _create_started(tenant_id, locators, *, account=ACCOUNT, device=DEVICE):
    task = proxy_tasks.create_task(
        tenant_id, USER, device_id=device, account=account, self_user_id=account,
        title='售前群代理', agent_id='agent-a', instruction='有客户提问时回答',
        sessions=locators)
    baselines = {locator: {'cursor': f'wb1.base-{index}', 'generation': 'gen-1',
                           'watermark': 100}
                 for index, locator in enumerate(locators)}
    return proxy_tasks.complete_start(tenant_id, USER, task['id'], device_id=device,
                                      self_user_id=account, baselines=baselines)


def _make_batch(tenant_id, task_id, session, *, end=100, messages=None, web_session_id=None,
                runner_id=None, status='pending'):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if web_session_id is None:
            cursor.execute('''SELECT web_session_id FROM bs_wecom_proxy_task_sessions
                              WHERE tenant_id=%s AND task_id=%s AND session=%s''',
                           (tenant_id, task_id, session))
            web_session_id = cursor.fetchone()['web_session_id']
        batch = proxy_tasks.create_batch_in_tx(
            cursor, tenant_id, USER, task_id=task_id, session=session, batch_end_msg_id=end,
            input_snapshot={'schema_version': 1, 'session': session,
                            'source_generation': 'gen-2',
                            'messages': messages if messages is not None else [_peer(99)]},
            agent_id='agent-a', instruction_snapshot='有客户提问时回答',
            web_session_id=web_session_id)
        if status != 'pending' or runner_id:
            proxy_tasks.transition_batch_in_tx(cursor, tenant_id, batch['id'], status,
                                               runner_id=runner_id)
        conn.commit()
    return proxy_tasks.get_batch(tenant_id, batch['id'])


_next_runner_seq = [0]


def _insert_runner(tenant_id, session, *, status='completed', attempt=1, output=None,
                   runner_id=None, credit_cost=None):
    _next_runner_seq[0] += 1
    runner_id = runner_id or f'runner_{_next_runner_seq[0]:08d}'
    result = {'status': status, 'output': output or '', 'images': [],
              'error_code': None if status == 'completed' else 'EXECUTION_FAILED'}
    # 终态公共结果含结算积分（finalizer 落 public_result.credit_cost 的形状）
    if credit_cost is not None:
        result['credit_cost'] = credit_cost
    result = json.dumps(result, ensure_ascii=False)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            '''INSERT INTO agent_runners
                   (runner_id, tenant_id, scope_key, session_kind, session_id, actor_kind,
                    actor_id, user_id, service_id, source, client_request_id, input_digest,
                    input, profile_id, profile_fingerprint, checkpoint, status, attempt,
                    result, record_id)
               VALUES (%s,%s,'tenant:'||%s,'web',%s,'system','wecom_proxy',%s,
                       'wecom_proxy','wecom_proxy',%s,'d','{}'::jsonb,'agent-a','fp',
                       '{}'::jsonb,%s,%s,%s::jsonb,'rec_'||%s)''',
            (runner_id, tenant_id, tenant_id, session, USER,
             f'{runner_id}:1', status, attempt, result, str(_next_runner_seq[0])))
        conn.commit()
    return runner_id


def _insert_send(tenant_id, runner_id, *, state='succeeded', effect='none',
                 tool='wecom_message_send'):
    invocation_id = str(uuid.uuid4())
    result = None
    if state == 'succeeded':
        result = json.dumps({'success': True, 'code': 'OK', 'effect': effect,
                             'data': {'verification': 'local-record', 'receipt_id': 'r1',
                                      'session': 'ws1.x',
                                      'verification_detail': {'method': 'local-record',
                                                              'message_id': 110,
                                                              'source_generation': 'gen-2'}}})
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            '''INSERT INTO local_tool_invocations
                   (id, tenant_id, user_id, session_id, device_id, tool_name, arguments_json,
                    state, effect, result_json, provider_key, business_kind, business_ref)
               VALUES (%s,%s,%s,'sess-proxy',%s,%s,'{}'::jsonb,%s,%s,%s::jsonb,'wecom',
                       'agent_runner',%s::jsonb)''',
            (invocation_id, tenant_id, USER, DEVICE, tool, state, effect, result,
             json.dumps({'runner_id': runner_id})))
        conn.commit()
    return invocation_id


def _batch_row(tenant_id, batch_id):
    return proxy_tasks.get_batch(tenant_id, batch_id)


def _target_row(tenant_id, task_id, session):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''SELECT * FROM bs_wecom_proxy_task_sessions
                          WHERE tenant_id=%s AND task_id=%s AND session=%s''',
                       (tenant_id, task_id, session))
        return dict(cursor.fetchone())


def _end_json(batch, *, needs_reply=True, replies=None, unanswered=None):
    return {'schema_version': 1, 'batch_id': str(batch['id']), 'task_id': str(batch['task_id']),
            'session': batch['session'], 'batch_end_msg_id': batch['batch_end_msg_id'],
            'needs_reply': needs_reply, 'replies': replies or [], 'unanswered': unanswered or []}


# ==================== 纯函数：@我核定与发送事实分类 ====================

def test_mention_adjudicated_from_snapshot_not_self_report():
    """@我是服务端核定事实：按快照 mentions（含 @所有人）核定，不看 Runner 自报。"""
    snapshot = {'messages': [_peer(1, mentions=[{'kind': 'user', 'user_id': ACCOUNT,
                                                 'name': '我'}])]}
    assert batch_has_explicit_mention(snapshot, ACCOUNT) is True
    all_msg = {'messages': [_peer(2, mentions=[{'kind': 'all', 'user_id': '0',
                                                'name': '所有人'}])]}
    assert batch_has_explicit_mention(all_msg, ACCOUNT) is True
    other = {'messages': [_peer(3, mentions=[{'kind': 'user', 'user_id': 'someone-else'}])]}
    assert batch_has_explicit_mention(other, ACCOUNT) is False
    assert batch_has_explicit_mention({'messages': [_peer(4)]}, ACCOUNT) is False
    assert batch_has_explicit_mention(None, ACCOUNT) is False


def test_mentions_complete_not_used_for_adjudication():
    """mentions_complete=false 不参与核定：既不据此认定有 @，也不据此认定无 @。"""
    incomplete = {'messages': [_peer(1, mentions=[], mentions_complete=False)]}
    assert batch_has_explicit_mention(incomplete, ACCOUNT) is False


def test_send_facts_classification_fail_closed():
    """发送事实三分类的 fail-closed 方向：succeeded 确认；failed/cancelled 且
    effect=none 证明未发送；其余（unknown/非终态/effect 不明）不可证明。"""
    confirmed, unprovable = classify_send_facts([
        {'state': 'succeeded', 'effect': 'none'},
        {'state': 'failed', 'effect': 'none'},
        {'state': 'cancelled', 'effect': 'none'},
        {'state': 'unknown', 'effect': None},
        {'state': 'failed', 'effect': 'unknown'},
        {'state': 'running', 'effect': None},
    ])
    assert len(confirmed) == 1 and len(unprovable) == 3


def test_parse_end_json_strict():
    batch = {'id': 5, 'task_id': 9, 'session': 'ws1.abc', 'batch_end_msg_id': 100}
    good = _end_json(batch)
    assert parse_end_json(json.dumps(good), batch) == good
    # 非 JSON/围栏包裹/缺字段/标识不符 → None（不猜成功）
    assert parse_end_json('好的，处理完成', batch) is None
    assert parse_end_json('```json\n' + json.dumps(good) + '\n```', batch) is None
    missing = {k: v for k, v in good.items() if k != 'needs_reply'}
    assert parse_end_json(json.dumps(missing), batch) is None
    assert parse_end_json(json.dumps({**good, 'batch_end_msg_id': 105}), batch) is None


# ==================== 纯函数：五条按序规则 ====================

_BATCH = {'id': 5, 'task_id': 9, 'session': 'ws1.abc', 'batch_end_msg_id': 100}


def _eval(runner_status, send_rows, *, mention=False, retry=0, end_raw=None):
    return evaluate_end(runner_status, end_raw, send_rows, explicit_mention=mention,
                        retry_count=retry, batch=_BATCH)


def test_rule1_no_reply_needed_advances():
    """①needs_reply=false＋零发送＋核定无 @我 → 推进。"""
    end_raw = json.dumps(_end_json(_BATCH, needs_reply=False), ensure_ascii=False)
    verdict = _eval('completed', [], end_raw=end_raw)
    assert verdict['action'] == 'advance' and verdict['reason'] == 'no_reply_needed'


def test_rule1_blocked_by_server_adjudicated_mention():
    """⑤明确 @我＋全批无需回复＋零发送：拒绝按①正常结束，落入③重试路径。"""
    end_raw = json.dumps(_end_json(_BATCH, needs_reply=False), ensure_ascii=False)
    verdict = _eval('completed', [], mention=True, end_raw=end_raw)
    assert verdict['action'] == 'retry' and verdict['reason'] == 'zero_send_retry'


def test_rule2_confirmed_send_advances_despite_unknown_others():
    """②≥1 条确认发送即推进；其余失败/unknown 仅展示（JSON 无效也不阻塞）。"""
    verdict = _eval('completed', [{'state': 'succeeded', 'effect': 'none'},
                                  {'state': 'unknown', 'effect': None},
                                  {'state': 'failed', 'effect': 'none'}], end_raw='不是JSON')
    assert verdict['action'] == 'advance' and verdict['reason'] == 'confirmed_send'
    assert verdict['end_json'] is None  # 非 JSON 不猜成功，原样不保存结构


def test_rule3_zero_send_retry_once_then_halt():
    """③零发送且全部证明未发送 → 重试一次；再失败 halted。"""
    end_raw = json.dumps(_end_json(_BATCH, needs_reply=True), ensure_ascii=False)
    first = _eval('completed', [{'state': 'failed', 'effect': 'none'}], retry=0,
                  end_raw=end_raw)
    assert first['action'] == 'retry' and first['reason'] == 'zero_send_retry'
    second = _eval('completed', [{'state': 'failed', 'effect': 'none'}], retry=1,
                   end_raw=end_raw)
    assert second['action'] == 'halt' and second['reason'] == 'zero_send_retry_exhausted'
    # 无任何发送调用同样走重试
    assert _eval('completed', [], end_raw=end_raw)['action'] == 'retry'


def test_rule3_applies_to_failed_runner_with_zero_send():
    """Runner 终态失败＋零发送 → 同③重试一次（不恢复终态 Runner，换新执行）。"""
    assert _eval('failed', [], retry=0)['action'] == 'retry'
    assert _eval('failed', [{'state': 'failed', 'effect': 'none'}], retry=0)['action'] == 'retry'
    assert _eval('failed', [], retry=1)['action'] == 'halt'


def test_rule4_unproven_send_reconcile_hold():
    """④发生过发送调用且任一次无法证明未发送且无确认 → reconcile_hold 不重发。"""
    for row in ({'state': 'unknown', 'effect': None},
                {'state': 'failed', 'effect': 'unknown'},
                {'state': 'cancelled', 'effect': 'partial'}):
        verdict = _eval('completed', [{'state': 'failed', 'effect': 'none'}, row], retry=0)
        assert verdict['action'] == 'reconcile_hold', row
    # Runner 失败＋unknown 同样规则 4（失败 Runner 也可能有在途 unknown）
    assert _eval('failed', [{'state': 'unknown', 'effect': None}], retry=0)['action'] \
        == 'reconcile_hold'


def test_rule4_precedence_over_retry_when_mention_and_unknown():
    """@我＋unknown 发送：规则 4 优先于规则 3/5（先对账，不自动重发）。"""
    verdict = _eval('completed', [{'state': 'unknown', 'effect': None}], mention=True, retry=0)
    assert verdict['action'] == 'reconcile_hold'


# ==================== 落库编排 ====================

def _adjudicate(tenant_id, batch):
    return proxy_settlement.adjudicate_batch(batch)


def test_adjudicate_advance_updates_position_and_end_json(tenant_id):
    """核定推进：批次 succeeded＋end_json 原样落库＋processed_position→冻结上界。"""
    locator = _locator(1)
    _register(tenant_id, locator)
    task = _create_started(tenant_id, [locator])
    snapshot_messages = [_peer(97), _peer(100, mentions=[{'kind': 'user', 'user_id': 'c9'}])]
    batch = _make_batch(tenant_id, task['id'], locator, end=100, messages=snapshot_messages,
                        runner_id=None)
    runner_id = _insert_runner(tenant_id, batch['web_session_id'],
                               output=json.dumps(_end_json(batch, needs_reply=True,
                                                           replies=[{'inbound_msg_ids': [97],
                                                                     'text': '好的'}]),
                                                 ensure_ascii=False))
    _insert_send(tenant_id, runner_id, state='succeeded')
    with get_db_connection() as conn:
        cursor = conn.cursor()
        proxy_tasks.transition_batch_in_tx(cursor, tenant_id, batch['id'], 'running',
                                           runner_id=runner_id)
        conn.commit()
    verdict = _adjudicate(tenant_id, proxy_tasks.get_batch(tenant_id, batch['id']))
    assert verdict['action'] == 'advance' and verdict['reason'] == 'confirmed_send'
    row = _batch_row(tenant_id, batch['id'])
    assert row['status'] == 'succeeded' and row['end_json']['replies'][0]['text'] == '好的'
    target = _target_row(tenant_id, task['id'], locator)
    assert target['processed_position'] == 100  # 推进只到冻结上界
    assert row['finished_at'] is not None


def test_adjudicate_position_100_next_batch_from_101(tenant_id):
    """100/101-109/110 位置语义：本批推进只到 100；101 起留给下批（监控游标已保证，
    此处验证推进值不被 end JSON 或 later_seen 覆盖）。"""
    locator = _locator(2)
    _register(tenant_id, locator)
    task = _create_started(tenant_id, [locator])
    batch = _make_batch(tenant_id, task['id'], locator, end=100,
                        messages=[_peer(100)], runner_id=None)
    runner_id = _insert_runner(tenant_id, batch['web_session_id'],
                               output=json.dumps(_end_json(batch, needs_reply=False),
                                                 ensure_ascii=False))
    with get_db_connection() as conn:
        cursor = conn.cursor()
        proxy_tasks.transition_batch_in_tx(cursor, tenant_id, batch['id'], 'running',
                                           runner_id=runner_id)
        conn.commit()
    _adjudicate(tenant_id, proxy_tasks.get_batch(tenant_id, batch['id']))
    target = _target_row(tenant_id, task['id'], locator)
    assert target['processed_position'] == 100


def test_adjudicate_zero_send_retry_then_halt(tenant_id):
    """needs_reply=true 零发送：重试一次（failed→pending，retry_count+1，清
    runner_id）；再失败 → 会话 halted，批次 failed。"""
    locator = _locator(3)
    _register(tenant_id, locator)
    task = _create_started(tenant_id, [locator])
    batch = _make_batch(tenant_id, task['id'], locator, end=100,
                        messages=[_peer(99)], runner_id=None)
    runner_id = _insert_runner(tenant_id, batch['web_session_id'],
                               output=json.dumps(_end_json(batch, needs_reply=True),
                                                 ensure_ascii=False))
    with get_db_connection() as conn:
        cursor = conn.cursor()
        proxy_tasks.transition_batch_in_tx(cursor, tenant_id, batch['id'], 'running',
                                           runner_id=runner_id)
        conn.commit()
    first = _adjudicate(tenant_id, proxy_tasks.get_batch(tenant_id, batch['id']))
    assert first['action'] == 'retry'
    row = _batch_row(tenant_id, batch['id'])
    assert row['status'] == 'pending' and row['retry_count'] == 1 and row['runner_id'] is None
    assert row['cli_error'] == 'zero_send_retry'
    # 第二次（重试 Runner）仍零发送 → halted
    batch2 = _batch_row(tenant_id, batch['id'])
    runner2 = _insert_runner(tenant_id, batch2['web_session_id'],
                             output=json.dumps(_end_json(batch2, needs_reply=True),
                                               ensure_ascii=False))
    with get_db_connection() as conn:
        cursor = conn.cursor()
        proxy_tasks.transition_batch_in_tx(cursor, tenant_id, batch['id'], 'running',
                                           runner_id=runner2)
        conn.commit()
    second = _adjudicate(tenant_id, proxy_tasks.get_batch(tenant_id, batch['id']))
    assert second['action'] == 'halt'
    row = _batch_row(tenant_id, batch['id'])
    assert row['status'] == 'failed' and row['retry_count'] == 1
    target = _target_row(tenant_id, task['id'], locator)
    assert target['status'] == 'halted' and 'zero_send' in target['halted_reason']


def test_adjudicate_unknown_send_reconcile_hold(tenant_id):
    """回执无法证明未发送（unknown）→ reconcile_hold，不自动重发不推进。"""
    locator = _locator(4)
    _register(tenant_id, locator)
    task = _create_started(tenant_id, [locator])
    batch = _make_batch(tenant_id, task['id'], locator, end=100,
                        messages=[_peer(99)], runner_id=None)
    runner_id = _insert_runner(tenant_id, batch['web_session_id'],
                               output=json.dumps(_end_json(batch, needs_reply=True,
                                                           replies=[], unanswered=[
                                                               {'msg_id': 99, 'reason': 'unknown'}]),
                                                 ensure_ascii=False))
    _insert_send(tenant_id, runner_id, state='unknown', effect='unknown')
    with get_db_connection() as conn:
        cursor = conn.cursor()
        proxy_tasks.transition_batch_in_tx(cursor, tenant_id, batch['id'], 'running',
                                           runner_id=runner_id)
        conn.commit()
    verdict = _adjudicate(tenant_id, proxy_tasks.get_batch(tenant_id, batch['id']))
    assert verdict['action'] == 'reconcile_hold'
    row = _batch_row(tenant_id, batch['id'])
    assert row['status'] == 'reconcile_hold'
    target = _target_row(tenant_id, task['id'], locator)
    assert target['status'] == 'active'  # 目标不停住，仅批次挂起待人工
    assert target['processed_position'] == 100  # 不推进


def test_adjudicate_confirmed_send_despite_unknown(tenant_id):
    """部分回复 unknown 但有一条 local-record 确认 → 仍推进（unanswered 仅展示）。"""
    locator = _locator(5)
    _register(tenant_id, locator)
    task = _create_started(tenant_id, [locator])
    batch = _make_batch(tenant_id, task['id'], locator, end=100,
                        messages=[_peer(98), _peer(100)], runner_id=None)
    runner_id = _insert_runner(tenant_id, batch['web_session_id'],
                               output=json.dumps(
                                   _end_json(batch, needs_reply=True,
                                             replies=[{'inbound_msg_ids': [98], 'text': '在的',
                                                       'receipt_id': 'r1',
                                                       'verification': 'local-record'}],
                                             unanswered=[{'msg_id': 100, 'reason': 'send_failed',
                                                          'cli_error': {'code': 'UI_CHANGED'}}]),
                                   ensure_ascii=False))
    _insert_send(tenant_id, runner_id, state='succeeded')
    _insert_send(tenant_id, runner_id, state='unknown', effect='unknown')
    with get_db_connection() as conn:
        cursor = conn.cursor()
        proxy_tasks.transition_batch_in_tx(cursor, tenant_id, batch['id'], 'running',
                                           runner_id=runner_id)
        conn.commit()
    verdict = _adjudicate(tenant_id, proxy_tasks.get_batch(tenant_id, batch['id']))
    assert verdict['action'] == 'advance' and verdict['reason'] == 'confirmed_send'
    row = _batch_row(tenant_id, batch['id'])
    assert row['status'] == 'succeeded' and row['end_json']['unanswered'][0]['msg_id'] == 100


def test_adjudicate_mention_rejects_no_reply(tenant_id):
    """@我核定拒绝"无需回复"：服务端按 mentions 判定，走重试路径。"""
    locator = _locator(6)
    _register(tenant_id, locator)
    task = _create_started(tenant_id, [locator])
    batch = _make_batch(tenant_id, task['id'], locator, end=100,
                        messages=[_peer(99, mentions=[{'kind': 'user', 'user_id': ACCOUNT,
                                                       'name': '我'}])],
                        runner_id=None)
    assert batch['input_snapshot']['messages'][0]['mentions'][0]['user_id'] == ACCOUNT
    runner_id = _insert_runner(tenant_id, batch['web_session_id'],
                               output=json.dumps(_end_json(batch, needs_reply=False),
                                                 ensure_ascii=False))
    with get_db_connection() as conn:
        cursor = conn.cursor()
        proxy_tasks.transition_batch_in_tx(cursor, tenant_id, batch['id'], 'running',
                                           runner_id=runner_id)
        conn.commit()
    verdict = _adjudicate(tenant_id, proxy_tasks.get_batch(tenant_id, batch['id']))
    assert verdict['action'] == 'retry'
    assert _batch_row(tenant_id, batch['id'])['status'] == 'pending'


def test_adjudicate_invalid_end_json_not_guess_success(tenant_id):
    """非 JSON 输出＋零发送：不猜成功——按规则 3 重试一次并留 cli_error。"""
    locator = _locator(7)
    _register(tenant_id, locator)
    task = _create_started(tenant_id, [locator])
    batch = _make_batch(tenant_id, task['id'], locator, end=100,
                        messages=[_peer(99)], runner_id=None)
    runner_id = _insert_runner(tenant_id, batch['web_session_id'], output='已处理完成。')
    with get_db_connection() as conn:
        cursor = conn.cursor()
        proxy_tasks.transition_batch_in_tx(cursor, tenant_id, batch['id'], 'running',
                                           runner_id=runner_id)
        conn.commit()
    verdict = _adjudicate(tenant_id, proxy_tasks.get_batch(tenant_id, batch['id']))
    assert verdict['action'] == 'retry' and verdict['end_json'] is None
    row = _batch_row(tenant_id, batch['id'])
    assert row['status'] == 'pending' and row['end_json'] is None


def test_detail_projection_returns_end_json(tenant_id):
    """任务详情投影返回批次 end_json（P4 评审缺口：回复文本＋CLI 证据可展示）。"""
    locator = _locator(8)
    _register(tenant_id, locator)
    task = _create_started(tenant_id, [locator])
    batch = _make_batch(tenant_id, task['id'], locator, end=100,
                        messages=[_peer(99)], runner_id=None)
    runner_id = _insert_runner(tenant_id, batch['web_session_id'],
                               output=json.dumps(_end_json(batch, needs_reply=False),
                                                 ensure_ascii=False))
    with get_db_connection() as conn:
        cursor = conn.cursor()
        proxy_tasks.transition_batch_in_tx(cursor, tenant_id, batch['id'], 'running',
                                           runner_id=runner_id)
        conn.commit()
    _adjudicate(tenant_id, proxy_tasks.get_batch(tenant_id, batch['id']))
    detail = proxy_tasks.get_task_detail(tenant_id, USER, task['id'])
    rows = [item for item in detail['batches'] if item['id'] == batch['id']]
    assert rows and rows[0]['end_json']['needs_reply'] is False
    assert rows[0]['runner_id'] == runner_id and rows[0]['status'] == 'succeeded'


def test_adjudicate_settles_stop_requested(tenant_id):
    """停止请求在批次核定离开 running 后收敛 stopped（复用 P4 语义）。"""
    locator = _locator(9)
    _register(tenant_id, locator)
    task = _create_started(tenant_id, [locator])
    batch = _make_batch(tenant_id, task['id'], locator, end=100,
                        messages=[_peer(99)], runner_id=None)
    runner_id = _insert_runner(tenant_id, batch['web_session_id'],
                               output=json.dumps(_end_json(batch, needs_reply=False),
                                                 ensure_ascii=False))
    with get_db_connection() as conn:
        cursor = conn.cursor()
        proxy_tasks.transition_batch_in_tx(cursor, tenant_id, batch['id'], 'running',
                                           runner_id=runner_id)
        cursor.execute('''UPDATE bs_wecom_proxy_tasks SET stop_requested=TRUE
                          WHERE id=%s AND tenant_id=%s''', (task['id'], tenant_id))
        conn.commit()
    _adjudicate(tenant_id, proxy_tasks.get_batch(tenant_id, batch['id']))
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT status FROM bs_wecom_proxy_tasks WHERE id=%s AND tenant_id=%s',
                       (task['id'], tenant_id))
        assert cursor.fetchone()['status'] == 'stopped'


# ==================== 批次回答积分落库（P7 评审补充，加性） ====================

def test_adjudicate_persists_runner_credit_cost(tenant_id):
    """核定推进同事务落 Runner 结算积分（public_result.credit_cost → 批次列）。"""
    locator = _locator(9)
    _register(tenant_id, locator)
    task = _create_started(tenant_id, [locator])
    batch = _make_batch(tenant_id, task['id'], locator, end=100,
                        messages=[_peer(99)], runner_id=None)
    runner_id = _insert_runner(tenant_id, batch['web_session_id'],
                               output=json.dumps(_end_json(batch, needs_reply=False),
                                                 ensure_ascii=False),
                               credit_cost=0.42)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        proxy_tasks.transition_batch_in_tx(cursor, tenant_id, batch['id'], 'running',
                                           runner_id=runner_id)
        conn.commit()
    verdict = _adjudicate(tenant_id, proxy_tasks.get_batch(tenant_id, batch['id']))
    assert verdict['action'] == 'advance'
    row = _batch_row(tenant_id, batch['id'])
    assert row['status'] == 'succeeded'
    assert row['credit_cost'] == pytest.approx(0.42)


def test_adjudicate_retry_accumulates_credit_across_attempts(tenant_id):
    """零发送重试再停住：同一批次行 credit_cost 跨 Runner attempt 累加。"""
    locator = _locator(10)
    _register(tenant_id, locator)
    task = _create_started(tenant_id, [locator])
    batch = _make_batch(tenant_id, task['id'], locator, end=100,
                        messages=[_peer(99)], runner_id=None)
    runner_id = _insert_runner(tenant_id, batch['web_session_id'],
                               output=json.dumps(_end_json(batch, needs_reply=True),
                                                 ensure_ascii=False),
                               credit_cost=0.30)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        proxy_tasks.transition_batch_in_tx(cursor, tenant_id, batch['id'], 'running',
                                           runner_id=runner_id)
        conn.commit()
    assert _adjudicate(tenant_id, proxy_tasks.get_batch(tenant_id, batch['id']))['action'] == 'retry'
    assert _batch_row(tenant_id, batch['id'])['credit_cost'] == pytest.approx(0.30)

    # 重试 Runner 仍零发送 → halt；积分累加为两次 attempt 合计
    batch2 = _batch_row(tenant_id, batch['id'])
    runner2 = _insert_runner(tenant_id, batch2['web_session_id'],
                             output=json.dumps(_end_json(batch2, needs_reply=True),
                                               ensure_ascii=False),
                             credit_cost=0.25)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        proxy_tasks.transition_batch_in_tx(cursor, tenant_id, batch['id'], 'running',
                                           runner_id=runner2)
        conn.commit()
    assert _adjudicate(tenant_id, proxy_tasks.get_batch(tenant_id, batch['id']))['action'] == 'halt'
    assert _batch_row(tenant_id, batch['id'])['credit_cost'] == pytest.approx(0.55)


def test_adjudicate_without_terminal_result_writes_no_credit(tenant_id):
    """中断 Runner 无终态 result（无 credit_cost 字段）：不写不猜 0，保持原值。"""
    locator = _locator(11)
    _register(tenant_id, locator)
    task = _create_started(tenant_id, [locator])
    batch = _make_batch(tenant_id, task['id'], locator, end=100,
                        messages=[_peer(99)], runner_id=None)
    runner_id = _insert_runner(tenant_id, batch['web_session_id'], status='interrupted',
                               attempt=5)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        proxy_tasks.transition_batch_in_tx(cursor, tenant_id, batch['id'], 'running',
                                           runner_id=runner_id)
        conn.commit()
    assert _adjudicate(tenant_id, proxy_tasks.get_batch(tenant_id, batch['id']))['action'] == 'retry'
    assert _batch_row(tenant_id, batch['id'])['credit_cost'] == 0


def test_runner_credit_cost_pure_extraction():
    """非数值/缺字段返回 None；布尔与字符串不当作积分。"""
    assert proxy_settlement._runner_credit_cost({'credit_cost': 1.5}) == 1.5
    assert proxy_settlement._runner_credit_cost({'credit_cost': 2}) == 2.0
    assert proxy_settlement._runner_credit_cost({'credit_cost': True}) is None
    assert proxy_settlement._runner_credit_cost({'credit_cost': '0.5'}) is None
    assert proxy_settlement._runner_credit_cost({'output': 'x'}) is None
    assert proxy_settlement._runner_credit_cost(None) is None
