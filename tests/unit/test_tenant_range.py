"""
检索租户范围 SQL 构建函数测试。

验证 build_tenant_range_conditions：本租户 + 已启用共享精确对（§6.1 设计约束）。
验证 load_shared_ranges：数字员工级启用 ∩ 租户级授权的交集计算。
"""
from unittest.mock import patch

from src.knowledge.retriever.tenant_range import (
    build_tenant_range_conditions,
    load_shared_ranges,
)


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


# ---------------------------------------------------------------
# load_shared_ranges：数字员工级启用 ∩ 租户级授权
# ---------------------------------------------------------------

class _FakeCursor:
    def __init__(self, row):
        self._row = row

    def execute(self, sql, args=None):
        self.sql = sql
        self.args = args

    def fetchone(self):
        return self._row


class _FakeConn:
    def __init__(self, row):
        self._row = row

    def cursor(self):
        return _FakeCursor(self._row)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestLoadSharedRanges:
    """数字员工级启用 ∩ 租户级授权的交集计算（无快照，撤销立即生效）"""

    def test_empty_tenant_or_subagent(self):
        """tenant_id / subagent_id 任一为空（主智能体直接调用）时不启用共享"""
        assert load_shared_ranges(None, None, None) == []
        assert load_shared_ranges("B", None, None) == []
        assert load_shared_ranges(None, "travel", None) == []

    def test_intersection_filters_unapproved_owner(self):
        """只保留 owner_tenant_id 在租户级授权（share_owners）内的 source"""
        row = {
            "sources": [
                {"owner_tenant_id": "A1", "source_type": "hotel_resource"},
                {"owner_tenant_id": "A2", "source_type": "attraction_resource"},
                {"owner_tenant_id": "A3", "source_type": "file"},      # 租户级未授权
                {"owner_tenant_id": None, "source_type": "local"},     # 本租户资源，无 owner
            ],
            "share_owners": ["A1", "A2"],
        }
        with patch("src.db.database.get_db_connection", return_value=_FakeConn(row)):
            ranges = load_shared_ranges("B", "travel-consultant", None)
        assert ranges == [("A1", "hotel_resource"), ("A2", "attraction_resource")]

    def test_source_type_filter(self):
        """传 source_type 时只返回该分类的共享项"""
        row = {
            "sources": [
                {"owner_tenant_id": "A1", "source_type": "hotel_resource"},
                {"owner_tenant_id": "A1", "source_type": "attraction_resource"},
            ],
            "share_owners": ["A1"],
        }
        with patch("src.db.database.get_db_connection", return_value=_FakeConn(row)):
            ranges = load_shared_ranges("B", "travel-consultant", "hotel_resource")
        assert ranges == [("A1", "hotel_resource")]

    def test_db_error_returns_empty(self):
        """数据库异常时降级为空共享范围，不阻断检索"""
        with patch("src.db.database.get_db_connection", side_effect=Exception("conn fail")):
            assert load_shared_ranges("B", "travel", None) == []
