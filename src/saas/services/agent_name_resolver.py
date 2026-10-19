"""智能体显示名解析。

会话表只存 subagent_id（dir_name / 租户副本 id），展示前需反查中文名。
名称来源优先级（高 -> 低）：

1. 租户实例覆盖 `tenant_agent_instances.display_name`（含直定制改名、租户副本）
2. 子智能体注册表：内置 `subagents/*/SUBAGENT.md` + DB 自定义定义
3. `subagent_definitions.name`（注册表未加载或该定义已不在注册表时的兜底）
4. 原样返回 agent_id

注意：`subagent_definitions` 只存管理后台保存过的定义，内置数字员工
（如 recruiting-operator）没有行，只查该表会退化成显示英文 id。
"""

from typing import Dict, Iterable, Optional

from loguru import logger


def resolve_agent_names(
    tenant_id: Optional[str],
    agent_ids: Iterable[str],
) -> Dict[str, str]:
    """批量解析 agent_id -> 显示名。

    Args:
        tenant_id: 租户 ID；用于读取租户实例覆盖项，为空时跳过该层
        agent_ids: agent_id 集合，None/空串自动忽略，重复项去重

    Returns:
        每个入参 agent_id 都有值的映射（兜底为 agent_id 本身）
    """
    ids = [aid for aid in dict.fromkeys(agent_ids) if aid]
    if not ids:
        return {}

    names: Dict[str, str] = {}

    # 1) 租户实例覆盖（直定制行 + 副本行；archived 行不覆盖）
    if tenant_id:
        try:
            from src.db.tenant_agent_instance_db import TenantAgentInstanceDB

            for row in TenantAgentInstanceDB.list_active_by_tenant(tenant_id):
                display_name = row.get("display_name")
                if display_name:
                    names[row["agent_id"]] = display_name
        except Exception as e:
            logger.opt(exception=True).error(f"查询租户数字员工实例失败 tenant={tenant_id}: {e}")

    # 2) 注册表（内置 SUBAGENT.md + DB 自定义）
    registry = None
    try:
        from src.core.agent import master_agent

        registry = master_agent.subagent_registry
    except Exception as e:
        logger.opt(exception=True).error(f"获取子智能体注册表失败: {e}")

    pending = [aid for aid in ids if aid not in names]
    if registry:
        for aid in pending:
            config = registry.get(aid) if aid in registry else None
            if config is not None and config.name:
                names[aid] = config.name

    # 3) subagent_definitions 兜底（注册表未加载 / 定义已被移出注册表）
    pending = [aid for aid in ids if aid not in names]
    if pending:
        try:
            from src.db.subagent_definition_db import SubagentDefinitionDB

            for aid, name in SubagentDefinitionDB.get_name_map(pending).items():
                if name:
                    names[aid] = name
        except Exception as e:
            logger.opt(exception=True).error(f"查询智能体定义名称失败: {e}")

    # 4) 兜底原样返回
    for aid in ids:
        names.setdefault(aid, aid)

    return names
