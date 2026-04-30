"""
SaaS 数字员工授权 API

路由：/api/saas/permissions/*
- 平台管理员：设置/获取租户级数字员工授权（通过订阅管理）
- 租户管理员：设置/获取用户级数字员工授权
- 普通用户：获取自身可使用的数字员工列表
"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from typing import List, Dict, Any
from loguru import logger

from src.saas.api.tenant_auth import get_current_admin, require_admin
from src.saas.db.permission_db import UserAgentPermissionDB
from src.saas.db.subscription_db import SubscriptionDB
from src.saas.db.agent_instance_db import AgentInstanceDB
from src.saas.permissions.checker import get_allowed_agent_ids_for_user
from src.db.database import get_db_connection
from src.core.agent import master_agent


router = APIRouter(prefix="/api/saas/permissions", tags=["SaaS 数字员工授权"])


# ============== 请求模型 ==============

class SetTenantAgentPermissionsRequest(BaseModel):
    agent_ids: List[str]
    agent_quotas: Dict[str, int] = Field(default_factory=dict, description="每个数字员工的实例配额，未指定的默认为1")


class SetUserAgentPermissionsRequest(BaseModel):
    agent_ids: List[str]


# ============== 平台管理员接口 ==============

@router.get("/tenant/{tenant_id}/agents")
def get_tenant_agent_permissions(request: Request, tenant_id: str):
    """获取租户当前授权的数字员工列表（仅平台管理员）"""
    admin = require_admin(request)
    if not admin or admin.get("role") != "platform_admin":
        raise HTTPException(status_code=403, detail="无权限")

    with get_db_connection() as conn:
        cursor = conn.cursor()
        # 获取所有有效订阅的 agent_id 和 instance_quota
        cursor.execute("""
            SELECT DISTINCT subagent_type, instance_quota
            FROM subscriptions
            WHERE tenant_id = %s
              AND status = 'active'
              AND starts_at <= CURRENT_TIMESTAMP
              AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
            ORDER BY subagent_type
        """, (tenant_id,))
        rows = cursor.fetchall()
        allowed = [row["subagent_type"] for row in rows]
        count = len(allowed)
        agent_quotas = {row["subagent_type"]: row["instance_quota"] for row in rows}
        return {
            "success": True,
            "data": {
                "agent_ids": allowed,
                "count": count,
                "agent_quotas": agent_quotas
            }
        }


@router.post("/tenant/{tenant_id}/agents")
def set_tenant_agent_permissions(request: Request, tenant_id: str, body: SetTenantAgentPermissionsRequest):
    """设置租户授权的数字员工列表（仅平台管理员）"""
    admin = require_admin(request)
    if not admin or admin.get("role") != "platform_admin":
        raise HTTPException(status_code=403, detail="无权限")

    with get_db_connection() as conn:
        SubscriptionDB.set_tenant_subscriptions(conn, tenant_id, body.agent_ids, body.agent_quotas)

    return {
        "success": True,
        "message": f"已设置租户 {tenant_id} 数字员工授权，共 {len(body.agent_ids)} 个"
    }


@router.get("/available-agents")
def get_all_available_agents(request: Request):
    """获取所有可用数字员工供选择（仅平台管理员）"""
    admin = require_admin(request)
    if not admin or admin.get("role") != "platform_admin":
        raise HTTPException(status_code=403, detail="无权限")

    registry = master_agent.subagent_registry
    if not registry:
        return {
            "success": True,
            "data": []
        }

    # 多 worker 部署时刷新定制 subagent
    if registry._custom_dir:
        registry._load_custom(registry._custom_dir)

    items = registry.get_all_subagents_with_type()
    # 整理为下拉选择需要的格式
    result = [
        {
            "agent_id": item["agent_id"],
            "name": item["name"],
            "description": item.get("description", ""),
            "type": item["type"]
        }
        for item in items
    ]

    # 添加主智能体（CEO智能体）作为可授权选项
    result.append({
        "agent_id": "main",
        "name": "CEO智能体",
        "description": "系统主智能体，具备通用能力和工具",
        "type": "builtin"
    })

    return {
        "success": True,
        "data": sorted(result, key=lambda x: x["name"])
    }


# ============== 租户管理员接口 ==============

@router.get("/user/{user_id}/agents")
def get_user_agent_permissions(request: Request, user_id: str):
    """获取用户当前授权的数字员工列表（仅租户管理员）"""
    admin = require_admin(request)
    if not admin:
        raise HTTPException(status_code=403, detail="无权限")

    with get_db_connection() as conn:
        allowed = UserAgentPermissionDB.get_allowed_agents(conn, user_id)
        count = len(allowed)
        return {
            "success": True,
            "data": {
                "agent_ids": allowed,
                "count": count
            }
        }


@router.post("/user/{user_id}/agents")
def set_user_agent_permissions(request: Request, user_id: str, body: SetUserAgentPermissionsRequest):
    """设置用户授权的数字员工列表（仅租户管理员）"""
    admin = require_admin(request)
    if not admin:
        raise HTTPException(status_code=403, detail="无权限")

    tenant_id = admin.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=400, detail="租户ID不存在")

    # 确保所有授权的agent_id都在租户授权范围内
    with get_db_connection() as conn:
        tenant_allowed = set(SubscriptionDB.get_allowed_subagent_types(conn, tenant_id))
        # 过滤掉超出租户范围的
        valid_agent_ids = [aid for aid in body.agent_ids if aid in tenant_allowed]

        if len(valid_agent_ids) != len(body.agent_ids):
            logger.warning(f"Filtered out agents not in tenant allowance: user={user_id}, tenant={tenant_id}")

        UserAgentPermissionDB.set_permissions(conn, user_id, tenant_id, valid_agent_ids)

    return {
        "success": True,
        "message": f"已设置用户 {user_id} 数字员工授权，共 {len(valid_agent_ids)} 个"
    }


@router.get("/tenant/available-user-agents")
def get_tenant_available_user_agents(request: Request):
    """获取租户授权范围内的所有数字员工供用户授权选择（仅租户管理员）"""
    admin = require_admin(request)
    if not admin:
        raise HTTPException(status_code=403, detail="无权限")

    tenant_id = admin.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=400, detail="租户ID不存在")

    registry = master_agent.subagent_registry
    if not registry:
        return {
            "success": True,
            "data": []
        }

    # 多 worker 部署时刷新定制 subagent
    if registry._custom_dir:
        registry._load_custom(registry._custom_dir)

    with get_db_connection() as conn:
        tenant_allowed = set(SubscriptionDB.get_allowed_subagent_types(conn, tenant_id))

    all_items = registry.get_all_subagents_with_type()
    result = [
        {
            "agent_id": item["agent_id"],
            "name": item["name"],
            "description": item.get("description", ""),
            "type": item["type"]
        }
        for item in all_items
        if item["agent_id"] in tenant_allowed
    ]

    # 如果租户有主智能体授权，添加到列表中
    if "main" in tenant_allowed:
        result.append({
            "agent_id": "main",
            "name": "CEO智能体",
            "description": "系统主智能体，具备通用能力和工具",
            "type": "builtin"
        })

    return {
        "success": True,
        "data": sorted(result, key=lambda x: x["name"])
    }


# ============== 所有用户接口 ==============

@router.get("/my/allowed-agents")
def get_my_allowed_agents(request: Request):
    """获取当前登录用户可使用的数字员工列表"""
    user = get_current_admin(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")

    allowed_ids = get_allowed_agent_ids_for_user(user)

    # 获取详细信息
    registry = master_agent.subagent_registry
    if not registry:
        return {
            "success": True,
            "data": []
        }

    all_items = registry.get_all_subagents_with_type()
    result = []
    for item in all_items:
        if item["agent_id"] in allowed_ids:
            # 直接返回完整项目，确保包含所有字段
            result.append(item)

    # 如果主智能体在允许列表中，添加到结果中
    if "main" in allowed_ids:
        result.append({
            "agent_id": "main",
            "name": "CEO智能体",
            "description": "系统主智能体，具备通用能力和工具",
            "type": "builtin",
            "capabilities": [],
            "business_pages": []
        })

    return {
        "success": True,
        "data": sorted(result, key=lambda x: x["name"]),
        "count": len(result)
    }


@router.post("/tenant/{tenant_id}/sync-instances")
def sync_tenant_instances(request: Request, tenant_id: str):
    """同步租户数字员工实例（根据配额创建/删除实例）"""
    admin = require_admin(request)
    if not admin or admin.get("role") != "platform_admin":
        raise HTTPException(status_code=403, detail="无权限")

    with get_db_connection() as conn:
        cursor = conn.cursor()

        # 获取租户所有有效订阅的数字员工及其配额
        cursor.execute("""
            SELECT subagent_type, instance_quota
            FROM subscriptions
            WHERE tenant_id = %s
              AND status = 'active'
              AND starts_at <= CURRENT_TIMESTAMP
              AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
            ORDER BY subagent_type
        """, (tenant_id,))
        subscriptions = cursor.fetchall()

        if not subscriptions:
            return {"success": True, "message": "租户无有效订阅", "created": 0, "deleted": 0}

        # 获取数字员工名称映射
        registry = master_agent.subagent_registry
        agent_name_map = {}
        if registry:
            all_items = registry.get_all_subagents_with_type()
            for item in all_items:
                agent_name_map[item["agent_id"]] = item["name"]

        # 添加主智能体
        agent_name_map["main"] = "CEO智能体"

        created_count = 0
        deleted_count = 0

        for sub in subscriptions:
            agent_id = sub["subagent_type"]
            quota = sub["instance_quota"]
            if quota < 1:
                quota = 1

            # 获取当前实例
            cursor.execute("""
                SELECT instance_id, status, created_at
                FROM agent_instances
                WHERE tenant_id = %s AND subagent_type = %s
                ORDER BY created_at ASC
            """, (tenant_id, agent_id))
            current_instances = cursor.fetchall()
            current_count = len(current_instances)

            # 获取数字员工显示名称
            display_name = agent_name_map.get(agent_id, agent_id)

            if current_count < quota:
                # 需要创建实例
                need_create = quota - current_count
                for i in range(need_create):
                    instance_num = current_count + i + 1
                    instance_name = f"{display_name} - 实例{instance_num}"
                    # 创建实例
                    instance = AgentInstanceDB.create(
                        tenant_id=tenant_id,
                        subagent_type=agent_id,
                        display_name=display_name,
                        instance_name=instance_name,
                        subscription_id=None,  # 暂不关联具体订阅
                        config={},
                    )
                    if instance:
                        created_count += 1
                        logger.info(f"Created instance {instance['instance_id']} for tenant {tenant_id}, agent {agent_id}")

            elif current_count > quota:
                # 需要删除实例（删除最晚创建的空闲实例）
                need_delete = current_count - quota
                # 按创建时间倒序，优先删除最晚创建的
                for inst in reversed(current_instances):
                    if need_delete <= 0:
                        break
                    # 只删除空闲实例
                    if inst["status"] == "idle":
                        success = AgentInstanceDB.delete(inst["instance_id"])
                        if success:
                            deleted_count += 1
                            need_delete -= 1
                            logger.info(f"Deleted instance {inst['instance_id']} for tenant {tenant_id}, agent {agent_id}")
                    # TODO: 如果有非空闲实例，需要处理策略（如标记为待删除）

        return {
            "success": True,
            "message": f"同步完成，创建 {created_count} 个实例，删除 {deleted_count} 个实例",
            "created": created_count,
            "deleted": deleted_count,
        }
