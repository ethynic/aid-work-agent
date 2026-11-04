"""Manual summary admission accepts one trusted today's scope."""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.wecom_conversation import api, plans, service


class Request:
    headers = {'Authorization': 'Bearer test'}

    def __init__(self, body):
        self.body = body

    async def json(self):
        return self.body


@pytest.mark.asyncio
@pytest.mark.parametrize('field', ['plan_task_id', 'business_date', 'device_id',
                                          'account', 'timezone', 'profile_id', 'instance_id'])
async def test_manual_rejects_client_selected_source_and_old_plan_fields(field):
    with patch.object(api, '_current_user_and_tenant', new_callable=AsyncMock,
                      return_value=('tenant-a', 'user-a')), \
         patch.object(api, '_directory_device', new_callable=AsyncMock) as directory, \
         patch.object(api.plans, 'submit_manual_report') as submit, \
         patch.object(api, '_err', side_effect=lambda status, message, code: {'code': code}):
        result = await api.generate_daily_report(Request({'conversation_ids': [1], field: 'forged'}))
    assert result == {'code': 'VALIDATION_FAILED'}
    directory.assert_not_called()
    submit.assert_not_called()


@pytest.mark.asyncio
async def test_sixty_selected_ids_submit_as_one_runner_with_verified_source():
    ids = list(range(1, 61))
    tool = MagicMock()
    tool._find_ready_device = AsyncMock(return_value=({'id': 'device-a'}, None))
    with patch.object(api, '_current_user_and_tenant', new_callable=AsyncMock,
                      return_value=('tenant-a', 'user-a')), \
         patch.object(api, '_directory_device', new_callable=AsyncMock,
                      return_value=(tool, {'id': 'device-a'}, None)), \
         patch.object(api, '_directory_call', new_callable=AsyncMock,
                      return_value=({'self_user_id': 'account-a'}, None)), \
         patch.object(service, 'report_conversations', return_value=ids) as scope, \
         patch.object(api.plans, 'submit_manual_report', new_callable=AsyncMock,
                      return_value={'runner_id': 'runner-one'}) as submit, \
         patch.object(api, '_ok', side_effect=lambda value: value):
        result = await api.generate_daily_report(Request({'conversation_ids': ids}))
    assert result['runner_id'] == 'runner-one'
    scope.assert_called_once()
    assert scope.call_args.args[2] == ids
    assert scope.call_args.kwargs == {'account': 'account-a', 'device_id': 'device-a'}
    submit.assert_awaited_once()
    assert submit.call_args.kwargs['conversation_ids'] == ids
    assert submit.call_args.kwargs['account'] == 'account-a'
    assert submit.call_args.kwargs['device_id'] == 'device-a'


def test_manual_scope_one_query_rejects_foreign_device():
    conn = MagicMock()
    cursor = conn.__enter__.return_value.cursor.return_value
    cursor.fetchall.return_value = [
        {'id': 1, 'source_account_id': 'account-a', 'device_id': 'device-a'},
        {'id': 2, 'source_account_id': 'account-a', 'device_id': 'device-b'}]
    with patch.object(service, 'get_db_connection', return_value=conn):
        with pytest.raises(service.ConversationServiceError) as error:
            service.report_conversations('tenant-a', 'user-a', [1, 2],
                                         account='account-a', device_id='device-a')
    assert error.value.code == 'PLAN_SOURCE_MISMATCH'
    assert cursor.execute.call_count == 1
    sql, params = cursor.execute.call_args.args
    assert 'tenant_id=%s AND user_id=%s AND id=ANY' in sql
    assert params == ('tenant-a', 'user-a', [1, 2])


@pytest.mark.asyncio
async def test_date_page_latest_generation_uses_accepted_runner_without_tail_result():
    """Queue cancellation must displace an older successful generation on the date page."""
    accepted = {'runner_id': 'runner-new', 'status': 'cancelled',
                'targets': [{'conversation_id': 1, 'status': 'not_executed'}]}
    with patch.object(api, '_current_user_and_tenant', new_callable=AsyncMock,
                      return_value=('tenant-a', 'user-a')), \
         patch.object(service, 'daily_summaries_for_date',
                      return_value={'reports': [], 'has_more': False, 'next_cursor': None}), \
         patch.object(service, 'list_daily_executions',
                      return_value={'executions': [{'runner_id': 'runner-new'}]}) as list_runs, \
         patch.object(service, 'get_daily_execution', return_value=accepted) as detail, \
         patch.object(service, 'last_daily_generation') as old_tail, \
         patch.object(api, '_ok', side_effect=lambda value: value):
        result = await api.daily_reports(Request({}), date(2026, 11, 4),
                                         task_id='task-one', cursor=None, limit=50)
    assert result['last_generation'] == accepted
    assert list_runs.call_args.kwargs['task_id'] == 'task-one'
    detail.assert_called_once_with('tenant-a', 'user-a', 'runner-new')
    old_tail.assert_not_called()


@pytest.mark.asyncio
async def test_manual_submission_keeps_sixty_ids_in_one_main_runner():
    """The real submission body must not split a large verified scope into tasks."""
    ids = list(range(1, 61))
    client = MagicMock()
    client.request = AsyncMock(return_value={
        'runner': {'runner_id': 'runner-one', 'status': 'queued'}, 'created': True})
    with patch.object(plans, '_ensure_session_committed') as ensure, \
         patch.object(plans, '_runner_client', return_value=client):
        submitted = await plans.submit_manual_report(
            'Bearer test', 'tenant-a', 'user-a', conversation_ids=ids,
            account='account-a', device_id='device-a', business_date='2026-11-04',
            client_request_id='request-one')
    assert submitted['runner_id'] == 'runner-one'
    ensure.assert_called_once()
    client.request.assert_awaited_once()
    method, path = client.request.call_args.args
    body = client.request.call_args.kwargs['body']
    assert (method, path) == ('POST', '/v1/runners')
    assert body['profile_id'] == 'main'
    assert body['request_data']['wecom_task_device'] == 'device-a'
    assert body['request_data'][plans.DESCRIPTOR_KEY] == {
        'schema_version': 2, 'kind': 'daily', 'trigger': 'manual',
        'business_date': '2026-11-04', 'timezone': 'Asia/Shanghai',
        'account': 'account-a', 'device': 'device-a', 'conversation_ids': ids}
