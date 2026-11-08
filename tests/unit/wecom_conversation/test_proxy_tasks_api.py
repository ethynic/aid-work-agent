"""代理任务 API 测试：就绪判定消费、能力闸门、受信调用失败不写脏数据。

沿目录 API 测试范式：mock 设备受信调用与服务层落库，核对编排顺序与拒绝路径。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.wecom_conversation import proxy_api, proxy_tasks

USER = {'user_id': 'user-1', 'role': 'user', 'tenant_id': 'tenant-1'}


class Request:
    def __init__(self, body=None):
        self.body = body or {}

    async def json(self):
        return self.body


def _err_stub(status, message, code, field_errors=None):
    return {'code': code, 'message': message, 'field_errors': field_errors or []}


def _ok_stub(value, status_code=200):
    return {'ok': value, 'status': status_code}


def _doctor_data(**overrides):
    data = {'schema_version': 1, 'success': True, 'checks': [],
            'runtime_readiness': {'ready': True, 'reason': ''},
            'localdb': {'success': True, 'steps': [], 'localdb_ready': True,
                        'mode_decision': {'mode': 'db', 'kind': None, 'version': 'v'},
                        'ui_ready': {'ready': True}, 'state_saved': True, 'guidance': []},
            'self_user_id': 'account-a'}
    data.update(overrides)
    return data


def _calibrate_data(**overrides):
    data = {'schema_version': 1, 'self_user_id': 'account-a',
            'title_point': {'x': 1310, 'y': 218}, 'input_point': {'x': 1310, 'y': 976},
            'width': 2560, 'height': 1440, 'dpi': 96}
    data.update(overrides)
    return data


# ==================== 就绪判定（契约 §2 服务端消费规则） ====================

@pytest.mark.parametrize('overrides,fragment', [
    ({'success': False}, 'success=false'),
    ({'localdb': {'localdb_ready': False, 'mode_decision': {'mode': 'db'}}}, 'localdb_ready'),
    ({'localdb': {'localdb_ready': True, 'mode_decision': {'mode': 'ui'}}}, 'mode'),
    ({'runtime_readiness': {'ready': False}}, 'runtime_readiness'),
    ({'self_user_id': ''}, 'self_user_id'),
])
def test_readiness_rejects_each_doctor_failure(overrides, fragment):
    problems = proxy_tasks.evaluate_creation_readiness(_doctor_data(**overrides),
                                                       _calibrate_data())
    assert len(problems) == 1 and fragment in problems[0]


@pytest.mark.parametrize('overrides,fragment', [
    ({'title_point': None}, '标题点'),
    ({'input_point': {'x': 1.5, 'y': 2}}, '输入点'),
    ({'self_user_id': ''}, '标定未确认'),
    ({'self_user_id': 'account-b'}, '不一致'),
])
def test_readiness_rejects_each_calibrate_failure(overrides, fragment):
    problems = proxy_tasks.evaluate_creation_readiness(_doctor_data(),
                                                       _calibrate_data(**overrides))
    assert len(problems) == 1 and fragment in problems[0]


def test_readiness_rejects_invalid_structures():
    assert proxy_tasks.evaluate_creation_readiness(None, _calibrate_data())
    assert proxy_tasks.evaluate_creation_readiness(_doctor_data(), {'schema_version': 2})
    assert proxy_tasks.evaluate_creation_readiness(_doctor_data(), _calibrate_data()) == []


# ==================== 创建：能力闸门与受信检查 ====================

@pytest.mark.asyncio
async def test_create_requires_config_capabilities_before_dispatch():
    device = {'id': 'device-1', 'capabilities_json': {'providers': ['wecom'],
                                                      'capabilities': ['wecom_read_session']}}
    with patch('src.local_tools.proxy_tool.WecomConfigDoctorTool._find_ready_device',
               AsyncMock(return_value=(device, None))), \
         patch('src.local_tools.proxy_tool.WecomConfigDoctorTool._dispatch_and_wait') as dispatch, \
         patch.object(proxy_api, '_err', _err_stub):
        _, error = await proxy_api._config_device('tenant-1', 'user-1')
    assert error['code'] == 'PROXY_CLIENT_UPGRADE'
    dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_create_maps_upgrade_code_when_device_lacks_tool_at_runtime():
    tool = MagicMock()
    tool._dispatch_and_wait = AsyncMock(return_value={
        'success': False, 'code': 'TOOL_NOT_SUPPORTED', 'message': '未支持', 'data': None})
    with patch.object(proxy_api, '_err', _err_stub):
        _, error = await proxy_api._config_call(tool, 'tenant-1', 'user-1', {'id': 'd1'})
    assert error['code'] == 'PROXY_CLIENT_UPGRADE'
    assert '客户端需升级' in error['message']


@pytest.mark.asyncio
async def test_create_rejects_forged_agent_before_any_device_call():
    with patch.object(proxy_api, '_current_user_context', AsyncMock(
            return_value=('tenant-1', 'user-1', USER))), \
         patch.object(proxy_api, '_agent_allowed', AsyncMock(return_value=False)), \
         patch.object(proxy_api, '_config_device', AsyncMock()) as device_gate, \
         patch.object(proxy_api, '_err', _err_stub):
        result = await proxy_api.create_proxy_task(Request(
            {'title': 'T', 'instruction': 'I', 'agent_id': 'agent-x', 'sessions': []}))
    assert result['code'] == 'AGENT_NOT_ALLOWED'
    device_gate.assert_not_called()


@pytest.mark.asyncio
async def test_create_rejects_unready_report_without_saving():
    doctor = _doctor_data(localdb={'localdb_ready': False, 'mode_decision': {'mode': 'ui'}})
    with patch.object(proxy_api, '_current_user_context', AsyncMock(
            return_value=('tenant-1', 'user-1', USER))), \
         patch.object(proxy_api, '_agent_allowed', AsyncMock(return_value=True)), \
         patch.object(proxy_api, '_config_device', AsyncMock(
             return_value=({'id': 'device-a'}, None))), \
         patch.object(proxy_api, '_config_call', AsyncMock(
             side_effect=[(doctor, None), (_calibrate_data(), None)])), \
         patch.object(proxy_tasks, 'create_task') as create, \
         patch.object(proxy_api, '_err', _err_stub):
        result = await proxy_api.create_proxy_task(Request(
            {'title': 'T', 'instruction': 'I', 'agent_id': 'agent-a',
             'sessions': ['ws1.x']}))
    assert result['code'] == 'PROXY_NOT_READY'
    assert any('localdb_ready' in item for item in result['field_errors'])
    create.assert_not_called()


@pytest.mark.asyncio
async def test_create_failure_report_keeps_structured_diagnostics():
    """检查未完成（success=false＋steps＋guidance）时返回结构化诊断而非 invocation 错误。"""
    doctor = _doctor_data(success=False)
    with patch.object(proxy_api, '_current_user_context', AsyncMock(
            return_value=('tenant-1', 'user-1', USER))), \
         patch.object(proxy_api, '_agent_allowed', AsyncMock(return_value=True)), \
         patch.object(proxy_api, '_config_device', AsyncMock(
             return_value=({'id': 'device-a'}, None))), \
         patch.object(proxy_api, '_config_call', AsyncMock(
             side_effect=[(doctor, None), (_calibrate_data(), None)])), \
         patch.object(proxy_tasks, 'create_task') as create, \
         patch.object(proxy_api, '_err', _err_stub):
        result = await proxy_api.create_proxy_task(Request(
            {'title': 'T', 'instruction': 'I', 'agent_id': 'agent-a', 'sessions': ['ws1.x']}))
    assert result['code'] == 'PROXY_NOT_READY'
    assert any('success=false' in item for item in result['field_errors'])
    create.assert_not_called()


@pytest.mark.asyncio
async def test_create_rejects_client_source_fields_before_device_gate():
    """浏览器不得指定设备或账号：来源仅取当前选定设备与受信检查核定账号。"""
    with patch.object(proxy_api, '_current_user_context', AsyncMock(
            return_value=('tenant-1', 'user-1', USER))), \
         patch.object(proxy_api, '_agent_allowed', AsyncMock(return_value=True)), \
         patch.object(proxy_api, '_config_device', AsyncMock()) as device_gate, \
         patch.object(proxy_tasks, 'create_task') as create, \
         patch.object(proxy_api, '_err', _err_stub):
        result = await proxy_api.create_proxy_task(Request(
            {'title': 'T', 'instruction': 'I', 'agent_id': 'agent-a', 'sessions': ['ws1.x'],
             'device_id': 'forged', 'account': 'forged'}))
    assert result['code'] == 'VALIDATION_FAILED'
    device_gate.assert_not_called()
    create.assert_not_called()


@pytest.mark.asyncio
async def test_create_passes_trusted_account_and_selected_device_to_service():
    created = {'id': 1, 'status': 'stopped'}
    with patch.object(proxy_api, '_current_user_context', AsyncMock(
            return_value=('tenant-1', 'user-1', USER))), \
         patch.object(proxy_api, '_agent_allowed', AsyncMock(return_value=True)), \
         patch.object(proxy_api, '_config_device', AsyncMock(
             return_value=({'id': 'device-a'}, None))), \
         patch.object(proxy_api, '_config_call', AsyncMock(
             side_effect=[(_doctor_data(), None), (_calibrate_data(), None)])), \
         patch.object(proxy_tasks, 'create_task', return_value=created) as create, \
         patch.object(proxy_api, '_err', _err_stub), \
         patch.object(proxy_api, '_ok', _ok_stub):
        result = await proxy_api.create_proxy_task(Request(
            {'title': 'T', 'instruction': 'I', 'agent_id': 'agent-a', 'sessions': ['ws1.x']}))
    assert result['ok'] == {'task': created}
    kwargs = create.call_args.kwargs
    assert kwargs['device_id'] == 'device-a'  # 来源取当前选定设备，不取请求字段
    assert kwargs['account'] == 'account-a' and kwargs['self_user_id'] == 'account-a'


# ==================== 启动：运行条件核对与基线读取失败不落库 ====================

@pytest.mark.asyncio
async def test_task_device_rejects_revoked_device_before_dispatch():
    """设备非 active（解绑/撤销）在派发前明确拒绝，不空等到超时。"""
    device = {'id': 'device-9', 'status': 'revoked', 'selected': False,
              'capabilities_json': {'providers': ['wecom'],
                                    'capabilities': ['wecom_read_sessions']}}
    with patch('src.local_tools.repository.list_devices', return_value=[device]), \
         patch.object(proxy_api, '_err', _err_stub):
        _, error = await proxy_api._task_device('tenant-1', 'user-1', 'device-9',
                                                ('wecom_read_sessions',))
    assert error['code'] == 'DEVICE_UNAVAILABLE'


@pytest.mark.asyncio
async def test_start_device_offline_blocks_before_any_read():
    with patch.object(proxy_api, '_current_user_context', AsyncMock(
            return_value=('tenant-1', 'user-1', USER))), \
         patch.object(proxy_api, '_agent_allowed', AsyncMock(return_value=True)), \
         patch.object(proxy_tasks, 'get_task', return_value={
             'id': 1, 'status': 'stopped', 'agent_id': 'agent-a', 'device_id': 'device-a',
             'targets': []}), \
         patch.object(proxy_tasks, 'start_prep', return_value=(
             {'id': 1, 'device_id': 'device-a'}, [], [])), \
         patch.object(proxy_api, '_task_device', AsyncMock(
             return_value=(None, _err_stub(409, '离线', 'DEVICE_OFFLINE')))), \
         patch.object(proxy_api, '_baseline_read', AsyncMock()) as baseline, \
         patch.object(proxy_tasks, 'complete_start') as complete, \
         patch.object(proxy_api, '_err', _err_stub):
        result = await proxy_api.start_proxy_task(1, Request())
    assert result['code'] == 'DEVICE_OFFLINE'
    baseline.assert_not_called()
    complete.assert_not_called()


@pytest.mark.asyncio
async def test_start_failed_baseline_read_writes_nothing():
    with patch.object(proxy_api, '_current_user_context', AsyncMock(
            return_value=('tenant-1', 'user-1', USER))), \
         patch.object(proxy_api, '_agent_allowed', AsyncMock(return_value=True)), \
         patch.object(proxy_tasks, 'get_task', return_value={
             'id': 1, 'status': 'stopped', 'agent_id': 'agent-a'}), \
         patch.object(proxy_tasks, 'start_prep', return_value=(
             {'id': 1, 'device_id': 'device-a'},
             [{'id': 11, 'session': 'ws1.a', 'wb1_cursor': None}], [])), \
         patch.object(proxy_api, '_task_device', AsyncMock(
             return_value=({'id': 'device-a'}, None))), \
         patch.object(proxy_api, '_baseline_read', AsyncMock(
             return_value=(None, _err_stub(409, '读取失败', 'BUSY')))), \
         patch.object(proxy_tasks, 'complete_start') as complete, \
         patch.object(proxy_api, '_err', _err_stub):
        result = await proxy_api.start_proxy_task(1, Request())
    assert result['code'] == 'BUSY'
    complete.assert_not_called()  # 受信读取失败：不翻转状态、不写基线


@pytest.mark.asyncio
async def test_start_probes_identity_when_all_baselines_exist():
    with patch.object(proxy_api, '_current_user_context', AsyncMock(
            return_value=('tenant-1', 'user-1', USER))), \
         patch.object(proxy_api, '_agent_allowed', AsyncMock(return_value=True)), \
         patch.object(proxy_tasks, 'get_task', return_value={
             'id': 1, 'status': 'stopped', 'agent_id': 'agent-a'}), \
         patch.object(proxy_tasks, 'start_prep', return_value=(
             {'id': 1, 'device_id': 'device-a'},
             [{'id': 11, 'session': 'ws1.a', 'wb1_cursor': 'wb1.x'}], [])), \
         patch.object(proxy_api, '_task_device', AsyncMock(
             return_value=({'id': 'device-a'}, None))), \
         patch.object(proxy_api, '_baseline_read', AsyncMock()) as baseline, \
         patch.object(proxy_api, '_identity_probe', AsyncMock(
             return_value=('account-a', None))) as probe, \
         patch.object(proxy_tasks, 'complete_start',
                      MagicMock(return_value={'id': 1, 'status': 'running'})) as complete, \
         patch.object(proxy_api, '_err', _err_stub), \
         patch.object(proxy_api, '_ok', _ok_stub):
        result = await proxy_api.start_proxy_task(1, Request())
    baseline.assert_not_called()  # 已有基线不重读
    probe.assert_awaited_once()
    assert complete.call_args.kwargs['self_user_id'] == 'account-a'
    assert complete.call_args.kwargs['baselines'] == {}
    assert result['ok']['task']['status'] == 'running'


@pytest.mark.asyncio
async def test_start_conflict_reports_owner_before_device_gate():
    with patch.object(proxy_api, '_current_user_context', AsyncMock(
            return_value=('tenant-1', 'user-1', USER))), \
         patch.object(proxy_api, '_agent_allowed', AsyncMock(return_value=True)), \
         patch.object(proxy_tasks, 'get_task', return_value={
             'id': 2, 'status': 'stopped', 'agent_id': 'agent-a'}), \
         patch.object(proxy_tasks, 'start_prep', return_value=(
             {'id': 2}, [], [{'task_id': 1, 'title': 'A', 'session': 'ws1.a'}])), \
         patch.object(proxy_api, '_task_device', AsyncMock()) as device_gate, \
         patch.object(proxy_api, '_err', _err_stub):
        result = await proxy_api.start_proxy_task(2, Request())
    assert result['code'] == 'PROXY_SESSION_OWNED'
    device_gate.assert_not_called()


@pytest.mark.asyncio
async def test_start_rejects_revoked_agent_without_touching_state():
    with patch.object(proxy_api, '_current_user_context', AsyncMock(
            return_value=('tenant-1', 'user-1', USER))), \
         patch.object(proxy_api, '_agent_allowed', AsyncMock(return_value=False)), \
         patch.object(proxy_tasks, 'get_task', return_value={
             'id': 1, 'status': 'stopped', 'agent_id': 'agent-a'}), \
         patch.object(proxy_tasks, 'start_prep') as prep, \
         patch.object(proxy_api, '_err', _err_stub):
        result = await proxy_api.start_proxy_task(1, Request())
    assert result['code'] == 'AGENT_NOT_ALLOWED'
    prep.assert_not_called()


# ==================== 编辑：新增目标经受信读取，账号不符不写 ====================

@pytest.mark.asyncio
async def test_edit_rejects_invalid_body_fields():
    with patch.object(proxy_api, '_current_user_and_tenant', AsyncMock(
            return_value=('tenant-1', 'user-1'))), \
         patch.object(proxy_api, '_err', _err_stub):
        result = await proxy_api.edit_proxy_task(1, Request({'status': 'running'}))
    assert result['code'] == 'VALIDATION_FAILED'


@pytest.mark.asyncio
async def test_edit_add_target_account_mismatch_writes_nothing():
    baseline = {'cursor': 'wb1.a', 'generation': 'gen-1', 'watermark': 5,
                'self_user_id': 'account-b'}
    with patch.object(proxy_api, '_current_user_and_tenant', AsyncMock(
            return_value=('tenant-1', 'user-1'))), \
         patch.object(proxy_tasks, 'get_task', return_value={
             'id': 1, 'status': 'stopped', 'device_id': 'device-a',
             'wecom_account_id': 'account-a'}), \
         patch.object(proxy_api, '_task_device', AsyncMock(
             return_value=({'id': 'device-a'}, None))), \
         patch.object(proxy_api, '_baseline_read', AsyncMock(return_value=(baseline, None))), \
         patch.object(proxy_tasks, 'edit_stopped_task') as edit, \
         patch.object(proxy_api, '_err', _err_stub):
        result = await proxy_api.edit_proxy_task(1, Request({'add_sessions': ['ws1.new']}))
    assert result['code'] == 'PROXY_ACCOUNT_MISMATCH'
    edit.assert_not_called()


@pytest.mark.asyncio
async def test_edit_add_target_passes_join_baseline_to_service():
    baseline = {'cursor': 'wb1.a', 'generation': 'gen-1', 'watermark': 5,
                'self_user_id': 'account-a'}
    with patch.object(proxy_api, '_current_user_and_tenant', AsyncMock(
            return_value=('tenant-1', 'user-1'))), \
         patch.object(proxy_tasks, 'get_task', return_value={
             'id': 1, 'status': 'stopped', 'device_id': 'device-a',
             'wecom_account_id': 'account-a'}), \
         patch.object(proxy_api, '_task_device', AsyncMock(
             return_value=({'id': 'device-a'}, None))), \
         patch.object(proxy_api, '_baseline_read', AsyncMock(return_value=(baseline, None))), \
         patch.object(proxy_tasks, 'edit_stopped_task',
                      return_value={'id': 1, 'targets': []}) as edit, \
         patch.object(proxy_api, '_err', _err_stub), \
         patch.object(proxy_api, '_ok', _ok_stub):
        await proxy_api.edit_proxy_task(1, Request({'add_sessions': ['ws1.new']}))
    kwargs = edit.call_args.kwargs
    assert kwargs['add_baselines']['ws1.new']['cursor'] == 'wb1.a'


@pytest.mark.asyncio
async def test_edit_without_add_sessions_skips_device_entirely():
    with patch.object(proxy_api, '_current_user_and_tenant', AsyncMock(
            return_value=('tenant-1', 'user-1'))), \
         patch.object(proxy_tasks, 'get_task', return_value={
             'id': 1, 'status': 'stopped', 'device_id': 'device-a'}), \
         patch.object(proxy_api, '_task_device', AsyncMock()) as device_gate, \
         patch.object(proxy_tasks, 'edit_stopped_task',
                      return_value={'id': 1}) as edit, \
         patch.object(proxy_api, '_err', _err_stub), \
         patch.object(proxy_api, '_ok', _ok_stub):
        await proxy_api.edit_proxy_task(1, Request({'title': '新标题'}))
    device_gate.assert_not_called()  # 纯标题/指令编辑不触设备
    assert edit.call_args.kwargs['title'] == '新标题'


# ==================== 停止 / 软删除 / 列表 / 详情 ====================

@pytest.mark.asyncio
async def test_stop_and_delete_delegate_to_service_with_owner_scope():
    with patch.object(proxy_api, '_current_user_and_tenant', AsyncMock(
            return_value=('tenant-1', 'user-1'))) as ctx, \
         patch.object(proxy_tasks, 'request_stop',
                      return_value={'id': 1, 'status': 'stopped'}) as stop, \
         patch.object(proxy_tasks, 'soft_delete_task',
                      return_value={'id': 1, 'status': 'deleted'}) as delete, \
         patch.object(proxy_api, '_ok', _ok_stub):
        await proxy_api.stop_proxy_task(1, Request())
        await proxy_api.delete_proxy_task(1, Request())
    assert ctx.await_count == 2
    assert stop.call_args.args == ('tenant-1', 'user-1', 1)
    assert delete.call_args.args == ('tenant-1', 'user-1', 1)


@pytest.mark.asyncio
async def test_list_and_detail_delegate_with_tenant_scope():
    with patch.object(proxy_api, '_current_user_and_tenant', AsyncMock(
            return_value=('tenant-1', 'user-1'))), \
         patch.object(proxy_tasks, 'list_tasks', return_value=[{'id': 1}]) as listing, \
         patch.object(proxy_tasks, 'get_task_detail', return_value=None), \
         patch.object(proxy_api, '_ok', _ok_stub), \
         patch.object(proxy_api, '_err', _err_stub):
        listed = await proxy_api.list_proxy_tasks(Request(), include_deleted=True, limit=5,
                                                  offset=1)
        detail = await proxy_api.get_proxy_task_detail(9, Request())
    assert listed['ok'] == {'tasks': [{'id': 1}]}
    assert listing.call_args.kwargs['include_deleted'] is True
    assert detail['code'] == 'PROXY_NOT_FOUND'
