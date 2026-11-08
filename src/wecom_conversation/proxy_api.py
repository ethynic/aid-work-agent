"""企微代理任务配置 API（代理任务契约 §2/§3/§4，`/api/wecom-proxy-tasks` 前缀）。

沿 `/api/wecom-conversations` 的认证与错误处理风格：只接受当前认证用户并核对
租户归属；服务端受信 local-tool invocation 沿 D1 页面目录先例
（`_directory_device`/`_directory_call`），配置检查与水位读取不进模型工具集。
监控循环属 P5、回答 Runner 属 P6，本模块不做。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Query, Request

from src.session_tasks.api import _current_user_and_tenant, _err, _from_service_error, _ok
from src.session_tasks.constants import SessionTaskError

from . import proxy_tasks, service

router = APIRouter(prefix="/api/wecom-proxy-tasks", tags=["wecom-proxy-tasks"])

# 本地执行失败码中代表"客户端缺少该工具能力"的子集（沿目录先例映射为需升级）
_CLIENT_UPGRADE_CODES = ('TOOL_NOT_SUPPORTED', 'TOOL_NOT_ALLOWED', 'LOCAL_DEVICE_UNSUPPORTED')


def _service_error(exc: service.ConversationServiceError):
    return _err(exc.status, str(exc), exc.code)


async def _current_user_context(request: Request):
    """当前认证用户与租户（含用户信息，供智能体权限核对）。"""
    from src.session_tasks.api import _get_current_user_sync

    user = await asyncio.to_thread(_get_current_user_sync, request)
    tenant_id = getattr(request.state, 'tenant_id', None)
    if not tenant_id:
        raise SessionTaskError('缺少租户上下文', 'VALIDATION_FAILED', 400)
    user_id = user.get('user_id') or user.get('id')
    if not user_id:
        raise SessionTaskError('无法识别当前用户', 'UNAUTHORIZED', 401)
    return tenant_id, str(user_id), user


async def _agent_allowed(user, agent_id):
    from src.saas.permissions.checker import check_agent_access
    return await asyncio.to_thread(check_agent_access, agent_id, user)


def _missing_capabilities(device, required_tools):
    """设备心跳上报的能力串核对（契约 §3：能力串＝MCP 工具名）。"""
    capabilities = (device.get('capabilities_json') or {}).get('capabilities') or []
    return [name for name in required_tools if name not in capabilities] or None


async def _config_device(tenant_id, user_id):
    """创建检查设备闸门：当前选定设备＋配置工具能力（缺能力只禁创建，提示升级）。"""
    from src.local_tools.proxy_tool import WecomConfigDoctorTool

    tool = WecomConfigDoctorTool()
    device, error = await tool._find_ready_device(tenant_id, user_id)
    if device is None:
        if error and '不支持企业微信操作' in error:
            return None, _err(409, '当前设备缺少配置检查能力，请安装最新版 Runtime 和 CLI',
                              'PROXY_CLIENT_UPGRADE')
        return None, _err(409, error or '当前选定设备不可用', 'DEVICE_UNAVAILABLE')
    missing = _missing_capabilities(device, proxy_tasks.CONFIG_TOOL_NAMES)
    if missing:
        return None, _err(409, '当前设备缺少配置检查能力（'
                          + ', '.join(missing) + '），请安装最新版 Runtime 和 CLI',
                          'PROXY_CLIENT_UPGRADE')
    return device, None


def _doctor_tool():
    from src.local_tools.proxy_tool import WecomConfigDoctorTool
    return WecomConfigDoctorTool()


def _calibrate_tool():
    from src.local_tools.proxy_tool import WecomConfigCalibrateTool
    return WecomConfigCalibrateTool()


async def _config_call(tool, tenant_id, user_id, device):
    """受信配置 invocation（doctor/calibrate 无参固定执行）。"""
    result = await tool._dispatch_and_wait(tenant_id, user_id, None, device, {}, None)
    if not result.get('success'):
        code = result.get('code') or 'CONFIG_CHECK_FAILED'
        if code in _CLIENT_UPGRADE_CODES:
            return None, _err(409, '客户端需升级：当前设备缺少配置检查能力，'
                              '请安装最新版 Runtime 和 CLI', 'PROXY_CLIENT_UPGRADE')
        return None, _err(409, result.get('message') or '本机配置检查执行失败', code)
    data = result.get('data')
    if not isinstance(data, dict) or data.get('schema_version') != 1:
        return None, _err(409, '配置检查结果结构无效，请升级客户端后再试', 'PROXY_CLIENT_UPGRADE')
    return data, None


async def _task_device(tenant_id, user_id, device_id, required_tools):
    """任务固定设备闸门：active＋在线＋provider 覆盖＋能力串（不要求选定该设备）。"""
    from src.local_tools import repository
    from src.local_tools.device_policy import device_ready_error

    devices = await asyncio.to_thread(repository.list_devices, tenant_id, user_id)
    device = next((item for item in devices if str(item['id']) == str(device_id)), None)
    if device is None:
        return None, _err(409, '任务固定的设备已不存在或已解绑，请检查设备', 'DEVICE_UNAVAILABLE')
    ready_error = device_ready_error(device, required_tools[0], datetime.now(timezone.utc),
                                     require_selected=False)
    if ready_error == 'offline':
        return None, _err(409, '任务固定的设备当前离线，请保持本机 Runtime 在线后再试',
                          'DEVICE_OFFLINE')
    if ready_error == 'unsupported':
        return None, _err(409, '任务固定的设备缺少所需能力，请安装最新版 Runtime 和 CLI',
                          'PROXY_CAPABILITY_MISSING')
    if ready_error:
        # require_selected=False 下仅剩设备非 active（解绑/撤销）：及时明确拒绝，
        # 不让受信 invocation 派发到不可领取的设备后空等到超时
        return None, _err(409, '任务固定的设备已解绑或被撤销，请检查设备绑定状态',
                          'DEVICE_UNAVAILABLE')
    missing = _missing_capabilities(device, required_tools)
    if missing:
        return None, _err(409, '任务固定的设备缺少所需能力（' + ', '.join(missing)
                          + '），请安装最新版 Runtime 和 CLI', 'PROXY_CAPABILITY_MISSING')
    return device, None


async def _read_sessions_call(tenant_id, user_id, device, arguments):
    """受信监控读取（wecom_read_sessions 只读；与监控循环共用同设备串行占用）。"""
    from src.local_tools.proxy_tool import WecomReadSessionsTool

    result = await WecomReadSessionsTool()._dispatch_and_wait(
        tenant_id, user_id, None, device, arguments, None)
    if not result.get('success'):
        code = result.get('code') or 'PROXY_READ_FAILED'
        if code in _CLIENT_UPGRADE_CODES:
            return None, _err(409, '当前设备缺少监控读取能力，请安装最新版 Runtime 和 CLI',
                              'PROXY_CAPABILITY_MISSING')
        return None, _err(409, result.get('message') or '本地会话读取失败', code)
    data = result.get('data')
    if not isinstance(data, dict) or not data.get('self_user_id'):
        return None, _err(409, '本地读取结果不完整，请升级企业微信插件', 'PROXY_READ_INVALID')
    return data, None


async def _baseline_read(tenant_id, user_id, device, locator):
    """单目标无游标首读记水位（initial 基线/编辑加入共用；按 has_more 有界排空）。

    返回 ({'cursor','generation','watermark','self_user_id'}, None)；游标逐目标
    独立（单会话读取，与 P5 监控读同形状）。
    """
    cursor = None
    generation = None
    watermark = None
    self_user_id = None
    for _ in range(20):
        arguments = {'sessions': [locator], 'limit': 50}
        if cursor:
            arguments['cursor'] = cursor
        data, error = await _read_sessions_call(tenant_id, user_id, device, arguments)
        if error:
            return None, error
        # CLI 批量读对单目标失败仍返回总体成功＋failed 数组，且游标照常返回
        # （sessions.ts 结果组装路径）：建基线必须确认本目标真实读到，失败即拒绝，
        # 不保存游标/水位、不把该会话当已建基线启动
        failed = data.get('failed')
        if isinstance(failed, list) and failed:
            first = failed[0] if isinstance(failed[0], dict) else {}
            code = first.get('code') or 'PROXY_READ_FAILED'
            return None, _err(409, '目标会话本地读取未完成，未建立基线；请核对会话状态后重试', code)
        self_user_id = data.get('self_user_id')
        generation = data.get('source_generation')
        cursor = data.get('cursor')
        if not isinstance(cursor, str) or not cursor:
            return None, _err(409, '本地读取结果缺少游标，无法记录基线', 'PROXY_CURSOR_MISSING')
        value = proxy_tasks.session_watermark(data, locator)
        if value is not None and (watermark is None or value > watermark):
            watermark = value
        if data.get('has_more') is not True:
            break
    return {'cursor': cursor, 'generation': generation, 'watermark': watermark,
            'self_user_id': self_user_id}, None


async def _identity_probe(tenant_id, user_id, device, locator):
    """单页只读探测当前登录账号（启动运行条件核对；不落任何位置）。"""
    data, error = await _read_sessions_call(
        tenant_id, user_id, device, {'sessions': [locator], 'limit': 1})
    if error:
        return None, error
    return data.get('self_user_id'), None


# ==================== 创建 / 查询 ====================

@router.post('')
async def create_proxy_task(request: Request):
    """创建：先经受信真实执行 doctor＋标定，就绪判定全部满足才允许保存。"""
    try:
        tenant_id, user_id, user = await _current_user_context(request)
        body = await request.json()
        if not isinstance(body, dict) or set(body) - {
                'title', 'instruction', 'agent_id', 'sessions', 'profile_id', 'instance_id'}:
            return _err(400, '任务配置字段无效', 'VALIDATION_FAILED')
        agent_id = body.get('agent_id')
        if not await _agent_allowed(user, agent_id if isinstance(agent_id, str) else ''):
            return _err(409, '所选智能体不存在或当前用户无权使用', 'AGENT_NOT_ALLOWED')
        device, error = await _config_device(tenant_id, user_id)
        if error:
            return error
        doctor_data, error = await _config_call(_doctor_tool(), tenant_id, user_id, device)
        if error:
            return error
        calibrate_data, error = await _config_call(_calibrate_tool(), tenant_id, user_id, device)
        if error:
            return error
        problems = proxy_tasks.evaluate_creation_readiness(doctor_data, calibrate_data)
        if problems:
            return _err(409, '；'.join(problems), 'PROXY_NOT_READY', field_errors=problems)
        account = doctor_data['self_user_id']
        task = await asyncio.to_thread(
            proxy_tasks.create_task, tenant_id, user_id,
            device_id=str(device['id']), account=account, self_user_id=account,
            title=body.get('title'), agent_id=agent_id, instruction=body.get('instruction'),
            sessions=body.get('sessions'), profile_id=body.get('profile_id'),
            instance_id=body.get('instance_id'))
        return _ok({'task': task}, status_code=201)
    except service.ConversationServiceError as exc:
        return _service_error(exc)
    except SessionTaskError as exc:
        return _from_service_error(exc)


@router.get('')
async def list_proxy_tasks(request: Request, include_deleted: bool = False,
                           limit: int = Query(50, ge=1, le=200),
                           offset: int = Query(0, ge=0)):
    """任务列表（软删除历史带 include_deleted 只读可查）。"""
    try:
        tenant_id, user_id = await _current_user_and_tenant(request)
        rows = await asyncio.to_thread(proxy_tasks.list_tasks, tenant_id, user_id,
                                       include_deleted=include_deleted, limit=limit,
                                       offset=offset)
        return _ok({'tasks': rows})
    except SessionTaskError as exc:
        return _from_service_error(exc)


@router.get('/{task_id}')
async def get_proxy_task_detail(task_id: int, request: Request):
    """详情：配置＋每目标位置只读投影＋最近批次。"""
    try:
        tenant_id, user_id = await _current_user_and_tenant(request)
        task = await asyncio.to_thread(proxy_tasks.get_task_detail, tenant_id, user_id, task_id)
        if task is None:
            return _err(404, '代理任务不存在或无权访问', 'PROXY_NOT_FOUND')
        return _ok({'task': task})
    except SessionTaskError as exc:
        return _from_service_error(exc)


# ==================== 启动 / 停止 / 编辑 / 软删除 ====================

@router.post('/{task_id}/start')
async def start_proxy_task(task_id: int, request: Request):
    """启动：核对运行条件（不要求配置工具能力）；首次启动为 initial 目标建基线。"""
    try:
        tenant_id, user_id, user = await _current_user_context(request)
        task = await asyncio.to_thread(proxy_tasks.get_task, tenant_id, user_id, task_id,
                                       include_targets=True)
        if task is None:
            return _err(404, '代理任务不存在或无权访问', 'PROXY_NOT_FOUND')
        if task['status'] == proxy_tasks.TASK_DELETED:
            return _err(409, '任务已删除，不可启动', 'PROXY_TASK_DELETED')
        if not await _agent_allowed(user, task['agent_id']):
            return _err(409, '所选智能体已被删除或当前用户已无权使用，无法启动',
                        'AGENT_NOT_ALLOWED')
        task, targets, conflicts = await asyncio.to_thread(
            proxy_tasks.start_prep, tenant_id, user_id, task_id)
        if conflicts:
            owner = conflicts[0]['task_id']
            return _err(409, f'同一企微账号的会话已由运行中的任务 #{owner} 监控，'
                        '请先停止该任务或调整会话', 'PROXY_SESSION_OWNED')
        device, error = await _task_device(tenant_id, user_id, task['device_id'],
                                           proxy_tasks.RUNTIME_TOOL_NAMES)
        if error:
            return error
        pending = [item for item in targets if item['wb1_cursor'] is None]
        baselines = {}
        identity = None
        for target in pending:
            baseline, error = await _baseline_read(tenant_id, user_id, device, target['session'])
            if error:
                return error
            baselines[target['session']] = baseline
            identity = baseline['self_user_id']
        if identity is None and targets:
            identity, error = await _identity_probe(tenant_id, user_id, device, targets[0]['session'])
            if error:
                return error
        task = await asyncio.to_thread(
            proxy_tasks.complete_start, tenant_id, user_id, task_id,
            device_id=str(device['id']), self_user_id=identity, baselines=baselines)
        return _ok({'task': task})
    except service.ConversationServiceError as exc:
        return _service_error(exc)
    except SessionTaskError as exc:
        return _from_service_error(exc)


@router.post('/{task_id}/stop')
async def stop_proxy_task(task_id: int, request: Request):
    """停止：置 stop_requested；当前无执行时立即落 stopped（停止期间可编辑）。"""
    try:
        tenant_id, user_id = await _current_user_and_tenant(request)
        task = await asyncio.to_thread(proxy_tasks.request_stop, tenant_id, user_id, task_id)
        return _ok({'task': task})
    except service.ConversationServiceError as exc:
        return _service_error(exc)
    except SessionTaskError as exc:
        return _from_service_error(exc)


@router.put('/{task_id}')
async def edit_proxy_task(task_id: int, request: Request):
    """已停止编辑：标题/指令；新增目标经受信一次性读取记水位（edit_join_read）。"""
    try:
        tenant_id, user_id = await _current_user_and_tenant(request)
        body = await request.json()
        if not isinstance(body, dict) or not body or set(body) - {
                'title', 'instruction', 'add_sessions', 'remove_sessions'}:
            return _err(400, '编辑字段无效', 'VALIDATION_FAILED')
        task = await asyncio.to_thread(proxy_tasks.get_task, tenant_id, user_id, task_id,
                                       include_targets=False)
        if task is None:
            return _err(404, '代理任务不存在或无权访问', 'PROXY_NOT_FOUND')
        add_sessions = body.get('add_sessions')
        add_baselines = None
        if add_sessions:
            device, error = await _task_device(tenant_id, user_id, task['device_id'],
                                               ('wecom_read_sessions',))
            if error:
                return error
            add_baselines = {}
            for locator in add_sessions if isinstance(add_sessions, list) else []:
                baseline, error = await _baseline_read(tenant_id, user_id, device, locator)
                if error:
                    return error
                if baseline['self_user_id'] != task['wecom_account_id']:
                    return _err(409, '设备当前企微账号与任务固定账号不一致，未加入会话',
                                'PROXY_ACCOUNT_MISMATCH')
                add_baselines[locator] = baseline
        task = await asyncio.to_thread(
            proxy_tasks.edit_stopped_task, tenant_id, user_id, task_id,
            title=body.get('title'), instruction=body.get('instruction'),
            add_sessions=add_sessions, add_baselines=add_baselines,
            remove_sessions=body.get('remove_sessions'))
        return _ok({'task': task})
    except service.ConversationServiceError as exc:
        return _service_error(exc)
    except SessionTaskError as exc:
        return _from_service_error(exc)


@router.delete('/{task_id}')
async def delete_proxy_task(task_id: int, request: Request):
    """软删除：仅已停止可删；未决批次转 ended_by_delete，历史只读可查。"""
    try:
        tenant_id, user_id = await _current_user_and_tenant(request)
        task = await asyncio.to_thread(proxy_tasks.soft_delete_task, tenant_id, user_id, task_id)
        return _ok({'task': task})
    except service.ConversationServiceError as exc:
        return _service_error(exc)
    except SessionTaskError as exc:
        return _from_service_error(exc)
