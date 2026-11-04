"""Dedicated plan API trusts selected device and local account, not request fields."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.wecom_conversation import api, plans


class Request:
    def __init__(self, body):
        self.body = body

    async def json(self):
        return self.body


def _tool(selected_id):
    tool = MagicMock()
    tool._find_ready_device = AsyncMock(return_value=({'id': selected_id}, None))
    return tool


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value', [('device_id', 'forged-device'),
                                          ('account', 'forged-account')])
async def test_plan_write_rejects_client_source_fields_before_directory(field, value):
    """浏览器不得替换目录核验出的设备或企微账号。"""
    body = {'target_day': 'today', 'scope_mode': 'all',
            'interval_hours': 2, field: value}
    with patch.object(api, '_current_user_and_tenant', new_callable=AsyncMock,
                      return_value=('tenant-1', 'user-1')), \
         patch.object(api, '_directory_device', new_callable=AsyncMock) as directory, \
         patch.object(plans, 'save_daily_plan') as save, \
         patch.object(api, '_err', side_effect=lambda status, message, code: {'code': code}):
        result = await api._write_daily_plan(Request(body), create=True)
    assert result == {'code': 'VALIDATION_FAILED'}
    directory.assert_not_called()
    save.assert_not_called()


@pytest.mark.asyncio
async def test_plan_write_stops_when_selected_device_changes_after_directory():
    """目录响应返回后设备选择变化时，不用旧来源创建计划。"""
    tool = _tool('device-b')
    with patch.object(api, '_current_user_and_tenant', new_callable=AsyncMock,
                      return_value=('tenant-1', 'user-1')), \
         patch.object(api, '_directory_device', new_callable=AsyncMock,
                      return_value=(tool, {'id': 'device-a'}, None)), \
         patch.object(api, '_directory_call', new_callable=AsyncMock,
                      return_value=({'self_user_id': 'account-a'}, None)), \
         patch.object(plans, 'save_daily_plan') as save, \
         patch.object(api, '_err', side_effect=lambda status, message, code: {'code': code}):
        result = await api._write_daily_plan(
            Request({'target_day': 'today', 'scope_mode': 'all', 'interval_hours': 2}),
            create=True)
    assert result == {'code': 'DEVICE_MISMATCH'}
    save.assert_not_called()


@pytest.mark.asyncio
async def test_resume_rejects_changed_local_account_before_dal_resume():
    """暂停计划只能在原账号和原设备恢复，不能用新目录账号消费旧范围。"""
    prompt = plans.plan_prompt('daily', plans.daily_plan_config(
        account='account-a', device='device-a', target_day='today', scope_mode='all'))
    tool = _tool('device-a')
    with patch.object(api, '_current_user_and_tenant', new_callable=AsyncMock,
                      return_value=('tenant-1', 'user-1')), \
         patch.object(plans, 'get_daily_plan', return_value={
             'task_id': 'sched-one', 'task_prompt': prompt, 'status': 'paused'}), \
         patch.object(api, '_directory_device', new_callable=AsyncMock,
                      return_value=(tool, {'id': 'device-a'}, None)), \
         patch.object(api, '_directory_call', new_callable=AsyncMock,
                      return_value=({'self_user_id': 'account-b'}, None)), \
         patch('src.scheduler.db.ScheduledTaskDB.resume_recompute') as resume, \
         patch.object(api, '_err', side_effect=lambda status, message, code: {'code': code}):
        result = await api.resume_daily_plan(Request({}))
    assert result == {'code': 'PLAN_SOURCE_MISMATCH'}
    resume.assert_not_called()


@pytest.mark.asyncio
async def test_plan_write_passes_only_directory_account_and_selected_device_to_service():
    """来源身份仅来自受信目录和当前选定设备。"""
    tool = _tool('device-a')
    with patch.object(api, '_current_user_and_tenant', new_callable=AsyncMock,
                      return_value=('tenant-1', 'user-1')), \
         patch.object(api, '_directory_device', new_callable=AsyncMock,
                      return_value=(tool, {'id': 'device-a'}, None)), \
         patch.object(api, '_directory_call', new_callable=AsyncMock,
                      return_value=({'self_user_id': 'account-a'}, None)), \
         patch.object(plans, 'save_daily_plan', return_value={
             'task_id': 'sched-one', 'task_prompt': 'prompt'}) as save, \
         patch.object(api, '_ok', side_effect=lambda value, **kwargs: value):
        await api._write_daily_plan(
            Request({'target_day': 'today', 'scope_mode': 'all', 'interval_hours': 2}),
            create=True)
    assert save.call_args.kwargs['account'] == 'account-a'
    assert save.call_args.kwargs['device'] == 'device-a'
