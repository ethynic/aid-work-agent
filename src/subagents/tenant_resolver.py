#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
租户数字员工实例解析器。

把 tenant_agent_instances 行与父定义合并为运行时 SubagentConfig：
- 副本实例行（agent_id != base_agent_id）：父定义的 system_prompt/工具/技能/模型
  + 租户覆盖项（display_name/description）
- 直用实例行（agent_id == base_agent_id）：父定义 + 租户覆盖项（不改 agent_id）
- archived 行：降级返回父定义（覆盖项剥离），保历史会话可用；新会话在 API 层拒绝

实例不进全局 registry（registry 无租户上下文）；合并配置每次实时读 DB，
与 factory 自定义智能体「方案 A」一致，多 worker 天然一致。
"""

from typing import Optional

from loguru import logger

from src.models.subagent import SubagentConfig


def resolve_tenant_config(registry, tenant_id: Optional[str], agent_id: str) -> Optional[SubagentConfig]:
    """解析租户数字员工实例为合并配置。

    Args:
        registry: SubagentRegistry（用于取父定义；父定义解析不传 tenant_id，
            避免实例 id 指向另一实例时递归）
        tenant_id: 租户 ID；为空时直接返回 None（无租户上下文无实例）
        agent_id: 请求的 agent_id（可能是实例 agent_id）

    Returns:
        合并后的 SubagentConfig（tenant_instance=True）；非实例返回 None
    """
    if not tenant_id or not agent_id:
        return None

    try:
        from src.db.tenant_agent_instance_db import TenantAgentInstanceDB
        row = TenantAgentInstanceDB.get(tenant_id, agent_id)
    except Exception as e:
        logger.opt(exception=True).error(f"查询租户数字员工实例失败 agent={agent_id}: {e}")
        return None

    if not row:
        return None

    base_agent_id = row["base_agent_id"]
    if not base_agent_id:
        logger.warning(f"租户实例行缺少 base_agent_id: {agent_id}")
        return None

    from src.subagents.factory import AgentFactory
    # 父定义解析不传 tenant_id：父是基础员工（非实例），同时防止异常数据递归
    base = AgentFactory.get_runtime_config(registry, base_agent_id)
    if base is None:
        logger.warning(
            f"租户实例父定义不存在: instance={agent_id}, base={base_agent_id}"
        )
        return None

    archived = row.get("status") != "active"
    display_name = None if archived else (row.get("display_name") or base.name)
    description = None if archived else (row.get("description") or base.description)

    merged = base.model_copy(update={
        "name": display_name or base.name,
        "description": description or base.description,
        # 直用实例行 agent_id == base_agent_id，dir_name 保持不变；副本实例行切换为实例 id，
        # 使 extra.md/知识栏目/模板按实例 agent_id 隔离
        "dir_name": row["agent_id"],
        # from_db=True 使 agent_router._should_rebuild 进入实时比对路径（覆盖项变更即时重建）
        "from_db": True,
        "tenant_instance": True,
        "base_agent_id": base_agent_id,
    })
    return merged
