"""代理任务服务层 SQL 测试（隔离库）：创建/基线/状态机/并发归属/批次转换。"""

import base64
import json

import pytest


@pytest.fixture(autouse=True)
def _require_db(db_ready):
    yield


from src.db.database import get_db_connection
from src.wecom_conversation import archive, proxy_tasks
from src.wecom_conversation.service import ConversationServiceError

DEVICE = '11111111-1111-1111-1111-111111111111'
ACCOUNT = 'acc1'
USER = 'user-1'


def _locator(number, account=ACCOUNT):
    value = {'account': account, 'conversation_id': f'R:group-{number}'}
    return 'ws1.' + base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')


def _register(tenant_id, locator, name='售前群', device=DEVICE):
    with get_db_connection() as conn:
        row, _ = archive.register_scope(conn, tenant_id, USER, locator,
                                        name=name, kind='group', device_id=device,
                                        reason='manual_add')
        conn.commit()
    return row


def _create(tenant_id, locators, *, account=ACCOUNT, device=DEVICE, agent_id='agent-a'):
    return proxy_tasks.create_task(
        tenant_id, USER, device_id=device, account=account, self_user_id=account,
        title='售前群代理', agent_id=agent_id, instruction='有客户提问时回答',
        sessions=locators)


def _baseline(watermark=100, cursor='wb1.abc', generation='gen-1'):
    return {'cursor': cursor, 'generation': generation, 'watermark': watermark}


def _code(exc):
    return exc.code


# ==================== 创建 ====================

def test_create_task_persists_stopped_with_active_targets(tenant_id):
    _register(tenant_id, _locator(1))
    _register(tenant_id, _locator(2))
    task = _create(tenant_id, [_locator(1), _locator(2)])
    assert task['status'] == 'stopped' and task['stop_requested'] is False
    assert task['revision'] == 0 and task['deleted_at'] is None
    loaded = proxy_tasks.get_task(tenant_id, USER, task['id'])
    assert [item['session'] for item in loaded['targets']] == [_locator(1), _locator(2)]
    assert all(item['status'] == 'active' and item['wb1_cursor'] is None
               and item['baseline_origin'] is None and item['web_session_id'] is None
               for item in loaded['targets'])
    # P7 评审补充：新建未读取 → last_checked_at 为 NULL（列表最近检查显示"无"）
    assert all(item['last_checked_at'] is None for item in loaded['targets'])


def test_create_rejects_session_of_other_account_or_unregistered(tenant_id):
    _register(tenant_id, _locator(1))
    with pytest.raises(ConversationServiceError) as exc:
        _create(tenant_id, [_locator(9, account='other-acc')])
    assert _code(exc.value) == 'PROXY_SESSION_INVALID'
    with pytest.raises(ConversationServiceError) as exc:
        _create(tenant_id, [_locator(1), _locator(404)])
    assert _code(exc.value) == 'PROXY_SESSION_INVALID'
    assert proxy_tasks.list_tasks(tenant_id, USER) == []  # 全部被拒，未落半行


def test_create_rejects_account_self_mismatch(tenant_id):
    with pytest.raises(ConversationServiceError) as exc:
        proxy_tasks.create_task(
            tenant_id, USER, device_id=DEVICE, account=ACCOUNT, self_user_id='acc-x',
            title='T', agent_id='agent-a', instruction='I', sessions=[_locator(1)])
    assert _code(exc.value) == 'PROXY_ACCOUNT_MISMATCH'


# ==================== 启动基线与 web_session 登记 ====================

def test_complete_start_builds_initial_baseline_and_web_session(tenant_id):
    _register(tenant_id, _locator(1))
    _register(tenant_id, _locator(2))
    task = _create(tenant_id, [_locator(1), _locator(2)])
    started = proxy_tasks.complete_start(
        tenant_id, USER, task['id'], device_id=DEVICE, self_user_id=ACCOUNT,
        baselines={_locator(1): _baseline(100), _locator(2): _baseline(200, cursor='wb1.def')})
    assert started['status'] == 'running' and started['revision'] == 1
    loaded = proxy_tasks.get_task(tenant_id, USER, task['id'])
    by_session = {item['session']: item for item in loaded['targets']}
    first = by_session[_locator(1)]
    assert first['baseline_origin'] == 'initial' and first['wb1_cursor'] == 'wb1.abc'
    assert first['processed_position'] == 100 and first['latest_seen_msg_id'] == 100
    assert first['source_generation'] == 'gen-1'
    # P7 评审补充：基线读取是真实读取 → last_checked_at 落值；未启动任务为 NULL
    assert first['last_checked_at'] is not None
    # web_session_id 按任务＋目标固定，且复用 Web 会话存储（chat_sessions 落行）
    assert first['web_session_id'] and first['web_session_id'].startswith('wecom_proxy_')
    assert by_session[_locator(2)]['web_session_id'] != first['web_session_id']
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT metadata FROM chat_sessions WHERE session_id=%s',
                       (first['web_session_id'],))
        row = cursor.fetchone()
    assert row is not None  # 沿现有 Web 会话存储登记


def test_complete_start_verifies_account_device_and_missing_baseline(tenant_id):
    _register(tenant_id, _locator(1))
    task = _create(tenant_id, [_locator(1)])
    with pytest.raises(ConversationServiceError) as exc:
        proxy_tasks.complete_start(tenant_id, USER, task['id'], device_id=DEVICE,
                                   self_user_id='acc-other',
                                   baselines={_locator(1): _baseline()})
    assert _code(exc.value) == 'PROXY_ACCOUNT_MISMATCH'
    with pytest.raises(ConversationServiceError) as exc:
        proxy_tasks.complete_start(tenant_id, USER, task['id'], device_id='22222222-2222-2222-2222-222222222222',
                                   self_user_id=ACCOUNT, baselines={_locator(1): _baseline()})
    assert _code(exc.value) == 'PROXY_DEVICE_MISMATCH'
    with pytest.raises(ConversationServiceError) as exc:
        proxy_tasks.complete_start(tenant_id, USER, task['id'], device_id=DEVICE,
                                   self_user_id=ACCOUNT, baselines={})
    assert _code(exc.value) == 'PROXY_BASELINE_MISSING'
    loaded = proxy_tasks.get_task(tenant_id, USER, task['id'])
    assert loaded['status'] == 'stopped'  # 失败不翻转状态、不写脏数据
    assert loaded['targets'][0]['wb1_cursor'] is None


def test_complete_start_never_overwrites_existing_baseline(tenant_id):
    _register(tenant_id, _locator(1))
    task = _create(tenant_id, [_locator(1)])
    proxy_tasks.complete_start(tenant_id, USER, task['id'], device_id=DEVICE,
                               self_user_id=ACCOUNT, baselines={_locator(1): _baseline(100)})
    proxy_tasks.request_stop(tenant_id, USER, task['id'])
    # 模拟编辑加入水位：直接落 edit_join_read 基线（更完整的编辑流在编辑用例覆盖）
    edited = proxy_tasks.edit_stopped_task(
        tenant_id, USER, task['id'], remove_sessions=[_locator(1)])
    assert edited['targets'][0]['status'] == 'removed'
    proxy_tasks.edit_stopped_task(
        tenant_id, USER, task['id'], add_sessions=[_locator(1)],
        add_baselines={_locator(1): _baseline(500, cursor='wb1.join', generation='gen-9')})
    # 再次启动：编辑加入水位不得被覆盖（契约 Q3）
    proxy_tasks.complete_start(tenant_id, USER, task['id'], device_id=DEVICE,
                               self_user_id=ACCOUNT, baselines={_locator(1): _baseline(999)})
    loaded = proxy_tasks.get_task(tenant_id, USER, task['id'])
    target = loaded['targets'][0]
    assert target['baseline_origin'] == 'edit_join_read'
    assert target['wb1_cursor'] == 'wb1.join' and target['latest_seen_msg_id'] == 500
    assert target['processed_position'] == 500 and target['source_generation'] == 'gen-9'


# ==================== 停止 / 编辑 / 软删除状态机 ====================

def test_stop_sets_flag_and_settles_to_stopped_when_idle(tenant_id):
    _register(tenant_id, _locator(1))
    task = _create(tenant_id, [_locator(1)])
    with pytest.raises(ConversationServiceError) as exc:
        proxy_tasks.request_stop(tenant_id, USER, task['id'])
    assert _code(exc.value) == 'PROXY_STATE_CONFLICT'
    proxy_tasks.complete_start(tenant_id, USER, task['id'], device_id=DEVICE,
                               self_user_id=ACCOUNT, baselines={_locator(1): _baseline()})
    stopped = proxy_tasks.request_stop(tenant_id, USER, task['id'])
    assert stopped['stop_settled'] is True and stopped['status'] == 'stopped'
    assert stopped['stop_requested'] is True  # 请求留痕保留，启动时清零
    # 停止后允许编辑标题
    proxy_tasks.edit_stopped_task(tenant_id, USER, task['id'], title='改名')
    assert proxy_tasks.get_task(tenant_id, USER, task['id'])['title'] == '改名'


def test_settle_stop_waits_for_running_batch(tenant_id):
    _register(tenant_id, _locator(1))
    task = _create(tenant_id, [_locator(1)])
    proxy_tasks.complete_start(tenant_id, USER, task['id'], device_id=DEVICE,
                               self_user_id=ACCOUNT, baselines={_locator(1): _baseline()})
    with get_db_connection() as conn:
        cursor = conn.cursor()
        batch = proxy_tasks.create_batch_in_tx(
            cursor, tenant_id, USER, task_id=task['id'], session=_locator(1),
            batch_end_msg_id=100, input_snapshot={'messages': []}, agent_id='agent-a',
            instruction_snapshot='I', web_session_id='wecom_proxy_x')
        proxy_tasks.transition_batch_in_tx(cursor, tenant_id, batch['id'], 'running')
        conn.commit()
    stopped = proxy_tasks.request_stop(tenant_id, USER, task['id'])
    assert stopped['stop_settled'] is False and stopped['status'] == 'running'
    # 当前批进入等待后可收敛（契约：当前批结束或进入等待后落 stopped）
    with get_db_connection() as conn:
        cursor = conn.cursor()
        proxy_tasks.transition_batch_in_tx(cursor, tenant_id, batch['id'], 'waiting')
        task_row = {'id': task['id'], 'tenant_id': tenant_id, 'status': 'running',
                    'stop_requested': True}
        assert proxy_tasks.settle_stop_in_tx(cursor, task_row) is True
        conn.commit()
    assert proxy_tasks.get_task(tenant_id, USER, task['id'])['status'] == 'stopped'


def test_edit_running_rejected_and_add_uses_edit_join_baseline(tenant_id):
    _register(tenant_id, _locator(1))
    _register(tenant_id, _locator(2))
    task = _create(tenant_id, [_locator(1)])
    proxy_tasks.complete_start(tenant_id, USER, task['id'], device_id=DEVICE,
                               self_user_id=ACCOUNT, baselines={_locator(1): _baseline()})
    with pytest.raises(ConversationServiceError) as exc:
        proxy_tasks.edit_stopped_task(tenant_id, USER, task['id'], title='X')
    assert _code(exc.value) == 'PROXY_STATE_CONFLICT'
    with pytest.raises(ConversationServiceError) as exc:
        proxy_tasks.edit_stopped_task(tenant_id, USER, task['id'],
                                      add_sessions=[_locator(2)],
                                      add_baselines={_locator(2): _baseline()})
    assert _code(exc.value) == 'PROXY_STATE_CONFLICT'
    proxy_tasks.request_stop(tenant_id, USER, task['id'])
    edited = proxy_tasks.edit_stopped_task(
        tenant_id, USER, task['id'], add_sessions=[_locator(2)],
        add_baselines={_locator(2): _baseline(300, cursor='wb1.edit', generation='gen-3')})
    added = [item for item in edited['targets'] if item['session'] == _locator(2)][0]
    assert added['status'] == 'active' and added['baseline_origin'] == 'edit_join_read'
    assert added['wb1_cursor'] == 'wb1.edit' and added['processed_position'] == 300
    with pytest.raises(ConversationServiceError) as exc:
        proxy_tasks.edit_stopped_task(tenant_id, USER, task['id'],
                                      add_sessions=[_locator(2)],
                                      add_baselines={_locator(2): _baseline()})
    assert _code(exc.value) == 'PROXY_SESSION_INVALID'  # 已在本任务中
    with pytest.raises(ConversationServiceError) as exc:
        proxy_tasks.edit_stopped_task(tenant_id, USER, task['id'],
                                      add_sessions=[_locator(3)],
                                      add_baselines={})  # 缺基线读取结果
    assert _code(exc.value) == 'PROXY_BASELINE_MISSING'


def test_remove_target_ends_undecided_batches(tenant_id):
    """移除目标使未决批次（含 waiting）转 ended_by_removal；同会话单未决批不变式下
    pending/waiting 分属不同会话目标（P5 部分唯一索引）。"""
    _register(tenant_id, _locator(1))
    _register(tenant_id, _locator(2))
    _register(tenant_id, _locator(3))
    task = _create(tenant_id, [_locator(1), _locator(2), _locator(3)])
    proxy_tasks.complete_start(tenant_id, USER, task['id'], device_id=DEVICE,
                               self_user_id=ACCOUNT,
                               baselines={_locator(1): _baseline(), _locator(2): _baseline(),
                                          _locator(3): _baseline()})
    proxy_tasks.request_stop(tenant_id, USER, task['id'])
    with get_db_connection() as conn:
        cursor = conn.cursor()
        pending = proxy_tasks.create_batch_in_tx(
            cursor, tenant_id, USER, task_id=task['id'], session=_locator(1),
            batch_end_msg_id=100, input_snapshot=None, agent_id='agent-a',
            instruction_snapshot='I', web_session_id='wecom_proxy_1')
        waiting = proxy_tasks.create_batch_in_tx(
            cursor, tenant_id, USER, task_id=task['id'], session=_locator(2),
            batch_end_msg_id=110, input_snapshot=None, agent_id='agent-a',
            instruction_snapshot='I', web_session_id='wecom_proxy_2')
        proxy_tasks.transition_batch_in_tx(cursor, tenant_id, waiting['id'], 'running')
        proxy_tasks.transition_batch_in_tx(cursor, tenant_id, waiting['id'], 'waiting')
        succeeded = proxy_tasks.create_batch_in_tx(
            cursor, tenant_id, USER, task_id=task['id'], session=_locator(3),
            batch_end_msg_id=120, input_snapshot=None, agent_id='agent-a',
            instruction_snapshot='I', web_session_id='wecom_proxy_3')
        proxy_tasks.transition_batch_in_tx(cursor, tenant_id, succeeded['id'], 'running')
        proxy_tasks.transition_batch_in_tx(cursor, tenant_id, succeeded['id'], 'succeeded')
        conn.commit()
    edited = proxy_tasks.edit_stopped_task(tenant_id, USER, task['id'],
                                           remove_sessions=[_locator(1), _locator(2)])
    status_by_session = {item['session']: item['status'] for item in edited['targets']}
    assert status_by_session[_locator(1)] == 'removed'
    assert status_by_session[_locator(2)] == 'removed'
    detail = proxy_tasks.get_task_detail(tenant_id, USER, task['id'])
    status_by_id = {item['id']: item['status'] for item in detail['batches']}
    assert status_by_id[pending['id']] == 'ended_by_removal'
    assert status_by_id[waiting['id']] == 'ended_by_removal'  # waiting 也结束，不得再发送
    assert status_by_id[succeeded['id']] == 'succeeded'  # 终态不受影响


def test_soft_delete_only_from_stopped_and_ends_undecided_batches(tenant_id):
    _register(tenant_id, _locator(1))
    task = _create(tenant_id, [_locator(1)])
    proxy_tasks.complete_start(tenant_id, USER, task['id'], device_id=DEVICE,
                               self_user_id=ACCOUNT, baselines={_locator(1): _baseline()})
    with pytest.raises(ConversationServiceError) as exc:
        proxy_tasks.soft_delete_task(tenant_id, USER, task['id'])
    assert _code(exc.value) == 'PROXY_STATE_CONFLICT'
    with get_db_connection() as conn:
        cursor = conn.cursor()
        hold = proxy_tasks.create_batch_in_tx(
            cursor, tenant_id, USER, task_id=task['id'], session=_locator(1),
            batch_end_msg_id=100, input_snapshot=None, agent_id='agent-a',
            instruction_snapshot='I', web_session_id='wecom_proxy_1')
        proxy_tasks.transition_batch_in_tx(cursor, tenant_id, hold['id'], 'running')
        proxy_tasks.transition_batch_in_tx(cursor, tenant_id, hold['id'], 'reconcile_hold')
        conn.commit()
    proxy_tasks.request_stop(tenant_id, USER, task['id'])
    deleted = proxy_tasks.soft_delete_task(tenant_id, USER, task['id'])
    assert deleted['status'] == 'deleted' and deleted['deleted_at'] is not None
    detail = proxy_tasks.get_task_detail(tenant_id, USER, task['id'])
    assert detail['batches'][0]['status'] == 'ended_by_delete'
    with pytest.raises(ConversationServiceError) as exc:
        proxy_tasks.soft_delete_task(tenant_id, USER, task['id'])
    assert _code(exc.value) == 'PROXY_ALREADY_DELETED'
    assert proxy_tasks.list_tasks(tenant_id, USER) == []  # 默认不含已删除
    assert len(proxy_tasks.list_tasks(tenant_id, USER, include_deleted=True)) == 1  # 历史只读可查
    with pytest.raises(ConversationServiceError) as exc:
        proxy_tasks.request_stop(tenant_id, USER, task['id'])
    assert _code(exc.value) == 'PROXY_TASK_DELETED'


# ==================== 并发启动同会话唯一归属 ====================

def test_concurrent_start_same_account_session_single_owner(tenant_id):
    _register(tenant_id, _locator(1))
    _register(tenant_id, _locator(2))
    first = _create(tenant_id, [_locator(1)], agent_id='agent-a')
    second = _create(tenant_id, [_locator(1), _locator(2)], agent_id='agent-b')
    proxy_tasks.complete_start(tenant_id, USER, first['id'], device_id=DEVICE,
                               self_user_id=ACCOUNT, baselines={_locator(1): _baseline()})
    task_b, targets_b, conflicts = proxy_tasks.start_prep(tenant_id, USER, second['id'])
    assert conflicts and conflicts[0]['task_id'] == first['id']
    with pytest.raises(ConversationServiceError) as exc:
        proxy_tasks.complete_start(tenant_id, USER, second['id'], device_id=DEVICE,
                                   self_user_id=ACCOUNT,
                                   baselines={item['session']: _baseline() for item in targets_b})
    assert _code(exc.value) == 'PROXY_SESSION_OWNED'
    assert proxy_tasks.get_task(tenant_id, USER, second['id'])['status'] == 'stopped'
    # 首任务停止释放归属后可启动
    proxy_tasks.request_stop(tenant_id, USER, first['id'])
    _, _, conflicts = proxy_tasks.start_prep(tenant_id, USER, second['id'])
    assert conflicts == []


def test_start_without_active_targets_rejected(tenant_id):
    _register(tenant_id, _locator(1))
    task = _create(tenant_id, [_locator(1)])
    task_edited = proxy_tasks.edit_stopped_task(tenant_id, USER, task['id'],
                                                remove_sessions=[_locator(1)])
    assert all(item['status'] == 'removed' for item in task_edited['targets'])
    with pytest.raises(ConversationServiceError) as exc:
        proxy_tasks.start_prep(tenant_id, USER, task['id'])
    assert _code(exc.value) == 'PROXY_NO_TARGETS'


def _zero_send_halted_task(tenant_id, *, initial_position=266, end_position=269):
    locator = _locator(77)
    _register(tenant_id, locator)
    task = _create(tenant_id, [locator])
    proxy_tasks.complete_start(tenant_id, USER, task['id'], device_id=DEVICE,
                               self_user_id=ACCOUNT,
                               baselines={locator: _baseline(initial_position)})
    target = proxy_tasks.get_task(tenant_id, USER, task['id'])['targets'][0]
    snapshot = {'schema_version': 1, 'session': locator,
                'messages': [{'msg_id': end_position, 'side': 'peer', 'text': '待回复'}]}
    with get_db_connection() as conn:
        cursor = conn.cursor()
        old = proxy_tasks.create_batch_in_tx(
            cursor, tenant_id, USER, task_id=task['id'], session=locator,
            batch_end_msg_id=end_position, input_snapshot=snapshot, agent_id='agent-a',
            instruction_snapshot='旧指令', web_session_id=target['web_session_id'])
        proxy_tasks.transition_batch_in_tx(cursor, tenant_id, old['id'], 'running')
        proxy_tasks.transition_batch_in_tx(cursor, tenant_id, old['id'], 'failed',
                                           cli_error='zero_send_retry_exhausted')
        cursor.execute(
            '''UPDATE bs_wecom_proxy_task_sessions SET status='halted',
                   halted_reason='zero_send_retry_exhausted', latest_seen_msg_id=%s,
                   wb1_cursor='wb1.after-message' WHERE id=%s''',
            (end_position, target['id']))
        cursor.execute("UPDATE bs_wecom_proxy_tasks SET status='stopped', stop_requested=TRUE WHERE id=%s",
                       (task['id'],))
        conn.commit()
    return task, target, old, snapshot


def test_manual_recovery_accepts_first_message_after_empty_baseline(tenant_id):
    task, target, _, snapshot = _zero_send_halted_task(
        tenant_id, initial_position=None, end_position=1)
    proxy_tasks.request_target_recovery(tenant_id, USER, task['id'], target['id'])
    proxy_tasks.complete_start(tenant_id, USER, task['id'], device_id=DEVICE,
                               self_user_id=ACCOUNT, baselines={})
    loaded = proxy_tasks.get_task_detail(tenant_id, USER, task['id'])
    assert loaded['targets'][0]['processed_position'] is None
    assert loaded['batches'][0]['status'] == 'pending'
    assert loaded['batches'][0]['batch_end_msg_id'] == 1
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT input_snapshot FROM bs_wecom_proxy_batches WHERE id=%s',
                       (loaded['batches'][0]['id'],))
        assert cursor.fetchone()['input_snapshot'] == snapshot


def test_manual_recovery_reuses_frozen_input_only_after_successful_start(tenant_id):
    task, target, old, snapshot = _zero_send_halted_task(tenant_id)
    before = proxy_tasks.get_task(tenant_id, USER, task['id'])['targets'][0]
    with pytest.raises(ConversationServiceError) as exc:
        proxy_tasks.start_prep(tenant_id, USER, task['id'])
    assert exc.value.code == 'PROXY_NO_TARGETS'  # 普通启动不偷偷解除停住

    proxy_tasks.request_target_recovery(tenant_id, USER, task['id'], target['id'])
    marked = proxy_tasks.get_task(tenant_id, USER, task['id'])['targets'][0]
    assert marked['status'] == 'active' and marked['halted_reason'] == 'zero_send_retry_exhausted'
    assert proxy_tasks.get_task_detail(tenant_id, USER, task['id'])['batches'][0]['id'] == old['id']
    with pytest.raises(ConversationServiceError) as exc:
        proxy_tasks.request_target_recovery(tenant_id, USER, task['id'], target['id'])
    assert exc.value.code == 'PROXY_RECOVERY_UNAVAILABLE'  # 重复点击不建批
    with pytest.raises(ConversationServiceError) as exc:
        proxy_tasks.complete_start(tenant_id, USER, task['id'], device_id=DEVICE,
                                   self_user_id='wrong-account', baselines={})
    assert exc.value.code == 'PROXY_ACCOUNT_MISMATCH'
    assert len(proxy_tasks.get_task_detail(tenant_id, USER, task['id'])['batches']) == 1  # 失败无占位

    proxy_tasks.complete_start(tenant_id, USER, task['id'], device_id=DEVICE,
                               self_user_id=ACCOUNT, baselines={})
    loaded = proxy_tasks.get_task_detail(tenant_id, USER, task['id'])
    batches = loaded['batches']
    assert loaded['status'] == 'running' and len(batches) == 2
    assert batches[1]['id'] == old['id'] and batches[1]['status'] == 'failed'
    assert batches[0]['status'] == 'pending' and batches[0]['retry_count'] == 0
    assert batches[0]['batch_end_msg_id'] == 269
    assert batches[0]['web_session_id'] == before['web_session_id']
    assert batches[0]['agent_id'] == task['agent_id']
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT input_snapshot, instruction_snapshot, runner_id, submit_text '
                       'FROM bs_wecom_proxy_batches WHERE id=%s', (batches[0]['id'],))
        fresh = cursor.fetchone()
    assert fresh['input_snapshot'] == snapshot
    assert fresh['instruction_snapshot'] == task['instruction']
    assert fresh['runner_id'] is None and fresh['submit_text'] is None
    after = loaded['targets'][0]
    for key in ('wb1_cursor', 'source_generation', 'processed_position',
                'latest_seen_msg_id', 'web_session_id'):
        assert after[key] == before[key]
    assert after['status'] == 'active' and after['halted_reason'] is None


def test_manual_recovery_rejects_other_reason_and_advanced_position(tenant_id):
    task, target, old, _ = _zero_send_halted_task(tenant_id)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE bs_wecom_proxy_task_sessions SET halted_reason='UI_CHANGED' WHERE id=%s",
                       (target['id'],))
        conn.commit()
    with pytest.raises(ConversationServiceError) as exc:
        proxy_tasks.request_target_recovery(tenant_id, USER, task['id'], target['id'])
    assert exc.value.code == 'PROXY_RECOVERY_UNAVAILABLE'
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''UPDATE bs_wecom_proxy_task_sessions SET processed_position=266
                       WHERE id=%s''', (target['id'],))
        cursor.execute("UPDATE bs_wecom_proxy_batches SET cli_error='unknown' WHERE id=%s",
                       (old['id'],))
        conn.commit()
    with pytest.raises(ConversationServiceError) as exc:
        proxy_tasks.request_target_recovery(tenant_id, USER, task['id'], target['id'])
    assert exc.value.code == 'PROXY_RECOVERY_UNAVAILABLE'  # 发送结果不明不可重试
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''UPDATE bs_wecom_proxy_task_sessions SET
                       halted_reason='zero_send_retry_exhausted', processed_position=269
                       WHERE id=%s''', (target['id'],))
        conn.commit()
    with pytest.raises(ConversationServiceError) as exc:
        proxy_tasks.request_target_recovery(tenant_id, USER, task['id'], target['id'])
    assert exc.value.code == 'PROXY_RECOVERY_UNAVAILABLE'


# ==================== 批次状态迁移（P5/P6 复用） ====================

def test_batch_transition_state_machine(tenant_id):
    _register(tenant_id, _locator(1))
    _register(tenant_id, _locator(2))  # reconcile_hold 用第二会话（同会话单未决批不变式）
    task = _create(tenant_id, [_locator(1)])
    with get_db_connection() as conn:
        cursor = conn.cursor()
        batch = proxy_tasks.create_batch_in_tx(
            cursor, tenant_id, USER, task_id=task['id'], session=_locator(1),
            batch_end_msg_id=100, input_snapshot={'messages': [{'msg_id': 100}]},
            agent_id='agent-a', instruction_snapshot='I', web_session_id='wecom_proxy_1')
        conn.commit()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        row = proxy_tasks.transition_batch_in_tx(cursor, tenant_id, batch['id'], 'running',
                                                 runner_id='runner-1')
        assert row['status'] == 'running' and row['started_at'] is not None
        row = proxy_tasks.transition_batch_in_tx(cursor, tenant_id, batch['id'], 'failed',
                                                 cli_error='SEND_FAILED')
        assert row['status'] == 'failed' and row['finished_at'] is not None
        # 零发送重试一次：failed→pending 且 retry_count+1
        row = proxy_tasks.transition_batch_in_tx(cursor, tenant_id, batch['id'], 'pending',
                                                 runner_id='runner-2', retry=True)
        assert row['retry_count'] == 1 and row['runner_id'] == 'runner-2'
        conn.commit()
    # 非法迁移：pending 不允许直达 succeeded；终态不允许再迁移；reconcile_hold 不可自动恢复
    with get_db_connection() as conn:
        cursor = conn.cursor()
        with pytest.raises(ConversationServiceError) as exc:
            proxy_tasks.transition_batch_in_tx(cursor, tenant_id, batch['id'], 'succeeded')
        assert _code(exc.value) == 'PROXY_BATCH_STATE_INVALID'
        conn.rollback()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        hold = proxy_tasks.create_batch_in_tx(
            cursor, tenant_id, USER, task_id=task['id'], session=_locator(2),
            batch_end_msg_id=120, input_snapshot=None, agent_id='agent-a',
            instruction_snapshot='I', web_session_id='wecom_proxy_1')
        proxy_tasks.transition_batch_in_tx(cursor, tenant_id, hold['id'], 'running')
        proxy_tasks.transition_batch_in_tx(cursor, tenant_id, hold['id'], 'reconcile_hold')
        conn.commit()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        with pytest.raises(ConversationServiceError):
            proxy_tasks.transition_batch_in_tx(cursor, tenant_id, hold['id'], 'pending')
        conn.rollback()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        proxy_tasks.transition_batch_in_tx(cursor, tenant_id, batch['id'], 'running')
        proxy_tasks.transition_batch_in_tx(cursor, tenant_id, batch['id'], 'succeeded')
        with pytest.raises(ConversationServiceError):
            proxy_tasks.transition_batch_in_tx(cursor, tenant_id, batch['id'], 'pending')
        # 幂等：同状态重复迁移不报错
        row = proxy_tasks.transition_batch_in_tx(cursor, tenant_id, batch['id'], 'succeeded')
        assert row['status'] == 'succeeded'
        conn.rollback()


def test_mark_target_halted_keeps_reason(tenant_id):
    _register(tenant_id, _locator(1))
    task = _create(tenant_id, [_locator(1)])
    with get_db_connection() as conn:
        cursor = conn.cursor()
        assert proxy_tasks.mark_target_halted_in_tx(
            cursor, tenant_id, task['id'], _locator(1), '重试再失败') is True
        conn.commit()
    target = proxy_tasks.get_task(tenant_id, USER, task['id'])['targets'][0]
    assert target['status'] == 'halted' and target['halted_reason'] == '重试再失败'
    assert target['halted_at'] is not None


# ==================== 租户隔离 ====================

def test_tenant_isolation_hides_and_blocks_other_users(tenant_id):
    _register(tenant_id, _locator(1))
    task = _create(tenant_id, [_locator(1)])
    assert proxy_tasks.get_task(tenant_id, 'other-user', task['id']) is None
    assert proxy_tasks.get_task_detail(tenant_id, 'other-user', task['id']) is None
    assert proxy_tasks.list_tasks(tenant_id, 'other-user') == []
    with pytest.raises(ConversationServiceError) as exc:
        proxy_tasks.request_stop(tenant_id, 'other-user', task['id'])
    assert _code(exc.value) == 'PROXY_NOT_FOUND'
    with pytest.raises(ConversationServiceError) as exc:
        proxy_tasks.soft_delete_task(tenant_id, 'other-user', task['id'])
    assert _code(exc.value) == 'PROXY_NOT_FOUND'
    with pytest.raises(ConversationServiceError) as exc:
        proxy_tasks.complete_start(tenant_id, 'other-user', task['id'], device_id=DEVICE,
                                   self_user_id=ACCOUNT, baselines={})
    assert _code(exc.value) == 'PROXY_NOT_FOUND'


# ==================== 批次翻页与列表运行信息（P7 评审补充，加性） ====================

def _insert_batch(tenant_id, task_id, locator, *, end=100, cli_error=None, credit=0.0,
                  status='succeeded', created_at=None):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            '''INSERT INTO bs_wecom_proxy_batches
                   (tenant_id, user_id, task_id, session, batch_end_msg_id, agent_id,
                    instruction_snapshot, status, end_json, cli_error, credit_cost,
                    created_at, updated_at, finished_at)
               VALUES (%s,%s,%s,%s,%s,'agent-a','I',%s,NULL,%s,%s,
                       COALESCE(%s, CURRENT_TIMESTAMP), COALESCE(%s, CURRENT_TIMESTAMP),
                       COALESCE(%s, CURRENT_TIMESTAMP)) RETURNING id''',
            (tenant_id, USER, task_id, locator, end, status, cli_error, credit,
             created_at, created_at, created_at))
        batch_id = cursor.fetchone()['id']
        conn.commit()
    return batch_id


def test_detail_batches_pagination_has_more_and_offset(tenant_id):
    """批次分页：新批次在前；batches_has_more 按取到条数==limit 判定。"""
    _register(tenant_id, _locator(1))
    task = _create(tenant_id, [_locator(1)])
    ids = [_insert_batch(tenant_id, task['id'], _locator(1), end=100 + index)
           for index in range(3)]
    first = proxy_tasks.get_task_detail(tenant_id, USER, task['id'], batch_limit=2)
    assert [item['id'] for item in first['batches']] == list(reversed(ids))[:2]
    assert first['batches_has_more'] is True
    second = proxy_tasks.get_task_detail(tenant_id, USER, task['id'],
                                         batch_limit=2, batch_offset=2)
    assert [item['id'] for item in second['batches']] == [ids[0]]
    assert second['batches_has_more'] is False
    # 详情投影含 credit_cost 列
    assert all('credit_cost' in item for item in first['batches'])


def test_list_runtime_info_projection(tenant_id):
    """列表聚合投影：最近检查/最新消息/空闲合计/积分合计；最近错误两臂按时间取最新。"""
    _register(tenant_id, _locator(1))
    _register(tenant_id, _locator(2))
    task = _create(tenant_id, [_locator(1), _locator(2)])
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # 目标 1：活跃，最近实际读取 10:00（last_checked_at）、最新消息 120、空闲 3 轮
        cursor.execute(
            '''UPDATE bs_wecom_proxy_task_sessions SET idle_read_count=3,
                   latest_seen_msg_id=120, wb1_cursor='wb1.x',
                   last_checked_at='2026-11-08 10:00:00+08',
                   updated_at='2026-11-08 10:00:00+08'
               WHERE tenant_id=%s AND task_id=%s AND session=%s''',
            (tenant_id, task['id'], _locator(1)))
        # 目标 2：停住（09:00，含原因），空闲 2 轮
        cursor.execute(
            '''UPDATE bs_wecom_proxy_task_sessions SET idle_read_count=2,
                   status='halted', halted_reason='PROXY_READ_FAILED',
                   halted_at='2026-11-08 09:00:00+08',
                   updated_at='2026-11-08 09:00:00+08'
               WHERE tenant_id=%s AND task_id=%s AND session=%s''',
            (tenant_id, task['id'], _locator(2)))
        conn.commit()
    # 先只插入早于停住时间（08:00）的批次错误 → last_error 取 halted_reason（09:00）
    _insert_batch(tenant_id, task['id'], _locator(1), status='failed',
                  cli_error='OLD_BATCH_ERROR', credit=0.5,
                  created_at='2026-11-08 08:00:00+08')
    row = [item for item in proxy_tasks.list_tasks(tenant_id, USER)
           if item['id'] == task['id']][0]
    assert row['active_target_count'] == 1
    assert row['idle_read_count'] == 5  # 按任务全部目标 SUM（含 halted）
    assert row['latest_seen_msg_id'] == 120  # active 目标 MAX
    assert row['credit_cost'] == pytest.approx(0.5)
    assert row['last_checked_at'].isoformat().startswith('2026-11-08T10:00:00')  # active 目标 MAX(last_checked_at)
    assert row['last_error'] == 'PROXY_READ_FAILED'
    # 再插入晚于停住时间（11:00）的批次错误 → last_error 翻转为批次 cli_error
    _insert_batch(tenant_id, task['id'], _locator(1), status='failed',
                  cli_error='SEND_TIMEOUT', credit=0.1,
                  created_at='2026-11-08 11:00:00+08')
    row = [item for item in proxy_tasks.list_tasks(tenant_id, USER)
           if item['id'] == task['id']][0]
    assert row['last_error'] == 'SEND_TIMEOUT'
    assert row['credit_cost'] == pytest.approx(0.6)


def test_list_runtime_info_defaults_without_batches_or_errors(tenant_id):
    """无批次无错误的新任务：合计为 0、最近错误为 None。

    last_checked_at 聚合 MAX(target.last_checked_at)（2026-11-08 评审补充）：
    仅实际监控读取写入——新建未读取任务为 NULL（页面显示"无"），
    监控读（空闲或建批）与启动基线读取会落值。
    """
    _register(tenant_id, _locator(3))
    task = _create(tenant_id, [_locator(3)])
    row = [item for item in proxy_tasks.list_tasks(tenant_id, USER)
           if item['id'] == task['id']][0]
    assert row['idle_read_count'] == 0
    assert row['credit_cost'] == 0
    assert row['last_error'] is None
    assert row['last_checked_at'] is None  # 从未读取：不显示兜底时间
    assert row['latest_seen_msg_id'] is None
