"""检索租户范围 SQL 构建。

本租户 + 已启用共享范围（精确 (from_tenant_id, source_type) 对）统一拼装为
OR 条件，供向量检索 / FTS / 标题回查三处复用。

设计约束：共享范围不允许"某来源租户全部分类"的形式。LLM 未传 source_type 时，
本租户搜全部分类，共享侧仍只搜已启用的各 (F, X) 精确对。否则会绕过数字员工级
启用清单，检索到未授权分类。
"""

from typing import List, Optional, Tuple

from loguru import logger


def load_shared_ranges(
    tenant_id: Optional[str],
    subagent_id: Optional[str],
    source_type: Optional[str] = None,
) -> List[Tuple[str, str]]:
    """读取已启用共享分类：数字员工级启用（subagent_knowledge_sources）∩ 租户级授权
    （tenant_knowledge_shares），返回精确 (from_tenant_id, source_type) 对。

    tenant_id / subagent_id 任一为空（主智能体直接调用 / 无租户模式）时返回空，
    与 knowledge_base_tool 共享范围语义一致：仅子智能体 + 租户模式生效。
    授权撤销后交集为空，共享项自动失效（无快照，撤销立即生效）。
    """
    if not tenant_id or not subagent_id:
        return []
    try:
        from src.db.database import get_db_connection
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                  (SELECT sources FROM subagent_knowledge_sources
                   WHERE tenant_id = %s AND subagent_name = %s) AS sources,
                  COALESCE((SELECT json_agg(from_tenant_id) FROM tenant_knowledge_shares
                   WHERE to_tenant_id = %s), '[]'::json) AS share_owners
            """, (tenant_id, subagent_id, tenant_id))
            row = cursor.fetchone()
    except Exception as e:
        logger.warning(f"后端日志：加载共享检索范围失败: {e}")
        return []

    if not row:
        return []
    sources = row["sources"] or []
    share_owners = set(row["share_owners"] or [])
    ranges = []
    for s in sources:
        if not isinstance(s, dict):
            continue
        owner = s.get("owner_tenant_id")
        st = s.get("source_type") or ""
        if owner and owner in share_owners:
            ranges.append((owner, st))
    if source_type:
        ranges = [r for r in ranges if r[1] == source_type]
    return ranges


def build_active_document_condition(alias: str = "d") -> str:
    """检索侧文档可见性条件（公众号内容入知识库 WP2，设计 §7.3）：
    仅 active 且未过期的文档参与检索，排序/LIMIT 前过滤。
    软删除（status='deleted'）与已过期（expires_at <= now()）文档对检索不可见；
    expires_at 只限制检索，不限制管理端列表查看（列表侧仅用 status 过滤）。
    返回常量 SQL 片段（自带外层括号），无参数。
    """
    return (
        f"({alias}.status = 'active' "
        f"AND ({alias}.expires_at IS NULL OR {alias}.expires_at > now()))"
    )


def build_tenant_range_conditions(
    tenant_id: Optional[str],
    source_type: Optional[str],
    shared_ranges: Optional[List[Tuple[str, str]]],
    alias: str = "d",
) -> Tuple[str, List[str]]:
    """构建检索租户范围 SQL 条件子句与参数。

    Args:
        tenant_id: 本租户 ID；为空（无租户上下文，如 platform_admin 全局视图）时返回空 SQL，调用方走无主文档分支
        source_type: LLM 传入的来源类型（本租户侧按此过滤）
        shared_ranges: 已启用共享分类的精确 (from_tenant_id, source_type) 对
        alias: documents 表别名

    Returns:
        (where_sql, params)。where_sql 形如
        "({alias}.tenant_id = %s AND {alias}.source_type = %s) OR (...)"，
        不含 WHERE 关键字。**是 OR 组合，调用方拼接进更大 WHERE 时必须自行
        加外层括号**（`AND ({range_sql})`），否则后续 AND 条件只约束最后一个
        OR 分支（AND 优先级高于 OR）。
    """
    if not tenant_id:
        return "", []

    conditions: List[str] = []
    params: List[str] = []

    if source_type:
        conditions.append(f"({alias}.tenant_id = %s AND {alias}.source_type = %s)")
        params += [tenant_id, source_type]
    else:
        conditions.append(f"({alias}.tenant_id = %s)")
        params.append(tenant_id)

    for from_tenant_id, st in (shared_ranges or []):
        conditions.append(f"({alias}.tenant_id = %s AND {alias}.source_type = %s)")
        params += [from_tenant_id, st]

    return " OR ".join(conditions), params
