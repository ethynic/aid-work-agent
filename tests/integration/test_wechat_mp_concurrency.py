"""微信公众号 WP1 schema 并发专项（真实 PostgreSQL，多连接多线程）。

验证设计 §8 队列语义的两条并发保证（开发者未覆盖）：
1. 已有 running 时仍可连续受理多个 queued（部分唯一索引只约束 running）。
2. 并发事务同时 queued→running 领取，同租户只有一个成功（部分唯一索引兜底）。
"""

import os
import threading
import uuid
from pathlib import Path

import pytest
from dotenv import load_dotenv

project_root = Path(__file__).parent.parent.parent
load_dotenv(project_root / ".env")
DATABASE_URL = os.getenv("DATABASE_URL", "")

psycopg2 = pytest.importorskip("psycopg2")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL or "postgresql" not in DATABASE_URL,
    reason="需要 PostgreSQL DATABASE_URL",
)


def _connect():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    return conn


@pytest.fixture()
def tenant_id():
    value = f"wmp_cc_{uuid.uuid4().hex[:12]}"
    yield value
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM bs_wechat_mp_sync_runs WHERE tenant_id = %s", (value,))
        conn.commit()
    finally:
        conn.close()


def _insert_run(conn, tenant_id, status):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO bs_wechat_mp_sync_runs (tenant_id, trigger_type, status)"
            " VALUES (%s, 'manual', %s) RETURNING id",
            (tenant_id, status),
        )
        return cur.fetchone()[0]


def test_queued_acceptance_unlimited_while_running(tenant_id):
    """已有 running 时仍可连续受理两个 queued；且受理与 running 可同事务提交。"""
    conn = _connect()
    try:
        _insert_run(conn, tenant_id, "running")
        conn.commit()
        # 连续两个独立事务各受理一个 queued
        _insert_run(conn, tenant_id, "queued")
        conn.commit()
        _insert_run(conn, tenant_id, "queued")
        conn.commit()
        # 同事务：queued 与更多 queued 混合
        _insert_run(conn, tenant_id, "queued")
        _insert_run(conn, tenant_id, "queued")
        conn.commit()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, COUNT(*) AS n FROM bs_wechat_mp_sync_runs"
                " WHERE tenant_id = %s GROUP BY status",
                (tenant_id,),
            )
            counts = dict(cur.fetchall())
        assert counts == {"running": 1, "queued": 4}
    finally:
        conn.close()


def test_concurrent_claim_only_one_running(tenant_id):
    """两个并发事务同时把各自 queued 置 running：只有一个提交成功。"""
    setup = _connect()
    try:
        run_a = _insert_run(setup, tenant_id, "queued")
        run_b = _insert_run(setup, tenant_id, "queued")
        setup.commit()
    finally:
        setup.close()

    barrier = threading.Barrier(2)
    results = {}

    def claim(name, run_id):
        conn = _connect()
        try:
            barrier.wait(timeout=15)
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE bs_wechat_mp_sync_runs SET status='running', started_at=now()"
                        " WHERE id = %s",
                        (run_id,),
                    )
                conn.commit()
                results[name] = "committed"
            except psycopg2.errors.UniqueViolation:
                conn.rollback()
                results[name] = "unique_violation"
            except Exception as exc:  # noqa: BLE001
                conn.rollback()
                results[name] = f"error:{type(exc).__name__}"
        finally:
            conn.close()

    threads = [
        threading.Thread(target=claim, args=("A", run_a)),
        threading.Thread(target=claim, args=("B", run_b)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
        assert not t.is_alive(), "并发领取线程超时（疑似死锁）"

    assert sorted(results.values()) == ["committed", "unique_violation"], (
        f"期望恰好一个成功一个唯一冲突，实际: {results}"
    )

    check = _connect()
    try:
        with check.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM bs_wechat_mp_sync_runs"
                " WHERE tenant_id = %s AND status = 'running'",
                (tenant_id,),
            )
            assert cur.fetchone()[0] == 1
    finally:
        check.close()
