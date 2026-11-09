"""设备组登记从任务和未结算批次恢复；查询只返回对应 Runtime。"""

import asyncio
import base64
import json

import pytest

from src.db.database import get_db_connection
from src.scheduler.manager import ScheduledTaskManager
from src.wecom_conversation import archive, proxy_dispatcher, proxy_monitor, proxy_tasks

USER = 'device-job-user'
DEVICE_A = '11111111-1111-1111-1111-111111111111'
DEVICE_B = '22222222-2222-2222-2222-222222222222'


@pytest.fixture(autouse=True)
def _require_db(db_ready):
    yield


def _started_task(tenant_id, device, account):
    locator = 'ws1.' + base64.urlsafe_b64encode(json.dumps({
        'account': account, 'conversation_id': 'R:job-' + account,
    }).encode()).decode().rstrip('=')
    with get_db_connection() as conn:
        archive.register_scope(conn, tenant_id, USER, locator, name='设备组测试',
                               kind='group', device_id=device, reason='manual_add')
        conn.commit()
    task = proxy_tasks.create_task(
        tenant_id, USER, device_id=device, account=account, self_user_id=account,
        title='设备组代理', agent_id='agent-a', instruction='回答', sessions=[locator])
    proxy_tasks.complete_start(
        tenant_id, USER, task['id'], device_id=device, self_user_id=account,
        baselines={locator: {'cursor': 'wb1.baseline', 'generation': 'gen-1',
                             'watermark': 10}})
    target = proxy_tasks.get_task(tenant_id, USER, task['id'])['targets'][0]
    return task, target


def test_stopped_task_running_batch_keeps_only_its_device_dispatch_job(tenant_id):
    """停止后的执行中批仍须结算；另一设备读取/派发不能把它装进自己的轮。"""
    task_a, target_a = _started_task(tenant_id, DEVICE_A, 'account-a')
    _started_task(tenant_id, DEVICE_B, 'account-b')
    with get_db_connection() as conn:
        cursor = conn.cursor()
        batch = proxy_tasks.create_batch_in_tx(
            cursor, tenant_id, USER, task_id=task_a['id'], session=target_a['session'],
            batch_end_msg_id=11,
            input_snapshot={'schema_version': 1, 'session': target_a['session'],
                            'messages': [{'msg_id': 11, 'side': 'peer', 'text': '测试'}]},
            agent_id='agent-a', instruction_snapshot='回答',
            web_session_id=target_a['web_session_id'])
        proxy_tasks.transition_batch_in_tx(cursor, tenant_id, batch['id'], 'running')
        cursor.execute("UPDATE bs_wecom_proxy_tasks SET status='stopped', stop_requested=TRUE WHERE id=%s",
                       (task_a['id'],))
        conn.commit()

    group_a = (tenant_id, DEVICE_A, 'account-a')
    group_b = (tenant_id, DEVICE_B, 'account-b')
    manager = ScheduledTaskManager()
    assert set(manager._load_wecom_proxy_groups()) == {group_a, group_b}
    assert proxy_monitor._load_work_items(group_a) == []
    assert len(proxy_monitor._load_work_items(group_b)) == 1
    assert [row['id'] for row in proxy_dispatcher._load_active_batches(group_a)] == [batch['id']]
    assert proxy_dispatcher._load_active_batches(group_b) == []

    with get_db_connection() as conn:
        cursor = conn.cursor()
        proxy_tasks.transition_batch_in_tx(cursor, tenant_id, batch['id'], 'failed',
                                           cli_error='test_finished')
        conn.commit()
    assert set(manager._load_wecom_proxy_groups()) == {group_b}


def test_halted_only_group_ends_but_stop_request_still_settles(tenant_id):
    """唯一目标停住后不空转；随后停止请求仍需一次监控轮把任务落 stopped。"""
    task, target = _started_task(tenant_id, DEVICE_A, 'account-a')
    group = (tenant_id, DEVICE_A, 'account-a')
    manager = ScheduledTaskManager()
    assert manager._load_wecom_proxy_groups() == [group]

    with get_db_connection() as conn:
        cursor = conn.cursor()
        assert proxy_tasks.mark_target_halted_in_tx(
            cursor, tenant_id, task['id'], target['session'], 'test_halted')
        conn.commit()
    assert manager._load_wecom_proxy_groups() == []

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('UPDATE bs_wecom_proxy_tasks SET stop_requested=TRUE WHERE id=%s',
                       (task['id'],))
        conn.commit()
    assert manager._load_wecom_proxy_groups() == [group]

    asyncio.run(proxy_monitor.run_tick(group))
    assert proxy_tasks.get_task(tenant_id, USER, task['id'])['status'] == 'stopped'
    assert manager._load_wecom_proxy_groups() == []
