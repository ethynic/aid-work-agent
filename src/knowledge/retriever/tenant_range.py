"""检索租户范围 SQL 构建。

本租户 + 已启用共享范围（精确 (from_tenant_id, source_type) 对）统一拼装为
OR 条件，供向量检索 / FTS / 标题回查三处复用。

设计约束：共享范围不允许"某来源租户全部分类"的形式。LLM 未传 source_type 时，
本租户搜全部分类，共享侧仍只搜已启用的各 (F, X) 精确对。否则会绕过数字员工级
启用清单，检索到未授权分类。

本租户栏目授权（2026-09-20 设计，subagent-kb-category-authorization-design.md）：
subagent_knowledge_sources 中本租户自有栏目项（owner_tenant_id 为空）构成授权
集合，语义为「为空 = 允许全部栏目（默认）；勾选 >= 1 = 仅允许勾选栏目」。
knowledge_base_search / knowledge_file_search 经 resolve_category_scope 收口，
传参未授权时拒绝并返回各授权栏目文档数，帮助 LLM 自纠。
"""

from typing import Any, Dict, List, Optional, Tuple, Union

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


def load_authorized_source_types(
    tenant_id: Optional[str],
    subagent_id: Optional[str],
) -> Optional[List[str]]:
    """读取本租户自有授权栏目集合（subagent_knowledge_sources 中 owner_tenant_id 为空的项）。

    授权语义（2026-09-20 产品确认）：自有授权栏目为空 = 允许读取所有栏目（默认，
    兼容存量配置）；勾选 >= 1 个 = 仅允许勾选栏目。

    Returns:
        None 表示未配置（允许全部栏目，主智能体 / 无租户上下文恒为 None 不受限）；
        非空列表表示仅允许列表内栏目。共享项（owner_tenant_id 非空）不参与本判定，
        仍由 load_shared_ranges 精确对约束。
    """
    if not tenant_id or not subagent_id:
        return None
    try:
        from src.db.database import get_db_connection
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT sources FROM subagent_knowledge_sources WHERE tenant_id = %s AND subagent_name = %s",
                (tenant_id, subagent_id),
            )
            row = cursor.fetchone()
    except Exception as e:
        logger.warning(f"后端日志：加载栏目授权失败: {e}")
        return None
    sources = (row["sources"] if row else None) or []
    owned = []
    for s in sources:
        if isinstance(s, dict) and not s.get("owner_tenant_id"):
            st = (s.get("source_type") or "").strip()
            if st:
                owned.append(st)
    # 空自有项（无行 / 仅共享项 / 勾选被清空）= 未配置 = 允许全部
    return owned or None


def count_active_documents_by_source_types(
    tenant_id: Optional[str],
    source_types: List[str],
) -> Dict[str, int]:
    """统计本租户各栏目下的 active 文档数（拒绝未授权栏目时返回，帮助 LLM 选栏目）。"""
    if not tenant_id or not source_types:
        return {}
    try:
        from src.db.database import get_db_connection
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT source_type, COUNT(*) AS c FROM documents
                   WHERE tenant_id = %s AND source_type = ANY(%s)
                     AND status = 'active'
                     AND (expires_at IS NULL OR expires_at > now())
                   GROUP BY source_type""",
                (tenant_id, list(source_types)),
            )
            return {r["source_type"]: r["c"] for r in cursor.fetchall()}
    except Exception as e:
        logger.warning(f"后端日志：统计栏目文档数失败: {e}")
        return {}


def resolve_category_scope(
    tenant_id: Optional[str],
    subagent_id: Optional[str],
    requested_source_type: Optional[str],
) -> Tuple[Optional[Union[str, List[str]]], Optional[Dict[str, Any]]]:
    """按本租户栏目授权收口检索范围（knowledge_base_search / knowledge_file_search 共用）。

    授权语义见 load_authorized_source_types。共享范围不受自有授权影响，
    仍由 load_shared_ranges 精确对约束（调用方收口后需以「收窄时不过滤共享侧、
    传具体栏目时过滤共享侧」的语义自行取 shared_ranges）。

    Returns:
        (effective_source_type, rejection)
        - effective_source_type: 传给检索层的 source_type。str = 指定栏目；
          list = 授权集合收窄（LLM 未传参时）；None = 未收窄。
          build_tenant_range_conditions 对 list 生成 source_type = ANY(%s)。
        - rejection: 传参未授权时的拒绝响应 dict（含各授权栏目文档数），无拒绝为 None。
    """
    authorized = load_authorized_source_types(tenant_id, subagent_id)
    if authorized is None:
        return requested_source_type, None

    if requested_source_type:
        if requested_source_type in authorized:
            return requested_source_type, None
        counts = count_active_documents_by_source_types(tenant_id, authorized)
        logger.info(
            f"后端日志：知识库检索栏目未授权 tenant_id={tenant_id}, subagent_id={subagent_id}, "
            f"requested={requested_source_type}, authorized={authorized}"
        )
        return None, {
            "note": (
                f"栏目 {requested_source_type} 未授权，本次检索已拒绝。"
                f"当前数字员工仅可检索以下知识库栏目，请从中选择后重试：\n"
                + "\n".join(f"- {st}（{counts.get(st, 0)} 篇文档）" for st in authorized)
            ),
            "authorized_categories": [
                {"source_type": st, "doc_count": counts.get(st, 0)}
                for st in authorized
            ],
        }

    # 未传 source_type：收窄为授权栏目集合
    return list(authorized), None


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
    source_type: Optional[Union[str, List[str]]],
    shared_ranges: Optional[List[Tuple[str, str]]],
    alias: str = "d",
) -> Tuple[str, List[Any]]:
    """构建检索租户范围 SQL 条件子句与参数。

    Args:
        tenant_id: 本租户 ID；为空（无租户上下文，如 platform_admin 全局视图）时返回空 SQL，调用方走无主文档分支
        source_type: LLM 传入的来源类型（本租户侧按此过滤）。str = 单栏目；
            非空 list = 栏目授权收窄集合（resolve_category_scope 未传参收窄场景），
            本租户侧生成 source_type = ANY(%s)
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
    params: List[Any] = []

    if source_type:
        if isinstance(source_type, (list, tuple)):
            conditions.append(f"({alias}.tenant_id = %s AND {alias}.source_type = ANY(%s))")
            params += [tenant_id, list(source_type)]
        else:
            conditions.append(f"({alias}.tenant_id = %s AND {alias}.source_type = %s)")
            params += [tenant_id, source_type]
    else:
        conditions.append(f"({alias}.tenant_id = %s)")
        params.append(tenant_id)

    for from_tenant_id, st in (shared_ranges or []):
        conditions.append(f"({alias}.tenant_id = %s AND {alias}.source_type = %s)")
        params += [from_tenant_id, st]

    return " OR ".join(conditions), params


def attach_owner_metadata(
    metadata: Optional[Dict[str, Any]],
    doc_tenant_id: Optional[str],
    current_tenant_id: Optional[str],
) -> Dict[str, Any]:
    """共享来源标注：文档属于其他租户（共享库）时，给 metadata 附加 owner_tenant_id，
    供 LLM 感知内容来源；本租户结果不加标注，行为与现状一致。"""
    md = dict(metadata or {})
    if doc_tenant_id and doc_tenant_id != current_tenant_id:
        md["owner_tenant_id"] = doc_tenant_id
    return md
