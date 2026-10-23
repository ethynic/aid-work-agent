"""
租户数字员工实例服务。

职责：
- create_copy：复制基础员工为租户副本实例（继承语义，父定义运行时实时合并）
- update_overrides：更新覆盖项（display_name/description/extra_prompt/greeting_*）
- create_direct_customization：确保租户对基础员工的直用实例行存在
- delete：软删副本实例（直用实例行不可删）
- 副本实例 agent_id 生成：{base}-{租户串}-{4位随机}，纯小写连字符，禁止下划线
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


# 副本实例 agent_id 合法字符：小写字母/数字/连字符（禁止下划线，防 session_id 末段提取破坏）
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
    """租户当前有效订阅的基础员工 agent_id 列表（副本实例订阅继承判定用）"""
    from src.db.database import get_db_connection
    from src.saas.db.subscription_db import SubscriptionDB

    with get_db_connection() as conn:
        return SubscriptionDB.get_allowed_subagent_types(conn, tenant_id)


def generate_instance_id(base_agent_id: str, tenant_id: str) -> str:
    """生成全局唯一副本实例 agent_id，最多尝试 20 次"""
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
    """复制数字员工为本租户副本实例（仅存覆盖项，父定义运行时合并）。

    复制来源可以是：基础员工（内置/平台自定义，需订阅），或本租户已有的
    实例行（直用实例行或副本实例行，方案 A：根父定义保持一层继承 + 克隆覆盖项）。
    """
    if not tenant_id:
        raise TenantAgentInstanceError("缺少租户上下文")
    if not display_name or not display_name.strip():
        raise TenantAgentInstanceError("副本显示名不能为空")
    display_name = display_name.strip()[:100]

    # 来源解析：本租户实例行（含直用实例行）优先，克隆其覆盖项到新副本实例
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

    # 上限（仅副本实例行计数，直用实例行不计入）
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

    # 克隆 per-(tenant, subagent) 资产：模板文件（磁盘+元数据）与知识库栏目。
    # 来源为被点击的 agent_id（可能是基础员工，也可能是本租户已有实例行），
    # 运行时这两者按实例 dir_name 读取，不克隆则副本实例会丢失。best-effort，不阻断复制。
    tpl_count = ks_count = 0
    try:
        from src.services.subagent_assets import copy_template_files, copy_knowledge_sources
        tpl_count = copy_template_files(tenant_id, base_agent_id, agent_id)
        ks_count = copy_knowledge_sources(tenant_id, base_agent_id, agent_id)
    except Exception as e:
        logger.opt(exception=True).error(
            f"复制副本资产克隆异常 tenant={tenant_id}, src={base_agent_id}, dst={agent_id}: {e}"
        )

    logger.info(
        f"后端日志：租户 {tenant_id} 复制数字员工 {base_agent_id} -> {agent_id}（{display_name}，"
        f"根父定义 {actual_base}，克隆覆盖项: {list(inherited.keys()) or '无'}，"
        f"模板 {tpl_count} 个，知识栏目 {ks_count} 项）"
    )
    return row


def create_direct_customization(
    tenant_id: str, base_agent_id: str, base_source: Optional[str] = None
) -> Dict[str, Any]:
    """确保租户对基础员工的直用实例行存在（幂等；agent_id = base_agent_id）"""
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
    """更新覆盖项（白名单过滤）；基础员工无直用实例行时自动补建后再更新"""
    row = TenantAgentInstanceDB.get(tenant_id, agent_id)
    if not row:
        # 基础员工（内置/平台自定义）首次定制：补建直用实例行；
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
    # 名称/简介参与空态摘要的 LLM 生成输入；不失效缓存会继续展示旧摘要（TTL 7 天）
    if {"display_name", "description"} & set(filtered):
        try:
            from src.core.cache_utils import CacheKeys, delete_cached

            delete_cached(CacheKeys.SUBAGENT_GREETING, tenant_id, agent_id)
        except Exception as e:
            logger.opt(exception=True).warning(f"失效空态摘要缓存失败 agent={agent_id}: {e}")
    updated = TenantAgentInstanceDB.get(tenant_id, agent_id)
    return updated or row


def delete_copy(tenant_id: str, agent_id: str) -> bool:
    """软删副本实例；直用实例行与不存在均拒绝"""
    row = TenantAgentInstanceDB.get(tenant_id, agent_id)
    if not row:
        raise TenantAgentInstanceError(f"数字员工实例不存在: {agent_id}")
    if row["agent_id"] == row["base_agent_id"]:
        raise TenantAgentInstanceError("基础员工的租户定制不可删除")
    # 渠道占用保护：副本实例被渠道配置引用时直接拒绝删除，需先解除渠道绑定
    try:
        from src.saas.db.channel_config_db import ChannelConfigDB
        if ChannelConfigDB.is_subagent_used(tenant_id, agent_id):
            raise TenantAgentInstanceError("该副本已被渠道配置使用，请先解除渠道绑定后再删除")
    except TenantAgentInstanceError:
        raise
    except Exception as e:
        logger.opt(exception=True).warning(
            f"渠道占用校验失败（放行删除） tenant={tenant_id}, agent={agent_id}: {e}"
        )
    ok = TenantAgentInstanceDB.soft_delete(tenant_id, agent_id)
    if not ok:
        raise TenantAgentInstanceError("删除失败")
    logger.info(f"后端日志：租户 {tenant_id} 删除数字员工副本 {agent_id}")
    return True


def apply_direct_customization_overrides(
    tenant_id: str, items: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """把直用实例行（agent_id == base_agent_id）的显示名/简介覆盖合并进基础员工列表项。

    直用实例行不产生新列表项，但覆盖项需体现到对应基础条目，否则列表接口
    （我的数字员工、新会话可用列表、用户授权列表）仍显示父定义名称/简介。
    """
    if not tenant_id or not items:
        return items
    try:
        rows = TenantAgentInstanceDB.list_by_tenant(tenant_id)
    except Exception as e:
        logger.opt(exception=True).error(f"查询租户数字员工实例失败: {e}")
        return items

    overrides = {
        r["agent_id"]: r
        for r in rows
        if r.get("status") == "active" and r.get("agent_id") == r.get("base_agent_id")
    }
    if not overrides:
        return items
    for item in items:
        row = overrides.get(item.get("agent_id"))
        if not row:
            continue
        if row.get("display_name"):
            item["name"] = row["display_name"]
            item["display_name"] = row["display_name"]
        if row.get("description"):
            item["description"] = row["description"]
    return items


def build_tenant_instance_items(tenant_id: str, allowed_ids: Any) -> List[Dict[str, Any]]:
    """构建本租户数字员工副本实例列表项（active 副本实例，base 在允许集合内）。

    name/description 用租户覆盖值，business_pages/chat_toolbar/upload_accept
    继承父定义（副本实例不可改工具与页面配置）。供 /api/subagents 列表、新会话页
    允许列表、用户管理授权列表共用，副本实例是运行时实例（不进 registry），
    必须由此显式补入，否则相关页面看不到副本实例。
    """
    if not tenant_id:
        return []
    allowed = set(allowed_ids or [])
    try:
        from src.db.tenant_agent_instance_db import TenantAgentInstanceDB
        rows = TenantAgentInstanceDB.list_by_tenant(tenant_id)
    except Exception as e:
        logger.opt(exception=True).error(f"查询租户数字员工实例失败: {e}")
        return []

    # 延迟导入，避免服务层 import 触发 Agent 构造副作用
    from src.core.agent import master_agent
    registry = master_agent.subagent_registry

    items = []
    for row in rows:
        if row.get("status") != "active":
            continue
        if row.get("agent_id") == row.get("base_agent_id"):
            continue  # 直用实例行不产生新列表项
        # 放行条件：base 在允许集合（连带/租户范围），或副本自身被直接授权（仅勾副本场景）
        if row["base_agent_id"] not in allowed and row["agent_id"] not in allowed:
            continue
        base_cfg = registry.get(row["base_agent_id"]) if registry else None
        item = {
            "agent_id": row["agent_id"],
            "name": row.get("display_name") or (base_cfg.name if base_cfg else row["agent_id"]),
            "description": row.get("description") or (base_cfg.description if base_cfg else ""),
            "type": "tenant_instance",
            "base_agent_id": row["base_agent_id"],
            "created_at": row.get("created_at"),
            "business_pages": (base_cfg.business_pages if base_cfg else None) or [],
        }
        if base_cfg is not None and base_cfg.chat_toolbar:
            item["chat_toolbar"] = base_cfg.chat_toolbar
        if base_cfg is not None and base_cfg.upload_accept:
            item["upload_accept"] = base_cfg.upload_accept
        items.append(item)
    return items


def _get_max_instances() -> int:
    try:
        from src.config.settings import settings
        value = getattr(settings, "agent", None)
        if value is not None:
            return int(getattr(value, "tenant_instance_max", 20))
    except Exception:
        pass
    return 20
