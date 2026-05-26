"""
订单-库存集成单元测试

测试 create-order 中的库存预留和 cancel-order 中的库存释放逻辑。
所有数据库操作均 mock，不依赖真实数据库。
"""

import sys
import os
from unittest.mock import MagicMock, patch, call
import pytest
from datetime import datetime

# 将 order_tool.py 所在目录加入 sys.path
sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "..",
        "src",
        "skills",
        "order-core-1.0.0",
        "scripts",
    ),
)

import order_tool

pytestmark = pytest.mark.tools


# ============================================================
# 公共工具
# ============================================================


class Args:
    """轻量参数容器，模拟 argparse Namespace"""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def make_mock_db(fetchone_results=None, fetchall_results=None):
    """
    构造 mock 数据库连接。

    - fetchone_results: list，按调用顺序依次返回
    - fetchall_results: list，按调用顺序依次返回
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


# ============================================================
# create-order 库存预留测试
# ============================================================


class TestCreateOrderInventoryReservation:
    """create-order 中的库存预留逻辑"""

    @patch.object(order_tool, "_get_current_tenant_id", return_value="tenant_001")
    @patch.object(order_tool, "get_db_connection")
    def test_reserve_with_sufficient_inventory(self, mock_get_db, mock_tenant):
        """库存充足时，成功预留库存"""
        # fetchone 仅在 SELECT inventory FOR UPDATE 时调用（RETURNING 结果未 fetchone）
        # (100, 20) → quantity_total=100, quantity_reserved=20 → available=80
        mock_conn, mock_cursor = make_mock_db(
            fetchone_results=[(100, 20)]
        )
        mock_get_db.return_value = mock_conn

        args = Args(
            user_id="user_001",
            items='[{"product_sku":"SKU001","product_name":"Widget","quantity":2,"unit_price":10,"subtotal":20}]',
        )

        result = order_tool.create_order(args)

        assert result["success"] is True
        assert result["inventory_reservations"] is not None
        assert len(result["inventory_reservations"]) == 1
        rsv = result["inventory_reservations"][0]
        assert rsv["sku"] == "SKU001"
        assert rsv["reserved"] == 2
        assert "reservation_id" in rsv

        # 验证 UPDATE inventory 和 INSERT reservation 被调用
        execute_calls = mock_cursor.execute.call_args_list
        # 倒数第二条应为 UPDATE inventory
        assert any(
            "UPDATE bs_order_processing_inventory" in str(c)
            and "quantity_reserved" in str(c)
            for c in execute_calls
        )
        # 倒数第一条应为 INSERT reservation
        assert any(
            "INSERT INTO bs_order_processing_inventory_reservations" in str(c)
            for c in execute_calls
        )

    @patch.object(order_tool, "_get_current_tenant_id", return_value="tenant_001")
    @patch.object(order_tool, "get_db_connection")
    def test_reserve_with_insufficient_inventory(self, mock_get_db, mock_tenant):
        """库存不足时，预留失败但订单仍创建成功"""
        # (100, 98) → available=2, quantity=5 → 不足
        mock_conn, mock_cursor = make_mock_db(
            fetchone_results=[(100, 98)]
        )
        mock_get_db.return_value = mock_conn

        args = Args(
            user_id="user_001",
            items='[{"product_sku":"SKU001","product_name":"Widget","quantity":5,"unit_price":10,"subtotal":50}]',
        )

        result = order_tool.create_order(args)

        assert result["success"] is True
        rsv = result["inventory_reservations"]
        assert len(rsv) == 1
        assert rsv[0]["reserved"] == 0
        assert "库存不足" in rsv[0]["error"]

        # 不应有 INSERT reservation
        execute_calls = mock_cursor.execute.call_args_list
        assert not any(
            "INSERT INTO bs_order_processing_inventory_reservations" in str(c)
            for c in execute_calls
        )

    @patch.object(order_tool, "_get_current_tenant_id", return_value="tenant_001")
    @patch.object(order_tool, "get_db_connection")
    def test_reserve_sku_not_in_inventory(self, mock_get_db, mock_tenant):
        """SKU 不在库存表中时跳过预留，订单正常创建"""
        # SELECT inventory FOR UPDATE → fetchone 返回 None → SKU 不存在
        mock_conn, mock_cursor = make_mock_db(
            fetchone_results=[None]
        )
        mock_get_db.return_value = mock_conn

        args = Args(
            user_id="user_001",
            items='[{"product_sku":"SKU_NOT_EXIST","product_name":"Ghost","quantity":1,"unit_price":5,"subtotal":5}]',
        )

        result = order_tool.create_order(args)

        assert result["success"] is True
        # 没有任何预留结果 → inventory_reservations 为空列表 → 转为 None
        assert result["inventory_reservations"] is None

    @patch.object(order_tool, "_get_current_tenant_id", return_value="tenant_001")
    @patch.object(order_tool, "get_db_connection")
    def test_skip_inventory_flag(self, mock_get_db, mock_tenant):
        """skip_inventory=True 时完全跳过库存查询"""
        # 跳过库存时无 fetchone 调用
        mock_conn, mock_cursor = make_mock_db()
        mock_get_db.return_value = mock_conn

        args = Args(
            user_id="user_001",
            items='[{"product_sku":"SKU001","product_name":"Widget","quantity":2,"unit_price":10,"subtotal":20}]',
            skip_inventory=True,
        )

        result = order_tool.create_order(args)

        assert result["success"] is True
        assert result["inventory_reservations"] is None

        # 不应出现任何 inventory 相关 SQL
        execute_calls = mock_cursor.execute.call_args_list
        for c in execute_calls:
            sql_str = str(c)
            assert "bs_order_processing_inventory" not in sql_str

    @patch.object(order_tool, "_get_current_tenant_id", return_value="tenant_001")
    @patch.object(order_tool, "get_db_connection")
    def test_item_missing_product_sku(self, mock_get_db, mock_tenant):
        """商品缺少 product_sku 时跳过该商品的预留"""
        # 无 SKU → 不进入库存查询分支，无 fetchone
        mock_conn, mock_cursor = make_mock_db()
        mock_get_db.return_value = mock_conn

        args = Args(
            user_id="user_001",
            items='[{"product_name":"No-SKU Item","quantity":1,"unit_price":10,"subtotal":10}]',
        )

        result = order_tool.create_order(args)

        assert result["success"] is True
        assert result["inventory_reservations"] is None

        # 只应有 INSERT orders / items / history 的调用，无 inventory 查询
        execute_calls = mock_cursor.execute.call_args_list
        for c in execute_calls:
            sql_str = str(c)
            assert "bs_order_processing_inventory" not in sql_str

    @patch.object(order_tool, "_get_current_tenant_id", return_value="tenant_001")
    @patch.object(order_tool, "get_db_connection")
    def test_multi_items_mixed_availability(self, mock_get_db, mock_tenant):
        """多商品混合：一个库存充足、一个库存不足、一个无 SKU"""
        # fetchone 序列（仅库存 SELECT 调用 fetchone，INSERT RETURNING 不调用）:
        #   1) SKU001 inventory → (50, 10) available=40 >= qty=2
        #   2) SKU002 inventory → (10, 9) available=1 < qty=3
        # SKU003 没有 product_sku → 跳过
        mock_conn, mock_cursor = make_mock_db(
            fetchone_results=[(50, 10), (10, 9)]
        )
        mock_get_db.return_value = mock_conn

        items_json = (
            '[{"product_sku":"SKU001","product_name":"A","quantity":2,"unit_price":10,"subtotal":20},'
            '{"product_sku":"SKU002","product_name":"B","quantity":3,"unit_price":5,"subtotal":15},'
            '{"product_name":"C","quantity":1,"unit_price":8,"subtotal":8}]'
        )
        args = Args(user_id="user_001", items=items_json)

        result = order_tool.create_order(args)

        assert result["success"] is True
        rsv = result["inventory_reservations"]
        assert len(rsv) == 2

        # SKU001 预留成功
        assert rsv[0]["sku"] == "SKU001"
        assert rsv[0]["reserved"] == 2

        # SKU002 库存不足
        assert rsv[1]["sku"] == "SKU002"
        assert rsv[1]["reserved"] == 0
        assert "库存不足" in rsv[1]["error"]


# ============================================================
# cancel-order 库存释放测试
# ============================================================


class TestCancelOrderInventoryRelease:
    """cancel-order 中的库存释放逻辑"""

    @patch.object(order_tool, "_get_current_tenant_id", return_value="tenant_001")
    @patch.object(order_tool, "get_db_connection")
    def test_release_active_reservations(self, mock_get_db, mock_tenant):
        """取消订单时释放所有活跃库存预留"""
        # fetchone: SELECT status → ("draft",)
        # fetchall: reservations → [("rsv_001","SKU001",2), ("rsv_002","SKU002",1)]
        mock_conn, mock_cursor = make_mock_db(
            fetchone_results=[("draft",)],
            fetchall_results=[[("rsv_001", "SKU001", 2), ("rsv_002", "SKU002", 1)]],
        )
        mock_get_db.return_value = mock_conn

        args = Args(order_id="ord_123456789012", reason="客户要求取消")

        result = order_tool.cancel_order(args)

        assert result["success"] is True
        assert result["inventory_released"] == 2
        assert result["status"] == "cancelled"
        assert result["previous_status"] == "draft"

        execute_calls = mock_cursor.execute.call_args_list

        # 验证释放预留的 UPDATE 被调用（每条预留 2 次 UPDATE）
        release_reservation_calls = [
            c for c in execute_calls
            if "bs_order_processing_inventory_reservations" in str(c)
            and "released" in str(c)
        ]
        assert len(release_reservation_calls) == 2

        # 验证库存恢复的 UPDATE 被调用
        restore_inventory_calls = [
            c for c in execute_calls
            if "UPDATE bs_order_processing_inventory" in str(c)
            and "quantity_reserved" in str(c)
        ]
        assert len(restore_inventory_calls) == 2

    @patch.object(order_tool, "_get_current_tenant_id", return_value="tenant_001")
    @patch.object(order_tool, "get_db_connection")
    def test_no_active_reservations(self, mock_get_db, mock_tenant):
        """取消订单时无活跃预留，库存释放数为 0"""
        mock_conn, mock_cursor = make_mock_db(
            fetchone_results=[("draft",)],
            fetchall_results=[[]],
        )
        mock_get_db.return_value = mock_conn

        args = Args(order_id="ord_123456789012", reason="不需要了")

        result = order_tool.cancel_order(args)

        assert result["success"] is True
        assert result["inventory_released"] == 0

        # 不应有 inventory 相关的 UPDATE
        execute_calls = mock_cursor.execute.call_args_list
        inventory_updates = [
            c for c in execute_calls
            if "UPDATE bs_order_processing_inventory" in str(c)
            and "quantity_reserved" in str(c)
        ]
        assert len(inventory_updates) == 0

    @patch.object(order_tool, "_get_current_tenant_id", return_value="tenant_001")
    @patch.object(order_tool, "get_db_connection")
    def test_cancel_not_allowed_for_status(self, mock_get_db, mock_tenant):
        """处于不可取消状态的订单，取消操作应失败"""
        # "delivered" 只允许 ["completed", "return_requested"]，不允许 "cancelled"
        mock_conn, mock_cursor = make_mock_db(
            fetchone_results=[("delivered",)],
        )
        mock_get_db.return_value = mock_conn

        args = Args(order_id="ord_123456789012", reason="想取消")

        result = order_tool.cancel_order(args)

        assert result["success"] is False
        assert "不允许取消" in result["error"]

    @patch.object(order_tool, "_get_current_tenant_id", return_value="tenant_001")
    @patch.object(order_tool, "get_db_connection")
    def test_cancel_order_not_found(self, mock_get_db, mock_tenant):
        """订单不存在时返回错误"""
        mock_conn, mock_cursor = make_mock_db(
            fetchone_results=[None],
        )
        mock_get_db.return_value = mock_conn

        args = Args(order_id="ord_nonexistent")

        result = order_tool.cancel_order(args)

        assert result["success"] is False
        assert "不存在" in result["error"]

    @patch.object(order_tool, "_get_current_tenant_id", return_value="tenant_001")
    @patch.object(order_tool, "get_db_connection")
    def test_release_restores_correct_quantity(self, mock_get_db, mock_tenant):
        """释放预留时，每条预留的 quantity 正确传递给库存恢复 SQL"""
        mock_conn, mock_cursor = make_mock_db(
            fetchone_results=[("approved",)],
            fetchall_results=[[("rsv_100", "SKU_X", 7)]],
        )
        mock_get_db.return_value = mock_conn

        args = Args(order_id="ord_test", reason="测试")

        result = order_tool.cancel_order(args)

        assert result["success"] is True
        assert result["inventory_released"] == 1

        # 找到库存恢复的 UPDATE 调用，验证参数
        execute_calls = mock_cursor.execute.call_args_list
        restore_calls = [
            c for c in execute_calls
            if "UPDATE bs_order_processing_inventory" in str(c)
            and "quantity_reserved" in str(c)
        ]
        assert len(restore_calls) == 1

        # 验证参数：第一个位置参数应为 7 (quantity)
        call_args = restore_calls[0]
        # call_args[0] 是位置参数 tuple，call_args[0][1] 是 SQL 参数 tuple
        params = call_args[0][1]
        assert params[0] == 7  # quantity_reserved - 7
        assert params[1] == 7  # quantity_available + 7
        assert params[3] == "tenant_001"  # tenant_id
        assert params[4] == "SKU_X"  # product_sku
