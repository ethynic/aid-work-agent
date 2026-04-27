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
from src.saas.db.permission_db import TenantAgentPermissionDB, UserAgentPermissionDB
from src.db.models import UserDB
from src.config.settings import settings
from src.db.database import get_db_connection

router = APIRouter(prefix="/api/saas/users", tags=["SaaS 企业用户"])


# ============== 请求模型 ==============

class UserCreateRequest(BaseModel):
    phone: str = Field(..., min_length=11, max_length=11, description="手机号")
    username: Optional[str] = Field(None, max_length=50, description="用户名")
    role: str = Field("user", description="角色，platform_admin/tenant_admin/user（租户管理员传 tenant_admin）")
    tenant_id: Optional[str] = Field(None, description="租户ID（平台管理员代租户创建用户时使用）")


class UserUpdateRequest(BaseModel):
    username: Optional[str] = Field(None, max_length=50, description="用户名")


class UserListRequest(BaseModel):
    page: int = Field(1, ge=1, description="页码")
    page_size: int = Field(20, ge=1, le=100, description="每页数量")


# ============== API 端点 ==============

@router.get("")
async def list_users(request: Request, page: int = 1, page_size: int = 20):
    """列出企业用户（分页）"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)
    # 平台管理员带 X-Tenant-Id header 访问租户前台时，进行租户隔离
    if admin.get("tenant_id"):
        result = UserDB.list_users(page=page, page_size=page_size, tenant_id=admin["tenant_id"])
    else:
        # 平台管理员在平台后台查看所有租户的用户
        result = UserDB.list_users(page=page, page_size=page_size)
    return {"success": True, **result}


@router.post("")
async def create_user(request: Request, body: UserCreateRequest):
    """手动创建单个用户"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)

    # 平台管理员可以代替租户管理员创建用户，但需要指定 tenant_id
    if admin.get("role") == "platform_admin":
        if not body.tenant_id:
            return {"success": False, "message": "平台管理员代租户创建用户时必须指定 tenant_id"}
        tenant_id = body.tenant_id
    else:
        tenant_id = admin["tenant_id"]

    # 1. 检查用户上限
    tenant = TenantDB.get_by_id(tenant_id)
    current_result = UserDB.list_by_tenant(tenant_id, page_size=1)  # 只需 total
    current_count = current_result["total"]
    if tenant and current_count >= tenant["max_users"]:
        raise HTTPException(status_code=400, detail=f"已达到最大用户数限制（{tenant['max_users']}）")

    # 2. 检查租户内用户名是否重复
    if body.username:
        existing_user_by_username = UserDB.get_by_username_in_tenant(body.username, tenant_id)
        if existing_user_by_username:
            return {
                "success": False,
                "error": f"用户名 '{body.username}' 在本企业已存在",
                "debug": f"Duplicate username '{body.username}' in tenant {tenant_id}"
            }

    # 3. 检查租户内手机号是否重复
    existing_user_by_phone = UserDB.get_by_phone_in_tenant(body.phone, tenant_id)
    if existing_user_by_phone:
        return {
            "success": False,
            "error": f"手机号 '{body.phone}' 在本企业已存在",
            "debug": f"Duplicate phone '{body.phone}' in tenant {tenant_id}"
        }

    # 4. 创建用户（租户内手机号唯一，不同租户允许相同手机号）
    user = UserDB.create(
        phone=body.phone,
        username=body.username,
        role=body.role,
        tenant_id=tenant_id,
    )

    if not user:
        raise HTTPException(status_code=500, detail="创建用户失败")

    # 如果租户只授权了一个数字员工，自动给新用户添加该授权
    with get_db_connection() as conn:
        tenant_allowed = TenantAgentPermissionDB.get_allowed_agents(conn, tenant_id)
        if len(tenant_allowed) == 1 and body.role == "user":
            # 自动授权唯一的那个数字员工
            agent_id = tenant_allowed[0]
            UserAgentPermissionDB.add_permission(conn, user["user_id"], tenant_id, agent_id)
            logger.info(f"Auto authorized new user {user['user_id']} for agent {agent_id} (tenant {tenant_id} has only one agent)")

    logger.info(f"User created for tenant {tenant_id}: {user['user_id']} ({body.phone})")
    return {"success": True, "user": user}


@router.post("/batch")
async def batch_import_users(request: Request, file: UploadFile = File(...), tenant_id: Optional[str] = None):
    """
    批量导入用户（CSV 上传）

    CSV 格式：phone,username
    """
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)

    # 平台管理员可以代替租户管理员导入用户，但需要指定 tenant_id
    if admin.get("role") == "platform_admin":
        if not tenant_id:
            return {"success": False, "message": "平台管理员代租户导入用户时必须指定 tenant_id"}
    else:
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
    seen_phones = set()  # 用于检测 CSV 内部重复手机号
    seen_usernames = set()  # 用于检测 CSV 内部重复用户名

    for i, row in enumerate(reader, start=2):  # 从第 2 行开始（第 1 行是表头）
        phone = row.get("phone", "").strip()
        username = row.get("username", "").strip() or None

        if not phone or len(phone) != 11:
            errors.append(f"第 {i} 行：手机号格式错误 ({phone})")
            continue

        # 检查 CSV 内部手机号重复
        if phone in seen_phones:
            errors.append(f"第 {i} 行：手机号 {phone} 在 CSV 中重复")
            continue
        seen_phones.add(phone)

        # 检查 CSV 内部用户名重复（仅当用户名非空时检查）
        if username and username in seen_usernames:
            errors.append(f"第 {i} 行：用户名 {username} 在 CSV 中重复")
            continue
        if username:
            seen_usernames.add(username)

        users_to_import.append({
            "phone": phone,
            "username": username,
        })

    if not users_to_import:
        return {"success": False, "message": "没有有效数据", "errors": errors}

    # 检查租户内手机号和用户名是否重复
    existing_phones = set()
    existing_usernames = set()
    current_result = UserDB.list_by_tenant(tenant_id, page_size=10000)
    for u in current_result["users"]:
        if u.get("phone"):
            existing_phones.add(u["phone"])
        if u.get("username"):
            existing_usernames.add(u["username"])

    # 检查即将导入的用户是否与租户内现有用户重复
    for idx, user_data in enumerate(users_to_import):
        row_num = idx + 2  # 实际行号
        phone = user_data["phone"]
        username = user_data["username"]

        if phone in existing_phones:
            errors.append(f"第 {row_num} 行：手机号 {phone} 在本企业已存在")
            users_to_import[idx] = None  # 标记为跳过
            continue

        if username and username in existing_usernames:
            errors.append(f"第 {row_num} 行：用户名 {username} 在本企业已存在")
            users_to_import[idx] = None  # 标记为跳过
            continue

    # 过滤掉重复的用户
    users_to_import = [u for u in users_to_import if u is not None]

    if not users_to_import:
        return {"success": False, "message": "所有用户均已存在或重复", "errors": errors}

    # 检查上限
    tenant = TenantDB.get_by_id(tenant_id)
    current_count = UserDB.list_by_tenant(tenant_id, page_size=1)["total"]
    if tenant and current_count + len(users_to_import) > tenant["max_users"]:
        raise HTTPException(
            status_code=400,
            detail=f"导入后用户数将超过上限（{tenant['max_users']}）"
        )

    # 创建用户
    imported = 0
    for user_data in users_to_import:
        user = UserDB.get_by_phone_in_tenant(user_data["phone"], tenant_id)
        if not user:
            user = UserDB.create(
                phone=user_data["phone"],
                username=user_data.get("username"),
                tenant_id=tenant_id,
            )

        if user:
            imported += 1

    logger.info(f"Batch import for tenant {tenant_id}: {imported} users imported")
    return {
        "success": True,
        "imported": imported,
        "total": len(users_to_import) + len(errors),
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
    # 同时清除该用户的所有数字员工授权
    with get_db_connection() as conn:
        UserAgentPermissionDB.clear_user_permissions(conn, user_id)

    success = UserDB.update(user_id, tenant_id=None)
    return {"success": success}