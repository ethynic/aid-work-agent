"""
monitor.py list_session_traces 撤回标记按 message_id 精确匹配的单元测试。

覆盖场景：
1. user_message_id 命中 recall_map（full）→ recall_type='full'
2. user_message_id 命中 recall_map（partial）→ recall_type='partial'
3. user_message_id 为 NULL（历史 trace）→ recall_type=None
4. user_message_id 不在 recall_map（未撤回）→ recall_type=None
5. 两条内容相同的消息，一条撤回一条未撤回 → 各自按 message_id 正确标记（回归本次 bug）
"""

import json
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.api


# ============== 测试辅助 ==============

def _make_trace_row(
    trace_id: str,
    session_id: str,
    input_text: str,
    user_message_id,
):
    """构造 obs_traces 行"""
    return {
        "trace_id": trace_id,
        "session_id": session_id,
        "input": input_text,
        "output": "回复内容",
        "status": "completed",
        "duration_ms": 100,
        "total_tokens": 50,
        "tool_calls_count": 0,
        "agent_iterations": 1,
        "tags": [],
        "source_type": "wecom_kf",
        "created_at": datetime(2026, 7, 2, 10, 0, 0),
        "user_message_id": user_message_id,
    }


def _make_msg_row(message_id: str, is_recalled: bool, recalled_parts=None):
    """构造 channel_messages 撤回查询行"""
    return {
        "message_id": message_id,
        "metadata": json.dumps(
            {"recalled_part_msgids": recalled_parts or []},
            ensure_ascii=False,
        ),
        "is_recalled": is_recalled,
    }


@contextmanager
def _mock_logs_conn(rows):
    """mock get_logs_connection：返回 rows 作为 fetchall 结果"""
    cursor = MagicMock()
    cursor.fetchall.return_value = rows
    cursor.fetchone.return_value = None
    yield cursor


@contextmanager
def _mock_db_conn(rows):
    """mock get_db_connection：返回 rows 作为 fetchall 结果"""
    cursor = MagicMock()
    cursor.fetchall.return_value = rows
    yield cursor


def _patch_admin():
    """mock 平台管理员权限校验"""
    return (
        patch("src.api.monitor.get_current_user", return_value={"role": "platform_admin"}),
        patch("src.api.monitor.is_platform_admin", return_value=True),
    )


# ============== 测试用例 ==============


@pytest.mark.asyncio
async def test_recall_full_match_by_message_id():
    """用例1：user_message_id 命中 recall_map（full）→ recall_type='full'"""
    from src.api.monitor import list_session_traces

    trace_rows = [
        _make_trace_row("tr_1", "sid1", "在吗？", "msg_abc"),
    ]
    msg_rows = [
        _make_msg_row("msg_abc", is_recalled=True),
    ]

    p1, p2 = _patch_admin()
    with p1, p2, \
            patch("src.db.database.get_logs_connection", return_value=_mock_logs_conn(trace_rows)), \
            patch("src.db.database.get_db_connection", return_value=_mock_db_conn(msg_rows)):
        result = await list_session_traces(session_id="sid1", request=MagicMock())

    assert result["success"] is True
    assert len(result["traces"]) == 1
    assert result["traces"][0].recall_type == "full"


@pytest.mark.asyncio
async def test_include_intermediate_false_filters_only_interrupted_traces():
    from src.api.monitor import list_session_traces

    normal = _make_trace_row("tr_message", "sid1", "正常", "msg_1")
    interrupted = _make_trace_row("tr_interrupted", "sid1", "追加", None)
    interrupted.update({
        "output": None,
        "status": "cancelled",
        "metadata": {
            "termination_reason": "message_merged",
            "merge_role": "merged_follower",
        },
    })
    p1, p2 = _patch_admin()
    with p1, p2, \
            patch("src.db.database.get_logs_connection", return_value=_mock_logs_conn([normal, interrupted])), \
            patch("src.db.database.get_db_connection", return_value=_mock_db_conn([])):
        result = await list_session_traces(
            session_id="sid1", request=MagicMock(), include_intermediate=False
        )

    assert [trace.trace_id for trace in result["traces"]] == ["tr_message"]


@pytest.mark.asyncio
async def test_recall_partial_match_by_message_id():
    """用例2：user_message_id 命中 recall_map（partial）→ recall_type='partial'"""
    from src.api.monitor import list_session_traces

    trace_rows = [
        _make_trace_row("tr_1", "sid1", "你好\n想去天眼", "msg_partial"),
    ]
    msg_rows = [
        _make_msg_row(
            "msg_partial",
            is_recalled=False,
            recalled_parts=["m1"],
        ),
    ]

    p1, p2 = _patch_admin()
    with p1, p2, \
            patch("src.db.database.get_logs_connection", return_value=_mock_logs_conn(trace_rows)), \
            patch("src.db.database.get_db_connection", return_value=_mock_db_conn(msg_rows)):
        result = await list_session_traces(session_id="sid1", request=MagicMock())

    assert result["success"] is True
    assert len(result["traces"]) == 1
    assert result["traces"][0].recall_type == "partial"


@pytest.mark.asyncio
async def test_null_user_message_id_no_recall():
    """用例3：user_message_id 为 NULL（历史 trace）→ recall_type=None"""
    from src.api.monitor import list_session_traces

    trace_rows = [
        _make_trace_row("tr_legacy", "sid1", "历史消息", None),
    ]
    # 即使主库有撤回消息，历史 trace 也不应显示撤回标记
    msg_rows = [
        _make_msg_row("msg_abc", is_recalled=True),
    ]

    p1, p2 = _patch_admin()
    with p1, p2, \
            patch("src.db.database.get_logs_connection", return_value=_mock_logs_conn(trace_rows)), \
            patch("src.db.database.get_db_connection", return_value=_mock_db_conn(msg_rows)):
        result = await list_session_traces(session_id="sid1", request=MagicMock())

    assert result["success"] is True
    assert len(result["traces"]) == 1
    assert result["traces"][0].recall_type is None


@pytest.mark.asyncio
async def test_unrecalled_message_no_recall():
    """用例4：user_message_id 不在 recall_map（未撤回）→ recall_type=None"""
    from src.api.monitor import list_session_traces

    trace_rows = [
        _make_trace_row("tr_1", "sid1", "在吗？", "msg_unrecalled"),
    ]
    # 撤回消息列表为空（该 session 没有任何撤回消息）
    msg_rows = []

    p1, p2 = _patch_admin()
    with p1, p2, \
            patch("src.db.database.get_logs_connection", return_value=_mock_logs_conn(trace_rows)), \
            patch("src.db.database.get_db_connection", return_value=_mock_db_conn(msg_rows)):
        result = await list_session_traces(session_id="sid1", request=MagicMock())

    assert result["success"] is True
    assert len(result["traces"]) == 1
    assert result["traces"][0].recall_type is None


@pytest.mark.asyncio
async def test_duplicate_content_distinguished_by_message_id():
    """
    用例5：两条内容相同的消息（回归本次 bug）。

    场景：同一 session 内两条 "[ASR识别结果] 在吗？" 语音消息，
    msg_1 已撤回（is_recalled=True），msg_2 未撤回。
    旧逻辑按归一化内容匹配会导致两条都被标 'full'；
    新逻辑按 message_id 精确匹配，各自正确标记。
    """
    from src.api.monitor import list_session_traces

    same_input = "[ASR识别结果] 在吗？"
    trace_rows = [
        _make_trace_row("tr_1", "sid1", same_input, "msg_1"),
        _make_trace_row("tr_2", "sid1", same_input, "msg_2"),
    ]
    # 只有 msg_1 撤回
    msg_rows = [
        _make_msg_row("msg_1", is_recalled=True),
    ]

    p1, p2 = _patch_admin()
    with p1, p2, \
            patch("src.db.database.get_logs_connection", return_value=_mock_logs_conn(trace_rows)), \
            patch("src.db.database.get_db_connection", return_value=_mock_db_conn(msg_rows)):
        result = await list_session_traces(session_id="sid1", request=MagicMock())

    assert result["success"] is True
    assert len(result["traces"]) == 2
    # 按 trace_id 找回对应项断言（避免依赖返回顺序）
    by_trace = {t.trace_id: t for t in result["traces"]}
    assert by_trace["tr_1"].recall_type == "full"  # msg_1 撤回
    assert by_trace["tr_2"].recall_type is None     # msg_2 未撤回
