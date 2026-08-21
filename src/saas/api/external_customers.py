"""
外部接待客户 API

路由：/api/saas/external-customers/*
- 外部用户列表查询（source 不为空的外部用户）
- 外部用户会话列表
- 会话消息查询

外部用户：指 users.source 不为空的客户，如 wecom_kf（企业微信客服）
"""

from typing import List, Optional
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from loguru import logger

from src.saas.api.tenant_auth import require_admin
from src.db.models import UserDB
from src.config.settings import settings

router = APIRouter(prefix="/api/saas/external-customers", tags=["外部接待客户"])


def _resolve_visible_kf_ids(admin: dict, tenant_id: str) -> Optional[List[str]]:
    """当前用户可见的客服账号 open_kfid 集合。

    管理员（platform_admin / tenant_admin）返回 None 表示全量可见；
    普通用户（引流员工）返回其负责的客服账号（kf_account.tenant_user_id == 自己）。
    """
    if admin.get("role") != "user":
        return None
    from src.saas.db.channel_config_db import ChannelConfigDB

    ids = []
    for cfg in ChannelConfigDB.list_by_tenant(tenant_id, "wecom_kf"):
        for kf in cfg.get("config", {}).get("kf_account", []):
            if kf.get("tenant_user_id") == admin.get("user_id") and kf.get("open_kfid"):
                ids.append(kf["open_kfid"])
    return ids


# ============== 请求模型 ==============

class ExternalUserQuery(BaseModel):
    username: Optional[str] = Field(None, description="用户名/昵称搜索")
    source: Optional[str] = Field(None, description="用户来源")
    page: int = Field(1, ge=1, description="页码")
    page_size: int = Field(20, ge=1, le=100, description="每页数量")


class SessionQuery(BaseModel):
    instance_id: Optional[str] = Field(None, description="数字员工实例ID")
    page: int = Field(1, ge=1, description="页码")
    page_size: int = Field(20, ge=1, le=100, description="每页数量")


class MessageQuery(BaseModel):
    content_search: Optional[str] = Field(None, description="聊天内容搜索")
    page: int = Field(1, ge=1, description="页码")
    page_size: int = Field(50, ge=1, le=200, description="每页数量")


# ============== API 端点 ==============

@router.get("/users")
async def list_external_users(
    request: Request,
    username: Optional[str] = None,
    source: Optional[str] = None,
    referrer_user_id: Optional[str] = None,
    channel_chat_id: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    """获取外部用户列表（source 不为空的租户用户）

    Args:
        username: 用户名/昵称搜索（可选）
        source: 用户来源筛选（可选）
        referrer_user_id: 引流员工筛选（可选，引流统计下钻时传入）
        channel_chat_id: 客服账号（open_kfid）筛选（可选，客服账号下拉框筛选时传入）
        page: 页码
        page_size: 每页数量
    """
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)
    tenant_id = admin.get("tenant_id")

    if not tenant_id:
        raise HTTPException(status_code=400, detail="缺少租户信息")

    # 普通用户（引流员工）只能看到自己负责的客服账号相关客户 + 自己引流的客户
    visible_kf_ids = _resolve_visible_kf_ids(admin, tenant_id)

    result = UserDB.list_external_users(
        tenant_id=tenant_id,
        username=username,
        source=source,
        referrer_user_id=referrer_user_id,
        visible_kf_ids=visible_kf_ids,
        current_user_id=admin.get("user_id") if visible_kf_ids is not None else None,
        channel_chat_id=channel_chat_id,
        page=page,
        page_size=page_size,
    )

    # 客服账号名反查：wecom_kf 按 open_kfid 一次构建查找表（未匹配回退原始 id）
    from src.saas.db.channel_config_db import ChannelConfigDB

    kf_map = {}
    for cfg in ChannelConfigDB.list_by_tenant(tenant_id, "wecom_kf"):
        for kf in cfg.get("config", {}).get("kf_account", []):
            open_kfid = kf.get("open_kfid")
            if open_kfid:
                kf_map.setdefault(open_kfid, kf.get("name") or open_kfid)

    for u in result.get("users", []):
        cid = u.get("channel_chat_id") or ""
        if u.get("channel_type") == "wecom_kf" and cid:
            if visible_kf_ids is not None and cid not in visible_kf_ids:
                # 普通用户：非自己负责的客服账号不反显账号名，避免泄露其它账号的名称/open_kfid
                u["kf_name"] = None
            else:
                u["kf_name"] = kf_map.get(cid, cid)
        else:
            u["kf_name"] = None

    return {"success": True, **result}


@router.get("/kf-accounts")
async def list_kf_accounts(request: Request):
    """客服账号列表（客服账号下拉框数据源）。

    管理员返回租户全部客服账号；普通用户（引流员工）只返回自己负责的客服账号。
    """
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)
    tenant_id = admin.get("tenant_id")

    if not tenant_id:
        raise HTTPException(status_code=400, detail="缺少租户信息")

    from src.saas.db.channel_config_db import ChannelConfigDB

    visible_kf_ids = _resolve_visible_kf_ids(admin, tenant_id)
    kf_accounts = []
    for cfg in ChannelConfigDB.list_by_tenant(tenant_id, "wecom_kf"):
        for kf in cfg.get("config", {}).get("kf_account", []):
            open_kfid = kf.get("open_kfid")
            if not open_kfid:
                continue
            if visible_kf_ids is not None and open_kfid not in visible_kf_ids:
                continue
            kf_accounts.append({"open_kfid": open_kfid, "name": kf.get("name") or open_kfid})

    return {"success": True, "kf_accounts": kf_accounts}


@router.get("/referral-stats")
async def get_referral_stats(
    request: Request,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
):
    """引流统计：总引流数 / 总对话消息数 / 员工维度分组（供「引流统计」Tab 使用）

    日期段过滤基准：
    - 引流数 / 员工分组 → customer_referrals.created_at
    - 总对话消息数 → channel_messages.created_at（is_recalled=FALSE）

    Args:
        start_date: 起始日期（含当日），格式 YYYY-MM-DD
        end_date: 结束日期（含当日，后端按 < 次日 语义处理，SQL 内 +1 天），格式 YYYY-MM-DD
    """
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)
    tenant_id = admin.get("tenant_id")

    if not tenant_id:
        raise HTTPException(status_code=400, detail="缺少租户信息")

    from src.db.models import CustomerReferralDB

    # 普通用户（引流员工）只能看到自己引流的统计
    referrer_user_id = admin.get("user_id") if admin.get("role") == "user" else None

    stats = CustomerReferralDB.referral_stats(
        tenant_id, start_date=start_date, end_date=end_date, referrer_user_id=referrer_user_id
    )
    total_messages = CustomerReferralDB.count_referred_messages(
        tenant_id, start_date=start_date, end_date=end_date, referrer_user_id=referrer_user_id
    )
    return {
        "success": True,
        "total_referrals": stats["total_referrals"],
        "total_messages": total_messages,
        "referrers": stats["referrers"],
    }


@router.get("/users/{user_id}/sessions")
async def get_user_sessions(
    request: Request,
    user_id: str,
    instance_id: Optional[str] = None,
    channel_type: Optional[str] = None,
    channel_chat_id: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    """获取外部用户的会话列表

    Args:
        user_id: 用户ID
        instance_id: 数字员工实例ID筛选（可选）
        channel_type: 渠道类型过滤（可选，与 channel_chat_id 组合精确定位某客服账号会话）
        channel_chat_id: 渠道会话/客服账号ID过滤（可选，空串匹配 legacy NULL 会话）
        page: 页码
        page_size: 每页数量
    """
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)
    tenant_id = admin.get("tenant_id")

    if not tenant_id:
        raise HTTPException(status_code=400, detail="缺少租户信息")

    # 验证用户属于该租户
    user = UserDB.get_by_id(user_id)
    if not user or user.get("tenant_id") != tenant_id:
        raise HTTPException(status_code=404, detail="用户不存在或不属于该租户")

    # 普通用户（引流员工）只能看自己负责的客服账号下的会话；非自己账号直接返回空
    visible_kf_ids = _resolve_visible_kf_ids(admin, tenant_id)
    if visible_kf_ids is not None and channel_chat_id not in visible_kf_ids:
        return {"success": True, "sessions": [], "total": 0, "page": page, "page_size": page_size}

    # 查询 channel_sessions（渠道会话表）
    from src.channels.session import channel_session_manager

    sessions = channel_session_manager.list_sessions(
        channel_type=channel_type,
        user_id=user_id,
        tenant_id=tenant_id,
        channel_chat_id=channel_chat_id,
        limit=page_size,
    )

    # 按 updated_at 降序排序并分页
    sessions.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
    total = len(sessions)
    start = (page - 1) * page_size
    end = start + page_size
    paginated_sessions = sessions[start:end]

    return {"success": True, "sessions": paginated_sessions, "total": total, "page": page, "page_size": page_size}


@router.get("/sessions/{session_id}/messages")
async def get_session_messages(
    request: Request,
    session_id: str,
    content_search: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
):
    """获取会话的消息列表（支持聊天内容搜索）

    Args:
        session_id: 会话ID
        content_search: 聊天内容搜索（可选）
        page: 页码
        page_size: 每页数量
    """
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)
    tenant_id = admin.get("tenant_id")

    if not tenant_id:
        raise HTTPException(status_code=400, detail="缺少租户信息")

    from src.channels.session import channel_session_manager

    # 验证渠道会话属于该租户
    session = channel_session_manager.get_session_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    if session.get("tenant_id") != tenant_id:
        raise HTTPException(status_code=403, detail="无权访问此会话")

    # 普通用户（引流员工）只能读自己负责的客服账号下的会话消息
    visible_kf_ids = _resolve_visible_kf_ids(admin, tenant_id)
    if visible_kf_ids is not None and session.get("channel_chat_id") not in visible_kf_ids:
        raise HTTPException(status_code=403, detail="无权访问此会话")

    result = channel_session_manager.get_messages_paginated(
        session_id=session_id,
        content_search=content_search,
        page=page,
        page_size=page_size,
    )
    return {"success": True, **result}


@router.get("/attachments/download")
async def download_attachment(
    request: Request,
    session_id: str,
    filename: str,
):
    """下载已保存的附件（用于外部接待页面回显）

    路径：`storage/tenants/{tenant_id}/conversation/{filename}`
    （旧版 `data/attachments/{session_id}/` 已废弃，不再回退读取）

    租户归属校验通过 `channel_sessions.tenant_id` 完成，确保不会跨租户下载文件。

    安全说明：本端点**不**校验管理员 token，原因是 HTML 的 `<img>/<audio>/<video>`
    标签无法附带 `Authorization` Header。访问控制依赖 `session_id` 本身的不透明性
    （含租户前缀 + 渠道随机串），等价于"知道 session_id 即拥有查看权限"，与
    `/api/files/{file_id}/inline` 的设计一致。调用方应仅在已通过 `require_admin`
    校验的页面（如本文件上方 `/sessions/{id}/messages`）渲染该 URL。

    Args:
        session_id: 会话ID
        filename: 附件文件名（不含 session_id 子目录）

    Returns:
        文件二进制内容
    """
    import os as _os
    from fastapi.responses import FileResponse

    if not settings.saas.enabled:
        raise HTTPException(status_code=400, detail="未启用 SaaS 模式无法访问")

    # 防止路径穿越：filename 只能取 basename
    filename = _os.path.basename(filename)

    # 验证渠道会话存在（session_id 本身作为访问令牌）
    from src.channels.session import channel_session_manager

    session = channel_session_manager.get_session_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    session_tenant_id = session.get("tenant_id")
    if not session_tenant_id:
        raise HTTPException(status_code=403, detail="会话未关联租户")

    # 仅读取新版路径 storage/tenants/{tenant_id}/conversation/{filename}
    from src.core.storage import get_tenant_storage_abs_path

    local_path = get_tenant_storage_abs_path(session_tenant_id, "conversation", filename)

    if not _os.path.exists(local_path):
        raise HTTPException(status_code=404, detail="附件文件不存在")

    # 推断 MIME 类型
    mime_type = "application/octet-stream"
    ext = _os.path.splitext(filename)[1].lower()
    _MIME_TYPE_MAP = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".gif": "image/gif", ".bmp": "image/bmp", ".webp": "image/webp",
        ".mp3": "audio/mpeg", ".amr": "audio/amr", ".wav": "audio/wav",
        ".mp4": "video/mp4",
        ".pdf": "application/pdf",
        ".doc": "application/msword",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xls": "application/vnd.ms-excel",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".ppt": "application/vnd.ms-powerpoint",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".txt": "text/plain",
        ".zip": "application/zip",
    }
    mime_type = _MIME_TYPE_MAP.get(ext, "application/octet-stream")

    return FileResponse(
        path=local_path,
        media_type=mime_type,
        filename=filename,
    )

