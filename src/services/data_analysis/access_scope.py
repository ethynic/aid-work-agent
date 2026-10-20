"""数据分析数据表可用智能体名单（agent scope）公共模块

集中处理 documents.metadata 保留字段 allowed_agent_ids 的语义，供管理 API（Phase 1）
与运行时工具/检索过滤（Phase 2）共用，避免各入口自行解析造成口径漂移：

- 名单标准化与校验（写入入口共用；新增 ID 必须属于目标租户可用智能体目录）；
- 已存名单分类读取：未配置（缺失/null/[]）与异常配置（非数组/非法元素）明确区分，
  运行时不得把异常配置当成共享（fail-closed）；
- 单表 Python 判断与参数化 SQL 条件片段（名单可见性 + 有效数据表识别）。

存储形态：metadata 仍为 TEXT 存 JSON 字符串，SQL 侧显式 ``metadata::jsonb``；
不新增 DDL、关联表或索引。名单语义见设计 §18.3：

===============  =========================================
缺失 / null / []  本租户全部智能体共享（兼容所有存量表）
非空字符串数组    仅所列本租户智能体可用
其他类型/非法元素  保存时拒绝；运行时不当共享，提示管理员修复
===============  =========================================
"""

import json
from typing import Any, Dict, List, Optional, Tuple

from src.db.database import get_db_connection
from src.services.data_analysis.constants import DATA_ANALYSIS_METADATA_SOURCE_TYPE

# metadata 中的保留字段名
ALLOWED_AGENT_IDS_FIELD = "allowed_agent_ids"

# 数据表文档标题前缀（移动兜底识别用）
DATA_TABLE_TITLE_PREFIX = "[数据表] "

# 主智能体固定 agent_id（与 /api/subagents 语义一致）
MAIN_AGENT_ID = "main"

# 恢复共享时写回的 JSON 值（[] 与缺失/null 同义，写 [] 便于显式可见）
SHARED_SCOPE_JSON = "[]"

# 已存名单分类：未配置（共享）/ 指定名单 / 异常配置
SCOPE_UNSET = "unset"
SCOPE_RESTRICTED = "restricted"
SCOPE_INVALID = "invalid"


def normalize_agent_scope_ids(value: Any) -> List[str]:
    """标准化请求名单：None/[] → 共享（返回 []）；非空 → 去重保序字符串数组。

    非数组、非字符串元素或空串抛 ValueError（写入入口转为 422）。
    已保存的失效 ID 不经过本函数重校验（读取走 read_stored_agent_scope 原样保留）。
    """
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("allowed_agent_ids 必须是字符串数组")
    ids: List[str] = []
    seen = set()
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("allowed_agent_ids 元素必须是非空字符串")
        if item not in seen:
            seen.add(item)
            ids.append(item)
    return ids


def read_stored_agent_scope(meta: Dict[str, Any]) -> Tuple[str, List[str]]:
    """读取已存名单并分类。

    Returns:
        (status, ids)：
        - (SCOPE_UNSET, [])：字段缺失 / null / 空数组，按本租户共享；
        - (SCOPE_RESTRICTED, ids)：非空字符串数组，仅名单内智能体可用
          （字符串元素原样返回，含已失效 ID，不做目录校验）；
        - (SCOPE_INVALID, [])：非数组或含非字符串/空串元素等异常配置，
          运行时不得当成共享（fail-closed），应提示管理员修复。
    """
    if not isinstance(meta, dict) or ALLOWED_AGENT_IDS_FIELD not in meta:
        return SCOPE_UNSET, []
    raw = meta[ALLOWED_AGENT_IDS_FIELD]
    if raw is None:
        return SCOPE_UNSET, []
    if not isinstance(raw, list):
        return SCOPE_INVALID, []
    if not all(isinstance(item, str) and item for item in raw):
        return SCOPE_INVALID, []
    if not raw:
        return SCOPE_UNSET, []
    return SCOPE_RESTRICTED, list(raw)


def standardize_display_ids(meta: Dict[str, Any]) -> List[str]:
    """管理列表展示用标准化名单：共享 → []；指定 → 名单 ID；异常配置 → []。

    异常配置的原始值仍保留在 metadata 中（可排查），展示层不虚构名单；
    运行时判断必须走 read_stored_agent_scope，不得用本函数结果代替 fail-closed 语义。
    """
    status, ids = read_stored_agent_scope(meta)
    if status != SCOPE_RESTRICTED:
        return []
    # 去重保序（写入侧已去重；兼容手工改库产生的重复项）
    seen = set()
    result: List[str] = []
    for item in ids:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def list_tenant_available_agent_ids(tenant_id: Optional[str]) -> List[str]:
    """目标租户可配置的智能体目录（复用 /api/subagents 的 agent_id 语义）。

    main 恒可用；其余为 subscriptions 有效订阅（status='active'、starts_at<=now、
    未过期）的 DISTINCT subagent_type，并沿用 expand_with_tenant_instances
    附加租户副本实例。服务端校验以此为准，不依赖前端选项。
    """
    if not tenant_id:
        return [MAIN_AGENT_ID]
    from src.saas.permissions.checker import expand_with_tenant_instances

    subscribed: List[str] = []
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT DISTINCT subagent_type
            FROM subscriptions
            WHERE tenant_id = %s
              AND status = 'active'
              AND starts_at <= CURRENT_TIMESTAMP
              AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
            """,
            (tenant_id,),
        )
        # 目录必须无重复：SQL 已 DISTINCT，读取侧再保序去重一次，
        # 不把唯一性寄托在单一数据源行为上（含与 main 重复的历史订阅行）
        seen = set()
        for row in cursor.fetchall():
            agent_id = row["subagent_type"]
            if agent_id not in seen:
                seen.add(agent_id)
                subscribed.append(agent_id)
    catalog = [MAIN_AGENT_ID] + [aid for aid in subscribed if aid != MAIN_AGENT_ID]
    return expand_with_tenant_instances(tenant_id, catalog)


def validate_agent_ids_against_catalog(
    ids: List[str], tenant_id: Optional[str], existing_ids: Optional[List[str]] = None
) -> List[str]:
    """管理员写入名单校验：每个新增 ID 必须属于目标租户可用目录，否则抛 ValueError。

    已保存在 metadata 中的失效 ID 不经过本函数（读取侧原样保留），
    仅约束本次新提交的名单。
    """
    added_ids = set(ids) - set(existing_ids or [])
    if not added_ids:
        return ids
    catalog = set(list_tenant_available_agent_ids(tenant_id))
    invalid = [aid for aid in ids if aid in added_ids and aid not in catalog]
    if invalid:
        raise ValueError(f"以下智能体不在当前租户可用目录中: {', '.join(invalid)}")
    return ids


def is_valid_data_table_row(
    source_type: Optional[str],
    title: Optional[str],
    meta: Any,
    tenant_id: Optional[str],
) -> bool:
    """单行 Python 判断：是否为有效数据表（与 valid_data_table_condition 同语义）。

    命中条件：原 source_type，或本租户移动兜底——title 为 [数据表] 前缀且
    metadata 含非空 table_name、columns 为数组。仅标题前缀相同的普通知识文档
    不纳入。

    source_type 分支的行内租户归属由调用方保证（行须按当前租户取得，
    同 SQL 片段的 tenant_id 条件）；tenant_id=None 时不匹配任何行（fail-closed），
    不得以请求体/LLM 入参填充。

    Args:
        source_type: documents.source_type（移动后可能已被覆盖）。
        title: documents.title。
        meta: metadata 解析后的 dict（或 JSON 字符串，内部兜底解析）。
        tenant_id: 当前解析租户。
    """
    if not tenant_id:
        return False
    if source_type == DATA_ANALYSIS_METADATA_SOURCE_TYPE:
        return True
    if not title or not title.startswith(DATA_TABLE_TITLE_PREFIX):
        return False
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except (json.JSONDecodeError, TypeError):
            meta = {}
    if not isinstance(meta, dict):
        return False
    table_name = meta.get("table_name")
    if not isinstance(table_name, str) or not table_name.strip():
        return False
    return isinstance(meta.get("columns"), list)


def valid_data_table_condition(
    tenant_id: Optional[str], metadata_expr: str = "metadata"
) -> Tuple[str, List[Any]]:
    """返回 (SQL 片段, 参数)：有效数据表识别条件，自包含可整体拼进 WHERE。

    条件：``(source_type='data-analysis-metadata' AND tenant_id=%s)
    OR (tenant_id=%s AND title ILIKE '[数据表] %' AND metadata 含非空 table_name
        且 columns 为 jsonb 数组)``。

    与 is_valid_data_table_row 同语义：移动兜底分支要求结构性 metadata，
    防止把同名前缀的普通文档当数据表；tenant_id=None 时条件恒不匹配
    （fail-closed），不得以请求体/LLM 入参填充租户。列表/计数、原更新/删除、
    agent-scope 定位共用；真实 PostgreSQL 的 SQL 行为验证按计划属于后续阶段，
    本阶段仅保证片段形态与 Python 判断一致。
    """
    sql = (
        "(source_type = %s AND tenant_id = %s"
        f" OR (tenant_id = %s AND title ILIKE %s"
        f" AND jsonb_typeof({metadata_expr}::jsonb->'table_name') = 'string'"
        f" AND length(btrim({metadata_expr}::jsonb->>'table_name')) > 0"
        f" AND jsonb_typeof({metadata_expr}::jsonb->'columns') = 'array'))"
    )
    return sql, [
        DATA_ANALYSIS_METADATA_SOURCE_TYPE,
        tenant_id,
        tenant_id,
        DATA_TABLE_TITLE_PREFIX + "%",
    ]


def agent_scope_visible_sql(
    agent_id: Optional[str],
    tenant_id: Optional[str] = None,
    metadata_expr: str = "metadata",
    alias: str = "",
) -> Tuple[str, List[Any]]:
    """返回 (名单可见性 SQL 条件, 参数)：供 Phase 2 运行时在外层租户/共享范围
    条件之外 AND 使用（避免 OR 分支绕过名单）；Phase 1 管理 API 不按名单过滤。

    语义（fail-closed，设计 §18.4）：
    - 共享表（字段缺失 / JSON null / 空数组）恒可见，跨租户共享保持原行为；
    - 非空数组仅当「归属当前租户且包含当前 agent_id」（jsonb @> 精确包含）可见；
      限定表必须绑定当前租户——跨租户共享范围内两边 agent_id 同名也不放行，
      不能只靠外层租户条件隔离（共享范围分支本身覆盖来源租户的行）；
    - 身份未知（agent_id=None）只可见共享表；
    - 异常配置（非数组非 null 类型）对任何身份均不可见，不当成共享。

    本片段假定只作用于数据表形态的行（调用方已按 source_type/标题前缀收窄或
    经 agent_scope_document_filter_sql 包裹）；metadata::jsonb cast 不得用于
    未收窄的普通知识文档行。
    """
    field = ALLOWED_AGENT_IDS_FIELD
    scope = f"{metadata_expr}::jsonb->'{field}'"
    tenant_col = f"{alias}tenant_id" if alias else "tenant_id"
    shared = (
        f"(NOT ({metadata_expr}::jsonb ? '{field}')"
        f" OR {scope} = 'null'::jsonb"
        f" OR (jsonb_typeof({scope}) = 'array'"
        f" AND jsonb_array_length({scope}) = 0))"
    )
    if not agent_id:
        # 身份未知：仅共享表可见；异常配置不可见（不误当共享）
        return shared, []
    # 指定名单表：数组包含当前 agent_id 且归属当前租户时可见；jsonb_typeof 防御非数组类型
    restricted = f"jsonb_typeof({scope}) = 'array' AND {scope} @> %s::jsonb"
    params: List[Any] = [json.dumps([agent_id], ensure_ascii=False)]
    if tenant_id:
        restricted += f" AND {tenant_col} = %s"
        params.append(tenant_id)
    else:
        # 身份非空但租户未知（可信上下文存在却未携带 tenant_id 的调用）：无法验证
        # 「归属当前租户」前提（§18.4），限定表一律不可见（fail-closed），
        # 不得以来源租户的限定表放行；共享表不受影响，仍走 shared 分支。
        restricted += " AND FALSE"
    return f"({shared} OR ({restricted}))", params


def agent_scope_document_filter_sql(
    agent_id: Optional[str],
    tenant_id: Optional[str],
    alias: str = "d",
) -> Tuple[str, List[Any]]:
    """返回 (检索文档级名单过滤 SQL, 参数)：供向量/FTS/标题检索在租户范围条件
    之外 AND 使用，作用于租户范围内全部文档行（含普通知识文档）。

    CR 约束：``metadata::jsonb`` cast 只允许落在数据表形态的行——先用 source_type
    判等或「[数据表] 」标题前缀收窄，再 AND 名单条件；普通知识文档走 NOT 分支
    直接放行、不接触 cast。否则租户内任意一行损坏 JSON metadata 会让整个检索
    500（名单字段只存在于数据表 metadata，普通文档无需判断）。
    """
    marker = (
        f"({alias}.source_type = %s"
        f" OR ({alias}.tenant_id = %s AND {alias}.title ILIKE %s))"
    )
    marker_params: List[Any] = [
        DATA_ANALYSIS_METADATA_SOURCE_TYPE,
        tenant_id,
        DATA_TABLE_TITLE_PREFIX + "%",
    ]
    metadata_expr = f"{alias}.metadata"
    scope_sql, scope_params = agent_scope_visible_sql(
        agent_id, tenant_id, metadata_expr=metadata_expr, alias=f"{alias}."
    )
    # marker 参数出现两次：正分支（数据表行按名单过滤）与负分支（普通文档无 cast 放行）
    sql = f"(({marker} AND ({scope_sql})) OR NOT ({marker}))"
    return sql, marker_params + scope_params + marker_params
