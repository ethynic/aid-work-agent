"""
用量统计查询

从 chat_records 聚合 token 使用数据，用于计费和报告。
"""

from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from loguru import logger

from src.db.database import get_db_connection


class UsageLogDB:
    """用量统计数据库访问类"""

    @staticmethod
    def get_tenant_usage(
        tenant_id: str,
        start_date: str,
        end_date: str,
    ) -> Dict[str, Any]:
        """获取租户在指定时间范围内的总用量"""
        with get_db_connection() as conn:
            cursor = conn.cursor()

            # 从 tenant_users 获取该租户的所有 user_id
            cursor.execute("""
                SELECT user_id FROM tenant_users
                WHERE tenant_id = ? AND status = 1
            """, (tenant_id,))
            user_ids = [row["user_id"] for row in cursor.fetchall()]

            if not user_ids:
                return {"total_tokens": 0, "total_sessions": 0, "active_users": 0}

            placeholders = ",".join("?" for _ in user_ids)

            # Token 总量
            cursor.execute(f"""
                SELECT
                    COALESCE(SUM(total_token_count), 0) as total_tokens,
                    COUNT(DISTINCT session_id) as total_sessions,
                    COUNT(DISTINCT user_id) as active_users
                FROM chat_records
                WHERE user_id IN ({placeholders})
                  AND created_at >= ? AND created_at <= ?
            """, (*user_ids, start_date, end_date))

            row = cursor.fetchone()
            return dict(row) if row else {"total_tokens": 0, "total_sessions": 0, "active_users": 0}

    @staticmethod
    def get_user_usage_detail(
        tenant_id: str,
        start_date: str,
        end_date: str,
    ) -> List[Dict[str, Any]]:
        """获取租户下每个用户的用量明细"""
        with get_db_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                SELECT
                    cr.user_id,
                    u.username,
                    COALESCE(SUM(cr.total_token_count), 0) as total_tokens,
                    COUNT(DISTINCT cr.session_id) as total_sessions,
                    MAX(cr.created_at) as last_active
                FROM tenant_users tu
                JOIN chat_records cr ON cr.user_id = tu.user_id
                LEFT JOIN users u ON u.user_id = tu.user_id
                WHERE tu.tenant_id = ? AND tu.status = 1
                  AND cr.created_at >= ? AND cr.created_at <= ?
                GROUP BY cr.user_id, u.username
                ORDER BY total_tokens DESC
            """, (tenant_id, start_date, end_date))

            return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def get_token_trend(
        tenant_id: str,
        start_date: str,
        end_date: str,
    ) -> List[Dict[str, Any]]:
        """获取每日 token 用量趋势"""
        with get_db_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                SELECT
                    tu.user_id FROM tenant_users tu
                WHERE tu.tenant_id = ? AND tu.status = 1
            """, (tenant_id,))
            user_ids = [row["user_id"] for row in cursor.fetchall()]

            if not user_ids:
                return []

            placeholders = ",".join("?" for _ in user_ids)

            cursor.execute(f"""
                SELECT
                    DATE(created_at) as date,
                    COALESCE(SUM(total_token_count), 0) as tokens,
                    COUNT(DISTINCT session_id) as sessions
                FROM chat_records
                WHERE user_id IN ({placeholders})
                  AND created_at >= ? AND created_at <= ?
                GROUP BY DATE(created_at)
                ORDER BY date
            """, (*user_ids, start_date, end_date))

            return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def get_personal_daily_usage(user_id: str, days: int = 30) -> List[Dict[str, Any]]:
        """获取个人每日用量（公共用户）"""
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    DATE(created_at) as date,
                    COALESCE(SUM(total_token_count), 0) as tokens,
                    COUNT(DISTINCT session_id) as sessions
                FROM chat_records
                WHERE user_id = ? AND created_at >= ?
                GROUP BY DATE(created_at)
                ORDER BY date
            """, (user_id, start_date))

            return [dict(row) for row in cursor.fetchall()]
