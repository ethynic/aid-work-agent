"""
协会客户端激活码 / 绑定 管理 API（平台管理员用）。

设计文档：docs/tools/association-client-design.md §2.5
路由前缀：/api/saas/client-activations
- POST /                生成激活码（指定 tenant_id）
- GET  /list            列出激活码（按 tenant 过滤）
- GET  /{code_id}       查看激活码详情
- DELETE /{code_id}     禁用激活码
- POST /{code_id}/revoke 吊销已激活的绑定

路由前缀：/api/saas/client-bindings
- GET  /list            列出客户端绑定
- POST /{binding_id}/disable  禁用绑定（踢下线）
- POST /{binding_id}/rotate-token  轮换 access_token

权限：仅 platform_admin
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from loguru import logger
from pydantic import BaseModel

from src.db.client_binding_db import ClientActivationCodeDB, ClientBindingDB
from src.saas.api.tenant_auth import require_admin

activation_router = APIRouter(prefix="/api/saas/client-activations", tags=["SaaS 客户端激活码管理"])
binding_router = APIRouter(prefix="/api/saas/client-bindings", tags=["SaaS 客户端绑定管理"])


# ============== 请求模型 ==============

class ActivationCodeCreateRequest(BaseModel):
    tenant_id: str
    client_name: Optional[str] = None
    expires_at: Optional[str] = None  # ISO 8601
    max_uses: int = 1


# ============== 激活码管理 ==============

@activation_router.post("")
async def create_activation_code(req: ActivationCodeCreateRequest, request: Request):
    """生成激活码（明文 code 仅此一次返回）。"""
    require_admin(request)

    expires_at = None
    if req.expires_at:
        try:
            expires_at = datetime.fromisoformat(req.expires_at)
        except ValueError:
            raise HTTPException(status_code=422, detail="expires_at 格式错误，需 ISO 8601")

    record = ClientActivationCodeDB.create(
        tenant_id=req.tenant_id,
        client_name=req.client_name,
        expires_at=expires_at,
        max_uses=req.max_uses,
    )
    # 明文 code 仅此一次返回给管理员
    logger.info(f"激活码生成 tenant={req.tenant_id} id={record['id']} client_name={req.client_name}")
    return {
        "id": record["id"],
        "code": record["code"],  # ⚠️ 仅此一次明文返回
        "tenant_id": record["tenant_id"],
        "client_name": record["client_name"],
        "status": record["status"],
        "max_uses": record["max_uses"],
        "expires_at": record.get("expires_at"),
        "created_at": record["created_at"].isoformat() if record.get("created_at") else None,
    }


@activation_router.get("/list")
async def list_activation_codes(
    request: Request,
    tenant_id: Optional[str] = Query(None),
):
    """列出激活码（支持按 tenant 过滤）。不含 code_hash。"""
    require_admin(request)
    if tenant_id:
        records = ClientActivationCodeDB.list_by_tenant(tenant_id)
    else:
        # 全量（管理员视角）；DB 层暂无 list_all，复用按租户查的方式需扩展
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, code, tenant_id, client_name, status, max_uses, used_count, "
                "activated_at, expires_at, created_at FROM client_activation_codes "
                "ORDER BY created_at DESC LIMIT 200"
            )
            records = [dict(r) for r in cursor.fetchall()]

    return [
        {
            "id": r["id"],
            "code": r["code"],
            "tenant_id": r["tenant_id"],
            "client_name": r.get("client_name"),
            "status": r["status"],
            "max_uses": r.get("max_uses", 1),
            "used_count": r.get("used_count", 0),
            "activated_at": r["activated_at"].isoformat() if r.get("activated_at") else None,
            "expires_at": r["expires_at"].isoformat() if r.get("expires_at") else None,
            "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
        }
        for r in records
    ]


@activation_router.get("/{code_id}")
async def get_activation_code(code_id: int, request: Request):
    """查看激活码详情。"""
    require_admin(request)
    record = ClientActivationCodeDB.get_by_id(code_id)
    if not record:
        raise HTTPException(status_code=404, detail="ACTIVATION_CODE_NOT_FOUND")
    return {
        "id": record["id"],
        "code": record["code"],
        "tenant_id": record["tenant_id"],
        "client_name": record.get("client_name"),
        "status": record["status"],
        "max_uses": record.get("max_uses", 1),
        "used_count": record.get("used_count", 0),
        "activated_at": record["activated_at"].isoformat() if record.get("activated_at") else None,
        "activated_machine": record.get("activated_machine"),
        "expires_at": record["expires_at"].isoformat() if record.get("expires_at") else None,
        "created_at": record["created_at"].isoformat() if record.get("created_at") else None,
    }


@activation_router.delete("/{code_id}")
async def disable_activation_code(code_id: int, request: Request):
    """禁用激活码（未激活的不可再激活）。"""
    require_admin(request)
    record = ClientActivationCodeDB.get_by_id(code_id)
    if not record:
        raise HTTPException(status_code=404, detail="ACTIVATION_CODE_NOT_FOUND")
    ClientActivationCodeDB.disable(code_id)
    logger.info(f"激活码禁用 id={code_id} tenant={record['tenant_id']}")
    return {"ok": True}


@activation_router.post("/{code_id}/revoke")
async def revoke_binding_by_code(code_id: int, request: Request):
    """吊销该激活码关联的客户端绑定（下线客户端）。"""
    require_admin(request)
    record = ClientActivationCodeDB.get_by_id(code_id)
    if not record:
        raise HTTPException(status_code=404, detail="ACTIVATION_CODE_NOT_FOUND")

    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT binding_id FROM client_bindings WHERE activation_code_id = %s AND status = 'active'",
            (code_id,),
        )
        bindings = [dict(r)["binding_id"] for r in cursor.fetchall()]

    for bid in bindings:
        ClientBindingDB.disable(bid)
    logger.info(f"激活码 {code_id} 吊销绑定 {len(bindings)} 个")
    return {"ok": True, "revoked_count": len(bindings)}


# ============== 绑定管理 ==============

@binding_router.get("/list")
async def list_bindings(
    request: Request,
    tenant_id: Optional[str] = Query(None),
):
    """列出客户端绑定。"""
    require_admin(request)
    if tenant_id:
        records = ClientBindingDB.list_by_tenant(tenant_id)
    else:
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, binding_id, tenant_id, activation_code_id, client_name, machine_id, "
                "status, last_seen_at, expires_at, created_at FROM client_bindings "
                "ORDER BY created_at DESC LIMIT 200"
            )
            records = [dict(r) for r in cursor.fetchall()]

    return [
        {
            "id": r["id"],
            "binding_id": r["binding_id"],
            "tenant_id": r["tenant_id"],
            "client_name": r.get("client_name"),
            "machine_id": r.get("machine_id"),
            "status": r["status"],
            "last_seen_at": r["last_seen_at"].isoformat() if r.get("last_seen_at") else None,
            "expires_at": r["expires_at"].isoformat() if r.get("expires_at") else None,
            "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
        }
        for r in records
    ]


@binding_router.post("/{binding_id}/disable")
async def disable_binding(binding_id: str, request: Request):
    """禁用绑定（踢下线）。"""
    require_admin(request)
    binding = ClientBindingDB.get_by_id(binding_id)
    if not binding:
        raise HTTPException(status_code=404, detail="BINDING_NOT_FOUND")
    ClientBindingDB.disable(binding_id)
    logger.info(f"客户端绑定禁用 binding_id={binding_id}")
    return {"ok": True}


@binding_router.post("/{binding_id}/rotate-token")
async def rotate_token(binding_id: str, request: Request):
    """轮换 access_token（返回新明文）。"""
    require_admin(request)
    new_binding = ClientBindingDB.rotate_token(binding_id)
    if not new_binding:
        raise HTTPException(status_code=404, detail="BINDING_NOT_FOUND")
    logger.info(f"客户端绑定令牌轮换 binding_id={binding_id}")
    return {"ok": True, "access_token": new_binding["access_token"]}
