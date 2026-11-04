"""结果受理回调路由测试（纯 mock；真实 SQL 见 test_archive/test_service_sql）。

覆盖设计 §3.1：只处理 wecom+成功结果；读取/发送/对账各走正确分支；
明确监控目标在空消息时登记，目录和日期读取不纳入；异常不外抛。
"""

import base64
import json
import uuid
from unittest.mock import patch

import pytest

from src.wecom_conversation import logsink


def _locator(account='acc1', conversation='R:group1'):
    return 'ws1.' + base64.urlsafe_b64encode(
        json.dumps({'account': account, 'conversation_id': conversation}).encode()).decode().rstrip('=')


def _row(tool='wecom_read_session', data=None, arguments=None, state='succeeded',
         provider_key='wecom'):
    return {'id': uuid.uuid4(), 'tenant_id': 't1', 'user_id': 'u1', 'tool_name': tool,
            'provider_key': provider_key, 'state': state, 'device_id': uuid.uuid4(),
            'result_json': {'data': data or {}}, 'arguments_json': arguments or {},
            'business_ref': {}}


@pytest.mark.asyncio
async def test_ignores_non_wecom_or_failed_results():
    calls = []

    async def fake_read(row):
        calls.append(row)

    with patch.object(logsink, '_handle_read', fake_read):
        await logsink.on_result_accepted(_row(state='failed'))
        await logsink.on_result_accepted(_row(provider_key='other'))
    assert calls == []


@pytest.mark.asyncio
async def test_read_result_stored_and_reconciled_riders_consumed():
    row = _row(data={'channel': 'local-db', 'source_generation': 'gen1',
                     'messages': [], 'reconciled_sends': [{'session': _locator()}]})
    stored = {}
    with patch.object(logsink, '_factory', lambda: None),          patch.object(logsink.archive, 'store_read_result',
                      lambda *a, **k: stored.setdefault('read', a) or {'messages': 1}), \
         patch.object(logsink.archive, 'store_reconciled_sends',
                      lambda *a, **k: stored.setdefault('reconciled', a) or {}):
        await logsink.on_result_accepted(row)
    assert 'read' in stored and 'reconciled' in stored


@pytest.mark.asyncio
async def test_attachment_and_resolve_reads_are_skipped():
    row = _row(arguments={'attachment': {'message_id': 5}})
    row2 = _row(arguments={'resolve_only': True})
    with patch.object(logsink.archive, 'store_read_result') as store:
        await logsink.on_result_accepted(row)
        await logsink.on_result_accepted(row2)
    store.assert_not_called()


@pytest.mark.asyncio
async def test_agent_explicit_empty_targets_enroll_but_scan_and_daily_do_not():
    one = _locator(conversation='R:empty')
    two = _locator(conversation='S:acc1_peer')
    registered = []

    def register(connection, tenant, user, locator, **kwargs):
        registered.append((locator, kwargs['reason']))
        return {'id': len(registered)}, True

    batch = _row(tool='wecom_read_sessions', arguments={'sessions': ['空群', '空单聊']},
                 data={'channel': 'local-db', 'self_user_id': 'acc1', 'sessions': [],
                       'resolved_targets': [{'session': one, 'kind': 'group'},
                                            {'session': two, 'kind': 'private'}]})
    batch['business_kind'] = 'agent_runner'
    scan = _row(tool='wecom_read_sessions', arguments={}, data=batch['result_json']['data'])
    scan['business_kind'] = 'agent_runner'
    daily = _row(arguments={'date': '2026-11-04'},
                 data={'channel': 'local-db', 'self_user_id': 'acc1', 'session': one})
    daily['business_kind'] = 'agent_runner'
    with patch.object(logsink, '_open_connection', lambda: _NullConn()), \
         patch.object(logsink.archive, 'register_scope', register), \
         patch.object(logsink.archive, 'store_read_result', lambda *a, **k: {'messages': 0}):
        for row in (batch, scan, daily):
            await logsink.on_result_accepted(row)
    assert registered == [(one, 'agent_monitor'), (two, 'agent_monitor')]


@pytest.mark.asyncio
async def test_agent_single_empty_read_enrolls_only_original_stable_target():
    original = _locator(conversation='R:original')
    extra = _locator(conversation='R:extra')
    registered = []
    def register(connection, tenant, user, locator, **kwargs):
        registered.append(locator)
        return {'id': 1}, True
    valid = _row(arguments={'session': original}, data={
        'channel': 'local-db', 'self_user_id': 'acc1', 'session': original, 'messages': []})
    valid['business_kind'] = 'agent_runner'
    forged = _row(arguments={'session': original}, data={
        'channel': 'local-db', 'self_user_id': 'acc1', 'session': extra, 'messages': []})
    forged['business_kind'] = 'agent_runner'
    with patch.object(logsink, '_open_connection', lambda: _NullConn()), \
         patch.object(logsink.archive, 'register_scope', register), \
         patch.object(logsink.archive, 'store_read_result', lambda *a, **k: {'messages': 0}):
        await logsink.on_result_accepted(valid)
        await logsink.on_result_accepted(forged)
    assert registered == [original]


def test_other_device_cannot_rebind_or_archive_same_locator():
    """设备 B 的已接受结果不能改绑设备 A 的会话，也不能混入 A 的消息日志。"""
    locator = _locator()
    other_device = 'device-a'
    source = _row(arguments={'session': locator}, data={
        'channel': 'local-db', 'self_user_id': 'acc1', 'session': locator, 'messages': []})
    source['business_kind'] = 'agent_runner'
    source['device_id'] = 'device-b'

    class BoundCursor:
        def execute(self, sql, params=None):
            if 'SELECT device_id FROM bs_wecom_conversations' in sql:
                self.one = {'device_id': other_device}
            elif 'SELECT id, session_locator' in sql:
                self.many = [{'id': 1, 'session_locator': locator,
                              'timezone': 'Asia/Shanghai', 'coverage': None,
                              'device_id': other_device}]
            elif 'pg_advisory_xact_lock' not in sql:
                raise AssertionError(f'unexpected write: {sql}')

        def fetchone(self):
            return getattr(self, 'one', None)

        def fetchall(self):
            return getattr(self, 'many', [])

    class BoundConnection:
        def cursor(self):
            return BoundCursor()

        def commit(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    factory = lambda: BoundConnection()
    plan = {'generation': 'gen1', 'sessions': [
        {'locator': locator, 'messages': []}]}
    with patch.object(logsink, '_open_connection', factory), \
         patch.object(logsink.archive, 'register_scope') as register, \
         patch.object(logsink.archive, 'extract_archive_plan', return_value=plan), \
         patch.object(logsink.archive, 'map_message',
                      return_value={'source_message_id': 1, 'sender_side': 'peer'}), \
         patch.object(logsink.archive, 'execute_values') as insert:
        logsink._register_read_targets(source, source['result_json']['data'],
                                       source['arguments_json'])
        logsink._register_from_send(source, locator=locator, reason='agent_replied')
        read = logsink.archive.store_read_result(
            factory, 't1', 'u1', source['result_json']['data'],
            source['arguments_json'], device_id='device-b', invocation_id=str(source['id']))
        send = logsink.archive.store_send_fragment(
            factory, 't1', 'u1', {'session': locator, 'source_generation': 'gen1',
                                  'messages': [{'msg_id': 1}]},
            device_id='device-b', invocation_id=str(source['id']))
    register.assert_not_called()
    insert.assert_not_called()
    assert read == {'conversations': 0, 'messages': 0}
    assert send == {'conversations': 0, 'messages': 0}


class _NullCursor:
    def execute(self, *a, **k):
        pass

    def fetchone(self):
        return None


class _NullConn:
    def cursor(self):
        return _NullCursor()

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _patch_open_connection():
    from unittest.mock import patch
    return patch.object(logsink, '_open_connection', lambda: _NullConn())


@pytest.mark.asyncio
async def test_successful_send_registers_scope_and_consumes_fragment():
    locator = _locator()
    row = _row(tool='wecom_message_send',
               data={'session': locator, 'title': '售前群', 'kind': 'group',
                     'observed_chat': {'session': locator, 'source_generation': 'gen2',
                                       'messages': []}})
    registered = {}
    fragment_saved = {}

    def fake_register(connection, tenant, user, loc, **kwargs):
        registered.update(locator=loc, reason=kwargs.get('reason'))
        return {'id': 1, 'session_locator': loc, 'timezone': 'Asia/Shanghai',
                'device_id': None}, True

    with patch.object(logsink.archive, 'register_scope', fake_register), \
         patch.object(logsink.archive, 'store_send_fragment',
                      lambda *a, **k: fragment_saved.update(a[3]) or {'messages': 0}), \
         patch.object(logsink, '_open_connection', lambda: _NullConn()):
        await logsink.on_result_accepted(row)
    assert registered['reason'] == 'agent_replied'
    assert fragment_saved.get('session') == locator
    assert registered['reason'] == 'agent_replied'


@pytest.mark.asyncio
async def test_repeat_send_does_not_create_another_scope():
    locator = _locator()
    row = _row(tool='wecom_message_send', data={'session': locator})
    calls = []

    def fake_register(connection, tenant, user, loc, **kwargs):
        return {'id': 1, 'session_locator': loc, 'timezone': 'Asia/Shanghai'}, False

    with patch.object(logsink.archive, 'register_scope', fake_register), \
         patch.object(logsink, '_open_connection', lambda: _NullConn()):
        await logsink.on_result_accepted(row)
    assert calls == []  # 回调不提交补录任务


@pytest.mark.asyncio
async def test_send_status_confirmed_consumes_original_observed_chat():
    locator = _locator()
    original_id = uuid.uuid4()
    row = _row(tool='wecom_send_status',
               data={'send_status': 'confirmed',
                     'reconciliation': {'message_id': 9, 'source_generation': 'gen1'}})
    row['business_ref'] = {'send_invocation_id': str(original_id)}
    fragment_saved = {}

    class FakeCursor:
        def __init__(self):
            self.step = 0

        def execute(self, *a, **k):
            self.step += 1

        def fetchone(self):
            if self.step == 1:  # 原发送行（含 unknown 时的 observed_chat）
                return {'id': str(original_id), 'result_json': {'data': {
                    'session': locator, 'observed_chat': {
                        'session': locator, 'source_generation': 'gen1', 'messages': []}}}}
            if self.step == 2:  # _runner_profile：agent_runners 查询
                return None
            return {'session': locator}  # 原发送会话定位

    class FakeConn:
        def cursor(self):
            return FakeCursor()

        def commit(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_register(connection, tenant, user, loc, **kwargs):
        return {'id': 1, 'session_locator': loc, 'timezone': 'Asia/Shanghai',
                'device_id': None}, True

    with patch('src.db.database.get_db_connection', lambda: FakeConn()), \
         patch.object(logsink.archive, 'register_scope', fake_register), \
         patch.object(logsink.archive, 'store_reconciled_sends',
                      lambda *a, **k: {'attributed': 0}), \
         patch.object(logsink.archive, 'store_send_fragment',
                      lambda *a, **k: fragment_saved.update(a[3]) or {'messages': 0}):
        await logsink.on_result_accepted(row)
    assert fragment_saved.get('session') == locator
    assert fragment_saved.get('confirmed_message_id') == 9  # 对账确认补精确命中标注


@pytest.mark.asyncio
async def test_callback_swallows_storage_errors():
    row = _row(data={'channel': 'local-db', 'source_generation': 'gen1'})
    with patch.object(logsink.archive, 'store_read_result',
                      side_effect=RuntimeError('db down')), \
         patch.object(logsink, '_note_scope_error', lambda *a, **k: None):
        await logsink.on_result_accepted(row)  # 不抛出：原结果不受影响
