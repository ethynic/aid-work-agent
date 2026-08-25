"""
订单物流发货 CLI 工具。

提供发货记录创建、物流状态追踪、签收确认等命令。
所有物流数据操作都通过本工具完成。

用法:
    python logistics_tool.py <command> [options]

命令:
    create-shipment    创建发货记录
    update-tracking    更新物流追踪信息
    get-shipment       查询发货详情
    list-shipments     查询发货列表
    query-tracking     按运单号查询物流
    confirm-delivery   确认签收
"""

import sys
import os
import json
import argparse
import uuid
from datetime import datetime

# 添加项目根目录到 Python 路径
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from loguru import logger


# ============================================================
# 物流状态流转定义
# ============================================================

SHIPMENT_STATUS_TRANSITIONS = {
    "pending": ["picked_up", "cancelled"],
    "picked_up": ["in_transit", "cancelled"],
    "in_transit": ["out_for_delivery", "cancelled"],
    "out_for_delivery": ["delivered", "cancelled"],
    "delivered": [],
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
        logger.info("[logistics_tool] 子进程中 PostgreSQL 连接池未初始化，正在自动初始化")
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


def _generate_id(prefix="sht"):
    """生成唯一ID"""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# ============================================================
# 表初始化
# ============================================================

def init_tables():
    """初始化物流发货相关表"""
    try:
        with get_db_connection() as conn:
            cursor = _get_cursor(conn)

            # 发货记录表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS bs_order_processing_shipments (
                    id SERIAL PRIMARY KEY,
                    shipment_id TEXT UNIQUE NOT NULL,
                    tenant_id TEXT,
                    user_id TEXT,
                    order_id TEXT NOT NULL,
                    carrier TEXT,
                    tracking_number TEXT,
                    shipping_method TEXT,
                    weight NUMERIC(8,2),
                    shipping_address TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    shipped_at TIMESTAMP,
                    delivered_at TIMESTAMP,
                    estimated_delivery TIMESTAMP,
                    notes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_shipments_tenant_order ON bs_order_processing_shipments (tenant_id, order_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_shipments_tracking ON bs_order_processing_shipments (tracking_number)")

            conn.commit()
            logger.info("[logistics_tool] 物流发货表初始化完成")
    except Exception as e:
        logger.opt(exception=True).error(f"[logistics_tool] 表初始化失败: {e}")
        raise


# ============================================================
# 创建发货记录
# ============================================================

def create_shipment(args):
    """创建发货记录，同时推进订单状态 processing -> shipped"""
    try:
        shipment_id = _generate_id("sht")
        tenant_id = getattr(args, "tenant_id", None) or _get_current_tenant_id()
        now = datetime.now()

        with get_db_connection() as conn:
            cursor = _get_cursor(conn)

            # 检查订单是否存在及当前状态
            cursor.execute("""
                SELECT status FROM bs_order_processing_orders
                WHERE order_id = %s
            """, (args.order_id,))
            row = cursor.fetchone()
            if not row:
                return {"success": False, "error": f"订单不存在: {args.order_id}"}

            order_status = row[0]

            # 插入发货记录
            cursor.execute("""
                INSERT INTO bs_order_processing_shipments
                (shipment_id, tenant_id, order_id, carrier, tracking_number,
                 shipping_method, weight, shipping_address, status,
                 shipped_at, estimated_delivery, notes, user_id, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                shipment_id, tenant_id, args.order_id,
                getattr(args, "carrier", None),
                getattr(args, "tracking_number", None),
                getattr(args, "shipping_method", None),
                getattr(args, "weight", None),
                getattr(args, "shipping_address", None),
                "pending",
                now,
                getattr(args, "estimated_delivery", None),
                getattr(args, "notes", None),
                None,
                now, now,
            ))

            # 自动推进订单状态 processing -> shipped
            order_updated = False
            if order_status == "processing":
                cursor.execute("""
                    UPDATE bs_order_processing_orders
                    SET status = %s, updated_at = %s
                    WHERE order_id = %s AND status = 'processing'
                """, ("shipped", now, args.order_id))

                if cursor.rowcount > 0:
                    order_updated = True
                    # 写入状态历史
                    cursor.execute("""
                        INSERT INTO bs_order_processing_status_history
                        (tenant_id, order_id, from_status, to_status, changed_by, change_reason, user_id, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """, (tenant_id, args.order_id, "processing", "shipped", None, "创建发货记录，订单自动发货", None, now))

            conn.commit()

        return {
            "success": True,
            "shipment_id": shipment_id,
            "order_id": args.order_id,
            "status": "pending",
            "order_status_updated": order_updated,
            "order_previous_status": order_status,
            "order_current_status": "shipped" if order_updated else order_status,
            "shipped_at": now.isoformat(),
            "created_at": now.isoformat(),
        }
    except Exception as e:
        logger.opt(exception=True).error(f"创建发货记录失败: {e}")
        return {"success": False, "error": f"创建发货记录失败: {str(e)}"}


# ============================================================
# 更新物流追踪信息
# ============================================================

def update_tracking(args):
    """更新物流追踪信息"""
    try:
        with get_db_connection() as conn:
            cursor = _get_cursor(conn)
            now = datetime.now()

            # 查询当前发货记录
            cursor.execute("""
                SELECT status FROM bs_order_processing_shipments
                WHERE shipment_id = %s
            """, (args.shipment_id,))
            row = cursor.fetchone()
            if not row:
                return {"success": False, "error": f"发货记录不存在: {args.shipment_id}"}

            current_status = row[0]

            # 如果提供了 status，验证状态流转
            new_status = getattr(args, "status", None)
            if new_status:
                allowed = SHIPMENT_STATUS_TRANSITIONS.get(current_status, [])
                if new_status not in allowed:
                    return {
                        "success": False,
                        "error": f"不允许从 {current_status} 转换到 {new_status}，允许的目标状态: {allowed}",
                    }

            # 构建更新字段
            updates = ["updated_at = %s"]
            params = [now]

            optional_fields = [
                ("tracking_number", "tracking_number"),
                ("carrier", "carrier"),
                ("estimated_delivery", "estimated_delivery"),
                ("notes", "notes"),
            ]
            for attr, col in optional_fields:
                val = getattr(args, attr, None)
                if val is not None:
                    updates.append(f"{col} = %s")
                    params.append(val)

            if new_status:
                updates.append("status = %s")
                params.append(new_status)

            params.append(args.shipment_id)

            cursor.execute(
                f"UPDATE bs_order_processing_shipments SET {', '.join(updates)} WHERE shipment_id = %s",
                params
            )

            if cursor.rowcount == 0:
                return {"success": False, "error": f"发货记录不存在: {args.shipment_id}"}

            conn.commit()

        return {
            "success": True,
            "shipment_id": args.shipment_id,
            "previous_status": current_status,
            "current_status": new_status or current_status,
            "updated_at": now.isoformat(),
        }
    except Exception as e:
        logger.opt(exception=True).error(f"更新物流追踪失败: {e}")
        return {"success": False, "error": f"更新物流追踪失败: {str(e)}"}


# ============================================================
# 查询发货详情
# ============================================================

def get_shipment(args):
    """查询发货详情"""
    try:
        with get_db_connection() as conn:
            cursor = _get_cursor(conn)

            cursor.execute("""
                SELECT shipment_id, tenant_id, order_id, carrier, tracking_number,
                       shipping_method, weight, shipping_address, status,
                       shipped_at, delivered_at, estimated_delivery, notes,
                       created_at, updated_at
                FROM bs_order_processing_shipments
                WHERE shipment_id = %s
            """, (args.shipment_id,))

            row = cursor.fetchone()
            if not row:
                return {"success": False, "error": f"发货记录不存在: {args.shipment_id}"}

            shipment = {
                "shipment_id": row[0],
                "tenant_id": row[1],
                "order_id": row[2],
                "carrier": row[3],
                "tracking_number": row[4],
                "shipping_method": row[5],
                "weight": float(row[6]) if row[6] else None,
                "shipping_address": row[7],
                "status": row[8],
                "shipped_at": row[9].isoformat() if row[9] else None,
                "delivered_at": row[10].isoformat() if row[10] else None,
                "estimated_delivery": row[11].isoformat() if row[11] else None,
                "notes": row[12],
                "created_at": row[13].isoformat() if row[13] else None,
                "updated_at": row[14].isoformat() if row[14] else None,
            }

        return {
            "success": True,
            "shipment": shipment,
        }
    except Exception as e:
        logger.opt(exception=True).error(f"查询发货详情失败: {e}")
        return {"success": False, "error": f"查询发货详情失败: {str(e)}"}


# ============================================================
# 查询发货列表
# ============================================================

def list_shipments(args):
    """查询发货列表"""
    try:
        with get_db_connection() as conn:
            cursor = _get_cursor(conn)

            conditions = []
            params = []

            tenant_id = _get_current_tenant_id()
            if tenant_id:
                conditions.append("tenant_id = %s")
                params.append(tenant_id)

            if getattr(args, "order_id", None):
                conditions.append("order_id = %s")
                params.append(args.order_id)

            if getattr(args, "status", None):
                conditions.append("status = %s")
                params.append(args.status)

            page = int(getattr(args, "page", 1) or 1)
            page_size = int(getattr(args, "page_size", 20) or 20)
            offset = (page - 1) * page_size

            where_clause = " AND ".join(conditions) if conditions else "1=1"

            # 总数统计
            cursor.execute(f"""
                SELECT COUNT(*)
                FROM bs_order_processing_shipments
                WHERE {where_clause}
            """, params)
            total = cursor.fetchone()[0]

            # 分页查询
            cursor.execute(f"""
                SELECT shipment_id, order_id, carrier, tracking_number,
                       shipping_method, weight, status, shipped_at,
                       delivered_at, estimated_delivery, created_at, updated_at
                FROM bs_order_processing_shipments
                WHERE {where_clause}
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
            """, params + [page_size, offset])

            rows = cursor.fetchall()
            shipments = []
            for row in rows:
                shipments.append({
                    "shipment_id": row[0],
                    "order_id": row[1],
                    "carrier": row[2],
                    "tracking_number": row[3],
                    "shipping_method": row[4],
                    "weight": float(row[5]) if row[5] else None,
                    "status": row[6],
                    "shipped_at": row[7].isoformat() if row[7] else None,
                    "delivered_at": row[8].isoformat() if row[8] else None,
                    "estimated_delivery": row[9].isoformat() if row[9] else None,
                    "created_at": row[10].isoformat() if row[10] else None,
                    "updated_at": row[11].isoformat() if row[11] else None,
                })

        return {
            "success": True,
            "shipments": shipments,
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    except Exception as e:
        logger.opt(exception=True).error(f"查询发货列表失败: {e}")
        return {"success": False, "error": f"查询发货列表失败: {str(e)}"}


# ============================================================
# 按运单号查询物流
# ============================================================

def query_tracking(args):
    """按运单号查询物流信息"""
    try:
        with get_db_connection() as conn:
            cursor = _get_cursor(conn)

            cursor.execute("""
                SELECT shipment_id, tenant_id, order_id, carrier, tracking_number,
                       shipping_method, weight, shipping_address, status,
                       shipped_at, delivered_at, estimated_delivery, notes,
                       created_at, updated_at
                FROM bs_order_processing_shipments
                WHERE tracking_number = %s
            """, (args.tracking_number,))

            row = cursor.fetchone()
            if not row:
                return {
                    "success": True,
                    "found": False,
                    "tracking_number": args.tracking_number,
                    "message": f"本地数据库未找到运单号 {args.tracking_number} 的记录，建议查询外部物流 API 获取实时追踪信息",
                }

            shipment = {
                "shipment_id": row[0],
                "tenant_id": row[1],
                "order_id": row[2],
                "carrier": row[3],
                "tracking_number": row[4],
                "shipping_method": row[5],
                "weight": float(row[6]) if row[6] else None,
                "shipping_address": row[7],
                "status": row[8],
                "shipped_at": row[9].isoformat() if row[9] else None,
                "delivered_at": row[10].isoformat() if row[10] else None,
                "estimated_delivery": row[11].isoformat() if row[11] else None,
                "notes": row[12],
                "created_at": row[13].isoformat() if row[13] else None,
                "updated_at": row[14].isoformat() if row[14] else None,
            }

        return {
            "success": True,
            "found": True,
            "shipment": shipment,
        }
    except Exception as e:
        logger.opt(exception=True).error(f"查询物流追踪失败: {e}")
        return {"success": False, "error": f"查询物流追踪失败: {str(e)}"}


# ============================================================
# 确认签收
# ============================================================

def confirm_delivery(args):
    """确认签收，同时推进订单状态 shipped -> delivered"""
    try:
        with get_db_connection() as conn:
            cursor = _get_cursor(conn)
            now = datetime.now()
            tenant_id = _get_current_tenant_id()

            # 查询发货记录
            cursor.execute("""
                SELECT order_id, status FROM bs_order_processing_shipments
                WHERE shipment_id = %s
            """, (args.shipment_id,))
            row = cursor.fetchone()
            if not row:
                return {"success": False, "error": f"发货记录不存在: {args.shipment_id}"}

            order_id, shipment_status = row[0], row[1]

            if shipment_status == "delivered":
                return {"success": False, "error": "该发货记录已签收，无需重复确认"}

            if shipment_status == "cancelled":
                return {"success": False, "error": "该发货记录已取消，无法确认签收"}

            # 检查订单状态
            cursor.execute("""
                SELECT status FROM bs_order_processing_orders
                WHERE order_id = %s
            """, (order_id,))
            order_row = cursor.fetchone()
            if not order_row:
                return {"success": False, "error": f"关联订单不存在: {order_id}"}

            order_status = order_row[0]
            if order_status != "shipped":
                return {
                    "success": False,
                    "error": f"订单当前状态为 {order_status}，仅 shipped 状态的订单可以确认签收",
                }

            # 更新发货记录状态为 delivered
            notes = getattr(args, "notes", None)
            update_fields = ["status = %s", "delivered_at = %s", "updated_at = %s"]
            update_params = ["delivered", now, now]

            if notes:
                update_fields.append("notes = %s")
                update_params.append(notes)

            update_params.append(args.shipment_id)

            cursor.execute(
                f"UPDATE bs_order_processing_shipments SET {', '.join(update_fields)} WHERE shipment_id = %s",
                update_params
            )

            # 推进订单状态 shipped -> delivered
            cursor.execute("""
                UPDATE bs_order_processing_orders
                SET status = %s, updated_at = %s
                WHERE order_id = %s AND status = 'shipped'
            """, ("delivered", now, order_id))

            order_updated = cursor.rowcount > 0

            # 写入状态历史
            if order_updated:
                cursor.execute("""
                    INSERT INTO bs_order_processing_status_history
                    (tenant_id, order_id, from_status, to_status, changed_by, change_reason, user_id, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (tenant_id, order_id, "shipped", "delivered", None, "确认签收，订单自动完成发货", None, now))

            conn.commit()

        return {
            "success": True,
            "shipment_id": args.shipment_id,
            "order_id": order_id,
            "shipment_status": "delivered",
            "order_status_updated": order_updated,
            "order_current_status": "delivered" if order_updated else order_status,
            "delivered_at": now.isoformat(),
        }
    except Exception as e:
        logger.opt(exception=True).error(f"确认签收失败: {e}")
        return {"success": False, "error": f"确认签收失败: {str(e)}"}


# ============================================================
# 主入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="订单物流发货工具")
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # create-shipment
    p = subparsers.add_parser("create-shipment", help="创建发货记录")
    p.add_argument("--order-id", required=True, help="订单ID")
    p.add_argument("--carrier", default=None, help="承运商")
    p.add_argument("--tracking-number", default=None, help="物流运单号")
    p.add_argument("--shipping-method", default=None, help="发货方式")
    p.add_argument("--weight", default=None, help="包裹重量(kg)")
    p.add_argument("--shipping-address", default=None, help="收货地址")
    p.add_argument("--estimated-delivery", default=None, help="预计送达日期")
    p.add_argument("--notes", default=None, help="备注")
    p.add_argument("--tenant-id", default=None, help="租户ID")

    # update-tracking
    p = subparsers.add_parser("update-tracking", help="更新物流追踪信息")
    p.add_argument("--shipment-id", required=True, help="发货记录ID")
    p.add_argument("--tracking-number", default=None, help="物流运单号")
    p.add_argument("--carrier", default=None, help="承运商")
    p.add_argument("--status", default=None, help="物流状态")
    p.add_argument("--estimated-delivery", default=None, help="预计送达日期")
    p.add_argument("--notes", default=None, help="备注")

    # get-shipment
    p = subparsers.add_parser("get-shipment", help="查询发货详情")
    p.add_argument("--shipment-id", required=True, help="发货记录ID")

    # list-shipments
    p = subparsers.add_parser("list-shipments", help="查询发货列表")
    p.add_argument("--order-id", default=None, help="订单ID")
    p.add_argument("--status", default=None, help="发货状态过滤")
    p.add_argument("--page", default=1, help="页码")
    p.add_argument("--page-size", default=20, help="每页数量")

    # query-tracking
    p = subparsers.add_parser("query-tracking", help="按运单号查询物流")
    p.add_argument("--tracking-number", required=True, help="物流运单号")

    # confirm-delivery
    p = subparsers.add_parser("confirm-delivery", help="确认签收")
    p.add_argument("--shipment-id", required=True, help="发货记录ID")
    p.add_argument("--notes", default=None, help="签收备注")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    init_tables()

    commands = {
        "create-shipment": create_shipment,
        "update-tracking": update_tracking,
        "get-shipment": get_shipment,
        "list-shipments": list_shipments,
        "query-tracking": query_tracking,
        "confirm-delivery": confirm_delivery,
    }

    handler = commands.get(args.command)
    if handler:
        result = handler(args)
    else:
        result = {"success": False, "error": f"未知命令: {args.command}"}

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
