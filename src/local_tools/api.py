"""本地工具 API：Web 用户 API + Runtime API

- Web 用户 API：get_current_user + request.state.tenant_id（TenantContextMiddleware 解析）
- Runtime API：设备 Bearer token 自认证（_require_device 依赖），所有隔离键取 device.tenant_id，
  不信任何请求体里的 tenant/user

所有同步 DB 调用一律 asyncio.to_thread 包裹，不阻塞事件循环。
"""

import asyncio
import base64
import binascii
import json
import uuid as _uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response, UploadFile
from loguru import logger
from pydantic import BaseModel, ConfigDict

from src.api.auth import get_current_user
from src.config.settings import settings
from src.db.client_binding_db import ClientUsageLogDB
from src.db.models import UserDB
from src.desktop_automation import audit, payload_resolver
from src.desktop_automation.adapters import AdapterNotFoundError
from src.desktop_automation.constants import BUSINESS_KIND_DESKTOP_AUTOMATION, OPERATION_PROTOCOL_V2
from src.local_tools import catalog, operation_result, pairing, permits, repository, screen_stream, weixin_friends, weixin_summary, weixin_tracking, weixin_tracking_schedule, weixin_visual
from src.local_tools.models import (
    ClaimRequest,
    HeartbeatRequest,
    OperationResultRequest,
    PairRequest,
    ReplacementCompleteRequest,
    ProgressRequest,
    ResultRequest,
    ResumeEvaluateRequest,
    StartedRequest,
    WriteAuthorizeRequest,
)
from src.local_tools.plugin_manifests import PLUGIN_PHASE, client_capabilities
from src.local_tools.pricing import RESUME_RECOGNITION_TOOL_NAME, resume_recognition_price
from src.services import recruiting_match_service, resume_vl_service
from src.services.boss_visual_service import BossVisualRequestBody, RuntimeVisualCaller, diagnose_visual
from src.saas.db.tenant_db import TenantDB
from src.saas.services.credit_gate import check_tenant_credit_blocked
from src.local_tools.security import generate_claim_token, sha256_hex
from src.utils import sanitize_error_info

router = APIRouter(prefix="/api/local-tools", tags=["local-tools"])

LEASE_SECONDS = 60          # claim 租约时长
CLAIM_POLL_INTERVAL = 0.5   # 长轮询间隔（秒）
CLAIM_MAX_WAIT = 30         # 长轮询最长等待（秒）
ONLINE_THRESHOLD_SECONDS = 30  # last_seen_at 距今 ≤30s 视为在线
PROGRESS_MESSAGE_MAX_LEN = 500


class WecomCalibrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    image: str


_WECOM_CALIBRATION_PROMPT = (
    '只定位企业微信右侧聊天区顶部标题第一个字中心，以及底部大块文本输入框中心。'
    '返回单个 JSON 对象，字段 divider_x、title_x、title_y、input_x、input_y 均为相对完整截图左上角的整数像素坐标。'
    'divider_x 是左侧列表与右侧聊天区分界线。看不清时返回 {"error":"point_not_found"}。不要返回文字内容。'
)


def _http_error(status_code: int, error: str, exc: Optional[Exception] = None) -> HTTPException:
    """统一错误响应：{error, debug(sanitize 后)}"""
    detail: Dict[str, Any] = {"error": error}
    if exc is not None:
        detail["debug"] = sanitize_error_info(str(exc))
    return HTTPException(status_code=status_code, detail=detail)


_wecom_log_tasks: set = set()


def _spawn_wecom_log_callback(row: Dict[str, Any]) -> None:
    """企微旁路日志回调的 fire-and-forget 派发（保持强引用防 GC；回调自身不抛出）。"""
    async def _run():
        from src.wecom_conversation import logsink
        await logsink.on_result_accepted(dict(row))

    try:
        task = asyncio.create_task(_run())
        _wecom_log_tasks.add(task)
        task.add_done_callback(_wecom_log_tasks.discard)
    except Exception as error:
        logger.warning(f"后端日志：企微旁路日志回调派发失败 invocation={row.get('id')} "
                       f"error={type(error).__name__}")


async def _current_user_and_tenant(request: Request) -> tuple:
    """Web API 鉴权：用户 token + middleware 解析的租户上下文"""
    user = await asyncio.to_thread(get_current_user, request)
    if not user:
        raise _http_error(401, "未登录或登录已过期")
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise _http_error(400, "缺少租户上下文（tenant_id）")
    return user, tenant_id


def _require_device(request: Request) -> Dict[str, Any]:
    """Runtime API 鉴权（同步依赖，FastAPI 在线程池执行）：设备 token → active 设备"""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise _http_error(401, "缺少设备 token")
    device = repository.get_device_by_token_hash(sha256_hex(auth_header[7:]))
    if not device:
        raise _http_error(401, "设备 token 无效或设备已撤销")
    return device


def _iso(value: Any) -> Optional[str]:
    return value.isoformat() if hasattr(value, "isoformat") else (str(value) if value else None)


def _valid_uuid(value: str) -> bool:
    """路径参数 UUID 形态预检：非法形态直接按 404 处理（P2-6），避免 DB 端 DataError 500"""
    try:
        _uuid.UUID(value)
        return True
    except (ValueError, AttributeError, TypeError):
        return False


# ==================== Web 用户 API ====================


@router.post("/weixin-summary/runs")
async def create_weixin_summary_run(body: weixin_summary.CreateRun, request: Request):
    user, tenant_id = await _current_user_and_tenant(request)
    if not body.confirmed or not body.target_name.strip():
        raise _http_error(400, '请输入完整群名并确认本次总结用途')
    credit = await asyncio.to_thread(check_tenant_credit_blocked, tenant_id)
    if credit is not None:
        raise HTTPException(status_code=credit.status_code, detail={
            'error': '积分余额已耗尽，无法生成今日总结', 'code': 'NO_CREDIT',
        })
    try:
        return await asyncio.to_thread(
            weixin_summary.create_run, tenant_id, user['user_id'], str(body.device_id), body.target_name,
        )
    except weixin_summary.RunUnavailable as error:
        code = str(error)
        message = {
            'DEVICE_NOT_SELECTED': '运行设备已变化，请刷新页面或到本地工具重新选择设备',
            'DEVICE_OFFLINE': '选定的 Runtime 当前离线',
            'WEIXIN_SUMMARY_PLUGIN_UNAVAILABLE': '选定设备未启用支持今日总结的签名微信插件',
        }[code]
        raise HTTPException(status_code=409, detail={'error': message, 'code': code}) from None
    except repository.DeviceOperationUnresolved:
        raise _http_error(409, '选定设备当前有待核对的操作') from None


@router.get("/weixin-summary/runs/{run_id}")
async def get_weixin_summary_run(run_id: str, request: Request):
    user, tenant_id = await _current_user_and_tenant(request)
    if not _valid_uuid(run_id):
        raise _http_error(404, '微信总结任务不存在')
    result = await asyncio.to_thread(weixin_summary.get_run, run_id, tenant_id, user['user_id'])
    if result is None:
        raise _http_error(404, '微信总结任务不存在')
    return result


@router.post("/runtime/weixin-summary")
async def runtime_weixin_summary(request: Request):
    auth = request.headers.get('Authorization', '')
    grant = auth[7:] if auth.startswith('Bearer ') else ''
    if len(grant) != 43 or not all(char.isascii() and (char.isalnum() or char in '-_') for char in grant):
        raise _http_error(401, '微信总结授权无效')
    upload = await weixin_summary.read_upload(request)
    if upload is None:
        raise _http_error(400, '微信总结请求格式无效')
    for_range = isinstance(upload, weixin_summary.RangeUpload)
    if for_range:
        run = await asyncio.to_thread(
            weixin_summary.consume_range_grant, upload.tenant_id, upload.start_at, upload.end_at, sha256_hex(grant),
        )
    else:
        run = await asyncio.to_thread(
            weixin_summary.consume_grant, upload.tenant_id, upload.business_date, sha256_hex(grant),
        )
    if not run:
        raise _http_error(403, '微信总结授权已失效')
    credit = await asyncio.to_thread(check_tenant_credit_blocked, upload.tenant_id)
    if credit is not None:
        return credit
    summary = await weixin_summary.summarize(
        upload.messages, upload.tenant_id, run['user_id'], **({'for_range': True} if for_range else {}),
    )
    if summary is None:
        raise _http_error(502, '微信总结生成失败')
    return {'summary': summary}



_WEIXIN_TRACKING_ERRORS = {
    'BATCH_NOT_FOUND': (404, '微信总结批次不存在'),
    'GROUP_NOT_FOUND': (404, '找不到所选跟踪群'),
    'SCHEDULE_NOT_FOUND': (404, '定时总结不存在'),
    'GROUP_DUPLICATE': (409, '已保存同名跟踪群'),
    'GROUP_NOT_RETRYABLE': (409, '仅可重试本次执行中失败的群聊'),
    'INVALID_RANGE': (400, '请选择最近 168 小时内的非空时间段'),
    'DEVICE_NOT_SELECTED': (409, '运行设备已变化或解绑，请核对本地工具中的设备'),
    'DEVICE_OFFLINE': (409, '运行设备当前离线'),
    'WEIXIN_RANGE_PLUGIN_UNAVAILABLE': (409, '运行设备未启用支持时间段总结的签名微信插件'),
    'NO_CREDIT': (403, '积分余额已耗尽，无法生成微信总结'),
    'RULE_UNAVAILABLE': (409, '这条群机器人规则已停用或未配置总结适用的终态事件'),
}


async def _weixin_tracking_call(request, operation, *args):
    user, tenant_id = await _current_user_and_tenant(request)
    try:
        return await asyncio.to_thread(operation, tenant_id, user['user_id'], *args)
    except weixin_tracking.Unavailable as error:
        code = str(error)
        status, message = _WEIXIN_TRACKING_ERRORS[code]
        raise HTTPException(status_code=status, detail={'error': message, 'code': code}) from None
    except repository.DeviceOperationUnresolved:
        raise HTTPException(status_code=409, detail={
            'error': '运行设备当前有待核对的操作', 'code': 'DEVICE_OPERATION_UNRESOLVED',
        }) from None


@router.get('/weixin-agent/groups')
async def list_weixin_groups(request: Request):
    return await _weixin_tracking_call(request, weixin_tracking.groups)


@router.post('/weixin-agent/groups')
async def add_weixin_group(body: weixin_tracking.GroupName, request: Request):
    return await _weixin_tracking_call(request, weixin_tracking.save_group, body.name)


@router.put('/weixin-agent/groups/{group_id}')
async def update_weixin_group(group_id: _uuid.UUID, body: weixin_tracking.GroupName, request: Request):
    return await _weixin_tracking_call(request, weixin_tracking.save_group, body.name, str(group_id))


@router.delete('/weixin-agent/groups/{group_id}', status_code=204, response_class=Response)
async def remove_weixin_group(group_id: _uuid.UUID, request: Request):
    await _weixin_tracking_call(request, weixin_tracking.remove_group, str(group_id))
    return Response(status_code=204)


@router.post('/weixin-agent/batches')
async def start_weixin_batch(body: weixin_tracking.BatchRequest, request: Request):
    return await _weixin_tracking_call(request, weixin_tracking.create_batch, body)


@router.get('/weixin-agent/batches')
async def list_weixin_batches(request: Request, group_id: _uuid.UUID | None = None, offset: int = Query(0, ge=0)):
    return await _weixin_tracking_call(request, weixin_tracking.list_batches, str(group_id) if group_id else None, offset)


@router.get('/weixin-agent/batches/{batch_id}')
async def get_weixin_batch(batch_id: _uuid.UUID, request: Request):
    return await _weixin_tracking_call(request, weixin_tracking.get_batch, str(batch_id))


@router.post('/weixin-agent/batches/{batch_id}/retry/{group_id}')
async def retry_weixin_group(batch_id: _uuid.UUID, group_id: _uuid.UUID, request: Request):
    return await _weixin_tracking_call(request, weixin_tracking.retry_group, str(batch_id), str(group_id))


@router.get('/weixin-agent/schedules')
async def list_weixin_schedules(request: Request):
    return await _weixin_tracking_call(request, weixin_tracking_schedule.list_schedules)


@router.get('/weixin-agent/notification-rules')
async def list_weixin_notification_rules(request: Request):
    _, tenant_id = await _current_user_and_tenant(request)
    return await asyncio.to_thread(weixin_tracking_schedule.rules, tenant_id)


@router.post('/weixin-agent/schedules')
async def create_weixin_schedule(body: weixin_tracking_schedule.Definition, request: Request):
    return await _weixin_tracking_call(request, weixin_tracking_schedule.save_schedule, body)


@router.put('/weixin-agent/schedules/{schedule_id}')
async def update_weixin_schedule(schedule_id: _uuid.UUID, body: weixin_tracking_schedule.Definition, request: Request):
    return await _weixin_tracking_call(request, weixin_tracking_schedule.save_schedule, body, str(schedule_id))


@router.patch('/weixin-agent/schedules/{schedule_id}')
async def change_weixin_schedule_state(schedule_id: _uuid.UUID, body: weixin_tracking_schedule.ScheduleState, request: Request):
    return await _weixin_tracking_call(request, weixin_tracking_schedule.set_paused, str(schedule_id), body.paused)


_WEIXIN_FRIEND_ERRORS = {
    'INVALID_FILE': (400, '请上传有效的 XLSX 或 UTF-8 CSV 文件。'),
    'FILE_TOO_LARGE': (400, '文件过大，请使用小于 2 MB 的清单。'),
    'MISSING_HEADER': (400, '清单缺少「手机号或微信号」列。'),
    'DUPLICATE_HEADER': (400, '清单含重复列名，请保留一列同名字段。'),
    'EMPTY_FILE': (400, '清单没有联系人。'),
    'TOO_MANY_CONTACTS': (400, f'每份清单最多 {weixin_friends.MAX_ROWS} 位联系人。'),
    'INVALID_VERIFICATION': (400, '验证语应为不超过 60 字的单行文字。'),
    'WEIXIN_FRIEND_PLUGIN_UNAVAILABLE': (409, '运行设备未启用支持好友申请的签名微信插件。'),
    'FRIEND_BATCH_NOT_FOUND': (404, '好友申请批次不存在。'),
    'NO_CREDIT': (403, '积分余额已耗尽，无法提交好友申请。'),
}


async def _weixin_friends_call(request, operation, *args):
    user, tenant_id = await _current_user_and_tenant(request)
    try:
        return await asyncio.to_thread(operation, tenant_id, user['user_id'], *args)
    except weixin_friends.Unavailable as error:
        code = str(error)
        status, message = (_WEIXIN_FRIEND_ERRORS.get(code) or _WEIXIN_TRACKING_ERRORS[code])
        raise HTTPException(status_code=status, detail={'error': message, 'code': code}) from None
    except repository.DeviceOperationUnresolved:
        raise HTTPException(status_code=409, detail={
            'error': '运行设备当前有待核对的操作', 'code': 'DEVICE_OPERATION_UNRESOLVED',
        }) from None


@router.post('/weixin-agent/friends/preview')
async def preview_weixin_friends(request: Request, file: UploadFile):
    content = await file.read(weixin_friends.MAX_FILE_BYTES + 1)
    return await _weixin_friends_call(request, weixin_friends.preview, file.filename or '', content)


@router.post('/weixin-agent/friends/batches')
async def start_weixin_friend_batch(body: weixin_friends.BatchRequest, request: Request):
    return await _weixin_friends_call(request, weixin_friends.create_batch, body)


@router.get('/weixin-agent/friends/batches')
async def list_weixin_friend_batches(request: Request, offset: int = Query(0, ge=0)):
    return await _weixin_friends_call(request, weixin_friends.list_batches, offset)


@router.get('/weixin-agent/friends/batches/{batch_id}')
async def get_weixin_friend_batch(batch_id: _uuid.UUID, request: Request):
    return await _weixin_friends_call(request, weixin_friends.get_batch, str(batch_id))


@router.post("/pairing-tickets")
async def create_pairing_ticket(request: Request):
    """创建一次性配对码（明文仅此一次返回，5 分钟有效）"""
    user, tenant_id = await _current_user_and_tenant(request)
    try:
        result = await asyncio.to_thread(
            pairing.create_pairing_ticket, tenant_id, user["user_id"]
        )
        return {"success": True, "code": result["code"], "expires_at": _iso(result["expires_at"])}
    except Exception as e:
        logger.opt(exception=True).error(f"后端日志：创建配对码失败: {e}")
        raise _http_error(500, "创建配对码失败，请稍后重试", e)


@router.get("/devices")
async def list_devices(request: Request):
    """当前 tenant+user 设备列表（含在线状态与选定标记）"""
    user, tenant_id = await _current_user_and_tenant(request)
    try:
        devices = await asyncio.to_thread(repository.list_devices, tenant_id, user["user_id"])
        now = datetime.now()
        screen_stream_enabled = settings.screen_stream.enabled
        items = []
        for d in devices:
            last_seen = d.get("last_seen_at")
            online = bool(last_seen and (now - last_seen).total_seconds() <= ONLINE_THRESHOLD_SECONDS)
            items.append(
                {
                    "device_id": str(d["id"]),
                    "name": d.get("name"),
                    "platform": d.get("platform"),
                    "runtime_version": d.get("runtime_version"),
                    # 设计 §0：不向 Web 暴露原始 capability payload（设备 token/CLI 路径同理不下发）
                    "selected": bool(d.get("selected")),
                    "status": d.get("status"),
                    "online": online,
                    "last_seen_at": _iso(last_seen),
                    "created_at": _iso(d.get("created_at")),
                    # 契约 2.25：只下发「是否支持实时画面」的结论，同样不暴露原始能力内容
                    "screen_stream": screen_stream_enabled and screen_stream.supports_screen_stream(
                        d.get("capabilities_json")),
                }
            )
        return {"success": True, "devices": items}
    except Exception as e:
        logger.opt(exception=True).error(f"后端日志：查询设备列表失败: {e}")
        raise _http_error(500, "查询设备列表失败，请稍后重试", e)


def _replacement_error(error: repository.DeviceReplacementError) -> HTTPException:
    messages = {
        "PAIRING_PROTOCOL_REQUIRED": (400, "此码用于更换设备，请安装支持设备替换的新版Runtime"),
        "REPLACEMENT_TARGET_NOT_FOUND": (404, "目标设备不存在或不属于当前账号"),
        "REPLACEMENT_DEVICE_REVOKED": (401, "新设备已撤销，无法完成替换"),
        "REPLACEMENT_ALREADY_COMPLETED": (409, "旧设备已由另一次替换完成，新设备连接仍保留，请在网页确认选定设备"),
        "REGISTRATION_INVALID": (400, "注册请求身份格式无效，请升级客户端"),
        "REGISTRATION_CONFLICT": (409, "注册请求与原请求不一致，请重试原注册"),
        "REGISTRATION_REVOKED": (401, "本次注册的设备已失效，请重新配对"),
        "PREVIOUS_DEVICE_INVALID": (401, "原设备凭据无效，请重新配对"),
        "PREVIOUS_DEVICE_SCOPE_MISMATCH": (403, "原设备与配对码不属于同一账号或租户"),
        "PAIRING_CODE_REQUIRED": (400, "首次注册需要有效配对码"),
    }
    status, message = messages[error.code]
    return HTTPException(status_code=status, detail={"error": message, "code": error.code, "debug": ""})


@router.post("/devices/{device_id}/replacement-ticket")
async def create_replacement_ticket(device_id: str, request: Request):
    """Logged-in user explicitly authorizes replacement of one owned old device."""
    user, tenant_id = await _current_user_and_tenant(request)
    if not _valid_uuid(device_id):
        raise _replacement_error(repository.DeviceReplacementError("REPLACEMENT_TARGET_NOT_FOUND"))
    try:
        result = await asyncio.to_thread(pairing.create_pairing_ticket, tenant_id, user["user_id"], device_id)
        return {"success": True, "code": result["code"], "expires_at": _iso(result["expires_at"]),
                "replacement_device_id": result["replacement_device_id"]}
    except repository.DeviceReplacementError as error:
        raise _replacement_error(error)
    except Exception:
        # Fixed diagnostics: database exceptions can include token/ticket hashes.
        logger.opt(exception=RuntimeError("REPLACEMENT_TICKET_TRANSACTION_FAILED")).error("后端日志：设备替换配对码创建失败")
        raise HTTPException(status_code=500, detail={"error": "设备替换配对码创建失败，请稍后重试", "debug": ""})


@router.post("/devices/{device_id}/select")
async def select_device(device_id: str, request: Request):
    """选定当前设备（单选，校验归属与 active）"""
    user, tenant_id = await _current_user_and_tenant(request)
    try:
        ok = await asyncio.to_thread(
            repository.select_device, tenant_id, user["user_id"], device_id
        )
        if not ok:
            raise _http_error(404, "设备不存在、无权限或已撤销")
        return {"success": True, "device_id": device_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.opt(exception=True).error(f"后端日志：选定设备失败 device={device_id}: {e}")
        raise _http_error(500, "选定设备失败，请稍后重试", e)


@router.delete("/devices/{device_id}")
async def revoke_device(device_id: str, request: Request):
    """撤销设备（token 立即失效，selected 清除）"""
    user, tenant_id = await _current_user_and_tenant(request)
    try:
        ok = await asyncio.to_thread(
            repository.revoke_device, tenant_id, user["user_id"], device_id
        )
        if not ok:
            raise _http_error(404, "设备不存在、无权限或已撤销")
        logger.info(f"后端日志：撤销本地工具设备 device={device_id} tenant={tenant_id}")
        return {"success": True, "device_id": device_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.opt(exception=True).error(f"后端日志：撤销设备失败 device={device_id}: {e}")
        raise _http_error(500, "撤销设备失败，请稍后重试", e)


# ---- 实时画面（设计 runtime-screen-stream-design.md 第 5 节，契约 2.25）----
# 画面走 WebRTC 点对点，服务端只做鉴权、记录会话、转交建连信令。轮询接口不打 info 日志；
# 信令内容不解析、不写日志。

SCREEN_NOTICE_TIMEOUT_SECONDS = 0.5  # 心跳里查画面会话通知的时限


def _screen_stream_http_error(error: screen_stream.ScreenStreamError) -> HTTPException:
    return HTTPException(status_code=error.status, detail={"error": error.message, "code": error.code, "debug": ""})


def _require_screen_stream() -> None:
    """功能开关：未启用时六个接口一律 503"""
    if not settings.screen_stream.enabled:
        raise _screen_stream_http_error(screen_stream.ScreenStreamError("SCREEN_STREAM_DISABLED"))


async def _screen_stream_call(operation, *args, **kwargs):
    """同步的画面会话函数放到线程里执行，类型化错误转成带 code 的 HTTP 错误"""
    try:
        return await asyncio.to_thread(operation, *args, **kwargs)
    except screen_stream.ScreenStreamError as error:
        raise _screen_stream_http_error(error) from None


async def _screen_stream_viewer(request: Request) -> tuple:
    """网页侧画面接口的公共入口：先登录态，再看功能开关（占用闸在它之前的中间件 ScreenStreamGateMiddleware 里）"""
    user, tenant_id = await _current_user_and_tenant(request)
    _require_screen_stream()
    return user, tenant_id


def _check_screen_session_path(device_id: str, session_id: str) -> None:
    """路径里的设备与会话编号形态预检：非法按 404，避免 DB 端 DataError 500"""
    if not (_valid_uuid(device_id) and _valid_uuid(session_id)):
        raise _screen_stream_http_error(screen_stream.ScreenStreamError("SCREEN_SESSION_NOT_FOUND"))


async def _load_screen_session(scope, session_id, *, viewer, after_seq):
    """读取会话：不存在或无权访问一律 404，不区分是哪一种"""
    result = await _screen_stream_call(
        screen_stream.load_session, scope, session_id,
        viewer=viewer, after_seq=after_seq, device_offline_seconds=ONLINE_THRESHOLD_SECONDS,
    )
    if result is None:
        raise _screen_stream_http_error(screen_stream.ScreenStreamError("SCREEN_SESSION_NOT_FOUND"))
    return {"success": True, **result}


async def _append_screen_signal(scope, session_id, direction, signal_type, payload):
    """追加一条信令：不存在或无权访问一律 404"""
    result = await _screen_stream_call(
        screen_stream.append_signal, scope, session_id, direction, signal_type, payload,
        device_offline_seconds=ONLINE_THRESHOLD_SECONDS,
    )
    if result is None:
        raise _screen_stream_http_error(screen_stream.ScreenStreamError("SCREEN_SESSION_NOT_FOUND"))
    return result


@router.post("/devices/{device_id}/screen-sessions")
async def create_device_screen_session(device_id: str, request: Request):
    """创建实时画面会话：同一设备同一时间只允许一个未结束会话"""
    user, tenant_id = await _screen_stream_viewer(request)
    if not _valid_uuid(device_id):
        raise _screen_stream_http_error(screen_stream.ScreenStreamError("DEVICE_NOT_FOUND"))
    session = await _screen_stream_call(
        screen_stream.create_session, tenant_id, user["user_id"], device_id,
        device_offline_seconds=ONLINE_THRESHOLD_SECONDS,
    )
    return {"success": True, "session": {**session, "stun_urls": list(settings.screen_stream.stun_urls)}}


@router.get("/devices/{device_id}/screen-sessions/{session_id}")
async def get_device_screen_session(request: Request, device_id: str, session_id: str,
                                    after_seq: int = Query(0, ge=0)):
    """网页轮询：会话状态和 Runtime 发来的、序号大于 after_seq 的信令"""
    user, tenant_id = await _screen_stream_viewer(request)
    _check_screen_session_path(device_id, session_id)
    return await _load_screen_session(
        screen_stream.viewer_scope(tenant_id, user["user_id"], device_id), session_id,
        viewer=True, after_seq=after_seq,
    )


@router.post("/devices/{device_id}/screen-sessions/{session_id}/signals")
async def append_device_screen_session_signal(request: Request, device_id: str, session_id: str,
                                              body: screen_stream.ViewerSignalRequest):
    """追加一条网页信令（start / answer / candidate / end）"""
    user, tenant_id = await _screen_stream_viewer(request)
    _check_screen_session_path(device_id, session_id)
    result = await _append_screen_signal(
        screen_stream.viewer_scope(tenant_id, user["user_id"], device_id), session_id,
        screen_stream.DIR_WEB, body.type, body.payload.model_dump(exclude_unset=True),
    )
    return {"success": True, "seq": result["seq"]}


@router.post("/devices/{device_id}/screen-sessions/{session_id}/end")
async def end_device_screen_session(request: Request, device_id: str, session_id: str,
                                    body: screen_stream.ViewerEndRequest = Body(default=screen_stream.ViewerEndRequest())):
    """网页结束会话（幂等），结束原因固定记 viewer_closed；请求体里的 reason 原样忽略"""
    user, tenant_id = await _screen_stream_viewer(request)
    _check_screen_session_path(device_id, session_id)
    result = await _append_screen_signal(
        screen_stream.viewer_scope(tenant_id, user["user_id"], device_id), session_id,
        screen_stream.DIR_WEB, "end", {},
    )
    return {"success": True, "session": result["session"]}


# ==================== Runtime API ====================


@router.post("/runtime/pair")
async def runtime_pair(body: PairRequest):
    """票据注册；协议3受保护请求身份支持丢响应后的幂等恢复。"""
    try:
        result = await asyncio.to_thread(
            pairing.pair,
            body.code,
            body.name,
            body.platform,
            body.runtime_version,
            body.capabilities,
            body.machine_fingerprint,
            body.pairing_protocol,
            body.registration_id,
            body.registration_token,
            body.previous_device_token,
        )
        if not result:
            raise _http_error(400, "配对码无效、已过期或已被使用")
        return {"success": True, **result}
    except repository.DeviceReplacementError as error:
        raise _replacement_error(error)
    except HTTPException:
        raise
    except Exception:
        logger.opt(exception=RuntimeError("PAIRING_TRANSACTION_FAILED")).error("后端日志：设备配对失败，事务未提交")
        raise HTTPException(status_code=500, detail={"error": "设备配对失败，请稍后重试", "debug": ""})


@router.post("/runtime/activate")
async def runtime_activate(
    body: ReplacementCompleteRequest = Body(default=ReplacementCompleteRequest()),
    device: Dict = Depends(_require_device),
):
    """Explicit migration completion: select only the authenticated device itself."""
    try:
        selected = await asyncio.to_thread(repository.select_device, device["tenant_id"], device["user_id"], str(device["id"]))
        if not selected:
            raise _replacement_error(repository.DeviceReplacementError("REGISTRATION_REVOKED"))
        return {"success": True, "selected": True}
    except HTTPException:
        raise
    except Exception:
        logger.opt(exception=RuntimeError("DEVICE_ACTIVATION_FAILED")).error("后端日志：设备激活失败")
        raise HTTPException(status_code=500, detail={"error": "设备激活失败，请稍后重试", "debug": ""})


@router.post("/runtime/replacement/complete")
async def runtime_complete_replacement(
    body: ReplacementCompleteRequest = Body(default=ReplacementCompleteRequest()),
    device: Dict = Depends(_require_device),
):
    """Client calls only after durable identity/HB and safe resource handover.

    Authentication and target ownership are rechecked under transaction locks.
    No old device id, user or tenant is accepted in this request.
    """
    try:
        completed = await asyncio.to_thread(repository.complete_device_replacement,
                                            device["tenant_id"], device["user_id"], str(device["id"]))
        return {"success": True, "replacement_completed": completed}
    except repository.DeviceReplacementError as error:
        if error.code == "REPLACEMENT_TARGET_NOT_FOUND":
            raise HTTPException(status_code=409, detail={"error": "旧设备替换关系无法核验，新设备连接已保留", "code": error.code, "debug": ""})
        raise _replacement_error(error)
    except Exception:
        logger.opt(exception=RuntimeError("REPLACEMENT_COMPLETE_TRANSACTION_FAILED")).error("后端日志：设备替换完成事务失败")
        raise HTTPException(status_code=500, detail={"error": "设备替换尚未完成，新设备连接保留，请稍后重试", "debug": ""})


@router.post("/runtime/heartbeat")
async def runtime_heartbeat(body: HeartbeatRequest, device: Dict = Depends(_require_device)):
    """心跳：更新 last_seen / 版本 / 能力 / manifest 摘要"""
    try:
        row = await asyncio.to_thread(
            repository.touch_device_seen,
            str(device["id"]),
            body.runtime_version,
            body.capabilities,
            body.manifest_digest,
            tenant_id=device["tenant_id"],
            plugin_manifests=body.plugin_manifests,
            plugin_trust_roots=settings.agent_runner.plugin_trust_roots,
        )
        if not row:
            raise _http_error(404, "设备不存在")
        response = {
            "success": True,
            "selected": bool(row.get("selected")),
            "server_time": datetime.now().isoformat(),
            # 契约 2.20 支持协商：服务端实现 parallel_only 领取即恒回 True；
            # 旧客户端忽略该字段，新版 Runtime 读到后才发送 parallel_only
            "parallel_claim": True,
            **({'missing_plugin_manifests': row['missing_plugin_manifests']}
               if row.get('missing_plugin_manifests') else {}),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.opt(exception=True).error(f"后端日志：设备心跳失败 device={device['id']}: {e}")
        raise _http_error(500, "心跳失败，请稍后重试", e)
    # 画面会话通知放在上面的 try/except 之外：设备更新成功就一定返回成功，
    # 画面查询自己出错或超时只告警，不会被外层 except 变成 500（设计 4.4）。
    # 查不查会话按这次心跳生效的能力体判断，取法与上面 touch_device_seen 存库时相同：
    # 上报的能力体归一化后非空就用它，否则用设备已保存的
    await _attach_screen_session_notice(
        response, str(device["id"]), client_capabilities(body.capabilities) or device.get("capabilities_json"))
    return response


async def _attach_screen_session_notice(response: Dict[str, Any], device_id: str, capabilities: Any) -> None:
    """设备更新成功后单独查画面会话通知（契约 2.25），写进心跳响应。

    capabilities 是这次心跳生效的能力体。不支持画面的设备不可能有画面会话（创建会话时就被拒绝）：
    不为它查会话，也就不拿占用闸的名额、不建画面连接；响应里照常带 screen_stream 标记。
    传画面期间每次心跳都带当前会话，这就是 Runtime 的续期；查询拿不到结果时响应里不带
    screen_session，只表示这一次不续期。日志里只出现设备编号，不出现信令内容。
    """
    if not settings.screen_stream.enabled:
        return
    response["screen_stream"] = {"version": screen_stream.PROTOCOL_VERSION}
    if not screen_stream.supports_screen_stream(capabilities):
        return
    try:
        notice = await asyncio.wait_for(
            asyncio.to_thread(screen_stream.heartbeat_notice, device_id,
                              device_offline_seconds=ONLINE_THRESHOLD_SECONDS),
            timeout=SCREEN_NOTICE_TIMEOUT_SECONDS,
        )
    except screen_stream.ScreenStreamError:
        # 画面请求同时借走的连接到了上限（占用闸已满），心跳里的画面查询让出了：这次不带会话通知，
        # 只记一条不带堆栈的告警，不算失败。这是 heartbeat_notice 唯一会抛的类型化错误
        logger.warning(f"后端日志：画面繁忙，本次心跳不带会话通知 device={device_id}")
    except Exception as error:
        # 两种超时都只记一条不带堆栈的告警：应用层等到 0.5 秒放弃（asyncio.TimeoutError），或者
        # 数据库在库里的等待上限到点、取消了语句（连接已归还）。其余异常才带堆栈记为失败。
        if isinstance(error, asyncio.TimeoutError) or screen_stream.is_database_timeout(error):
            logger.warning(f"后端日志：画面会话查询超时 device={device_id}")
        else:
            logger.opt(exception=True).warning(f"后端日志：画面会话查询失败 device={device_id}")
    else:
        if notice:
            response["screen_session"] = notice


@router.post("/runtime/screen/session")
async def get_runtime_screen_session(body: screen_stream.RuntimeSessionRequest,
                                      device: Dict = Depends(_require_device)):
    """Runtime 取会话：状态和网页发来的、序号大于 after_seq 的信令"""
    _require_screen_stream()
    result = await _load_screen_session(
        screen_stream.runtime_scope(device["tenant_id"], str(device["id"])), str(body.session_id),
        viewer=False, after_seq=body.after_seq,
    )
    result["session"]["stun_urls"] = list(settings.screen_stream.stun_urls)
    return result


@router.post("/runtime/screen/signal")
async def append_runtime_screen_signal(body: screen_stream.RuntimeSignalRequest,
                                        device: Dict = Depends(_require_device)):
    """Runtime 追加一条信令（prepared / offer / candidate / status / end）"""
    _require_screen_stream()
    result = await _append_screen_signal(
        screen_stream.runtime_scope(device["tenant_id"], str(device["id"])), str(body.session_id),
        screen_stream.DIR_RT, body.type, body.payload.model_dump(exclude_unset=True),
    )
    return {"success": True, "seq": result["seq"]}


@router.post("/runtime/claim")
async def runtime_claim(wait: int = 20, parallel_only: bool = False, device: Dict = Depends(_require_device),
                        body: Optional[ClaimRequest] = None):
    """长轮询领取 invocation（claim_token 明文仅此一次返回）。
    超时返回 {"success": true, "invocation": null}（200，简化客户端）。

    provider 语义（契约补充）：响应 provider = invocation 行 provider_key；
    旧行 NULL 时回退设备 capabilities 解析的默认 provider。领取过滤：
    行 provider_key NULL 任意设备可领（旧行为），非 NULL 须设备能力包含该 provider。

    parallel_only（契约 2.20）：True 时仅领取「并行可领」作业（provider_key 非 NULL 且
    tool_name 属于该设备心跳上报 parallel_tools[provider_key]）。映射取自设备行
    capabilities_json 并做最小形态归一（非 dict/值非 list 的键丢弃），异常形态退化为
    空映射（匹配不到任何行，不抛 500）。缺省 False 时选取规则完全不变。

    行级放行（契约 2.20 修订，声明即调度授权）：已上报 tool_declarations_v1 且
    declared_tools 非空的设备，非 NULL provider 行以设备 declared_tools 为准
    （串行与 parallel_only 两条领取路径共用同一判定）；显式空列表不给该 provider
    授权。未被声明覆盖的 provider 与旧 NULL 行沿用目录判定，默认 provider
    明确声明空列表时也拒绝 NULL 行；
    无声明/旧设备/解析异常完整回退 catalog.is_tool_allowed，行为不变。
    """
    device_id = str(device["id"])
    tenant_id = device["tenant_id"]
    default_provider_key = catalog.get_provider_key_for_device(device.get("capabilities_json"))
    provider_keys = catalog.get_provider_keys_for_device(device.get("capabilities_json"))
    parallel_tools: Optional[Dict[str, List[str]]] = None
    if parallel_only:
        parallel_tools = catalog.parallel_tools_for_device(device.get("capabilities_json"))
    wait = max(0, min(wait, CLAIM_MAX_WAIT))
    plugin_digests = (body.plugin_manifest_digests if body else None) or {}

    try:
        # 顺带清理租约过期的 claimed/running（MVP 无后台任务）
        await asyncio.to_thread(repository.expire_stale_claims)

        deadline = asyncio.get_event_loop().time() + wait
        while True:
            claim_token = generate_claim_token()
            invocation = await asyncio.to_thread(
                repository.claim_next,
                device_id,
                tenant_id,
                sha256_hex(claim_token),
                LEASE_SECONDS,
                provider_keys,
                parallel_only=parallel_only,
                parallel_tools=parallel_tools,
                **({"plugin_manifest_digests": plugin_digests} if plugin_digests else {}),
            )
            if invocation:
                # 校验 tool_name 在该 invocation Provider（行 provider_key 优先）批准的
                # tools 内；声明设备以 declared_tools 分流（含并行领取路径）
                row_provider_key = invocation.get("provider_key")
                invocation_provider = row_provider_key or default_provider_key
                ref = (invocation.get('business_ref') or {}) if invocation.get('business_kind') == 'agent_runner' else {}
                is_plugin = (ref.get('branch') == PLUGIN_PHASE
                             or invocation.get('business_kind') in catalog.WEIXIN_SIGNED_KINDS)
                capabilities = (invocation.get('device_capabilities') if is_plugin
                                else device.get('capabilities_json'))
                if not catalog.is_tool_allowed_for_device(
                    capabilities, row_provider_key,
                    invocation["tool_name"], default_provider_key,
                ):
                    logger.warning(
                        f"后端日志：invocation {invocation['id']} 工具 "
                        f"{invocation['tool_name']} 不在设备 Provider 批准清单内，置为失败"
                    )
                    await asyncio.to_thread(
                        repository.write_runtime_result,
                        str(invocation["id"]),
                        tenant_id,
                        sha256_hex(claim_token),
                        False,
                        "TOOL_NOT_ALLOWED",
                        "工具不在设备 Provider 批准清单内",
                        **({"effect": "none"} if invocation.get('business_kind') == catalog.WEIXIN_FRIEND_BATCH_KIND else {}),
                    )
                else:
                    logger.info(
                        f"后端日志：invocation {invocation['id']} 被设备 {device_id} 领取 "
                        f"tool={invocation['tool_name']} provider={invocation_provider}"
                    )
                    receipt_version = catalog.execution_receipt_version(capabilities, invocation)
                    return {
                        "success": True,
                        "invocation_id": str(invocation["id"]),
                        "tool_name": invocation["tool_name"],
                        "arguments": invocation["arguments_json"],
                        "claim_token": claim_token,
                        "lease_expires_at": _iso(invocation["lease_expires_at"]),
                        "provider": invocation_provider,
                        **({"receipt_version": receipt_version} if receipt_version else {}),
                    }
            if asyncio.get_event_loop().time() >= deadline:
                return {"success": True, "invocation": None}
            await asyncio.sleep(CLAIM_POLL_INTERVAL)
    except repository.DeviceOperationUnresolved:
        return {'success': True, 'invocation': None, 'blocked_reason': 'LOCAL_DEVICE_STOP_VERIFICATION_REQUIRED'}
    except HTTPException:
        raise
    except Exception as e:
        logger.opt(exception=True).error(f"后端日志：设备领取 invocation 失败 device={device_id}: {e}")
        raise _http_error(500, "领取任务失败，请稍后重试", e)


@router.post("/runtime/invocations/{invocation_id}/started")
async def runtime_started(
    invocation_id: str, body: StartedRequest, device: Dict = Depends(_require_device)
):
    """claimed → running（幂等：重复调用返回当前状态）"""
    tenant_id = device["tenant_id"]
    try:
        row = await asyncio.to_thread(
            repository.mark_started, invocation_id, tenant_id, sha256_hex(body.claim_token),
            device_id=str(device['id']),
        )
        if not row:
            raise _http_error(404, "invocation 不存在或 claim token 不匹配")
        if row["state"] != "running":
            raise _http_error(409, f"当前状态不允许标记开始: {row['state']}")
        return {"success": True, "state": row["state"]}
    except repository.DeviceOperationUnresolved as error:
        raise HTTPException(status_code=409, detail={'error': str(error), 'debug': str(error),
            'message': '原本机操作尚未确认完成。请保持原 Runtime 连接以回传结果，暂不能开始新操作。'}) from error
    except HTTPException:
        raise
    except Exception as e:
        logger.opt(exception=True).error(f"后端日志：标记 invocation 开始失败 id={invocation_id}: {e}")
        raise _http_error(500, "标记开始失败，请稍后重试", e)


@router.post("/runtime/invocations/{invocation_id}/progress")
async def runtime_progress(
    invocation_id: str, body: ProgressRequest, device: Dict = Depends(_require_device)
):
    """追加进度事件（seq 递增 + 续租），返回 cancel 标志"""
    tenant_id = device["tenant_id"]
    message = body.message[:PROGRESS_MESSAGE_MAX_LEN] if body.message else None
    try:
        result = await asyncio.to_thread(
            repository.append_event,
            invocation_id,
            tenant_id,
            sha256_hex(body.claim_token),
            body.stage,
            body.current,
            body.total,
            message,
            LEASE_SECONDS,
        )
        if not result:
            raise _http_error(404, "invocation 不存在、claim token 不匹配或状态不允许上报进度")
        seq, cancel = result
        return {"success": True, "seq": seq, "cancel": cancel}
    except HTTPException:
        raise
    except Exception as e:
        logger.opt(exception=True).error(f"后端日志：上报进度失败 id={invocation_id}: {e}")
        raise _http_error(500, "上报进度失败，请稍后重试", e)


@router.post("/runtime/invocations/{invocation_id}/result")
async def runtime_result(
    invocation_id: str, body: ResultRequest, device: Dict = Depends(_require_device)
):
    """写入终态（幂等：已终态重复写返回当前状态）"""
    tenant_id = device["tenant_id"]
    try:
        row = await asyncio.to_thread(
            repository.write_runtime_result,
            invocation_id,
            tenant_id,
            sha256_hex(body.claim_token),
            body.success,
            body.code,
            body.message,
            body.effect,
            body.data,
            body.retryable,
            device_id=str(device['id']),
        )
        if not row:
            raise _http_error(404, "invocation 不存在或 claim token 不匹配")
        # 企微旁路日志（设计 §3.1）：原结果已接受并持久化之后的尽力异步回调，
        # 不进入本响应事务、不影响 ACK/Agent 结果/计费；失败允许丢失。
        # 迟到成功（原机制持久 state=unknown + _late_result）复用已有
        # observed_invocation 可信完成视图纳入旁路登记；原 unknown 事实不改写
        # （评审二 P1-7）。
        if row.get("provider_key") == "wecom":
            from src.local_tools.repository import observed_invocation
            view = observed_invocation(dict(row))
            if view.get("state") == "succeeded":
                _spawn_wecom_log_callback(view)
        # data_bytes：与客户端「终态已回传 data_bytes=」行比对，定位回传链路丢/裁 data（2026-09-10 排障整改）
        data_bytes = len(json.dumps(body.data).encode("utf-8")) if body.data else 0
        logger.info(
            f"后端日志：invocation {invocation_id} 写入终态 state={row['state']} effect={row['effect']} "
            f"tool={row['tool_name']} data_bytes={data_bytes}"
        )
        return {"success": True, "state": row["state"], "effect": row["effect"]}
    except HTTPException:
        raise
    except Exception as e:
        logger.opt(exception=True).error(f"后端日志：写入 invocation 终态失败 id={invocation_id}: {e}")
        raise _http_error(500, "写入结果失败，请稍后重试", e)


async def _active_runtime_owner(device: Dict) -> Dict:
    tenant_id, user_id = device["tenant_id"], device["user_id"]
    tenant, user = await asyncio.gather(
        asyncio.to_thread(TenantDB.get_by_id, tenant_id),
        asyncio.to_thread(UserDB.get_by_id, user_id),
    )
    if not tenant or tenant.get("status") != "active":
        raise _http_error(403, "设备租户不存在或已停用")
    # Pairing follows tenant-context authority: platform admins may act in a tenant
    # without belonging to it; the authenticated device still fixes the billing tenant.
    if (not user or user.get("status") != "active"
            or user.get("role") != "platform_admin" and user.get("tenant_id") != tenant_id):
        raise _http_error(403, "设备用户不存在、已停用或不属于当前租户")
    return tenant


@router.post("/runtime/boss/visual-diagnose")
async def runtime_boss_visual_diagnose(
    body: BossVisualRequestBody, device: Dict = Depends(_require_device)
):
    """复用配对设备身份诊断；新请求按实际用量×20，旧费率和回执保持冻结。"""
    from fastapi.responses import JSONResponse

    await _active_runtime_owner(device)
    caller = RuntimeVisualCaller(tenant_id=device["tenant_id"], device_id=str(device["id"]), user_id=device["user_id"])
    result = await diagnose_visual(body, caller)
    if result["state"] in {"reserved", "dispatched"}:
        return JSONResponse(result, status_code=202, headers={"Retry-After": "2"})
    return result


@router.post("/runtime/weixin/visual-locate")
async def runtime_weixin_visual_locate(
    body: weixin_visual.LocateRequest, device: Dict = Depends(_require_device)
):
    tenant = await _active_runtime_owner(device)
    if float(tenant.get("credit_balance") or 0) <= 0:
        raise _http_error(402, "积分余额不足")
    return await weixin_visual.locate(body, device)


@router.post("/runtime/wecom/calibrate")
async def runtime_wecom_calibrate(
    body: WecomCalibrationRequest, device: Dict = Depends(_require_device)
):
    """用已配对设备身份为企微双点标定定位；不接收任意模型或对话。"""
    prefix = "data:image/png;base64,"
    if not body.image.startswith(prefix):
        raise _http_error(400, "标定截图必须是本地 PNG 数据")
    encoded = body.image[len(prefix):]
    if len(encoded) > 14_000_000:
        raise _http_error(413, "标定截图过大")
    try:
        screenshot = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise _http_error(400, "标定截图不是有效 PNG 数据") from None
    if not screenshot.startswith(b"\x89PNG\r\n\x1a\n"):
        raise _http_error(400, "标定截图不是有效 PNG 数据")
    if len(screenshot) > 10 * 1024 * 1024:
        raise _http_error(413, "标定截图过大")

    tenant_id, user_id = device["tenant_id"], device["user_id"]
    tenant, user = await asyncio.gather(
        asyncio.to_thread(TenantDB.get_by_id, tenant_id),
        asyncio.to_thread(UserDB.get_by_id, user_id),
    )
    if not tenant or tenant.get("status") != "active":
        raise _http_error(403, "设备租户不存在或已停用")
    if (not user or user.get("status") != "active"
            or user.get("role") != "platform_admin" and user.get("tenant_id") != tenant_id):
        raise _http_error(403, "设备用户不存在、已停用或不属于当前租户")
    if float(tenant.get("credit_balance") or 0) <= 0:
        raise _http_error(402, "积分余额不足")

    from src.api.client_routes import _get_model_gateway

    gateway = await asyncio.to_thread(_get_model_gateway, "GLM-5.3-Flash")
    try:
        response = await gateway.chat(
            messages=[
                {"role": "system", "content": _WECOM_CALIBRATION_PROMPT},
                {"role": "user", "content": [
                    {"type": "text", "text": "按截图原像素给出两个中心坐标；不要缩放。"},
                    {"type": "image_url", "image_url": {"url": body.image}},
                ]},
            ],
            temperature=0,
            max_tokens=4000,
            response_format=None,
            reasoning_effort="max",
        )
    except Exception as error:
        logger.opt(exception=True).error(
            f"后端日志：企微双点标定模型调用失败 device={device['id']} error={type(error).__name__}"
        )
        raise _http_error(502, "企微双点标定模型调用失败") from None

    usage = response.get("usage") or {}
    model = response.get("model") or gateway.get_model_name()
    provider = response.get("provider") or gateway.get_provider_name()
    billing = await asyncio.to_thread(
        ClientUsageLogDB.record_llm_usage,
        tenant_id=tenant_id,
        binding_id="wecom-local-runtime",
        model=model,
        provider=provider,
        usage=usage,
        stage="wecom-title-coordinate",
        detail={"device_id": str(device["id"])},
    )
    return {"content": response.get("content", ""), "model": model,
            "provider": provider, "usage": usage, "billing": billing}


@router.post("/runtime/resume/evaluate")
async def runtime_resume_evaluate(
    body: ResumeEvaluateRequest, device: Dict = Depends(_require_device)
):
    """简历评估接口（2026-09-17 去 OCR 化 v2，boss-resume-vl-recognition-design.md §4.4）：
    拼接长图/分段图 → 云端 GLM 多模态一次评估 → 返回 {name_seen, resume_summary, score,
    match_summary, key_info}，评估成功按份扣简历识别费。

    客户端（boss CLI 等）用设备 token 调用。姓名门（决策⑨）：candidate_name 必填并传入
    VL 提示词，服务端将其与图中姓名（name_seen）确定性比对，不符 422 RESUME_NAME_MISMATCH
    不扣费 + 全量日志——防 runtime 点击错位开错人。模型可显式指定（provider/model，白名单
    校验），缺省 zhipu/GLM-5.3-Flash——VL 绝不随主链路（可能纯文本）漂移。

    计费语义（租户决策 2026-09-17）：截图不扣费，VL 评估成功（姓名门通过）即扣一份
    resume_recognition_price（识别失败不扣）；台账 tool_name=boss_resume_recognition
    与工具层同科目，对账同源。
    """
    tenant_id = device["tenant_id"]
    candidate_name = (body.candidate_name or "").strip()
    if not candidate_name:
        # 姓名门的比较基准，必填（决策⑨）
        raise _http_error(422, "缺少 candidate_name：页面候选人姓名必传（姓名核对用）")
    # 模型白名单校验放切片之前：非法 model + 大图不必白耗一次 Pillow 解码切片
    try:
        provider_name, model = resume_vl_service.resolve_model_spec(body.model)
    except resume_vl_service.ResumeVLModelError as e:
        raise _http_error(422, f"{e}")
    bands = [b for b in (body.images or []) if (b or "").strip()]
    if not bands and (body.image or "").strip():
        # 切片是同步 CPU 活（Pillow 解码 + 裁剪 + PNG 重编码），放线程池不阻塞事件循环
        try:
            bands = await asyncio.to_thread(
                resume_vl_service.slice_stitched_image, body.image
            )
        except resume_vl_service.ResumeVLError as e:
            raise _http_error(422, f"拼接长图切片失败：{e}")
    if not bands:
        raise _http_error(422, "缺少识别入参：请传 image（拼接长图 base64）或 images（分段图列表）")

    # 职位上下文（给了 job_id/job_name 才评分；职位库未命中 → 最小上下文走隐含要求路径，
    # 沿袭「带职位名就评」语义；无 → score/match_summary=null 只出总结）
    job_ctx = None
    if (body.job_id or body.job_name or "").strip():
        job_ctx = await asyncio.to_thread(
            recruiting_match_service._load_job_context, tenant_id, body.job_id, body.job_name
        ) or {"job_name": body.job_name}

    try:
        evaluation = await resume_vl_service.evaluate_resume(
            bands, candidate_name, job_ctx, body.model
        )
    except resume_vl_service.ResumeVLModelError as e:
        raise _http_error(422, f"{e}")
    except resume_vl_service.ResumeVLError as e:
        # 评估失败（含重试后仍失败）：不扣费，fail-loud 让调用方重试
        logger.error(
            f"后端日志：简历评估接口失败 tenant={tenant_id} device={device.get('id')} "
            f"model={provider_name}/{model}: {e}"
        )
        raise _http_error(502, f"简历评估失败（重试后仍失败），本次不扣费：{e}")

    # 姓名门（决策⑨）：不符 422 + 全量日志（name_seen 区分识别误读 vs 真点错人），不扣费
    name_seen = evaluation.get("name_seen") or ""
    if not resume_vl_service.resume_name_matches(candidate_name, name_seen):
        logger.error(
            f"后端日志：简历评估接口姓名核对不匹配（不扣费） tenant={tenant_id} "
            f"device={device.get('id')} candidate_name={candidate_name} name_seen={name_seen} "
            f"bands={len(bands)} model={evaluation.get('model')}"
        )
        raise _http_error(
            422,
            f"姓名核对不匹配（页面候选人「{candidate_name}」与简历所示「{name_seen}」不一致）："
            "疑似图片简历与所传姓名不符，请人工核对",
        )

    # 评估成功（姓名门通过）→ 按份扣简历识别费（弹层自愈同款编排层直记；失败只告警不回滚）
    price = resume_recognition_price()
    billing: Dict[str, Any] = {"credit_cost": 0.0, "balance_after": None}
    if price > 0:
        try:
            usage_row = await asyncio.to_thread(
                ClientUsageLogDB.record_tool_usage,
                tenant_id=tenant_id,
                tool_name=RESUME_RECOGNITION_TOOL_NAME,
                credit_cost=price,
                session_id=None,
                user_id=device.get("user_id"),
                # 无 invocation_id 的直记场景，device_id 是对账唯一线索
                device_id=str(device["id"]) if device.get("id") is not None else None,
            )
            billing = {
                "credit_cost": price,
                "balance_after": (usage_row or {}).get("balance_after"),
            }
        except Exception as e:  # noqa: BLE001 计费失败不影响评估结果返回（台账可对账）
            logger.opt(exception=True).error(
                f"后端日志：简历评估接口计费落账失败（不影响返回）tenant={tenant_id}: {e}"
            )
    logger.info(
        f"后端日志：简历评估接口成功 tenant={tenant_id} device={device.get('id')} "
        f"candidate={candidate_name} name_seen={name_seen} score={evaluation.get('score')} "
        f"model={evaluation.get('model')} cost={billing['credit_cost']}"
    )
    return {
        "success": True,
        "name_seen": name_seen,
        "resume_summary": evaluation.get("resume_summary"),
        "score": evaluation.get("score"),
        "match_summary": evaluation.get("match_summary"),
        "key_info": evaluation.get("key_info"),
        "model": evaluation.get("model"),
        "bands": len(bands),
        "billing": billing,
    }


# ==================== Runtime API（v2 写动作许可 / 结果回传，不暴露给 LLM）====================


@router.post("/runtime/invocations/{invocation_id}/write-authorize")
async def runtime_write_authorize(
    invocation_id: str, body: WriteAuthorizeRequest, device: Dict = Depends(_require_device)
):
    """v2 写动作许可签发（§5.2）：单事务校验 claim/租约/取消/业务类型/截止/场景授权/额度，
    返回 permit_id/permit_token（明文仅此一次）/deadline_at。旧 invocation（business_kind
    非 desktop_automation）返回 409。"""
    tenant_id = device["tenant_id"]
    if not _valid_uuid(invocation_id):
        raise _http_error(404, "invocation 不存在")
    try:
        result = await asyncio.to_thread(
            permits.write_authorize,
            tenant_id=tenant_id,
            device_id=str(device["id"]),
            invocation_id=invocation_id,
            claim_token_hash=sha256_hex(body.claim_token),
            request_id=body.request_id,
            target_version=body.target_version,
            payload_hash=body.payload_hash,
        )
        return {
            "success": True,
            "permit_id": result["permit_id"],
            "permit_token": result["permit_token"],
            "deadline_at": _iso(result["deadline_at"]),
        }
    except permits.PermitError as e:
        logger.warning(
            f"后端日志：写动作许可拒绝 tenant={tenant_id} invocation={invocation_id} "
            f"code={e.code}: {e.message}"
        )
        raise HTTPException(status_code=e.http_status, detail={"error": e.code, "message": e.message})
    except HTTPException:
        raise
    except Exception as e:
        logger.opt(exception=True).error(
            f"后端日志：写动作许可签发失败 tenant={tenant_id} invocation={invocation_id}: {e}"
        )
        raise _http_error(500, "许可签发失败，请稍后重试", e)


@router.post("/runtime/invocations/{invocation_id}/operation-result")
async def runtime_operation_result(
    invocation_id: str, body: OperationResultRequest, device: Dict = Depends(_require_device)
):
    """v2 操作结果回传（持久 ACK 幂等；迟到只对账不改判）。effect/phase 优先于
    success/code 解释（R10）；v2 路径默认 0 价计费（R2）。"""
    tenant_id = device["tenant_id"]
    if not _valid_uuid(invocation_id):
        raise _http_error(404, "invocation 不存在")
    try:
        result = await asyncio.to_thread(
            operation_result.apply_operation_result,
            tenant_id=tenant_id,
            device_id=str(device["id"]),
            invocation_id=invocation_id,
            claim_token_hash=sha256_hex(body.claim_token),
            request_id=body.request_id,
            effect=body.effect,
            phase=body.phase,
            evidence_ref=body.evidence_ref,
            safe_to_retry=body.safe_to_retry,
            code=body.code,
            message=body.message,
            permit_id=body.permit_id,
            permit_token=body.permit_token,
        )
        return {
            "success": True,
            "state": result["state"],
            "effect": result["effect"],
            "run_state": result["run_state"],
            "late": result["late"],
        }
    except operation_result.OperationResultError as e:
        logger.warning(
            f"后端日志：v2 结果回传拒绝 tenant={tenant_id} invocation={invocation_id} "
            f"code={e.code}: {e.message}"
        )
        raise HTTPException(status_code=e.http_status, detail={"error": e.code, "message": e.message})
    except HTTPException:
        raise
    except Exception as e:
        logger.opt(exception=True).error(
            f"后端日志：v2 结果回传失败 tenant={tenant_id} invocation={invocation_id}: {e}"
        )
        raise _http_error(500, "结果回传失败，请稍后重试", e)


class PayloadEndpointError(Exception):
    """payload 端点拒绝（code 对应 HTTP status；context 供审计补充）"""

    def __init__(self, code: str, http_status: int, message: str, context: Optional[Dict] = None):
        self.code = code
        self.http_status = http_status
        self.message = message
        self.context = context or {}
        super().__init__(f"{code}: {message}")


def _serve_invocation_payload(tenant_id: str, device_id: str, invocation_id: str):
    """payload 端点同步服务（线程池执行）：校验链 + resolver 取受控字节。

    校验链（与 write-authorize permits 同口径排序）：invocation 归属租户/设备（跨租户/
    跨设备统一 404 不区分存在性）→ v2 业务类型 → protocol_version=2 → 已领取未终态
    （claimed/running/cancel_requested，queued 拒绝）→ payload_ref 存在 → 场景绑定
    fail-closed（business_ref 缺 scenario/task 一律 409）→ resolve_payload（hash
    核对，不符 500+审计）。claim token 校验未实现（GET 无 body；对 R17 的明示偏差）。
    """
    inv = repository.get_invocation(invocation_id, tenant_id)
    if inv is None or str(inv["device_id"]) != device_id:
        raise PayloadEndpointError("INVOCATION_NOT_FOUND", 404, "invocation 不存在或不属于当前设备")
    if inv["business_kind"] != BUSINESS_KIND_DESKTOP_AUTOMATION:
        # 与 write-authorize 同口径：仅 v2 桌面自动任务链路（fail-closed）
        raise PayloadEndpointError(
            "NOT_DESKTOP_AUTOMATION", 409, "仅桌面自动任务 v2 invocation 支持载荷下载"
        )
    args = inv["arguments_json"] or {}
    if args.get("protocol_version") != OPERATION_PROTOCOL_V2:
        # 与 write-authorize parity（permits NOT_V2_OPERATION）
        raise PayloadEndpointError("NOT_V2_OPERATION", 409, "非 v2 操作描述，不支持载荷下载")
    if inv["state"] not in payload_resolver.PAYLOAD_ALLOWED_INVOCATION_STATES:
        if inv["state"] == "queued":
            raise PayloadEndpointError(
                "INVOCATION_NOT_CLAIMED", 409, "invocation 尚未被设备领取，拒绝下载载荷"
            )
        raise PayloadEndpointError(
            "INVOCATION_TERMINATED", 409, f"invocation 已终态，拒绝下载载荷: {inv['state']}"
        )
    payload_ref = args.get("payload_ref")
    if not payload_ref or not isinstance(payload_ref, str):
        raise PayloadEndpointError("PAYLOAD_REF_MISSING", 422, "invocation 未携带 payload_ref")
    business_ref = inv["business_ref"] or {}
    try:
        parsed_scenario, _opaque = payload_resolver.parse_payload_ref(payload_ref)
    except payload_resolver.PayloadRefError as e:
        raise PayloadEndpointError("INVALID_PAYLOAD_REF", 422, str(e))
    bound_scenario = business_ref.get("scenario_key") or ""
    bound_task_ref = business_ref.get("task_ref") or ""
    if not bound_scenario or not bound_task_ref:
        # fail-closed（与 write-authorize permits SCENARIO_BINDING_MISSING 同口径）：
        # business_ref 缺 scenario/task 绑定一律 409，不允许跳过场景一致性校验
        raise PayloadEndpointError(
            "SCENARIO_BINDING_MISSING", 409, "invocation 未绑定 scenario/task，拒绝下载载荷"
        )
    if parsed_scenario != bound_scenario:
        raise PayloadEndpointError(
            "PAYLOAD_REF_SCENARIO_MISMATCH", 422, "payload_ref 场景与 invocation 绑定场景不一致"
        )
    try:
        return payload_resolver.resolve_payload(
            tenant_id,
            payload_ref,
            expectation_hash=args.get("payload_hash"),
            user_id=str(inv["user_id"]) if inv.get("user_id") else None,
            task_ref=bound_task_ref,
            revision_ref=str(args.get("authorization_revision") or ""),
        )
    except payload_resolver.PayloadHashMismatch as e:
        raise PayloadEndpointError(
            "PAYLOAD_HASH_MISMATCH", 500, "载荷哈希校验失败，拒绝下发",
            context={
                "payload_ref": payload_ref,
                "expected_hash": e.expected_hash,
                "actual_hash": e.actual_hash,
            },
        ) from e
    except AdapterNotFoundError:
        raise PayloadEndpointError(
            "SCENARIO_NOT_REGISTERED", 500, f"场景 {parsed_scenario} 未注册受信适配器"
        )


@router.get("/runtime/invocations/{invocation_id}/payload")
async def runtime_payload(invocation_id: str, device: Dict = Depends(_require_device)):
    """v2 invocation 载荷下载（R17）：设备 token + invocation 归属设备 + 已领取未终态校验
    （queued 拒绝；claim token 校验未实现——GET 无 body，为对 R17 的明示偏差，见
    payload_resolver.PAYLOAD_ALLOWED_INVOCATION_STATES 注释）；经 TrustedAdapterRegistry
    场景适配器 serve_payload 取受控字节（不接任意 URL/路径），响应带 X-Payload-Hash、
    Content-Type 与 X-Content-Type-Options: nosniff；hash 不符 500 + 审计。"""
    tenant_id = device["tenant_id"]
    if not _valid_uuid(invocation_id):
        raise _http_error(404, "invocation 不存在")
    try:
        resolution = await asyncio.to_thread(
            _serve_invocation_payload, tenant_id, str(device["id"]), invocation_id
        )
    except PayloadEndpointError as e:
        if e.code == "PAYLOAD_HASH_MISMATCH":
            # 审计：字节与授权凭据漂移必须留痕（只记哈希与引用，不写正文）；
            # 同步 DB 调用须 to_thread，不阻塞事件循环
            await asyncio.to_thread(
                audit.log_audit,
                tenant_id, "payload_hash_mismatch", "invocation", invocation_id,
                detail={
                    "payload_ref": e.context.get("payload_ref"),
                    "expected_hash": e.context.get("expected_hash"),
                    "actual_hash": e.context.get("actual_hash"),
                },
            )
        logger.warning(
            f"后端日志：payload 下载拒绝 tenant={tenant_id} invocation={invocation_id} "
            f"code={e.code}: {e.message}"
        )
        raise HTTPException(
            status_code=e.http_status, detail={"error": e.code, "message": e.message}
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.opt(exception=True).error(
            f"后端日志：payload 下载失败 tenant={tenant_id} invocation={invocation_id}: {e}"
        )
        raise _http_error(500, "载荷下载失败，请稍后重试", e)
    return Response(
        content=resolution.data,
        media_type=resolution.mime,
        headers={
            "X-Payload-Hash": resolution.payload_hash,
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/runtime/invocations/{invocation_id}/assets/{asset_id}")
async def runtime_invocation_asset(
    invocation_id: str, asset_id: str, device: Dict = Depends(_require_device)
):
    """微信场景 invocation 素材下载（R57 P4-A）：设备 token + invocation 归属设备 +
    已领取未终态 + 场景绑定 fail-closed + revision 绑定资产集校验（素材必须被本
    invocation 绑定 revision 的 image 块引用，不接受任意 asset_id）；受控字节流 +
    X-Asset-Hash，无重定向/无任意 URL；文件 hash 与登记不符 500 + 审计。
    校验链核心在 src/weixin_marketing/assets.serve_invocation_asset（场景域逻辑
    留场景模块，此处只做 transport；懒 import 避免 local_tools → weixin 顶层依赖）。"""
    tenant_id = device["tenant_id"]
    if not _valid_uuid(invocation_id) or not _valid_uuid(asset_id):
        raise _http_error(404, "invocation 或素材不存在")
    try:
        from src.weixin_marketing.assets import AssetEndpointError, serve_invocation_asset

        resolution = await asyncio.to_thread(
            serve_invocation_asset, tenant_id, str(device["id"]), invocation_id, asset_id
        )
    except AssetEndpointError as e:
        if e.code in ("ASSET_HASH_MISMATCH", "ASSET_FILE_MISSING"):
            # 审计：素材字节漂移/失联必须留痕（只记哈希与引用，不写图片内容）
            await asyncio.to_thread(
                audit.log_audit,
                tenant_id, e.code.lower(), "invocation", invocation_id,
                detail={
                    "asset_id": e.context.get("asset_id"),
                    "invocation_id": e.context.get("invocation_id"),
                },
            )
        logger.warning(
            f"后端日志：素材下载拒绝 tenant={tenant_id} invocation={invocation_id} "
            f"asset={asset_id} code={e.code}: {e.message}"
        )
        raise HTTPException(
            status_code=e.http_status, detail={"error": e.code, "message": e.message}
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.opt(exception=True).error(
            f"后端日志：素材下载失败 tenant={tenant_id} invocation={invocation_id} "
            f"asset={asset_id}: {e}"
        )
        raise _http_error(500, "素材下载失败，请稍后重试", e)
    return Response(
        content=resolution.data,
        media_type=resolution.mime,
        headers={
            "X-Asset-Hash": resolution.sha256,
            "X-Content-Type-Options": "nosniff",
        },
    )
