"""
obs_traces.total_cost 观测成本闭环单元测试（不依赖 PG，mock 连接层）。

覆盖双时序与降级：
1. trace 先落库、计费后完成：update_total_cost UPDATE 语句与参数正确
2. 计费先完成、trace 后 UPSERT：INSERT/UPSERT 携带 total_cost（内存回写路径）
3. worker 事务在途（UPDATE 0 行）：pending 补丁由随后的 UPSERT 事务内补写
4. 重复回填：幂等（第二次 UPDATE 同值，不登记 pending）
5. 失败降级：update 抛异常时 save() 主流程不受影响、返回值不变、降级日志无敏感值
6. 有界性：pending 队列超界时按插入顺序丢弃最旧条目
"""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from loguru import logger

from src.core.trace_collector import TraceCollector
from src.core.trace_persist import (
    _do_persist,
    _pending_total_cost_updates,
    _remember_pending_total_cost,
    update_total_cost,
)
from src.services.session_record import SessionRecordService

pytestmark = pytest.mark.unit


@contextmanager
def _logs_cm(cursor):
    """mock get_logs_connection 返回的 context manager"""
    yield cursor


@pytest.fixture(autouse=True)
def _clean_pending_total_cost():
    """每个用例前后清空进程内 pending 补丁，避免用例间残留"""
    _pending_total_cost_updates.clear()
    yield
    _pending_total_cost_updates.clear()


# ============================================================
# 时序一：trace 先落库、计费后完成 → UPDATE 已落库行
# ============================================================


def test_update_total_cost_sql_and_params_when_row_exists():
    """UPDATE SQL 与参数正确，commit 被调用，不登记 pending"""
    cursor = MagicMock()
    cursor.rowcount = 1

    with patch("src.db.database.get_logs_connection", return_value=_logs_cm(cursor)):
        update_total_cost("tr_abc123", 1.23)

    cursor.execute.assert_called_once()
    sql_arg, params_arg = cursor.execute.call_args[0]
    assert "UPDATE obs_traces" in sql_arg
    assert "total_cost = %s" in sql_arg
    assert "trace_id = %s" in sql_arg
    # 参数顺序：(total_cost, trace_id)
    assert params_arg == (1.23, "tr_abc123")
    cursor.commit.assert_called_once()
    # 命中已落库行，无需 pending 补丁
    assert _pending_total_cost_updates == {}


# ============================================================
# 时序二：计费先完成、trace 后 UPSERT → INSERT 携带 total_cost
# ============================================================


def test_upsert_carries_memory_total_cost():
    """内存 trace.total_cost 回写后，UPSERT 以参数携带真实成本（非字面量 0）"""
    collector = TraceCollector("sid", "t1", "user", "input", "chat")
    collector.trace.total_cost = 2.5
    collector.trace.total_tokens = 100
    collector.trace.duration_ms = 800

    cursor = MagicMock()
    with patch("src.db.database.get_logs_connection", return_value=_logs_cm(cursor)):
        _do_persist(collector.trace)

    sql_arg, params_arg = cursor.execute.call_args_list[0][0]
    assert "INSERT INTO obs_traces" in sql_arg
    # 18 个业务参数全部参数化（total_cost 不再是字面量 0）
    assert sql_arg.count("%s") == 18
    # ON CONFLICT 分支同步覆盖 total_cost
    assert "total_cost = EXCLUDED.total_cost" in sql_arg
    # 参数位置：..., total_tokens(9), total_cost(10), duration_ms(11), ...
    assert params_arg[9] == 100
    assert params_arg[10] == 2.5
    assert params_arg[11] == 800
    cursor.commit.assert_called_once()


def test_update_total_cost_zero_row_registers_pending_replayed_by_upsert():
    """worker 事务在途（UPDATE 0 行）：登记 pending，随后的 UPSERT 提交前补写"""
    # 第一步：UPDATE 未命中（trace 行尚未提交可见）
    update_cursor = MagicMock()
    update_cursor.rowcount = 0
    with patch("src.db.database.get_logs_connection", return_value=_logs_cm(update_cursor)):
        update_total_cost("tr_race", 0.75)
    assert _pending_total_cost_updates == {"tr_race": 0.75}

    # 第二步：worker UPSERT 落库，同一事务内补写 pending 成本
    trace = TraceCollector("sid", "t1", "user", "input", "chat").trace
    trace.trace_id = "tr_race"
    persist_cursor = MagicMock()
    with patch("src.db.database.get_logs_connection", return_value=_logs_cm(persist_cursor)):
        _do_persist(trace)

    calls = persist_cursor.execute.call_args_list
    assert "INSERT INTO obs_traces" in calls[0][0][0]
    # 第二条语句即 pending 补写 UPDATE
    replay_sql, replay_params = calls[1][0]
    assert "UPDATE obs_traces" in replay_sql
    assert "total_cost = %s" in replay_sql
    assert replay_params == (0.75, "tr_race")
    persist_cursor.commit.assert_called_once()
    # 补丁已消费，不留残留
    assert "tr_race" not in _pending_total_cost_updates


# ============================================================
# 幂等：重复回填
# ============================================================


def test_update_total_cost_repeat_is_idempotent():
    """第二次回填同值：执行相同 UPDATE，不登记 pending，无副作用叠加"""
    cursor = MagicMock()
    cursor.rowcount = 1

    # side_effect 保证每次调用返回全新 context manager（@contextmanager 一次性）
    with patch(
        "src.db.database.get_logs_connection",
        side_effect=lambda: _logs_cm(cursor),
    ):
        update_total_cost("tr_dup", 3.21)
        update_total_cost("tr_dup", 3.21)

    assert cursor.execute.call_count == 2
    first = cursor.execute.call_args_list[0][0]
    second = cursor.execute.call_args_list[1][0]
    assert first == second
    assert _pending_total_cost_updates == {}
    cursor.commit.assert_called()


# ============================================================
# 失败降级：update 层
# ============================================================


def test_update_total_cost_failure_registers_pending_no_raise():
    """execute 抛异常时不外抛，登记 pending 供后续 UPSERT 补写"""
    cursor = MagicMock()
    cursor.execute.side_effect = Exception("DB connection lost")

    with patch("src.db.database.get_logs_connection", return_value=_logs_cm(cursor)):
        # 不应抛异常
        update_total_cost("tr_down", 0.5)

    cursor.commit.assert_not_called()
    assert _pending_total_cost_updates == {"tr_down": 0.5}


def test_update_total_cost_connection_failure_no_raise():
    """连接失败（追踪库未配置等）也不抛异常"""
    with patch(
        "src.db.database.get_logs_connection",
        side_effect=RuntimeError("追踪库未配置"),
    ):
        update_total_cost("tr_abc123", 1.0)


# ============================================================
# 失败降级：session_record.save() 层
# ============================================================


def _make_record_service(collector):
    record = SessionRecordService("sid_cost", "user_1", "机密用户输入内容", tenant_id="t1")
    record.trace_collector = collector
    record.add_llm_usage({
        "prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
    })
    record.assistant_message = "机密助手回复内容"
    return record


def _mock_chat_record_create():
    saved = []

    def _fake_create(**kwargs):
        saved.append(kwargs)
        return {"record_id": "rec_cost_1"}

    return patch("src.db.models.ChatRecordDB.create", side_effect=_fake_create), saved


def _mock_billing_cost(cost):
    return patch(
        "src.services.session_record.calculate_llm_credit_cost_with_breakdown",
        return_value=(cost, {}),
    )


def test_save_backfills_memory_trace_and_calls_update_total_cost():
    """save() 计算出 credit_cost 后：内存 trace 回写 + update_total_cost 回填"""
    collector = TraceCollector("sid_cost", "t1", "user_1", "机密用户输入内容", "chat")
    record = _make_record_service(collector)

    create_patch, _ = _mock_chat_record_create()
    with create_patch, _mock_billing_cost(0.42), \
            patch("src.core.trace_persist.update_total_cost") as update_mock:
        result = record.save()

    # 主流程返回值不变
    assert result == {"record_id": "rec_cost_1"}
    # 内存 trace 已回写（覆盖 worker 未处理时序）
    assert collector.trace.total_cost == 0.42
    # DB 回填携带相同金额（覆盖 worker 已处理时序）
    update_mock.assert_called_once_with(collector.trace_id, 0.42)


def test_save_degradation_update_raises_does_not_affect_result():
    """update_total_cost 抛异常：save() 返回值不变，降级 debug 日志无敏感值"""
    collector = TraceCollector("sid_cost", "t1", "user_1", "机密用户输入内容", "chat")
    record = _make_record_service(collector)

    messages = []
    handler_id = logger.add(lambda m: messages.append(str(m)), level="DEBUG")
    try:
        create_patch, _ = _mock_chat_record_create()
        with create_patch, _mock_billing_cost(0.42), \
                patch(
                    "src.core.trace_persist.update_total_cost",
                    side_effect=RuntimeError("观测库暂时不可用"),
                ) as update_mock:
            result = record.save()
    finally:
        logger.remove(handler_id)

    # update 被尝试过，但 save() 主流程与返回值不受影响
    update_mock.assert_called_once_with(collector.trace_id, 0.42)
    assert result == {"record_id": "rec_cost_1"}

    # 降级日志只含失败事实，不含用户消息 / 助手回复等敏感内容
    degraded = [m for m in messages if "total_cost" in m]
    assert degraded, "应产生 total_cost 回填失败的 debug 日志"
    for msg in degraded:
        assert "机密用户输入内容" not in msg
        assert "机密助手回复内容" not in msg


def test_save_skips_backfill_without_trace_collector_or_zero_cost():
    """无 trace_collector 或 credit_cost=0 时跳过回填（0 已是默认值，无需写）"""
    # 场景一：无 trace_collector（merged_follower 等未走 agent 的路径）
    record = _make_record_service(collector=None)
    create_patch, _ = _mock_chat_record_create()
    with create_patch, _mock_billing_cost(0.42), \
            patch("src.core.trace_persist.update_total_cost") as update_mock:
        assert record.save() == {"record_id": "rec_cost_1"}
    update_mock.assert_not_called()

    # 场景二：credit_cost = 0（单价缺失 / 计费降级）
    collector = TraceCollector("sid_cost", "t1", "user_1", "input", "chat")
    record2 = _make_record_service(collector)
    create_patch2, _ = _mock_chat_record_create()
    with create_patch2, _mock_billing_cost(0.0), \
            patch("src.core.trace_persist.update_total_cost") as update_mock2:
        assert record2.save() == {"record_id": "rec_cost_1"}
    update_mock2.assert_not_called()
    assert collector.trace.total_cost == 0


# ============================================================
# 有界性：pending 队列超界丢弃
# ============================================================


def test_pending_total_cost_updates_are_bounded():
    """超过上限时按插入顺序丢弃最旧条目，进程内缓存不无限增长"""
    with patch("src.core.trace_persist._PENDING_TOTAL_COST_MAX", 3):
        for i in range(5):
            _remember_pending_total_cost(f"tr_bounded_{i}", float(i))

    # 最旧两条被丢弃，仅保留最新 3 条
    assert len(_pending_total_cost_updates) == 3
    assert set(_pending_total_cost_updates) == {
        "tr_bounded_2", "tr_bounded_3", "tr_bounded_4",
    }
    assert _pending_total_cost_updates["tr_bounded_4"] == 4.0
