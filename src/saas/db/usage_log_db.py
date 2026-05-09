"""
用量统计查询

从 chat_records 聚合 token 使用数据，用于计费和报告。
"""

from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from loguru import logger

from src.db.database import get_db_connection


class UsageLogDB:
    """用量统计数据库访问类 - 从 chat_records 表聚合数据"""

    @staticmethod
    def get_tenant_usage(
        tenant_id: str,
        start_date: str,
        end_date: str,
    ) -> Dict[str, Any]:
        """获取租户在指定时间范围内的总用量"""
        with get_db_connection() as conn:
            cursor = conn.cursor()

            # 优先通过 chat_records.tenant_id 直接查询（更准确）
            cursor.execute("""
                SELECT
                    COALESCE(SUM(total_token_count), 0) as total_tokens,
                    COALESCE(SUM(prompt_tokens), 0) as input_tokens,
                    COALESCE(SUM(completion_tokens), 0) as output_tokens,
                    COALESCE(SUM(cached_input_tokens), 0) as cached_tokens,
                    COUNT(DISTINCT session_id) as total_sessions,
                    COUNT(DISTINCT user_id) as active_users,
                    COUNT(*) as total_conversations
                FROM chat_records
                WHERE tenant_id = %s
                  AND created_at >= %s AND created_at <= %s
            """, (tenant_id, start_date, end_date))

            row = cursor.fetchone()
            result = dict(row) if row else {"total_tokens": 0, "total_sessions": 0, "active_users": 0}
            if result.get("total_tokens", 0) > 0 and result.get("total_sessions", 0) > 0:
                result["avg_tokens_per_session"] = round(result["total_tokens"] / result["total_sessions"])
            else:
                result["avg_tokens_per_session"] = 0
            return result

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
                    COALESCE(SUM(cr.prompt_tokens), 0) as input_tokens,
                    COALESCE(SUM(cr.completion_tokens), 0) as output_tokens,
                    COALESCE(SUM(cr.cached_input_tokens), 0) as cached_tokens,
                    COUNT(DISTINCT cr.session_id) as total_sessions,
                    COUNT(*) as total_conversations,
                    MAX(cr.created_at) as last_active
                FROM chat_records cr
                LEFT JOIN users u ON cr.user_id = u.user_id
                WHERE cr.tenant_id = %s
                  AND cr.created_at >= %s AND cr.created_at <= %s
                GROUP BY cr.user_id, u.username
                ORDER BY total_tokens DESC
            """, (tenant_id, start_date, end_date))

            results = [dict(row) for row in cursor.fetchall()]
            for r in results:
                if r.get("total_tokens", 0) > 0 and r.get("total_sessions", 0) > 0:
                    r["avg_tokens_per_session"] = round(r["total_tokens"] / r["total_sessions"])
                else:
                    r["avg_tokens_per_session"] = 0
            return results

    @staticmethod
    def get_token_trend(
        tenant_id: str,
        start_date: str,
        end_date: str,
    ) -> List[Dict[str, Any]]:
        """获取每日 token 用量趋势（含 input/output/cached 分项）"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    DATE(created_at) as date,
                    COALESCE(SUM(total_token_count), 0) as tokens,
                    COALESCE(SUM(prompt_tokens), 0) as input_tokens,
                    COALESCE(SUM(completion_tokens), 0) as output_tokens,
                    COALESCE(SUM(cached_input_tokens), 0) as cached_tokens,
                    COUNT(DISTINCT session_id) as sessions,
                    COUNT(*) as conversations
                FROM chat_records
                WHERE tenant_id = %s
                  AND created_at >= %s AND created_at <= %s
                GROUP BY DATE(created_at)
                ORDER BY date
            """, (tenant_id, start_date, end_date))

            return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def get_model_usage(
        tenant_id: str,
        start_date: str,
        end_date: str,
    ) -> List[Dict[str, Any]]:
        """获取按模型分组的用量统计"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    COALESCE(model, 'unknown') as model,
                    COALESCE(provider, 'unknown') as provider,
                    COUNT(*) as conversation_count,
                    COALESCE(SUM(total_token_count), 0) as total_tokens,
                    COALESCE(SUM(prompt_tokens), 0) as input_tokens,
                    COALESCE(SUM(completion_tokens), 0) as output_tokens,
                    COALESCE(SUM(cached_input_tokens), 0) as cached_tokens,
                    COALESCE(SUM(duration_ms), 0) as total_duration_ms,
                    AVG(agent_iterations) as avg_iterations
                FROM chat_records
                WHERE tenant_id = %s
                  AND created_at >= %s AND created_at <= %s
                GROUP BY model, provider
                ORDER BY total_tokens DESC
            """, (tenant_id, start_date, end_date))

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
                    COALESCE(SUM(prompt_tokens), 0) as input_tokens,
                    COALESCE(SUM(completion_tokens), 0) as output_tokens,
                    COUNT(DISTINCT session_id) as sessions
                FROM chat_records
                WHERE user_id = %s AND created_at >= %s
                GROUP BY DATE(created_at)
                ORDER BY date
            """, (user_id, start_date))

            return [dict(row) for row in cursor.fetchall()]
