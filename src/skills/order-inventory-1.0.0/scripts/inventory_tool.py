"""
订单处理库存管理 CLI 工具。

提供商品管理、库存查询、库存预留/提交/释放等命令。
所有库存数据操作都通过本工具完成。

用法:
    python inventory_tool.py <command> [options]

命令:
    create-product        创建商品
    get-product           查询商品详情
    list-products         查询商品列表
    update-product        更新商品信息
    check-stock           查询库存
    reserve-stock         预留库存
    commit-reservation    提交预留
    release-reservation   释放预留
    update-stock          手动调整库存
    sync-from-external    外部库存同步
    list-low-stock        低库存预警
"""

import sys
import os
import json
import argparse
import uuid
from datetime import datetime, timedelta

# 添加项目根目录到 Python 路径
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from loguru import logger


def get_db_connection():
    """获取数据库连接（返回上下文管理器）"""
    from src.db.database import (
        get_db_connection as _get_db,
        get_postgres_pool,
        init_postgres_pool,
    )
    if get_postgres_pool() is None:
        logger.info("[inventory_tool] 子进程中 PostgreSQL 连接池未初始化，正在自动初始化")
        init_postgres_pool()
    return _get_db()


# CursorWrapper 的 cursor() 无参时返回 RealDictCursor，
# 需要转为普通 tuple cursor 以支持数字索引（与 order_tool.py 一致）
def _get_cursor(conn):
    """获取普通 tuple cursor（支持 row[0] 数字索引）"""
    return conn._conn.cursor()


def _get_current_tenant_id():
    """获取当前租户ID"""
    try:
        from src.saas.context import get_current_tenant_id
        tid = get_current_tenant_id()
        if tid:
            return tid
    except Exception:
        pass
    return os.environ.get("CURRENT_TENANT_ID")


def _generate_id(prefix="prod"):
    """生成唯一ID"""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# ============================================================
# 表初始化
# ============================================================

def init_tables():
    """初始化库存管理相关表"""
    try:
        with get_db_connection() as conn:
            cursor = _get_cursor(conn)

            # 商品信息表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS bs_order_processing_products (
                    id SERIAL PRIMARY KEY,
                    product_id TEXT UNIQUE NOT NULL,
                    tenant_id TEXT,
                    user_id TEXT,
                    product_sku TEXT NOT NULL,
                    product_name TEXT NOT NULL,
                    category TEXT,
                    brand TEXT,
                    model TEXT,
                    specifications TEXT,
                    unit TEXT DEFAULT '个',
                    cost_price NUMERIC(10,2),
                    selling_price NUMERIC(10,2),
                    description TEXT,
                    images TEXT,
                    tags TEXT,
                    status TEXT DEFAULT 'active',
                    weight NUMERIC(8,2),
                    barcode TEXT,
                    external_product_id TEXT,
                    supplier TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(tenant_id, product_sku)
                )
            """)

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_products_tenant_category ON bs_order_processing_products (tenant_id, category)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_products_tenant_sku ON bs_order_processing_products (tenant_id, product_sku)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_products_tenant_status ON bs_order_processing_products (tenant_id, status)")

            # 库存快照表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS bs_order_processing_inventory (
                    id SERIAL PRIMARY KEY,
                    tenant_id TEXT,
                    user_id TEXT,
                    product_sku TEXT NOT NULL,
                    product_name TEXT,
                    quantity_total INTEGER NOT NULL DEFAULT 0,
                    quantity_reserved INTEGER NOT NULL DEFAULT 0,
                    quantity_available INTEGER NOT NULL DEFAULT 0,
                    low_stock_threshold INTEGER DEFAULT 10,
                    external_product_id TEXT,
                    last_synced_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(tenant_id, product_sku)
                )
            """)

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_inventory_tenant_sku ON bs_order_processing_inventory (tenant_id, product_sku)")

            # 库存预留表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS bs_order_processing_inventory_reservations (
                    id SERIAL PRIMARY KEY,
                    reservation_id TEXT UNIQUE NOT NULL,
                    tenant_id TEXT,
                    user_id TEXT,
                    order_id TEXT NOT NULL,
                    product_sku TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    expires_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_reservations_tenant_order ON bs_order_processing_inventory_reservations (tenant_id, order_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_reservations_status_expires ON bs_order_processing_inventory_reservations (status, expires_at)")

            conn.commit()
            logger.info("[inventory_tool] 库存管理表初始化完成")
    except Exception as e:
        logger.error(f"[inventory_tool] 表初始化失败: {e}", exc_info=True)
        raise


# ============================================================
# 创建商品
# ============================================================

def create_product(args):
    """创建商品，同时自动创建库存记录（初始数量为 0）"""
    try:
        product_id = _generate_id("prod")
        tenant_id = getattr(args, "tenant_id", None) or _get_current_tenant_id()
        now = datetime.now()

        with get_db_connection() as conn:
            cursor = _get_cursor(conn)

            # 插入商品记录
            cursor.execute("""
                INSERT INTO bs_order_processing_products
                (product_id, tenant_id, user_id, product_sku, product_name, category, brand, model,
                 specifications, unit, cost_price, selling_price, description, images, tags,
                 status, weight, barcode, external_product_id, supplier, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                product_id, tenant_id, None, args.product_sku, args.product_name,
                getattr(args, "category", None),
                getattr(args, "brand", None),
                getattr(args, "model", None),
                getattr(args, "specifications", None),
                getattr(args, "unit", "个"),
                getattr(args, "cost_price", None),
                getattr(args, "selling_price", None),
                getattr(args, "description", None),
                getattr(args, "images", None),
                getattr(args, "tags", None),
                getattr(args, "status", "active"),
                getattr(args, "weight", None),
                getattr(args, "barcode", None),
                getattr(args, "external_product_id", None),
                getattr(args, "supplier", None),
                now, now,
            ))

            # 自动创建库存记录（初始数量为 0）
            cursor.execute("""
                INSERT INTO bs_order_processing_inventory
                (tenant_id, user_id, product_sku, product_name, quantity_total, quantity_reserved,
                 quantity_available, low_stock_threshold, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                tenant_id, None, args.product_sku, args.product_name,
                0, 0, 0, 10, now, now,
            ))

            conn.commit()

        return {
            "success": True,
            "product_id": product_id,
            "product_sku": args.product_sku,
            "product_name": args.product_name,
            "inventory_initialized": True,
            "created_at": now.isoformat(),
        }
    except Exception as e:
        logger.error(f"创建商品失败: {e}", exc_info=True)
        return {"success": False, "error": f"创建商品失败: {str(e)}"}


# ============================================================
# 查询商品详情
# ============================================================

def get_product(args):
    """查询商品详情及库存状态"""
    try:
        with get_db_connection() as conn:
            cursor = _get_cursor(conn)

            # 查询商品信息
            cursor.execute("""
                SELECT product_id, tenant_id, product_sku, product_name, category, brand, model,
                       specifications, unit, cost_price, selling_price, description, images, tags,
                       status, weight, barcode, external_product_id, supplier,
                       created_at, updated_at
                FROM bs_order_processing_products
                WHERE product_id = %s
            """, (args.product_id,))

            row = cursor.fetchone()
            if not row:
                return {"success": False, "error": f"商品不存在: {args.product_id}"}

            product = {
                "product_id": row[0],
                "tenant_id": row[1],
                "product_sku": row[2],
                "product_name": row[3],
                "category": row[4],
                "brand": row[5],
                "model": row[6],
                "specifications": row[7],
                "unit": row[8],
                "cost_price": float(row[9]) if row[9] else None,
                "selling_price": float(row[10]) if row[10] else None,
                "description": row[11],
                "images": row[12],
                "tags": row[13],
                "status": row[14],
                "weight": float(row[15]) if row[15] else None,
                "barcode": row[16],
                "external_product_id": row[17],
                "supplier": row[18],
                "created_at": row[19].isoformat() if row[19] else None,
                "updated_at": row[20].isoformat() if row[20] else None,
            }

            # 查询库存状态
            inventory = None
            cursor.execute("""
                SELECT quantity_total, quantity_reserved, quantity_available,
                       low_stock_threshold, last_synced_at
                FROM bs_order_processing_inventory
                WHERE product_sku = %s
            """, (product["product_sku"],))
            inv_row = cursor.fetchone()
            if inv_row:
                inventory = {
                    "quantity_total": inv_row[0],
                    "quantity_reserved": inv_row[1],
                    "quantity_available": inv_row[2],
                    "low_stock_threshold": inv_row[3],
                    "last_synced_at": inv_row[4].isoformat() if inv_row[4] else None,
                    "is_low_stock": inv_row[2] < inv_row[3],
                }

        return {
            "success": True,
            "product": product,
            "inventory": inventory,
        }
    except Exception as e:
        logger.error(f"查询商品详情失败: {e}", exc_info=True)
        return {"success": False, "error": f"查询商品详情失败: {str(e)}"}


# ============================================================
# 查询商品列表
# ============================================================

def list_products(args):
    """查询商品列表"""
    try:
        with get_db_connection() as conn:
            cursor = _get_cursor(conn)

            conditions = []
            params = []

            tenant_id = _get_current_tenant_id()
            if tenant_id:
                conditions.append("tenant_id = %s")
                params.append(tenant_id)

            if getattr(args, "category", None):
                conditions.append("category = %s")
                params.append(args.category)

            if getattr(args, "status", None):
                conditions.append("status = %s")
                params.append(args.status)

            if getattr(args, "keyword", None):
                conditions.append("(product_name ILIKE %s OR product_sku ILIKE %s)")
                keyword = f"%{args.keyword}%"
                params.extend([keyword, keyword])

            where_clause = " AND ".join(conditions) if conditions else "1=1"

            page = int(getattr(args, "page", 1) or 1)
            page_size = int(getattr(args, "page_size", 20) or 20)
            offset = (page - 1) * page_size

            # 总数统计
            cursor.execute(f"""
                SELECT COUNT(*)
                FROM bs_order_processing_products
                WHERE {where_clause}
            """, params)
            total = cursor.fetchone()[0]

            # 分页查询
            cursor.execute(f"""
                SELECT product_id, product_sku, product_name, category, brand,
                       selling_price, status, created_at, updated_at
                FROM bs_order_processing_products
                WHERE {where_clause}
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
            """, params + [page_size, offset])

            rows = cursor.fetchall()
            products = []
            for row in rows:
                products.append({
                    "product_id": row[0],
                    "product_sku": row[1],
                    "product_name": row[2],
                    "category": row[3],
                    "brand": row[4],
                    "selling_price": float(row[5]) if row[5] else None,
                    "status": row[6],
                    "created_at": row[7].isoformat() if row[7] else None,
                    "updated_at": row[8].isoformat() if row[8] else None,
                })

        return {
            "success": True,
            "products": products,
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    except Exception as e:
        logger.error(f"查询商品列表失败: {e}", exc_info=True)
        return {"success": False, "error": f"查询商品列表失败: {str(e)}"}


# ============================================================
# 更新商品信息
# ============================================================

def update_product(args):
    """更新商品信息"""
    try:
        with get_db_connection() as conn:
            cursor = _get_cursor(conn)
            now = datetime.now()

            # 检查商品是否存在
            cursor.execute("""
                SELECT product_id FROM bs_order_processing_products
                WHERE product_id = %s
            """, (args.product_id,))
            if not cursor.fetchone():
                return {"success": False, "error": f"商品不存在: {args.product_id}"}

            updates = ["updated_at = %s"]
            params = [now]

            optional_fields = [
                ("product_name", "product_name"),
                ("category", "category"),
                ("brand", "brand"),
                ("model", "model"),
                ("specifications", "specifications"),
                ("unit", "unit"),
                ("cost_price", "cost_price"),
                ("selling_price", "selling_price"),
                ("description", "description"),
                ("images", "images"),
                ("tags", "tags"),
                ("status", "status"),
                ("weight", "weight"),
                ("barcode", "barcode"),
                ("external_product_id", "external_product_id"),
                ("supplier", "supplier"),
            ]
            for attr, col in optional_fields:
                val = getattr(args, attr, None)
                if val is not None:
                    updates.append(f"{col} = %s")
                    params.append(val)

            params.append(args.product_id)

            cursor.execute(
                f"UPDATE bs_order_processing_products SET {', '.join(updates)} WHERE product_id = %s",
                params
            )

            if cursor.rowcount == 0:
                return {"success": False, "error": f"商品不存在: {args.product_id}"}

            conn.commit()

        return {
            "success": True,
            "product_id": args.product_id,
            "updated_at": now.isoformat(),
        }
    except Exception as e:
        logger.error(f"更新商品失败: {e}", exc_info=True)
        return {"success": False, "error": f"更新商品失败: {str(e)}"}


# ============================================================
# 查询库存
# ============================================================

def check_stock(args):
    """按 SKU 查询库存状态"""
    try:
        with get_db_connection() as conn:
            cursor = _get_cursor(conn)

            conditions = ["product_sku = %s"]
            params = [args.sku]

            tenant_id = _get_current_tenant_id()
            if tenant_id:
                conditions.append("tenant_id = %s")
                params.append(tenant_id)

            where_clause = " AND ".join(conditions)

            cursor.execute(f"""
                SELECT product_sku, product_name, quantity_total, quantity_reserved,
                       quantity_available, low_stock_threshold, last_synced_at
                FROM bs_order_processing_inventory
                WHERE {where_clause}
            """, params)

            row = cursor.fetchone()
            if not row:
                return {"success": False, "error": f"库存记录不存在: SKU {args.sku}"}

            quantity_total = row[2]
            quantity_reserved = row[3]
            low_stock_threshold = row[5]
            quantity_available = quantity_total - quantity_reserved

        return {
            "success": True,
            "product_sku": row[0],
            "product_name": row[1],
            "quantity_total": quantity_total,
            "quantity_reserved": quantity_reserved,
            "quantity_available": quantity_available,
            "low_stock_threshold": low_stock_threshold,
            "is_low_stock": quantity_available < low_stock_threshold,
            "last_synced_at": row[6].isoformat() if row[6] else None,
        }
    except Exception as e:
        logger.error(f"查询库存失败: {e}", exc_info=True)
        return {"success": False, "error": f"查询库存失败: {str(e)}"}


# ============================================================
# 预留库存
# ============================================================

def reserve_stock(args):
    """预留库存（SELECT FOR UPDATE 防超卖）"""
    try:
        reservation_id = _generate_id("rsv")
        tenant_id = _get_current_tenant_id()
        now = datetime.now()
        quantity = int(args.quantity)
        expires_minutes = int(getattr(args, "expires_minutes", 60) or 60)
        expires_at = now + timedelta(minutes=expires_minutes)

        with get_db_connection() as conn:
            cursor = _get_cursor(conn)

            # 锁定库存行，防止超卖
            conditions = ["product_sku = %s"]
            params = [args.sku]
            if tenant_id:
                conditions.append("tenant_id = %s")
                params.append(tenant_id)
            where_clause = " AND ".join(conditions)

            cursor.execute(f"""
                SELECT quantity_total, quantity_reserved
                FROM bs_order_processing_inventory
                WHERE {where_clause}
                FOR UPDATE
            """, params)

            row = cursor.fetchone()
            if not row:
                return {"success": False, "error": f"库存记录不存在: SKU {args.sku}"}

            quantity_total = row[0]
            quantity_reserved = row[1]
            quantity_available = quantity_total - quantity_reserved

            if quantity_available < quantity:
                return {
                    "success": False,
                    "error": "库存不足，无法预留",
                    "available": quantity_available,
                    "requested": quantity,
                }

            # 更新库存预留数量
            new_reserved = quantity_reserved + quantity
            new_available = quantity_total - new_reserved
            cursor.execute("""
                UPDATE bs_order_processing_inventory
                SET quantity_reserved = %s, quantity_available = %s, updated_at = %s
                WHERE product_sku = %s
            """, (new_reserved, new_available, now, args.sku))

            # 插入预留记录
            cursor.execute("""
                INSERT INTO bs_order_processing_inventory_reservations
                (reservation_id, tenant_id, user_id, order_id, product_sku, quantity,
                 status, expires_at, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                reservation_id, tenant_id, None, args.order_id, args.sku,
                quantity, "active", expires_at, now, now,
            ))

            conn.commit()

        return {
            "success": True,
            "reservation_id": reservation_id,
            "order_id": args.order_id,
            "product_sku": args.sku,
            "quantity": quantity,
            "quantity_available_after_reserve": new_available,
            "expires_at": expires_at.isoformat(),
            "created_at": now.isoformat(),
        }
    except Exception as e:
        logger.error(f"预留库存失败: {e}", exc_info=True)
        return {"success": False, "error": f"预留库存失败: {str(e)}"}


# ============================================================
# 提交预留
# ============================================================

def commit_reservation(args):
    """付款后提交预留，扣减实际库存"""
    try:
        with get_db_connection() as conn:
            cursor = _get_cursor(conn)
            now = datetime.now()

            # 查找预留记录
            cursor.execute("""
                SELECT reservation_id, product_sku, quantity, status
                FROM bs_order_processing_inventory_reservations
                WHERE reservation_id = %s
            """, (args.reservation_id,))

            row = cursor.fetchone()
            if not row:
                return {"success": False, "error": f"预留记录不存在: {args.reservation_id}"}

            reservation_id, product_sku, quantity, status = row[0], row[1], row[2], row[3]

            if status != "active":
                return {"success": False, "error": f"预留记录状态不是 active，当前状态: {status}"}

            # 扣减库存总量
            cursor.execute("""
                UPDATE bs_order_processing_inventory
                SET quantity_total = quantity_total - %s,
                    quantity_reserved = quantity_reserved - %s,
                    quantity_available = quantity_total - quantity_reserved - %s + %s,
                    updated_at = %s
                WHERE product_sku = %s
            """, (quantity, quantity, quantity, quantity, now, product_sku))

            # 实际上 quantity_available = (quantity_total - quantity) - (quantity_reserved - quantity)
            # 简化：重新计算
            cursor.execute("""
                UPDATE bs_order_processing_inventory
                SET quantity_available = quantity_total - quantity_reserved,
                    updated_at = %s
                WHERE product_sku = %s
            """, (now, product_sku))

            # 更新预留状态
            cursor.execute("""
                UPDATE bs_order_processing_inventory_reservations
                SET status = %s, updated_at = %s
                WHERE reservation_id = %s
            """, ("committed", now, args.reservation_id))

            conn.commit()

        return {
            "success": True,
            "reservation_id": args.reservation_id,
            "product_sku": product_sku,
            "quantity_committed": quantity,
            "status": "committed",
            "updated_at": now.isoformat(),
        }
    except Exception as e:
        logger.error(f"提交预留失败: {e}", exc_info=True)
        return {"success": False, "error": f"提交预留失败: {str(e)}"}


# ============================================================
# 释放预留
# ============================================================

def release_reservation(args):
    """取消或超时时释放预留"""
    try:
        with get_db_connection() as conn:
            cursor = _get_cursor(conn)
            now = datetime.now()

            # 查找预留记录
            cursor.execute("""
                SELECT reservation_id, product_sku, quantity, status
                FROM bs_order_processing_inventory_reservations
                WHERE reservation_id = %s
            """, (args.reservation_id,))

            row = cursor.fetchone()
            if not row:
                return {"success": False, "error": f"预留记录不存在: {args.reservation_id}"}

            reservation_id, product_sku, quantity, status = row[0], row[1], row[2], row[3]

            if status != "active":
                return {"success": False, "error": f"预留记录状态不是 active，当前状态: {status}"}

            # 归还预留数量
            cursor.execute("""
                UPDATE bs_order_processing_inventory
                SET quantity_reserved = quantity_reserved - %s,
                    quantity_available = quantity_total - (quantity_reserved - %s),
                    updated_at = %s
                WHERE product_sku = %s
            """, (quantity, quantity, now, product_sku))

            # 更新预留状态
            cursor.execute("""
                UPDATE bs_order_processing_inventory_reservations
                SET status = %s, updated_at = %s
                WHERE reservation_id = %s
            """, ("released", now, args.reservation_id))

            conn.commit()

        return {
            "success": True,
            "reservation_id": args.reservation_id,
            "product_sku": product_sku,
            "quantity_released": quantity,
            "status": "released",
            "updated_at": now.isoformat(),
        }
    except Exception as e:
        logger.error(f"释放预留失败: {e}", exc_info=True)
        return {"success": False, "error": f"释放预留失败: {str(e)}"}


# ============================================================
# 手动调整库存
# ============================================================

def update_stock(args):
    """手动增减库存"""
    try:
        with get_db_connection() as conn:
            cursor = _get_cursor(conn)
            now = datetime.now()

            quantity_delta = int(args.quantity_delta)
            tenant_id = _get_current_tenant_id()

            # 检查库存记录是否存在
            conditions = ["product_sku = %s"]
            params = [args.sku]
            if tenant_id:
                conditions.append("tenant_id = %s")
                params.append(tenant_id)
            where_clause = " AND ".join(conditions)

            cursor.execute(f"""
                SELECT quantity_total FROM bs_order_processing_inventory
                WHERE {where_clause}
            """, params)

            row = cursor.fetchone()
            if not row:
                return {"success": False, "error": f"库存记录不存在: SKU {args.sku}"}

            current_total = row[0]
            new_total = current_total + quantity_delta

            if new_total < 0:
                return {
                    "success": False,
                    "error": f"库存不足，当前库存 {current_total}，尝试减少 {-quantity_delta}",
                }

            # 更新库存
            cursor.execute("""
                UPDATE bs_order_processing_inventory
                SET quantity_total = %s,
                    quantity_available = %s - quantity_reserved,
                    updated_at = %s
                WHERE product_sku = %s
            """, (new_total, new_total, now, args.sku))

            conn.commit()

        return {
            "success": True,
            "product_sku": args.sku,
            "previous_total": current_total,
            "quantity_delta": quantity_delta,
            "new_total": new_total,
            "reason": getattr(args, "reason", None),
            "updated_at": now.isoformat(),
        }
    except Exception as e:
        logger.error(f"调整库存失败: {e}", exc_info=True)
        return {"success": False, "error": f"调整库存失败: {str(e)}"}


# ============================================================
# 外部库存同步
# ============================================================

def sync_from_external(args):
    """从外部 ERP 同步库存快照"""
    try:
        # 解析 JSON 数据
        try:
            items = json.loads(args.items)
        except (json.JSONDecodeError, TypeError):
            return {"success": False, "error": "items 参数不是有效的 JSON 字符串"}

        if not items or not isinstance(items, list):
            return {"success": False, "error": "items 必须是非空数组"}

        tenant_id = _get_current_tenant_id()
        now = datetime.now()
        synced_count = 0
        errors = []

        with get_db_connection() as conn:
            cursor = _get_cursor(conn)

            for item in items:
                sku = item.get("product_sku")
                qty = item.get("quantity_total", 0)
                name = item.get("product_name", "")

                if not sku:
                    errors.append({"product_sku": None, "error": "缺少 product_sku"})
                    continue

                try:
                    # UPSERT：存在则更新，不存在则插入
                    cursor.execute("""
                        INSERT INTO bs_order_processing_inventory
                        (tenant_id, product_sku, product_name, quantity_total,
                         quantity_reserved, quantity_available, last_synced_at, user_id, created_at, updated_at)
                        VALUES (%s, %s, %s, %s, 0, %s, %s, %s, %s, %s)
                        ON CONFLICT (tenant_id, product_sku)
                        DO UPDATE SET
                            quantity_total = EXCLUDED.quantity_total,
                            quantity_available = EXCLUDED.quantity_total - bs_order_processing_inventory.quantity_reserved,
                            product_name = COALESCE(EXCLUDED.product_name, bs_order_processing_inventory.product_name),
                            last_synced_at = EXCLUDED.last_synced_at,
                            updated_at = EXCLUDED.updated_at
                    """, (
                        tenant_id, sku, name, qty,
                        qty, now, None, now, now,
                    ))
                    synced_count += 1
                except Exception as item_err:
                    errors.append({"product_sku": sku, "error": str(item_err)})

            conn.commit()

        result = {
            "success": True,
            "synced_count": synced_count,
            "total_items": len(items),
            "synced_at": now.isoformat(),
        }
        if errors:
            result["errors"] = errors

        return result
    except Exception as e:
        logger.error(f"外部库存同步失败: {e}", exc_info=True)
        return {"success": False, "error": f"外部库存同步失败: {str(e)}"}


# ============================================================
# 低库存预警
# ============================================================

def list_low_stock(args):
    """查询库存低于阈值的商品"""
    try:
        with get_db_connection() as conn:
            cursor = _get_cursor(conn)

            conditions = ["quantity_available < low_stock_threshold"]
            params = []

            tenant_id = _get_current_tenant_id()
            if tenant_id:
                conditions.append("tenant_id = %s")
                params.append(tenant_id)

            where_clause = " AND ".join(conditions)

            page = int(getattr(args, "page", 1) or 1)
            page_size = int(getattr(args, "page_size", 20) or 20)
            offset = (page - 1) * page_size

            # 总数统计
            cursor.execute(f"""
                SELECT COUNT(*)
                FROM bs_order_processing_inventory
                WHERE {where_clause}
            """, params)
            total = cursor.fetchone()[0]

            # 分页查询
            cursor.execute(f"""
                SELECT product_sku, product_name, quantity_total, quantity_reserved,
                       quantity_available, low_stock_threshold
                FROM bs_order_processing_inventory
                WHERE {where_clause}
                ORDER BY quantity_available ASC
                LIMIT %s OFFSET %s
            """, params + [page_size, offset])

            rows = cursor.fetchall()
            items = []
            for row in rows:
                items.append({
                    "product_sku": row[0],
                    "product_name": row[1],
                    "quantity_total": row[2],
                    "quantity_reserved": row[3],
                    "quantity_available": row[4],
                    "low_stock_threshold": row[5],
                    "shortage": row[5] - row[4],
                })

        return {
            "success": True,
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    except Exception as e:
        logger.error(f"查询低库存预警失败: {e}", exc_info=True)
        return {"success": False, "error": f"查询低库存预警失败: {str(e)}"}


# ============================================================
# 主入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="订单处理库存管理工具")
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # create-product
    p = subparsers.add_parser("create-product", help="创建商品")
    p.add_argument("--product-sku", required=True, help="商品 SKU")
    p.add_argument("--product-name", required=True, help="商品名称")
    p.add_argument("--category", default=None, help="商品分类")
    p.add_argument("--brand", default=None, help="品牌")
    p.add_argument("--model", default=None, help="型号")
    p.add_argument("--specifications", default=None, help="规格参数 JSON")
    p.add_argument("--unit", default="个", help="计量单位")
    p.add_argument("--cost-price", default=None, help="成本价")
    p.add_argument("--selling-price", default=None, help="销售价")
    p.add_argument("--description", default=None, help="商品描述")
    p.add_argument("--tags", default=None, help="标签")
    p.add_argument("--status", default="active", help="商品状态")
    p.add_argument("--weight", default=None, help="重量(kg)")
    p.add_argument("--barcode", default=None, help="条形码")
    p.add_argument("--external-product-id", default=None, help="外部系统商品 ID")
    p.add_argument("--supplier", default=None, help="供应商")
    p.add_argument("--tenant-id", default=None, help="租户ID")

    # get-product
    p = subparsers.add_parser("get-product", help="查询商品详情")
    p.add_argument("--product-id", required=True, help="商品ID")

    # list-products
    p = subparsers.add_parser("list-products", help="查询商品列表")
    p.add_argument("--category", default=None, help="按分类过滤")
    p.add_argument("--status", default=None, help="按状态过滤")
    p.add_argument("--keyword", default=None, help="搜索关键词")
    p.add_argument("--page", default=1, help="页码")
    p.add_argument("--page-size", default=20, help="每页数量")

    # update-product
    p = subparsers.add_parser("update-product", help="更新商品信息")
    p.add_argument("--product-id", required=True, help="商品ID")
    p.add_argument("--product-name", default=None, help="商品名称")
    p.add_argument("--category", default=None, help="商品分类")
    p.add_argument("--brand", default=None, help="品牌")
    p.add_argument("--model", default=None, help="型号")
    p.add_argument("--specifications", default=None, help="规格参数 JSON")
    p.add_argument("--unit", default=None, help="计量单位")
    p.add_argument("--cost-price", default=None, help="成本价")
    p.add_argument("--selling-price", default=None, help="销售价")
    p.add_argument("--description", default=None, help="商品描述")
    p.add_argument("--images", default=None, help="图片")
    p.add_argument("--tags", default=None, help="标签")
    p.add_argument("--status", default=None, help="商品状态")
    p.add_argument("--weight", default=None, help="重量(kg)")
    p.add_argument("--barcode", default=None, help="条形码")
    p.add_argument("--external-product-id", default=None, help="外部系统商品 ID")
    p.add_argument("--supplier", default=None, help="供应商")

    # check-stock
    p = subparsers.add_parser("check-stock", help="查询库存")
    p.add_argument("--sku", required=True, help="商品 SKU")

    # reserve-stock
    p = subparsers.add_parser("reserve-stock", help="预留库存")
    p.add_argument("--order-id", required=True, help="订单ID")
    p.add_argument("--sku", required=True, help="商品 SKU")
    p.add_argument("--quantity", required=True, help="预留数量")
    p.add_argument("--expires-minutes", default=60, help="过期时间(分钟)")

    # commit-reservation
    p = subparsers.add_parser("commit-reservation", help="提交预留")
    p.add_argument("--reservation-id", required=True, help="预留ID")

    # release-reservation
    p = subparsers.add_parser("release-reservation", help="释放预留")
    p.add_argument("--reservation-id", required=True, help="预留ID")

    # update-stock
    p = subparsers.add_parser("update-stock", help="手动调整库存")
    p.add_argument("--sku", required=True, help="商品 SKU")
    p.add_argument("--quantity-delta", required=True, help="变更数量（正增负减）")
    p.add_argument("--reason", default=None, help="调整原因")

    # sync-from-external
    p = subparsers.add_parser("sync-from-external", help="外部库存同步")
    p.add_argument("--items", required=True, help="库存数据 JSON 数组")

    # list-low-stock
    p = subparsers.add_parser("list-low-stock", help="低库存预警")
    p.add_argument("--page", default=1, help="页码")
    p.add_argument("--page-size", default=20, help="每页数量")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    init_tables()

    commands = {
        "create-product": create_product,
        "get-product": get_product,
        "list-products": list_products,
        "update-product": update_product,
        "check-stock": check_stock,
        "reserve-stock": reserve_stock,
        "commit-reservation": commit_reservation,
        "release-reservation": release_reservation,
        "update-stock": update_stock,
        "sync-from-external": sync_from_external,
        "list-low-stock": list_low_stock,
    }

    handler = commands.get(args.command)
    if handler:
        result = handler(args)
    else:
        result = {"success": False, "error": f"未知命令: {args.command}"}

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
