"""Picker additions verify every selected target before any business row is written."""
import base64
import json
from unittest.mock import patch

import pytest

from src.wecom_conversation import api


def locator(number):
    value = {'account': 'account-1', 'conversation_id': f'R:group-{number}'}
    return 'ws1.' + base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')


class Request:
    def __init__(self, **expected):
        self.expected = expected

    async def json(self):
        return {'sessions': [locator(i) for i in range(60)], **self.expected}


@pytest.mark.asyncio
async def test_old_selected_device_without_directory_capability_does_not_enqueue():
    device = {'id': 'device-1', 'capabilities_json': {'providers': ['wecom'],
                                                     'capabilities': ['wecom_read_session']}}
    with patch('src.local_tools.proxy_tool.WecomSessionDirectoryTool._find_ready_device',
               return_value=(device, None)), \
         patch('src.local_tools.proxy_tool.WecomSessionDirectoryTool._dispatch_and_wait') as dispatch, \
         patch.object(api, '_err', lambda status, message, code: {'code': code}):
        _, selected, error = await api._directory_device('tenant-1', 'user-1')
    assert selected is None and error == {'code': 'TOOL_NOT_SUPPORTED'}
    dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_add_many_uses_bounded_resolution_but_one_atomic_write():
    calls = []
    writes = []

    async def directory(tool, tenant, user, device, arguments):
        calls.append(arguments['sessions'])
        return {'schema_version': 1, 'self_user_id': 'account-1', 'source_generation': 'generation-1',
                'resolved_targets': [{'session': session, 'name': '同名', 'kind': 'group',
                                      'peer_platform': 'unknown'} for session in arguments['sessions']]}, None

    tool = type('Tool', (), {'_find_ready_device': lambda self, *args: selected_device()})()
    async def selected_device():
        return {'id': 'device-1'}, None
    with patch.object(api, '_current_user_and_tenant', return_value=('tenant-1', 'user-1')), \
         patch.object(api, '_directory_device', return_value=(tool, {'id': 'device-1'}, None)), \
         patch.object(api, '_directory_call', directory), \
         patch.object(api.service, 'add_verified_conversations',
                      lambda *args, **kwargs: writes.append((args, kwargs)) or []), \
         patch.object(api, '_ok', lambda value: value):
        result = await api.add_local_conversations(Request())
    assert [len(chunk) for chunk in calls] == [50, 10]
    assert len(writes) == 1 and len(writes[0][0][2]) == 60
    assert result['added_count'] == 0


@pytest.mark.asyncio
async def test_failed_late_resolution_never_writes_partial_scope():
    writes = []
    tool = type('Tool', (), {'_find_ready_device': lambda self, *args: selected_device()})()
    async def selected_device():
        return {'id': 'device-1'}, None
    async def directory(tool, tenant, user, device, arguments):
        if arguments['sessions'][0] == locator(50):
            return None, {'error': 'account changed'}
        return {'schema_version': 1, 'self_user_id': 'account-1', 'source_generation': 'generation-1',
                'resolved_targets': [{'session': item} for item in arguments['sessions']]}, None
    with patch.object(api, '_current_user_and_tenant', return_value=('tenant-1', 'user-1')), \
         patch.object(api, '_directory_device', return_value=(tool, {'id': 'device-1'}, None)), \
         patch.object(api, '_directory_call', directory), \
         patch.object(api.service, 'add_verified_conversations', lambda *args, **kwargs: writes.append(args)):
        result = await api.add_local_conversations(Request())
    assert result == {'error': 'account changed'}
    assert writes == []


@pytest.mark.asyncio
async def test_add_rejects_device_changed_since_picker_before_resolve():
    with patch.object(api, '_current_user_and_tenant', return_value=('tenant-1', 'user-1')), \
         patch.object(api, '_directory_device', return_value=(object(), {'id': 'device-b'}, None)), \
         patch.object(api, '_directory_call') as resolve, \
         patch.object(api.service, 'add_verified_conversations') as write, \
         patch.object(api, '_err', lambda status, message, code: {'code': code}):
        result = await api.add_local_conversations(Request(expected_device_id='device-a',
                                                           expected_source_generation='generation-1'))
    assert result == {'code': 'DEVICE_MISMATCH'}
    resolve.assert_not_called()
    write.assert_not_called()


@pytest.mark.asyncio
async def test_add_rejects_source_generation_changed_since_picker_without_writing():
    async def selected_device():
        return {'id': 'device-a'}, None

    tool = type('Tool', (), {'_find_ready_device': lambda self, *args: selected_device()})()
    async def resolve(*args):
        return {'schema_version': 1, 'self_user_id': 'account-1', 'source_generation': 'generation-2',
                'resolved_targets': [{'session': item} for item in args[-1]['sessions']]}, None

    with patch.object(api, '_current_user_and_tenant', return_value=('tenant-1', 'user-1')), \
         patch.object(api, '_directory_device', return_value=(tool, {'id': 'device-a'}, None)), \
         patch.object(api, '_directory_call', resolve), \
         patch.object(api.service, 'add_verified_conversations') as write, \
         patch.object(api, '_err', lambda status, message, code: {'code': code}):
        result = await api.add_local_conversations(Request(expected_device_id='device-a',
                                                           expected_source_generation='generation-1'))
    assert result == {'code': 'IDENTITY_UNCONFIRMED'}
    write.assert_not_called()
