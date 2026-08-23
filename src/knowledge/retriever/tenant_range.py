"""检索租户范围 SQL 构建。

本租户 + 已启用共享范围（精确 (from_tenant_id, source_type) 对）统一拼装为
OR 条件，供向量检索 / FTS / 标题回查三处复用。

设计约束：共享范围不允许"某来源租户全部分类"的形式。LLM 未传 source_type 时，
本租户搜全部分类，共享侧仍只搜已启用的各 (F, X) 精确对。否则会绕过数字员工级
启用清单，检索到未授权分类。
"""

from typing import List, Optional, Tuple


def build_tenant_range_conditions(
    tenant_id: Optional[str],
    source_type: Optional[str],
    shared_ranges: Optional[List[Tuple[str, str]]],
    alias: str = "d",
) -> Tuple[str, List[str]]:
    """构建检索租户范围 SQL 条件子句与参数。

    Args:
        tenant_id: 本租户 ID；为空（demo/无租户模式）时返回空 SQL，调用方用 demo 分支
        source_type: LLM 传入的来源类型（本租户侧按此过滤）
        shared_ranges: 已启用共享分类的精确 (from_tenant_id, source_type) 对
        alias: documents 表别名

    Returns:
        (where_sql, params)。where_sql 形如
        "({alias}.tenant_id = %s AND {alias}.source_type = %s) OR (...)"，
        不含 WHERE 关键字。
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
