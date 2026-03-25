#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
外贸客户信息管理脚本

用于保存外贸获客过程中匹配到的客户信息，跟踪邮件发送记录

用法:
    python customer_manager.py save-customers --user-id USER --session-id SESSION --customers JSON
    python customer_manager.py list-customers --user-id USER [--session-id SESSION]
    python customer_manager.py get-customer --customer-id ID
    python customer_manager.py record-email --customer-id ID --user-id USER --session-id SESSION --subject SUBJECT --body BODY [--language LANG] [--status STATUS]
    python customer_manager.py list-emails --customer-id ID 或 --user-id USER
    python customer_manager.py stats --user-id USER
"""

import argparse
import json
import sys
import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any

# 添加项目根目录到路径
sys.path.insert(0, str(__file__).rsplit('/src/', 1)[0] if '/src/' in __file__ else '.')

from loguru import logger


def generate_customer_id() -> str:
    """生成唯一客户ID"""
    return f"cust_{uuid.uuid4().hex[:12]}"


def generate_email_id() -> str:
    """生成唯一邮件ID"""
    return f"email_{uuid.uuid4().hex[:12]}"


def get_db_connection():
    """获取数据库连接"""
    from src.db.database import get_db_connection as _get_db
    return _get_db()


def init_tables():
    """初始化客户相关表"""
    with get_db_connection() as conn:
        cursor = conn.cursor()

        # 匹配客户表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS matched_customers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id TEXT UNIQUE NOT NULL,
                user_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                company_name TEXT,
                contact_name TEXT,
                email TEXT,
                country TEXT,
                language TEXT,
                industry TEXT,
                import_category TEXT,
                company_size TEXT,
                match_reason TEXT,
                match_date TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 客户邮件表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS customer_emails (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email_id TEXT UNIQUE NOT NULL,
                customer_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                email_subject TEXT,
                email_body TEXT,
                email_language TEXT DEFAULT 'en',
                send_time TEXT,
                send_status TEXT DEFAULT 'success',
                error_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (customer_id) REFERENCES matched_customers(customer_id)
            )
        """)

        # 索引
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_matched_customers_user
            ON matched_customers(user_id, created_at DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_matched_customers_session
            ON matched_customers(session_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_customer_emails_customer
            ON customer_emails(customer_id, created_at DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_customer_emails_user
            ON customer_emails(user_id, created_at DESC)
        """)

        conn.commit()
        logger.info("客户管理表初始化完成")


def save_customers(user_id: str, session_id: str, customers: List[Dict[str, Any]]) -> Dict[str, Any]:
    """保存客户信息"""
    if not user_id or not session_id:
        return {"success": False, "error": "user_id 和 session_id 是必需参数"}

    if not customers:
        return {"success": False, "error": "客户列表不能为空"}

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            saved_customers = []

            for customer in customers:
                customer_id = generate_customer_id()
                match_date = datetime.now().strftime("%Y-%m-%d")

                cursor.execute("""
                    INSERT INTO matched_customers
                    (customer_id, user_id, session_id, company_name, contact_name,
                     email, country, language, industry, import_category,
                     company_size, match_reason, match_date)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    customer_id,
                    user_id,
                    session_id,
                    customer.get("company_name", ""),
                    customer.get("contact_name", ""),
                    customer.get("email", ""),
                    customer.get("country", ""),
                    customer.get("language", ""),
                    customer.get("industry", ""),
                    customer.get("import_category", ""),
                    customer.get("company_size", ""),
                    customer.get("match_reason", ""),
                    match_date
                ))

                saved_customers.append({
                    "customer_id": customer_id,
                    "company_name": customer.get("company_name", ""),
                    "contact_name": customer.get("contact_name", ""),
                    "email": customer.get("email", "")
                })

            conn.commit()
            logger.info(f"后端日志：保存了 {len(saved_customers)} 个客户")

            return {
                "success": True,
                "data": {
                    "saved_count": len(saved_customers),
                    "customers": saved_customers
                }
            }
    except Exception as e:
        logger.error(f"后端日志：保存客户失败: {e}", exc_info=True)
        return {
            "success": False,
            "error": "保存客户信息失败",
            "debug": str(e)
        }


def list_customers(user_id: str, session_id: Optional[str] = None) -> Dict[str, Any]:
    """查询客户列表"""
    if not user_id:
        return {"success": False, "error": "user_id 是必需参数"}

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()

            if session_id:
                cursor.execute("""
                    SELECT * FROM matched_customers
                    WHERE user_id = ? AND session_id = ?
                    ORDER BY created_at DESC
                """, (user_id, session_id))
            else:
                cursor.execute("""
                    SELECT * FROM matched_customers
                    WHERE user_id = ?
                    ORDER BY created_at DESC
                """, (user_id,))

            rows = cursor.fetchall()
            customers = []
            for row in rows:
                customers.append(dict(row))

            logger.info(f"后端日志：查询到 {len(customers)} 个客户")

            return {
                "success": True,
                "data": {
                    "count": len(customers),
                    "customers": customers
                }
            }
    except Exception as e:
        logger.error(f"后端日志：查询客户列表失败: {e}", exc_info=True)
        return {
            "success": False,
            "error": "查询客户列表失败",
            "debug": str(e)
        }


def get_customer(customer_id: str) -> Dict[str, Any]:
    """查询客户详情"""
    if not customer_id:
        return {"success": False, "error": "customer_id 是必需参数"}

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM matched_customers WHERE customer_id = ?
            """, (customer_id,))

            row = cursor.fetchone()
            if not row:
                return {
                    "success": False,
                    "error": f"客户 {customer_id} 不存在"
                }

            customer = dict(row)

            # 查询该客户的邮件历史
            cursor.execute("""
                SELECT * FROM customer_emails
                WHERE customer_id = ?
                ORDER BY created_at DESC
            """, (customer_id,))

            email_rows = cursor.fetchall()
            emails = [dict(r) for r in email_rows]
            customer["emails"] = emails

            logger.info(f"后端日志：查询客户详情 {customer_id}")

            return {
                "success": True,
                "data": customer
            }
    except Exception as e:
        logger.error(f"后端日志：查询客户详情失败: {e}", exc_info=True)
        return {
            "success": False,
            "error": "查询客户详情失败",
            "debug": str(e)
        }


def record_email(
    customer_id: str,
    user_id: str,
    session_id: str,
    email_subject: str,
    email_body: str,
    email_language: str = "en",
    send_status: str = "success",
    error_message: Optional[str] = None
) -> Dict[str, Any]:
    """记录邮件发送"""
    if not all([customer_id, user_id, session_id, email_subject, email_body]):
        return {"success": False, "error": "缺少必需参数"}

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()

            # 检查客户是否存在
            cursor.execute("SELECT customer_id FROM matched_customers WHERE customer_id = ?",
                          (customer_id,))
            if not cursor.fetchone():
                return {
                    "success": False,
                    "error": f"客户 {customer_id} 不存在"
                }

            email_id = generate_email_id()
            send_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            cursor.execute("""
                INSERT INTO customer_emails
                (email_id, customer_id, user_id, session_id, email_subject,
                 email_body, email_language, send_time, send_status, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                email_id,
                customer_id,
                user_id,
                session_id,
                email_subject,
                email_body,
                email_language,
                send_time,
                send_status,
                error_message
            ))

            conn.commit()
            logger.info(f"后端日志：记录邮件发送 {email_id} 给客户 {customer_id}")

            return {
                "success": True,
                "data": {
                    "email_id": email_id,
                    "customer_id": customer_id,
                    "send_time": send_time,
                    "send_status": send_status
                }
            }
    except Exception as e:
        logger.error(f"后端日志：记录邮件发送失败: {e}", exc_info=True)
        return {
            "success": False,
            "error": "记录邮件发送失败",
            "debug": str(e)
        }


def list_emails(
    customer_id: Optional[str] = None,
    user_id: Optional[str] = None,
    limit: int = 100
) -> Dict[str, Any]:
    """查询邮件历史"""
    if not customer_id and not user_id:
        return {"success": False, "error": "customer_id 或 user_id 至少需要提供一个"}

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()

            if customer_id:
                cursor.execute("""
                    SELECT ce.*, mc.company_name, mc.contact_name
                    FROM customer_emails ce
                    LEFT JOIN matched_customers mc ON ce.customer_id = mc.customer_id
                    WHERE ce.customer_id = ?
                    ORDER BY ce.created_at DESC
                    LIMIT ?
                """, (customer_id, limit))
            else:
                cursor.execute("""
                    SELECT ce.*, mc.company_name, mc.contact_name
                    FROM customer_emails ce
                    LEFT JOIN matched_customers mc ON ce.customer_id = mc.customer_id
                    WHERE ce.user_id = ?
                    ORDER BY ce.created_at DESC
                    LIMIT ?
                """, (user_id, limit))

            rows = cursor.fetchall()
            emails = [dict(row) for row in rows]

            logger.info(f"后端日志：查询到 {len(emails)} 条邮件记录")

            return {
                "success": True,
                "data": {
                    "count": len(emails),
                    "emails": emails
                }
            }
    except Exception as e:
        logger.error(f"后端日志：查询邮件历史失败: {e}", exc_info=True)
        return {
            "success": False,
            "error": "查询邮件历史失败",
            "debug": str(e)
        }


def get_stats(user_id: str) -> Dict[str, Any]:
    """获取用户统计信息"""
    if not user_id:
        return {"success": False, "error": "user_id 是必需参数"}

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()

            # 客户总数
            cursor.execute("""
                SELECT COUNT(*) as total FROM matched_customers WHERE user_id = ?
            """, (user_id,))
            total_customers = cursor.fetchone()["total"]

            # 邮件发送总数
            cursor.execute("""
                SELECT COUNT(*) as total FROM customer_emails WHERE user_id = ?
            """, (user_id,))
            total_emails = cursor.fetchone()["total"]

            # 成功发送数
            cursor.execute("""
                SELECT COUNT(*) as total FROM customer_emails
                WHERE user_id = ? AND send_status = 'success'
            """, (user_id,))
            success_emails = cursor.fetchone()["total"]

            # 发送失败的邮件数
            cursor.execute("""
                SELECT COUNT(*) as total FROM customer_emails
                WHERE user_id = ? AND send_status = 'failed'
            """, (user_id,))
            failed_emails = cursor.fetchone()["total"]

            # 最近7天的客户新增数
            cursor.execute("""
                SELECT COUNT(*) as total FROM matched_customers
                WHERE user_id = ? AND created_at >= datetime('now', '-7 days')
            """, (user_id,))
            recent_customers = cursor.fetchone()["total"]

            # 最近7天的邮件发送数
            cursor.execute("""
                SELECT COUNT(*) as total FROM customer_emails
                WHERE user_id = ? AND created_at >= datetime('now', '-7 days')
            """, (user_id,))
            recent_emails = cursor.fetchone()["total"]

            # 按国家分布
            cursor.execute("""
                SELECT country, COUNT(*) as count
                FROM matched_customers
                WHERE user_id = ?
                GROUP BY country
                ORDER BY count DESC
                LIMIT 10
            """, (user_id,))
            country_dist = [dict(row) for row in cursor.fetchall()]

            stats = {
                "total_customers": total_customers,
                "total_emails": total_emails,
                "success_emails": success_emails,
                "failed_emails": failed_emails,
                "recent_customers": recent_customers,
                "recent_emails": recent_emails,
                "country_distribution": country_dist
            }

            logger.info(f"后端日志：用户 {user_id} 统计信息")

            return {
                "success": True,
                "data": stats
            }
    except Exception as e:
        logger.error(f"后端日志：获取统计信息失败: {e}", exc_info=True)
        return {
            "success": False,
            "error": "获取统计信息失败",
            "debug": str(e)
        }


def main():
    parser = argparse.ArgumentParser(description="外贸客户信息管理")
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # 保存客户
    save_parser = subparsers.add_parser("save-customers", help="保存客户信息")
    save_parser.add_argument("--user-id", required=True, help="用户ID")
    save_parser.add_argument("--session-id", required=True, help="会话ID")
    save_parser.add_argument("--customers", required=True, help="客户信息JSON数组")

    # 列出客户
    list_parser = subparsers.add_parser("list-customers", help="查询客户列表")
    list_parser.add_argument("--user-id", required=True, help="用户ID")
    list_parser.add_argument("--session-id", help="会话ID")

    # 客户详情
    get_parser = subparsers.add_parser("get-customer", help="查询客户详情")
    get_parser.add_argument("--customer-id", required=True, help="客户ID")

    # 记录邮件
    email_parser = subparsers.add_parser("record-email", help="记录邮件发送")
    email_parser.add_argument("--customer-id", required=True, help="客户ID")
    email_parser.add_argument("--user-id", required=True, help="用户ID")
    email_parser.add_argument("--session-id", required=True, help="会话ID")
    email_parser.add_argument("--subject", required=True, help="邮件主题")
    email_parser.add_argument("--body", required=True, help="邮件正文")
    email_parser.add_argument("--language", default="en", help="邮件语言")
    email_parser.add_argument("--status", default="success", help="发送状态")
    email_parser.add_argument("--error-message", help="错误信息")

    # 列出邮件
    list_email_parser = subparsers.add_parser("list-emails", help="查询邮件历史")
    list_email_parser.add_argument("--customer-id", help="客户ID")
    list_email_parser.add_argument("--user-id", help="用户ID")
    list_email_parser.add_argument("--limit", type=int, default=100, help="返回数量限制")

    # 统计
    stats_parser = subparsers.add_parser("stats", help="获取统计信息")
    stats_parser.add_argument("--user-id", required=True, help="用户ID")

    args = parser.parse_args()

    # 初始化表
    init_tables()

    if not args.command:
        parser.print_help()
        return

    result = None

    if args.command == "save-customers":
        try:
            customers = json.loads(args.customers)
        except json.JSONDecodeError as e:
            result = {"success": False, "error": "JSON格式错误", "debug": str(e)}
        else:
            result = save_customers(args.user_id, args.session_id, customers)

    elif args.command == "list-customers":
        result = list_customers(args.user_id, args.session_id)

    elif args.command == "get-customer":
        result = get_customer(args.customer_id)

    elif args.command == "record-email":
        result = record_email(
            args.customer_id,
            args.user_id,
            args.session_id,
            args.subject,
            args.body,
            args.language,
            args.status,
            args.error_message
        )

    elif args.command == "list-emails":
        result = list_emails(args.customer_id, args.user_id, args.limit)

    elif args.command == "stats":
        result = get_stats(args.user_id)

    else:
        result = {"success": False, "error": f"未知命令: {args.command}"}

    # 输出结果
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
