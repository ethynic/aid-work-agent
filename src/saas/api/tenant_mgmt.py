"""
SaaS 企业信息管理 API

路由：/api/saas/tenants/*
- 获取企业信息
- 更新企业信息
- 概览统计
- 租户增删改查（仅平台管理员）
"""

import asyncio
import json
import os
import re
import tempfile
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form
from loguru import logger
from pydantic import BaseModel, Field

from src.saas.api.tenant_auth import require_admin
from src.core.cache_utils import invalidate_tenant_cache
from src.saas.db.tenant_db import TenantDB
from src.saas.db.subscription_db import SubscriptionDB
from src.saas.models.enums import BehaviorAction, BehaviorResourceType, TenantStatus, TenantType
from src.saas.services.renewal import enrich_tenants_with_renewal
from src.services.behavior_log import audit_action
from src.saas.models.tenant import TenantCreate, TenantUpdate
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
    admin = require_admin(request)
    tenant = TenantDB.get_by_id(admin["tenant_id"])
    if not tenant:
        raise HTTPException(status_code=404, detail="企业不存在")
    return {"success": True, "tenant": tenant}


@router.patch("/me")
@audit_action(BehaviorAction.UPDATE, BehaviorResourceType.TENANT, name_arg="company_name")
async def update_tenant_info(request: Request, body: TenantUpdateRequest):
    """更新当前企业信息"""
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
    admin = require_admin(request)

    # 只有平台管理员可以查看所有租户
    if admin.get("role") != "platform_admin":
        return {"success": False, "message": "权限不足"}

    result = TenantDB.list_tenants(page=page, page_size=page_size)
    # 补充续费状态计算列：日均使用积分 / 预估可用天数 / 是否待续费
    enrich_tenants_with_renewal(result["tenants"])
    return {"success": True, **result}


# ============== 计划任务设置（租户管理员，设计 §15.2.6） ==============

def _tenant_scheduler_override(tenant_id: str):
    """读取 tenants.settings.scheduler.max_tasks_per_user 覆盖值（正整数或 None）"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT settings FROM tenants WHERE tenant_id = %s", (tenant_id,))
        row = cursor.fetchone()
    settings = row["settings"] if row else None
    if isinstance(settings, str):
        settings = json.loads(settings or "{}")
    override = ((settings or {}) if isinstance(settings, dict) else {}).get("scheduler", {}).get("max_tasks_per_user")
    if isinstance(override, bool) or not isinstance(override, int) or override <= 0:
        return None
    return override


class TenantSchedulerSettingsRequest(BaseModel):
    """计划任务设置更新：只允许每人活跃任务上限（正整数保存覆盖值，null 清除）"""
    max_tasks_per_user: Optional[int] = Field(None, ge=1, le=10000,
                                              description="本租户每人活跃任务上限（正整数）；传 null 清除覆盖恢复全局默认")


def _require_tenant_admin_role(admin: dict):
    """计划任务设置为租户管理员专用（设计 §15.3）。require_admin 只认证不校验
    角色（普通 user 也返回通过），此处显式复核——防止普通用户越权改全租户配额。"""
    if admin.get("role") not in ("tenant_admin", "platform_admin"):
        raise HTTPException(status_code=403, detail="仅租户管理员可访问计划任务设置")


@router.get("/me/scheduler")
async def get_tenant_scheduler(request: Request):
    """计划任务设置（设计 §15.2.6）：全局最短间隔（只读，配置文件管理）+
    本租户每人活跃任务上限（覆盖值可编辑，无覆盖时为配置默认值）"""
    from src.scheduler.schedule import max_tasks_per_user, min_interval_hours

    admin = require_admin(request)
    _require_tenant_admin_role(admin)
    tenant_id = admin.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=403, detail="缺少租户上下文")
    return {"success": True, "data": {
        "min_interval_hours": min_interval_hours(),
        "max_tasks_per_user": _tenant_scheduler_override(tenant_id),
        "effective_max_tasks_per_user": max_tasks_per_user(tenant_id),
    }}


@router.patch("/me/scheduler")
@audit_action(BehaviorAction.UPDATE, BehaviorResourceType.TENANT)
async def update_tenant_scheduler(request: Request, body: TenantSchedulerSettingsRequest):
    """更新本租户每人活跃任务上限（设计 §15.2.6，租户管理员专用）。

    只写 tenants.settings.scheduler.max_tasks_per_user（事务内 JSON 整体更新，
    保留其他租户设置键）：正整数保存覆盖值、null 清除覆盖恢复配置默认。
    降低上限不改动已有任务状态，超额时仅拒绝新建/恢复。"""
    admin = require_admin(request)
    _require_tenant_admin_role(admin)
    tenant_id = admin.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=403, detail="缺少租户上下文")
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT settings FROM tenants WHERE tenant_id = %s FOR UPDATE",
                           (tenant_id,))
            row = cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="企业不存在")
            settings = row["settings"]
            if isinstance(settings, str):
                settings = json.loads(settings or "{}")
            settings = settings if isinstance(settings, dict) else {}
            scheduler_settings = settings.get("scheduler")
            scheduler_settings = scheduler_settings if isinstance(scheduler_settings, dict) else {}
            if body.max_tasks_per_user is None:
                scheduler_settings.pop("max_tasks_per_user", None)
            else:
                scheduler_settings["max_tasks_per_user"] = int(body.max_tasks_per_user)
            if scheduler_settings:
                settings["scheduler"] = scheduler_settings
            else:
                settings.pop("scheduler", None)
            cursor.execute("UPDATE tenants SET settings = %s WHERE tenant_id = %s",
                           (json.dumps(settings, ensure_ascii=False), tenant_id))
            conn.commit()
    except HTTPException:
        raise
    except Exception as e:
        logger.opt(exception=True).error(f"更新租户计划任务设置失败 tenant_id={tenant_id} {e}")
        raise HTTPException(status_code=500, detail="保存失败，请稍后重试")
    logger.info(f"Tenant scheduler settings updated: tenant_id={tenant_id}, "
                f"max_tasks_per_user={'(cleared)' if body.max_tasks_per_user is None else body.max_tasks_per_user}")
    return {"success": True, "data": {
        "max_tasks_per_user": _tenant_scheduler_override(tenant_id),
    }}


# ============== 租户偏好设置（租户管理员） ==============

class TenantPreferencesRequest(BaseModel):
    """租户偏好设置更新：AI 生成角标显示开关"""
    show_ai_badge: bool = Field(..., description="会话图片是否显示“AI生成”角标（默认 true）")


@router.patch("/me/preferences")
@audit_action(BehaviorAction.UPDATE, BehaviorResourceType.TENANT)
async def update_tenant_preferences(request: Request, body: TenantPreferencesRequest):
    """更新本租户偏好设置（租户管理员专用）。

    只写 tenants.settings.show_ai_badge（事务内 JSON 整体更新，保留其他租户设置键）。
    会话图片右下角“AI生成”角标为前端显示标识（生成图片文件本身无水印）；默认显示，
    关闭有合规责任（《人工智能生成合成内容标识办法》要求 AI 生成内容显式标识），由租户自担。"""
    admin = require_admin(request)
    _require_tenant_admin_role(admin)
    tenant_id = admin.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=403, detail="缺少租户上下文")
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT settings FROM tenants WHERE tenant_id = %s FOR UPDATE",
                           (tenant_id,))
            row = cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="企业不存在")
            settings = row["settings"]
            if isinstance(settings, str):
                settings = json.loads(settings or "{}")
            settings = settings if isinstance(settings, dict) else {}
            settings["show_ai_badge"] = bool(body.show_ai_badge)
            cursor.execute("UPDATE tenants SET settings = %s WHERE tenant_id = %s",
                           (json.dumps(settings, ensure_ascii=False), tenant_id))
            conn.commit()
        # get_by_id 走 30 分钟 Redis 缓存，改完立即失效保证 auth/me 即时生效
        invalidate_tenant_cache(tenant_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.opt(exception=True).error(f"更新租户偏好设置失败 tenant_id={tenant_id} {e}")
        raise HTTPException(status_code=500, detail="保存失败，请稍后重试")
    logger.info(f"Tenant preferences updated: tenant_id={tenant_id}, show_ai_badge={body.show_ai_badge}")
    return {"success": True, "data": {"show_ai_badge": bool(body.show_ai_badge)}}


class TenantLlmProviderEditRequest(BaseModel):
    """租户 LLM 提供者定制权限开关（仅平台管理员）"""
    allow_llm_provider_edit: bool = Field(..., description="是否允许租户在定制弹框修改 LLM 提供者与模型（默认 False）")


@router.patch("/{tenant_id}/llm-provider-edit")
@audit_action(BehaviorAction.UPDATE, BehaviorResourceType.TENANT, id_arg="tenant_id")
async def set_tenant_llm_provider_edit(request: Request, tenant_id: str, body: TenantLlmProviderEditRequest):
    """设置租户「允许修改LLM提供者」开关（仅平台管理员）。

    只写 tenants.settings.allow_llm_provider_edit（事务内 JSON 整体更新，保留其他键）。
    默认关闭：关闭时租户定制弹框不显示 LLM 配置区块、实例更新接口拒绝 llm 字段，
    且运行时立即失效租户已配置的 LLM 覆盖（回退父定义/全局默认，行数据保留）。"""
    admin = require_admin(request)
    if admin.get("role") != "platform_admin":
        raise HTTPException(status_code=403, detail="仅平台管理员可设置")
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT settings FROM tenants WHERE tenant_id = %s FOR UPDATE",
                           (tenant_id,))
            row = cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="企业不存在")
            settings = row["settings"]
            if isinstance(settings, str):
                settings = json.loads(settings or "{}")
            settings = settings if isinstance(settings, dict) else {}
            settings["allow_llm_provider_edit"] = bool(body.allow_llm_provider_edit)
            cursor.execute("UPDATE tenants SET settings = %s WHERE tenant_id = %s",
                           (json.dumps(settings, ensure_ascii=False), tenant_id))
            conn.commit()
        # get_by_id 走 30 分钟 Redis 缓存，改完立即失效保证租户前台显隐即时生效
        invalidate_tenant_cache(tenant_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.opt(exception=True).error(f"更新租户 LLM 提供者定制开关失败 tenant_id={tenant_id} {e}")
        raise HTTPException(status_code=500, detail="保存失败，请稍后重试")
    logger.info(f"Tenant LLM provider edit updated: tenant_id={tenant_id}, allow={body.allow_llm_provider_edit}")
    return {"success": True, "data": {"allow_llm_provider_edit": bool(body.allow_llm_provider_edit)}}


# ============== 平台管理员 - 租户 CRUD ==============

@router.post("/")
@audit_action(BehaviorAction.CREATE, BehaviorResourceType.TENANT, name_arg="company_name")
async def create_tenant(request: Request, body: TenantCreate):
    """创建租户（仅平台管理员）"""
    admin = require_admin(request)
    if admin.get("role") != "platform_admin":
        return {"success": False, "error": "权限不足", "debug": "Not platform_admin"}

    try:
        # 处理到期日期
        expire_at = _normalize_expire_date(body.expire_at) if body.expire_at else None

        # 校验租户类型
        if body.tenant_type not in TenantType.all_values():
            return {"success": False, "error": "租户类型不合法", "debug": f"Invalid tenant_type: {body.tenant_type}"}

        tenant = TenantDB.create(
            company_name=body.company_name,
            tenant_code=body.tenant_code,
            contact_name=body.contact_name,
            contact_phone=body.contact_phone,
            initial_admin_name=body.initial_admin_name,
            initial_admin_phone=body.initial_admin_phone,
            plan=body.plan,
            tenant_type=body.tenant_type,
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
@audit_action(BehaviorAction.UPDATE, BehaviorResourceType.TENANT, id_arg="tenant_id", name_arg="company_name")
async def update_tenant(request: Request, tenant_id: str, body: TenantUpdate):
    """更新租户信息（仅平台管理员）"""
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

    # 处理 tenant_type 字段：校验枚举值（real/test），非法值直接拒绝
    if "tenant_type" in updates:
        tenant_type_val = updates["tenant_type"]
        if not (isinstance(tenant_type_val, str) and tenant_type_val in TenantType.all_values()):
            return {"success": False, "error": "租户类型不合法", "debug": f"Invalid tenant_type: {tenant_type_val}"}

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

            # 0. 租户类型实际变更时，失效平台月度汇总缓存，保证统计口径立即生效
            if "tenant_type" in updates and updates["tenant_type"] != (existing.get("tenant_type") or "test"):
                try:
                    from src.core.cache_utils import CacheKeys, delete_cached_pattern
                    deleted_keys = delete_cached_pattern(CacheKeys.PLATFORM_USAGE, "")
                    logger.info(f"租户类型变更，已失效平台汇总缓存 {deleted_keys} 个 key: {tenant_id}")
                except Exception as cache_err:
                    logger.warning(f"失效平台汇总缓存失败（非阻断，最长 1 小时后自然过期）: {cache_err}")

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
@audit_action(BehaviorAction.DELETE, BehaviorResourceType.TENANT, id_arg="tenant_id")
async def delete_tenant(request: Request, tenant_id: str):
    """删除租户（仅平台管理员）

    - 正常租户：软删除（status -> deactivated）
    - 已删除（deactivated）租户：物理删除核心数据（彻底删除）
    """
    admin = require_admin(request)
    if admin.get("role") != "platform_admin":
        return {"success": False, "error": "权限不足", "debug": "Not platform_admin"}

    existing = TenantDB.get_by_id(tenant_id)
    if not existing:
        return {"success": False, "error": "租户不存在", "debug": f"Tenant {tenant_id} not found"}

    # 已删除租户的二次删除 -> 彻底删除（物理删除核心数据，剩余历史数据由夜间孤儿清理兜底）
    if existing.get("status") == TenantStatus.DEACTIVATED.value:
        try:
            from src.saas.services.tenant_purge import purge_tenant_core
            result = await asyncio.to_thread(purge_tenant_core, tenant_id)
            if not result["success"]:
                return {"success": False, "error": "彻底删除失败", "debug": sanitize_error_info(result.get("error", ""))}
            logger.info(f"租户彻底删除成功: {tenant_id} by admin {admin['user_id']}, deleted={result['deleted']}")
            return {"success": True, "message": "彻底删除成功：核心数据已物理删除，历史数据将由夜间清理任务删除"}
        except Exception as e:
            logger.opt(exception=True).error(f"租户彻底删除异常: {tenant_id}: {e}")
            return {"success": False, "error": "彻底删除失败", "debug": sanitize_error_info(str(e))}

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
@audit_action(BehaviorAction.UPDATE, BehaviorResourceType.TENANT)
async def upload_tenant_logo(
    request: Request,
    file: UploadFile = File(...),
    tenant_id: Optional[str] = Form(None),
):
    """上传租户 Logo 图片

    鉴权：require_admin（平台管理员或租户管理员均可）。
    目标租户解析优先级：
    1. 租户管理员登录 -> 使用账号自身 tenant_id
    2. 平台管理员 + X-Tenant-Id header（租户前台页面，中间件/require_admin 已切换）
    3. 平台管理员 + multipart form 显式 tenant_id（管理后台编辑弹框，无 X-Tenant-Id）

    流程：
    1. 校验扩展名和大小
    2. 落临时文件
    3. 调 ImageRegistry.register（source=user_upload, usage=inline, ttl_seconds=-1 永久保留）
    4. 返回 file_id + download_url

    注意：上传只注册资产返回 file_id，不立即改租户表；点保存才把 logo_file_id 写入 tenants 表。
    """
    admin = require_admin(request)
    # 优先 admin 上下文（租户管理员自身 / 平台管理员 + X-Tenant-Id），其次 form 显式传入
    tenant_id = admin.get("tenant_id") or tenant_id
    if not tenant_id:
        return {"success": False, "error": "无法确定目标租户", "debug": "Missing tenant_id in admin context"}

    # 平台管理员通过 form 显式指定租户时，校验租户存在，防止伪造/越权
    if admin.get("role") == "platform_admin" and not admin.get("tenant_id"):
        if not TenantDB.get_by_id(tenant_id):
            return {"success": False, "error": "目标租户不存在", "debug": f"Tenant {tenant_id} not found"}

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
