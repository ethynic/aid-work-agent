"""
外部接待客户相关 DB 层单元测试

覆盖：
- UserDB.list_external_users 组合粒度 SQL（wecom_kf 按客服账号拆分、其它渠道折叠）
- ChannelSessionManager.list_sessions 的 channel_chat_id 三种取值过滤分支
"""

import pytest
from unittest.mock import MagicMock, patch


class TestListExternalUsersCombinationGranularity:
    """UserDB.list_external_users 组合粒度 SQL 与返回行归一化"""

    def _run(self, rows=None, count=0, **kwargs):
        from src.db.models import UserDB

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {"cnt": count}
        mock_cursor.fetchall.return_value = rows or []
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("src.db.models.get_db_connection") as mock_get_db:
            mock_get_db.return_value.__enter__.return_value = mock_conn
            result = UserDB.list_external_users(**kwargs)
            return result, mock_cursor.execute.call_args_list

    def test_wecom_kf_split_and_other_channels_folded(self):
        """SQL 应仅对 wecom_kf 按 channel_chat_id 拆分，其它渠道折叠为空串"""
        result, calls = self._run(
            tenant_id="tenant_001",
            page=1,
            page_size=20,
        )
        count_sql = calls[0][0][0]
        list_sql = calls[1][0][0]
        # wecom_kf CASE 分组表达式同时出现在总数与列表查询中
        assert "CASE WHEN channel_type = 'wecom_kf' THEN COALESCE(channel_chat_id, '') ELSE '' END" in count_sql
        assert "CASE WHEN channel_type = 'wecom_kf' THEN COALESCE(channel_chat_id, '') ELSE '' END" in list_sql
        # 分组三要素：user_id / channel_type / CASE 折叠
        assert "GROUP BY user_id, channel_type," in list_sql
        # 外层 SELECT 带出组合标识字段
        assert "cs.channel_type" in list_sql
        assert "cs.channel_chat_id" in list_sql
        # 总数与列表同一套 JOIN 结构（cs 子查询 + customer_referrals）
        assert "LEFT JOIN (" in count_sql and "customer_referrals cr" in count_sql
        assert "LEFT JOIN (" in list_sql and "customer_referrals cr" in list_sql
        # 分页参数追加在 where 参数之后
        assert calls[1][0][1][-2:] == [20, 0]

    def test_referrer_filter_adds_condition(self):
        """referrer_user_id 过滤应加入 cr.referrer_user_id 条件并在参数中透传"""
        _, calls = self._run(
            tenant_id="tenant_001",
            referrer_user_id="emp_001",
            page=1,
            page_size=20,
        )
        count_sql = calls[0][0][0]
        count_params = calls[0][0][1]
        assert "cr.referrer_user_id = %s" in count_sql
        assert "emp_001" in count_params

    def test_username_search_adds_conditions(self):
        """username 搜索应追加 username/nickname ILIKE 条件"""
        _, calls = self._run(
            tenant_id="tenant_001",
            username="张三",
            page=1,
            page_size=20,
        )
        count_sql = calls[0][0][0]
        count_params = calls[0][0][1]
        assert "u.username ILIKE %s OR u.nickname ILIKE %s" in count_sql
        assert "%张三%" in count_params

    def test_normalizes_channel_chat_id_and_referrer_name(self):
        """返回行归一化：无会话用户 channel_chat_id 为空串；引流人名称正确解析"""
        rows = [
            {
                "user_id": "u1", "username": "c1", "nickname": "客户一",
                "avatar_url": None, "source": "wecom_kf", "tenant_id": "tenant_001",
                "created_at": "2026-01-01", "channel_type": None,
                "channel_chat_id": None, "first_session_at": None,
                "last_session_at": None, "referrer_user_id": "emp_001",
                "referrer_nickname": "李老师", "referrer_username": "lilaoshi",
            },
            {
                "user_id": "u2", "username": "c2", "nickname": None,
                "avatar_url": None, "source": "wecom_kf", "tenant_id": "tenant_001",
                "created_at": "2026-01-01", "channel_type": "wecom_kf",
                "channel_chat_id": "open_kfid_A", "first_session_at": None,
                "last_session_at": None, "referrer_user_id": None,
                "referrer_nickname": None, "referrer_username": None,
            },
        ]
        result, _ = self._run(
            rows=rows,
            count=2,
            tenant_id="tenant_001",
            page=1,
            page_size=20,
        )
        users = result["users"]
        # 无会话用户：channel_type 为 None、channel_chat_id 归一为空串
        assert users[0]["channel_chat_id"] == ""
        # 引流人名称：优先昵称
        assert users[0]["referrer_name"] == "李老师"
        # 无引流人：referrer_name 为 None
        assert users[1]["referrer_name"] is None
        # wecom_kf 有账号的组合保留原始 channel_chat_id
        assert users[1]["channel_chat_id"] == "open_kfid_A"
        assert result["total"] == 2


class TestListSessionsChannelChatIdFilter:
    """ChannelSessionManager.list_sessions 的 channel_chat_id 过滤分支"""

    def _run(self, channel_chat_id=None):
        from src.channels.session import ChannelSessionManager

        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("src.channels.session.get_db_connection") as mock_get_db:
            mock_get_db.return_value.__enter__.return_value = mock_conn
            manager = ChannelSessionManager()
            manager.list_sessions(
                tenant_id="tenant_001",
                user_id="user_001",
                channel_chat_id=channel_chat_id,
                limit=50,
            )
            sql, params = mock_cursor.execute.call_args[0]
            return sql, params

    def test_none_does_not_add_channel_chat_id_condition(self):
        """channel_chat_id 为 None 时不追加过滤条件（保持旧行为，零影响现有调用方）"""
        sql, params = self._run(None)
        assert "channel_chat_id" not in sql
        assert params == ["tenant_001", "user_001"]

    def test_empty_string_matches_null_or_empty(self):
        """空串生成 NULL-or-empty 条件（兼容 legacy 未拆分会话）"""
        sql, params = self._run("")
        assert "(channel_chat_id IS NULL OR channel_chat_id = '')" in sql
        assert params == ["tenant_001", "user_001"]

    def test_non_empty_exact_match(self):
        """非空值精确匹配 channel_chat_id"""
        sql, params = self._run("open_kfid_A")
        assert "channel_chat_id = %s" in sql
        assert "open_kfid_A" in params
        # 参数顺序：tenant_id、user_id、channel_chat_id
        assert params == ["tenant_001", "user_001", "open_kfid_A"]
