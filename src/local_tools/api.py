"""本地工具 API：Web 用户 API + Runtime API

- Web 用户 API：get_current_user + request.state.tenant_id（TenantContextMiddleware 解析）
- Runtime API：设备 Bearer token 自认证（_require_device 依赖），所有隔离键取 device.tenant_id，
  不信任何请求体里的 tenant/user

所有同步 DB 调用一律 asyncio.to_thread 包裹，不阻塞事件循环。
"""

import asyncio
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from loguru import logger

from src.api.auth import get_current_user
from src.local_tools import catalog, pairing, repository
from src.local_tools.models import (
    HeartbeatRequest,
    PairRequest,
    ProgressRequest,
    ResultRequest,
    StartedRequest,
)
from src.local_tools.security import generate_claim_token, sha256_hex
from src.utils import sanitize_error_info

router = APIRouter(prefix="/api/local-tools", tags=["local-tools"])

LEASE_SECONDS = 60          # claim 租约时长
CLAIM_POLL_INTERVAL = 0.5   # 长轮询间隔（秒）
CLAIM_MAX_WAIT = 30         # 长轮询最长等待（秒）
ONLINE_THRESHOLD_SECONDS = 30  # last_seen_at 距今 ≤30s 视为在线
PROGRESS_MESSAGE_MAX_LEN = 500


def _http_error(status_code: int, error: str, exc: Optional[Exception] = None) -> HTTPException:
    """统一错误响应：{error, debug(sanitize 后)}"""
    detail: Dict[str, Any] = {"error": error}
    if exc is not None:
        detail["debug"] = sanitize_error_info(str(exc))
    return HTTPException(status_code=status_code, detail=detail)


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


# ==================== Web 用户 API ====================


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
        logger.error(f"后端日志：创建配对码失败: {e}", exc_info=True)
        raise _http_error(500, "创建配对码失败，请稍后重试", e)


@router.get("/devices")
async def list_devices(request: Request):
    """当前 tenant+user 设备列表（含在线状态与选定标记）"""
    user, tenant_id = await _current_user_and_tenant(request)
    try:
        devices = await asyncio.to_thread(repository.list_devices, tenant_id, user["user_id"])
        now = datetime.now()
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
                }
            )
        return {"success": True, "devices": items}
    except Exception as e:
        logger.error(f"后端日志：查询设备列表失败: {e}", exc_info=True)
        raise _http_error(500, "查询设备列表失败，请稍后重试", e)


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
        logger.error(f"后端日志：选定设备失败 device={device_id}: {e}", exc_info=True)
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
        logger.error(f"后端日志：撤销设备失败 device={device_id}: {e}", exc_info=True)
        raise _http_error(500, "撤销设备失败，请稍后重试", e)


# ==================== Runtime API ====================


@router.post("/runtime/pair")
async def runtime_pair(body: PairRequest):
    """配对码换设备 + 设备 token（均无 header；token 明文仅此一次返回）"""
    try:
        result = await asyncio.to_thread(
            pairing.pair,
            body.code,
            body.name,
            body.platform,
            body.runtime_version,
            body.capabilities,
            body.machine_fingerprint,
        )
        if not result:
            raise _http_error(400, "配对码无效、已过期或已被使用")
        return {"success": True, **result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"后端日志：设备配对失败: {e}", exc_info=True)
        raise _http_error(500, "设备配对失败，请稍后重试", e)


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
        )
        if not row:
            raise _http_error(404, "设备不存在")
        return {
            "success": True,
            "selected": bool(row.get("selected")),
            "server_time": datetime.now().isoformat(),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"后端日志：设备心跳失败 device={device['id']}: {e}", exc_info=True)
        raise _http_error(500, "心跳失败，请稍后重试", e)


@router.post("/runtime/claim")
async def runtime_claim(wait: int = 20, device: Dict = Depends(_require_device)):
    """长轮询领取 invocation（claim_token 明文仅此一次返回）。

    超时返回 {"success": true, "invocation": null}（200，简化客户端）。
    """
    device_id = str(device["id"])
    tenant_id = device["tenant_id"]
    provider_key = catalog.get_provider_key_for_device(device.get("capabilities_json"))
    wait = max(0, min(wait, CLAIM_MAX_WAIT))

    try:
        # 顺带清理租约过期的 claimed/running（MVP 无后台任务）
        await asyncio.to_thread(repository.expire_stale_claims)

        deadline = asyncio.get_event_loop().time() + wait
        while True:
            claim_token = generate_claim_token()
            invocation = await asyncio.to_thread(
                repository.claim_next, device_id, tenant_id, sha256_hex(claim_token), LEASE_SECONDS
            )
            if invocation:
                # 校验 tool_name 在该设备 Provider 批准的 tools 内
                if not catalog.is_tool_allowed(provider_key, invocation["tool_name"]):
                    logger.warning(
                        f"后端日志：invocation {invocation['id']} 工具 "
                        f"{invocation['tool_name']} 不在设备 Provider 批准清单内，置为失败"
                    )
                    await asyncio.to_thread(
                        repository.write_result,
                        str(invocation["id"]),
                        tenant_id,
                        sha256_hex(claim_token),
                        False,
                        "TOOL_NOT_ALLOWED",
                        "工具不在设备 Provider 批准清单内",
                    )
                else:
                    logger.info(
                        f"后端日志：invocation {invocation['id']} 被设备 {device_id} 领取 "
                        f"tool={invocation['tool_name']}"
                    )
                    return {
                        "success": True,
                        "invocation_id": str(invocation["id"]),
                        "tool_name": invocation["tool_name"],
                        "arguments": invocation["arguments_json"],
                        "claim_token": claim_token,
                        "lease_expires_at": _iso(invocation["lease_expires_at"]),
                        "provider": provider_key,
                    }
            if asyncio.get_event_loop().time() >= deadline:
                return {"success": True, "invocation": None}
            await asyncio.sleep(CLAIM_POLL_INTERVAL)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"后端日志：设备领取 invocation 失败 device={device_id}: {e}", exc_info=True)
        raise _http_error(500, "领取任务失败，请稍后重试", e)


@router.post("/runtime/invocations/{invocation_id}/started")
async def runtime_started(
    invocation_id: str, body: StartedRequest, device: Dict = Depends(_require_device)
):
    """claimed → running（幂等：重复调用返回当前状态）"""
    tenant_id = device["tenant_id"]
    try:
        row = await asyncio.to_thread(
            repository.mark_started, invocation_id, tenant_id, sha256_hex(body.claim_token)
        )
        if not row:
            raise _http_error(404, "invocation 不存在或 claim token 不匹配")
        if row["state"] != "running":
            raise _http_error(409, f"当前状态不允许标记开始: {row['state']}")
        return {"success": True, "state": row["state"]}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"后端日志：标记 invocation 开始失败 id={invocation_id}: {e}", exc_info=True)
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
        logger.error(f"后端日志：上报进度失败 id={invocation_id}: {e}", exc_info=True)
        raise _http_error(500, "上报进度失败，请稍后重试", e)


@router.post("/runtime/invocations/{invocation_id}/result")
async def runtime_result(
    invocation_id: str, body: ResultRequest, device: Dict = Depends(_require_device)
):
    """写入终态（幂等：已终态重复写返回当前状态）"""
    tenant_id = device["tenant_id"]
    try:
        row = await asyncio.to_thread(
            repository.write_result,
            invocation_id,
            tenant_id,
            sha256_hex(body.claim_token),
            body.success,
            body.code,
            body.message,
            body.effect,
            body.data,
            body.retryable,
        )
        if not row:
            raise _http_error(404, "invocation 不存在或 claim token 不匹配")
        logger.info(
            f"后端日志：invocation {invocation_id} 写入终态 state={row['state']} effect={row['effect']}"
        )
        return {"success": True, "state": row["state"], "effect": row["effect"]}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"后端日志：写入 invocation 终态失败 id={invocation_id}: {e}", exc_info=True)
        raise _http_error(500, "写入结果失败，请稍后重试", e)
