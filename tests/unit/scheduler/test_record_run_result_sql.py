"""record_run_result 生产 SQL 真实执行回归（Phase 3 三轮评审 P1）。

此前参数元组与占位符顺序错位（WHERE task_id 绑到了 error_code），
UPDATE 静默命中 0 行——mock rowcount=1 的测试无法发现。本组用例把生产
SQL（从 mock 捕获）在 sqlite 内存库真实执行，断言最终行内容：
- once + scheduled 终态 → completed、统计正确；
- once + manual 终态 → 不消费额度（status 保持）；
- recurring + CREDIT_BLOCKED → paused + pause_reason + 四个 pending 清空；
- recurring + 纯成功 → 仅统计累加、pending 不动。
"""

import sqlite3
from unittest.mock import MagicMock, patch

from src.scheduler.db import ScheduledTaskDB

_COLUMNS = ("task_id TEXT PRIMARY KEY, schedule_type TEXT, status TEXT, "
            "pause_reason TEXT, pending_occurrence_key TEXT, pending_trigger TEXT, "
            "pending_retry_at TEXT, pending_snapshot TEXT, "
            "total_runs INTEGER DEFAULT 0, success_count INTEGER DEFAULT 0, "
            "fail_count INTEGER DEFAULT 0, last_run_at TEXT, last_status TEXT, "
            "updated_at TEXT")


def _execute_real_sql(run_status, trigger, error_code=None, row=None):
    """捕获生产 SQL+参数后在 sqlite 真实执行，返回更新后的行。"""
    cursor = MagicMock()
    cursor.rowcount = 1
    with patch("src.scheduler.db.get_db_connection") as mock_conn:
        mock_conn.return_value.__enter__.return_value.cursor.return_value = cursor
        ScheduledTaskDB.record_run_result(
            "sched_1", run_status=run_status, trigger=trigger,
            error_code=error_code, finished_at="2026-10-28 09:02:00")
    sql, params = cursor.execute.call_args.args
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE scheduled_tasks (" + _COLUMNS + ")")
    conn.execute("INSERT INTO scheduled_tasks (task_id, schedule_type, status, "
                 "pending_occurrence_key, pending_trigger, pending_retry_at, "
                 "pending_snapshot) VALUES (?,?,?,?,?,?,?)",
                 ("sched_1", row["schedule_type"], row.get("status", "active"),
                  row.get("pending_occurrence_key"), row.get("pending_trigger"),
                  row.get("pending_retry_at"), row.get("pending_snapshot")))
    # psycopg2 风格 %s → sqlite ?（逐个替换，避开 %% 转义）；now() 为
    # PostgreSQL 方言，sqlite 用 CURRENT_TIMESTAMP 等价替换（语义相同）
    import re as _re
    sqlite_sql = _re.sub(r"%s", "?", sql.replace("now()", "CURRENT_TIMESTAMP"))
    conn.execute(sqlite_sql, params)
    conn.commit()
    updated = dict(zip([c[0] for c in conn.execute(
        "SELECT * FROM scheduled_tasks").description],
        conn.execute("SELECT * FROM scheduled_tasks").fetchone()))
    conn.close()
    return updated


def test_once_scheduled_success_consumes_and_counts():
    row = _execute_real_sql("completed", "scheduled",
                            row={"schedule_type": "once", "status": "active"})
    assert row["status"] == "completed"          # once 额度消费
    assert row["total_runs"] == 1 and row["success_count"] == 1
    assert row["fail_count"] == 0
    assert row["last_status"] == "completed"


def test_once_manual_success_does_not_consume():
    row = _execute_real_sql("completed", "manual",
                            row={"schedule_type": "once", "status": "active"})
    assert row["status"] == "active"             # manual 不消费 once 额度
    assert row["total_runs"] == 1 and row["success_count"] == 1


def test_recurring_credit_blocked_pauses_and_clears_pending():
    row = _execute_real_sql(
        "failed", "scheduled", error_code="CREDIT_BLOCKED",
        row={"schedule_type": "interval", "status": "active",
             "pending_occurrence_key": "k1", "pending_trigger": "scheduled",
             "pending_retry_at": "2026-10-28 09:00:30",
             "pending_snapshot": '{"text": "..."}'})
    assert row["status"] == "paused"
    assert row["pause_reason"] == "credit_blocked"
    # 四个 pending 字段全部清空（评审复现：暂停后残留导致循环提交）
    assert row["pending_occurrence_key"] is None
    assert row["pending_trigger"] is None
    assert row["pending_retry_at"] is None
    assert row["pending_snapshot"] is None
    assert row["fail_count"] == 1 and row["total_runs"] == 1


def test_recurring_plain_success_keeps_pending():
    row = _execute_real_sql(
        "completed", "scheduled",
        row={"schedule_type": "interval", "status": "active",
             "pending_occurrence_key": "next_occ", "pending_trigger": "scheduled"})
    assert row["status"] == "active"             # 成功不暂停
    # 下一 occurrence 的 pending 不被误清
    assert row["pending_occurrence_key"] == "next_occ"
    assert row["pending_trigger"] == "scheduled"


def test_once_error_keeps_completed_not_paused():
    """once 额度已消费：执行侧错误不置 paused（失败经 last_status 呈现）"""
    row = _execute_real_sql(
        "failed", "scheduled", error_code="CREDIT_BLOCKED",
        row={"schedule_type": "once", "status": "active"})
    assert row["status"] == "completed"
    assert row["pause_reason"] is None
    assert row["last_status"] == "failed"
