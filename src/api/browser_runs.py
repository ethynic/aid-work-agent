"""浏览器实时视图与网页人工接管 API。"""

from __future__ import annotations

import asyncio
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from src.api.auth import get_current_user
from src.core.cache_utils import CacheKeys
from src.core.redis_client import redis_client
from src.tools.browser.executor.models import KeyboardCommand, PointerCommand
from src.tools.browser.human_control import HumanControlCoordinator, get_owned_runtime
from src.tools.browser.agent_resume_coordinator import AgentResumeCoordinator
from src.tools.browser.resume_store import ResumeStore
from src.tools.browser.run_store import create_run_store
from src.tools.browser.view_hub import browser_view_hub


router = APIRouter(prefix="/api/browser", tags=["browser-human-control"])
agent_router = APIRouter(prefix="/api/agent", tags=["agent-continuations"])
_RESUME_TASKS: set[asyncio.Task] = set()


class InputMessage(BaseModel):
    type: str
    action: str | None = None
    x: float = Field(default=0, ge=0, le=1280, allow_inf_nan=False)
    y: float = Field(default=0, ge=0, le=720, allow_inf_nan=False)
    delta_x: float = Field(default=0, ge=-10000, le=10000, allow_inf_nan=False)
    delta_y: float = Field(default=0, ge=-10000, le=10000, allow_inf_nan=False)
    key: str | None = Field(default=None, max_length=128)


def _identity(request) -> tuple[str, str]:
    user = get_current_user(request)
    tenant_id = getattr(request.state, "tenant_id", None)
    if not user or not tenant_id:
        raise HTTPException(status_code=401, detail="未登录")
    return str(tenant_id), str(user["user_id"])


async def _authorize_run(request, run_id: str):
    tenant_id, user_id = _identity(request)
    record = await create_run_store().get(tenant_id, run_id)
    if record is None or record.user_id != user_id:
        raise HTTPException(status_code=404, detail="浏览器任务不存在")
    return tenant_id, user_id, record


@router.post("/runs/{run_id}/view_ticket")
async def create_view_ticket(request: Request, run_id: str):
    tenant_id, user_id, _ = await _authorize_run(request, run_id)
    if not redis_client.is_available():
        raise HTTPException(status_code=503, detail={"error_code": "WEB_PRESENCE_REQUIRED"})
    ticket = secrets.token_urlsafe(32)
    jti = secrets.token_hex(16)
    # WebSocket API 无法由浏览器前端附加 Bearer header；ticket 本身就是一次性
    # bearer credential，因此按不可猜测的 jti 建索引，并把租户/用户归属封装在值中。
    key = redis_client.make_key(CacheKeys.BROWSER_VIEW_TICKET, jti)
    redis_client.set(key, {
        "ticket_hash": __import__("hashlib").sha256(ticket.encode()).hexdigest(),
        "tenant_id": tenant_id, "user_id": user_id, "run_id": run_id,
    }, ex=60)
    return {"ticket": f"{jti}.{ticket}", "expires_in": 60}


async def _consume_ticket(websocket: WebSocket, run_id: str, ticket: str) -> tuple[str, str] | None:
    try:
        jti, secret = ticket.split(".", 1)
    except ValueError:
        return None
    if (
        len(jti) != 32
        or any(char not in "0123456789abcdef" for char in jti)
        or not 32 <= len(secret) <= 64
    ):
        return None
    key = redis_client.make_key(CacheKeys.BROWSER_VIEW_TICKET, jti)
    lock_value = secrets.token_hex(16)
    if not redis_client.acquire_lock(key + ":consume", lock_value, ex=5):
        return None
    try:
        value = redis_client.get(key)
        redis_client.delete(key)  # 无论票据是否合法都一次性消费。
        expected = __import__("hashlib").sha256(secret.encode()).hexdigest()
        if not value or not secrets.compare_digest(value.get("ticket_hash", ""), expected):
            return None
        tenant_id = value.get("tenant_id")
        user_id = value.get("user_id")
        if not tenant_id or not user_id or value.get("run_id") != run_id:
            return None
        return str(tenant_id), str(user_id)
    finally:
        redis_client.release_lock(key + ":consume", lock_value)


@router.websocket("/runs/{run_id}/view_ws")
async def browser_view_ws(websocket: WebSocket, run_id: str, ticket: str):
    identity = await _consume_ticket(websocket, run_id, ticket)
    if identity is None:
        await websocket.close(code=4403)
        return
    tenant_id, user_id = identity
    await websocket.accept()
    subscription = await browser_view_hub.subscribe(tenant_id, run_id)
    send_lock = asyncio.Lock()

    async def send_frames():
        while True:
            frame = await subscription.next_frame()
            if subscription.closed:
                return
            if frame is None:
                async with send_lock:
                    await websocket.send_json({"type": "heartbeat"})
                continue
            # 元数据与二进制帧必须作为不可交错的一对发送，避免控制响应插入后
            # 前端把随后的 JPEG 误判为无对应元数据的孤立帧。
            async with send_lock:
                await websocket.send_json({
                    "type": "frame", "seq": frame.seq, "width": frame.width,
                    "height": frame.height, "captured_at": frame.captured_at,
                })
                await websocket.send_bytes(frame.jpeg)

    async def receive_input():
        input_tokens = 120.0
        last_refill = asyncio.get_running_loop().time()
        while True:
            message = InputMessage.model_validate(await websocket.receive_json())
            now = asyncio.get_running_loop().time()
            input_tokens = min(120.0, input_tokens + (now - last_refill) * 60.0)
            last_refill = now
            if input_tokens < 1.0:
                async with send_lock:
                    await websocket.send_json({
                        "type": "input_rejected", "error_code": "INPUT_RATE_LIMITED"
                    })
                continue
            input_tokens -= 1.0
            store_record = await create_run_store().get(tenant_id, run_id)
            if store_record is None or store_record.user_id != user_id:
                async with send_lock:
                    await websocket.close(code=4404)
                return
            if store_record.state != "RUNNING_HUMAN":
                async with send_lock:
                    await websocket.send_json({"type": "input_rejected", "error_code": "HUMAN_CONTROL_REQUIRED"})
                continue
            runtime = await get_owned_runtime(tenant_id, run_id)
            if runtime is None:
                async with send_lock:
                    await websocket.send_json({"type": "input_rejected", "error_code": "RESUME_CONTEXT_LOST"})
                continue
            fields = runtime.orchestrator.page_ops._fields()
            if message.type == "keyboard" and message.key:
                await runtime.executor.keyboard(KeyboardCommand(**fields, key=message.key))
            elif message.type == "pointer" and message.action in {"click", "move", "down", "up", "wheel"}:
                await runtime.executor.pointer(PointerCommand(
                    **fields, action=message.action, x=message.x, y=message.y,
                    delta_x=message.delta_x, delta_y=message.delta_y,
                ))

    tasks = [asyncio.create_task(send_frames()), asyncio.create_task(receive_input())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except WebSocketDisconnect:
        pass
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await subscription.close()


async def _coordinator_call(request: Request, run_id: str, assistance_id: str, method: str):
    tenant_id, user_id, record = await _authorize_run(request, run_id)
    if record.run_id != run_id:
        raise HTTPException(status_code=404, detail="浏览器任务不存在")
    coordinator = HumanControlCoordinator()
    try:
        assistance = await coordinator.store.get_assistance(tenant_id, assistance_id)
        if (
            assistance is None
            or assistance.user_id != user_id
            or assistance.run_id != run_id
        ):
            raise KeyError("ASSISTANCE_NOT_FOUND")
        return await getattr(coordinator, method)(tenant_id, user_id, assistance_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="人工协助请求不存在")
    except RuntimeError as exc:
        code = str(exc) if str(exc).isupper() else "INTERNAL_ERROR"
        status = 409 if code in {
            "CONTROL_ALREADY_TAKEN", "RESUME_ALREADY_CONSUMED",
            "HUMAN_COMPLETION_NOT_MET", "HUMAN_EXTEND_LIMIT",
        } else 410 if code in {"HUMAN_TIMEOUT", "RESUME_CONTEXT_LOST"} else 503
        raise HTTPException(status_code=status, detail={"error_code": code})


@router.post("/runs/{run_id}/take_control")
async def take_control(request: Request, run_id: str, assistance_id: str):
    record = await _coordinator_call(request, run_id, assistance_id, "take_control")
    return {"success": True, "state": record.state}


@router.post("/runs/{run_id}/assistance/{assistance_id}/complete")
async def complete_assistance(request: Request, run_id: str, assistance_id: str):
    record, missing = await _coordinator_call(request, run_id, assistance_id, "complete")
    if missing:
        return {
            "success": False, "error_code": "HUMAN_COMPLETION_NOT_MET",
            "missing_conditions": missing, "state": record.state,
        }
    task = asyncio.create_task(AgentResumeCoordinator().resume(record.tenant_id, assistance_id))
    _RESUME_TASKS.add(task)
    task.add_done_callback(_RESUME_TASKS.discard)
    return {"success": True, "state": record.state, "continuation_id": record.continuation_id}


@router.post("/runs/{run_id}/assistance/{assistance_id}/extend")
async def extend_assistance(request: Request, run_id: str, assistance_id: str):
    record = await _coordinator_call(request, run_id, assistance_id, "extend")
    return {
        "success": True, "expires_at": datetime.fromtimestamp(
            record.expires_at, timezone.utc
        ).isoformat(),
    }


@router.post("/runs/{run_id}/cancel")
async def cancel_run(request: Request, run_id: str, assistance_id: str):
    await _coordinator_call(request, run_id, assistance_id, "cancel")
    return {"success": True}


@agent_router.get("/continuations/{continuation_id}/events")
async def continuation_events(request: Request, continuation_id: str, after_seq: int = 0):
    tenant_id, user_id = _identity(request)
    store = ResumeStore()
    events = await store.events_after(tenant_id, continuation_id, max(0, after_seq))
    # continuation id 只能经该用户 assistance 获得；事件本身不含敏感正文。
    matching = False
    pattern = redis_client.make_key(CacheKeys.BROWSER_ASSISTANCE, f"{tenant_id}:*")
    cursor = 0
    while True:
        cursor, keys = redis_client.scan(cursor, pattern, 100)
        for key in keys:
            item = redis_client.get(key)
            if item and item.get("continuation_id") == continuation_id and item.get("user_id") == user_id:
                matching = True
                break
        if matching or cursor == 0:
            break
    if not matching:
        raise HTTPException(status_code=404, detail="续跑事件不存在")
    return {"events": events, "last_seq": events[-1]["seq"] if events else after_seq}
