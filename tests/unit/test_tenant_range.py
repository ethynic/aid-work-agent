"""
检索租户范围 SQL 构建函数测试。

验证 build_tenant_range_conditions：本租户 + 已启用共享精确对（§6.1 设计约束）。
验证 load_shared_ranges：数字员工级启用 ∩ 租户级授权的交集计算。
验证 load_authorized_source_types / resolve_category_scope：本租户栏目授权（2026-09-20）。
"""
from unittest.mock import patch

from src.knowledge.retriever.tenant_range import (
    build_tenant_range_conditions,
    load_authorized_source_types,
    load_shared_ranges,
    resolve_category_scope,
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


def test_range_no_tenant_returns_empty():
    """无 tenant_id（platform_admin 全局视图等）时返回空 SQL 与空参数，由调用方走无主文档分支"""
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


def test_range_source_type_list_uses_any():
    """source_type 为授权收窄 list 时生成 source_type = ANY(%s)（栏目授权收窄场景）"""
    sql, params = build_tenant_range_conditions("B", ["cat_a", "cat_b"], None)
    assert sql == "(d.tenant_id = %s AND d.source_type = ANY(%s))"
    assert params == ["B", ["cat_a", "cat_b"]]


def test_range_source_type_list_with_shared_ranges():
    """收窄 list 与共享精确对叠加（本租户收窄 + 共享精确对同时生效）"""
    sql, params = build_tenant_range_conditions("B", ["cat_a"], [("A1", "hotel_resource")])
    assert sql == (
        "(d.tenant_id = %s AND d.source_type = ANY(%s)) OR "
        "(d.tenant_id = %s AND d.source_type = %s)"
    )
    assert params == ["B", ["cat_a"], "A1", "hotel_resource"]


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


# ---------------------------------------------------------------
# load_authorized_source_types：本租户自有授权栏目（owner_tenant_id 为空的项）
# ---------------------------------------------------------------

class TestLoadAuthorizedSourceTypes:
    """授权语义：空 = 允许全部（None）；非空 = 仅允许列表内栏目"""

    def test_empty_tenant_or_subagent_returns_none(self):
        """主智能体 / 无租户上下文不受限"""
        assert load_authorized_source_types(None, None) is None
        assert load_authorized_source_types("B", None) is None
        assert load_authorized_source_types(None, "data-analysis") is None

    def test_no_row_returns_none(self):
        """无配置行 = 未配置 = 允许全部"""
        with patch("src.db.database.get_db_connection", return_value=_FakeConn(None)):
            assert load_authorized_source_types("B", "data-analysis") is None

    def test_only_shared_items_returns_none(self):
        """仅共享项（owner_tenant_id 非空）= 自有项为空 = 未配置"""
        row = {"sources": [
            {"owner_tenant_id": "A1", "source_type": "hotel_resource"},
        ]}
        with patch("src.db.database.get_db_connection", return_value=_FakeConn(row)):
            assert load_authorized_source_types("B", "data-analysis") is None

    def test_owned_items_returned_excluding_shared(self):
        """混合项时只返回自有栏目；共享项不参与判定"""
        row = {"sources": [
            {"owner_tenant_id": None, "source_type": "data-analysis"},
            {"owner_tenant_id": "A1", "source_type": "hotel_resource"},
            {"source_type": "file"},  # owner_tenant_id 缺失同样视为自有项
        ]}
        with patch("src.db.database.get_db_connection", return_value=_FakeConn(row)):
            assert load_authorized_source_types("B", "data-analysis") == [
                "data-analysis", "file",
            ]

    def test_blank_source_type_skipped_and_empty_owned_means_none(self):
        """空 source_type 的自有项被剔除；剔除后为空 = 未配置"""
        row = {"sources": [
            {"owner_tenant_id": None, "source_type": "  "},
            {"owner_tenant_id": None},
        ]}
        with patch("src.db.database.get_db_connection", return_value=_FakeConn(row)):
            assert load_authorized_source_types("B", "data-analysis") is None

    def test_non_dict_items_ignored(self):
        """sources 中非 dict 项跳过"""
        row = {"sources": ["garbage", {"owner_tenant_id": None, "source_type": "cat_a"}]}
        with patch("src.db.database.get_db_connection", return_value=_FakeConn(row)):
            assert load_authorized_source_types("B", "data-analysis") == ["cat_a"]

    def test_db_error_returns_none(self):
        """数据库异常时降级为未配置（允许全部），不阻断检索"""
        with patch("src.db.database.get_db_connection", side_effect=Exception("conn fail")):
            assert load_authorized_source_types("B", "data-analysis") is None


# ---------------------------------------------------------------
# resolve_category_scope：栏目授权收口（收窄 / 拒绝）
# ---------------------------------------------------------------

class TestResolveCategoryScope:
    """knowledge_base_search / knowledge_file_search 共用收口规则（§4.2）"""

    def test_not_configured_passthrough(self):
        """未配置精细授权：行为与现状一致，原样透传"""
        with patch(
            "src.knowledge.retriever.tenant_range.load_authorized_source_types",
            return_value=None,
        ):
            assert resolve_category_scope("B", "da", "cat_x") == ("cat_x", None)
            assert resolve_category_scope("B", "da", None) == (None, None)

    def test_requested_authorized_passthrough(self):
        """传参在授权集合内：正常透传"""
        with patch(
            "src.knowledge.retriever.tenant_range.load_authorized_source_types",
            return_value=["cat_a", "cat_b"],
        ):
            assert resolve_category_scope("B", "da", "cat_a") == ("cat_a", None)

    def test_requested_unauthorized_rejected_with_doc_counts(self):
        """传参未授权：拒绝并返回各授权栏目文档数，帮助 LLM 自纠"""
        with patch(
            "src.knowledge.retriever.tenant_range.load_authorized_source_types",
            return_value=["cat_a", "cat_b"],
        ), patch(
            "src.knowledge.retriever.tenant_range.count_active_documents_by_source_types",
            return_value={"cat_a": 3, "cat_b": 0},
        ):
            effective, rejection = resolve_category_scope("B", "da", "cat_x")
        assert effective is None
        assert rejection is not None
        assert rejection["authorized_categories"] == [
            {"source_type": "cat_a", "doc_count": 3},
            {"source_type": "cat_b", "doc_count": 0},
        ]
        assert "cat_x" in rejection["note"]
        assert "cat_a（3 篇文档）" in rejection["note"]

    def test_no_request_narrows_to_authorized_set(self):
        """未传 source_type：收窄为授权栏目集合（list）"""
        with patch(
            "src.knowledge.retriever.tenant_range.load_authorized_source_types",
            return_value=["cat_a", "cat_b"],
        ):
            effective, rejection = resolve_category_scope("B", "da", None)
        assert effective == ["cat_a", "cat_b"]
        assert rejection is None
