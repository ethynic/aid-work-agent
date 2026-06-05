"""
订单处理核心 CLI 工具。

提供订单创建、查询、状态流转、审批等命令。
所有订单数据操作都通过本工具完成。

用法:
    python order_tool.py <command> [options]

命令:
    create-order        创建订单
    get-order           查询订单详情
    list-orders         查询订单列表
    update-order        更新订单信息
    cancel-order        取消订单
    change-status       变更订单状态
    create-approval     创建审批记录
    approve-order       审批通过
    reject-order        审批拒绝
    get-status-history  查询状态变更历史
    stats               订单统计
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


# ============================================================
# 状态流转定义
# ============================================================

STATUS_TRANSITIONS = {
    "draft": ["pending_approval", "cancelled"],
    "pending_approval": ["approved", "rejected", "cancelled"],
    "rejected": ["draft", "cancelled"],
    "approved": ["processing", "cancelled"],
    "processing": ["shipped", "cancelled"],
    "shipped": ["delivered", "cancelled"],
    "delivered": ["completed", "return_requested"],
    "completed": ["return_requested"],
    "return_requested": ["returned"],
    "returned": [],
    "cancelled": [],
}


def get_db_connection():
    """获取数据库连接（返回上下文管理器）"""
    from src.db.database import (
        get_db_connection as _get_db,
        get_postgres_pool,
        init_postgres_pool,
    )
    if get_postgres_pool() is None:
        logger.info("[order_tool] 子进程中 PostgreSQL 连接池未初始化，正在自动初始化")
        init_postgres_pool()
    return _get_db()


# CursorWrapper 的 cursor() 无参时返回 RealDictCursor，
# 需要转为普通 tuple cursor 以支持数字索引（与 complaint_tool.py 一致）
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


def _generate_id(prefix="ord"):
    """生成唯一ID"""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# ============================================================
# 表初始化
# ============================================================

def init_tables():
    """初始化订单处理相关表"""
    try:
        with get_db_connection() as conn:
            cursor = _get_cursor(conn)

            # 订单主表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS bs_order_processing_orders (
                    id SERIAL PRIMARY KEY,
                    order_id TEXT UNIQUE NOT NULL,
                    tenant_id TEXT,
                    user_id TEXT NOT NULL,
                    session_id TEXT,
                    customer_name TEXT,
                    customer_phone TEXT,
                    customer_address TEXT,
                    status TEXT NOT NULL DEFAULT 'draft',
                    total_amount NUMERIC(12,2),
                    currency TEXT DEFAULT 'CNY',
                    discount_amount NUMERIC(12,2) DEFAULT 0,
                    shipping_fee NUMERIC(10,2) DEFAULT 0,
                    final_amount NUMERIC(12,2),
                    approval_status TEXT,
                    approved_by TEXT,
                    approved_at TIMESTAMP,
                    approval_note TEXT,
                    rejection_reason TEXT,
                    payment_status TEXT DEFAULT 'unpaid',
                    payment_method TEXT,
                    payment_transaction_id TEXT,
                    paid_at TIMESTAMP,
                    notes TEXT,
                    tags TEXT,
                    external_order_id TEXT,
                    source TEXT DEFAULT 'internal',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_tenant_status ON bs_order_processing_orders (tenant_id, status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_user_status ON bs_order_processing_orders (user_id, status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_order_id ON bs_order_processing_orders (order_id)")

            # 订单明细表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS bs_order_processing_order_items (
                    id SERIAL PRIMARY KEY,
                    tenant_id TEXT,
                    user_id TEXT,
                    order_id TEXT NOT NULL,
                    item_id TEXT UNIQUE NOT NULL,
                    product_sku TEXT,
                    product_name TEXT NOT NULL,
                    quantity INTEGER NOT NULL DEFAULT 1,
                    unit_price NUMERIC(10,2) NOT NULL,
                    discount_rate NUMERIC(5,4) DEFAULT 0,
                    subtotal NUMERIC(10,2) NOT NULL,
                    product_snapshot TEXT,
                    notes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_order_items_tenant_order ON bs_order_processing_order_items (tenant_id, order_id)")

            # 状态变更历史表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS bs_order_processing_status_history (
                    id SERIAL PRIMARY KEY,
                    tenant_id TEXT,
                    user_id TEXT,
                    order_id TEXT NOT NULL,
                    from_status TEXT,
                    to_status TEXT NOT NULL,
                    changed_by TEXT,
                    change_reason TEXT,
                    note TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_status_history_tenant_order ON bs_order_processing_status_history (tenant_id, order_id)")

            # 审批记录表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS bs_order_processing_approvals (
                    id SERIAL PRIMARY KEY,
                    approval_id TEXT UNIQUE NOT NULL,
                    tenant_id TEXT,
                    user_id TEXT,
                    order_id TEXT NOT NULL,
                    approval_type TEXT NOT NULL DEFAULT 'order_approval',
                    status TEXT NOT NULL DEFAULT 'pending',
                    requested_by TEXT NOT NULL,
                    assigned_to TEXT,
                    amount_threshold NUMERIC(12,2),
                    resolved_by TEXT,
                    resolved_at TIMESTAMP,
                    note TEXT,
                    rejection_reason TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_approvals_tenant_status ON bs_order_processing_approvals (tenant_id, status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_approvals_order ON bs_order_processing_approvals (order_id)")

            # Webhook 事件表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS bs_order_processing_webhook_events (
                    id SERIAL PRIMARY KEY,
                    event_id TEXT UNIQUE NOT NULL,
                    tenant_id TEXT,
                    user_id TEXT,
                    event_type TEXT NOT NULL,
                    source TEXT NOT NULL,
                    payload JSON NOT NULL,
                    processed BOOLEAN DEFAULT false,
                    processing_result TEXT,
                    error_message TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    processed_at TIMESTAMP
                )
            """)

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_webhook_tenant_type ON bs_order_processing_webhook_events (tenant_id, event_type)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_webhook_processed_received ON bs_order_processing_webhook_events (processed, created_at)")

            conn.commit()
            logger.info("[order_tool] 订单处理表初始化完成")
    except Exception as e:
        logger.error(f"[order_tool] 表初始化失败: {e}", exc_info=True)
        raise


# ============================================================
# 创建订单
# ============================================================

def create_order(args):
    """创建订单"""
    try:
        order_id = _generate_id("ord")
        tenant_id = getattr(args, "tenant_id", None) or _get_current_tenant_id()

        # 解析订单明细
        try:
            items = json.loads(args.items)
        except (json.JSONDecodeError, TypeError):
            return {"success": False, "error": "items 参数不是有效的 JSON 字符串"}

        if not items or not isinstance(items, list):
            return {"success": False, "error": "items 必须是非空数组"}

        # 计算总金额
        total_amount = sum(float(item.get("subtotal", 0)) for item in items)
        discount_amount = float(getattr(args, "discount_amount", 0) or 0)
        shipping_fee = float(getattr(args, "shipping_fee", 0) or 0)
        final_amount = total_amount - discount_amount + shipping_fee

        now = datetime.now()

        with get_db_connection() as conn:
            cursor = _get_cursor(conn)

            # 插入订单主记录
            cursor.execute("""
                INSERT INTO bs_order_processing_orders
                (order_id, tenant_id, user_id, session_id, customer_name, customer_phone,
                 customer_address, status, total_amount, currency, discount_amount,
                 shipping_fee, final_amount, notes, tags, external_order_id, source,
                 created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                order_id, tenant_id, args.user_id,
                getattr(args, "session_id", None),
                getattr(args, "customer_name", None),
                getattr(args, "customer_phone", None),
                getattr(args, "customer_address", None),
                "draft", total_amount,
                getattr(args, "currency", "CNY"),
                discount_amount, shipping_fee, final_amount,
                getattr(args, "notes", None),
                getattr(args, "tags", None),
                getattr(args, "external_order_id", None),
                getattr(args, "source", "internal"),
                now, now,
            ))

            # 插入订单明细
            for item in items:
                item_id = _generate_id("itm")
                cursor.execute("""
                    INSERT INTO bs_order_processing_order_items
                    (tenant_id, user_id, order_id, item_id, product_sku, product_name,
                     quantity, unit_price, discount_rate, subtotal,
                     product_snapshot, notes, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    tenant_id, args.user_id, order_id, item_id,
                    item.get("product_sku"),
                    item.get("product_name", ""),
                    int(item.get("quantity", 1)),
                    float(item.get("unit_price", 0)),
                    float(item.get("discount_rate", 0)),
                    float(item.get("subtotal", 0)),
                    item.get("product_snapshot"),
                    item.get("notes"),
                    now,
                ))

            # 插入初始状态历史
            cursor.execute("""
                INSERT INTO bs_order_processing_status_history
                (tenant_id, user_id, order_id, from_status, to_status, changed_by, change_reason, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (tenant_id, args.user_id, order_id, None, "draft", args.user_id, "订单创建", now))

            # 预留库存（尝试为每个商品的 SKU 预留库存）
            reservation_results = []
            skip_reserve = getattr(args, "skip_inventory", False)
            if not skip_reserve:
                from datetime import timedelta as _td
                expires_at = now + _td(minutes=60)
                for item in items:
                    sku = item.get("product_sku")
                    qty = int(item.get("quantity", 1))
                    if not sku or qty <= 0:
                        continue
                    # 检查库存表是否存在并有足够库存
                    cursor.execute("""
                        SELECT quantity_total, quantity_reserved
                        FROM bs_order_processing_inventory
                        WHERE tenant_id = %s AND product_sku = %s
                        FOR UPDATE
                    """, (tenant_id, sku))
                    inv_row = cursor.fetchone()
                    if inv_row:
                        available = inv_row[0] - inv_row[1]
                        if available >= qty:
                            reservation_id = _generate_id("rsv")
                            cursor.execute("""
                                UPDATE bs_order_processing_inventory
                                SET quantity_reserved = quantity_reserved + %s,
                                    quantity_available = quantity_available - %s,
                                    updated_at = %s
                                WHERE tenant_id = %s AND product_sku = %s
                            """, (qty, qty, now, tenant_id, sku))
                            cursor.execute("""
                                INSERT INTO bs_order_processing_inventory_reservations
                                (reservation_id, tenant_id, order_id, product_sku, quantity, status, expires_at, created_at, updated_at)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """, (reservation_id, tenant_id, order_id, sku, qty, "active", expires_at, now, now))
                            reservation_results.append({"sku": sku, "reserved": qty, "reservation_id": reservation_id})
                        else:
                            reservation_results.append({"sku": sku, "reserved": 0, "error": f"库存不足，可用 {available}，需要 {qty}"})
                    # 如果库存表中没有该 SKU 记录，跳过预留（不阻断订单创建）

            conn.commit()

        return {
            "success": True,
            "order_id": order_id,
            "status": "draft",
            "total_amount": total_amount,
            "discount_amount": discount_amount,
            "shipping_fee": shipping_fee,
            "final_amount": final_amount,
            "item_count": len(items),
            "inventory_reservations": reservation_results if reservation_results else None,
            "created_at": now.isoformat(),
        }
    except Exception as e:
        logger.error(f"创建订单失败: {e}", exc_info=True)
        return {"success": False, "error": f"创建订单失败: {str(e)}"}


# ============================================================
# 查询订单详情
# ============================================================

def get_order(args):
    """查询订单详情"""
    try:
        with get_db_connection() as conn:
            cursor = _get_cursor(conn)

            # 查询订单主信息
            cursor.execute("""
                SELECT order_id, tenant_id, user_id, session_id, customer_name,
                       customer_phone, customer_address, status, total_amount,
                       currency, discount_amount, shipping_fee, final_amount,
                       approval_status, approved_by, approved_at, approval_note,
                       rejection_reason, payment_status, payment_method,
                       payment_transaction_id, paid_at, notes, tags,
                       external_order_id, source, created_at, updated_at
                FROM bs_order_processing_orders
                WHERE order_id = %s
            """, (args.order_id,))

            row = cursor.fetchone()
            if not row:
                return {"success": False, "error": f"订单不存在: {args.order_id}"}

            order = {
                "order_id": row[0],
                "tenant_id": row[1],
                "user_id": row[2],
                "session_id": row[3],
                "customer_name": row[4],
                "customer_phone": row[5],
                "customer_address": row[6],
                "status": row[7],
                "total_amount": float(row[8]) if row[8] else None,
                "currency": row[9],
                "discount_amount": float(row[10]) if row[10] else 0,
                "shipping_fee": float(row[11]) if row[11] else 0,
                "final_amount": float(row[12]) if row[12] else None,
                "approval_status": row[13],
                "approved_by": row[14],
                "approved_at": row[15].isoformat() if row[15] else None,
                "approval_note": row[16],
                "rejection_reason": row[17],
                "payment_status": row[18],
                "payment_method": row[19],
                "payment_transaction_id": row[20],
                "paid_at": row[21].isoformat() if row[21] else None,
                "notes": row[22],
                "tags": row[23],
                "external_order_id": row[24],
                "source": row[25],
                "created_at": row[26].isoformat() if row[26] else None,
                "updated_at": row[27].isoformat() if row[27] else None,
            }

            # 查询订单明细
            cursor.execute("""
                SELECT item_id, product_sku, product_name, quantity, unit_price,
                       discount_rate, subtotal, notes, created_at
                FROM bs_order_processing_order_items
                WHERE order_id = %s
                ORDER BY created_at DESC
            """, (args.order_id,))

            item_rows = cursor.fetchall()
            items = []
            for ir in item_rows:
                items.append({
                    "item_id": ir[0],
                    "product_sku": ir[1],
                    "product_name": ir[2],
                    "quantity": ir[3],
                    "unit_price": float(ir[4]) if ir[4] else 0,
                    "discount_rate": float(ir[5]) if ir[5] else 0,
                    "subtotal": float(ir[6]) if ir[6] else 0,
                    "notes": ir[7],
                    "created_at": ir[8].isoformat() if ir[8] else None,
                })

            # 查询最近 10 条状态历史
            cursor.execute("""
                SELECT from_status, to_status, changed_by, change_reason, note, created_at
                FROM bs_order_processing_status_history
                WHERE order_id = %s
                ORDER BY created_at DESC
                LIMIT 10
            """, (args.order_id,))

            history_rows = cursor.fetchall()
            status_history = []
            for hr in history_rows:
                status_history.append({
                    "from_status": hr[0],
                    "to_status": hr[1],
                    "changed_by": hr[2],
                    "change_reason": hr[3],
                    "note": hr[4],
                    "created_at": hr[5].isoformat() if hr[5] else None,
                })

        return {
            "success": True,
            "order": order,
            "items": items,
            "item_count": len(items),
            "status_history": status_history,
        }
    except Exception as e:
        logger.error(f"查询订单详情失败: {e}", exc_info=True)
        return {"success": False, "error": f"查询订单详情失败: {str(e)}"}


# ============================================================
# 查询订单列表
# ============================================================

def list_orders(args):
    """查询订单列表"""
    try:
        with get_db_connection() as conn:
            cursor = _get_cursor(conn)

            conditions = ["user_id = %s"]
            params = [args.user_id]

            tenant_id = _get_current_tenant_id()
            if tenant_id:
                conditions.append("tenant_id = %s")
                params.append(tenant_id)

            if getattr(args, "status", None):
                conditions.append("status = %s")
                params.append(args.status)

            if getattr(args, "keyword", None):
                conditions.append("(customer_name ILIKE %s OR order_id ILIKE %s)")
                keyword = f"%{args.keyword}%"
                params.extend([keyword, keyword])

            if getattr(args, "date_from", None):
                conditions.append("created_at >= %s")
                params.append(args.date_from)

            if getattr(args, "date_to", None):
                conditions.append("created_at <= %s")
                params.append(args.date_to + " 23:59:59" if len(args.date_to) <= 10 else args.date_to)

            page = int(getattr(args, "page", 1) or 1)
            page_size = int(getattr(args, "page_size", 20) or 20)
            offset = (page - 1) * page_size

            where_clause = " AND ".join(conditions)

            # 总数统计
            cursor.execute(f"""
                SELECT COUNT(*)
                FROM bs_order_processing_orders
                WHERE {where_clause}
            """, params)
            total = cursor.fetchone()[0]

            # 分页查询
            cursor.execute(f"""
                SELECT order_id, customer_name, status, total_amount, final_amount,
                       currency, approval_status, payment_status, item_count,
                       created_at, updated_at
                FROM bs_order_processing_orders
                LEFT JOIN LATERAL (
                    SELECT COUNT(*) AS item_count
                    FROM bs_order_processing_order_items
                    WHERE bs_order_processing_order_items.order_id = bs_order_processing_orders.order_id
                ) sub ON true
                WHERE {where_clause}
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
            """, params + [page_size, offset])

            rows = cursor.fetchall()
            orders = []
            for row in rows:
                orders.append({
                    "order_id": row[0],
                    "customer_name": row[1],
                    "status": row[2],
                    "total_amount": float(row[3]) if row[3] else None,
                    "final_amount": float(row[4]) if row[4] else None,
                    "currency": row[5],
                    "approval_status": row[6],
                    "payment_status": row[7],
                    "item_count": row[8],
                    "created_at": row[9].isoformat() if row[9] else None,
                    "updated_at": row[10].isoformat() if row[10] else None,
                })

        return {
            "success": True,
            "orders": orders,
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    except Exception as e:
        logger.error(f"查询订单列表失败: {e}", exc_info=True)
        return {"success": False, "error": f"查询订单列表失败: {str(e)}"}


# ============================================================
# 更新订单
# ============================================================

def update_order(args):
    """更新订单信息（仅 draft 状态允许更新）"""
    try:
        with get_db_connection() as conn:
            cursor = _get_cursor(conn)
            now = datetime.now()

            # 检查当前状态
            cursor.execute("""
                SELECT status, total_amount FROM bs_order_processing_orders
                WHERE order_id = %s
            """, (args.order_id,))
            row = cursor.fetchone()
            if not row:
                return {"success": False, "error": f"订单不存在: {args.order_id}"}

            current_status, total_amount = row[0], row[1]
            if current_status != "draft":
                return {"success": False, "error": f"仅 draft 状态的订单允许更新，当前状态: {current_status}"}

            updates = ["updated_at = %s"]
            params = [now]

            optional_fields = [
                ("customer_name", "customer_name"),
                ("customer_phone", "customer_phone"),
                ("customer_address", "customer_address"),
                ("notes", "notes"),
                ("tags", "tags"),
            ]
            for attr, col in optional_fields:
                val = getattr(args, attr, None)
                if val is not None:
                    updates.append(f"{col} = %s")
                    params.append(val)

            discount_changed = False
            shipping_changed = False

            discount_amount = getattr(args, "discount_amount", None)
            if discount_amount is not None:
                updates.append("discount_amount = %s")
                params.append(float(discount_amount))
                discount_changed = True

            shipping_fee = getattr(args, "shipping_fee", None)
            if shipping_fee is not None:
                updates.append("shipping_fee = %s")
                params.append(float(shipping_fee))
                shipping_changed = True

            # 重新计算 final_amount
            if discount_changed or shipping_changed:
                new_discount = float(discount_amount) if discount_changed else 0
                new_shipping = float(shipping_fee) if shipping_changed else 0
                old_total = float(total_amount) if total_amount else 0
                # 如果 discount 没变，从 DB 获取原值
                if not discount_changed or not shipping_changed:
                    cursor.execute("""
                        SELECT discount_amount, shipping_fee FROM bs_order_processing_orders
                        WHERE order_id = %s
                    """, (args.order_id,))
                    db_row = cursor.fetchone()
                    if not discount_changed:
                        new_discount = float(db_row[0]) if db_row[0] else 0
                    if not shipping_changed:
                        new_shipping = float(db_row[1]) if db_row[1] else 0

                final_amount = old_total - new_discount + new_shipping
                updates.append("final_amount = %s")
                params.append(final_amount)

            params.append(args.order_id)

            cursor.execute(
                f"UPDATE bs_order_processing_orders SET {', '.join(updates)} WHERE order_id = %s",
                params
            )

            if cursor.rowcount == 0:
                return {"success": False, "error": f"订单不存在: {args.order_id}"}

            conn.commit()

        return {
            "success": True,
            "order_id": args.order_id,
            "updated_at": now.isoformat(),
        }
    except Exception as e:
        logger.error(f"更新订单失败: {e}", exc_info=True)
        return {"success": False, "error": f"更新订单失败: {str(e)}"}


# ============================================================
# 取消订单
# ============================================================

def cancel_order(args):
    """取消订单"""
    try:
        with get_db_connection() as conn:
            cursor = _get_cursor(conn)
            now = datetime.now()

            # 获取当前状态
            cursor.execute("""
                SELECT status FROM bs_order_processing_orders
                WHERE order_id = %s
            """, (args.order_id,))
            row = cursor.fetchone()
            if not row:
                return {"success": False, "error": f"订单不存在: {args.order_id}"}

            current_status = row[0]

            # 验证状态流转
            if "cancelled" not in STATUS_TRANSITIONS.get(current_status, []):
                return {"success": False, "error": f"当前状态 {current_status} 不允许取消"}

            tenant_id = _get_current_tenant_id()
            reason = getattr(args, "reason", "")

            # 更新状态
            cursor.execute("""
                UPDATE bs_order_processing_orders
                SET status = %s, updated_at = %s
                WHERE order_id = %s
            """, ("cancelled", now, args.order_id))

            # 写入状态历史
            cursor.execute("""
                INSERT INTO bs_order_processing_status_history
                (tenant_id, user_id, order_id, from_status, to_status, changed_by, change_reason, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (tenant_id, None, args.order_id, current_status, "cancelled", None, reason or "订单取消", now))

            # 释放该订单的所有活跃库存预留
            cursor.execute("""
                SELECT reservation_id, product_sku, quantity
                FROM bs_order_processing_inventory_reservations
                WHERE order_id = %s AND status = 'active'
            """, (args.order_id,))
            reservations = cursor.fetchall()
            released_count = 0
            for rsv in reservations:
                cursor.execute("""
                    UPDATE bs_order_processing_inventory_reservations
                    SET status = 'released', updated_at = %s
                    WHERE reservation_id = %s
                """, (now, rsv[0]))
                cursor.execute("""
                    UPDATE bs_order_processing_inventory
                    SET quantity_reserved = quantity_reserved - %s,
                        quantity_available = quantity_available + %s,
                        updated_at = %s
                    WHERE tenant_id = %s AND product_sku = %s
                """, (rsv[2], rsv[2], now, tenant_id, rsv[1]))
                released_count += 1

            conn.commit()

        return {
            "success": True,
            "order_id": args.order_id,
            "previous_status": current_status,
            "status": "cancelled",
            "inventory_released": released_count,
            "updated_at": now.isoformat(),
        }
    except Exception as e:
        logger.error(f"取消订单失败: {e}", exc_info=True)
        return {"success": False, "error": f"取消订单失败: {str(e)}"}


# ============================================================
# 变更订单状态
# ============================================================

def change_status(args):
    """变更订单状态"""
    try:
        with get_db_connection() as conn:
            cursor = _get_cursor(conn)
            now = datetime.now()

            # 获取当前状态
            cursor.execute("""
                SELECT status FROM bs_order_processing_orders
                WHERE order_id = %s
            """, (args.order_id,))
            row = cursor.fetchone()
            if not row:
                return {"success": False, "error": f"订单不存在: {args.order_id}"}

            current_status = row[0]
            target_status = args.status

            # 验证状态流转
            allowed = STATUS_TRANSITIONS.get(current_status, [])
            if target_status not in allowed:
                return {
                    "success": False,
                    "error": f"不允许从 {current_status} 转换到 {target_status}，允许的目标状态: {allowed}",
                }

            tenant_id = _get_current_tenant_id()
            reason = getattr(args, "reason", "")

            # 更新状态
            cursor.execute("""
                UPDATE bs_order_processing_orders
                SET status = %s, updated_at = %s
                WHERE order_id = %s
            """, (target_status, now, args.order_id))

            # 写入状态历史
            cursor.execute("""
                INSERT INTO bs_order_processing_status_history
                (tenant_id, user_id, order_id, from_status, to_status, changed_by, change_reason, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (tenant_id, None, args.order_id, current_status, target_status, None, reason, now))

            conn.commit()

        return {
            "success": True,
            "order_id": args.order_id,
            "from_status": current_status,
            "to_status": target_status,
            "updated_at": now.isoformat(),
        }
    except Exception as e:
        logger.error(f"变更订单状态失败: {e}", exc_info=True)
        return {"success": False, "error": f"变更订单状态失败: {str(e)}"}


# ============================================================
# 创建审批记录
# ============================================================

def create_approval(args):
    """创建审批记录"""
    try:
        approval_id = _generate_id("apr")
        tenant_id = _get_current_tenant_id()
        now = datetime.now()

        with get_db_connection() as conn:
            cursor = _get_cursor(conn)

            # 检查订单是否存在
            cursor.execute("""
                SELECT status FROM bs_order_processing_orders
                WHERE order_id = %s
            """, (args.order_id,))
            row = cursor.fetchone()
            if not row:
                return {"success": False, "error": f"订单不存在: {args.order_id}"}

            current_status = row[0]

            # 插入审批记录
            cursor.execute("""
                INSERT INTO bs_order_processing_approvals
                (approval_id, tenant_id, user_id, order_id, approval_type, status,
                 requested_by, assigned_to, amount_threshold, note,
                 created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                approval_id, tenant_id, args.requested_by, args.order_id,
                getattr(args, "approval_type", "order_approval"),
                "pending",
                args.requested_by,
                getattr(args, "assigned_to", None),
                getattr(args, "amount_threshold", None),
                getattr(args, "note", None),
                now, now,
            ))

            # 更新订单审批状态
            cursor.execute("""
                UPDATE bs_order_processing_orders
                SET approval_status = %s, updated_at = %s
                WHERE order_id = %s
            """, ("pending", now, args.order_id))

            # 如果订单状态为 draft，自动流转到 pending_approval
            if current_status == "draft":
                cursor.execute("""
                    UPDATE bs_order_processing_orders
                    SET status = %s, updated_at = %s
                    WHERE order_id = %s
                """, ("pending_approval", now, args.order_id))

                cursor.execute("""
                    INSERT INTO bs_order_processing_status_history
                    (tenant_id, user_id, order_id, from_status, to_status, changed_by, change_reason, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (tenant_id, args.requested_by, args.order_id, current_status, "pending_approval", args.requested_by, "提交审批", now))

            conn.commit()

        return {
            "success": True,
            "approval_id": approval_id,
            "order_id": args.order_id,
            "approval_status": "pending",
            "order_status": "pending_approval" if current_status == "draft" else None,
            "created_at": now.isoformat(),
        }
    except Exception as e:
        logger.error(f"创建审批记录失败: {e}", exc_info=True)
        return {"success": False, "error": f"创建审批记录失败: {str(e)}"}


# ============================================================
# 审批通过
# ============================================================

def approve_order(args):
    """审批通过"""
    try:
        with get_db_connection() as conn:
            cursor = _get_cursor(conn)
            now = datetime.now()
            tenant_id = _get_current_tenant_id()

            # 检查订单是否存在
            cursor.execute("""
                SELECT status FROM bs_order_processing_orders
                WHERE order_id = %s
            """, (args.order_id,))
            row = cursor.fetchone()
            if not row:
                return {"success": False, "error": f"订单不存在: {args.order_id}"}

            # 更新审批记录
            cursor.execute("""
                UPDATE bs_order_processing_approvals
                SET status = %s, resolved_by = %s, resolved_at = %s, updated_at = %s
                WHERE order_id = %s AND status = 'pending'
            """, ("approved", args.resolved_by, now, now, args.order_id))

            if cursor.rowcount == 0:
                return {"success": False, "error": f"未找到待审批记录: {args.order_id}"}

            # 更新订单审批信息
            note = getattr(args, "note", None)
            cursor.execute("""
                UPDATE bs_order_processing_orders
                SET approval_status = %s, approved_by = %s, approved_at = %s,
                    approval_note = %s, updated_at = %s
                WHERE order_id = %s
            """, ("approved", args.resolved_by, now, note, now, args.order_id))

            conn.commit()

        # 内部调用 change_status 将订单流转到 approved
        class _StatusArgs:
            pass

        status_args = _StatusArgs()
        status_args.order_id = args.order_id
        status_args.status = "approved"
        status_args.reason = "审批通过"

        status_result = change_status(status_args)
        if not status_result.get("success"):
            return status_result

        return {
            "success": True,
            "order_id": args.order_id,
            "approval_status": "approved",
            "approved_by": args.resolved_by,
            "approved_at": now.isoformat(),
            "order_status": "approved",
        }
    except Exception as e:
        logger.error(f"审批通过失败: {e}", exc_info=True)
        return {"success": False, "error": f"审批通过失败: {str(e)}"}


# ============================================================
# 审批拒绝
# ============================================================

def reject_order(args):
    """审批拒绝"""
    try:
        with get_db_connection() as conn:
            cursor = _get_cursor(conn)
            now = datetime.now()
            tenant_id = _get_current_tenant_id()

            # 检查订单是否存在
            cursor.execute("""
                SELECT status FROM bs_order_processing_orders
                WHERE order_id = %s
            """, (args.order_id,))
            row = cursor.fetchone()
            if not row:
                return {"success": False, "error": f"订单不存在: {args.order_id}"}

            rejection_reason = getattr(args, "rejection_reason", None)

            # 更新审批记录
            cursor.execute("""
                UPDATE bs_order_processing_approvals
                SET status = %s, resolved_by = %s, resolved_at = %s,
                    rejection_reason = %s, updated_at = %s
                WHERE order_id = %s AND status = 'pending'
            """, ("rejected", args.resolved_by, now, rejection_reason, now, args.order_id))

            if cursor.rowcount == 0:
                return {"success": False, "error": f"未找到待审批记录: {args.order_id}"}

            # 更新订单审批信息
            cursor.execute("""
                UPDATE bs_order_processing_orders
                SET approval_status = %s, rejection_reason = %s, updated_at = %s
                WHERE order_id = %s
            """, ("rejected", rejection_reason, now, args.order_id))

            conn.commit()

        # 内部调用 change_status 将订单流转到 rejected
        class _StatusArgs:
            pass

        status_args = _StatusArgs()
        status_args.order_id = args.order_id
        status_args.status = "rejected"
        status_args.reason = rejection_reason or "审批拒绝"

        status_result = change_status(status_args)
        if not status_result.get("success"):
            return status_result

        return {
            "success": True,
            "order_id": args.order_id,
            "approval_status": "rejected",
            "rejection_reason": rejection_reason,
            "order_status": "rejected",
        }
    except Exception as e:
        logger.error(f"审批拒绝失败: {e}", exc_info=True)
        return {"success": False, "error": f"审批拒绝失败: {str(e)}"}


# ============================================================
# 查询状态变更历史
# ============================================================

def get_status_history(args):
    """查询状态变更历史"""
    try:
        with get_db_connection() as conn:
            cursor = _get_cursor(conn)

            cursor.execute("""
                SELECT from_status, to_status, changed_by, change_reason, note, created_at
                FROM bs_order_processing_status_history
                WHERE order_id = %s
                ORDER BY created_at DESC
            """, (args.order_id,))

            rows = cursor.fetchall()
            history = []
            for row in rows:
                history.append({
                    "from_status": row[0],
                    "to_status": row[1],
                    "changed_by": row[2],
                    "change_reason": row[3],
                    "note": row[4],
                    "created_at": row[5].isoformat() if row[5] else None,
                })

        return {
            "success": True,
            "order_id": args.order_id,
            "history": history,
            "total": len(history),
        }
    except Exception as e:
        logger.error(f"查询状态历史失败: {e}", exc_info=True)
        return {"success": False, "error": f"查询状态历史失败: {str(e)}"}


# ============================================================
# 订单统计
# ============================================================

def stats(args):
    """订单统计"""
    try:
        with get_db_connection() as conn:
            cursor = _get_cursor(conn)

            tenant_id = _get_current_tenant_id()
            period = getattr(args, "period", "30d")
            group_by = getattr(args, "group_by", "status")

            # 计算时间范围
            days = 30
            if period.endswith("d"):
                days = int(period[:-1])
            elif period.endswith("w"):
                days = int(period[:-1]) * 7
            elif period.endswith("m"):
                days = int(period[:-1]) * 30

            since = datetime.now() - timedelta(days=days)

            conditions = ["created_at >= %s"]
            params = [since]
            if tenant_id:
                conditions.append("tenant_id = %s")
                params.append(tenant_id)

            where_clause = " AND ".join(conditions)

            # 总数及各状态统计
            status_fields = ", ".join([
                f"COUNT(CASE WHEN status = '{s}' THEN 1 END)" for s in [
                    "draft", "pending_approval", "approved", "rejected",
                    "processing", "shipped", "delivered", "completed",
                    "return_requested", "returned", "cancelled",
                ]
            ])

            cursor.execute(f"""
                SELECT COUNT(*), {status_fields}
                FROM bs_order_processing_orders
                WHERE {where_clause}
            """, params)
            total_row = cursor.fetchone()

            status_keys = [
                "draft", "pending_approval", "approved", "rejected",
                "processing", "shipped", "delivered", "completed",
                "return_requested", "returned", "cancelled",
            ]
            status_breakdown = {}
            for i, key in enumerate(status_keys):
                status_breakdown[key] = total_row[i + 1]

            # 分组统计
            valid_group_by = {"status": "status", "date": "DATE(created_at)"}.get(group_by, "status")
            cursor.execute(f"""
                SELECT {valid_group_by}, COUNT(*)
                FROM bs_order_processing_orders
                WHERE {where_clause}
                GROUP BY {valid_group_by}
                ORDER BY COUNT(*) DESC
            """, params)
            group_rows = cursor.fetchall()

            group_stats = [{"name": str(row[0]), "count": row[1]} for row in group_rows]

        return {
            "success": True,
            "period": period,
            "total": total_row[0],
            "status_breakdown": status_breakdown,
            "group_by": group_by,
            "group_stats": group_stats,
        }
    except Exception as e:
        logger.error(f"订单统计失败: {e}", exc_info=True)
        return {"success": False, "error": f"订单统计失败: {str(e)}"}


# ============================================================
# 主入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="订单处理核心工具")
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # create-order
    p = subparsers.add_parser("create-order", help="创建订单")
    p.add_argument("--user-id", required=True, help="用户ID")
    p.add_argument("--items", required=True, help="订单明细 JSON 字符串")
    p.add_argument("--customer-name", default=None, help="客户姓名")
    p.add_argument("--customer-phone", default=None, help="客户电话")
    p.add_argument("--customer-address", default=None, help="客户地址")
    p.add_argument("--currency", default="CNY", help="币种")
    p.add_argument("--discount-amount", default=None, help="整单优惠金额")
    p.add_argument("--shipping-fee", default=None, help="运费")
    p.add_argument("--notes", default=None, help="备注")
    p.add_argument("--tags", default=None, help="标签")
    p.add_argument("--external-order-id", default=None, help="外部系统订单号")
    p.add_argument("--source", default="internal", help="订单来源")
    p.add_argument("--tenant-id", default=None, help="租户ID")
    p.add_argument("--session-id", default=None, help="会话ID")
    p.add_argument("--skip-inventory", action="store_true", default=False, help="跳过库存预留")

    # get-order
    p = subparsers.add_parser("get-order", help="查询订单详情")
    p.add_argument("--order-id", required=True, help="订单ID")

    # list-orders
    p = subparsers.add_parser("list-orders", help="查询订单列表")
    p.add_argument("--user-id", required=True, help="用户ID")
    p.add_argument("--status", default=None, help="状态过滤")
    p.add_argument("--keyword", default=None, help="搜索关键词（客户姓名/订单号）")
    p.add_argument("--page", default=1, help="页码")
    p.add_argument("--page-size", default=20, help="每页数量")
    p.add_argument("--date-from", default=None, help="起始日期")
    p.add_argument("--date-to", default=None, help="截止日期")

    # update-order
    p = subparsers.add_parser("update-order", help="更新订单信息")
    p.add_argument("--order-id", required=True, help="订单ID")
    p.add_argument("--customer-name", default=None, help="客户姓名")
    p.add_argument("--customer-phone", default=None, help="客户电话")
    p.add_argument("--customer-address", default=None, help="客户地址")
    p.add_argument("--notes", default=None, help="备注")
    p.add_argument("--tags", default=None, help="标签")
    p.add_argument("--discount-amount", default=None, help="整单优惠金额")
    p.add_argument("--shipping-fee", default=None, help="运费")

    # cancel-order
    p = subparsers.add_parser("cancel-order", help="取消订单")
    p.add_argument("--order-id", required=True, help="订单ID")
    p.add_argument("--reason", default="", help="取消原因")

    # change-status
    p = subparsers.add_parser("change-status", help="变更订单状态")
    p.add_argument("--order-id", required=True, help="订单ID")
    p.add_argument("--status", required=True, help="目标状态")
    p.add_argument("--reason", default="", help="变更原因")

    # create-approval
    p = subparsers.add_parser("create-approval", help="创建审批记录")
    p.add_argument("--order-id", required=True, help="订单ID")
    p.add_argument("--requested-by", required=True, help="申请人ID")
    p.add_argument("--approval-type", default="order_approval", help="审批类型")
    p.add_argument("--assigned-to", default=None, help="审批人ID")
    p.add_argument("--amount-threshold", default=None, help="审批金额阈值")
    p.add_argument("--note", default=None, help="审批备注")

    # approve-order
    p = subparsers.add_parser("approve-order", help="审批通过")
    p.add_argument("--order-id", required=True, help="订单ID")
    p.add_argument("--resolved-by", required=True, help="审批人ID")
    p.add_argument("--note", default=None, help="审批备注")

    # reject-order
    p = subparsers.add_parser("reject-order", help="审批拒绝")
    p.add_argument("--order-id", required=True, help="订单ID")
    p.add_argument("--resolved-by", required=True, help="审批人ID")
    p.add_argument("--rejection-reason", default=None, help="拒绝原因")

    # get-status-history
    p = subparsers.add_parser("get-status-history", help="查询状态变更历史")
    p.add_argument("--order-id", required=True, help="订单ID")

    # stats
    p = subparsers.add_parser("stats", help="订单统计")
    p.add_argument("--period", default="30d", help="统计周期 (如 7d, 30d, 1m)")
    p.add_argument("--group-by", default="status", help="分组维度 (status/date)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    init_tables()

    commands = {
        "create-order": create_order,
        "get-order": get_order,
        "list-orders": list_orders,
        "update-order": update_order,
        "cancel-order": cancel_order,
        "change-status": change_status,
        "create-approval": create_approval,
        "approve-order": approve_order,
        "reject-order": reject_order,
        "get-status-history": get_status_history,
        "stats": stats,
    }

    handler = commands.get(args.command)
    if handler:
        result = handler(args)
    else:
        result = {"success": False, "error": f"未知命令: {args.command}"}

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
