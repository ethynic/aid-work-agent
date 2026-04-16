#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
外贸客户信息管理脚本

用于保存外贸获客过程中匹配到的客户信息，跟踪邮件发送记录

用法:
    # 单个客户保存（推荐，每次生成一个客户）
    python customer_manager.py save-customer --user-id USER --session-id SESSION --customer JSON

    # 批量保存客户（一次保存多个）
    python customer_manager.py save-customers --user-id USER --session-id SESSION --customers JSON

    # 其他操作
    python customer_manager.py list-customers --user-id USER [--session-id SESSION]
    python customer_manager.py get-customer --customer-id ID
    python customer_manager.py record-email --customer-id ID --user-id USER --session-id SESSION --subject SUBJECT --body BODY [--language LANG] [--status STATUS]
    python customer_manager.py list-emails --customer-id ID 或 --user-id USER
    python customer_manager.py stats --user-id USER
"""

import argparse
import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

# 添加项目根目录到路径（兼容 Windows 和 Linux）
script_path = Path(__file__).resolve()
# 向上查找 src 目录
if 'src' in script_path.parts:
    src_index = script_path.parts.index('src')
    project_root = Path(*script_path.parts[:src_index])
else:
    project_root = script_path.parent
sys.path.insert(0, str(project_root))

from loguru import logger


def generate_customer_id() -> str:
    """生成唯一客户ID"""
    return f"cust_{uuid.uuid4().hex[:12]}"


def generate_email_id() -> str:
    """生成唯一邮件ID"""
    return f"email_{uuid.uuid4().hex[:12]}"


def save_execution_log(command: str, args: Dict[str, Any], result: Dict[str, Any]) -> str:
    """保存执行结果到日志文件

    Args:
        command: 命令名称
        args: 命令参数
        result: 执行结果

    Returns:
        日志文件路径
    """
    # 获取日志目录
    script_dir = Path(__file__).parent
    skill_dir = script_dir.parent
    log_dir = skill_dir / "logs"
    log_dir.mkdir(exist_ok=True)

    # 生成日志文件名
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"{command}_{timestamp}.json"

    # 构建日志内容
    log_content = {
        "timestamp": datetime.now().isoformat(),
        "command": command,
        "args": args,
        "result": result
    }

    # 保存到文件
    with open(log_file, "w", encoding="utf-8") as f:
        json.dump(log_content, f, ensure_ascii=False, indent=2)

    return str(log_file)


def parse_json_safe(json_str: str) -> Any:
    """安全解析 JSON，支持多种格式

    支持格式：
    - 标准 JSON（双引号）
    - 单引号 JSON
    - JavaScript 风格裸键格式，如 [{key:value,key2:value2}]
    - 带单引号包裹的 JSON 字符串

    Args:
        json_str: JSON 字符串

    Returns:
        解析后的对象
    """
    if not json_str:
        return None

    # 去除首尾空白
    json_str = json_str.strip()

    # 去除首尾多余的单引号（处理 "'[...]'" 这种情况）
    if json_str.startswith("'") and json_str.endswith("'"):
        json_str = json_str[1:-1].strip()

    # 先尝试直接解析（标准 JSON，双引号）
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        pass

    # 尝试将单引号替换为双引号（兼容性处理）
    try:
        import re
        # 匹配不在转义序列中的单引号
        fixed = re.sub(r"(?<!\\)'", '"', json_str)
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass

    # 处理 JavaScript 风格的裸键格式
    # 例如: [{company_name:値,contact_person:値}] -> [{"company_name":"値","contact_person":"値"}]
    try:
        import re

        # 检测是否是裸键格式（键没有引号）
        if re.search(r'[a-zA-Z_][a-zA-Z0-9_]*\s*:', json_str):
            # 判断是数组还是对象
            is_array = json_str.startswith('[')

            # 去除数组括号
            content = json_str.strip('[]')

            # 分割 items - 按逗号分割，但跳过引号内的逗号
            items = []
            depth = 0
            current = []
            in_string = False
            escape_next = False

            i = 0
            while i < len(content):
                char = content[i]

                if escape_next:
                    current.append(char)
                    escape_next = False
                    i += 1
                    continue

                if char == '\\':
                    escape_next = True
                    current.append(char)
                    i += 1
                    continue

                if char == '"':
                    in_string = not in_string
                    current.append(char)
                    i += 1
                    continue

                if not in_string:
                    if char in ('{', '['):
                        depth += 1
                        current.append(char)
                    elif char in (']', '}'):
                        depth -= 1
                        current.append(char)
                    elif char == ',' and depth == 0:
                        items.append(''.join(current))
                        current = []
                        i += 1
                        continue

                current.append(char)
                i += 1

            if current:
                items.append(''.join(current))

            # 解析每个 item
            results = []
            for item in items:
                item = item.strip()
                if not item:
                    continue

                # 去除首尾的花括号
                item = item.strip()
                if item.startswith('{'):
                    item = item[1:]
                if item.endswith('}'):
                    item = item[:-1]

                # 使用正则匹配 key:value
                pairs = re.findall(r'([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*(?:"([^"]*)"|\'([^\']*)\'|([^,\}]+))', item)

                obj = {}
                for match in pairs:
                    key = match[0]
                    value = match[1] if match[1] else (match[2] if match[2] else match[3])
                    if value:
                        value = value.strip()
                        value = value.replace('"', '\\"')
                        obj[key] = value

                if obj:
                    results.append(obj)

            return results if is_array else (results[0] if results else {})
    except Exception:
        pass

    # 最后尝试 ast.literal_eval（更宽容）
    try:
        import ast
        return ast.literal_eval(json_str)
    except Exception:
        raise json.JSONDecodeError(f"无法解析 JSON: {json_str}", json_str, 0)


def get_db_connection():
    """获取数据库连接"""
    from src.db.database import (
        get_db_connection as _get_db,
        get_db_placeholder as _placeholder,
        DB_TYPE as _db_type,
        get_postgres_pool,
        init_postgres_pool,
    )
    # 子进程独立运行时，PostgreSQL 连接池可能未初始化，需要自动初始化
    if _db_type == "postgresql" and get_postgres_pool() is None:
        logger.info("后端日志：[trade-customer] 子进程中 PostgreSQL 连接池未初始化，正在自动初始化")
        init_postgres_pool()
    return _get_db()


def get_db_placeholder():
    """获取参数占位符"""
    from src.db.database import get_db_placeholder as _placeholder
    return _placeholder()


def get_db_type():
    """获取数据库类型"""
    from src.db.database import DB_TYPE
    return DB_TYPE


def get_date_offset(days: int) -> str:
    """获取指定天数前的日期时间函数"""
    from src.db.database import get_date_offset as _get_date_offset
    return _get_date_offset(days)


def init_tables():
    """初始化客户相关表"""
    db_type = get_db_type()
    id_column = "INTEGER PRIMARY KEY AUTOINCREMENT" if db_type == "sqlite" else "SERIAL PRIMARY KEY"

    with get_db_connection() as conn:
        cursor = conn.cursor()

        # 匹配客户表
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS bs_trade_specialist_matched_customers (
                id {id_column},
                customer_id TEXT UNIQUE NOT NULL,
                tenant_id TEXT NOT NULL,
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
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS bs_trade_specialist_customer_emails (
                id {id_column},
                email_id TEXT UNIQUE NOT NULL,
                customer_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                email_subject TEXT,
                email_body TEXT,
                email_language TEXT DEFAULT 'en',
                send_time TEXT,
                send_status TEXT DEFAULT 'success',
                error_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (customer_id) REFERENCES bs_trade_specialist_matched_customers(customer_id)
            )
        """)

        # 索引
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_bs_trade_specialist_matched_customers_tenant
            ON bs_trade_specialist_matched_customers(tenant_id, created_at DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_bs_trade_specialist_matched_customers_user
            ON bs_trade_specialist_matched_customers(user_id, created_at DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_bs_trade_specialist_matched_customers_session
            ON bs_trade_specialist_matched_customers(session_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_bs_trade_specialist_customer_emails_customer
            ON bs_trade_specialist_customer_emails(customer_id, created_at DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_bs_trade_specialist_customer_emails_user
            ON bs_trade_specialist_customer_emails(user_id, created_at DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_bs_trade_specialist_customer_emails_tenant
            ON bs_trade_specialist_customer_emails(tenant_id, created_at DESC)
        """)

        conn.commit()
        logger.info("客户管理表初始化完成")


def save_customer(user_id: str, session_id: str, customer: Dict[str, Any]) -> Dict[str, Any]:
    """保存单个客户信息（推荐方式）

    每次只保存一个客户，降低大模型生成完整JSON的难度。

    Args:
        user_id: 用户ID
        session_id: 会话ID
        customer: 单个客户信息字典

    Returns:
        保存结果
    """
    if not user_id or not session_id:
        logger.warning(f"后端日志：[trade-customer诊断] save_customer 缺少必需参数: user_id={'有' if user_id else '无'}, session_id={'有' if session_id else '无'}")
        return {"success": False, "error": "user_id 和 session_id 是必需参数"}

    if not customer:
        logger.warning(f"后端日志：[trade-customer诊断] save_customer customer 为空")
        return {"success": False, "error": "客户信息不能为空"}

    try:
        logger.info(f"后端日志：[trade-customer诊断] save_customer 开始写入数据库, user_id={user_id}, session_id={session_id}, customer_keys={list(customer.keys()) if customer else '无'}")
        logger.info(f"后端日志：[trade-customer诊断] save_customer 客户详情: company_name={customer.get('company_name')}, contact_name={customer.get('contact_name') or customer.get('contact_person')}, email={customer.get('email')}, country={customer.get('country')}")
        with get_db_connection() as conn:
            cursor = conn.cursor()
            customer_id = generate_customer_id()
            match_date = datetime.now().strftime("%Y-%m-%d")

            # 字段映射：兼容不同格式的字段名
            contact_name = customer.get("contact_name") or customer.get("contact_person", "")
            import_category = customer.get("import_category") or customer.get("import_products", "")

            cursor.execute("""
                INSERT INTO bs_trade_specialist_matched_customers
                (customer_id, user_id, session_id, company_name, contact_name,
                 email, country, language, industry, import_category,
                 company_size, match_reason, match_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                customer_id,
                user_id,
                session_id,
                customer.get("company_name", ""),
                contact_name,
                customer.get("email", ""),
                customer.get("country", ""),
                customer.get("language", ""),
                customer.get("industry", ""),
                import_category,
                customer.get("company_size", ""),
                customer.get("match_reason", ""),
                match_date
            ))

            conn.commit()
            logger.info(f"后端日志：保存单个客户 {customer_id} - {customer.get('company_name', '')}")
            logger.info(f"后端日志：[trade-customer诊断] save_customer 数据库写入成功! customer_id={customer_id}, session_id={session_id}, company={customer.get('company_name')}")

            return {
                "success": True,
                "data": {
                    "customer_id": customer_id,
                    "company_name": customer.get("company_name", ""),
                    "contact_name": contact_name,
                    "email": customer.get("email", "")
                }
            }
    except Exception as e:
        logger.error(f"后端日志：保存单个客户失败: {e}", exc_info=True)
        logger.error(f"后端日志：[trade-customer诊断] save_customer 数据库写入失败! user_id={user_id}, session_id={session_id}, error={e}", exc_info=True)
        return {
            "success": False,
            "error": "保存客户信息失败",
            "debug": str(e)
        }


def save_customers(user_id: str, session_id: str, customers: List[Dict[str, Any]]) -> Dict[str, Any]:
    """保存客户信息（批量方式）"""
    if not user_id or not session_id:
        logger.warning(f"后端日志：[trade-customer诊断] save_customers 缺少必需参数: user_id={'有' if user_id else '无'}, session_id={'有' if session_id else '无'}")
        return {"success": False, "error": "user_id 和 session_id 是必需参数"}

    if not customers:
        logger.warning(f"后端日志：[trade-customer诊断] save_customers customers 为空")
        return {"success": False, "error": "客户列表不能为空"}

    try:
        logger.info(f"后端日志：[trade-customer诊断] save_customers 开始批量写入数据库, user_id={user_id}, session_id={session_id}, customers_count={len(customers)}")
        for i, c in enumerate(customers):
            logger.info(f"后端日志：[trade-customer诊断] 客户[{i}]: company_name={c.get('company_name')}, contact_name={c.get('contact_name') or c.get('contact_person')}, email={c.get('email')}, country={c.get('country')}")
        with get_db_connection() as conn:
            cursor = conn.cursor()
            saved_customers = []

            for customer in customers:
                customer_id = generate_customer_id()
                match_date = datetime.now().strftime("%Y-%m-%d")

                # 字段映射：兼容不同格式的字段名
                contact_name = customer.get("contact_name") or customer.get("contact_person", "")
                import_category = customer.get("import_category") or customer.get("import_products", "")

                cursor.execute("""
                    INSERT INTO bs_trade_specialist_matched_customers
                    (customer_id, user_id, session_id, company_name, contact_name,
                     email, country, language, industry, import_category,
                     company_size, match_reason, match_date)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    customer_id,
                    user_id,
                    session_id,
                    customer.get("company_name", ""),
                    contact_name,
                    customer.get("email", ""),
                    customer.get("country", ""),
                    customer.get("language", ""),
                    customer.get("industry", ""),
                    import_category,
                    customer.get("company_size", ""),
                    customer.get("match_reason", ""),
                    match_date
                ))

                saved_customers.append({
                    "customer_id": customer_id,
                    "company_name": customer.get("company_name", ""),
                    "contact_name": contact_name,
                    "email": customer.get("email", "")
                })

            conn.commit()
            logger.info(f"后端日志：保存了 {len(saved_customers)} 个客户")
            logger.info(f"后端日志：[trade-customer诊断] save_customers 批量写入成功! saved_count={len(saved_customers)}, session_id={session_id}")

            return {
                "success": True,
                "data": {
                    "saved_count": len(saved_customers),
                    "customers": saved_customers
                }
            }
    except Exception as e:
        logger.error(f"后端日志：保存客户失败: {e}", exc_info=True)
        logger.error(f"后端日志：[trade-customer诊断] save_customers 批量写入失败! user_id={user_id}, session_id={session_id}, error={e}", exc_info=True)
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
                    SELECT * FROM bs_trade_specialist_matched_customers
                    WHERE user_id = ? AND session_id = ?
                    ORDER BY created_at DESC
                """, (user_id, session_id))
            else:
                cursor.execute("""
                    SELECT * FROM bs_trade_specialist_matched_customers
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
                SELECT * FROM bs_trade_specialist_matched_customers WHERE customer_id = ?
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
                SELECT * FROM bs_trade_specialist_customer_emails
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
            cursor.execute("SELECT customer_id FROM bs_trade_specialist_matched_customers WHERE customer_id = ?",
                          (customer_id,))
            if not cursor.fetchone():
                return {
                    "success": False,
                    "error": f"客户 {customer_id} 不存在"
                }

            email_id = generate_email_id()
            send_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            cursor.execute("""
                INSERT INTO bs_trade_specialist_customer_emails
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
            placeholder = get_db_placeholder()

            if customer_id:
                # PostgreSQL 不支持 LIMIT ?，需要直接拼接
                cursor.execute(f"""
                    SELECT ce.*, mc.company_name, mc.contact_name
                    FROM bs_trade_specialist_customer_emails ce
                    LEFT JOIN bs_trade_specialist_matched_customers mc ON ce.customer_id = mc.customer_id
                    WHERE ce.customer_id = {placeholder}
                    ORDER BY ce.created_at DESC
                    LIMIT {limit}
                """, (customer_id,))
            else:
                cursor.execute(f"""
                    SELECT ce.*, mc.company_name, mc.contact_name
                    FROM bs_trade_specialist_customer_emails ce
                    LEFT JOIN bs_trade_specialist_matched_customers mc ON ce.customer_id = mc.customer_id
                    WHERE ce.user_id = {placeholder}
                    ORDER BY ce.created_at DESC
                    LIMIT {limit}
                """, (user_id,))

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
                SELECT COUNT(*) as total FROM bs_trade_specialist_matched_customers WHERE user_id = ?
            """, (user_id,))
            total_customers = cursor.fetchone()["total"]

            # 邮件发送总数
            cursor.execute("""
                SELECT COUNT(*) as total FROM bs_trade_specialist_customer_emails WHERE user_id = ?
            """, (user_id,))
            total_emails = cursor.fetchone()["total"]

            # 成功发送数
            cursor.execute("""
                SELECT COUNT(*) as total FROM bs_trade_specialist_customer_emails
                WHERE user_id = ? AND send_status = 'success'
            """, (user_id,))
            success_emails = cursor.fetchone()["total"]

            # 发送失败的邮件数
            cursor.execute("""
                SELECT COUNT(*) as total FROM bs_trade_specialist_customer_emails
                WHERE user_id = ? AND send_status = 'failed'
            """, (user_id,))
            failed_emails = cursor.fetchone()["total"]

            # 最近7天的客户新增数
            seven_days_ago = get_date_offset(-7)
            cursor.execute(f"""
                SELECT COUNT(*) as total FROM bs_trade_specialist_matched_customers
                WHERE user_id = ? AND created_at >= {seven_days_ago}
            """, (user_id,))
            recent_customers = cursor.fetchone()["total"]

            # 最近7天的邮件发送数
            cursor.execute(f"""
                SELECT COUNT(*) as total FROM bs_trade_specialist_customer_emails
                WHERE user_id = ? AND created_at >= {seven_days_ago}
            """, (user_id,))
            recent_emails = cursor.fetchone()["total"]

            # 按国家分布
            cursor.execute("""
                SELECT country, COUNT(*) as count
                FROM bs_trade_specialist_matched_customers
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

    # 保存单个客户（推荐方式）
    save_single_parser = subparsers.add_parser("save-customer", help="保存单个客户信息（推荐）")
    save_single_parser.add_argument("--user-id", required=True, help="用户ID")
    save_single_parser.add_argument("--session-id", required=True, help="会话ID")
    save_single_parser.add_argument("--customer", help="单个客户信息JSON对象")

    # 保存客户（批量方式）
    save_parser = subparsers.add_parser("save-customers", help="批量保存客户信息")
    save_parser.add_argument("--user-id", required=True, help="用户ID")
    save_parser.add_argument("--session-id", required=True, help="会话ID")
    save_parser.add_argument("--customers", help="客户信息JSON数组（直接传递）")
    save_parser.add_argument("--customers-file", help="客户信息JSON文件路径（从文件读取，推荐用于大量客户）")

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

    if args.command == "save-customer":
        # 后端日志：诊断 save-customer 命令入口
        logger.info(f"后端日志：[trade-customer诊断] save-customer 命令被调用, user_id={args.user_id}, session_id={args.session_id}")
        customer_raw = getattr(args, 'customer', None)
        if customer_raw:
            logger.info(f"后端日志：[trade-customer诊断] --customer 原始值: {customer_raw[:500] if len(str(customer_raw)) > 500 else customer_raw}")
        else:
            logger.warning(f"后端日志：[trade-customer诊断] --customer 参数为空! 未传入客户数据")
        try:
            if customer_raw:
                customer = parse_json_safe(args.customer)
                if customer:
                    logger.info(f"后端日志：[trade-customer诊断] JSON 解析成功, customer 类型={type(customer).__name__}, 内容={json.dumps(customer, ensure_ascii=False)[:500]}")
                else:
                    logger.error(f"后端日志：[trade-customer诊断] JSON 解析结果为 None! 原始值: {customer_raw[:200]}")
            else:
                result = {"success": False, "error": "必须指定 --customer"}
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"后端日志：[trade-customer诊断] JSON 解析异常: {e}, 原始值: {customer_raw[:200] if customer_raw else '空'}")
            result = {"success": False, "error": "JSON格式错误", "debug": str(e)}
        else:
            if result is None:
                result = save_customer(args.user_id, args.session_id, customer)

    elif args.command == "save-customers":
        # 后端日志：诊断 save-customers 命令入口
        logger.info(f"后端日志：[trade-customer诊断] save-customers 命令被调用, user_id={args.user_id}, session_id={args.session_id}")
        customers_raw = getattr(args, 'customers', None)
        if customers_raw:
            logger.info(f"后端日志：[trade-customer诊断] --customers 原始值: {customers_raw[:500] if len(str(customers_raw)) > 500 else customers_raw}")
        else:
            logger.warning(f"后端日志：[trade-customer诊断] --customers 参数为空!")
        try:
            # 优先从文件读取，其次从命令行参数
            if getattr(args, 'customers_file', None):
                customers_file = Path(args.customers_file)
                if not customers_file.exists():
                    result = {"success": False, "error": f"客户文件不存在: {args.customers_file}"}
                else:
                    customers = parse_json_safe(customers_file.read_text(encoding="utf-8"))
            elif customers_raw:
                customers = parse_json_safe(args.customers)
                if customers:
                    logger.info(f"后端日志：[trade-customer诊断] JSON 解析成功, customers 类型={type(customers).__name__}, 数量={len(customers) if isinstance(customers, list) else 'N/A'}")
                else:
                    logger.error(f"后端日志：[trade-customer诊断] JSON 解析结果为 None!")
            else:
                result = {"success": False, "error": "必须指定 --customers 或 --customers-file"}
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"后端日志：[trade-customer诊断] JSON 解析异常: {e}")
            result = {"success": False, "error": "JSON格式错误", "debug": str(e)}
        else:
            if result is None:
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

    # 后端日志：诊断最终输出结果
    if result:
        logger.info(f"后端日志：[trade-customer诊断] 命令 '{args.command}' 最终结果: success={result.get('success')}")
        if not result.get("success"):
            logger.warning(f"后端日志：[trade-customer诊断] 命令执行失败! error={result.get('error')}, debug={result.get('debug', '')}")

    # 保存执行日志到文件
    log_args = vars(args) if hasattr(args, '__dict__') else {}
    log_file = save_execution_log(args.command, log_args, result)
    print(f"\n[日志已保存] {log_file}")


if __name__ == "__main__":
    main()
