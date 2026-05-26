"""
物流发货 CLI 工具单元测试。

测试 src/skills/order-logistics-1.0.0/scripts/logistics_tool.py 中的
create_shipment、update_tracking、get_shipment、list_shipments、
query_tracking、confirm_delivery 六个核心函数。

所有数据库调用均通过 mock 模拟，不依赖真实 PostgreSQL。
"""

import sys
import os
from datetime import datetime
from unittest.mock import MagicMock, patch, call
import pytest

# 将 skill scripts 目录加入 Python 路径
sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "..",
        "src",
        "skills",
        "order-logistics-1.0.0",
        "scripts",
    ),
)

import logistics_tool

pytestmark = pytest.mark.tools


# ============================================================
# 辅助工具
# ============================================================


class Args:
    """轻量级参数对象，模拟 argparse.Namespace"""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def make_mock_db(fetchone_results=None, fetchall_results=None):
    """
    构造 mock 数据库连接对象，模拟 get_db_connection() 返回的上下文管理器。

    logistics_tool 通过 conn._conn.cursor() 获取原始 tuple cursor，
    因此 mock 结构为: conn._conn.cursor() -> mock_cursor
    """
    mock_cursor = MagicMock()
    if fetchone_results is not None:
        mock_cursor.fetchone.side_effect = fetchone_results
    if fetchall_results is not None:
        mock_cursor.fetchall.side_effect = fetchall_results

    mock_inner_conn = MagicMock()
    mock_inner_conn.cursor.return_value = mock_cursor

    mock_conn = MagicMock()
    mock_conn._conn = mock_inner_conn
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)

    return mock_conn, mock_cursor


# 测试用固定数据
FIXED_NOW = datetime(2026, 5, 26, 10, 0, 0)
SHIPMENT_ROW_15 = (
    "sht_abc123",
    "tenant_001",
    "ord_xyz789",
    "顺丰速运",
    "SF1234567890",
    "陆运",
    2.5,
    "北京市朝阳区xxx",
    "in_transit",
    datetime(2026, 5, 25, 8, 0, 0),  # shipped_at
    None,                              # delivered_at
    datetime(2026, 5, 28, 18, 0, 0),  # estimated_delivery
    "易碎品轻放",
    datetime(2026, 5, 25, 8, 0, 0),  # created_at
    datetime(2026, 5, 26, 9, 30, 0), # updated_at
)

LIST_ROW_12 = (
    "sht_abc123",
    "ord_xyz789",
    "顺丰速运",
    "SF1234567890",
    "陆运",
    2.5,
    "in_transit",
    datetime(2026, 5, 25, 8, 0, 0),  # shipped_at
    None,                              # delivered_at
    datetime(2026, 5, 28, 18, 0, 0),  # estimated_delivery
    datetime(2026, 5, 25, 8, 0, 0),  # created_at
    datetime(2026, 5, 26, 9, 30, 0), # updated_at
)


# ============================================================
# create_shipment 测试
# ============================================================


class TestCreateShipment:
    """创建发货记录"""

    @patch.object(logistics_tool, "_get_current_tenant_id", return_value="tenant_001")
    @patch.object(logistics_tool, "get_db_connection")
    def test_success_with_processing_order(self, mock_get_db, mock_tenant):
        """订单状态为 processing 时自动推进到 shipped"""
        mock_conn, mock_cursor = make_mock_db(
            fetchone_results=[("processing",), (1,)]
        )
        mock_cursor.rowcount = 1
        mock_get_db.return_value = mock_conn

        args = Args(
            order_id="ord_001",
            carrier="顺丰速运",
            tracking_number="SF123",
            shipping_method="陆运",
            weight="1.5",
            shipping_address="北京市朝阳区",
            estimated_delivery="2026-06-01",
            notes="测试发货",
        )

        result = logistics_tool.create_shipment(args)

        assert result["success"] is True
        assert result["shipment_id"].startswith("sht_")
        assert result["order_status_updated"] is True
        assert result["order_previous_status"] == "processing"
        assert result["order_current_status"] == "shipped"
        assert result["status"] == "pending"

        # 验证 INSERT 发货记录
        insert_calls = [
            c for c in mock_cursor.execute.call_args_list
            if "INSERT INTO bs_order_processing_shipments" in str(c)
        ]
        assert len(insert_calls) == 1

        # 验证 UPDATE 订单状态
        update_calls = [
            c for c in mock_cursor.execute.call_args_list
            if "UPDATE bs_order_processing_orders" in str(c)
        ]
        assert len(update_calls) == 1

        # 验证写入状态历史
        history_calls = [
            c for c in mock_cursor.execute.call_args_list
            if "bs_order_processing_status_history" in str(c)
        ]
        assert len(history_calls) == 1

        # 验证 commit
        mock_conn.commit.assert_called_once()

    @patch.object(logistics_tool, "_get_current_tenant_id", return_value="tenant_001")
    @patch.object(logistics_tool, "get_db_connection")
    def test_success_with_non_processing_order(self, mock_get_db, mock_tenant):
        """订单状态非 processing 时不自动推进"""
        mock_conn, mock_cursor = make_mock_db(
            fetchone_results=[("approved",)]
        )
        mock_get_db.return_value = mock_conn

        args = Args(order_id="ord_002", carrier="中通快递")

        result = logistics_tool.create_shipment(args)

        assert result["success"] is True
        assert result["order_status_updated"] is False
        assert result["order_previous_status"] == "approved"
        assert result["order_current_status"] == "approved"

        # 不应有 UPDATE 订单操作
        update_calls = [
            c for c in mock_cursor.execute.call_args_list
            if "UPDATE" in str(c)
        ]
        assert len(update_calls) == 0

    @patch.object(logistics_tool, "_get_current_tenant_id", return_value="tenant_001")
    @patch.object(logistics_tool, "get_db_connection")
    def test_order_not_found(self, mock_get_db, mock_tenant):
        """订单不存在时返回错误"""
        mock_conn, mock_cursor = make_mock_db(fetchone_results=[None])
        mock_get_db.return_value = mock_conn

        args = Args(order_id="ord_nonexist")

        result = logistics_tool.create_shipment(args)

        assert result["success"] is False
        assert "订单不存在" in result["error"]
        assert "ord_nonexist" in result["error"]

        # 不应有 commit
        mock_conn.commit.assert_not_called()

    @patch.object(logistics_tool, "_get_current_tenant_id", return_value="tenant_001")
    @patch.object(logistics_tool, "get_db_connection")
    def test_with_tenant_id_from_args(self, mock_get_db, mock_tenant):
        """优先使用 args.tenant_id"""
        mock_conn, mock_cursor = make_mock_db(
            fetchone_results=[("processing",), (1,)]
        )
        mock_cursor.rowcount = 1
        mock_get_db.return_value = mock_conn

        args = Args(order_id="ord_003", tenant_id="tenant_custom")

        result = logistics_tool.create_shipment(args)

        assert result["success"] is True
        # INSERT 调用中应包含 tenant_custom
        insert_call = [
            c for c in mock_cursor.execute.call_args_list
            if "INSERT INTO bs_order_processing_shipments" in str(c)
        ][0]
        assert "tenant_custom" in str(insert_call)


# ============================================================
# update_tracking 测试
# ============================================================


class TestUpdateTracking:
    """更新物流追踪信息"""

    @patch.object(logistics_tool, "get_db_connection")
    def test_success_with_valid_status_transition(self, mock_get_db):
        """合法状态流转：pending -> picked_up"""
        mock_conn, mock_cursor = make_mock_db(
            fetchone_results=[("pending",)]
        )
        mock_cursor.rowcount = 1
        mock_get_db.return_value = mock_conn

        args = Args(shipment_id="sht_001", status="picked_up")

        result = logistics_tool.update_tracking(args)

        assert result["success"] is True
        assert result["previous_status"] == "pending"
        assert result["current_status"] == "picked_up"
        mock_conn.commit.assert_called_once()

    @patch.object(logistics_tool, "get_db_connection")
    def test_invalid_status_transition(self, mock_get_db):
        """非法状态流转：pending -> delivered，应报错"""
        mock_conn, mock_cursor = make_mock_db(
            fetchone_results=[("pending",)]
        )
        mock_get_db.return_value = mock_conn

        args = Args(shipment_id="sht_001", status="delivered")

        result = logistics_tool.update_tracking(args)

        assert result["success"] is False
        assert "不允许从 pending 转换到 delivered" in result["error"]
        mock_conn.commit.assert_not_called()

    @patch.object(logistics_tool, "get_db_connection")
    def test_update_without_status_change(self, mock_get_db):
        """只更新运单号，不变更状态"""
        mock_conn, mock_cursor = make_mock_db(
            fetchone_results=[("in_transit",)]
        )
        mock_cursor.rowcount = 1
        mock_get_db.return_value = mock_conn

        args = Args(shipment_id="sht_001", tracking_number="SF9999")

        result = logistics_tool.update_tracking(args)

        assert result["success"] is True
        assert result["previous_status"] == "in_transit"
        assert result["current_status"] == "in_transit"
        mock_conn.commit.assert_called_once()

    @patch.object(logistics_tool, "get_db_connection")
    def test_shipment_not_found(self, mock_get_db):
        """发货记录不存在"""
        mock_conn, mock_cursor = make_mock_db(fetchone_results=[None])
        mock_get_db.return_value = mock_conn

        args = Args(shipment_id="sht_nonexist", status="picked_up")

        result = logistics_tool.update_tracking(args)

        assert result["success"] is False
        assert "发货记录不存在" in result["error"]

    @patch.object(logistics_tool, "get_db_connection")
    def test_update_multiple_fields(self, mock_get_db):
        """同时更新多个可选字段"""
        mock_conn, mock_cursor = make_mock_db(
            fetchone_results=[("pending",)]
        )
        mock_cursor.rowcount = 1
        mock_get_db.return_value = mock_conn

        args = Args(
            shipment_id="sht_001",
            tracking_number="SF_NEW",
            carrier="圆通速递",
            notes="转运中",
            status="picked_up",
        )

        result = logistics_tool.update_tracking(args)

        assert result["success"] is True
        assert result["current_status"] == "picked_up"

        # 验证 UPDATE SQL 包含所有字段
        update_call = [
            c for c in mock_cursor.execute.call_args_list
            if "UPDATE" in str(c)
        ][0]
        sql_str = str(update_call)
        assert "tracking_number" in sql_str
        assert "carrier" in sql_str
        assert "notes" in sql_str
        assert "status" in sql_str

    @patch.object(logistics_tool, "get_db_connection")
    def test_terminal_status_no_transitions(self, mock_get_db):
        """终态（delivered/cancelled）不允许任何转换"""
        mock_conn, mock_cursor = make_mock_db(
            fetchone_results=[("delivered",)]
        )
        mock_get_db.return_value = mock_conn

        args = Args(shipment_id="sht_001", status="pending")

        result = logistics_tool.update_tracking(args)

        assert result["success"] is False
        assert "不允许从 delivered 转换到 pending" in result["error"]


# ============================================================
# get_shipment 测试
# ============================================================


class TestGetShipment:
    """查询发货详情"""

    @patch.object(logistics_tool, "get_db_connection")
    def test_success(self, mock_get_db):
        """成功查询发货记录详情"""
        mock_conn, mock_cursor = make_mock_db(
            fetchone_results=[SHIPMENT_ROW_15]
        )
        mock_get_db.return_value = mock_conn

        args = Args(shipment_id="sht_abc123")

        result = logistics_tool.get_shipment(args)

        assert result["success"] is True
        shipment = result["shipment"]
        assert shipment["shipment_id"] == "sht_abc123"
        assert shipment["tenant_id"] == "tenant_001"
        assert shipment["order_id"] == "ord_xyz789"
        assert shipment["carrier"] == "顺丰速运"
        assert shipment["tracking_number"] == "SF1234567890"
        assert shipment["shipping_method"] == "陆运"
        assert shipment["weight"] == 2.5
        assert shipment["shipping_address"] == "北京市朝阳区xxx"
        assert shipment["status"] == "in_transit"
        assert shipment["shipped_at"] == "2026-05-25T08:00:00"
        assert shipment["delivered_at"] is None
        assert shipment["estimated_delivery"] == "2026-05-28T18:00:00"
        assert shipment["notes"] == "易碎品轻放"
        assert shipment["created_at"] == "2026-05-25T08:00:00"
        assert shipment["updated_at"] == "2026-05-26T09:30:00"

    @patch.object(logistics_tool, "get_db_connection")
    def test_not_found(self, mock_get_db):
        """发货记录不存在"""
        mock_conn, mock_cursor = make_mock_db(fetchone_results=[None])
        mock_get_db.return_value = mock_conn

        args = Args(shipment_id="sht_nonexist")

        result = logistics_tool.get_shipment(args)

        assert result["success"] is False
        assert "发货记录不存在" in result["error"]


# ============================================================
# list_shipments 测试
# ============================================================


class TestListShipments:
    """查询发货列表"""

    @patch.object(logistics_tool, "_get_current_tenant_id", return_value="tenant_001")
    @patch.object(logistics_tool, "get_db_connection")
    def test_success_with_results(self, mock_get_db, mock_tenant):
        """有数据时返回列表和分页信息"""
        row2 = (
            "sht_def456",
            "ord_another",
            "中通快递",
            "ZT9876543210",
            "空运",
            1.0,
            "pending",
            datetime(2026, 5, 26, 9, 0, 0),
            None,
            None,
            datetime(2026, 5, 26, 9, 0, 0),
            datetime(2026, 5, 26, 9, 0, 0),
        )

        mock_conn, mock_cursor = make_mock_db(
            fetchone_results=[(2,)],
            fetchall_results=[[LIST_ROW_12, row2]],
        )
        mock_get_db.return_value = mock_conn

        args = Args(page=1, page_size=20)

        result = logistics_tool.list_shipments(args)

        assert result["success"] is True
        assert result["total"] == 2
        assert result["page"] == 1
        assert result["page_size"] == 20
        assert len(result["shipments"]) == 2
        assert result["shipments"][0]["shipment_id"] == "sht_abc123"
        assert result["shipments"][1]["shipment_id"] == "sht_def456"

    @patch.object(logistics_tool, "_get_current_tenant_id", return_value="tenant_001")
    @patch.object(logistics_tool, "get_db_connection")
    def test_empty_list(self, mock_get_db, mock_tenant):
        """无数据时返回空列表"""
        mock_conn, mock_cursor = make_mock_db(
            fetchone_results=[(0,)],
            fetchall_results=[[]],
        )
        mock_get_db.return_value = mock_conn

        args = Args(page=1, page_size=20)

        result = logistics_tool.list_shipments(args)

        assert result["success"] is True
        assert result["total"] == 0
        assert result["shipments"] == []

    @patch.object(logistics_tool, "_get_current_tenant_id", return_value="tenant_001")
    @patch.object(logistics_tool, "get_db_connection")
    def test_filter_by_order_id(self, mock_get_db, mock_tenant):
        """按订单 ID 过滤"""
        mock_conn, mock_cursor = make_mock_db(
            fetchone_results=[(1,)],
            fetchall_results=[[LIST_ROW_12]],
        )
        mock_get_db.return_value = mock_conn

        args = Args(order_id="ord_xyz789", page=1, page_size=10)

        result = logistics_tool.list_shipments(args)

        assert result["success"] is True
        assert result["total"] == 1
        # 验证 WHERE 条件中包含 order_id
        count_call = mock_cursor.execute.call_args_list[0]
        assert "order_id" in str(count_call)

    @patch.object(logistics_tool, "_get_current_tenant_id", return_value="tenant_001")
    @patch.object(logistics_tool, "get_db_connection")
    def test_filter_by_status(self, mock_get_db, mock_tenant):
        """按状态过滤"""
        mock_conn, mock_cursor = make_mock_db(
            fetchone_results=[(1,)],
            fetchall_results=[[LIST_ROW_12]],
        )
        mock_get_db.return_value = mock_conn

        args = Args(status="in_transit", page=1, page_size=20)

        result = logistics_tool.list_shipments(args)

        assert result["success"] is True
        count_call = mock_cursor.execute.call_args_list[0]
        assert "status" in str(count_call)

    @patch.object(logistics_tool, "_get_current_tenant_id", return_value=None)
    @patch.object(logistics_tool, "get_db_connection")
    def test_no_tenant_id(self, mock_get_db, mock_tenant):
        """无租户 ID 时使用 1=1 作为默认条件"""
        mock_conn, mock_cursor = make_mock_db(
            fetchone_results=[(0,)],
            fetchall_results=[[]],
        )
        mock_get_db.return_value = mock_conn

        args = Args(page=1, page_size=20)

        result = logistics_tool.list_shipments(args)

        assert result["success"] is True
        # 验证使用了 1=1 默认条件
        count_call = mock_cursor.execute.call_args_list[0]
        assert "1=1" in str(count_call)

    @patch.object(logistics_tool, "_get_current_tenant_id", return_value="tenant_001")
    @patch.object(logistics_tool, "get_db_connection")
    def test_pagination_offset(self, mock_get_db, mock_tenant):
        """第 2 页 offset 计算正确"""
        mock_conn, mock_cursor = make_mock_db(
            fetchone_results=[(25,)],
            fetchall_results=[[]],
        )
        mock_get_db.return_value = mock_conn

        args = Args(page=2, page_size=10)

        result = logistics_tool.list_shipments(args)

        assert result["success"] is True
        assert result["page"] == 2
        # 验证 LIMIT/OFFSET 参数
        data_call = mock_cursor.execute.call_args_list[1]
        assert "LIMIT" in str(data_call)
        # 第二个调用（列表查询）的参数列表最后两个应为 page_size 和 offset
        call_args = data_call[0][1]
        assert call_args[-2] == 10   # page_size
        assert call_args[-1] == 10   # offset = (2-1)*10


# ============================================================
# query_tracking 测试
# ============================================================


class TestQueryTracking:
    """按运单号查询物流"""

    @patch.object(logistics_tool, "get_db_connection")
    def test_found(self, mock_get_db):
        """本地数据库找到运单号"""
        mock_conn, mock_cursor = make_mock_db(
            fetchone_results=[SHIPMENT_ROW_15]
        )
        mock_get_db.return_value = mock_conn

        args = Args(tracking_number="SF1234567890")

        result = logistics_tool.query_tracking(args)

        assert result["success"] is True
        assert result["found"] is True
        assert result["shipment"]["tracking_number"] == "SF1234567890"
        assert result["shipment"]["shipment_id"] == "sht_abc123"
        assert result["shipment"]["carrier"] == "顺丰速运"
        assert result["shipment"]["status"] == "in_transit"

    @patch.object(logistics_tool, "get_db_connection")
    def test_not_found(self, mock_get_db):
        """本地数据库未找到运单号，返回外部 API 建议"""
        mock_conn, mock_cursor = make_mock_db(fetchone_results=[None])
        mock_get_db.return_value = mock_conn

        args = Args(tracking_number="UNKNOWN123")

        result = logistics_tool.query_tracking(args)

        assert result["success"] is True
        assert result["found"] is False
        assert result["tracking_number"] == "UNKNOWN123"
        assert "外部物流 API" in result["message"]


# ============================================================
# confirm_delivery 测试
# ============================================================


class TestConfirmDelivery:
    """确认签收"""

    @patch.object(logistics_tool, "_get_current_tenant_id", return_value="tenant_001")
    @patch.object(logistics_tool, "get_db_connection")
    def test_success(self, mock_get_db, mock_tenant):
        """正常签收：发货 in_transit + 订单 shipped -> 两者均更新"""
        mock_conn, mock_cursor = make_mock_db(
            fetchone_results=[
                ("ord_001", "in_transit"),  # shipment 查询
                ("shipped",),                # order 状态查询
            ]
        )
        mock_cursor.rowcount = 1
        mock_get_db.return_value = mock_conn

        args = Args(shipment_id="sht_001", notes="本人签收")

        result = logistics_tool.confirm_delivery(args)

        assert result["success"] is True
        assert result["shipment_status"] == "delivered"
        assert result["order_status_updated"] is True
        assert result["order_current_status"] == "delivered"
        assert result["order_id"] == "ord_001"

        # 验证 UPDATE shipment
        shipment_update = [
            c for c in mock_cursor.execute.call_args_list
            if "UPDATE bs_order_processing_shipments" in str(c)
        ]
        assert len(shipment_update) == 1

        # 验证 UPDATE order
        order_update = [
            c for c in mock_cursor.execute.call_args_list
            if "UPDATE bs_order_processing_orders" in str(c)
        ]
        assert len(order_update) == 1

        # 验证写入状态历史
        history = [
            c for c in mock_cursor.execute.call_args_list
            if "bs_order_processing_status_history" in str(c)
        ]
        assert len(history) == 1

        mock_conn.commit.assert_called_once()

    @patch.object(logistics_tool, "_get_current_tenant_id", return_value="tenant_001")
    @patch.object(logistics_tool, "get_db_connection")
    def test_already_delivered(self, mock_get_db, mock_tenant):
        """发货记录已签收，拒绝重复确认"""
        mock_conn, mock_cursor = make_mock_db(
            fetchone_results=[("ord_001", "delivered")]
        )
        mock_get_db.return_value = mock_conn

        args = Args(shipment_id="sht_001")

        result = logistics_tool.confirm_delivery(args)

        assert result["success"] is False
        assert "已签收" in result["error"]

    @patch.object(logistics_tool, "_get_current_tenant_id", return_value="tenant_001")
    @patch.object(logistics_tool, "get_db_connection")
    def test_cancelled_shipment(self, mock_get_db, mock_tenant):
        """已取消的发货记录无法确认签收"""
        mock_conn, mock_cursor = make_mock_db(
            fetchone_results=[("ord_001", "cancelled")]
        )
        mock_get_db.return_value = mock_conn

        args = Args(shipment_id="sht_001")

        result = logistics_tool.confirm_delivery(args)

        assert result["success"] is False
        assert "已取消" in result["error"]

    @patch.object(logistics_tool, "_get_current_tenant_id", return_value="tenant_001")
    @patch.object(logistics_tool, "get_db_connection")
    def test_order_not_shipped(self, mock_get_db, mock_tenant):
        """订单状态非 shipped（如 processing），无法签收"""
        mock_conn, mock_cursor = make_mock_db(
            fetchone_results=[
                ("ord_001", "out_for_delivery"),  # shipment 查询
                ("processing",),                    # order 状态不是 shipped
            ]
        )
        mock_get_db.return_value = mock_conn

        args = Args(shipment_id="sht_001")

        result = logistics_tool.confirm_delivery(args)

        assert result["success"] is False
        assert "processing" in result["error"]
        assert "shipped" in result["error"]

    @patch.object(logistics_tool, "_get_current_tenant_id", return_value="tenant_001")
    @patch.object(logistics_tool, "get_db_connection")
    def test_shipment_not_found(self, mock_get_db, mock_tenant):
        """发货记录不存在"""
        mock_conn, mock_cursor = make_mock_db(fetchone_results=[None])
        mock_get_db.return_value = mock_conn

        args = Args(shipment_id="sht_nonexist")

        result = logistics_tool.confirm_delivery(args)

        assert result["success"] is False
        assert "发货记录不存在" in result["error"]

    @patch.object(logistics_tool, "_get_current_tenant_id", return_value="tenant_001")
    @patch.object(logistics_tool, "get_db_connection")
    def test_order_not_found(self, mock_get_db, mock_tenant):
        """发货记录存在但关联订单不存在"""
        mock_conn, mock_cursor = make_mock_db(
            fetchone_results=[
                ("ord_ghost", "in_transit"),  # shipment 存在
                None,                          # order 不存在
            ]
        )
        mock_get_db.return_value = mock_conn

        args = Args(shipment_id="sht_001")

        result = logistics_tool.confirm_delivery(args)

        assert result["success"] is False
        assert "关联订单不存在" in result["error"]

    @patch.object(logistics_tool, "_get_current_tenant_id", return_value="tenant_001")
    @patch.object(logistics_tool, "get_db_connection")
    def test_without_notes(self, mock_get_db, mock_tenant):
        """无签收备注时 notes 字段不更新"""
        mock_conn, mock_cursor = make_mock_db(
            fetchone_results=[
                ("ord_001", "out_for_delivery"),
                ("shipped",),
            ]
        )
        mock_cursor.rowcount = 1
        mock_get_db.return_value = mock_conn

        args = Args(shipment_id="sht_001")

        result = logistics_tool.confirm_delivery(args)

        assert result["success"] is True
        # UPDATE shipment 的 SQL 不应包含 notes 字段
        shipment_update = [
            c for c in mock_cursor.execute.call_args_list
            if "UPDATE bs_order_processing_shipments" in str(c)
        ][0]
        # 只有 3 个 SET 字段 + 1 个 WHERE 参数 = 4 个参数
        assert len(shipment_update[0][1]) == 4


# ============================================================
# 状态流转定义验证
# ============================================================


class TestShipmentStatusTransitions:
    """验证 SHIPMENT_STATUS_TRANSITIONS 常量"""

    def test_transitions_dict_keys(self):
        """所有预期的状态都存在"""
        expected = {"pending", "picked_up", "in_transit", "out_for_delivery", "delivered", "cancelled"}
        assert set(logistics_tool.SHIPMENT_STATUS_TRANSITIONS.keys()) == expected

    def test_terminal_states_empty(self):
        """终态不允许任何转换"""
        assert logistics_tool.SHIPMENT_STATUS_TRANSITIONS["delivered"] == []
        assert logistics_tool.SHIPMENT_STATUS_TRANSITIONS["cancelled"] == []

    def test_pending_can_cancel(self):
        """pending 可以取消"""
        assert "cancelled" in logistics_tool.SHIPMENT_STATUS_TRANSITIONS["pending"]

    def test_each_non_terminal_can_cancel(self):
        """每个非终态都可以转换到 cancelled"""
        for status, targets in logistics_tool.SHIPMENT_STATUS_TRANSITIONS.items():
            if status not in ("delivered", "cancelled"):
                assert "cancelled" in targets, f"{status} 应允许取消"


# ============================================================
# _generate_id 测试
# ============================================================


class TestGenerateId:
    """ID 生成"""

    def test_default_prefix(self):
        """默认前缀为 sht"""
        generated_id = logistics_tool._generate_id()
        assert generated_id.startswith("sht_")

    def test_custom_prefix(self):
        """支持自定义前缀"""
        generated_id = logistics_tool._generate_id("test")
        assert generated_id.startswith("test_")

    def test_uniqueness(self):
        """多次调用生成不同 ID"""
        ids = {logistics_tool._generate_id() for _ in range(100)}
        assert len(ids) == 100
