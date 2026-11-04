"""日报计划/补录任务标记协议与提交接点测试。

纯逻辑部分：标记解析/受信描述注入（普通任务不受影响）。
SQL 部分（隔离库）：同来源复用合适计划、一个计划管理多个会话。
"""

import base64
import json
import os
import uuid
from unittest.mock import patch

import pytest

from src.db.database import get_db_connection
from src.wecom_conversation import archive, plans


def _locator(account='acc1', conversation='R:group1'):
    return 'ws1.' + base64.urlsafe_b64encode(
        json.dumps({'account': account, 'conversation_id': conversation}).encode()).decode().rstrip('=')


def test_plan_prompt_marker_roundtrip():
    config = {'timezone': 'Asia/Shanghai', 'device': 'dev-1', 'account': 'acc1'}
    prompt = plans.plan_prompt('daily', config, '重点关注退换货')
    parsed = plans.parse_plan_prompt(prompt)
    assert parsed['kind'] == 'daily'
    assert parsed['timezone'] == 'Asia/Shanghai'
    assert parsed['device'] == 'dev-1'
    # 标记行之后保留人类可读要求文本
    assert '重点关注退换货' in prompt.split('\n', 1)[1]


def test_parse_plan_prompt_rejects_normal_prompts_and_garbage():
    assert plans.parse_plan_prompt('每天早上八点总结昨天的会话') is None
    assert plans.parse_plan_prompt('') is None
    assert plans.parse_plan_prompt('[wecom-conversation:daily]not-json') is None
    assert plans.parse_plan_prompt(None) is None


def test_augment_submission_injects_descriptor_and_device():
    prompt = plans.plan_prompt('daily', {'timezone': 'Asia/Shanghai', 'device': 'dev-1'})
    body = {'source': 'scheduled', 'text': prompt,
            'request_data': {'scheduled': {'task_id': 'sched_abc', 'trigger': 'scheduled'}}}
    plans.augment_submission(body)
    descriptor = body['request_data'][plans.DESCRIPTOR_KEY]
    assert descriptor['kind'] == 'daily'
    assert descriptor['task_id'] == 'sched_abc'
    assert body['request_data']['wecom_task_device'] == 'dev-1'


def test_augment_submission_leaves_normal_tasks_untouched():
    body = {'source': 'scheduled', 'text': '普通任务定义',
            'request_data': {'scheduled': {'task_id': 'sched_xyz'}}}
    plans.augment_submission(body)
    assert plans.DESCRIPTOR_KEY not in body['request_data']
    assert 'wecom_task_device' not in body['request_data']


def test_backfill_marker_carries_session_and_date():
    prompt = plans.plan_prompt('backfill',
                               {'session': _locator(), 'date': '2026-11-03', 'timezone': 'Asia/Shanghai'})
    parsed = plans.parse_plan_prompt(prompt)
    assert parsed['kind'] == 'backfill'
    assert parsed['date'] == '2026-11-03'
    assert parsed['session'].startswith('ws1.')


@pytest.fixture(autouse=True)
def _require_db(db_ready):
    yield


def _clear_backfill_tasks(tenant_id):
    """补录 once 任务计入每用户 active 配额（并行时间窗任务引入）：测试内清理。"""
    from src.db.database import get_db_connection
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM scheduled_tasks WHERE tenant_id=%s AND task_prompt LIKE %s",
                       (tenant_id, '[wecom-conversation:backfill]%'))
        conn.commit()


def test_ensure_daily_plan_creates_and_reuses(tenant_id):
    import uuid as _uuid
    user_id = 'user-1'
    device = _uuid.uuid4()
    locator_a = _locator('acc1', 'R:group1')
    locator_b = _locator('acc1', 'R:group2')
    other_account = _locator('acc9', 'R:group9')
    created = plans.ensure_daily_plan(
        tenant_id, user_id, locators=[locator_a],
        schedule={'schedule_type': 'daily', 'cron_expression': '0 8 * * *'},
        profile_id='trade-specialist', timezone_name='Asia/Shanghai',
        device_id=device, requirements='重点关注退换货')
    assert created['created'] is True
    task_id = created['plan']['task_id']
    assert [row['session_locator'] for row in created['conversations']] == [locator_a]
    _clear_backfill_tasks(tenant_id)

    # 同来源（账号+设备+智能体一致且无新显式要求）：复用计划并入新会话（评审 P1-7）
    reused = plans.ensure_daily_plan(
        tenant_id, user_id, locators=[locator_a, locator_b],
        schedule={'schedule_type': 'daily', 'cron_expression': '0 8 * * *'},
        profile_id='trade-specialist', timezone_name='Asia/Shanghai', device_id=device)
    assert reused['created'] is False
    assert reused['plan']['task_id'] == task_id
    assert {row['session_locator'] for row in reused['conversations']} == {locator_a, locator_b}
    _clear_backfill_tasks(tenant_id)

    # 不同设备：不跨设备复用，创建对应计划（评审 P1-7）
    cross_device = plans.ensure_daily_plan(
        tenant_id, user_id, locators=[_locator('acc1', 'R:group3')],
        schedule={'schedule_type': 'daily', 'cron_expression': '0 8 * * *'},
        profile_id='trade-specialist', timezone_name='Asia/Shanghai',
        device_id=_uuid.uuid4())
    assert cross_device['created'] is True
    assert cross_device['plan']['task_id'] != task_id
    _clear_backfill_tasks(tenant_id)

    # 显式提出不同要求：不复用旧计划（评审 P1-7）
    different = plans.ensure_daily_plan(
        tenant_id, user_id, locators=[_locator('acc1', 'R:group4')],
        schedule={'schedule_type': 'daily', 'cron_expression': '0 9 * * *'},
        profile_id='trade-specialist', timezone_name='Asia/Shanghai',
        device_id=device)
    assert different['created'] is True
    _clear_backfill_tasks(tenant_id)

    # 仅改 interval 也必须新建计划（独立复测 P2-1：原比较是死代码）
    interval_plan = plans.ensure_daily_plan(
        tenant_id, user_id, locators=[_locator('acc1', 'R:group5')],
        schedule={'schedule_type': 'interval', 'interval_seconds': 7200},
        profile_id='trade-specialist', timezone_name='Asia/Shanghai',
        device_id=device)
    assert interval_plan['created'] is True
    assert interval_plan['plan']['interval_seconds'] == 7200
    _clear_backfill_tasks(tenant_id)

    # 不同来源账号：不混入同一计划
    other = plans.ensure_daily_plan(
        tenant_id, user_id, locators=[other_account],
        schedule={'schedule_type': 'daily', 'cron_expression': '0 8 * * *'},
        profile_id='trade-specialist', timezone_name='Asia/Shanghai', device_id=device)
    assert other['created'] is True
    assert other['plan']['task_id'] != task_id
    _clear_backfill_tasks(tenant_id)

    from src.wecom_conversation import service
    managed = service.plan_conversations(tenant_id, user_id, task_id)
    assert {row['session_locator'] for row in managed} == {locator_a, locator_b}


def test_ensure_daily_plan_rejects_mixed_sources(tenant_id):
    with pytest.raises(Exception) as error:
        plans.ensure_daily_plan(
            't', 'u', locators=[_locator('acc1', 'R:g1'), _locator('acc2', 'R:g2')],
            schedule={'schedule_type': 'daily'})
    assert 'PLAN_SOURCE_MIXED' in str(error.value) or '同一来源' in str(error.value)


def test_schedule_backfill_task_registers_once_task(tenant_id):
    from src.db.database import get_db_connection
    conversation = {'id': 12345, 'session_locator': _locator(), 'timezone': 'Asia/Shanghai',
                    'name': '售前群'}
    task = plans.schedule_backfill_task(tenant_id, 'user-1', conversation, device_id=None)
    assert task['schedule_type'] == 'once'
    parsed = plans.parse_plan_prompt(task['task_prompt'])
    assert parsed['kind'] == 'backfill'
    assert parsed['session'] == conversation['session_locator']
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM scheduled_tasks WHERE task_id=%s', (task['task_id'],))
        conn.commit()


@pytest.mark.usefixtures("db_ready")
def test_plan_source_resolved_from_conversation_rows_without_device_param(tenant_id):
    """评审三 P1-2：Skill 示例省略 --device 时，计划来源从已登记会话行解析
    （agent_replied 登记已带原设备 D），执行不依赖全局 selected。"""
    import uuid as _uuid
    user_id = 'user-1'
    device_d = _uuid.uuid4()
    locator = _locator('acc1', 'R:fromagent')
    # 会话此前由发送回调登记，行上带原设备 D
    from src.wecom_conversation import archive
    with get_db_connection() as connection:
        archive.register_scope(connection, tenant_id, user_id, locator,
                               device_id=device_d, reason='agent_replied')
        connection.commit()
    created = plans.ensure_daily_plan(
        tenant_id, user_id, locators=[locator],
        schedule={'schedule_type': 'daily', 'cron_expression': '0 8 * * *'},
        timezone_name='Asia/Shanghai')  # 不传 device_id（Skill 示例路径）
    assert created['created'] is True
    parsed = plans.parse_plan_prompt(created['plan']['task_prompt'])
    assert parsed['device'] == str(device_d)  # 原设备进入计划配置 → augment_submission 注入绑定
    assert str(created['conversations'][0]['device_id']) == str(device_d)
    _clear_backfill_tasks(tenant_id)


@pytest.mark.usefixtures("db_ready")
def test_config_change_closes_orphaned_old_plan_and_keeps_split_plan(tenant_id):
    """评审三 P2-3：配置变化=完整业务操作——新计划生效、空旧计划沿 Scheduler
    暂停收口；仍有会话的旧计划照常执行。"""
    user_id = 'user-1'
    device = uuid.uuid4()
    locator_a = _locator('acc1', 'R:split1')
    locator_b = _locator('acc1', 'R:split2')
    made = plans.ensure_daily_plan(
        tenant_id, user_id, locators=[locator_a, locator_b],
        schedule={'schedule_type': 'daily', 'cron_expression': '0 8 * * *'},
        profile_id='main', timezone_name='Asia/Shanghai', device_id=device)
    plan_a = made['plan']['task_id']
    _clear_backfill_tasks(tenant_id)

    # 拆出会话 A 到 09:00 新计划：A 仍有会话 B → 照常执行，不收口
    split = plans.ensure_daily_plan(
        tenant_id, user_id, locators=[locator_a],
        schedule={'schedule_type': 'daily', 'cron_expression': '0 9 * * *'},
        profile_id='main', timezone_name='Asia/Shanghai', device_id=device)
    plan_b = split['plan']['task_id']
    assert plan_b != plan_a
    from src.scheduler.db import ScheduledTaskDB
    from src.wecom_conversation import service
    assert ScheduledTaskDB.get_by_id(plan_a, tenant_id=tenant_id, user_id=user_id)['status'] == 'active'
    assert [row['id'] for row in service.plan_conversations(tenant_id, user_id, plan_a)] ==         [made['conversations'][1]['id']]
    _clear_backfill_tasks(tenant_id)

    # 会话 B 也移动到 09:00 计划（配置一致 → 复用 B）：A 变空 → 沿 Scheduler 暂停
    moved = plans.ensure_daily_plan(
        tenant_id, user_id, locators=[locator_b],
        schedule={'schedule_type': 'daily', 'cron_expression': '0 9 * * *'},
        profile_id='main', timezone_name='Asia/Shanghai', device_id=device)
    assert moved['plan']['task_id'] == plan_b
    row_a = ScheduledTaskDB.get_by_id(plan_a, tenant_id=tenant_id, user_id=user_id)
    assert row_a['status'] == 'paused'  # 无目标旧计划收口
    assert row_a['pause_reason'] == 'definition_invalid'
    assert service.plan_conversations(tenant_id, user_id, plan_a) == []
    assert {row['daily_plan_task_id'] for row in service.plan_conversations(tenant_id, user_id, plan_b)} == {plan_b}
    _clear_backfill_tasks(tenant_id)


@pytest.mark.usefixtures("db_ready")
def test_plan_source_from_runner_binding_when_no_business_record(tenant_id):
    """评审四 P2-1：只读新群、无业务记录、未发送消息、直接建日报——来源从
    当前已授权 Runner 的 wecom 绑定固化；日报与首次补录均绑定原设备。"""
    import uuid as _uuid
    user_id = 'user-1'
    device_d = _uuid.uuid4()
    session = 'wecom_plan_src_sess'
    runner_id = 'run-wecom-src'
    identity = {'tenant_id': tenant_id, 'user_id': user_id, 'session_id': session,
                'source': 'chat', 'session_kind': 'web'}
    checkpoint = {'execution': {'execution_id': runner_id, 'identity': identity,
                                'resources': {'wecom_task_device': {
                                    'version': 1, 'runner_id': runner_id,
                                    'identity': identity, 'device_id': str(device_d)}}}}
    from src.db.database import get_db_connection
    with get_db_connection() as connection:
        cursor = connection.cursor()
        cursor.execute(
            '''INSERT INTO agent_runners
                   (runner_id, tenant_id, scope_key, session_kind, session_id,
                    actor_kind, actor_id, user_id, service_id, source,
                    client_request_id, input_digest, input, profile_id,
                    profile_fingerprint, checkpoint, status, record_id)
               VALUES (%s, %s, 'tenant:'||%s, 'web', %s, 'user', %s, %s,
                       'web', 'chat', 'creq-src', 'd', '{}'::jsonb, 'main', 'fp',
                       %s::jsonb, 'running', 'rec-src')''',
            (runner_id, tenant_id, tenant_id, session, user_id, user_id,
             json.dumps(checkpoint)))
        connection.commit()

    with patch.dict(os.environ, {'AID_RUNNER_ID': runner_id,
                                 'AID_SESSION_ID': session}):
        resolved = plans.resolve_runner_source_device(
            tenant_id, user_id, runner_id=runner_id, session_id=session)
        assert resolved == str(device_d)
        locator = _locator('acc1', 'R:newgroup')
        created = plans.ensure_daily_plan(
            tenant_id, user_id, locators=[locator],
            schedule={'schedule_type': 'daily', 'cron_expression': '0 8 * * *'},
            timezone_name='Asia/Shanghai')  # Skill 示例路径，无业务记录
    assert created['created'] is True
    parsed = plans.parse_plan_prompt(created['plan']['task_prompt'])
    assert parsed['device'] == str(device_d)  # 日报绑定原设备
    # 首次补录同样绑定原设备
    from src.db.database import get_db_connection as _g
    with _g() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT task_prompt FROM scheduled_tasks WHERE tenant_id=%s AND task_prompt LIKE %s",
            (tenant_id, '[wecom-conversation:backfill]%'))
        backfills = [dict(row) for row in cursor.fetchall()]
    assert backfills and plans.parse_plan_prompt(backfills[0]['task_prompt'])['device'] == str(device_d)
    _clear_backfill_tasks(tenant_id)


def _insert_read_invocation(connection, tenant_id, user_id, device, locator,
                            *, seconds_ago=1):
    """插入一条成功的 wecom 原读取调用（参数/结果携带目标会话标识）。"""
    import uuid as _uuid
    cursor = connection.cursor()
    cursor.execute(
        "INSERT INTO local_tool_invocations"
        " (id, tenant_id, user_id, device_id, tool_name, provider_key, state,"
        "  result_json, arguments_json, created_at)"
        " VALUES (%s,%s,%s,%s,'wecom_read_session','wecom','succeeded',"
        " %s::jsonb, %s::jsonb, NOW() - interval '%s seconds')",
        (str(_uuid.uuid4()), tenant_id, user_id, str(device),
         json.dumps({'success': True, 'data': {'session': locator, 'messages': []}}),
         json.dumps({'session': locator, 'limit': 100}),
         seconds_ago))
    connection.commit()


@pytest.mark.usefixtures("db_ready")
def test_plan_source_fallback_matches_target_conversation(tenant_id):
    """评审四 P2-1 回退：无 Runner 绑定时取**匹配目标会话**的原读取设备。"""
    import uuid as _uuid
    user_id = 'user-1'
    device_r = _uuid.uuid4()
    locator = _locator('acc1', 'R:target')
    with get_db_connection() as connection:
        _insert_read_invocation(connection, tenant_id, user_id, device_r, locator,
                                seconds_ago=100)
    resolved = plans.resolve_runner_source_device(tenant_id, user_id,
                                                  locators=[locator])
    assert resolved == str(device_r)


@pytest.mark.usefixtures("db_ready")
def test_plan_source_ignores_unrelated_conversation_reads(tenant_id):
    """评审五 P2：目标群在 A 读取，其他会话后来在 B 读取——目标群来源固化
    为 A，不取 B；完全无匹配时不固化任何设备。"""
    import uuid as _uuid
    user_id = 'user-1'
    device_a = _uuid.uuid4()
    device_b = _uuid.uuid4()
    locator_target = _locator('acc1', 'R:target')
    locator_other = _locator('acc1', 'R:other')
    with get_db_connection() as connection:
        # 目标群较早前在 A 读取；其他会话后来在 B 读取
        _insert_read_invocation(connection, tenant_id, user_id, device_a,
                                locator_target, seconds_ago=100)
        _insert_read_invocation(connection, tenant_id, user_id, device_b,
                                locator_other, seconds_ago=10)
    resolved = plans.resolve_runner_source_device(tenant_id, user_id,
                                                  locators=[locator_target])
    assert resolved == str(device_a)  # 不取无关会话的设备 B
    # 无任何匹配证据 → None（不固化）
    assert plans.resolve_runner_source_device(
        tenant_id, user_id, locators=[_locator('acc1', 'R:never')]) is None


@pytest.mark.usefixtures("db_ready")
def test_plan_source_unknown_stays_unset_not_fabricated(tenant_id):
    """无 Runner 绑定、无读取事实 → 不伪造来源（计划 config 无 device 键）。"""
    user_id = 'user-1'
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop('AID_RUNNER_ID', None)
        os.environ.pop('AID_SESSION_ID', None)
        resolved = plans.resolve_runner_source_device(tenant_id, user_id)
    assert resolved is None
    created = plans.ensure_daily_plan(
        tenant_id, user_id, locators=[_locator('acc1', 'R:neverread')],
        schedule={'schedule_type': 'daily', 'cron_expression': '0 8 * * *'},
        timezone_name='Asia/Shanghai')
    parsed = plans.parse_plan_prompt(created['plan']['task_prompt'])
    assert 'device' not in parsed  # 不伪造
    _clear_backfill_tasks(tenant_id)
