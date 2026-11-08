"""代理任务监控循环测试（隔离库＋假 Runtime 回执）：位置三事实、队列占用与
单未决批不变式、重启恢复、停止语义、失败分流。受信调用沿 P4 API 测试的 mock 缝
（proxy_api._read_sessions_call / _task_device）。"""

import base64
import json
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture(autouse=True)
def _require_db(db_ready):
    yield


from src.db.database import get_db_connection
from src.wecom_conversation import archive, proxy_api, proxy_monitor, proxy_tasks
from src.wecom_conversation.service import ConversationServiceError

DEVICE = '11111111-1111-1111-1111-111111111111'
ACCOUNT = 'acc1'
USER = 'user-1'


# ==================== 构造工具 ====================

def _locator(number, account=ACCOUNT):
    value = {'account': account, 'conversation_id': f'R:group-{number}'}
    return 'ws1.' + base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')


def _register(tenant_id, locator, device=DEVICE):
    with get_db_connection() as conn:
        row, _ = archive.register_scope(conn, tenant_id, USER, locator,
                                        name='售前群', kind='group', device_id=device,
                                        reason='manual_add')
        conn.commit()
    return row


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


def _peer(msg_id, text='在吗', **overrides):
    message = {'msg_id': msg_id, 'side': 'peer', 'system': False, 'text': text,
               'sender_id': 'customer-1', 'sender_name': '客户',
               'sender_platform': 'wecom', 'send_time': 1760000000,
               'mentions': [], 'mentions_complete': True}
    message.update(overrides)
    return message


def _self(msg_id, **overrides):
    return _peer(msg_id, side='self', **overrides)


def _system_msg(msg_id):
    return _peer(msg_id, system=True)


def _page(locator, messages, *, cursor='wb1.next', generation='gen-2',
          has_more=False, failed=None):
    return {'self_user_id': ACCOUNT, 'source_generation': generation, 'cursor': cursor,
            'has_more': has_more, 'failed': list(failed or []),
            'sessions': [{'session': locator, 'messages': list(messages)}]}


def _err_stub(message, code):
    return {'code': code, 'message': message}


async def _tick(read_pages):
    """跑一轮监控；read_pages 为 _read_sessions_call 的返回序列 [(data, error)]。"""
    with patch.object(proxy_api, '_task_device', AsyncMock(
            return_value=({'id': DEVICE}, None))), \
         patch.object(proxy_api, '_read_sessions_call',
                      AsyncMock(side_effect=read_pages)) as read:
        await proxy_monitor.run_tick()
    return read


def _make_batch(tenant_id, task_id, session, *, web_session_id='wecom_proxy_x'):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        batch = proxy_tasks.create_batch_in_tx(
            cursor, tenant_id, USER, task_id=task_id, session=session,
            batch_end_msg_id=100, input_snapshot=None, agent_id='agent-a',
            instruction_snapshot='I', web_session_id=web_session_id)
        conn.commit()
    return batch


def _task_row(tenant_id, task_id):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM bs_wecom_proxy_tasks WHERE id=%s AND tenant_id=%s',
                       (task_id, tenant_id))
        return dict(cursor.fetchone())


def _target_rows(tenant_id, task_id):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''SELECT * FROM bs_wecom_proxy_task_sessions
                          WHERE tenant_id=%s AND task_id=%s ORDER BY id''',
                       (tenant_id, task_id))
        return [dict(row) for row in cursor.fetchall()]


def _target(tenant_id, task_id, locator):
    return [item for item in _target_rows(tenant_id, task_id)
            if item['session'] == locator][0]


def _batch_rows(tenant_id, task_id):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''SELECT * FROM bs_wecom_proxy_batches
                          WHERE tenant_id=%s AND task_id=%s ORDER BY id''',
                       (tenant_id, task_id))
        return [dict(row) for row in cursor.fetchall()]


# ==================== 1. 位置三事实与候选消费 ====================

@pytest.mark.asyncio
async def test_candidates_persist_batch_and_advance_cursor(tenant_id):
    """候选持久化：批次冻结上界＋快照原文身份；wb1_cursor 推进、processed_position 不动。"""
    _register(tenant_id, _locator(1))
    task = _create_started(tenant_id, [_locator(1)])
    locator = _locator(1)
    read = await _tick([(_page(locator, [_peer(101), _self(107), _peer(105)],
                               cursor='wb1.pos-1'), None)])
    read.assert_awaited_once()
    batches = _batch_rows(tenant_id, task['id'])
    assert len(batches) == 1 and batches[0]['status'] == 'pending'
    assert batches[0]['batch_end_msg_id'] == 105  # 本批候选最大 msg_id（≤ 读取位置 107）
    snapshot = batches[0]['input_snapshot']
    assert snapshot['schema_version'] == 1 and snapshot['session'] == locator
    assert [item['msg_id'] for item in snapshot['messages']] == [101, 105]  # 自己 107 不入批
    assert snapshot['messages'][0]['sender_id'] == 'customer-1'  # 原文与身份入快照
    assert snapshot['messages'][0]['text'] == '在吗'
    target = _target(tenant_id, task['id'], locator)
    assert target['wb1_cursor'] == 'wb1.pos-1'  # 推进到读取位置（下次监控读起点）
    assert target['processed_position'] == 100  # 保持（推进语义属 P6 核定）
    assert target['latest_seen_msg_id'] == 107  # 本轮最大 msg_id（含自己消息）
    assert target['source_generation'] == 'gen-2'


@pytest.mark.asyncio
async def test_no_candidates_creates_zero_batch(tenant_id):
    """无候选（自己/系统消息）：零批次，只更新 latest_seen 与读取游标。"""
    _register(tenant_id, _locator(1))
    task = _create_started(tenant_id, [_locator(1)])
    locator = _locator(1)
    await _tick([(_page(locator, [_self(103), _system_msg(104)],
                        cursor='wb1.pos-2'), None)])
    assert _batch_rows(tenant_id, task['id']) == []
    target = _target(tenant_id, task['id'], locator)
    assert target['wb1_cursor'] == 'wb1.pos-2'
    assert target['latest_seen_msg_id'] == 104
    assert target['processed_position'] == 100


@pytest.mark.asyncio
async def test_peer_messages_at_or_below_processed_position_not_candidates(tenant_id):
    """msg_id ≤ processed_position 的 peer 消息不重复激活（去重幂等）。"""
    _register(tenant_id, _locator(1))
    task = _create_started(tenant_id, [_locator(1)])
    await _tick([(_page(_locator(1), [_peer(99), _peer(100)],
                        cursor='wb1.pos-3'), None)])
    assert _batch_rows(tenant_id, task['id']) == []


@pytest.mark.asyncio
async def test_multi_page_drain_merges_single_batch(tenant_id):
    """has_more 有界排空：多页候选合并一个批次，游标取末页读取位置。"""
    _register(tenant_id, _locator(1))
    task = _create_started(tenant_id, [_locator(1)])
    locator = _locator(1)
    page1 = _page(locator, [_peer(101)], cursor='wb1.mid', has_more=True)
    page2 = _page(locator, [_peer(105), _self(106)], cursor='wb1.tail')
    await _tick([(page1, None), (page2, None)])
    batches = _batch_rows(tenant_id, task['id'])
    assert len(batches) == 1
    assert batches[0]['batch_end_msg_id'] == 105
    assert [item['msg_id'] for item in batches[0]['input_snapshot']['messages']] == [101, 105]
    target = _target(tenant_id, task['id'], locator)
    assert target['wb1_cursor'] == 'wb1.tail'
    assert target['latest_seen_msg_id'] == 106


# ==================== 2. 竞争：单未决批不变式与队列占用 ====================

@pytest.mark.asyncio
async def test_same_session_single_undecided_batch_invariant(tenant_id):
    """同会话单未决批不变式：重复/并发触发下不双建（部分唯一索引判出）。"""
    _register(tenant_id, _locator(1))
    task = _create_started(tenant_id, [_locator(1)])
    locator = _locator(1)
    _make_batch(tenant_id, task['id'], locator)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        with pytest.raises(ConversationServiceError) as exc:
            proxy_tasks.create_batch_in_tx(
                cursor, tenant_id, USER, task_id=task['id'], session=locator,
                batch_end_msg_id=105, input_snapshot=None, agent_id='agent-a',
                instruction_snapshot='I', web_session_id='wecom_proxy_x')
        assert exc.value.code == 'PROXY_BATCH_OCCUPIED'
    assert len(_batch_rows(tenant_id, task['id'])) == 1


@pytest.mark.asyncio
async def test_running_batch_blocks_whole_group_read(tenant_id):
    """A 会话有 running 批（Runner 执行中）：整组本轮零读取（设计 §9.3 队列占用
    覆盖监控读与回答两阶段），B 会话也不读，Runner 结束或进入等待后再补读。"""
    _register(tenant_id, _locator(1))
    _register(tenant_id, _locator(2))
    task = _create_started(tenant_id, [_locator(1), _locator(2)])
    locator1, locator2 = _locator(1), _locator(2)
    batch = _make_batch(tenant_id, task['id'], locator1)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        proxy_tasks.transition_batch_in_tx(cursor, tenant_id, batch['id'], 'running')
        conn.commit()
    read = await _tick([(_page(locator2, [_peer(201)], cursor='wb1.p2'), None)])
    read.assert_not_awaited()  # 组级占用：B 会话也不读
    batches = _batch_rows(tenant_id, task['id'])
    assert len(batches) == 1 and batches[0]['status'] == 'running'  # 不新建批


@pytest.mark.asyncio
async def test_session_level_occupation_skips_only_that_session(tenant_id):
    """waiting（Runner 已释放）不阻塞组级读取：A 会话不读，B 会话正常读建批。"""
    _register(tenant_id, _locator(1))
    _register(tenant_id, _locator(2))
    task = _create_started(tenant_id, [_locator(1), _locator(2)])
    locator1, locator2 = _locator(1), _locator(2)
    batch = _make_batch(tenant_id, task['id'], locator1)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        proxy_tasks.transition_batch_in_tx(cursor, tenant_id, batch['id'], 'running')
        proxy_tasks.transition_batch_in_tx(cursor, tenant_id, batch['id'], 'waiting')
        conn.commit()
    read = await _tick([(_page(locator2, [_peer(201)], cursor='wb1.p2'), None)])
    assert read.await_count == 1  # 组未被 running 批阻塞
    assert read.await_args.args[3]['sessions'] == [locator2]  # 仅占用会话跳过
    batches = _batch_rows(tenant_id, task['id'])
    assert len([item for item in batches if item['session'] == locator1]) == 1  # A 不重读重建
    # （tick 内恢复机制把 waiting 重排为 pending 属设计行为；A 的会话仍被该批占用）
    assert [item for item in batches
            if item['session'] == locator2][0]['batch_end_msg_id'] == 201


@pytest.mark.asyncio
async def test_waiting_and_reconcile_hold_occupy_sessions(tenant_id):
    """waiting/reconcile_hold/pending 同样占用：本轮不读（推进属 P6/人工）。"""
    _register(tenant_id, _locator(1))
    _register(tenant_id, _locator(2))
    _register(tenant_id, _locator(3))
    task = _create_started(tenant_id, [_locator(1), _locator(2), _locator(3)])
    statuses = {'pending': _locator(1), 'waiting': _locator(2),
                'reconcile_hold': _locator(3)}
    for status, locator in statuses.items():
        batch = _make_batch(tenant_id, task['id'], locator)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            if status != 'pending':
                proxy_tasks.transition_batch_in_tx(cursor, tenant_id, batch['id'], 'running')
                proxy_tasks.transition_batch_in_tx(cursor, tenant_id, batch['id'], status)
            conn.commit()
    read = await _tick([(_page(locator, [_peer(101)]), None) for locator in statuses.values()])
    read.assert_not_awaited()
    assert len(_batch_rows(tenant_id, task['id'])) == 3


@pytest.mark.asyncio
async def test_cross_task_occupation_skips_read(tenant_id):
    """同设备其他任务对同会话的占用同样跳过（同账号同稳定会话单运行归属）。"""
    locator = _locator(1)
    _register(tenant_id, locator)
    other = _create_started(tenant_id, [locator])
    _make_batch(tenant_id, other['id'], locator, web_session_id='wecom_proxy_a')
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # 模拟任务 A 已停止但遗留 pending 批（跨任务占用场景）
        cursor.execute("UPDATE bs_wecom_proxy_tasks SET status='stopped' WHERE id=%s",
                       (other['id'],))
        conn.commit()
    task_b = _create_started(tenant_id, [locator])  # A 非 running：B 可启动
    read = await _tick([(_page(locator, [_peer(101)], cursor='wb1.x'), None)])
    read.assert_not_awaited()
    assert _batch_rows(tenant_id, task_b['id']) == []


@pytest.mark.asyncio
async def test_multi_task_multi_target_single_loop_serial(tenant_id):
    """同设备多任务多目标：单循环串行处理，各目标独立游标各自建批。"""
    _register(tenant_id, _locator(1))
    _register(tenant_id, _locator(2))
    _register(tenant_id, _locator(3))
    task_a = _create_started(tenant_id, [_locator(1)])
    task_b = _create_started(tenant_id, [_locator(2), _locator(3)])
    read = await _tick([
        (_page(_locator(1), [_peer(101)], cursor='wb1.a'), None),
        (_page(_locator(2), [_peer(201)], cursor='wb1.b'), None),
        (_page(_locator(3), [_peer(301)], cursor='wb1.c'), None),
    ])
    assert read.await_count == 3
    assert [item.args[3]['sessions'][0] for item in read.await_args_list] == [
        _locator(1), _locator(2), _locator(3)]  # 按任务/目标顺序串行
    assert len(_batch_rows(tenant_id, task_a['id'])) == 1
    assert len(_batch_rows(tenant_id, task_b['id'])) == 2


@pytest.mark.asyncio
async def test_failed_batch_occupies_session_until_p6_resolution(tenant_id):
    """终态失败批占用会话：监控不另建新批（重试一次属 P6 核定路径，契约 §9.3）。"""
    _register(tenant_id, _locator(1))
    task = _create_started(tenant_id, [_locator(1)])
    locator = _locator(1)
    batch = _make_batch(tenant_id, task['id'], locator)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        proxy_tasks.transition_batch_in_tx(cursor, tenant_id, batch['id'], 'running')
        proxy_tasks.transition_batch_in_tx(cursor, tenant_id, batch['id'], 'failed',
                                           cli_error='SEND_FAILED')
        conn.commit()
    read = await _tick([(_page(locator, [_peer(101)], cursor='wb1.z'), None)])
    read.assert_not_awaited()
    assert len(_batch_rows(tenant_id, task['id'])) == 1  # 不为同消息范围另建新批


# ==================== 3. 重启：从持久事实继续 ====================

@pytest.mark.asyncio
async def test_restart_continues_from_persisted_facts(tenant_id):
    """循环重启后从持久事实继续：不重复建批、游标不回退、P6 推进后继续下批。"""
    _register(tenant_id, _locator(1))
    task = _create_started(tenant_id, [_locator(1)])
    locator = _locator(1)
    await _tick([(_page(locator, [_peer(101)], cursor='wb1.r1'), None)])
    batches = _batch_rows(tenant_id, task['id'])
    assert len(batches) == 1 and batches[0]['status'] == 'pending'
    # 服务端重启（新 tick）：pending 批占用会话 → 不重读不重建、游标不回退
    read = await _tick([(_page(locator, [_peer(101)], cursor='wb1.r2'), None)])
    read.assert_not_awaited()
    assert _target(tenant_id, task['id'], locator)['wb1_cursor'] == 'wb1.r1'
    assert len(_batch_rows(tenant_id, task['id'])) == 1
    # P6 模拟核定推进：批次成功＋processed_position 前移到冻结上界
    with get_db_connection() as conn:
        cursor = conn.cursor()
        proxy_tasks.transition_batch_in_tx(cursor, tenant_id, batches[0]['id'], 'running')
        proxy_tasks.transition_batch_in_tx(cursor, tenant_id, batches[0]['id'], 'succeeded')
        cursor.execute('''UPDATE bs_wecom_proxy_task_sessions SET processed_position=101
                          WHERE tenant_id=%s AND task_id=%s''', (tenant_id, task['id']))
        conn.commit()
    await _tick([(_page(locator, [_peer(102)], cursor='wb1.r3'), None)])
    batches = _batch_rows(tenant_id, task['id'])
    assert len(batches) == 2
    assert batches[1]['batch_end_msg_id'] == 102  # 下批从 101 之后，不重复处理 101
    assert [item['msg_id'] for item in batches[1]['input_snapshot']['messages']] == [102]


@pytest.mark.asyncio
async def test_halted_target_stays_halted_across_ticks(tenant_id):
    """halted 会话保持 halted：后续 tick 不读不自动恢复（待人工处置）。"""
    _register(tenant_id, _locator(1))
    _register(tenant_id, _locator(2))
    task = _create_started(tenant_id, [_locator(1), _locator(2)])
    await _tick([(_page(_locator(1), [], failed=[{'code': 'TARGET_NOT_FOUND'}]), None),
                 (_page(_locator(2), [], cursor='wb1.h2'), None)])
    assert _target(tenant_id, task['id'], _locator(1))['status'] == 'halted'
    read = await _tick([(_page(_locator(2), [], cursor='wb1.h3'), None)])
    assert [item.args[3]['sessions'][0] for item in read.await_args_list] == [_locator(2)]
    assert _target(tenant_id, task['id'], _locator(1))['status'] == 'halted'


# ==================== 4. 停止语义 ====================

@pytest.mark.asyncio
async def test_stop_requested_skips_reads_and_settles_when_clear(tenant_id):
    """stop_requested：不读不建新批；组内无未决（执行）批收敛落 stopped。"""
    _register(tenant_id, _locator(1))
    task = _create_started(tenant_id, [_locator(1)])
    locator = _locator(1)
    batch = _make_batch(tenant_id, task['id'], locator)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        proxy_tasks.transition_batch_in_tx(cursor, tenant_id, batch['id'], 'running')
        conn.commit()
    proxy_tasks.request_stop(tenant_id, USER, task['id'])  # running 批在：不收敛
    assert _task_row(tenant_id, task['id'])['status'] == 'running'
    read = await _tick([(_page(locator, [_peer(101)], cursor='wb1.s1'), None)])
    read.assert_not_awaited()  # stop_requested 不读不建新批
    assert len(_batch_rows(tenant_id, task['id'])) == 1
    # 当前批结束 → tick 收敛落 stopped（契约 §4：当前批结束后落 stopped）
    with get_db_connection() as conn:
        cursor = conn.cursor()
        proxy_tasks.transition_batch_in_tx(cursor, tenant_id, batch['id'], 'succeeded')
        conn.commit()
    await _tick([])
    row = _task_row(tenant_id, task['id'])
    assert row['status'] == 'stopped' and row['stop_requested'] is True  # 留痕保留


@pytest.mark.asyncio
async def test_waiting_batch_not_requeued_while_stopped_then_requeued_after_restart(tenant_id):
    """等待批：停止期间不恢复；重新启动后恢复重排（waiting→pending 回队列）。"""
    _register(tenant_id, _locator(1))
    task = _create_started(tenant_id, [_locator(1)])
    locator = _locator(1)
    batch = _make_batch(tenant_id, task['id'], locator)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        proxy_tasks.transition_batch_in_tx(cursor, tenant_id, batch['id'], 'running')
        proxy_tasks.transition_batch_in_tx(cursor, tenant_id, batch['id'], 'waiting')
        conn.commit()
    stopped = proxy_tasks.request_stop(tenant_id, USER, task['id'])  # 批已 waiting：收敛
    assert stopped['status'] == 'stopped'
    await _tick([])  # 停止期间：等待批不恢复
    assert _batch_rows(tenant_id, task['id'])[0]['status'] == 'waiting'
    # 重新启动（基线仍在，complete_start 直接落位；start 清零 stop_requested）
    proxy_tasks.complete_start(tenant_id, USER, task['id'], device_id=DEVICE,
                               self_user_id=ACCOUNT, baselines={})
    read = await _tick([(_page(locator, [_peer(101)], cursor='wb1.w1'), None)])
    assert _batch_rows(tenant_id, task['id'])[0]['status'] == 'pending'  # 恢复重排
    read.assert_not_awaited()  # 重排后仍占用会话：本轮不读（派发属 P6）


@pytest.mark.asyncio
async def test_terminal_failed_batch_not_requeued_by_monitor(tenant_id):
    """终态失败批不被监控/恢复路径重排（failed→pending 仅 P6 零发送重试一次）。"""
    _register(tenant_id, _locator(1))
    task = _create_started(tenant_id, [_locator(1)])
    batch = _make_batch(tenant_id, task['id'], _locator(1))
    with get_db_connection() as conn:
        cursor = conn.cursor()
        proxy_tasks.transition_batch_in_tx(cursor, tenant_id, batch['id'], 'running')
        proxy_tasks.transition_batch_in_tx(cursor, tenant_id, batch['id'], 'failed')
        conn.commit()
    await _tick([])
    assert _batch_rows(tenant_id, task['id'])[0]['status'] == 'failed'


# ==================== 5. 失败路径分流 ====================

@pytest.mark.asyncio
async def test_target_failed_halts_session_and_continues_others(tenant_id):
    """单目标 failed → halted＋CLI 失败码原因，其他目标继续（不重试到成功）。"""
    _register(tenant_id, _locator(1))
    _register(tenant_id, _locator(2))
    task = _create_started(tenant_id, [_locator(1), _locator(2)])
    locator1, locator2 = _locator(1), _locator(2)
    await _tick([
        (_page(locator1, [], failed=[{'code': 'SOURCE_GENERATION_CHANGED'}]), None),
        (_page(locator2, [_peer(201)], cursor='wb1.f2'), None),
    ])
    halted = _target(tenant_id, task['id'], locator1)
    assert halted['status'] == 'halted'
    assert halted['halted_reason'] == 'SOURCE_GENERATION_CHANGED'
    assert halted['halted_at'] is not None
    assert _target(tenant_id, task['id'], locator2)['status'] == 'active'
    batches = _batch_rows(tenant_id, task['id'])
    assert len(batches) == 1 and batches[0]['session'] == locator2


@pytest.mark.asyncio
async def test_offline_skips_group_without_failing_task(tenant_id):
    """Runtime 离线：本轮跳过并记日志，不读不失败任务、位置不动。"""
    _register(tenant_id, _locator(1))
    task = _create_started(tenant_id, [_locator(1)])
    with patch.object(proxy_api, '_task_device', AsyncMock(return_value=(
            None, _err_stub('设备离线', 'DEVICE_OFFLINE')))), \
         patch.object(proxy_api, '_read_sessions_call', AsyncMock()) as read:
        await proxy_monitor.run_tick()
    read.assert_not_awaited()
    assert _task_row(tenant_id, task['id'])['status'] == 'running'
    target = _target(tenant_id, task['id'], _locator(1))
    assert target['status'] == 'active' and target['wb1_cursor'] == 'wb1.base-0'
    assert _batch_rows(tenant_id, task['id']) == []


@pytest.mark.asyncio
async def test_busy_skips_target_without_halt(tenant_id):
    """外部 BUSY：等待重试（本轮跳过），不算失败不 halt、游标不动。"""
    _register(tenant_id, _locator(1))
    task = _create_started(tenant_id, [_locator(1)])
    await _tick([(None, _err_stub('企微忙碌', 'BUSY'))])
    target = _target(tenant_id, task['id'], _locator(1))
    assert target['status'] == 'active' and target['halted_reason'] is None
    assert target['wb1_cursor'] == 'wb1.base-0'
    assert _batch_rows(tenant_id, task['id']) == []


@pytest.mark.asyncio
async def test_identity_unconfirmed_skips_and_auto_resumes(tenant_id):
    """切号（IDENTITY_UNCONFIRMED）：瞬态跳过——active 保持、游标保留；原账号重新
    登录后下一轮成功读自动继续（§9.3：中断且不改绑定）。"""
    _register(tenant_id, _locator(1))
    task = _create_started(tenant_id, [_locator(1)])
    locator = _locator(1)
    await _tick([(None, _err_stub('账号已切换', 'IDENTITY_UNCONFIRMED'))])
    target = _target(tenant_id, task['id'], locator)
    assert target['status'] == 'active' and target['halted_reason'] is None
    assert target['wb1_cursor'] == 'wb1.base-0'  # 游标与位置保留
    assert target['halted_at'] is None
    assert _batch_rows(tenant_id, task['id']) == []
    read = await _tick([(_page(locator, [_peer(101)], cursor='wb1.i1'), None)])
    read.assert_awaited_once()  # 切回后自动继续
    assert len(_batch_rows(tenant_id, task['id'])) == 1
    assert _target(tenant_id, task['id'], locator)['status'] == 'active'


@pytest.mark.asyncio
async def test_identity_unconfirmed_via_failed_array_skips(tenant_id):
    """failed 数组路径透传 IDENTITY_UNCONFIRMED：同样走瞬态集，不 halt。"""
    _register(tenant_id, _locator(1))
    task = _create_started(tenant_id, [_locator(1)])
    await _tick([(_page(_locator(1), [], failed=[{'code': 'IDENTITY_UNCONFIRMED'}]), None)])
    target = _target(tenant_id, task['id'], _locator(1))
    assert target['status'] == 'active' and target['halted_reason'] is None
    assert target['wb1_cursor'] == 'wb1.base-0'
    assert _batch_rows(tenant_id, task['id']) == []


@pytest.mark.asyncio
async def test_capability_missing_skips_without_halt(tenant_id):
    """PROXY_CAPABILITY_MISSING（invocation 级重映射码）：跳过不 halt，升级后恢复。"""
    _register(tenant_id, _locator(1))
    task = _create_started(tenant_id, [_locator(1)])
    await _tick([(None, _err_stub('缺少监控读取能力', 'PROXY_CAPABILITY_MISSING'))])
    target = _target(tenant_id, task['id'], _locator(1))
    assert target['status'] == 'active' and target['halted_reason'] is None
    assert target['wb1_cursor'] == 'wb1.base-0'
    assert _batch_rows(tenant_id, task['id']) == []


@pytest.mark.asyncio
async def test_invocation_read_failure_halts_session(tenant_id):
    """源库代际/游标失效等读失败：halted 该会话并显示原因，其他会话继续。"""
    _register(tenant_id, _locator(1))
    task = _create_started(tenant_id, [_locator(1)])
    await _tick([(None, _err_stub('游标已失效', 'CURSOR_INVALID'))])
    target = _target(tenant_id, task['id'], _locator(1))
    assert target['status'] == 'halted'
    assert target['halted_reason'] == 'CURSOR_INVALID'
    assert _batch_rows(tenant_id, task['id']) == []


@pytest.mark.asyncio
async def test_backlog_too_large_halts_session(tenant_id):
    """20 页排空后仍 has_more=true → PROXY_BACKLOG_TOO_LARGE，不静默截断不建批。"""
    _register(tenant_id, _locator(1))
    task = _create_started(tenant_id, [_locator(1)])
    locator = _locator(1)
    pages = [(_page(locator, [_peer(101)], cursor=f'wb1.p{index}', has_more=True), None)
             for index in range(proxy_api.READ_MAX_PAGES)]
    await _tick(pages)
    target = _target(tenant_id, task['id'], locator)
    assert target['status'] == 'halted'
    assert target['halted_reason'] == 'PROXY_BACKLOG_TOO_LARGE'
    assert _batch_rows(tenant_id, task['id']) == []
