"""腾讯文档知识库导入 API（任务 #5/#7）

- 凭据绑定（开发期个人测试 Token；任务 #2 OAuth 接入后换授权流，接口形态不变）
- 目录浏览代理（服务端持凭据调 OpenAPI，token 不下发前端）
- 导入批次：创建（预检+配额闸门+无变化跳过）/ 列表 / 详情 / 重试

认证：与 knowledge/api.py 同模式（get_current_user + get_current_tenant_id）。
"""

import asyncio
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from loguru import logger
from pydantic import BaseModel, Field

from src.api import auth
from src.db.tdoc_import import TdocImportBatchDB, TdocImportItemDB, TdocImportedDocDB
from src.saas.context import get_current_tenant_id
from src.services.tencent_docs import oauth as tdoc_oauth
from src.services.tencent_docs.client import TencentDocsClient, TencentDocsError
from src.services.tencent_docs.credentials import (
    CredentialsNotBoundError,
    delete_credentials,
    get_credentials_with_refresh,
    get_status,
    save_credentials,
    save_oauth_credentials,
)
from src.services.tencent_docs.import_service import create_batch

router = APIRouter(prefix="/api/knowledge/tdoc", tags=["knowledge-tdoc"])


class SaveAuthRequest(BaseModel):
    client_id: str = Field(min_length=1, description="Client ID")
    access_token: str = Field(min_length=1, description="Access Token")
    open_id: str = Field(min_length=1, description="Open ID")
    refresh_token: Optional[str] = Field(default=None, description="Refresh Token（OAuth 接入后使用）")


class ImportFileItem(BaseModel):
    file_id: str = Field(description="内容操作 ID（快捷方式文档传 shortcutID）")
    title: str = ""
    doc_type: str = ""
    last_modify_time: Optional[int] = None


class CreateImportRequest(BaseModel):
    files: List[ImportFileItem]


def _require_auth(http_request: Request) -> Dict[str, Any]:
    current_user = auth.get_current_user(http_request)
    if not current_user:
        raise HTTPException(status_code=401, detail="未登录")
    tenant_id = get_current_tenant_id()
    if not tenant_id:
        raise HTTPException(status_code=403, detail="缺少租户上下文")
    return {"user_id": str(current_user.get("user_id")), "tenant_id": tenant_id}


# ------------------------------- 凭据绑定 -------------------------------

@router.get("/auth")
async def get_auth_status(http_request: Request):
    ctx = _require_auth(http_request)
    return {
        "success": True,
        "oauth_enabled": tdoc_oauth.is_oauth_configured(),
        **get_status(ctx["tenant_id"], ctx["user_id"]),
    }


@router.post("/auth")
async def save_auth(request: SaveAuthRequest, http_request: Request):
    ctx = _require_auth(http_request)
    try:
        save_credentials(
            tenant_id=ctx["tenant_id"],
            user_id=ctx["user_id"],
            client_id=request.client_id.strip(),
            access_token=request.access_token.strip(),
            open_id=request.open_id.strip(),
            refresh_token=(request.refresh_token or "").strip() or None,
        )
    except Exception as e:
        logger.opt(exception=True).error(f"腾讯文档凭据保存失败: {e}")
        raise HTTPException(status_code=500, detail="凭据保存失败")
    return {"success": True, **get_status(ctx["tenant_id"], ctx["user_id"])}


@router.delete("/auth")
async def remove_auth(http_request: Request):
    ctx = _require_auth(http_request)
    deleted = delete_credentials(ctx["tenant_id"], ctx["user_id"])
    return {"success": True, "deleted": deleted}


# ------------------------------- OAuth 授权流（任务 #2） -------------------------------

def _auth_page_url(tenant_id: str, ok: bool, reason: str = "") -> str:
    """授权结果回跳前端页面（与 redirect_uri 同 origin 的租户前台）。"""
    base = tdoc_oauth.frontend_base_url()
    result = "success" if ok else "failed"
    url = f"{base}/t/{tenant_id}/knowledge/tdoc-imports?tdoc_auth={result}"
    if reason:
        url += f"&reason={quote(reason)}"
    return url


@router.get("/oauth/start")
async def start_oauth(http_request: Request):
    """发起 OAuth 授权：登录态用户获取绑定到自身身份的授权跳转 URL。"""
    ctx = _require_auth(http_request)
    if not tdoc_oauth.is_oauth_configured():
        raise HTTPException(status_code=409, detail="腾讯文档 OAuth 未配置，请联系管理员")
    if not await asyncio.to_thread(tdoc_oauth.redis_available):
        # state 是跨 worker 会话凭据：内存降级会让回调必然 fail-closed，
        # 与其让用户白走腾讯授权页再失败，不如发起前 fail-fast
        raise HTTPException(status_code=503, detail="缓存服务不可用，请稍后再试")
    try:
        state = tdoc_oauth.create_oauth_state(ctx["tenant_id"], ctx["user_id"])
        authorize_url = tdoc_oauth.build_authorize_url(state)
    except Exception as e:
        logger.opt(exception=True).error(f"腾讯文档 OAuth state 生成失败: {e}")
        raise HTTPException(status_code=500, detail="授权发起失败，请稍后重试")
    return {"success": True, "authorize_url": authorize_url}


@router.get("/oauth/callback")
async def oauth_callback(
    http_request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    error_description: str = "",
):
    """腾讯文档 OAuth 回调（浏览器 302 进入，无认证头；身份由一次性 state 还原）。

    成功 -> 302 回 /t/{tenant}/knowledge/tdoc-imports?tdoc_auth=success
    失败 -> 302 回同页 ?tdoc_auth=failed&reason=...（state 无效时退回站点首页）
    """
    payload = tdoc_oauth.consume_oauth_state(state)
    if not payload:
        # state 无效/过期：无法定位租户页面，退回前端站点根
        return RedirectResponse(
            f"{tdoc_oauth.frontend_base_url()}/?tdoc_auth=failed&reason={quote('授权会话已过期，请重新发起')}",
            status_code=302,
        )
    tenant_id, user_id = payload["tenant_id"], payload["user_id"]

    if error:
        reason = error_description or error
        logger.warning(f"腾讯文档 OAuth 回调报错 tenant_id={tenant_id} user_id={user_id}: {error} {error_description}")
        return RedirectResponse(_auth_page_url(tenant_id, ok=False, reason=reason), status_code=302)

    if not code:
        return RedirectResponse(_auth_page_url(tenant_id, ok=False, reason="回调缺少授权码"), status_code=302)

    try:
        token = await tdoc_oauth.exchange_code(code)
        userinfo = await tdoc_oauth.get_userinfo(token.access_token)
        await save_oauth_credentials(tenant_id, user_id, token, userinfo)
    except tdoc_oauth.OAuthError as e:
        logger.error(f"腾讯文档 OAuth code 换取失败 tenant_id={tenant_id} user_id={user_id}: {e}")
        return RedirectResponse(_auth_page_url(tenant_id, ok=False, reason=e.message), status_code=302)
    except Exception as e:
        logger.opt(exception=True).error(f"腾讯文档 OAuth 凭据落库失败: {e}")
        return RedirectResponse(_auth_page_url(tenant_id, ok=False, reason="授权处理失败，请重试"), status_code=302)

    logger.info(f"腾讯文档 OAuth 授权完成 tenant_id={tenant_id} user_id={user_id} open_id={token.open_id}")
    return RedirectResponse(_auth_page_url(tenant_id, ok=True), status_code=302)


# ------------------------------- 目录浏览 -------------------------------

@router.get("/files")
async def list_files(
    http_request: Request,
    folder_id: str = "",
    start: int = 0,
    limit: int = 50,
):
    """代理浏览腾讯文档目录（分页，start=0 表示根目录；返回 next 供翻页）。"""
    ctx = _require_auth(http_request)
    try:
        credentials = await get_credentials_with_refresh(ctx["tenant_id"], ctx["user_id"])
    except CredentialsNotBoundError as e:
        raise HTTPException(status_code=409, detail=str(e))

    try:
        async with TencentDocsClient(credentials) as client:
            items, next_start = await client.list_folder(folder_id, start=start, limit=min(limit, 100))
    except TencentDocsError as e:
        raise HTTPException(status_code=502, detail=f"腾讯文档接口错误：{e.message}")

    imported_map = TdocImportedDocDB.get_all(ctx["tenant_id"]) if not folder_id else {}
    files = []
    for f in items:
        mapped = imported_map.get(f.file_id)
        unchanged = bool(
            mapped
            and f.last_modify_time
            and mapped.get("last_modify_time") is not None
            and int(mapped["last_modify_time"]) == f.last_modify_time
        )
        files.append({
            "file_id": f.file_id,
            "content_id": f.content_target_id(),
            "title": f.title,
            "type": f.type,
            "last_modify_time": f.last_modify_time,
            "is_owner": f.is_owner,
            "imported": bool(mapped),
            "unchanged": unchanged,
        })
    return {"success": True, "items": files, "next": next_start}


# ------------------------------- 导入批次 -------------------------------

@router.post("/imports")
async def create_import(request: CreateImportRequest, http_request: Request):
    """创建导入批次（预检 + 配额闸门 + 无变化跳过在服务端完成）。"""
    ctx = _require_auth(http_request)
    try:
        result = await create_batch(
            tenant_id=ctx["tenant_id"],
            user_id=ctx["user_id"],
            files=[f.model_dump() for f in request.files],
        )
    except CredentialsNotBoundError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if not result.get("success"):
        return result  # 业务拒绝（如配额不足），前端展示 error
    return result


def _serialize_item(row: Dict[str, Any]) -> Dict[str, Any]:
    d = dict(row)
    for k in ("created_at", "updated_at", "next_retry_at"):
        if d.get(k) is not None:
            d[k] = d[k].isoformat()
    return d


@router.get("/imports")
async def list_imports(http_request: Request, page: int = 1, page_size: int = 20):
    ctx = _require_auth(http_request)
    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    batches, total = TdocImportBatchDB.list_by_tenant(ctx["tenant_id"], page, page_size)
    return {"success": True, "items": batches, "total": total, "page": page, "page_size": page_size}


@router.get("/imports/{batch_id}")
async def get_import(batch_id: str, http_request: Request):
    """批次详情 + items（前端轮询此接口，2s 间隔）。"""
    ctx = _require_auth(http_request)
    batch = TdocImportBatchDB.get(batch_id, ctx["tenant_id"])
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")
    items = TdocImportItemDB.list_by_batch(batch_id)
    return {
        "success": True,
        "batch": batch,
        "items": [_serialize_item(i) for i in items],
    }


@router.post("/imports/{batch_id}/retry")
async def retry_import(batch_id: str, http_request: Request):
    """重试批次内 failed items（重置为 pending，调度器下一轮领取）。"""
    ctx = _require_auth(http_request)
    batch = TdocImportBatchDB.get(batch_id, ctx["tenant_id"])
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")
    if batch.get("status") == "running":
        return {"success": False, "error": "批次执行中，无法重试"}
    reset = TdocImportItemDB.reset_failed_for_retry(batch_id)
    return {"success": True, "reset_count": reset}
