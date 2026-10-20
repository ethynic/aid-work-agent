"""access_scope 名单公共模块单测（Phase 1）

覆盖：名单标准化（缺失/null/[]/非法元素/非数组）、去重保序、
目标租户目录校验（main 恒可用、订阅命中、副本实例附加、未订阅拒绝）、
恢复共享语义、有效数据表识别、名单 SQL 条件片段形态（fail-closed）。
"""

from unittest.mock import MagicMock, patch

import pytest

from src.services.data_analysis.access_scope import (
    ALLOWED_AGENT_IDS_FIELD,
    MAIN_AGENT_ID,
    SCOPE_INVALID,
    SCOPE_RESTRICTED,
    SCOPE_UNSET,
    agent_scope_document_filter_sql,
    agent_scope_visible_sql,
    is_valid_data_table_row,
    list_tenant_available_agent_ids,
    normalize_agent_scope_ids,
    read_stored_agent_scope,
    standardize_display_ids,
    valid_data_table_condition,
    validate_agent_ids_against_catalog,
)

pytestmark = pytest.mark.unit


# ============== 名单标准化 ==============


def test_normalize_none_and_empty_mean_shared():
    """缺失（None）与空数组均为共享（返回空名单）"""
    assert normalize_agent_scope_ids(None) == []
    assert normalize_agent_scope_ids([]) == []


def test_normalize_dedup_preserves_order():
    """去重保序"""
    assert normalize_agent_scope_ids(["b", "a", "b", "c", "a"]) == ["b", "a", "c"]


@pytest.mark.parametrize("bad", ["main", 123, {"a": 1}, ("a",)])
def test_normalize_rejects_non_array(bad):
    """非数组类型拒绝"""
    with pytest.raises(ValueError):
        normalize_agent_scope_ids(bad)


@pytest.mark.parametrize("bad_item", [1, None, True, "", "   ", ["x"]])
def test_normalize_rejects_invalid_elements(bad_item):
    """非法数组元素（非字符串/空串/纯空白）拒绝"""
    with pytest.raises(ValueError):
        normalize_agent_scope_ids([bad_item])


# ============== 已存名单分类读取 ==============


def test_read_stored_unset_forms():
    """字段缺失 / null / [] / 非 dict 均为未配置（共享）"""
    assert read_stored_agent_scope({}) == (SCOPE_UNSET, [])
    assert read_stored_agent_scope({ALLOWED_AGENT_IDS_FIELD: None}) == (SCOPE_UNSET, [])
    assert read_stored_agent_scope({ALLOWED_AGENT_IDS_FIELD: []}) == (SCOPE_UNSET, [])
    assert read_stored_agent_scope(None) == (SCOPE_UNSET, [])


def test_read_stored_restricted_keeps_stale_ids():
    """非空名单原样返回（含已失效 ID，不做目录校验）"""
    status, ids = read_stored_agent_scope({ALLOWED_AGENT_IDS_FIELD: ["a", "gone-agent"]})
    assert status == SCOPE_RESTRICTED
    assert ids == ["a", "gone-agent"]


@pytest.mark.parametrize("bad", ["main", 123, ["a", 1], [""], [None]])
def test_read_stored_invalid_not_treated_as_shared(bad):
    """异常配置（非数组/非法元素）分类为 invalid，运行时不得当成共享"""
    assert read_stored_agent_scope({ALLOWED_AGENT_IDS_FIELD: bad}) == (SCOPE_INVALID, [])


def test_standardize_display_ids():
    """展示标准化：共享→[]；指定→去重名单；异常配置→[]（原始值保留在 metadata）"""
    meta = {ALLOWED_AGENT_IDS_FIELD: ["a", "a", "b"]}
    assert standardize_display_ids(meta) == ["a", "b"]
    assert standardize_display_ids({ALLOWED_AGENT_IDS_FIELD: "bad"}) == []
    assert standardize_display_ids({ALLOWED_AGENT_IDS_FIELD: ["a", ""]}) == []
    assert standardize_display_ids({}) == []
    # 不修改入参
    assert meta[ALLOWED_AGENT_IDS_FIELD] == ["a", "a", "b"]


# ============== 目标租户目录校验 ==============


def _catalog_db_mock(subscribed):
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = [{"subagent_type": t} for t in subscribed]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_cm = MagicMock()
    mock_cm.__enter__ = MagicMock(return_value=mock_conn)
    mock_cm.__exit__ = MagicMock(return_value=None)
    return mock_cm, mock_cursor


def test_catalog_contains_main_and_subscribed():
    """main 恒可用 + 订阅 DISTINCT subagent_type"""
    mock_cm, mock_cursor = _catalog_db_mock(["sales-assistant", "main", "sales-assistant"])
    with patch("src.services.data_analysis.access_scope.get_db_connection", return_value=mock_cm), \
            patch("src.saas.permissions.checker.expand_with_tenant_instances",
                  side_effect=lambda tid, ids: ids):
        catalog = list_tenant_available_agent_ids("tenant_t1")

    assert catalog == [MAIN_AGENT_ID, "sales-assistant"]
    sql = mock_cursor.execute.call_args[0][0]
    params = mock_cursor.execute.call_args[0][1]
    # 订阅判定 SQL 与 get_allowed_agent_ids_for_user 租户分支同口径
    assert "DISTINCT subagent_type" in sql
    assert "status = 'active'" in sql
    assert "starts_at <= CURRENT_TIMESTAMP" in sql
    assert "expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP" in sql
    assert params == ("tenant_t1",)


def test_catalog_appends_tenant_copy_instances():
    """沿用 expand_with_tenant_instances 附加租户副本实例"""
    mock_cm, _ = _catalog_db_mock(["sales-assistant"])
    with patch("src.services.data_analysis.access_scope.get_db_connection", return_value=mock_cm), \
            patch("src.saas.permissions.checker.expand_with_tenant_instances",
                  side_effect=lambda tid, ids: ids + ["sales-assistant#copy1"]):
        catalog = list_tenant_available_agent_ids("tenant_t1")
    assert "sales-assistant#copy1" in catalog


def test_validate_accepts_main_subscribed_and_copy():
    """main、已订阅、副本实例均通过校验并原样返回去重名单"""
    mock_cm, _ = _catalog_db_mock(["sales-assistant"])
    with patch("src.services.data_analysis.access_scope.get_db_connection", return_value=mock_cm), \
            patch("src.saas.permissions.checker.expand_with_tenant_instances",
                  side_effect=lambda tid, ids: ids + ["sales-assistant#copy1"]):
        ids = validate_agent_ids_against_catalog(
            ["main", "sales-assistant#copy1", "sales-assistant"], "tenant_t1"
        )
    assert ids == ["main", "sales-assistant#copy1", "sales-assistant"]


def test_validate_rejects_unsubscribed_with_ids_in_error():
    """未订阅/不存在的 ID 拒绝，错误信息列出具体 ID"""
    mock_cm, _ = _catalog_db_mock(["sales-assistant"])
    with patch("src.services.data_analysis.access_scope.get_db_connection", return_value=mock_cm), \
            patch("src.saas.permissions.checker.expand_with_tenant_instances",
                  side_effect=lambda tid, ids: ids):
        with pytest.raises(ValueError) as exc_info:
            validate_agent_ids_against_catalog(["main", "ghost"], "tenant_t1")
    assert "ghost" in str(exc_info.value)


def test_validate_empty_list_short_circuits_without_db():
    """空名单（恢复共享）无需目录校验，不触发查询"""
    with patch("src.services.data_analysis.access_scope.get_db_connection") as db_mock:
        assert validate_agent_ids_against_catalog([], "tenant_t1") == []
    db_mock.assert_not_called()


# ============== 恢复共享语义 ==============


def test_restore_shared_means_empty_normalized_list():
    """显式 null / [] 恢复共享：标准化后均为空名单（写入侧存 []）"""
    assert normalize_agent_scope_ids(None) == []
    assert normalize_agent_scope_ids([]) == []
    # 恢复共享免目录校验
    assert validate_agent_ids_against_catalog([], "tenant_t1") == []


# ============== 有效数据表识别（Python 判断） ==============


def test_valid_row_by_source_type():
    assert is_valid_data_table_row(
        "data-analysis-metadata", "[数据表] 销售", {"table_name": "销售", "columns": []}, "t1"
    )


def test_valid_row_moved_table_requires_structural_metadata():
    """移动兜底：本租户 + 标题前缀 + 结构性 metadata 同时满足"""
    meta = {"table_name": "销售", "columns": [{"name": "a"}]}
    assert is_valid_data_table_row("kb-doc", "[数据表] 销售", meta, "t1")
    # 缺 columns（普通同名文档）
    assert not is_valid_data_table_row("kb-doc", "[数据表] 销售", {"table_name": "销售"}, "t1")
    # columns 非数组
    assert not is_valid_data_table_row("kb-doc", "[数据表] 销售", {"table_name": "销售", "columns": "x"}, "t1")
    # table_name 为空
    assert not is_valid_data_table_row("kb-doc", "[数据表] 销售", {"table_name": "  ", "columns": []}, "t1")
    # 非数据表标题
    assert not is_valid_data_table_row("kb-doc", "销售明细说明", meta, "t1")


def test_valid_row_rejects_none_tenant_and_bad_meta():
    """缺失租户不匹配任何行（fail-closed）；metadata 字符串/非法串边界"""
    meta = {"table_name": "销售", "columns": []}
    assert not is_valid_data_table_row("kb-doc", "[数据表] 销售", meta, None)
    # metadata 为 JSON 字符串时兜底解析
    import json as _json
    assert is_valid_data_table_row("kb-doc", "[数据表] 销售", _json.dumps(meta), "t1")
    # 非法 JSON 串不匹配
    assert not is_valid_data_table_row("kb-doc", "[数据表] 销售", "{bad", "t1")
    # title 缺失/为空不匹配
    assert not is_valid_data_table_row("kb-doc", None, meta, "t1")


# ============== SQL 条件片段形态 ==============


def test_valid_data_table_condition_shape():
    """识别条件含 source_type 与移动兜底分支，租户参数各分支独立填充"""
    sql, params = valid_data_table_condition("tenant_t1")
    assert "source_type = %s AND tenant_id = %s" in sql
    assert "title ILIKE %s" in sql
    assert "jsonb_typeof(metadata::jsonb->'table_name') = 'string'" in sql
    assert "length(btrim(metadata::jsonb->>'table_name')) > 0" in sql
    assert "jsonb_typeof(metadata::jsonb->'columns') = 'array'" in sql
    assert params == ["data-analysis-metadata", "tenant_t1", "tenant_t1", "[数据表] %"]


def test_agent_scope_sql_unknown_identity_shared_only():
    """身份未知：仅共享表可见；异常配置（非数组非 null）不可见"""
    sql, params = agent_scope_visible_sql(None)
    assert params == []
    # 字段缺失
    assert "NOT (metadata::jsonb ? 'allowed_agent_ids')" in sql
    # JSON null / 空数组
    assert "= 'null'::jsonb" in sql
    assert "jsonb_array_length(metadata::jsonb->'allowed_agent_ids') = 0" in sql
    # 不含包含判断（身份未知不放宽）
    assert "@>" not in sql


def test_agent_scope_sql_known_agent_uses_containment():
    """指定身份+已知租户：共享 OR 数组包含该 agent_id（含租户绑定），参数为 JSON 数组"""
    import json as _json

    sql, params = agent_scope_visible_sql("sales-assistant", "tenant_t1")
    assert "@> %s::jsonb" in sql
    assert "jsonb_typeof(metadata::jsonb->'allowed_agent_ids') = 'array' AND" in sql
    assert params == [
        _json.dumps(["sales-assistant"], ensure_ascii=False),
        "tenant_t1",
    ]


def test_agent_scope_sql_known_agent_without_tenant_fail_closed():
    """身份非空但租户未知：无法验证「归属当前租户」前提（§18.4），
    限定表一律不可见（fail-closed），不得以来源租户限定表放行；共享分支不受影响"""
    import json as _json

    sql, params = agent_scope_visible_sql("sales-assistant")
    assert "AND FALSE" in sql
    # 不产生租户绑定参数（无法绑定），仅保留包含参数
    assert params == [_json.dumps(["sales-assistant"], ensure_ascii=False)]
    # 共享分支仍在：缺失/null/[] 判定不受租户未知影响
    assert "NOT (metadata::jsonb ? 'allowed_agent_ids')" in sql
    assert "= 'null'::jsonb" in sql


# ============== Phase 2：运行时名单 SQL 条件（租户绑定 + cast 收窄） ==============


def test_agent_scope_sql_restricted_binds_current_tenant():
    """限定表必须归属当前租户且名单命中：跨租户共享范围内同名 agent_id 不放行（§18.4）"""
    import json as _json

    sql, params = agent_scope_visible_sql("sales-assistant", "tenant_t1")
    # 包含判断带租户绑定，且绑定出现在包含条件之后（占位符与参数成对追加）
    assert "jsonb_typeof(metadata::jsonb->'allowed_agent_ids') = 'array'" in sql
    assert "metadata::jsonb->'allowed_agent_ids' @> %s::jsonb AND tenant_id = %s" in sql
    assert params == [
        _json.dumps(["sales-assistant"], ensure_ascii=False),
        "tenant_t1",
    ]


def test_agent_scope_sql_unknown_identity_has_no_tenant_param():
    """身份未知：仅共享语义，不产生任何参数（租户绑定只属于限定分支）"""
    sql, params = agent_scope_visible_sql(None, "tenant_t1")
    assert params == []
    assert "@>" not in sql


def test_agent_scope_document_filter_casts_only_data_table_rows():
    """CR 约束：metadata::jsonb cast 只落在数据表形态行——普通文档走 NOT marker
    分支放行，租户内任意一行损坏 JSON metadata 不会 500 整个检索"""
    sql, params = agent_scope_document_filter_sql(None, "tenant_t1", alias="d")

    # marker：source_type 判等 + 本租户标题前缀收窄（无 cast）
    assert "(d.source_type = %s OR (d.tenant_id = %s AND d.title ILIKE %s))" in sql
    # 名单 cast 只出现在 marker 命中分支；NOT 分支不含 cast
    assert "((d.source_type = %s OR (d.tenant_id = %s AND d.title ILIKE %s)) AND ((NOT (d.metadata::jsonb ? 'allowed_agent_ids')" in sql
    assert "OR NOT ((d.source_type = %s" in sql
    # marker 参数出现两次（正/负分支），名单片段无参（身份未知）
    assert params == [
        "data-analysis-metadata", "tenant_t1", "[数据表] %",
        "data-analysis-metadata", "tenant_t1", "[数据表] %",
    ]


def test_agent_scope_document_filter_with_agent_binds_tenant():
    """指定身份：文档级过滤的限定分支同时绑定当前租户"""
    import json as _json

    sql, params = agent_scope_document_filter_sql("sales-assistant", "tenant_t1", alias="d")
    assert "d.metadata::jsonb->'allowed_agent_ids' @> %s::jsonb AND d.tenant_id = %s" in sql
    marker = ["data-analysis-metadata", "tenant_t1", "[数据表] %"]
    assert params == marker + [
        _json.dumps(["sales-assistant"], ensure_ascii=False), "tenant_t1",
    ] + marker
