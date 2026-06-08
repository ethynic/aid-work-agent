"""
聊天实例选择与并发控制 API

⚠️ 智能体实例并发控制功能拟废弃 ⚠️

路由：/api/chat/instances/*
- 用户获取可用的数字员工实例列表（含实时状态）
- 锁定实例
- 查询排队状态
- 释放实例
- 设备接管
"""

from typing import Optional
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from loguru import logger

from src.saas.services.instance_service import InstanceService
from src.config.settings import settings
from src.api import auth


router = APIRouter(prefix="/api/chat/instances", tags=["聊天实例"])


# ============== 请求模型 ==============


class LockInstanceRequest(BaseModel):
    """锁定实例请求"""
    session_id: str = Field(..., description="当前会话ID")


class ReleaseInstanceRequest(BaseModel):
    """释放实例请求"""
    session_id: str = Field(..., description="当前会话ID")


class TakeOverRequest(BaseModel):
    """接管实例请求"""
    new_session_id: str = Field(..., description="新会话ID")


# ============== API 端点 ==============


@router.get("")
async def list_instances(request: Request, subagent_type: Optional[str] = None):
    """
    获取当前用户可用的数字员工实例列表（含实时状态）

    Query 参数:
        subagent_type: 可选，按子智能体类型过滤

    返回:
        instances: 实例列表（含状态：空闲/忙碌/离线、排队人数、是否可接管）
        quota: 租户配额信息
    """
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    # 从 request.state 获取用户信息（由 TenantContextMiddleware 设置）
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="未登录或租户信息无效")

    # 获取当前用户ID
    user_id = getattr(request.state, "user_id", None)

    instances = InstanceService.list_tenant_instances(
        tenant_id=tenant_id,
        subagent_type=subagent_type,
        current_user_id=user_id,
    )

    return {
        "success": True,
        "instances": instances,
    }


@router.post("/{instance_id}/lock")
async def lock_instance(instance_id: str, request: Request, body: LockInstanceRequest):
    """
    尝试锁定实例

    - 如果空闲，直接锁定成功，返回实例信息
    - 如果忙碌，进入排队，返回排队位置和预计等待时间
    - 如果离线，返回错误
    """
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="未登录或租户信息无效")

    # 从认证 token 中获取用户信息（与 chat_stream 保持一致）
    current_user = auth.get_current_user(request)
    if not current_user:
        raise HTTPException(status_code=401, detail="未登录")
    user_id = current_user["user_id"]

    result = InstanceService.try_lock_instance(
        instance_id=instance_id,
        session_id=body.session_id,
        user_id=user_id,
        tenant_id=tenant_id,
        lock_timeout_minutes=30,
    )

    if result.get("success"):
        return result

    if result.get("is_queued"):
        # 排队中，返回 429 状态码
        return result

    raise HTTPException(status_code=400, detail=result.get("error", "锁定失败"))


@router.get("/{instance_id}/queue-status")
async def check_queue_status(instance_id: str, session_id: str, request: Request):
    """
    检查排队状态

    返回状态：
        - not_in_queue: 不在队列中
        - waiting: 排队中，返回位置和预计等待时间
        - ready: 已到号，可以开始对话
        - expired: 排队超时
    """
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="未登录或租户信息无效")

    status = InstanceService.check_queue_status(instance_id, session_id)

    return {
        "success": True,
        "data": status,
    }


@router.post("/{instance_id}/release")
async def release_instance(instance_id: str, request: Request, body: ReleaseInstanceRequest):
    """
    释放实例锁（对话结束时调用）

    释放后会自动唤醒队列头部的等待用户
    """
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="未登录或租户信息无效")

    success = InstanceService.release_instance(instance_id, body.session_id)

    return {"success": success, "released": success}


@router.delete("/{instance_id}/cancel_queue")
async def cancel_queue(instance_id: str, session_id: str, request: Request):
    """
    取消排队
    """
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="未登录或租户信息无效")

    success = InstanceService.cancel_queue(instance_id, session_id)

    return {"success": success, "cancelled": success}


@router.post("/{instance_id}/take-over")
async def take_over_instance(instance_id: str, request: Request, body: TakeOverRequest):
    """
    跨设备接管对话

    当用户在另一台设备上看到"正在您的另一台设备上对话"时，可以点击此接口将实例接管到当前会话
    """
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="未登录或租户信息无效")

    # 从认证 token 中获取用户信息
    current_user = auth.get_current_user(request)
    if not current_user:
        raise HTTPException(status_code=401, detail="未登录")
    user_id = current_user["user_id"]

    result = InstanceService.take_over_instance(
        instance_id=instance_id,
        new_session_id=body.new_session_id,
        user_id=user_id,
    )

    if result.get("success"):
        return result

    raise HTTPException(status_code=400, detail=result.get("error", "接管失败"))
