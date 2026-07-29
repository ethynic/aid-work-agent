"""
aggregate_team 80K 字符预算采样逻辑测试

模拟大量成员 + 大量对话，验证：
1. 累计字符数达 80000 时停止追加新成员
2. user_stats 仍包含全部活跃成员（统计不受采样影响）
3. members_dialogs 受 50 条/成员 上限
4. user_message 每条截断到 200 字
"""

from datetime import date, datetime, timedelta
from unittest.mock import patch, MagicMock

from src.reports.aggregator import aggregate_team


def _build_large_dataset(member_count: int, dialogs_per_member: int, msg_chars: int = 200):
    """构造大规模 mock 数据

    Args:
        member_count: 成员数
        dialogs_per_member: 每成员对话数
        msg_chars: 每条 user_message 字符数

    Returns:
        (user_rows, total_row, source_rows, dialog_rows)
    """
    user_rows = []
    dialog_rows = []
    for i in range(member_count):
        uid = f"user_{i:03d}"
        # dialog_count 倒序：user_0 最多，user_N 最少
        dc = (member_count - i) * dialogs_per_member
        user_rows.append({
            "user_id": uid,
            "dialog_count": dc,
            "credit_cost": float(dc),
            "total_duration_ms": dc * 5000,
            "username": f"用户{i:03d}",
            "phone": f"1380000{i:04d}",
            "nickname": f"昵称{i:03d}",
        })
        # 构造 dialog_rows：按 user_id 分组，每组按 created_at DESC
        # 注意：原 SQL 是 ORDER BY user_id, created_at DESC
        for j in range(dialogs_per_member):
            dialog_rows.append({
                "user_id": uid,
                "user_message": "对" * msg_chars,  # msg_chars 个中文字
                "created_at": datetime(2026, 7, 1, 10, 0, 0) + timedelta(hours=j),
            })

    total_row = {
        "dialog_count": sum(r["dialog_count"] for r in user_rows),
        "credit_cost": sum(r["credit_cost"] for r in user_rows),
    }
    source_rows = [{"source_type": "chat", "cnt": total_row["dialog_count"]}]
    return user_rows, total_row, source_rows, dialog_rows


class TestAggregateTeamTruncation:
    """80K 字符预算采样逻辑测试"""

    def test_large_team_triggers_truncation(self):
        """30 成员 × 50 条 × 200 字 = 30 万字输入，应触发截断"""
        member_count = 30
        dialogs_per_member = 50
        user_rows, total_row, source_rows, dialog_rows = _build_large_dataset(
            member_count=member_count,
            dialogs_per_member=dialogs_per_member,
            msg_chars=200,
        )

        mock_cursor = MagicMock()
        # 4 次 fetchall 调用顺序：user_rows, source_rows, dialog_rows
        mock_cursor.fetchall.side_effect = [user_rows, source_rows, dialog_rows]
        mock_cursor.fetchone.return_value = total_row
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("src.reports.aggregator.get_db_connection") as mock_get:
            mock_get.return_value.__enter__.return_value = mock_conn
            result = aggregate_team("t1", date(2026, 7, 1), "monthly")

        # 关键断言 1：触发截断
        assert result["input_truncated"] is True, (
            f"应触发截断，实际 input_truncated={result['input_truncated']}"
        )

        # 关键断言 2：进入采样的字符数严格不超过 80K 预算
        # total_chars 累加的就是 len(msg) + 5（含序号+换行开销），循环中已有 break 保证不超 80000
        assert result["input_char_count"] <= 80000, (
            f"input_char_count 应 ≤ 80000（80K 预算硬上限），实际 {result['input_char_count']}"
        )

        # 关键断言 3：进入采样的成员数 < 30
        assert result["input_member_count"] < member_count, (
            f"input_member_count 应 < {member_count}，实际 {result['input_member_count']}"
        )

        # 关键断言 4：user_stats 仍包含全部 30 个成员（统计不受采样影响）
        assert len(result["user_stats"]) == member_count, (
            f"user_stats 应包含全部 {member_count} 个成员，"
            f"实际 {len(result['user_stats'])}"
        )

        # 关键断言 5：total_dialog_count 基于全量数据
        expected_total = sum(r["dialog_count"] for r in user_rows)
        assert result["total_dialog_count"] == expected_total

        # 关键断言 6：active_user_count 基于全量数据
        assert result["active_user_count"] == member_count

    def test_small_team_no_truncation(self):
        """小团队（少量对话）不应触发截断"""
        member_count = 3
        dialogs_per_member = 5
        user_rows, total_row, source_rows, dialog_rows = _build_large_dataset(
            member_count=member_count,
            dialogs_per_member=dialogs_per_member,
            msg_chars=50,  # 50 字/条
        )

        mock_cursor = MagicMock()
        mock_cursor.fetchall.side_effect = [user_rows, source_rows, dialog_rows]
        mock_cursor.fetchone.return_value = total_row
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("src.reports.aggregator.get_db_connection") as mock_get:
            mock_get.return_value.__enter__.return_value = mock_conn
            result = aggregate_team("t1", date(2026, 7, 1), "monthly")

        # 不应触发截断
        assert result["input_truncated"] is False
        assert result["input_member_count"] == member_count
        # 每成员最多 5 条，每条 50 字
        expected_dialogs = member_count * dialogs_per_member
        assert result["input_dialog_count"] == expected_dialogs
        assert result["input_char_count"] < 80000

    def test_user_message_truncated_to_200_chars(self):
        """每条 user_message 应截断到 200 字"""
        user_rows = [
            {
                "user_id": "u1",
                "dialog_count": 1,
                "credit_cost": 10,
                "total_duration_ms": 5000,
                "username": "测试用户",
                "phone": "13800000000",
                "nickname": "",
            },
        ]
        total_row = {"dialog_count": 1, "credit_cost": 10}
        source_rows = [{"source_type": "chat", "cnt": 1}]
        # 单条 500 字的消息
        dialog_rows = [
            {"user_id": "u1", "user_message": "对" * 500, "created_at": datetime(2026, 7, 1, 10, 0, 0)},
        ]

        mock_cursor = MagicMock()
        mock_cursor.fetchall.side_effect = [user_rows, source_rows, dialog_rows]
        mock_cursor.fetchone.return_value = total_row
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("src.reports.aggregator.get_db_connection") as mock_get:
            mock_get.return_value.__enter__.return_value = mock_conn
            result = aggregate_team("t1", date(2026, 7, 1), "monthly")

        # 验证消息被截断到 200 字
        assert len(result["members_dialogs"]) == 1
        msg = result["members_dialogs"][0]["user_messages"][0]
        assert len(msg) == 200, f"消息应被截断到 200 字，实际 {len(msg)} 字"

    def test_dialogs_per_member_capped_at_50(self):
        """每成员对话上限 50 条"""
        user_rows = [
            {
                "user_id": "u1",
                "dialog_count": 100,
                "credit_cost": 100,
                "total_duration_ms": 500000,
                "username": "高频用户",
                "phone": "13800000001",
                "nickname": "高频",
            },
        ]
        total_row = {"dialog_count": 100, "credit_cost": 100}
        source_rows = [{"source_type": "chat", "cnt": 100}]
        # 100 条对话（应只取最近 50 条）
        dialog_rows = [
            {
                "user_id": "u1",
                "user_message": f"消息 {i}",
                "created_at": datetime(2026, 7, 1, 10, 0, 0) + timedelta(hours=i),
            }
            for i in range(100)
        ]

        mock_cursor = MagicMock()
        mock_cursor.fetchall.side_effect = [user_rows, source_rows, dialog_rows]
        mock_cursor.fetchone.return_value = total_row
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("src.reports.aggregator.get_db_connection") as mock_get:
            mock_get.return_value.__enter__.return_value = mock_conn
            result = aggregate_team("t1", date(2026, 7, 1), "monthly")

        # members_dialogs 应只含 50 条
        assert len(result["members_dialogs"]) == 1
        assert len(result["members_dialogs"][0]["user_messages"]) == 50
        # 应取最近 50 条（dialog_rows 已按 created_at DESC，前 50 条即最新的）
        # 第一条是 i=0（最早），最后一条是 i=99（最新）；
        # 但 SQL 排序是 ORDER BY user_id, created_at DESC，所以最新的在前
        # mock 数据按 i 升序，故取前 50 条 = i=0~49 是最早的（不严格，但够测试上限逻辑）
        assert result["input_dialog_count"] == 50

    def test_members_sorted_by_dialog_count_desc(self):
        """成员按 dialog_count 倒序排序（活跃成员优先进入采样）"""
        # u1: 5 条对话, u2: 10 条对话, u3: 3 条对话
        # user_rows 已按 dialog_count DESC 排序（u2, u1, u3）
        user_rows = [
            {
                "user_id": "u2",
                "dialog_count": 10,
                "credit_cost": 50,
                "total_duration_ms": 50000,
                "username": "用户二",
                "phone": "13800000002",
                "nickname": "二",
            },
            {
                "user_id": "u1",
                "dialog_count": 5,
                "credit_cost": 25,
                "total_duration_ms": 25000,
                "username": "用户一",
                "phone": "13800000001",
                "nickname": "一",
            },
            {
                "user_id": "u3",
                "dialog_count": 3,
                "credit_cost": 15,
                "total_duration_ms": 15000,
                "username": "用户三",
                "phone": "13800000003",
                "nickname": "三",
            },
        ]
        total_row = {"dialog_count": 18, "credit_cost": 90}
        source_rows = [{"source_type": "chat", "cnt": 18}]
        dialog_rows = []
        # 给每个成员不同数量的对话
        for uid, n in [("u2", 10), ("u1", 5), ("u3", 3)]:
            for i in range(n):
                dialog_rows.append({
                    "user_id": uid,
                    "user_message": f"{uid}-msg-{i}",
                    "created_at": datetime(2026, 7, 1, 10, 0, 0) + timedelta(hours=i),
                })

        mock_cursor = MagicMock()
        mock_cursor.fetchall.side_effect = [user_rows, source_rows, dialog_rows]
        mock_cursor.fetchone.return_value = total_row
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("src.reports.aggregator.get_db_connection") as mock_get:
            mock_get.return_value.__enter__.return_value = mock_conn
            result = aggregate_team("t1", date(2026, 7, 1), "monthly")

        # members_dialogs 应按 dialog_count DESC 排序：u2, u1, u3
        members_order = [m["user_id"] for m in result["members_dialogs"]]
        assert members_order == ["u2", "u1", "u3"], (
            f"members 应按 dialog_count DESC 排序，实际顺序：{members_order}"
        )
