"""结果受理回调路由测试（纯 mock；真实 SQL 见 test_archive/test_service_sql）。

覆盖设计 §3.1：只处理 wecom+成功结果；读取/发送/对账各走正确分支；首次
登记触发补录提交；异常不外抛。
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

    backfills = []

    with patch.object(logsink.archive, 'register_scope', fake_register), \
         patch.object(logsink.archive, 'store_send_fragment',
                      lambda *a, **k: fragment_saved.update(a[3]) or {'messages': 0}), \
         patch.object(logsink, '_schedule_backfill',
                      lambda *a, **k: backfills.append(a)), \
         patch.object(logsink, '_open_connection', lambda: _NullConn()):
        await logsink.on_result_accepted(row)
    assert registered['reason'] == 'agent_replied'
    assert fragment_saved.get('session') == locator
    assert backfills  # 首次登记触发补录提交接点


@pytest.mark.asyncio
async def test_repeat_send_does_not_reschedule_backfill():
    locator = _locator()
    row = _row(tool='wecom_message_send', data={'session': locator})
    backfills = []

    def fake_register(connection, tenant, user, loc, **kwargs):
        return {'id': 1, 'session_locator': loc, 'timezone': 'Asia/Shanghai'}, False

    with patch.object(logsink.archive, 'register_scope', fake_register), \
         patch.object(logsink, '_schedule_backfill',
                      lambda *a, **k: backfills.append(a)), \
         patch.object(logsink, '_open_connection', lambda: _NullConn()):
        await logsink.on_result_accepted(row)
    assert backfills == []  # 已在范围：不重复提交补录


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
         patch.object(logsink, '_schedule_backfill', lambda *a, **k: None), \
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
