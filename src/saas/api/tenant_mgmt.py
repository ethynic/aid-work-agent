"""
SaaS 企业信息管理 API

路由：/api/saas/tenants/*
- 获取企业信息
- 更新企业信息
- 概览统计
- 租户增删改查（仅平台管理员）
"""

from fastapi import APIRouter, HTTPException, Request, UploadFile, File
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from loguru import logger
import os
import re
import tempfile

from src.saas.api.tenant_auth import require_admin
from src.saas.db.tenant_db import TenantDB
from src.saas.db.subscription_db import SubscriptionDB
from src.saas.services.renewal import enrich_tenants_with_renewal
from src.saas.models.tenant import TenantCreate, TenantUpdate
from src.config.settings import settings
from src.db.models import UserDB, TokenDB
from src.db.database import get_db_connection

router = APIRouter(prefix="/api/saas/tenants", tags=["SaaS 企业管理"])


# 租户 Logo 上传允许的扩展名（与 travel_quote.py 图片白名单一致，额外允许 svg）
_LOGO_ALLOWED_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".svg"}
# 租户 Logo 文件大小上限（2MB）
_LOGO_MAX_SIZE = 2 * 1024 * 1024


def _normalize_expire_date(date_str: str | None) -> datetime | None:
    """
    将日期字符串标准化为当天 23:59:59 的 datetime

    输入格式: YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS
    输出: datetime 对象（时分秒为 23:59:59）
    """
    if not date_str:
        return None

    try:
        # 如果已经是完整的 datetime 格式
        if " " in date_str or "T" in date_str:
            dt = datetime.fromisoformat(date_str.replace("T", " "))
            # 强制设置为当天 23:59:59
            return dt.replace(hour=23, minute=59, second=59, microsecond=0)
        else:
            # 只有日期部分
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            return dt.replace(hour=23, minute=59, second=59, microsecond=0)
    except ValueError:
        return None


# ============== 请求模型 ==============

class TenantUpdateRequest(BaseModel):
    company_name: Optional[str] = Field(None, max_length=100, description="企业名称")
    contact_name: Optional[str] = Field(None, max_length=50, description="联系人姓名")
    contact_phone: Optional[str] = Field(None, max_length=20, description="联系人电话")
    initial_admin_name: Optional[str] = Field(None, max_length=50, description="初始管理员姓名")
    initial_admin_phone: Optional[str] = Field(None, max_length=11, description="初始管理员手机号")
    logo_file_id: Optional[str] = Field(None, description="租户 Logo 文件 ID，传 null 清空")


def sanitize_error_info(error_msg: str) -> str:
    """过滤敏感信息"""
    import re
    sensitive_patterns = [
        r'password["\s:=]+\S+',
        r'passwd["\s:=]+\S+',
        r'secret["\s:=]+\S+',
        r'token["\s:=]+\S+',
        r'api[_-]?key["\s:=]+\S+',
        r'access[_-]?key["\s:=]+\S+',
        r'private[_-]?key["\s:=]+\S+',
        r'auth[_-]?token["\s:=]+\S+',
    ]
    sanitized = error_msg
    for pattern in sensitive_patterns:
        sanitized = re.sub(pattern, lambda m: m.group(0).split('=')[0] + '=***', sanitized, flags=re.IGNORECASE)
    return sanitized


def is_valid_phone(phone: str) -> bool:
    """验证手机号是否为合法中国手机号（11位，以1开头）"""
    if not phone or len(phone) != 11:
        return False
    return bool(re.match(r'^1[3-9]\d{9}$', phone))


def create_initial_admin(tenant_id: str, admin_name: str, admin_phone: str) -> Optional[dict]:
    """
    创建初始租户管理员账户
    如果该租户内手机号已存在用户，则返回 None
    """
    # 检查租户内用户是否已存在
    existing_user = UserDB.get_by_phone_in_tenant(admin_phone, tenant_id)
    if existing_user:
        logger.info(f"手机号 {admin_phone} 在租户 {tenant_id} 内已存在用户，无法创建初始管理员")
        return None

    # 创建租户管理员（密码留空）
    user = UserDB.create(
        phone=admin_phone,
        username=admin_name or f"管理员{admin_phone[-4:]}",
        role="tenant_admin",
        tenant_id=tenant_id,
    )

    if user:
        logger.info(f"为租户 {tenant_id} 创建初始管理员: {admin_phone}")
        return {
            "user_id": user["user_id"],
            "phone": admin_phone,
            "username": user["username"],
            "role": user["role"],
        }
    return None


# ============== API 端点 ==============

@router.get("/me")
async def get_tenant_info(request: Request):
    """获取当前企业信息"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)
    tenant = TenantDB.get_by_id(admin["tenant_id"])
    if not tenant:
        raise HTTPException(status_code=404, detail="企业不存在")
    return {"success": True, "tenant": tenant}


@router.patch("/me")
async def update_tenant_info(request: Request, body: TenantUpdateRequest):
    """更新当前企业信息"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)
    updates = body.model_dump(exclude_unset=True)

    if not updates:
        return {"success": False, "message": "没有需要更新的字段"}

    success = TenantDB.update(admin["tenant_id"], **updates)
    if success:
        tenant = TenantDB.get_by_id(admin["tenant_id"])
        logger.info(f"Tenant updated: {admin['tenant_id']} fields={list(updates.keys())}")
        return {"success": True, "tenant": tenant}
    return {"success": False, "message": "更新失败"}


@router.get("/list_tenants")
async def list_tenants(request: Request, page: int = 1, page_size: int = 20):
    """获取租户列表（仅平台管理员，分页）"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)

    # 只有平台管理员可以查看所有租户
    if admin.get("role") != "platform_admin":
        return {"success": False, "message": "权限不足"}

    result = TenantDB.list_tenants(page=page, page_size=page_size)
    # 补充续费状态计算列：日均使用积分 / 预估可用天数 / 是否待续费
    enrich_tenants_with_renewal(result["tenants"])
    return {"success": True, **result}


# ============== 平台管理员 - 租户 CRUD ==============

@router.post("/")
async def create_tenant(request: Request, body: TenantCreate):
    """创建租户（仅平台管理员）"""
    if not settings.saas.enabled:
        return {"success": False, "error": "未启用 SaaS 模式无法访问", "debug": "SaaS mode disabled"}

    admin = require_admin(request)
    if admin.get("role") != "platform_admin":
        return {"success": False, "error": "权限不足", "debug": "Not platform_admin"}

    try:
        # 处理到期日期
        expire_at = _normalize_expire_date(body.expire_at) if body.expire_at else None

        tenant = TenantDB.create(
            company_name=body.company_name,
            tenant_code=body.tenant_code,
            contact_name=body.contact_name,
            contact_phone=body.contact_phone,
            initial_admin_name=body.initial_admin_name,
            initial_admin_phone=body.initial_admin_phone,
            plan=body.plan,
            max_instances=body.max_instances or 5,
            max_users=body.max_users or 50,
            expire_at=expire_at,
        )
        if not tenant:
            return {"success": False, "error": "创建租户失败", "debug": "TenantDB.create returned None"}

        # 检查是否需要创建初始管理员
        admin_account = None
        if body.initial_admin_phone and is_valid_phone(body.initial_admin_phone):
            admin_account = create_initial_admin(
                tenant["tenant_id"],
                body.initial_admin_name,
                body.initial_admin_phone,
            )

        logger.info(f"租户创建成功: {tenant['tenant_id']} by admin {admin['user_id']}")

        response = {"success": True, "tenant": tenant}
        if admin_account:
            response["message"] = "租户信息保存成功，且创建初始管理员账户 {}，初始密码为空，用户可以点击'忘记密码'通过短信验证码重置密码。".format(
                admin_account["phone"]
            )
            response["admin_account"] = admin_account
        return response

    except Exception as e:
        logger.opt(exception=True).error(f"创建租户异常: {e}")
        return {"success": False, "error": "创建租户失败", "debug": sanitize_error_info(str(e))}


@router.put("/{tenant_id}")
async def update_tenant(request: Request, tenant_id: str, body: TenantUpdate):
    """更新租户信息（仅平台管理员）"""
    if not settings.saas.enabled:
        return {"success": False, "error": "未启用 SaaS 模式无法访问", "debug": "SaaS mode disabled"}

    admin = require_admin(request)
    if admin.get("role") != "platform_admin":
        return {"success": False, "error": "权限不足", "debug": "Not platform_admin"}

    existing = TenantDB.get_by_id(tenant_id)
    if not existing:
        return {"success": False, "error": "租户不存在", "debug": f"Tenant {tenant_id} not found"}

    updates = body.model_dump(exclude_unset=True)
    if not updates:
        return {"success": False, "error": "没有需要更新的字段", "debug": "No fields to update"}

    # 处理 status 字段：直接使用字符串值，不做数字映射
    # 数据库按 enums.py 规范存储：active/suspended/deactivated
    if "status" in updates:
        status_val = updates["status"]
        # 验证并规范化 status 值
        valid_statuses = {"active", "suspended", "deactivated"}
        if isinstance(status_val, str) and status_val in valid_statuses:
            updates["status"] = status_val

    # 处理到期日期：标准化为当天 23:59:59
    if "expire_at" in updates:
        updates["expire_at"] = _normalize_expire_date(updates["expire_at"])

    try:
        success = TenantDB.update(tenant_id, **updates)
        if success:
            tenant = TenantDB.get_by_id(tenant_id)
            logger.info(f"租户更新成功: {tenant_id} by admin {admin['user_id']}, fields={list(updates.keys())}")

            # 检查是否需要创建初始管理员
            admin_account = None
            if body.initial_admin_phone and is_valid_phone(body.initial_admin_phone):
                admin_account = create_initial_admin(
                    tenant_id,
                    body.initial_admin_name,
                    body.initial_admin_phone,
                )

            # 处理 token 联动操作
            token_messages: list[str] = []

            # 1. 如果租户被禁用（status 变为 suspended/deactivated），删除该租户下所有用户 token
            if "status" in updates:
                new_status = updates["status"]
                old_status = existing.get("status")
                if new_status in {"suspended", "deactivated"} and old_status == "active":
                    deleted = TokenDB.delete_by_tenant(tenant_id)
                    if deleted > 0:
                        token_messages.append(f"删除{deleted}个用户token")

            # 2. 如果租户过期日期被改小，将该租户下用户 token 的 expires_at 相应提前
            if "expire_at" in updates:
                new_expire = updates["expire_at"]
                old_expire = existing.get("expire_at")
                should_adjust = False
                if new_expire is not None:
                    if old_expire is None:
                        should_adjust = True
                    else:
                        # 统一转换为 datetime 比较
                        if isinstance(old_expire, str):
                            old_expire_str = old_expire.replace("T", " ")
                            if " " in old_expire_str:
                                old_expire_dt = datetime.strptime(old_expire_str, "%Y-%m-%d %H:%M:%S")
                            else:
                                old_expire_dt = datetime.strptime(old_expire_str, "%Y-%m-%d")
                        else:
                            old_expire_dt = old_expire
                        if new_expire < old_expire_dt:
                            should_adjust = True
                if should_adjust:
                    updated = TokenDB.update_expires_by_tenant(tenant_id, new_expire)
                    if updated > 0:
                        token_messages.append(f"修改{updated}个用户token的过期时间")

            # 组装响应消息
            parts: list[str] = []
            if admin_account:
                parts.append(
                    "且创建初始管理员账户 {}，初始密码为空，用户可以点击'忘记密码'通过短信验证码重置密码。".format(
                        admin_account["phone"]
                    )
                )
            if token_messages:
                parts.append("，".join(token_messages))

            message = "租户信息保存成功"
            if parts:
                message += "，" + "，".join(parts)

            response = {"success": True, "tenant": tenant, "message": message}
            if admin_account:
                response["admin_account"] = admin_account
            return response
        return {"success": False, "error": "更新失败", "debug": "TenantDB.update returned False"}
    except Exception as e:
        logger.opt(exception=True).error(f"更新租户异常: {e}")
        return {"success": False, "error": "更新失败", "debug": sanitize_error_info(str(e))}


@router.delete("/{tenant_id}")
async def delete_tenant(request: Request, tenant_id: str):
    """删除租户（仅平台管理员）"""
    if not settings.saas.enabled:
        return {"success": False, "error": "未启用 SaaS 模式无法访问", "debug": "SaaS mode disabled"}

    admin = require_admin(request)
    if admin.get("role") != "platform_admin":
        return {"success": False, "error": "权限不足", "debug": "Not platform_admin"}

    existing = TenantDB.get_by_id(tenant_id)
    if not existing:
        return {"success": False, "error": "租户不存在", "debug": f"Tenant {tenant_id} not found"}

    try:
        success = TenantDB.delete(tenant_id)
        if success:
            # 级联删除：删除该租户所有的数字员工授权（租户级和用户级）
            with get_db_connection() as conn:
                SubscriptionDB.delete_all_for_tenant(conn, tenant_id)
            logger.info(f"租户删除成功: {tenant_id} by admin {admin['user_id']}, permissions deleted")
            return {"success": True, "message": "删除成功"}
        return {"success": False, "error": "删除失败", "debug": "TenantDB.delete returned False"}
    except Exception as e:
        logger.opt(exception=True).error(f"删除租户异常: {e}")
        return {"success": False, "error": "删除失败", "debug": sanitize_error_info(str(e))}


@router.post("/logo")
async def upload_tenant_logo(request: Request, file: UploadFile = File(...)):
    """上传租户 Logo 图片

    鉴权：require_admin（平台管理员或租户管理员均可）。
    平台管理员代管时通过 X-Tenant-Id header 指定目标租户，由 TenantContextMiddleware 处理。

    流程：
    1. 校验扩展名和大小
    2. 落临时文件
    3. 调 ImageRegistry.register（source=user_upload, usage=inline, ttl_seconds=-1 永久保留）
    4. 返回 file_id + download_url

    注意：上传只注册资产返回 file_id，不立即改租户表；点保存才把 logo_file_id 写入 tenants 表。
    """
    if not settings.saas.enabled:
        return {"success": False, "error": "未启用 SaaS 模式无法访问", "debug": "SaaS mode disabled"}

    admin = require_admin(request)
    tenant_id = admin.get("tenant_id")
    if not tenant_id:
        return {"success": False, "error": "无法确定目标租户", "debug": "Missing tenant_id in admin context"}

    # 1. 校验文件名扩展
    filename = file.filename or ""
    ext = os.path.splitext(filename)[1].lower()
    if ext not in _LOGO_ALLOWED_EXTS:
        return {
            "success": False,
            "error": f"不支持的图片格式（仅支持 {sorted(_LOGO_ALLOWED_EXTS)}）",
            "debug": f"Invalid ext: {ext}",
        }

    # 2. 读取内容并校验大小
    content = await file.read()
    if len(content) == 0:
        return {"success": False, "error": "文件为空", "debug": "Empty file content"}
    if len(content) > _LOGO_MAX_SIZE:
        return {
            "success": False,
            "error": f"文件过大（上限 {_LOGO_MAX_SIZE // 1024 // 1024}MB）",
            "debug": f"File size {len(content)} exceeds {_LOGO_MAX_SIZE}",
        }

    # 3. 落临时文件（register 会 move 到租户目录）
    suffix = ext
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        # 4. 注册到 ImageRegistry（永久保留，不被 cleanup_temp 清理）
        from src.core.image_asset import get_image_registry
        registry = get_image_registry()
        # register 是 async 协程，但内部仅做磁盘 IO 和 Redis hset（同步），
        # 此处直接 await 即可，无需 to_thread 包裹
        ref = await registry.register(
            source_path=tmp_path,
            tenant_id=tenant_id,
            user_id=admin.get("user_id"),
            display_name=os.path.basename(filename) or f"logo{suffix}",
            source="user_upload",
            usage="inline",
            move=True,
            ttl_seconds=-1,  # 永久保留，不调 expire
        )
        logger.info(
            f"租户 Logo 上传成功: tenant={tenant_id} file_id={ref.file_id} "
            f"size={len(content)} by admin={admin.get('user_id')}"
        )
        return {
            "success": True,
            "file_id": ref.file_id,
            "download_url": ref.download_url,
        }
    except Exception as e:
        logger.opt(exception=True).error(f"租户 Logo 上传异常: {e}")
        return {"success": False, "error": "上传失败", "debug": sanitize_error_info(str(e))}
    finally:
        # register 用 move=True，成功后临时文件已被移走；失败时清理
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass
