"""端侧会话任务 API（C1，设计 §9/§13.5）。

用户根 `/api/session-tasks`：draft/confirm/publish/控制/查询；认证用户 + 租户
中间件；创建与发布要求 Idempotency-Key（业务写入与幂等回执同事务，R51 范式）。
设备根 `/api/local-tools/runtime/session-tasks`：设备 token（Bearer → sha256 →
local_tool_devices.token_hash 且 active）；claim/renew/events/decisions 的幂等
由协议自身保证（fence/event_id/五元唯一键），不另加 Idempotency-Key。
绑定辅助路由 `/api/weixin-conversation/bindings`（pending 骨架）。

async 端点内同步 DB 一律 asyncio.to_thread（项目异步规范）；跨租户/非属主统一
404，不泄露存在性；模型/设备身份不得调用 confirm（仅认证用户请求可达）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from uuid import UUID

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse, Response

from . import decisions as decisions_mod
from . import service
from .constants import (
    ERR_IDEMPOTENCY_CONFLICT,
    ERR_VALIDATION_FAILED,
    SessionTaskError,
)
from .models import TaskDraftCreatePayload, validate_task_spec
from .texts import digest_payload

router = APIRouter(prefix="/api/session-tasks", tags=["session-tasks"])
device_router = APIRouter(prefix="/api/local-tools/runtime/session-tasks", tags=["session-tasks-device"])
bindings_router = APIRouter(prefix="/api/weixin-conversation", tags=["weixin-conversation"])

_IDEM_TTL_SECONDS = 600
_IDEM_DDL = """
CREATE TABLE IF NOT EXISTS session_tasks_idempotency_keys (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    route TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    response_json TEXT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
    UNIQUE (tenant_id, user_id, route, idempotency_key)
)
"""


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return value


def _ok(data: Any, status_code: int = 200) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"success": True, "data": _jsonable(data)})


def _err(status_code: int, message: str, code: str, field_errors=None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"success": False, "error": message, "code": code, "field_errors": field_errors or [], "debug": None},
    )


def _from_service_error(exc: SessionTaskError) -> JSONResponse:
    return _err(exc.status_code, str(exc), exc.code)


async def _current_user_and_tenant(request: Request):
    user = await asyncio.to_thread(_get_current_user_sync, request)
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise SessionTaskError("缺少租户上下文", ERR_VALIDATION_FAILED, 400)
    user_id = user.get("user_id") or user.get("id")
    if not user_id:
        raise SessionTaskError("无法识别当前用户", "UNAUTHORIZED", 401)
    return tenant_id, str(user_id)


def _get_current_user_sync(request: Request) -> Dict[str, Any]:
    from src.api.auth import get_current_user

    user = get_current_user(request)
    if not user:
        raise SessionTaskError("未认证", "UNAUTHORIZED", 401)
    return user


def _require_device(request: Request) -> Dict[str, Any]:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise SessionTaskError("缺少设备 token", "UNAUTHORIZED", 401)
    from src.local_tools.repository import get_device_by_token_hash
    from src.local_tools.security import sha256_hex

    device = get_device_by_token_hash(sha256_hex(auth_header[7:]))
    if not device:
        raise SessionTaskError("设备 token 无效或设备已撤销", "UNAUTHORIZED", 401)
    return device


def _parse_path_uuid(value: str, field: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as exc:
        raise SessionTaskError(f"{field} 非法", ERR_VALIDATION_FAILED, 400) from exc


def _parse_spec_body(body: Dict[str, Any]) -> Dict[str, Any]:
    try:
        validated = validate_task_spec(body.get("spec") or {})
    except Exception as exc:  # noqa: BLE001 pydantic ValidationError → field_errors
        details = getattr(exc, "errors", lambda: [])() or []
        fields = [
            {"field": ".".join(str(p) for p in d.get("loc", [])), "message": d.get("msg", str(exc))}
            for d in details
        ]
        raise SessionTaskError("spec 校验失败", ERR_VALIDATION_FAILED, 400) from exc
    return {"validated_spec": validated, "raw": body}


def _execute_idempotent(tenant_id: str, user_id: str, route: str, idempotency_key: Optional[str],
                        body: Dict[str, Any], execute) -> JSONResponse:  # noqa: ANN001
    """接口幂等（设计 §9，R51 范式）：同 key 同请求返回原资源，异请求 409。

    并发竞态语义：占位用 INSERT ... ON CONFLICT DO NOTHING RETURNING——只有占位
    成功者执行业务（回执与业务写入同事务）；并发败者读占位行，TTL 内得 409
    in_progress，TTL 后（崩溃残留）可接管重执行。执行失败时删除占位行
    （abandon），避免同 key 被毒化 10 分钟。
    """
    if not idempotency_key or len(idempotency_key) > 200:
        raise SessionTaskError("必须提供 Idempotency-Key（≤200 字符）", ERR_VALIDATION_FAILED, 400)
    digest = digest_payload({"route": route, "body": body})
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(_IDEM_DDL)  # 兜底幂等表（幂等，正常路径已由 init 建好）
        cursor.execute(
            """
            INSERT INTO session_tasks_idempotency_keys (tenant_id, user_id, route, idempotency_key, request_digest, status)
            VALUES (%s, %s, %s, %s, %s, 'pending')
            ON CONFLICT (tenant_id, user_id, route, idempotency_key) DO NOTHING
            RETURNING id
            """,
            (tenant_id, user_id, route, idempotency_key, digest),
        )
        reserved = cursor.fetchone()
        if reserved is None:
            # 并发败者：读占位行判定 replay / in_progress / TTL 接管
            cursor.execute(
                """
                SELECT request_digest, status, response_json, created_at
                FROM session_tasks_idempotency_keys
                WHERE tenant_id=%s AND user_id=%s AND route=%s AND idempotency_key=%s
                """,
                (tenant_id, user_id, route, idempotency_key),
            )
            existing = cursor.fetchone()
            if existing is not None and existing["response_json"]:
                if existing["request_digest"] != digest:
                    raise SessionTaskError("同 Idempotency-Key 已绑定不同请求体", ERR_IDEMPOTENCY_CONFLICT, 409)
                return _ok(json.loads(existing["response_json"]), status_code=200)
            created = existing["created_at"] if existing else None
            created = created if created and created.tzinfo else (created.replace(tzinfo=timezone.utc) if created else None)
            if created is None or created > datetime.now(timezone.utc) - timedelta(seconds=_IDEM_TTL_SECONDS):
                raise SessionTaskError("前一同键请求仍在处理中", ERR_IDEMPOTENCY_CONFLICT, 409)
            # TTL 后崩溃残留：接管（CAS 占位）
            cursor.execute(
                """
                UPDATE session_tasks_idempotency_keys
                SET request_digest=%s, status='pending', response_json=NULL, updated_at=CURRENT_TIMESTAMP
                WHERE tenant_id=%s AND user_id=%s AND route=%s AND idempotency_key=%s
                  AND (response_json IS NULL) AND created_at <= %s
                RETURNING id
                """,
                (digest, tenant_id, user_id, route, idempotency_key,
                 datetime.now(timezone.utc) - timedelta(seconds=_IDEM_TTL_SECONDS)),
            )
            if cursor.fetchone() is None:
                raise SessionTaskError("前一同键请求仍在处理中", ERR_IDEMPOTENCY_CONFLICT, 409)
        conn.commit()

    def finalizer(conn, result: Dict[str, Any]) -> None:  # noqa: ANN001
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE session_tasks_idempotency_keys
            SET status='completed', response_json=%s, updated_at=CURRENT_TIMESTAMP
            WHERE tenant_id=%s AND user_id=%s AND route=%s AND idempotency_key=%s AND status='pending'
            """,
            (json.dumps(_jsonable(result), ensure_ascii=False), tenant_id, user_id, route, idempotency_key),
        )

    try:
        result = execute(finalizer)
    except Exception:
        # 失败 abandon：删除占位，允许调用方立即同 key 重试（不吞业务异常）
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    DELETE FROM session_tasks_idempotency_keys
                    WHERE tenant_id=%s AND user_id=%s AND route=%s AND idempotency_key=%s AND status='pending'
                    """,
                    (tenant_id, user_id, route, idempotency_key),
                )
                conn.commit()
        except Exception:  # noqa: BLE001 清理失败不影响原异常传递
            pass
        raise
    return _ok(result, status_code=201)


# ---------------------------------------------------------------------------
# 用户根
# ---------------------------------------------------------------------------


@router.post("")
async def create_task(request: Request, idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")):
    try:
        tenant_id, user_id = await _current_user_and_tenant(request)
        body = await request.json()
        payload = TaskDraftCreatePayload.model_validate(body)
        return await asyncio.to_thread(
            _execute_idempotent, tenant_id, user_id, "POST /api/session-tasks", idempotency_key, body,
            lambda finalizer: service.create_draft(tenant_id, user_id, payload, finalizer=finalizer),
        )
    except SessionTaskError as exc:
        return _from_service_error(exc)
    except Exception as exc:  # noqa: BLE001 pydantic 校验失败等
        return _validation_error(exc)


@router.get("")
async def list_tasks(request: Request, status: Optional[str] = None, limit: int = 20, offset: int = 0):
    try:
        tenant_id, user_id = await _current_user_and_tenant(request)
        result = await asyncio.to_thread(service.list_tasks, tenant_id, user_id, limit=min(limit, 100), offset=max(offset, 0), status=status)
        return _ok(result)
    except SessionTaskError as exc:
        return _from_service_error(exc)


@router.get("/{task_id}")
async def get_task(request: Request, task_id: str):
    try:
        tenant_id, user_id = await _current_user_and_tenant(request)
        result = await asyncio.to_thread(service.get_task, tenant_id, user_id, _parse_path_uuid(task_id, "task_id"))
        return _ok(result)
    except SessionTaskError as exc:
        return _from_service_error(exc)


@router.patch("/{task_id}/draft")
async def update_draft(request: Request, task_id: str):
    try:
        tenant_id, user_id = await _current_user_and_tenant(request)
        body = await request.json()
        expected_version = int(body.get("expected_version", 0))
        if expected_version <= 0:
            raise SessionTaskError("expected_version 必填", ERR_VALIDATION_FAILED, 400)
        result = await asyncio.to_thread(
            service.update_draft, tenant_id, user_id, _parse_path_uuid(task_id, "task_id"), expected_version, body.get("spec") or {}
        )
        return _ok(result)
    except SessionTaskError as exc:
        return _from_service_error(exc)
    except (TypeError, ValueError) as exc:
        return _err(400, f"参数非法: {exc}", ERR_VALIDATION_FAILED)
    except Exception as exc:  # noqa: BLE001
        return _validation_error(exc)


@router.post("/{task_id}/confirm")
async def confirm_publish(request: Request, task_id: str):
    """签发一次性发布确认（仅认证用户；绑定用户实际查看的版本+摘要，10 分钟有效）。"""
    try:
        tenant_id, user_id = await _current_user_and_tenant(request)
        body = await request.json()
        expected_version = int(body.get("expected_version", 0))
        result = await asyncio.to_thread(
            service.issue_publish_confirmation, tenant_id, user_id, _parse_path_uuid(task_id, "task_id"), expected_version
        )
        return _ok(result)
    except SessionTaskError as exc:
        return _from_service_error(exc)
    except (TypeError, ValueError) as exc:
        return _err(400, f"参数非法: {exc}", ERR_VALIDATION_FAILED)


@router.post("/{task_id}/publish")
async def publish_task(request: Request, task_id: str, idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")):
    try:
        tenant_id, user_id = await _current_user_and_tenant(request)
        body = await request.json()
        expected_version = int(body.get("expected_version", 0))
        confirmation_id = body.get("confirmation_id")
        if expected_version <= 0 or not confirmation_id:
            raise SessionTaskError("expected_version 与 confirmation_id 必填", ERR_VALIDATION_FAILED, 400)
        tid = _parse_path_uuid(task_id, "task_id")
        cid = _parse_path_uuid(str(confirmation_id), "confirmation_id")
        return await asyncio.to_thread(
            _execute_idempotent, tenant_id, user_id, f"POST /api/session-tasks/{task_id}/publish", idempotency_key, body,
            lambda finalizer: service.publish_task(tenant_id, user_id, tid, expected_version, cid, finalizer=finalizer),
        )
    except SessionTaskError as exc:
        return _from_service_error(exc)
    except (TypeError, ValueError) as exc:
        return _err(400, f"参数非法: {exc}", ERR_VALIDATION_FAILED)


async def _control_endpoint(request: Request, task_id: str, action: str) -> JSONResponse:
    try:
        tenant_id, user_id = await _current_user_and_tenant(request)
        body = await request.json()
        expected_version = int(body.get("expected_version", 0))
        if expected_version <= 0:
            raise SessionTaskError("expected_version 必填", ERR_VALIDATION_FAILED, 400)
        result = await asyncio.to_thread(
            service.control_task, tenant_id, user_id, _parse_path_uuid(task_id, "task_id"), action,
            expected_version, body.get("reason_code"),
        )
        return _ok(result)
    except SessionTaskError as exc:
        return _from_service_error(exc)
    except (TypeError, ValueError) as exc:
        return _err(400, f"参数非法: {exc}", ERR_VALIDATION_FAILED)


@router.post("/{task_id}/pause")
async def pause_task(request: Request, task_id: str):
    return await _control_endpoint(request, task_id, "pause")


@router.post("/{task_id}/resume")
async def resume_task(request: Request, task_id: str):
    return await _control_endpoint(request, task_id, "resume")


@router.post("/{task_id}/stop")
async def stop_task(request: Request, task_id: str):
    return await _control_endpoint(request, task_id, "stop")


@router.post("/{task_id}/handoff")
async def handoff_task(request: Request, task_id: str):
    return await _control_endpoint(request, task_id, "handoff")


# ---------------------------------------------------------------------------
# 设备根
# ---------------------------------------------------------------------------


@device_router.post("/claim")
async def claim(request: Request):
    try:
        device = await asyncio.to_thread(_require_device, request)
        body = await request.json()
        runtime_instance_id = str(body.get("runtime_instance_id", ""))
        result = await asyncio.to_thread(service.claim_task, device, runtime_instance_id)
        if result is None:
            return Response(status_code=204)
        return _ok(result)
    except SessionTaskError as exc:
        return _from_service_error(exc)


@device_router.post("/{assignment_id}/renew")
async def renew(request: Request, assignment_id: str):
    try:
        device = await asyncio.to_thread(_require_device, request)
        body = await request.json()
        result = await asyncio.to_thread(
            service.renew_assignment, device["tenant_id"], device["id"],
            _parse_path_uuid(assignment_id, "assignment_id"),
            int(body.get("fence", -1)), int(body.get("control_epoch", -1)),
        )
        return _ok(result)
    except SessionTaskError as exc:
        return _from_service_error(exc)
    except (TypeError, ValueError) as exc:
        return _err(400, f"参数非法: {exc}", ERR_VALIDATION_FAILED)


@device_router.post("/{assignment_id}/events")
async def events(request: Request, assignment_id: str):
    try:
        device = await asyncio.to_thread(_require_device, request)
        body = await request.json()
        records = body.get("records")
        if not isinstance(records, list):
            raise SessionTaskError("records 必须是数组", ERR_VALIDATION_FAILED, 400)
        result = await asyncio.to_thread(
            service.ingest_events, device["tenant_id"], device["id"],
            _parse_path_uuid(assignment_id, "assignment_id"),
            int(body.get("fence", -1)), records,
        )
        return _ok(result)
    except SessionTaskError as exc:
        return _from_service_error(exc)
    except (TypeError, ValueError) as exc:
        return _err(400, f"参数非法: {exc}", ERR_VALIDATION_FAILED)


@device_router.post("/{assignment_id}/decisions")
async def create_decision(request: Request, assignment_id: str):
    try:
        device = await asyncio.to_thread(_require_device, request)
        body = await request.json()
        result = await asyncio.to_thread(
            service.create_decision, device["tenant_id"], device["id"],
            _parse_path_uuid(assignment_id, "assignment_id"),
            int(body.get("fence", -1)), str(body.get("batch_id", "")), str(body.get("decision_kind", "")),
            int(body.get("input_version", 0)), int(body.get("control_epoch", -1)),
            int(body.get("spec_revision", -1)),
        )
        return _ok(result, status_code=202)
    except SessionTaskError as exc:
        return _from_service_error(exc)
    except (TypeError, ValueError) as exc:
        return _err(400, f"参数非法: {exc}", ERR_VALIDATION_FAILED)


@device_router.get("/{assignment_id}/decisions/{decision_id}")
async def get_decision(request: Request, assignment_id: str, decision_id: str):
    try:
        device = await asyncio.to_thread(_require_device, request)
        result = await asyncio.to_thread(
            service.get_decision, device["tenant_id"], device["id"],
            _parse_path_uuid(assignment_id, "assignment_id"),
            _parse_path_uuid(decision_id, "decision_id"),
        )
        return _ok(result)
    except SessionTaskError as exc:
        return _from_service_error(exc)


@device_router.post("/{assignment_id}/decisions/{decision_id}/prepare-send")
async def prepare_send(request: Request, assignment_id: str, decision_id: str):
    """ready reply/opening → 幂等物化单条底座执行单元，返回 invocation_id（§9）。

    决策已 superseded/版本失配时 200 返回 {invocation_id: null, decision_status}，
    端侧据此放弃发送相位；工作时段外 409 WORK_WINDOW_CLOSED（端侧稍后重试）。
    幂等豁免 Idempotency-Key：协议自身以 decision_id 唯一映射（execution_links
    UNIQUE(tenant, decision)）+ 底座 dedupe_key 收敛，重复请求返回同一 invocation。
    """
    try:
        device = await asyncio.to_thread(_require_device, request)
        body = await request.json()
        result = await asyncio.to_thread(
            decisions_mod.prepare_send, device["tenant_id"], device["id"],
            _parse_path_uuid(assignment_id, "assignment_id"),
            int(body.get("fence", -1)),
            _parse_path_uuid(decision_id, "decision_id"),
        )
        return _ok(result)
    except SessionTaskError as exc:
        return _from_service_error(exc)
    except (TypeError, ValueError) as exc:
        return _err(400, f"参数非法: {exc}", ERR_VALIDATION_FAILED)


@device_router.post("/{assignment_id}/invocations/{invocation_id}/claim")
async def claim_session_invocation(request: Request, assignment_id: str, invocation_id: str):
    """定向领取 session 道 invocation（§9：不领取任意 invocation；复用 claim token/
    租约与 v2 校验；通用 claim 在 SQL 层排除 session_task）。"""
    try:
        device = await asyncio.to_thread(_require_device, request)
        body = await request.json()
        from src.local_tools import catalog
        from src.local_tools.security import generate_claim_token, sha256_hex

        claim_token = generate_claim_token()
        result = await asyncio.to_thread(
            decisions_mod.claim_session_invocation,
            device["tenant_id"], device["id"],
            _parse_path_uuid(assignment_id, "assignment_id"),
            int(body.get("fence", -1)),
            _parse_path_uuid(invocation_id, "invocation_id"),
            sha256_hex(claim_token),
            60,
            catalog.get_provider_keys_for_device(device.get("capabilities_json")),
        )
        invocation = result.get("invocation")
        if invocation is None:
            return _ok({"invocation": None, "state": result.get("state")})
        default_provider = catalog.get_provider_key_for_device(device.get("capabilities_json"))
        lease_expires = invocation.get("lease_expires_at")
        return _ok({
            "invocation_id": str(invocation["id"]),
            "tool_name": invocation["tool_name"],
            "arguments": invocation["arguments_json"],
            "claim_token": claim_token,
            "lease_expires_at": lease_expires.isoformat() if hasattr(lease_expires, "isoformat") else lease_expires,
            "provider": invocation.get("provider_key") or default_provider,
        })
    except SessionTaskError as exc:
        return _from_service_error(exc)
    except (TypeError, ValueError) as exc:
        return _err(400, f"参数非法: {exc}", ERR_VALIDATION_FAILED)


# ---------------------------------------------------------------------------
# 绑定辅助路由（pending 骨架；verified 只能来自真机证据，不开接口）
# ---------------------------------------------------------------------------


@bindings_router.get("/bindings")
async def list_bindings(request: Request, device_id: str = "", limit: int = 50):
    try:
        tenant_id, user_id = await _current_user_and_tenant(request)
        from src.weixin_conversation import bindings as bindings_service

        result = await asyncio.to_thread(bindings_service.list_bindings, tenant_id, user_id, device_id, min(limit, 200))
        return _ok(result)
    except SessionTaskError as exc:
        return _from_service_error(exc)


@bindings_router.post("/bindings")
async def create_binding(request: Request):
    try:
        tenant_id, user_id = await _current_user_and_tenant(request)
        body = await request.json()
        from src.weixin_conversation import bindings as bindings_service

        result = await asyncio.to_thread(
            bindings_service.create_binding, tenant_id, user_id,
            str(body.get("device_id", "")), str(body.get("account_binding_id", "")),
            str(body.get("conversation_type", "")), str(body.get("label", "")),
        )
        return _ok(result, status_code=201)
    except SessionTaskError as exc:
        return _from_service_error(exc)


def _validation_error(exc: Exception) -> JSONResponse:
    details = getattr(exc, "errors", lambda: [])() or []
    fields = [
        {"field": ".".join(str(p) for p in d.get("loc", [])), "message": d.get("msg", str(exc))}
        for d in details
    ]
    return _err(400, "请求校验失败", ERR_VALIDATION_FAILED, field_errors=fields)
