"""
租户数字员工实例服务。

职责：
- create_copy：复制基础员工为租户副本（继承语义，父定义运行时实时合并）
- update_overrides：更新覆盖项（display_name/description/extra_prompt/greeting_*）
- create_direct_customization：确保租户对基础员工的直定制行存在
- delete：软删副本（直定制行不可删）
- 副本 agent_id 生成：{base}-{租户串}-{4位随机}，纯小写连字符，禁止下划线
  （渠道 session_id 末段按 _ 提取 subagent，见 src/services/recap/runner.py）
"""

import random
import re
import string
from typing import Any, Dict, List, Optional

from loguru import logger

from src.db.tenant_agent_instance_db import TenantAgentInstanceDB, OVERRIDABLE_FIELDS


class TenantAgentInstanceError(Exception):
    """业务校验错误（调用方转 400）"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


# 副本 agent_id 合法字符：小写字母/数字/连字符（禁止下划线，防 session_id 末段提取破坏）
_INSTANCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _tenant_slug(tenant_id: str) -> str:
    """tenant_id -> 租户串：非 [a-z0-9] 统一替换为连字符，压缩连续连字符"""
    slug = re.sub(r"[^a-z0-9]+", "-", (tenant_id or "").lower()).strip("-")
    return slug[:24] or "tenant"


def _random_suffix(length: int = 4) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "".join(random.choice(alphabet) for _ in range(length))


def _resolve_base_config(base_agent_id: str) -> Optional[Dict[str, Any]]:
    """查找父定义：registry（内置，含被 DB 覆盖版本）或 subagent_definitions（平台自定义）。

    Returns:
        {"base_source": "builtin"|"custom", "name": ..., "exists": True} 或 None
    """
    # 延迟导入，避免服务层 import 触发 Agent 构造副作用
    from src.core.agent import master_agent
    from src.db.subagent_definition_db import SubagentDefinitionDB

    registry = master_agent.subagent_registry
    if registry and base_agent_id in registry:
        # registry._configs 以 agent_id 为键，in 判定为精确匹配（不经显示名兜底）
        return {"base_source": "builtin" if registry.is_builtin(base_agent_id) else "custom"}
    row = SubagentDefinitionDB.get_by_agent_id(base_agent_id)
    if row:
        return {"base_source": "custom"}
    return None


def _is_id_taken(candidate: str) -> bool:
    from src.core.agent import master_agent
    from src.db.subagent_definition_db import SubagentDefinitionDB

    if TenantAgentInstanceDB.exists_any(candidate):
        return True
    registry = master_agent.subagent_registry
    if registry and not registry.validate_id_uniqueness(candidate):
        return True
    if SubagentDefinitionDB.get_by_agent_id(candidate):
        return True
    return False


def get_tenant_subscribed_agent_ids(tenant_id: str) -> List[str]:
    """租户当前有效订阅的基础员工 agent_id 列表（副本订阅继承判定用）"""
    from src.db.database import get_db_connection
    from src.saas.db.subscription_db import SubscriptionDB

    with get_db_connection() as conn:
        return SubscriptionDB.get_allowed_subagent_types(conn, tenant_id)


def generate_instance_id(base_agent_id: str, tenant_id: str) -> str:
    """生成全局唯一副本 agent_id，最多尝试 20 次"""
    slug = _tenant_slug(tenant_id)
    for _ in range(20):
        candidate = f"{base_agent_id}-{slug}-{_random_suffix()}"
        if not _is_id_taken(candidate):
            return candidate
    raise TenantAgentInstanceError("副本 ID 生成失败，请重试")


def validate_instance_id(agent_id: str) -> Optional[str]:
    """校验 agent_id 合法性，返回错误信息或 None"""
    if not agent_id or len(agent_id) > 128:
        return "agent_id 为空或超长"
    if "_" in agent_id:
        return "agent_id 不能包含下划线"
    if not _INSTANCE_ID_RE.match(agent_id):
        return "agent_id 仅允许小写字母、数字和连字符"
    return None


def create_copy(
    tenant_id: str,
    base_agent_id: str,
    display_name: str,
    created_by: Optional[str] = None,
) -> Dict[str, Any]:
    """复制数字员工为本租户副本（仅存覆盖项，父定义运行时合并）。

    复制来源可以是：基础员工（内置/平台自定义，需订阅），或本租户已有的
    实例行（直定制行或副本行，方案 A：根父定义保持一层继承 + 克隆覆盖项）。
    """
    if not tenant_id:
        raise TenantAgentInstanceError("缺少租户上下文")
    if not display_name or not display_name.strip():
        raise TenantAgentInstanceError("副本显示名不能为空")
    display_name = display_name.strip()[:100]

    # 来源解析：本租户实例行（含直定制行）优先，克隆其覆盖项到新副本
    source_row = TenantAgentInstanceDB.get(tenant_id, base_agent_id)
    inherited: Dict[str, Any] = {}
    if source_row and source_row.get("status") == "active":
        actual_base = source_row["base_agent_id"]
        for field in ("description", "extra_prompt", "greeting_summary", "greeting_prompts"):
            if source_row.get(field) is not None:
                inherited[field] = source_row[field]
    else:
        actual_base = base_agent_id

    base = _resolve_base_config(actual_base)
    if not base:
        raise TenantAgentInstanceError(f"数字员工不存在: {base_agent_id}")

    # 订阅继承：根父定义必须在租户订阅内
    subscribed = get_tenant_subscribed_agent_ids(tenant_id)
    if actual_base not in subscribed:
        raise TenantAgentInstanceError(f"当前租户未订阅数字员工: {actual_base}")

    # 上限（仅副本行计数，直定制行不计入）
    max_instances = _get_max_instances()
    count = TenantAgentInstanceDB.count_active(tenant_id)
    if count >= max_instances:
        raise TenantAgentInstanceError(f"副本数量已达上限（{max_instances}）")

    agent_id = generate_instance_id(actual_base, tenant_id)
    err = validate_instance_id(agent_id)
    if err:
        raise TenantAgentInstanceError(err)

    row = TenantAgentInstanceDB.create(
        tenant_id=tenant_id,
        agent_id=agent_id,
        base_agent_id=actual_base,
        base_source=base["base_source"],
        display_name=display_name,
        description=inherited.get("description"),
        extra_prompt=inherited.get("extra_prompt"),
        greeting_summary=inherited.get("greeting_summary"),
        greeting_prompts=inherited.get("greeting_prompts"),
        created_by=created_by,
    )
    logger.info(
        f"后端日志：租户 {tenant_id} 复制数字员工 {base_agent_id} -> {agent_id}（{display_name}，"
        f"根父定义 {actual_base}，克隆覆盖项: {list(inherited.keys()) or '无'}）"
    )
    return row


def create_direct_customization(
    tenant_id: str, base_agent_id: str, base_source: Optional[str] = None
) -> Dict[str, Any]:
    """确保租户对基础员工的直定制行存在（幂等；agent_id = base_agent_id）"""
    existing = TenantAgentInstanceDB.get(tenant_id, base_agent_id)
    if existing:
        return existing
    return TenantAgentInstanceDB.create(
        tenant_id=tenant_id,
        agent_id=base_agent_id,
        base_agent_id=base_agent_id,
        base_source=base_source,
    )


def update_overrides(tenant_id: str, agent_id: str, overrides: Dict[str, Any]) -> Dict[str, Any]:
    """更新覆盖项（白名单过滤）；基础员工无直定制行时自动补建后再更新"""
    row = TenantAgentInstanceDB.get(tenant_id, agent_id)
    if not row:
        # 基础员工（内置/平台自定义）首次定制：补建直定制行；
        # 仅当 agent_id 确为存在的父定义时才允许，防伪造 agent_id 写入
        if _resolve_base_config(agent_id):
            create_direct_customization(tenant_id, agent_id, base_source=_resolve_base_config(agent_id)["base_source"])
            row = TenantAgentInstanceDB.get(tenant_id, agent_id)
        if not row:
            raise TenantAgentInstanceError(f"数字员工实例不存在: {agent_id}")
    if row["status"] != "active":
        raise TenantAgentInstanceError("实例已归档，不可修改")

    filtered = {k: v for k, v in overrides.items() if k in OVERRIDABLE_FIELDS}
    if not filtered:
        raise TenantAgentInstanceError("无可更新的字段")

    if "display_name" in filtered and filtered["display_name"] is not None:
        filtered["display_name"] = str(filtered["display_name"]).strip()[:100] or None

    ok = TenantAgentInstanceDB.update_overrides(tenant_id, agent_id, filtered)
    if not ok:
        raise TenantAgentInstanceError("更新失败")
    updated = TenantAgentInstanceDB.get(tenant_id, agent_id)
    return updated or row


def delete_copy(tenant_id: str, agent_id: str) -> bool:
    """软删副本；直定制行与不存在均拒绝"""
    row = TenantAgentInstanceDB.get(tenant_id, agent_id)
    if not row:
        raise TenantAgentInstanceError(f"数字员工实例不存在: {agent_id}")
    if row["agent_id"] == row["base_agent_id"]:
        raise TenantAgentInstanceError("基础员工的租户定制不可删除")
    ok = TenantAgentInstanceDB.soft_delete(tenant_id, agent_id)
    if not ok:
        raise TenantAgentInstanceError("删除失败")
    logger.info(f"后端日志：租户 {tenant_id} 删除数字员工副本 {agent_id}")
    return True


def _get_max_instances() -> int:
    try:
        from src.config.settings import settings
        value = getattr(settings, "agent", None)
        if value is not None:
            return int(getattr(value, "tenant_instance_max", 20))
    except Exception:
        pass
    return 20
