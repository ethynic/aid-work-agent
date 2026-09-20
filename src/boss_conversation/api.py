"""BOSS 候选人绑定管理 API（B2，设计 §5.2/§5.5.5）。

/api/boss-conversation/bindings：list / create-pending / invalidate / unblock
（owner-only + expected_block_epoch CAS）。owner ACL：user_id 必须匹配绑定/设备
属主（服务层强制）；verified 不开接口（仅 Provider 真机证据）。
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request

from src.session_tasks.api import _current_user_and_tenant, _err, _from_service_error, _ok
from src.session_tasks.constants import SessionTaskError

from .constants import SCENARIO_KEY

router = APIRouter(prefix="/api/boss-conversation", tags=["boss-conversation"])


@router.get("/bindings")
async def list_bindings(request: Request, device_id: str = "", limit: int = 50):
    try:
        tenant_id, user_id = await _current_user_and_tenant(request)
        from . import bindings as bindings_service

        result = await asyncio.to_thread(
            bindings_service.list_bindings, tenant_id, user_id, device_id, min(limit, 200)
        )
        return _ok({"scenario_key": SCENARIO_KEY, "bindings": result})
    except SessionTaskError as exc:
        return _from_service_error(exc)


@router.post("/bindings")
async def create_pending_binding(request: Request):
    """create-pending（从既有"已沟通"数据导入；verified 仅真机证据，不开接口）。"""
    try:
        tenant_id, user_id = await _current_user_and_tenant(request)
        body = await request.json()
        resume_id = body.get("resume_id")
        result = await asyncio.to_thread(
            _create_pending,
            tenant_id, user_id,
            device_id=str(body.get("device_id", "")),
            account_scope_id=str(body.get("account_scope_id", "")),
            candidate_name=str(body.get("candidate_name", "")),
            job_id=str(body.get("job_id", "")),
            resume_id=int(resume_id) if resume_id not in (None, "") else None,
            login_fingerprint_hash=body.get("login_fingerprint_hash") or None,
        )
        return _ok(result, status_code=201)
    except SessionTaskError as exc:
        return _from_service_error(exc)
    except (ValueError, TypeError):
        return _err(400, "请求参数非法（resume_id 须为整数）", "VALIDATION_FAILED")


def _create_pending(tenant_id: str, user_id: str, **kwargs):  # noqa: ANN003
    from . import bindings as bindings_service

    return bindings_service.create_pending_binding(tenant_id, user_id, **kwargs)


@router.post("/bindings/{binding_id}/invalidate")
async def invalidate_binding(binding_id: str, request: Request):
    try:
        tenant_id, user_id = await _current_user_and_tenant(request)
        from . import bindings as bindings_service

        result = await asyncio.to_thread(
            bindings_service.invalidate_binding, tenant_id, user_id, binding_id
        )
        return _ok(result)
    except SessionTaskError as exc:
        return _from_service_error(exc)


@router.post("/bindings/{binding_id}/unblock")
async def unblock_binding(binding_id: str, request: Request):
    """人工解阻（owner-only + expected_block_epoch CAS；解阻 ≠ 任务恢复）。

    解阻成功仅清除 binding 同步阻断；human_required 任务仍须既有显式 resume。"""
    try:
        tenant_id, user_id = await _current_user_and_tenant(request)
        body = await request.json()
        expected = body.get("expected_block_epoch")
        if expected is None or not isinstance(expected, int) or isinstance(expected, bool):
            return _err(400, "expected_block_epoch 必填（整数）", "VALIDATION_FAILED")
        from . import bindings as bindings_service

        result = await asyncio.to_thread(
            bindings_service.unblock_binding, tenant_id, user_id, binding_id,
            expected_block_epoch=expected,
        )
        return _ok(result)
    except SessionTaskError as exc:
        return _from_service_error(exc)
