"""PostgreSQL resume 队列红灯契约测试（Phase 3R）。

验证 BrowserResumeJobDB：
- enqueue 用 ON CONFLICT DO NOTHING 幂等入队，assistance_id 参数化；
- claim_batch 使用 FOR UPDATE SKIP LOCKED，state/lease 参数化；
- mark_completed / mark_failed 带 job_id 作用域；
- 所有写操作 commit；
- available() 探活不抛异常。

同时断言 resume_store 不再引用 Redis Stream（XADD/XREAD）。
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from src.tools.browser import run_db as run_db_module
from src.tools.browser import resume_store as resume_store_module
from src.tools.browser.run_db import BrowserResumeJobDB


class _FakeDBConnection:
    def __init__(self, *, rows=None, rowcount=1):
        self.calls: list[tuple[str, tuple]] = []
        self.rowcount = rowcount
        self.commits = 0
        self._rows = rows or []
        self._row_idx = 0

    def execute(self, sql, params):
        self.calls.append((" ".join(sql.split()), params))

    def fetchone(self):
        return {"tenant_id": "tenant", "run_id": "run"} if self._row_idx == 0 else None

    def fetchall(self):
        rows = self._rows
        self._rows = []
        return rows

    def commit(self):
        self.commits += 1


@contextmanager
def _fake_ctx(conn):
    yield conn


@pytest.mark.asyncio
async def test_enqueue_is_idempotent_parameterized_and_committed(monkeypatch):
    conn = _FakeDBConnection(rowcount=1)
    monkeypatch.setattr(run_db_module, "get_db_connection", lambda: _fake_ctx(conn))
    db = BrowserResumeJobDB()
    ok = await db.enqueue(
        tenant_id="tenant", assistance_id="assist-a", run_id="run-a", job_id="brj_a",
    )
    assert ok is True
    sql, params = conn.calls[0]
    assert "ON CONFLICT (assistance_id) DO NOTHING" in sql
    assert "INSERT INTO bs_browser_resume_jobs" in sql
    assert params == ("brj_a", "tenant", "assist-a", "run-a")
    assert conn.commits == 1


@pytest.mark.asyncio
async def test_enqueue_duplicate_returns_false(monkeypatch):
    conn = _FakeDBConnection(rowcount=0)  # ON CONFLICT 命中已存在，rowcount=0
    monkeypatch.setattr(run_db_module, "get_db_connection", lambda: _fake_ctx(conn))
    db = BrowserResumeJobDB()
    ok = await db.enqueue(
        tenant_id="tenant", assistance_id="assist-a", run_id="run-a", job_id="brj_b",
    )
    assert ok is False


@pytest.mark.asyncio
async def test_claim_batch_uses_for_update_skip_locked(monkeypatch):
    rows = [
        {"job_id": "brj_a", "tenant_id": "tenant", "assistance_id": "assist-a", "run_id": "run-a", "attempts": 0},
    ]
    conn = _FakeDBConnection(rows=rows)
    monkeypatch.setattr(run_db_module, "get_db_connection", lambda: _fake_ctx(conn))
    db = BrowserResumeJobDB()
    claimed = await db.claim_batch(lease_owner="worker-1", lease_seconds=600, limit=10)
    assert len(claimed) == 1
    assert claimed[0]["assistance_id"] == "assist-a"
    # SELECT 用 FOR UPDATE SKIP LOCKED
    select_sql = next(sql for sql, _ in conn.calls if sql.startswith("SELECT"))
    assert "FOR UPDATE SKIP LOCKED" in select_sql
    # UPDATE 标记 processing + lease
    update_sql = next(sql for sql, _ in conn.calls if sql.startswith("UPDATE"))
    assert "state = 'processing'" in update_sql
    assert "lease_owner = %s" in update_sql
    assert "attempts = attempts + 1" in update_sql
    assert conn.commits == 1


@pytest.mark.asyncio
async def test_claim_batch_empty_returns_empty_and_commits(monkeypatch):
    conn = _FakeDBConnection(rows=[])
    monkeypatch.setattr(run_db_module, "get_db_connection", lambda: _fake_ctx(conn))
    db = BrowserResumeJobDB()
    claimed = await db.claim_batch(lease_owner="worker-1")
    assert claimed == []
    assert conn.commits == 1


@pytest.mark.asyncio
async def test_mark_completed_scoped_to_processing(monkeypatch):
    conn = _FakeDBConnection(rowcount=1)
    monkeypatch.setattr(run_db_module, "get_db_connection", lambda: _fake_ctx(conn))
    db = BrowserResumeJobDB()
    ok = await db.mark_completed(tenant_id="tenant", job_id="brj_a", lease_owner="worker-1")
    assert ok is True
    sql, params = conn.calls[0]
    assert "state='completed'" in sql
    assert "WHERE tenant_id=%s AND job_id=%s AND state='processing'" in sql
    assert "lease_owner=%s" in sql
    assert params == ("tenant", "brj_a", "worker-1")
    assert conn.commits == 1


@pytest.mark.asyncio
async def test_mark_failed_permanent_sets_failed(monkeypatch):
    conn = _FakeDBConnection()
    monkeypatch.setattr(run_db_module, "get_db_connection", lambda: _fake_ctx(conn))
    db = BrowserResumeJobDB()
    await db.mark_failed(
        tenant_id="tenant", job_id="brj_a", lease_owner="worker-1",
        error_code="RESUME_CONTEXT_LOST", permanent=True,
    )
    sql, _ = conn.calls[0]
    assert "state='failed'" in sql
    assert "last_error_code=%s" in sql


@pytest.mark.asyncio
async def test_mark_failed_retry_resets_to_pending_with_backoff(monkeypatch):
    conn = _FakeDBConnection()
    monkeypatch.setattr(run_db_module, "get_db_connection", lambda: _fake_ctx(conn))
    db = BrowserResumeJobDB()
    await db.mark_failed(
        tenant_id="tenant", job_id="brj_a", lease_owner="worker-1",
        error_code="TRANSIENT", permanent=False, backoff_seconds=45,
    )
    sql, params = conn.calls[0]
    assert "state='pending'" in sql
    assert "available_at=CURRENT_TIMESTAMP + (" in sql
    assert "::interval" in sql
    # backoff 秒数以参数形式传入（防 SQL 注入），不是字面拼接
    assert "45" in params


def test_available_returns_bool_without_raising():
    # 无真实 PG 时返回 False，不抛异常（惰性探活，不阻塞 import）
    result = BrowserResumeJobDB.available()
    assert isinstance(result, bool)


def test_resume_store_no_longer_uses_redis_stream():
    """Phase 3R 红灯：resume_store 不得调用 XADD/XREAD/XGROUP。"""
    import inspect

    source = inspect.getsource(resume_store_module.ResumeStore)
    for forbidden in ("xadd", "xread", "xgroup", "xrange", "xtrim"):
        assert forbidden not in source, f"resume_store 仍引用 Redis Stream 命令: {forbidden}"


def test_resume_store_exposes_pg_queue_methods():
    assert hasattr(resume_store_module.ResumeStore, "claim_resume_jobs")
    assert hasattr(resume_store_module.ResumeStore, "complete_resume_job")
    assert hasattr(resume_store_module.ResumeStore, "fail_resume_job")
    # 旧 Redis Stream 接口应已移除
    assert not hasattr(resume_store_module.ResumeStore, "read_resume_jobs")
