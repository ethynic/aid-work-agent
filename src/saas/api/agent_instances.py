"""
SaaS 智能体实例管理 API

路由：/api/saas/instances/*
- 实例 CRUD
- 启动/停止实例
"""

from typing import Optional, List

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from loguru import logger

from src.saas.api.tenant_auth import require_admin
from src.saas.db.agent_instance_db import AgentInstanceDB
from src.saas.db.tenant_db import TenantDB
from src.saas.services.billing import create_subscription_for_tenant
from src.saas.services.instance_manager import instance_manager
from src.config.settings import settings

router = APIRouter(prefix="/api/saas/instances", tags=["SaaS 智能体实例"])


# ============== 请求模型 ==============

class InstanceCreateRequest(BaseModel):
    subagent_type: str = Field(..., description="子智能体类型（对应 subagents 目录名）")
    display_name: str = Field(..., min_length=1, max_length=50, description="实例显示名称")
    plan: str = Field("basic", description="套餐：basic/standard/premium")
    billing_cycle: str = Field("monthly", description="计费周期：monthly/yearly")
    config: Optional[dict] = Field(None, description="自定义配置")
    bound_channel_type: Optional[str] = Field(None, description="绑定渠道：wecom/dingtalk/feishu")
    allowed_skills: Optional[List[str]] = Field(None, description="允许的 Skill 列表")


class InstanceUpdateRequest(BaseModel):
    display_name: Optional[str] = Field(None, max_length=50, description="显示名称")
    config: Optional[dict] = Field(None, description="自定义配置")
    bound_channel_type: Optional[str] = Field(None, description="绑定渠道")
    allowed_skills: Optional[List[str]] = Field(None, description="允许的 Skill 列表")


# ============== API 端点 ==============

@router.get("")
async def list_instances(request: Request):
    """列出当前租户的实例"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)
    instances = AgentInstanceDB.list_by_tenant(admin["tenant_id"])
    return {"success": True, "instances": instances}


@router.post("")
async def create_instance(request: Request, body: InstanceCreateRequest):
    """
    创建智能体实例

    流程：创建订阅 → 创建实例 → 返回信息
    """
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)
    tenant_id = admin["tenant_id"]

    # 1. 检查实例上限
    tenant = TenantDB.get_by_id(tenant_id)
    current_instances = AgentInstanceDB.list_by_tenant(tenant_id)
    if tenant and len(current_instances) >= tenant["max_instances"]:
        raise HTTPException(
            status_code=400,
            detail=f"已达到最大实例数限制（{tenant['max_instances']}）"
        )

    # 2. 创建订阅
    subscription = create_subscription_for_tenant(
        tenant_id=tenant_id,
        plan_name=body.plan,
        billing_cycle=body.billing_cycle,
        subagent_type=body.subagent_type,
    )
    if not subscription:
        raise HTTPException(status_code=500, detail="创建订阅失败")

    # 3. 创建实例
    instance = AgentInstanceDB.create(
        tenant_id=tenant_id,
        subagent_type=body.subagent_type,
        display_name=body.display_name,
        subscription_id=subscription["subscription_id"],
        config=body.config,
        bound_channel_type=body.bound_channel_type,
        allowed_skills=body.allowed_skills,
    )

    if not instance:
        raise HTTPException(status_code=500, detail="创建实例失败")

    logger.info(
        f"Instance created: {instance['instance_id']} "
        f"tenant={tenant_id} type={body.subagent_type}"
    )

    return {
        "success": True,
        "instance": instance,
        "subscription": {
            "subscription_id": subscription["subscription_id"],
            "token_quota": subscription["token_quota"],
            "unit_price": subscription["unit_price"],
        },
    }


@router.get("/{instance_id}")
async def get_instance(instance_id: str, request: Request):
    """获取实例详情"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)
    instance = AgentInstanceDB.get_by_id(instance_id)
    if not instance:
        raise HTTPException(status_code=404, detail="实例不存在")
    if instance["tenant_id"] != admin["tenant_id"]:
        raise HTTPException(status_code=403, detail="无权访问此实例")
    return {"success": True, "instance": instance}


@router.patch("/{instance_id}")
async def update_instance(instance_id: str, request: Request, body: InstanceUpdateRequest):
    """更新实例配置"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)
    instance = AgentInstanceDB.get_by_id(instance_id)
    if not instance:
        raise HTTPException(status_code=404, detail="实例不存在")
    if instance["tenant_id"] != admin["tenant_id"]:
        raise HTTPException(status_code=403, detail="无权操作此实例")

    updates = body.model_dump(exclude_unset=True)
    if not updates:
        return {"success": False, "message": "没有需要更新的字段"}

    success = AgentInstanceDB.update(instance_id, **updates)
    if success:
        updated = AgentInstanceDB.get_by_id(instance_id)
        return {"success": True, "instance": updated}
    return {"success": False, "message": "更新失败"}


@router.delete("/{instance_id}")
async def delete_instance(instance_id: str, request: Request):
    """删除实例"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)
    instance = AgentInstanceDB.get_by_id(instance_id)
    if not instance:
        raise HTTPException(status_code=404, detail="实例不存在")
    if instance["tenant_id"] != admin["tenant_id"]:
        raise HTTPException(status_code=403, detail="无权操作此实例")

    # 先停止
    if instance_manager.is_running(instance_id):
        instance_manager.stop_instance(instance_id)

    success = AgentInstanceDB.delete(instance_id)
    return {"success": success}


@router.post("/{instance_id}/start")
async def start_instance(instance_id: str, request: Request):
    """启动实例"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)
    instance = AgentInstanceDB.get_by_id(instance_id)
    if not instance:
        raise HTTPException(status_code=404, detail="实例不存在")
    if instance["tenant_id"] != admin["tenant_id"]:
        raise HTTPException(status_code=403, detail="无权操作此实例")

    # 检查订阅状态
    from src.saas.db.subscription_db import SubscriptionDB
    if instance.get("subscription_id"):
        sub = SubscriptionDB.get_by_id(instance["subscription_id"])
        if sub and sub["status"] != "active":
            raise HTTPException(
                status_code=400,
                detail=f"关联订阅状态为 {sub['status']}，请先完成订阅支付"
            )

    ok = instance_manager.start_instance(instance_id)
    if ok:
        return {"success": True, "message": "实例已启动"}
    raise HTTPException(status_code=500, detail="启动失败")


@router.post("/{instance_id}/stop")
async def stop_instance(instance_id: str, request: Request):
    """停止实例"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)
    instance = AgentInstanceDB.get_by_id(instance_id)
    if not instance:
        raise HTTPException(status_code=404, detail="实例不存在")
    if instance["tenant_id"] != admin["tenant_id"]:
        raise HTTPException(status_code=403, detail="无权操作此实例")

    instance_manager.stop_instance(instance_id)
    return {"success": True, "message": "实例已停止"}
