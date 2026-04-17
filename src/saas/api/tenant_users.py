"""
SaaS 企业用户管理 API

路由：/api/saas/users/*
- 用户列表、创建、更新、删除
- 批量导入（CSV）

注意：用户统一存储在 users 表中，通过 tenant_id 字段区分租户
"""

import csv
import io
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Request, UploadFile, File
from pydantic import BaseModel, Field
from loguru import logger

from src.saas.api.tenant_auth import require_admin
from src.saas.db.tenant_db import TenantDB
from src.db.models import UserDB
from src.config.settings import settings

router = APIRouter(prefix="/api/saas/users", tags=["SaaS 企业用户"])


# ============== 请求模型 ==============

class UserCreateRequest(BaseModel):
    phone: str = Field(..., min_length=11, max_length=11, description="手机号")
    username: Optional[str] = Field(None, max_length=50, description="用户名")


class UserUpdateRequest(BaseModel):
    username: Optional[str] = Field(None, max_length=50, description="用户名")


# ============== API 端点 ==============

@router.get("")
async def list_users(request: Request):
    """列出企业用户"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)
    # 平台管理员可查看所有租户的用户
    if admin.get("role") == "platform_admin":
        users = UserDB.list_users()
    else:
        users = UserDB.list_by_tenant(admin["tenant_id"])
    return {"success": True, "users": users}


@router.post("")
async def create_user(request: Request, body: UserCreateRequest):
    """手动创建单个用户"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)
    tenant_id = admin["tenant_id"]

    # 平台管理员不能直接创建用户，需要指定租户
    if admin.get("role") == "platform_admin":
        return {"success": False, "message": "平台管理员请使用租户管理功能"}

    # 1. 检查用户上限
    tenant = TenantDB.get_by_id(tenant_id)
    current_users = UserDB.list_by_tenant(tenant_id)
    if tenant and len(current_users) >= tenant["max_users"]:
        raise HTTPException(status_code=400, detail=f"已达到最大用户数限制（{tenant['max_users']}）")

    # 2. 创建或查找用户
    user = UserDB.get_by_phone(body.phone)
    if not user:
        user = UserDB.create(
            phone=body.phone,
            username=body.username,
            tenant_id=tenant_id,
        )
    else:
        # 用户已存在，更新 tenant_id
        UserDB.update(user["user_id"], tenant_id=tenant_id)

    if not user:
        raise HTTPException(status_code=500, detail="创建用户失败")

    logger.info(f"User created for tenant {tenant_id}: {user['user_id']} ({body.phone})")
    return {"success": True, "user": user}


@router.post("/batch")
async def batch_import_users(request: Request, file: UploadFile = File(...)):
    """
    批量导入用户（CSV 上传）

    CSV 格式：phone,username
    """
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)

    # 平台管理员不能直接导入
    if admin.get("role") == "platform_admin":
        return {"success": False, "message": "平台管理员请使用租户管理功能"}

    tenant_id = admin["tenant_id"]

    # 读取 CSV
    content = await file.read()
    try:
        text = content.decode("utf-8-sig")  # 支持 BOM
    except UnicodeDecodeError:
        text = content.decode("gbk")

    reader = csv.DictReader(io.StringIO(text))
    users_to_import = []
    errors = []

    for i, row in enumerate(reader, start=2):  # 从第 2 行开始（第 1 行是表头）
        phone = row.get("phone", "").strip()
        if not phone or len(phone) != 11:
            errors.append(f"第 {i} 行：手机号格式错误 ({phone})")
            continue

        users_to_import.append({
            "phone": phone,
            "username": row.get("username", "").strip() or None,
        })

    if not users_to_import:
        return {"success": False, "message": "没有有效数据", "errors": errors}

    # 检查上限
    tenant = TenantDB.get_by_id(tenant_id)
    current_count = len(UserDB.list_by_tenant(tenant_id))
    if tenant and current_count + len(users_to_import) > tenant["max_users"]:
        raise HTTPException(
            status_code=400,
            detail=f"导入后用户数将超过上限（{tenant['max_users']}）"
        )

    # 创建用户
    imported = 0
    for user_data in users_to_import:
        user = UserDB.get_by_phone(user_data["phone"])
        if not user:
            user = UserDB.create(
                phone=user_data["phone"],
                username=user_data.get("username"),
                tenant_id=tenant_id,
            )
        else:
            # 用户已存在，更新 tenant_id
            UserDB.update(user["user_id"], tenant_id=tenant_id)

        if user:
            imported += 1

    logger.info(f"Batch import for tenant {tenant_id}: {imported} users imported")
    return {
        "success": True,
        "imported": imported,
        "total": len(users_to_import),
        "errors": errors,
    }


@router.patch("/{user_id}")
async def update_user(user_id: str, request: Request, body: UserUpdateRequest):
    """更新企业用户信息"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)

    # 检查用户是否属于该租户
    user = UserDB.get_by_id(user_id)
    if not user or user.get("tenant_id") != admin["tenant_id"]:
        raise HTTPException(status_code=404, detail="用户不在此企业中")

    updates = body.model_dump(exclude_unset=True)
    if not updates:
        return {"success": False, "message": "没有需要更新的字段"}

    success = UserDB.update(user_id, **updates)
    return {"success": success}


@router.delete("/{user_id}")
async def remove_user(user_id: str, request: Request):
    """移除企业用户（仅从租户中移除，不删除用户）"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)

    # 检查用户是否属于该租户
    user = UserDB.get_by_id(user_id)
    if not user or user.get("tenant_id") != admin["tenant_id"]:
        raise HTTPException(status_code=404, detail="用户不在此企业中")

    # 平台管理员不能被移除
    if user.get("role") == "platform_admin":
        return {"success": False, "message": "无法移除平台管理员"}

    # 将 tenant_id 设为 None，而不是删除用户
    success = UserDB.update(user_id, tenant_id=None)
    return {"success": success}