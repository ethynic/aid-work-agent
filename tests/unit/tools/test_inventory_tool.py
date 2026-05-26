"""
库存管理 CLI 工具单元测试

测试 inventory_tool.py 中所有命令函数（mock 数据库连接）。
"""

import sys
import os
import json
import types
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

# 将 skill scripts 目录加入 path，以便 import inventory_tool
sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(__file__),
        '..', '..', '..',
        'src', 'skills', 'order-inventory-1.0.0', 'scripts',
    ),
)

import inventory_tool  # noqa: E402

pytestmark = pytest.mark.tools


# ============================================================
# Helpers
# ============================================================

class Args:
    """模拟 argparse.Namespace 的简单容器"""
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def make_mock_db(fetchone_results=None, fetchall_results=None, rowcount=1):
    """
    构建模拟 get_db_connection() 返回值的 context manager。

    fetchone_results: list — 依次返回的 fetchone 值
    fetchall_results: list — 依次返回的 fetchall 值
    rowcount: int — cursor.rowcount 返回值
    """
    mock_cursor = MagicMock()
    mock_cursor.rowcount = rowcount

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


# 固定 tenant_id
TENANT_ID = "tenant_001"


def _patches(mock_conn):
    """返回常用的一组 patch 装饰器/上下文管理器"""
    return (
        patch.object(inventory_tool, 'get_db_connection', return_value=mock_conn),
        patch.object(inventory_tool, '_get_current_tenant_id', return_value=TENANT_ID),
    )


# ============================================================
# 1. create_product
# ============================================================

class TestCreateProduct:

    def test_success(self):
        mock_conn, mock_cursor = make_mock_db()
        with _patches(mock_conn)[0] as mock_db, _patches(mock_conn)[1] as mock_tid:
            result = inventory_tool.create_product(
                Args(product_sku="SKU001", product_name="Test Product")
            )

        assert result["success"] is True
        assert result["product_id"].startswith("prod_")
        assert result["product_sku"] == "SKU001"
        assert result["product_name"] == "Test Product"
        assert result["inventory_initialized"] is True

        # 验证执行了 INSERT INTO products 和 INSERT INTO inventory
        sqls = [c[0][0] for c in mock_cursor.execute.call_args_list]
        assert any("INSERT INTO bs_order_processing_products" in s for s in sqls)
        assert any("INSERT INTO bs_order_processing_inventory" in s for s in sqls)

    def test_with_optional_fields(self):
        mock_conn, mock_cursor = make_mock_db()
        with _patches(mock_conn)[0] as mock_db, _patches(mock_conn)[1] as mock_tid:
            result = inventory_tool.create_product(
                Args(
                    product_sku="SKU002",
                    product_name="Full Product",
                    category="electronics",
                    brand="BrandX",
                    model="M1",
                    cost_price="99.00",
                    selling_price="149.00",
                    status="active",
                    unit="台",
                )
            )

        assert result["success"] is True
        assert result["product_sku"] == "SKU002"

    def test_db_error(self):
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(side_effect=Exception("DB down"))
        mock_conn.__exit__ = MagicMock(return_value=False)

        with patch.object(inventory_tool, 'get_db_connection', return_value=mock_conn):
            result = inventory_tool.create_product(
                Args(product_sku="SKU003", product_name="Fail Product")
            )

        assert result["success"] is False
        assert "DB down" in result["error"]


# ============================================================
# 2. get_product
# ============================================================

class TestGetProduct:

    def test_success(self):
        now = datetime.now()
        # 21 fields for product row + 5 fields for inventory row
        product_row = (
            "prod_abc123", TENANT_ID, "SKU001", "Test Product",
            "cat1", "BrandX", "M1",           # category, brand, model
            '{"k":"v"}', "个",                # specifications, unit
            99.00, 149.00,                    # cost_price, selling_price
            "desc", "img1.jpg", "tag1",       # description, images, tags
            "active", 1.5, "BC001",           # status, weight, barcode
            "ext_001", "SupplierA",           # external_product_id, supplier
            now, now,                         # created_at, updated_at
        )
        inventory_row = (100, 20, 80, 10, now)

        mock_conn, mock_cursor = make_mock_db(
            fetchone_results=[product_row, inventory_row]
        )
        with _patches(mock_conn)[0] as mock_db, _patches(mock_conn)[1] as mock_tid:
            result = inventory_tool.get_product(Args(product_id="prod_abc123"))

        assert result["success"] is True
        assert result["product"]["product_id"] == "prod_abc123"
        assert result["product"]["product_sku"] == "SKU001"
        assert result["product"]["cost_price"] == 99.0
        assert result["product"]["selling_price"] == 149.0
        assert result["inventory"]["quantity_total"] == 100
        assert result["inventory"]["quantity_reserved"] == 20
        assert result["inventory"]["is_low_stock"] is False

    def test_not_found(self):
        mock_conn, mock_cursor = make_mock_db(fetchone_results=[None])
        with _patches(mock_conn)[0] as mock_db, _patches(mock_conn)[1] as mock_tid:
            result = inventory_tool.get_product(Args(product_id="prod_missing"))

        assert result["success"] is False
        assert "不存在" in result["error"]

    def test_product_without_inventory(self):
        now = datetime.now()
        product_row = (
            "prod_xyz", None, "SKU999", "NoInv",
            None, None, None, None, "个",
            None, None, None, None, None,
            "active", None, None, None, None,
            now, now,
        )
        # inventory fetchone returns None
        mock_conn, mock_cursor = make_mock_db(
            fetchone_results=[product_row, None]
        )
        with _patches(mock_conn)[0] as mock_db, _patches(mock_conn)[1] as mock_tid:
            result = inventory_tool.get_product(Args(product_id="prod_xyz"))

        assert result["success"] is True
        assert result["inventory"] is None


# ============================================================
# 3. list_products
# ============================================================

class TestListProducts:

    def test_success_with_pagination(self):
        now = datetime.now()
        # fetchone: count; fetchall: list of 9-field tuples
        count_row = (2,)
        product_rows = [
            ("prod_1", "SKU001", "Product A", "cat1", "BrandA", 99.0, "active", now, now),
            ("prod_2", "SKU002", "Product B", "cat2", "BrandB", 149.0, "active", now, now),
        ]

        mock_conn, mock_cursor = make_mock_db(
            fetchone_results=[count_row],
            fetchall_results=[product_rows],
        )
        with _patches(mock_conn)[0] as mock_db, _patches(mock_conn)[1] as mock_tid:
            result = inventory_tool.list_products(Args(page=1, page_size=20))

        assert result["success"] is True
        assert result["total"] == 2
        assert len(result["products"]) == 2
        assert result["page"] == 1
        assert result["page_size"] == 20
        assert result["products"][0]["product_sku"] == "SKU001"

    def test_empty_list(self):
        mock_conn, mock_cursor = make_mock_db(
            fetchone_results=[(0,)],
            fetchall_results=[[]],
        )
        with _patches(mock_conn)[0] as mock_db, _patches(mock_conn)[1] as mock_tid:
            result = inventory_tool.list_products(Args())

        assert result["success"] is True
        assert result["total"] == 0
        assert result["products"] == []

    def test_with_filters(self):
        now = datetime.now()
        mock_conn, mock_cursor = make_mock_db(
            fetchone_results=[(1,)],
            fetchall_results=[[("prod_1", "SKU001", "Product A", "electronics", "BrandA", 99.0, "active", now, now)]],
        )
        with _patches(mock_conn)[0] as mock_db, _patches(mock_conn)[1] as mock_tid:
            result = inventory_tool.list_products(
                Args(category="electronics", status="active", keyword="Product")
            )

        assert result["success"] is True
        # 验证 SQL 中包含 filter 条件
        count_sql = mock_cursor.execute.call_args_list[0][0][0]
        assert "category = %s" in count_sql
        assert "status = %s" in count_sql
        assert "ILIKE" in count_sql


# ============================================================
# 4. update_product
# ============================================================

class TestUpdateProduct:

    def test_success(self):
        mock_conn, mock_cursor = make_mock_db(
            fetchone_results=[("prod_abc123",)],
            rowcount=1,
        )
        with _patches(mock_conn)[0] as mock_db, _patches(mock_conn)[1] as mock_tid:
            result = inventory_tool.update_product(
                Args(product_id="prod_abc123", product_name="Updated Name")
            )

        assert result["success"] is True
        assert result["product_id"] == "prod_abc123"
        assert "updated_at" in result

        # 验证执行了 UPDATE SQL
        sqls = [c[0][0] for c in mock_cursor.execute.call_args_list]
        assert any("UPDATE bs_order_processing_products" in s for s in sqls)

    def test_not_found(self):
        mock_conn, mock_cursor = make_mock_db(fetchone_results=[None])
        with _patches(mock_conn)[0] as mock_db, _patches(mock_conn)[1] as mock_tid:
            result = inventory_tool.update_product(
                Args(product_id="prod_missing", product_name="New Name")
            )

        assert result["success"] is False
        assert "不存在" in result["error"]

    def test_rowcount_zero(self):
        # exists 检查通过，但 UPDATE 影响 0 行
        mock_conn, mock_cursor = make_mock_db(
            fetchone_results=[("prod_abc123",)],
            rowcount=0,
        )
        with _patches(mock_conn)[0] as mock_db, _patches(mock_conn)[1] as mock_tid:
            result = inventory_tool.update_product(
                Args(product_id="prod_abc123", product_name="New")
            )

        assert result["success"] is False

    def test_update_multiple_fields(self):
        mock_conn, mock_cursor = make_mock_db(
            fetchone_results=[("prod_multi",)],
            rowcount=1,
        )
        with _patches(mock_conn)[0] as mock_db, _patches(mock_conn)[1] as mock_tid:
            result = inventory_tool.update_product(
                Args(
                    product_id="prod_multi",
                    product_name="New Name",
                    category="new_cat",
                    selling_price="199.00",
                    status="inactive",
                )
            )

        assert result["success"] is True
        update_sql = mock_cursor.execute.call_args_list[1][0][0]
        assert "product_name" in update_sql
        assert "category" in update_sql
        assert "selling_price" in update_sql
        assert "status" in update_sql


# ============================================================
# 5. check_stock
# ============================================================

class TestCheckStock:

    def test_success(self):
        now = datetime.now()
        # (sku, name, qty_total, qty_reserved, qty_available, low_stock_threshold, last_synced_at)
        row = ("SKU001", "Test Product", 100, 20, 80, 10, now)

        mock_conn, mock_cursor = make_mock_db(fetchone_results=[row])
        with _patches(mock_conn)[0] as mock_db, _patches(mock_conn)[1] as mock_tid:
            result = inventory_tool.check_stock(Args(sku="SKU001"))

        assert result["success"] is True
        assert result["product_sku"] == "SKU001"
        assert result["quantity_total"] == 100
        assert result["quantity_reserved"] == 20
        # available = total - reserved = 100 - 20 = 80
        assert result["quantity_available"] == 80
        # is_low_stock = 80 < 10 → False
        assert result["is_low_stock"] is False

    def test_low_stock(self):
        now = datetime.now()
        row = ("SKU002", "Low Item", 10, 5, 5, 10, now)

        mock_conn, mock_cursor = make_mock_db(fetchone_results=[row])
        with _patches(mock_conn)[0] as mock_db, _patches(mock_conn)[1] as mock_tid:
            result = inventory_tool.check_stock(Args(sku="SKU002"))

        assert result["success"] is True
        # available = 10 - 5 = 5, is_low_stock = 5 < 10 → True
        assert result["quantity_available"] == 5
        assert result["is_low_stock"] is True

    def test_not_found(self):
        mock_conn, mock_cursor = make_mock_db(fetchone_results=[None])
        with _patches(mock_conn)[0] as mock_db, _patches(mock_conn)[1] as mock_tid:
            result = inventory_tool.check_stock(Args(sku="SKU_MISSING"))

        assert result["success"] is False
        assert "不存在" in result["error"]


# ============================================================
# 6. reserve_stock
# ============================================================

class TestReserveStock:

    def test_success(self):
        # (quantity_total, quantity_reserved) → available = 100 - 20 = 80
        mock_conn, mock_cursor = make_mock_db(
            fetchone_results=[(100, 20)]
        )
        with _patches(mock_conn)[0] as mock_db, _patches(mock_conn)[1] as mock_tid:
            result = inventory_tool.reserve_stock(
                Args(order_id="ORD001", sku="SKU001", quantity="5", expires_minutes=60)
            )

        assert result["success"] is True
        assert result["reservation_id"].startswith("rsv_")
        assert result["order_id"] == "ORD001"
        assert result["product_sku"] == "SKU001"
        assert result["quantity"] == 5
        # new_reserved = 20 + 5 = 25, new_available = 100 - 25 = 75
        assert result["quantity_available_after_reserve"] == 75

        # 验证执行了 UPDATE inventory 和 INSERT reservation
        sqls = [c[0][0] for c in mock_cursor.execute.call_args_list]
        assert any("UPDATE bs_order_processing_inventory" in s for s in sqls)
        assert any("INSERT INTO bs_order_processing_inventory_reservations" in s for s in sqls)

    def test_insufficient_stock(self):
        # available = 10 - 8 = 2, request 5 → fail
        mock_conn, mock_cursor = make_mock_db(
            fetchone_results=[(10, 8)]
        )
        with _patches(mock_conn)[0] as mock_db, _patches(mock_conn)[1] as mock_tid:
            result = inventory_tool.reserve_stock(
                Args(order_id="ORD002", sku="SKU002", quantity="5")
            )

        assert result["success"] is False
        assert "库存不足" in result["error"]
        assert result["available"] == 2
        assert result["requested"] == 5

    def test_not_found(self):
        mock_conn, mock_cursor = make_mock_db(fetchone_results=[None])
        with _patches(mock_conn)[0] as mock_db, _patches(mock_conn)[1] as mock_tid:
            result = inventory_tool.reserve_stock(
                Args(order_id="ORD003", sku="SKU_MISSING", quantity="1")
            )

        assert result["success"] is False
        assert "不存在" in result["error"]

    def test_exact_available(self):
        # available = 100 - 95 = 5, request 5 → succeed
        mock_conn, mock_cursor = make_mock_db(
            fetchone_results=[(100, 95)]
        )
        with _patches(mock_conn)[0] as mock_db, _patches(mock_conn)[1] as mock_tid:
            result = inventory_tool.reserve_stock(
                Args(order_id="ORD004", sku="SKU003", quantity="5")
            )

        assert result["success"] is True
        assert result["quantity_available_after_reserve"] == 0


# ============================================================
# 7. commit_reservation
# ============================================================

class TestCommitReservation:

    def test_success(self):
        rsv_id = "rsv_test001"
        # (reservation_id, product_sku, quantity, status)
        mock_conn, mock_cursor = make_mock_db(
            fetchone_results=[(rsv_id, "SKU001", 5, "active")]
        )
        with _patches(mock_conn)[0] as mock_db, _patches(mock_conn)[1] as mock_tid:
            result = inventory_tool.commit_reservation(
                Args(reservation_id=rsv_id)
            )

        assert result["success"] is True
        assert result["reservation_id"] == rsv_id
        assert result["product_sku"] == "SKU001"
        assert result["quantity_committed"] == 5
        assert result["status"] == "committed"

        # 验证更新了 inventory 和 reservation
        sqls = [c[0][0] for c in mock_cursor.execute.call_args_list]
        assert any("UPDATE bs_order_processing_inventory" in s for s in sqls)
        assert any("UPDATE bs_order_processing_inventory_reservations" in s for s in sqls)

    def test_not_active(self):
        rsv_id = "rsv_test002"
        mock_conn, mock_cursor = make_mock_db(
            fetchone_results=[(rsv_id, "SKU001", 5, "released")]
        )
        with _patches(mock_conn)[0] as mock_db, _patches(mock_conn)[1] as mock_tid:
            result = inventory_tool.commit_reservation(
                Args(reservation_id=rsv_id)
            )

        assert result["success"] is False
        assert "active" in result["error"]

    def test_not_found(self):
        mock_conn, mock_cursor = make_mock_db(fetchone_results=[None])
        with _patches(mock_conn)[0] as mock_db, _patches(mock_conn)[1] as mock_tid:
            result = inventory_tool.commit_reservation(
                Args(reservation_id="rsv_missing")
            )

        assert result["success"] is False
        assert "不存在" in result["error"]

    def test_already_committed(self):
        rsv_id = "rsv_test003"
        mock_conn, mock_cursor = make_mock_db(
            fetchone_results=[(rsv_id, "SKU001", 3, "committed")]
        )
        with _patches(mock_conn)[0] as mock_db, _patches(mock_conn)[1] as mock_tid:
            result = inventory_tool.commit_reservation(
                Args(reservation_id=rsv_id)
            )

        assert result["success"] is False
        assert "active" in result["error"]


# ============================================================
# 8. release_reservation
# ============================================================

class TestReleaseReservation:

    def test_success(self):
        rsv_id = "rsv_release001"
        mock_conn, mock_cursor = make_mock_db(
            fetchone_results=[(rsv_id, "SKU001", 5, "active")]
        )
        with _patches(mock_conn)[0] as mock_db, _patches(mock_conn)[1] as mock_tid:
            result = inventory_tool.release_reservation(
                Args(reservation_id=rsv_id)
            )

        assert result["success"] is True
        assert result["reservation_id"] == rsv_id
        assert result["product_sku"] == "SKU001"
        assert result["quantity_released"] == 5
        assert result["status"] == "released"

        # 验证归还了库存并更新了 reservation
        sqls = [c[0][0] for c in mock_cursor.execute.call_args_list]
        assert any("UPDATE bs_order_processing_inventory" in s for s in sqls)
        assert any("released" in str(c[0]) for c in mock_cursor.execute.call_args_list)

    def test_not_active(self):
        rsv_id = "rsv_release002"
        mock_conn, mock_cursor = make_mock_db(
            fetchone_results=[(rsv_id, "SKU001", 5, "committed")]
        )
        with _patches(mock_conn)[0] as mock_db, _patches(mock_conn)[1] as mock_tid:
            result = inventory_tool.release_reservation(
                Args(reservation_id=rsv_id)
            )

        assert result["success"] is False
        assert "active" in result["error"]

    def test_not_found(self):
        mock_conn, mock_cursor = make_mock_db(fetchone_results=[None])
        with _patches(mock_conn)[0] as mock_db, _patches(mock_conn)[1] as mock_tid:
            result = inventory_tool.release_reservation(
                Args(reservation_id="rsv_missing")
            )

        assert result["success"] is False
        assert "不存在" in result["error"]


# ============================================================
# 9. update_stock
# ============================================================

class TestUpdateStock:

    def test_positive_delta(self):
        mock_conn, mock_cursor = make_mock_db(fetchone_results=[(50,)])
        with _patches(mock_conn)[0] as mock_db, _patches(mock_conn)[1] as mock_tid:
            result = inventory_tool.update_stock(
                Args(sku="SKU001", quantity_delta="5", reason="replenish")
            )

        assert result["success"] is True
        assert result["previous_total"] == 50
        assert result["quantity_delta"] == 5
        assert result["new_total"] == 55
        assert result["reason"] == "replenish"

    def test_negative_delta(self):
        mock_conn, mock_cursor = make_mock_db(fetchone_results=[(50,)])
        with _patches(mock_conn)[0] as mock_db, _patches(mock_conn)[1] as mock_tid:
            result = inventory_tool.update_stock(
                Args(sku="SKU001", quantity_delta="-10", reason="correction")
            )

        assert result["success"] is True
        assert result["previous_total"] == 50
        assert result["quantity_delta"] == -10
        assert result["new_total"] == 40

    def test_insufficient_for_negative(self):
        mock_conn, mock_cursor = make_mock_db(fetchone_results=[(5,)])
        with _patches(mock_conn)[0] as mock_db, _patches(mock_conn)[1] as mock_tid:
            result = inventory_tool.update_stock(
                Args(sku="SKU001", quantity_delta="-10")
            )

        assert result["success"] is False
        assert "库存不足" in result["error"]

    def test_not_found(self):
        mock_conn, mock_cursor = make_mock_db(fetchone_results=[None])
        with _patches(mock_conn)[0] as mock_db, _patches(mock_conn)[1] as mock_tid:
            result = inventory_tool.update_stock(
                Args(sku="SKU_MISSING", quantity_delta="5")
            )

        assert result["success"] is False
        assert "不存在" in result["error"]

    def test_delta_to_exact_zero(self):
        mock_conn, mock_cursor = make_mock_db(fetchone_results=[(10,)])
        with _patches(mock_conn)[0] as mock_db, _patches(mock_conn)[1] as mock_tid:
            result = inventory_tool.update_stock(
                Args(sku="SKU001", quantity_delta="-10")
            )

        assert result["success"] is True
        assert result["new_total"] == 0


# ============================================================
# 10. sync_from_external
# ============================================================

class TestSyncFromExternal:

    def test_success_with_multiple_items(self):
        items = [
            {"product_sku": "SKU001", "product_name": "Product A", "quantity_total": 100},
            {"product_sku": "SKU002", "product_name": "Product B", "quantity_total": 200},
        ]

        mock_conn, mock_cursor = make_mock_db()
        with _patches(mock_conn)[0] as mock_db, _patches(mock_conn)[1] as mock_tid:
            result = inventory_tool.sync_from_external(
                Args(items=json.dumps(items))
            )

        assert result["success"] is True
        assert result["synced_count"] == 2
        assert result["total_items"] == 2
        # 验证为每个 item 执行了 UPSERT
        upsert_calls = [
            c for c in mock_cursor.execute.call_args_list
            if "INSERT INTO bs_order_processing_inventory" in c[0][0]
        ]
        assert len(upsert_calls) == 2

    def test_invalid_json(self):
        mock_conn, _ = make_mock_db()
        with _patches(mock_conn)[0] as mock_db, _patches(mock_conn)[1] as mock_tid:
            result = inventory_tool.sync_from_external(
                Args(items="not valid json{{{")
            )

        assert result["success"] is False
        assert "JSON" in result["error"]

    def test_empty_items_array(self):
        mock_conn, _ = make_mock_db()
        with _patches(mock_conn)[0] as mock_db, _patches(mock_conn)[1] as mock_tid:
            result = inventory_tool.sync_from_external(
                Args(items="[]")
            )

        assert result["success"] is False
        assert "非空数组" in result["error"]

    def test_item_missing_sku(self):
        items = [
            {"product_name": "No SKU Product", "quantity_total": 50},
        ]

        mock_conn, mock_cursor = make_mock_db()
        with _patches(mock_conn)[0] as mock_db, _patches(mock_conn)[1] as mock_tid:
            result = inventory_tool.sync_from_external(
                Args(items=json.dumps(items))
            )

        assert result["success"] is True
        assert result["synced_count"] == 0
        assert len(result["errors"]) == 1
        assert result["errors"][0]["error"] == "缺少 product_sku"

    def test_partial_failure(self):
        items = [
            {"product_sku": "SKU001", "product_name": "Good", "quantity_total": 10},
            {"product_name": "No SKU", "quantity_total": 20},
            {"product_sku": "SKU002", "product_name": "Also Good", "quantity_total": 30},
        ]

        mock_conn, mock_cursor = make_mock_db()
        with _patches(mock_conn)[0] as mock_db, _patches(mock_conn)[1] as mock_tid:
            result = inventory_tool.sync_from_external(
                Args(items=json.dumps(items))
            )

        assert result["success"] is True
        assert result["synced_count"] == 2
        assert result["total_items"] == 3
        assert len(result["errors"]) == 1


# ============================================================
# 11. list_low_stock
# ============================================================

class TestListLowStock:

    def test_success(self):
        # fetchone: count; fetchall: list of 6-field tuples
        count_row = (2,)
        low_stock_rows = [
            ("SKU001", "Product A", 15, 10, 5, 10),
            ("SKU002", "Product B", 8, 5, 3, 10),
        ]

        mock_conn, mock_cursor = make_mock_db(
            fetchone_results=[count_row],
            fetchall_results=[low_stock_rows],
        )
        with _patches(mock_conn)[0] as mock_db, _patches(mock_conn)[1] as mock_tid:
            result = inventory_tool.list_low_stock(Args(page=1, page_size=20))

        assert result["success"] is True
        assert result["total"] == 2
        assert len(result["items"]) == 2
        assert result["items"][0]["product_sku"] == "SKU001"
        assert result["items"][0]["quantity_available"] == 5
        assert result["items"][0]["shortage"] == 5  # 10 - 5

    def test_empty(self):
        mock_conn, mock_cursor = make_mock_db(
            fetchone_results=[(0,)],
            fetchall_results=[[]],
        )
        with _patches(mock_conn)[0] as mock_db, _patches(mock_conn)[1] as mock_tid:
            result = inventory_tool.list_low_stock(Args())

        assert result["success"] is True
        assert result["total"] == 0
        assert result["items"] == []

    def test_pagination(self):
        count_row = (25,)
        # Only return 10 items for page 2 (page_size=10)
        page_rows = [
            ("SKU011", f"Product {i}", 5, 0, 5, 10)
            for i in range(10)
        ]

        mock_conn, mock_cursor = make_mock_db(
            fetchone_results=[count_row],
            fetchall_results=[page_rows],
        )
        with _patches(mock_conn)[0] as mock_db, _patches(mock_conn)[1] as mock_tid:
            result = inventory_tool.list_low_stock(Args(page=2, page_size=10))

        assert result["success"] is True
        assert result["page"] == 2
        assert result["page_size"] == 10
        assert result["total"] == 25
        assert len(result["items"]) == 10

        # 验证分页 SQL 使用了正确的 OFFSET
        pagination_sql = mock_cursor.execute.call_args_list[1][0][0]
        assert "LIMIT %s OFFSET %s" in pagination_sql


# ============================================================
# 12. _generate_id
# ============================================================

class TestGenerateId:

    def test_default_prefix(self):
        result = inventory_tool._generate_id()
        assert result.startswith("prod_")

    def test_custom_prefix(self):
        result = inventory_tool._generate_id("rsv")
        assert result.startswith("rsv_")

    def test_uniqueness(self):
        ids = {inventory_tool._generate_id() for _ in range(100)}
        assert len(ids) == 100
