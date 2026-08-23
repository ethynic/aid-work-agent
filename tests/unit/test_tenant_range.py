"""
检索租户范围 SQL 构建函数测试。

验证 build_tenant_range_conditions：本租户 + 已启用共享精确对（§6.1 设计约束）。
"""
from src.knowledge.retriever.tenant_range import build_tenant_range_conditions


def test_range_own_tenant_without_source_type():
    """本租户、未传 source_type：只限制 tenant_id"""
    sql, params = build_tenant_range_conditions("B", None, None)
    assert sql == "(d.tenant_id = %s)"
    assert params == ["B"]


def test_range_own_tenant_with_source_type():
    """本租户、传 source_type：限制 tenant_id + source_type"""
    sql, params = build_tenant_range_conditions("B", "product", None)
    assert sql == "(d.tenant_id = %s AND d.source_type = %s)"
    assert params == ["B", "product"]


def test_range_with_shared_ranges_exact_pairs():
    """共享范围以精确 (from_tenant_id, source_type) 对拼接 OR 条件"""
    sql, params = build_tenant_range_conditions("B", None, [("A1", "industry"), ("A2", "tech")])
    assert sql == (
        "(d.tenant_id = %s) OR "
        "(d.tenant_id = %s AND d.source_type = %s) OR "
        "(d.tenant_id = %s AND d.source_type = %s)"
    )
    assert params == ["B", "A1", "industry", "A2", "tech"]


def test_range_shared_still_limited_when_no_source_type():
    """未传 source_type 时，共享侧仍只限已启用精确对，不允许'来源租户全部分类'（§6.1 硬约束）"""
    sql, params = build_tenant_range_conditions("B", None, [("A1", "industry")])
    assert sql == (
        "(d.tenant_id = %s) OR "
        "(d.tenant_id = %s AND d.source_type = %s)"
    )
    # 共享项必须带 source_type：不允许出现不含 source_type 的 (A1) 条件
    assert "d.tenant_id = %s AND d.source_type = %s" in sql


def test_range_same_source_type_merged():
    """本租户与共享来源存在同名 source_type 时，合并为精确对并行检索"""
    sql, params = build_tenant_range_conditions("B", "industry", [("A1", "industry")])
    assert sql == (
        "(d.tenant_id = %s AND d.source_type = %s) OR "
        "(d.tenant_id = %s AND d.source_type = %s)"
    )
    assert params == ["B", "industry", "A1", "industry"]


def test_range_demo_mode_returns_empty():
    """无 tenant_id（demo/命令行）时返回空 SQL 与空参数，由调用方走 demo 分支"""
    sql, params = build_tenant_range_conditions(None, None, None)
    assert sql == ""
    assert params == []


def test_range_custom_alias():
    """alias 参数应用到所有条件（documents 表回查场景）"""
    sql, params = build_tenant_range_conditions("B", None, [("A1", "industry")], alias="documents")
    assert sql == (
        "(documents.tenant_id = %s) OR "
        "(documents.tenant_id = %s AND documents.source_type = %s)"
    )
    assert params == ["B", "A1", "industry"]
