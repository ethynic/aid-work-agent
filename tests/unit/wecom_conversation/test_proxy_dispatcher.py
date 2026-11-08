"""代理批次派发器测试（代理任务契约 §6，设计 §9.3，P6）。

派发编排（Runner 提交沿 mock 缝）：updated_at＋id 队序（P5 遗留① tiebreaker）、
同组占用互斥、派发前核对（stop_requested/目标 removed/智能体失权）、提交失败
分流（瞬时保留占用同键重试/确定性失败停住）、waiting 释放→重排→恢复原 Runner
（非新批）、中断恢复驱动与恢复耗尽、孤儿批同键重提。
"""

import base64
import json
import asyncio
import uuid
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture(autouse=True)
def _require_db(db_ready):
    yield


from src.db.database import get_db_connection
from src.services.agent_runner.contracts import RunnerError
from src.wecom_conversation import proxy_api, proxy_dispatcher, proxy_tasks
from src.wecom_conversation.proxy_dispatcher import run_tick

DEVICE = '11111111-1111-1111-1111-111111111111'
ACCOUNT = 'acc1'
USER = 'user-1'


def _locator(number, account=ACCOUNT):
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


def _web_session_id(tenant_id, task_id, session):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''SELECT web_session_id FROM bs_wecom_proxy_task_sessions
                          WHERE tenant_id=%s AND task_id=%s AND session=%s''',
                       (tenant_id, task_id, session))
        return cursor.fetchone()['web_session_id']


def _make_batch(tenant_id, task_id, session, *, end=100, runner_id=None, status='pending',
                updated_at=None, messages=None):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        batch = proxy_tasks.create_batch_in_tx(
            cursor, tenant_id, USER, task_id=task_id, session=session, batch_end_msg_id=end,
            input_snapshot={'schema_version': 1, 'session': session,
                            'source_generation': 'gen-2',
                            'messages': messages if messages is not None else [_peer(99)]},
            agent_id='agent-a', instruction_snapshot='有客户提问时回答',
            web_session_id=_web_session_id(tenant_id, task_id, session))
        if status != 'pending' or runner_id:
            proxy_tasks.transition_batch_in_tx(cursor, tenant_id, batch['id'], status,
                                               runner_id=runner_id)
        if updated_at:
            cursor.execute('''UPDATE bs_wecom_proxy_batches SET updated_at=%s
                              WHERE id=%s''', (updated_at, batch['id']))
        conn.commit()
    return proxy_tasks.get_batch(tenant_id, batch['id'])


def _batch_row(tenant_id, batch_id):
    return proxy_tasks.get_batch(tenant_id, batch_id)


_next_runner_seq = [0]


def _insert_runner(tenant_id, session, *, status='queued', attempt=1, runner_id=None,
                   resume_control_id=None):
    _next_runner_seq[0] += 1
    runner_id = runner_id or f'runner_{_next_runner_seq[0]:08d}'
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            '''INSERT INTO agent_runners
                   (runner_id, tenant_id, scope_key, session_kind, session_id, actor_kind,
                    actor_id, user_id, service_id, source, client_request_id, input_digest,
                    input, profile_id, profile_fingerprint, checkpoint, status, attempt,
                    resume_control_id, result, record_id)
               VALUES (%s,%s,'tenant:'||%s,'web',%s,'system','wecom_proxy',%s,
                       'wecom_proxy','wecom_proxy',%s,'d','{}'::jsonb,'agent-a','fp',
                       '{}'::jsonb,%s,%s,%s,'{}'::jsonb,'rec_'||%s)''',
            (runner_id, tenant_id, tenant_id, session, USER,
             f'{runner_id}:1', status, attempt, resume_control_id, str(_next_runner_seq[0])))
        conn.commit()
    return runner_id


def _runner_row(runner_id):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM agent_runners WHERE runner_id=%s', (runner_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def _task_row(tenant_id, task_id):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM bs_wecom_proxy_tasks WHERE id=%s AND tenant_id=%s',
                       (task_id, tenant_id))
        return dict(cursor.fetchone())


def _target_row(tenant_id, task_id, session):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''SELECT * FROM bs_wecom_proxy_task_sessions
                          WHERE tenant_id=%s AND task_id=%s AND session=%s''',
                       (tenant_id, task_id, session))
        return dict(cursor.fetchone())


def _runner_payload(runner_id, status='queued'):
    return {'runner_id': runner_id, 'client_request_id': 'k', 'profile_id': 'agent-a',
            'queue_order': 1, 'session': {'kind': 'web', 'session_id': 'sess'},
            'status': status, 'snapshot': {}, 'revision': 0, 'view_revision': 0,
            'control_revision': 0}


def _submit_response(runner_id):
    return {'success': True, 'created': True, 'runner': _runner_payload(runner_id)}


def _control_response(runner_id, key):
    return {'success': True,
            'control': {'control_id': 'c_' + uuid.uuid4().hex, 'runner_id': runner_id,
                        'client_request_id': key, 'status': 'accepted'},
            'runner': _runner_payload(runner_id, 'waiting')}


class _ClientStub:
    """Runner 网关客户端桩：记录请求与已受理 runner_id，按脚本应答。"""

    def __init__(self, responses=None):
        self.requests = []
        self.created = []
        self.responses = list(responses or [])

    async def request(self, method, path, *, authorization, body=None, params=None):
        self.requests.append((method, path, body))
        if self.responses:
            item = self.responses.pop(0)
            if isinstance(item, Exception):
                raise item
            return item(body)
        runner_id = 'runner_stub_' + uuid.uuid4().hex[:8]
        self.created.append(runner_id)
        return _submit_response(runner_id)


def _patches(client, *, device_ok=True, agent_ok=True, recent_page=None, recent_error=None):
    device = {'id': DEVICE, 'capabilities_json': {'capabilities': list(proxy_tasks.RUNTIME_TOOL_NAMES)}}
    device_error = None if device_ok else ({'code': 'DEVICE_OFFLINE', 'message': '离线'})
    if recent_error is not None:
        recent = AsyncMock(return_value=(None, recent_error))
    else:
        recent = AsyncMock(return_value=(recent_page if recent_page is not None else
                                         {'self_user_id': ACCOUNT, 'cursor': 'wb1.h',
                                          'has_more': False}, None))
    return (patch.object(proxy_api, '_task_device', AsyncMock(
                return_value=(device, device_error))),
            patch.object(proxy_api, '_read_sessions_call', recent),
            patch.object(proxy_dispatcher, '_agent_allowed', AsyncMock(return_value=agent_ok)),
            patch.object(proxy_dispatcher, '_proxy_client', lambda: client))


async def _tick(client, *, device_ok=True, agent_ok=True, recent_page=None, recent_error=None,
                clear_memo=True):
    """跑一轮派发；clear_memo 清近期往来备忘（用例间隔离）。"""
    if clear_memo:
        proxy_dispatcher._recent_context_memo.clear()
    device_patch, recent_patch, agent_patch, client_patch = _patches(
        client, device_ok=device_ok, agent_ok=agent_ok,
        recent_page=recent_page, recent_error=recent_error)
    with device_patch, recent_patch, agent_patch, client_patch:
        await run_tick()


# ==================== 派发编排 ====================


@pytest.mark.asyncio
async def test_dispatch_flips_batch_and_records_runner_id(tenant_id):
    """派发：pending→running＋runner_id 回写；提交体携带契约 §6 受信字段。"""
    locator = _locator(1)
    _register(tenant_id, locator)
    task = _create_started(tenant_id, [locator])
    batch = _make_batch(tenant_id, task['id'], locator)
    client = _ClientStub()
    await _tick(client)
    assert len(client.requests) == 1
    method, path, body = client.requests[0]
    assert (method, path) == ('POST', '/v1/runners')
    assert body['source'] == 'wecom_proxy'
    assert body['session'] == {'kind': 'web', 'session_id': batch['web_session_id']}
    assert body['client_request_id'] == f'{batch["id"]}:0'  # 幂等键
    assert body['profile_id'] == 'agent-a'  # 任务固定智能体
    assert body['prompt_augmentations'][0] == '有客户提问时回答'  # 冻结指令快照
    assert 'msg_id=99' in body['text'] and '在吗' in body['text']  # 本批原文入提示
    assert body['request_data']['wecom_proxy_batch']['batch_end_msg_id'] == 100
    assert body['request_data']['wecom_task_device'] == DEVICE
    row = _batch_row(tenant_id, batch['id'])
    assert row['status'] == 'running' and row['runner_id'] is not None


@pytest.mark.asyncio
async def test_dispatch_order_updated_at_then_id_tiebreaker(tenant_id):
    """队序（P5 遗留①）：同组多批按 updated_at＋id 取队首——updated_at 早者
    先派；跨组同 updated_at 时 id 小者先派。"""
    locator_a, locator_b = _locator(1), _locator(2)
    _register(tenant_id, locator_a)
    _register(tenant_id, locator_b)
    task = _create_started(tenant_id, [locator_a, locator_b])
    late = _make_batch(tenant_id, task['id'], locator_a, updated_at='2026-11-08 10:00:00')
    early = _make_batch(tenant_id, task['id'], locator_b, updated_at='2026-11-08 09:00:00')
    client = _ClientStub()
    await _tick(client)
    assert len(client.requests) == 1
    assert client.requests[0][2]['client_request_id'] == f'{early["id"]}:0'
    assert _batch_row(tenant_id, late['id'])['status'] == 'pending'
    # 补 early 的 Runner 行（避免下轮被判孤儿重提）
    _insert_runner(tenant_id, _web_session_id(tenant_id, task['id'], locator_b),
                   status='running', runner_id=client.created[0])
    # 跨组同 updated_at → id tiebreaker（同会话仅一个未决批：先终结组内旧批）
    device2 = '22222222-2222-2222-2222-222222222222'
    locator_c = _locator(3, account='acc2')
    _register(tenant_id, locator_c, device=device2)
    task2 = _create_started(tenant_id, [locator_c], account='acc2', device=device2)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''UPDATE bs_wecom_proxy_batches SET status='failed'
                          WHERE id IN (%s, %s)''', (early['id'], late['id']))
        conn.commit()
    tie1 = _make_batch(tenant_id, task['id'], locator_a, updated_at='2026-11-08 11:00:00')
    tie2 = _make_batch(tenant_id, task2['id'], locator_c, updated_at='2026-11-08 11:00:00')
    client2 = _ClientStub()
    await _tick(client2)
    assert len(client2.requests) == 2
    first_key = client2.requests[0][2]['client_request_id']
    # 同 updated_at：id 较小者先被取走
    smaller = min(tie1['id'], tie2['id'])
    assert first_key == f'{smaller}:0'


@pytest.mark.asyncio
async def test_dispatch_same_group_one_runner_per_tick(tenant_id):
    """同设备/账号组同轮只派一个 Runner（组级占用语义）。"""
    locator_a, locator_b = _locator(1), _locator(2)
    _register(tenant_id, locator_a)
    _register(tenant_id, locator_b)
    task = _create_started(tenant_id, [locator_a, locator_b])
    first = _make_batch(tenant_id, task['id'], locator_a, updated_at='2026-11-08 09:00:00')
    second = _make_batch(tenant_id, task['id'], locator_b, updated_at='2026-11-08 09:00:01')
    client = _ClientStub()
    await _tick(client)
    assert len(client.requests) == 1
    assert _batch_row(tenant_id, first['id'])['status'] == 'running'
    assert _batch_row(tenant_id, second['id'])['status'] == 'pending'


@pytest.mark.asyncio
async def test_running_batch_blocks_monitor_reads(tenant_id):
    """组级占用：running 批次使 proxy_monitor._group_runner_busy 命中（监控整组不读）。"""
    locator = _locator(1)
    _register(tenant_id, locator)
    task = _create_started(tenant_id, [locator])
    batch = _make_batch(tenant_id, task['id'], locator)
    client = _ClientStub()
    await _tick(client)
    assert _batch_row(tenant_id, batch['id'])['status'] == 'running'
    from src.wecom_conversation import proxy_monitor
    assert proxy_monitor._group_runner_busy(tenant_id, DEVICE, ACCOUNT) is True


@pytest.mark.asyncio
async def test_device_unavailable_skips_without_failure(tenant_id):
    """设备运行条件不满足：批次留在 pending，不失败任务不转嫁会话 halted。"""
    locator = _locator(1)
    _register(tenant_id, locator)
    task = _create_started(tenant_id, [locator])
    batch = _make_batch(tenant_id, task['id'], locator)
    client = _ClientStub()
    await _tick(client, device_ok=False)
    assert client.requests == []
    row = _batch_row(tenant_id, batch['id'])
    assert row['status'] == 'pending'
    assert _target_row(tenant_id, task['id'], locator)['status'] == 'active'


@pytest.mark.asyncio
async def test_stop_requested_task_not_dispatched(tenant_id):
    """stop_requested 任务的批次不派发（新批派发检查点，契约 §6）。"""
    locator = _locator(1)
    _register(tenant_id, locator)
    task = _create_started(tenant_id, [locator])
    batch = _make_batch(tenant_id, task['id'], locator)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''UPDATE bs_wecom_proxy_tasks SET stop_requested=TRUE
                          WHERE id=%s AND tenant_id=%s''', (task['id'], tenant_id))
        conn.commit()
    client = _ClientStub()
    await _tick(client)
    assert client.requests == []
    assert _batch_row(tenant_id, batch['id'])['status'] == 'pending'


@pytest.mark.asyncio
async def test_removed_target_not_dispatched(tenant_id):
    """目标 removed 的批次不派发（编辑移除未结批的防御面）。"""
    locator = _locator(1)
    _register(tenant_id, locator)
    task = _create_started(tenant_id, [locator])
    batch = _make_batch(tenant_id, task['id'], locator)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''UPDATE bs_wecom_proxy_task_sessions SET status='removed'
                          WHERE tenant_id=%s AND task_id=%s AND session=%s''',
                       (tenant_id, task['id'], locator))
        conn.commit()
    client = _ClientStub()
    await _tick(client)
    assert client.requests == []
    assert _batch_row(tenant_id, batch['id'])['status'] == 'pending'


@pytest.mark.asyncio
async def test_agent_not_allowed_fails_batch_and_stops_task(tenant_id):
    """智能体失权：批次 failed＋会话停住＋任务停止（契约 §6 失权即停任务）。"""
    locator = _locator(1)
    _register(tenant_id, locator)
    task = _create_started(tenant_id, [locator])
    batch = _make_batch(tenant_id, task['id'], locator)
    client = _ClientStub()
    await _tick(client, agent_ok=False)
    assert client.requests == []
    assert _batch_row(tenant_id, batch['id'])['status'] == 'failed'
    assert _target_row(tenant_id, task['id'], locator)['status'] == 'halted'
    assert _task_row(tenant_id, task['id'])['status'] == 'stopped'


@pytest.mark.asyncio
async def test_transient_submit_failure_keeps_occupancy_then_retries(tenant_id):
    """提交瞬时失败：批次保持 running（占用保持），下轮同键幂等重提成功。"""
    locator = _locator(1)
    _register(tenant_id, locator)
    task = _create_started(tenant_id, [locator])
    batch = _make_batch(tenant_id, task['id'], locator)
    client = _ClientStub(responses=[RunnerError('RUNNER_SERVICE_UNAVAILABLE', 503)])
    await _tick(client)
    row = _batch_row(tenant_id, batch['id'])
    assert row['status'] == 'running' and row['runner_id'] is None
    # 下轮：孤儿批同键重提（命中服务端已受理的原 Runner 或新建）
    client2 = _ClientStub(responses=[lambda body: _submit_response('runner_orphan1')])
    await _tick(client2)
    assert len(client2.requests) == 1
    assert client2.requests[0][2]['client_request_id'] == f'{batch["id"]}:0'
    row = _batch_row(tenant_id, batch['id'])
    assert row['status'] == 'running' and row['runner_id'] == 'runner_orphan1'


@pytest.mark.asyncio
async def test_deterministic_submit_rejection_fails_batch_and_halts(tenant_id):
    """确定性拒绝（非权限类）：批次 failed＋会话停住，任务保持 running。"""
    locator = _locator(1)
    _register(tenant_id, locator)
    task = _create_started(tenant_id, [locator])
    batch = _make_batch(tenant_id, task['id'], locator)
    client = _ClientStub(responses=[RunnerError('INVALID_REQUEST', 422)])
    await _tick(client)
    assert _batch_row(tenant_id, batch['id'])['status'] == 'failed'
    assert _target_row(tenant_id, task['id'], locator)['status'] == 'halted'
    assert _task_row(tenant_id, task['id'])['status'] == 'running'


@pytest.mark.asyncio
async def test_runner_terminal_adjudicates_advance(tenant_id):
    """Runner 终态核定：已完成＋无需回复 JSON → 批次 succeeded＋推进。"""
    locator = _locator(1)
    _register(tenant_id, locator)
    task = _create_started(tenant_id, [locator])
    batch = _make_batch(tenant_id, task['id'], locator)
    client = _ClientStub()
    await _tick(client)
    runner_id = _batch_row(tenant_id, batch['id'])['runner_id']
    end = {'schema_version': 1, 'batch_id': str(batch['id']), 'task_id': str(task['id']),
           'session': locator, 'batch_end_msg_id': 100, 'needs_reply': False,
           'replies': [], 'unanswered': []}
    _insert_runner(tenant_id, batch['web_session_id'], status='completed', runner_id=runner_id)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''UPDATE agent_runners SET result=%s::jsonb WHERE runner_id=%s''',
                       (json.dumps({'status': 'completed', 'output': json.dumps(end),
                                    'images': [], 'error_code': None}), runner_id))
        conn.commit()
    await _tick(_ClientStub())
    row = _batch_row(tenant_id, batch['id'])
    assert row['status'] == 'succeeded' and row['end_json']['needs_reply'] is False
    assert _target_row(tenant_id, task['id'], locator)['processed_position'] == 100


@pytest.mark.asyncio
async def test_runner_waiting_releases_batch_then_resume_original(tenant_id):
    """等待释放→重排→恢复原 Runner（非新批）：释放后经 P5 重排回 pending，
    派发器占用取得后仅发恢复控制，不重复提交新执行。"""
    locator = _locator(1)
    _register(tenant_id, locator)
    task = _create_started(tenant_id, [locator])
    batch = _make_batch(tenant_id, task['id'], locator)
    client = _ClientStub()
    await _tick(client)
    runner_id = _batch_row(tenant_id, batch['id'])['runner_id']
    _insert_runner(tenant_id, batch['web_session_id'], status='waiting', runner_id=runner_id)
    await _tick(_ClientStub())
    assert _batch_row(tenant_id, batch['id'])['status'] == 'waiting'  # 释放队列
    # P5 重排：waiting→pending（任务 running、目标 active）
    with get_db_connection() as conn:
        cursor = conn.cursor()
        requeued = proxy_tasks.requeue_waiting_batches_in_tx(cursor, tenant_id)
        conn.commit()
    assert requeued == 1
    # 派发：恢复原 Runner（POST controls），不新建执行
    resume_client = _ClientStub(responses=[
        lambda body: _control_response(runner_id, body['client_request_id'])])
    await _tick(resume_client)
    assert len(resume_client.requests) == 1
    method, path, body = resume_client.requests[0]
    assert (method, path) == ('POST', f'/v1/runners/{runner_id}/controls')
    assert body['action'] == 'resume'
    assert body['client_request_id'].startswith('wecom-proxy-wake:')
    row = _batch_row(tenant_id, batch['id'])
    assert row['status'] == 'running' and row['runner_id'] == runner_id  # 原 Runner


@pytest.mark.asyncio
async def test_runner_interrupted_resume_drive_and_exhaustion(tenant_id):
    """中断恢复驱动：attempt 未达上限发幂等 resume；达上限按事实落 reconcile_hold。"""
    locator = _locator(1)
    _register(tenant_id, locator)
    task = _create_started(tenant_id, [locator])
    batch = _make_batch(tenant_id, task['id'], locator)
    client = _ClientStub()
    await _tick(client)
    runner_id = _batch_row(tenant_id, batch['id'])['runner_id']
    _insert_runner(tenant_id, batch['web_session_id'], status='interrupted',
                   runner_id=runner_id)
    # unknown 发送事实（无法证明未发送）
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            '''INSERT INTO local_tool_invocations
                   (id, tenant_id, user_id, session_id, device_id, tool_name, arguments_json,
                    state, effect, result_json, provider_key, business_kind, business_ref)
               VALUES (%s,%s,%s,%s,%s,'wecom_message_send','{}'::jsonb,'unknown',
                       'unknown',NULL,'wecom','agent_runner',%s::jsonb)''',
            (str(uuid.uuid4()), tenant_id, USER, batch['web_session_id'], DEVICE,
             json.dumps({'runner_id': runner_id})))
        conn.commit()
    resume_client = _ClientStub(responses=[
        lambda body: _control_response(runner_id, body['client_request_id'])])
    await _tick(resume_client)
    assert resume_client.requests[0][1] == f'/v1/runners/{runner_id}/controls'
    # 恢复耗尽（attempt 达上限仍中断）→ reconcile_hold（unknown 不重发）
    from src.wecom_conversation import proxy_settlement

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''UPDATE agent_runners SET attempt=%s
                          WHERE runner_id=%s''',
                       (proxy_settlement.MAX_RESUME_ATTEMPT, runner_id))
        conn.commit()
    await _tick(_ClientStub())
    assert _batch_row(tenant_id, batch['id'])['status'] == 'reconcile_hold'


@pytest.mark.asyncio
async def test_runner_paused_with_pending_resume_control_not_released(tenant_id):
    """已有受理未消费的恢复控制（resume_control_id 非空）：不释放队列，避免
    释放→重排→同键幂等命中已消费控制的死循环。"""
    locator = _locator(1)
    _register(tenant_id, locator)
    task = _create_started(tenant_id, [locator])
    batch = _make_batch(tenant_id, task['id'], locator)
    client = _ClientStub()
    await _tick(client)
    runner_id = _batch_row(tenant_id, batch['id'])['runner_id']
    _insert_runner(tenant_id, batch['web_session_id'], status='waiting',
                   runner_id=runner_id, resume_control_id='ctrl_pending')
    await _tick(_ClientStub())
    assert _batch_row(tenant_id, batch['id'])['status'] == 'running'  # 占用保持


# ==================== 派发门智能体权限核对（契约 §6：每次企微工具派发核对） ====================

from src.local_tools.repository import DeviceOperationUnresolved
from src.services.agent_runner.wecom_device_binding import assert_proxy_dispatch_in_tx
from src.wecom_conversation import proxy_monitor


def _grant_agent(tenant_id, agent_id='agent-a', user_id=USER):
    """授予任务属主智能体权限（租户订阅＋用户级授权；users 行归属当前租户）。"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''INSERT INTO users (user_id, tenant_id, role, status)
                          VALUES (%s,%s,'user','active')
                          ON CONFLICT (user_id) DO UPDATE SET
                              tenant_id=EXCLUDED.tenant_id, role='user', status='active' ''',
                       (user_id, tenant_id))
        cursor.execute('''INSERT INTO subscriptions
                              (subscription_id, tenant_id, subagent_type, status)
                          VALUES (%s,%s,%s,'active')
                          ON CONFLICT (subscription_id) DO NOTHING''',
                       (f'sub_{tenant_id}', tenant_id, agent_id))
        cursor.execute('''INSERT INTO user_agent_permissions (user_id, agent_id, tenant_id)
                          VALUES (%s,%s,%s) ON CONFLICT DO NOTHING''',
                       (user_id, agent_id, tenant_id))
        conn.commit()


def _revoke_agent(tenant_id, agent_id='agent-a', user_id=USER):
    """撤销用户级授权（权限撤销场景；租户订阅仍在）。"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''DELETE FROM user_agent_permissions
                          WHERE user_id=%s AND agent_id=%s AND tenant_id=%s''',
                       (user_id, agent_id, tenant_id))
        conn.commit()


def _running_batch_with_runner(tenant_id, locator):
    """任务＋目标＋running 批次＋Runner 行（门禁核对的真实前置状态）。"""
    _register(tenant_id, locator)
    task = _create_started(tenant_id, [locator])
    batch = _make_batch(tenant_id, task['id'], locator, status='running',
                        runner_id='runner_gate_' + uuid.uuid4().hex[:8])
    _insert_runner(tenant_id, batch['web_session_id'], status='running',
                   runner_id=batch['runner_id'])
    return task, batch


def _gate_call(tenant_id, batch, tool, arguments):
    """真实库上的门禁调用；返回错误码（放行返回 None）。"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            assert_proxy_dispatch_in_tx(cursor,
                                        {'source': 'wecom_proxy', 'tenant_id': tenant_id,
                                         'runner_id': batch['runner_id']},
                                        tool, arguments)
            return None
        except DeviceOperationUnresolved as error:
            return error.args[0]
        finally:
            conn.rollback()


def test_gate_allows_authorized_agent_dispatch(tenant_id):
    """①智能体有权：读与发的企微工具派发放行，任务不受影响。"""
    locator = _locator(1)
    task, batch = _running_batch_with_runner(tenant_id, locator)
    _grant_agent(tenant_id)
    assert _gate_call(tenant_id, batch, 'wecom_read_session',
                      {'session': locator, 'limit': 10}) is None
    assert _gate_call(tenant_id, batch, 'wecom_message_send',
                      {'session': locator, 'text': 'hi'}) is None
    assert _task_row(tenant_id, task['id'])['stop_requested'] is False


def test_gate_rejects_revoked_agent_stops_task_no_advance(tenant_id):
    """②权限撤销后企微工具派发被拒（含只读调用），任务按失权即停收敛
    stopped，processed_position 不推进（真库断言）。"""
    locator = _locator(2)
    task, batch = _running_batch_with_runner(tenant_id, locator)
    _grant_agent(tenant_id)
    _revoke_agent(tenant_id)
    # 只读类企微调用同样被门拦截（门覆盖读调用）
    assert _gate_call(tenant_id, batch, 'wecom_read_session',
                      {'session': locator, 'limit': 10}) == 'WECOM_PROXY_AGENT_REVOKED'
    assert _gate_call(tenant_id, batch, 'wecom_message_send',
                      {'session': locator, 'text': 'x'}) == 'WECOM_PROXY_AGENT_REVOKED'
    # 失权即停：stop_requested 已置位（批次运行中保留位置，沿 P4 语义收敛）
    row = _task_row(tenant_id, task['id'])
    assert row['stop_requested'] is True and row['status'] == 'running'
    # 批次终态后沿 P5 tick 收敛函数落 stopped；批次不推进 processed_position
    with get_db_connection() as conn:
        cursor = conn.cursor()
        proxy_tasks.transition_batch_in_tx(cursor, tenant_id, batch['id'], 'failed',
                                           cli_error='WECOM_PROXY_AGENT_REVOKED')
        conn.commit()
    proxy_monitor._settle_stop_requested([_task_row(tenant_id, task['id'])])
    row = _task_row(tenant_id, task['id'])
    assert row['status'] == 'stopped' and row['stop_requested'] is True
    target = _target_row(tenant_id, task['id'], locator)
    assert target['status'] == 'active' and target['processed_position'] == 100


def test_gate_missing_user_row_fail_closed(tenant_id):
    """任务属主用户行缺失：fail-closed 拒绝派发并停任务。"""
    locator = _locator(3)
    task, batch = _running_batch_with_runner(tenant_id, locator)
    assert _gate_call(tenant_id, batch, 'wecom_read_session',
                      {'session': locator}) == 'WECOM_PROXY_AGENT_REVOKED'
    assert _task_row(tenant_id, task['id'])['stop_requested'] is True


# ==================== 评审问题 1：本批之前的近期往来 ====================


def _page(locator, messages, *, cursor='wb1.recent', has_more=False):
    return {'self_user_id': ACCOUNT, 'source_generation': 'gen-2', 'cursor': cursor,
            'has_more': has_more,
            'sessions': [{'session': locator, 'messages': list(messages)}]}


@pytest.mark.asyncio
async def test_dispatch_includes_recent_context_before_batch(tenant_id):
    """①近期前文：提交文本含"本批之前"段且只含 msg_id<本批最小值的消息——
    本批内（97/100，97＝本批最小值）与批后（101-109 场景的 105）不入前文段。"""
    locator = _locator(1)
    _register(tenant_id, locator)
    task = _create_started(tenant_id, [locator])
    batch = _make_batch(tenant_id, task['id'], locator, end=100, runner_id=None,
                        updated_at='2026-11-08 09:00:00')
    # 先终结占位批（同会话仅一个未决批），再建本批：快照消息 97/100（min=97）
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''UPDATE bs_wecom_proxy_batches SET status='failed'
                          WHERE id=%s''', (batch['id'],))
        conn.commit()
    batch = _make_batch(tenant_id, task['id'], locator, end=100,
                        updated_at='2026-11-08 09:01:00',
                        messages=[_peer(97, text='本批第一条'), _peer(100, text='本批末条')])
    recent_page = _page(locator, [
        _peer(90, text='之前的上下文'),
        _peer(95, text='本批前最后一条'),
        _peer(97),   # 本批消息（等于本批最小值）：排除
        _peer(105, text='批后新消息'),  # 101-109 场景：排除
    ])
    client = _ClientStub()
    await _tick(client, recent_page=recent_page)
    assert len(client.requests) == 1
    text = client.requests[0][2]['text']
    assert '本批之前的近期往来' in text
    assert 'msg_id=95' in text and '本批前最后一条' in text
    assert 'msg_id=90' in text
    assert 'msg_id=105' not in text           # 批后新消息不入前文
    assert text.count('msg_id=97') == 1       # 本批消息只出现在本批段
    recent_segment = text.split('本批之前的近期往来')[1].split('本批新消息')[0]
    assert 'msg_id=97' not in recent_segment  # 97（＝本批最小值）不入前文段
    assert 'msg_id=99' not in text            # 快照外的旧默认消息不存在（防串批次）
    # 本批段在前文段之后（前文为背景，本批为待处理）
    assert text.index('本批之前的近期往来') < text.index('本批新消息')


@pytest.mark.asyncio
async def test_dispatch_recent_read_failure_continues_without_section(tenant_id):
    """①前文读取失败：记日志并继续提交，提交文本无前文段，批次正常派发。"""
    from loguru import logger

    locator = _locator(2)
    _register(tenant_id, locator)
    task = _create_started(tenant_id, [locator])
    batch = _make_batch(tenant_id, task['id'], locator)
    records = []
    handler_id = logger.add(lambda message: records.append(message), level='WARNING')
    try:
        client = _ClientStub()
        await _tick(client, recent_error={'code': 'DEVICE_OFFLINE', 'message': '离线'})
    finally:
        logger.remove(handler_id)
    assert len(client.requests) == 1   # 提交未因前文读取失败而放弃
    text = client.requests[0][2]['text']
    assert '本批之前的近期往来' not in text
    assert 'msg_id=99' in text         # 本批原文仍在
    assert any('近期往来读取失败' in str(message) for message in records)


@pytest.mark.asyncio
async def test_dispatch_reuses_recent_context_for_same_key_retry(tenant_id):
    """①备忘：提交瞬时失败后的同键重试复用首次近期往来（幂等意图逐字节一致），
    不重读最新页。"""
    locator = _locator(4)
    _register(tenant_id, locator)
    task = _create_started(tenant_id, [locator])
    batch = _make_batch(tenant_id, task['id'], locator)
    recent_page = _page(locator, [_peer(95, text='本批前消息')])
    client = _ClientStub(responses=[RunnerError('RUNNER_SERVICE_UNAVAILABLE', 503)])
    await _tick(client, recent_page=recent_page)
    assert _batch_row(tenant_id, batch['id'])['status'] == 'running'
    # 第二轮孤儿重提：备忘命中，不再发起受信读（读 mock 计数不变）
    with patch.object(proxy_api, '_read_sessions_call') as read_mock:
        read_mock.side_effect = AssertionError('重试不应重读近期往来')
        device = {'id': DEVICE, 'capabilities_json': {'capabilities': list(proxy_tasks.RUNTIME_TOOL_NAMES)}}
        with patch.object(proxy_api, '_task_device', AsyncMock(return_value=(device, None))), \
             patch.object(proxy_dispatcher, '_agent_allowed', AsyncMock(return_value=True)), \
             patch.object(proxy_dispatcher, '_proxy_client', lambda: client):
            await run_tick()
    assert len(client.requests) == 2
    assert client.requests[1][2]['client_request_id'] == client.requests[0][2][
        'client_request_id']
    assert client.requests[1][2]['text'] == client.requests[0][2]['text']


@pytest.mark.asyncio
async def test_orphan_resubmit_digest_mismatch_fails_deterministically(tenant_id):
    """①幂等失配（进程重启备忘不可得、重读漂移）：同键重提交遇
    IDEMPOTENCY_INPUT_MISMATCH 走确定性拒绝——批次 failed＋目标 halted，
    不再重试、不产生第二次 Runner 执行。"""
    locator = _locator(9)
    _register(tenant_id, locator)
    task = _create_started(tenant_id, [locator])
    batch = _make_batch(tenant_id, task['id'], locator)
    client = _ClientStub(responses=[RunnerError('RUNNER_SERVICE_UNAVAILABLE', 503)])
    await _tick(client, recent_page=_page(locator, [_peer(95, text='旧前文')]))
    assert _batch_row(tenant_id, batch['id'])['status'] == 'running'
    # 模拟进程重启：备忘清空后重读最新页（新消息使前文漂移→text 与首次不同）
    client2 = _ClientStub(responses=[RunnerError('IDEMPOTENCY_INPUT_MISMATCH', 409)])
    await _tick(client2, recent_page=_page(locator, [_peer(95, text='旧前文'),
                                                     _peer(96, text='期间新到')]))
    assert len(client2.requests) == 1          # 失配即确定性拒绝，不二刷同键
    assert _batch_row(tenant_id, batch['id'])['status'] == 'failed'
    assert _target_row(tenant_id, task['id'], locator)['status'] == 'halted'
    assert _task_row(tenant_id, task['id'])['status'] == 'running'  # 非权限类不停任务
    # 后续轮次不再对该批发起任何提交（failed 为终态，不产生第二次执行）
    client3 = _ClientStub()
    await _tick(client3)
    assert client3.requests == []


# ==================== 评审问题 2：占用事务内任务/目标复核 ====================


@pytest.mark.asyncio
@pytest.mark.parametrize('branch', ['task_stopped', 'stop_requested', 'target_removed'])
async def test_occupy_rechecks_task_and_target_in_tx(tenant_id, branch):
    """②占用复核：任务已 stopped/stop_requested=true/目标 removed 三分支——
    occupy 返回 False、批次保持 pending（停止任务的 pending 批留待下次启动）。"""
    locator = _locator(5 if branch == 'task_stopped' else
                       6 if branch == 'stop_requested' else 7)
    _register(tenant_id, locator)
    task = _create_started(tenant_id, [locator])
    batch = _make_batch(tenant_id, task['id'], locator)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if branch == 'task_stopped':
            cursor.execute('''UPDATE bs_wecom_proxy_tasks SET status='stopped'
                              WHERE id=%s AND tenant_id=%s''', (task['id'], tenant_id))
        elif branch == 'stop_requested':
            cursor.execute('''UPDATE bs_wecom_proxy_tasks SET stop_requested=TRUE
                              WHERE id=%s AND tenant_id=%s''', (task['id'], tenant_id))
        else:
            cursor.execute('''UPDATE bs_wecom_proxy_task_sessions SET status='removed'
                              WHERE tenant_id=%s AND task_id=%s AND session=%s''',
                           (tenant_id, task['id'], locator))
        conn.commit()
    batch = _batch_row(tenant_id, batch['id'])
    occupied = await asyncio.to_thread(proxy_dispatcher._occupy_batch, batch)
    assert occupied is False
    assert _batch_row(tenant_id, batch['id'])['status'] == 'pending'


@pytest.mark.asyncio
async def test_occupy_still_succeeds_on_healthy_batch(tenant_id):
    """②正常路径不回归：健康 pending 批占用成功置 running。"""
    locator = _locator(8)
    _register(tenant_id, locator)
    task = _create_started(tenant_id, [locator])
    batch = _make_batch(tenant_id, task['id'], locator)
    occupied = await asyncio.to_thread(proxy_dispatcher._occupy_batch, batch)
    assert occupied is True
    assert _batch_row(tenant_id, batch['id'])['status'] == 'running'


# ==================== 评审问题 3：消息时间按北京时间渲染 ====================


def test_render_batch_text_uses_shanghai_time_with_label():
    """③send_time 渲染为上海时间（+8）且显式标注北京时间，非 UTC。"""
    from datetime import datetime as dt
    from zoneinfo import ZoneInfo

    batch = {'id': 1, 'session': 'ws1.x', 'batch_end_msg_id': 100,
             'input_snapshot': {'messages': [_peer(99, send_time=1760000000)]}}
    text = proxy_dispatcher.render_batch_text(batch)
    expected = dt.fromtimestamp(1760000000, ZoneInfo('Asia/Shanghai')).strftime('%m-%d %H:%M')
    utc_text = dt.fromtimestamp(1760000000, ZoneInfo('UTC')).strftime('%m-%d %H:%M')
    assert expected != utc_text                        # 该时点上海与 UTC 相差 8 小时
    assert f'[msg_id=99 {expected} 北京时间]' in text  # 上海时间＋显式标注
    assert utc_text not in text                        # 不再渲染 UTC
    assert '时间均为北京时间' in text


def test_render_batch_text_recent_section_same_format():
    """①③前文段与本批段同格式（时间/发送人/提及，北京时间标注）。"""
    batch = {'id': 1, 'session': 'ws1.x', 'batch_end_msg_id': 100,
             'input_snapshot': {'messages': [_peer(100, send_time=1760000000)]}}
    recent = [_peer(95, send_time=1759999000,
                    mentions=[{'kind': 'user', 'user_id': 'acc1', 'name': '我'}])]
    text = proxy_dispatcher.render_batch_text(batch, recent)
    recent_segment = text.split('本批之前的近期往来')[1].split('本批新消息')[0]
    assert 'msg_id=95' in recent_segment and '@我' in recent_segment
    assert '北京时间]' in recent_segment
