#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
售后服务内部操作脚本。

提供内部工单和退换货记录的 CLI 操作。
数据库表在 init_tables() 中自动创建。

用法:
    python after_sales_tool.py create-ticket --user-id USER --description DESC --category CAT [--order-id ORDER] [--priority normal]
    python after_sales_tool.py query-ticket --ticket-id ID
    python after_sales_tool.py list-tickets --user-id USER [--status STATUS]
    python after_sales_tool.py create-return --user-id USER --order-id ORDER --type return --reason REASON [--items JSON]
    python after_sales_tool.py query-returns --user-id USER [--order-id ORDER]
"""

import argparse
import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

# 添加项目根目录到路径
script_path = Path(__file__).resolve()
if 'src' in script_path.parts:
    src_index = script_path.parts.index('src')
    project_root = Path(*script_path.parts[:src_index])
else:
    project_root = script_path.parent
sys.path.insert(0, str(project_root))

from loguru import logger


def _generate_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def get_db_connection():
    """获取数据库连接（子进程可能需要自动初始化连接池）"""
    from src.db.database import (
        get_db_connection as _get_db,
        get_postgres_pool,
        init_postgres_pool,
    )
    if get_postgres_pool() is None:
        logger.info("[after-sales] 子进程中 PostgreSQL 连接池未初始化，正在自动初始化")
        init_postgres_pool()
    return _get_db()


def _get_current_tenant_id() -> Optional[str]:
    """获取当前租户 ID（优先级：stdin > 环境变量 > ContextVar）"""
    # 1. 从 stdin 读取（skill_execute 通过 stdin 注入 JSON）
    try:
        raw = sys.stdin.read().strip()
        if raw:
            data = json.loads(raw)
            if isinstance(data, dict) and data.get("tenant_id"):
                return data["tenant_id"]
    except (json.JSONDecodeError, EOFError, ValueError):
        pass

    # 2. 从环境变量获取
    tenant_id = os.environ.get("CURRENT_TENANT_ID")
    if tenant_id:
        return tenant_id

    # 3. 从 ContextVar 获取（子进程中通常不可用）
    try:
        from src.saas.context import get_current_tenant_id
        return get_current_tenant_id()
    except Exception:
        return None


def init_tables():
    """初始化售后服务相关数据库表"""
    with get_db_connection() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bs_after_sales_tickets (
                id SERIAL PRIMARY KEY,
                ticket_id TEXT UNIQUE NOT NULL,
                tenant_id TEXT,
                user_id TEXT NOT NULL,
                session_id TEXT,
                order_id TEXT,
                category TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                priority TEXT NOT NULL DEFAULT 'normal',
                description TEXT NOT NULL,
                resolution TEXT,
                external_ticket_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bs_after_sales_ticket_messages (
                id SERIAL PRIMARY KEY,
                tenant_id TEXT,
                ticket_id TEXT NOT NULL,
                sender_type TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bs_after_sales_returns (
                id SERIAL PRIMARY KEY,
                return_id TEXT UNIQUE NOT NULL,
                tenant_id TEXT,
                user_id TEXT NOT NULL,
                session_id TEXT,
                order_id TEXT NOT NULL,
                type TEXT NOT NULL,
                reason TEXT NOT NULL,
                items JSON,
                status TEXT NOT NULL DEFAULT 'pending',
                external_return_id TEXT,
                refund_amount NUMERIC(10,2),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_ast_tenant ON bs_after_sales_tickets(tenant_id, status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_ast_user ON bs_after_sales_tickets(user_id, status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_astm_ticket ON bs_after_sales_ticket_messages(tenant_id, ticket_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_asr_tenant ON bs_after_sales_returns(tenant_id, status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_asr_user ON bs_after_sales_returns(user_id, status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_asr_order ON bs_after_sales_returns(order_id)")

        conn.commit()
        logger.info("售后数据表初始化完成")


# ========== 工单操作 ==========

def create_ticket(
    user_id: str,
    description: str,
    category: str,
    tenant_id: Optional[str] = None,
    session_id: Optional[str] = None,
    order_id: Optional[str] = None,
    priority: str = "normal",
) -> Dict[str, Any]:
    """创建售后工单"""
    if not user_id or not description or not category:
        return {"success": False, "error": "user_id, description, category 为必填参数"}

    ticket_id = _generate_id("ast")
    if not tenant_id:
        tenant_id = _get_current_tenant_id()

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO bs_after_sales_tickets
                   (ticket_id, tenant_id, user_id, session_id, order_id,
                    category, priority, description)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (ticket_id, tenant_id, user_id, session_id, order_id,
                 category, priority, description)
            )
            conn.commit()

        return {
            "success": True,
            "ticket_id": ticket_id,
            "category": category,
            "status": "open",
            "priority": priority,
        }
    except Exception as e:
        logger.error(f"创建售后工单失败: {e}", exc_info=True)
        return {"success": False, "error": f"创建工单失败: {e}"}


def query_ticket(ticket_id: str, tenant_id: Optional[str] = None) -> Dict[str, Any]:
    """查询工单"""
    if not ticket_id:
        return {"success": False, "error": "ticket_id 为必填参数"}

    if not tenant_id:
        tenant_id = _get_current_tenant_id()

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            if tenant_id:
                cursor.execute(
                    "SELECT * FROM bs_after_sales_tickets WHERE ticket_id = %s AND tenant_id = %s",
                    (ticket_id, tenant_id)
                )
            else:
                cursor.execute(
                    "SELECT * FROM bs_after_sales_tickets WHERE ticket_id = %s",
                    (ticket_id,)
                )
            row = cursor.fetchone()
            if row:
                t = dict(row)
                return {
                    "success": True,
                    "ticket": {
                        "ticket_id": t["ticket_id"],
                        "category": t["category"],
                        "status": t["status"],
                        "priority": t["priority"],
                        "description": t["description"],
                        "resolution": t.get("resolution"),
                        "order_id": t.get("order_id"),
                        "created_at": str(t.get("created_at", "")),
                        "updated_at": str(t.get("updated_at", "")),
                    }
                }
            return {"success": False, "error": f"未找到工单: {ticket_id}"}
    except Exception as e:
        logger.error(f"查询工单失败: {e}", exc_info=True)
        return {"success": False, "error": f"查询工单失败: {e}"}


def list_tickets(
    user_id: str,
    tenant_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 20,
) -> Dict[str, Any]:
    """列出用户的工单"""
    if not user_id:
        return {"success": False, "error": "user_id 为必填参数"}

    if not tenant_id:
        tenant_id = _get_current_tenant_id()

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            conditions = ["user_id = %s"]
            params: list = [user_id]

            if tenant_id:
                conditions.append("tenant_id = %s")
                params.append(tenant_id)

            if status:
                conditions.append("status = %s")
                params.append(status)

            where_clause = " AND ".join(conditions)

            cursor.execute(
                f"SELECT * FROM bs_after_sales_tickets WHERE {where_clause} "
                f"ORDER BY created_at DESC LIMIT {limit}",
                tuple(params)
            )
            rows = cursor.fetchall()
            tickets = [
                {
                    "ticket_id": t["ticket_id"],
                    "category": t["category"],
                    "status": t["status"],
                    "description": t["description"][:100] + "..." if len(t["description"]) > 100 else t["description"],
                    "created_at": str(t.get("created_at", "")),
                }
                for t in (dict(r) for r in rows)
            ]

            return {"success": True, "count": len(tickets), "tickets": tickets}
    except Exception as e:
        logger.error(f"列出工单失败: {e}", exc_info=True)
        return {"success": False, "error": f"列出工单失败: {e}"}


# ========== 退换货操作 ==========

def create_return(
    user_id: str,
    order_id: str,
    return_type: str,
    reason: str,
    tenant_id: Optional[str] = None,
    session_id: Optional[str] = None,
    items: Optional[str] = None,
) -> Dict[str, Any]:
    """创建退换货记录"""
    if not user_id or not order_id or not return_type or not reason:
        return {"success": False, "error": "user_id, order_id, type, reason 为必填参数"}

    if return_type not in ("return", "exchange"):
        return {"success": False, "error": "type 必须为 return 或 exchange"}

    return_id = _generate_id("ret")
    if not tenant_id:
        tenant_id = _get_current_tenant_id()

    items_json = None
    if items:
        try:
            items_json = json.loads(items) if isinstance(items, str) else items
        except json.JSONDecodeError:
            items_json = items
        items_json = json.dumps(items_json, ensure_ascii=False) if not isinstance(items_json, str) else items_json

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO bs_after_sales_returns
                   (return_id, tenant_id, user_id, session_id, order_id,
                    type, reason, items)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (return_id, tenant_id, user_id, session_id, order_id,
                 return_type, reason, items_json)
            )
            conn.commit()

        return {
            "success": True,
            "return_id": return_id,
            "type": return_type,
            "status": "pending",
        }
    except Exception as e:
        logger.error(f"创建退换货记录失败: {e}", exc_info=True)
        return {"success": False, "error": f"创建退换货记录失败: {e}"}


def query_returns(
    user_id: str,
    tenant_id: Optional[str] = None,
    order_id: Optional[str] = None,
    limit: int = 20,
) -> Dict[str, Any]:
    """查询退换货记录"""
    if not user_id:
        return {"success": False, "error": "user_id 为必填参数"}

    if not tenant_id:
        tenant_id = _get_current_tenant_id()

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            conditions = ["user_id = %s"]
            params: list = [user_id]

            if tenant_id:
                conditions.append("tenant_id = %s")
                params.append(tenant_id)

            if order_id:
                conditions.append("order_id = %s")
                params.append(order_id)

            where_clause = " AND ".join(conditions)

            cursor.execute(
                f"SELECT * FROM bs_after_sales_returns WHERE {where_clause} "
                f"ORDER BY created_at DESC LIMIT {limit}",
                tuple(params)
            )
            rows = cursor.fetchall()
            records = [
                {
                    "return_id": r["return_id"],
                    "order_id": r["order_id"],
                    "type": r["type"],
                    "reason": r["reason"],
                    "status": r["status"],
                    "created_at": str(r.get("created_at", "")),
                }
                for r in (dict(row) for row in rows)
            ]

            return {"success": True, "count": len(records), "returns": records}
    except Exception as e:
        logger.error(f"查询退换货记录失败: {e}", exc_info=True)
        return {"success": False, "error": f"查询退换货记录失败: {e}"}


# ========== CLI 入口 ==========

def main():
    parser = argparse.ArgumentParser(description="售后服务内部操作")
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # create-ticket
    ct = subparsers.add_parser("create-ticket", help="创建内部工单")
    ct.add_argument("--user-id", required=True, help="用户 ID")
    ct.add_argument("--description", required=True, help="问题描述")
    ct.add_argument("--category", required=True, help="问题分类: order_issue/return/exchange/repair/usage/other")
    ct.add_argument("--order-id", help="关联订单号")
    ct.add_argument("--priority", default="normal", help="优先级: low/normal/high/urgent")
    ct.add_argument("--tenant-id", help="租户 ID")
    ct.add_argument("--session-id", help="会话 ID")

    # query-ticket
    qt = subparsers.add_parser("query-ticket", help="查询工单")
    qt.add_argument("--ticket-id", required=True, help="工单 ID")
    qt.add_argument("--tenant-id", help="租户 ID")

    # list-tickets
    lt = subparsers.add_parser("list-tickets", help="列出用户工单")
    lt.add_argument("--user-id", required=True, help="用户 ID")
    lt.add_argument("--status", help="状态筛选")
    lt.add_argument("--tenant-id", help="租户 ID")

    # create-return
    cr = subparsers.add_parser("create-return", help="创建退换货记录")
    cr.add_argument("--user-id", required=True, help="用户 ID")
    cr.add_argument("--order-id", required=True, help="订单号")
    cr.add_argument("--type", required=True, help="类型: return/exchange")
    cr.add_argument("--reason", required=True, help="退换货原因")
    cr.add_argument("--items", help="涉及商品 JSON")
    cr.add_argument("--tenant-id", help="租户 ID")
    cr.add_argument("--session-id", help="会话 ID")

    # query-returns
    qr = subparsers.add_parser("query-returns", help="查询退换货记录")
    qr.add_argument("--user-id", required=True, help="用户 ID")
    qr.add_argument("--order-id", help="按订单号筛选")
    qr.add_argument("--tenant-id", help="租户 ID")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    # 初始化表
    init_tables()

    result = None

    if args.command == "create-ticket":
        result = create_ticket(
            user_id=args.user_id,
            description=args.description,
            category=args.category,
            tenant_id=args.tenant_id,
            session_id=args.session_id,
            order_id=args.order_id,
            priority=args.priority,
        )

    elif args.command == "query-ticket":
        result = query_ticket(
            ticket_id=args.ticket_id,
            tenant_id=args.tenant_id,
        )

    elif args.command == "list-tickets":
        result = list_tickets(
            user_id=args.user_id,
            tenant_id=args.tenant_id,
            status=args.status,
        )

    elif args.command == "create-return":
        result = create_return(
            user_id=args.user_id,
            order_id=args.order_id,
            return_type=args.type,
            reason=args.reason,
            tenant_id=args.tenant_id,
            session_id=args.session_id,
            items=args.items,
        )

    elif args.command == "query-returns":
        result = query_returns(
            user_id=args.user_id,
            tenant_id=args.tenant_id,
            order_id=args.order_id,
        )

    else:
        result = {"success": False, "error": f"未知命令: {args.command}"}

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
